import json
import unittest
from unittest.mock import Mock, patch

import requests

import main
from api.base import Chaoxing, StudyResult
from api.decode import decode_course_card, decode_course_card_count, decode_questions_info
from api.exceptions import MaxRetryExceeded
from tests.test_video_recheck import COURSE, POINT, VIDEO, card_html, video_card


def response(html):
    return Mock(status_code=200, text=html, url="https://mooc1.chaoxing.com/mooc-ans/test")


class TaskDiscoveryTests(unittest.TestCase):
    def test_card_count_uses_the_platform_input_not_an_arbitrary_script_value(self):
        self.assertEqual(decode_course_card_count('<input id="cardcount" value="9">'), 9)
        self.assertEqual(decode_course_card_count('<input name="cardcount" value="3">'), 3)
        self.assertEqual(decode_course_card_count('<input id="cardcount" name="cardcount" value="1">'), 1)
        for html in (
            "", 'var cardcount=9;', '<input name="cardcount" value="0">',
            '<input name="cardcount" value="-1">', '<input name="cardcount" value="bad">',
            '<input name="cardcount" value="2"><input name="cardcount" value="3">',
        ):
            with self.subTest(html=html):
                self.assertIsNone(decode_course_card_count(html))
        with self.assertRaises(ValueError):
            decode_course_card_count('<input name="cardcount" value="101">')

    def test_reads_all_cards_including_the_eighth_and_keeps_each_cards_parameters(self):
        client, session = Chaoxing(), Mock()
        client.rate_limiter = Mock()
        def get(url, **kwargs):
            if "studentstudyAjax" in url:
                return response('<input name="cardcount" value="9">')
            number = kwargs["params"]["num"]
            cards = [video_card(jobid="last-video")] if number == "8" else []
            return response(card_html(cards, {"ktoken": "token-" + number}))
        session.get.side_effect = get
        with patch("api.base.SessionManager.get_session", return_value=session):
            jobs, info = client.get_job_list(COURSE, POINT, visit_empty=False)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["_card_num"], "8")
        self.assertEqual(jobs[0]["_job_info"]["ktoken"], "token-8")
        self.assertEqual(info["card_count"], 9)
        self.assertTrue(info["card_count_known"])
        self.assertEqual(session.get.call_count, 10)

    def test_known_single_card_does_not_scan_six_phantom_cards(self):
        client, session = Chaoxing(), Mock()
        client.rate_limiter = Mock()
        session.get.side_effect = [
            response('<input id="cardcount" value="1">'), response(card_html([])),
        ]
        with patch("api.base.SessionManager.get_session", return_value=session):
            _, info = client.get_job_list(COURSE, POINT, visit_empty=False)
        self.assertEqual(info["card_count"], 1)
        self.assertEqual(session.get.call_count, 2)

    def test_missing_or_failed_count_endpoint_keeps_legacy_scan(self):
        for navigation in (response("<p>No card counter</p>"), requests.Timeout()):
            client, session = Chaoxing(), Mock()
            client.rate_limiter = Mock()
            session.get.side_effect = [navigation] + [response(card_html([])) for _ in range(7)]
            with self.subTest(navigation=type(navigation).__name__), \
                    patch("api.base.SessionManager.get_session", return_value=session):
                _, info = client.get_job_list(COURSE, POINT, visit_empty=False)
            self.assertEqual(info["card_count"], 7)
            self.assertFalse(info["card_count_known"])

    def test_login_redirect_in_count_endpoint_cannot_mean_an_empty_chapter(self):
        client, session = Chaoxing(), Mock()
        client.rate_limiter = Mock()
        session.get.return_value = response("")
        session.get.return_value.url = "https://passport2.chaoxing.com/login"
        with patch("api.base.SessionManager.get_session", return_value=session), \
                self.assertRaises(MaxRetryExceeded):
            client.get_job_list(COURSE, POINT, visit_empty=False)

    def test_unsupported_task_is_retained_without_urls_tokens_or_raw_payload_logging(self):
        task = {
            "type": "hyperlink", "job": True, "jobid": "hyperlink-job",
            "jtoken": "private-fixture", "property": {
                "href": "https://private.example/?secret=fixture", "title": "Account setup",
            },
        }
        with patch("api.decode.logger") as logger:
            jobs, info = decode_course_card(card_html([task]))
        self.assertEqual(jobs, [])
        self.assertEqual(info["unresolved_tasks"], [{
            "type": "hyperlink", "jobid": "hyperlink-job", "title": "Account setup",
        }])
        self.assertNotIn("private", str(logger.mock_calls))
        self.assertNotIn("secret", json.dumps(info))

    def test_explicit_pending_tasks_without_job_flag_are_not_dropped(self):
        for kind in ("document", "workid", "read"):
            task = {"type": kind, "jobid": "pending-job", "isPassed": False, "property": {}}
            with self.subTest(kind=kind):
                jobs, _ = decode_course_card(card_html([task]))
                self.assertEqual([job["type"] for job in jobs], [kind])
        optional = {"type": "document", "jobid": "optional", "property": {}}
        self.assertEqual(decode_course_card(card_html([optional]))[0], [])

    def test_document_tasks_expose_a_bounded_display_name_without_authentication_fields(self):
        jobs, _ = decode_course_card(card_html([{
            "type": "document", "job": True, "jobid": "file-task", "jtoken": "private-token",
            "property": {"name": "Lecture 01.pptx", "objectid": "private-object"},
        }]))
        self.assertEqual(jobs[0]["name"], "Lecture 01.pptx")

    def test_tracked_read_task_is_supported_even_with_a_job_flag(self):
        jobs, info = decode_course_card(card_html([{
            "type": "read", "job": True, "jobid": "read-job", "property": {},
        }]))
        self.assertEqual([job["type"] for job in jobs], ["read"])
        self.assertNotIn("unresolved_tasks", info)

    def test_manual_task_stays_incomplete_but_does_not_discard_video(self):
        client = Mock()
        client.get_job_list.return_value = ([VIDEO.copy()], {"unresolved_tasks": [{
            "type": "hyperlink", "title": "Account setup", "jobid": "link", "card_num": "0",
            "href": "https://private.example/?token=fixture",
        }]})
        with patch("main.process_job", return_value=StudyResult.SUCCESS) as process:
            with patch("main.report") as report:
                self.assertEqual(
                    main.process_chapter(client, COURSE, POINT, 1, expect_tasks=True),
                    main.ChapterResult.MANUAL,
                )
        process.assert_called_once()
        payload = report.call_args_list[-1].kwargs["manual_tasks"]
        self.assertEqual(payload, [{"type": "hyperlink", "title": "Account setup", "card_num": "0"}])
        self.assertNotIn("href", json.dumps(payload))
        with patch("main.process_job", return_value=StudyResult.SUCCESS):
            self.assertEqual(
                main.process_chapter(client, COURSE, POINT, 1, videos_only=True),
                main.ChapterResult.SUCCESS,
            )

    def test_no_jobs_only_succeeds_when_fresh_outline_confirms_the_target_chapter(self):
        client = Mock()
        client.get_job_list.return_value = ([], {})
        for refreshed, expected in (
            ([POINT], main.ChapterResult.INCOMPLETE),
            ([], main.ChapterResult.INCOMPLETE),
            ([POINT | {"has_finished": True}], main.ChapterResult.SUCCESS),
            ([POINT | {"has_finished": True, "need_unlock": True}], main.ChapterResult.INCOMPLETE),
        ):
            client.get_course_point.return_value = {"points": refreshed}
            with self.subTest(refreshed=refreshed):
                self.assertEqual(
                    main.process_chapter(client, COURSE, POINT, 1, expect_tasks=True), expected,
                )

    def test_upload_assignment_is_not_misclassified_as_an_ordinary_ai_question(self):
        def question(title, kind="4", control=""):
            return decode_questions_info(
                '<form><div class="singleQuesId" data="1">'
                f'<div class="TiMu" data="{kind}"><div class="Zy_TItle">{title}</div>'
                f'{control}</div></div></form>'
            )["questions"][0]
        for title in (
            "请将你为HTML5文件代码书写的注释文件上传。",
            "请上传实验报告文件。", "需要提交你的作品。",
        ):
            with self.subTest(title=title):
                self.assertEqual(question(title)["manual_reason"], "FILE_UPLOAD_REQUIRED")
        self.assertEqual(
            question("Attachment exercise", control='<input type="file">')["manual_reason"],
            "FILE_UPLOAD_REQUIRED",
        )
        self.assertNotIn("manual_reason", question("请解释上传文件的原理。", kind="0"))
        for title in ("请解释上传文件的原理。", "请你简述HTML5文件上传的实现步骤。"):
            with self.subTest(title=title):
                self.assertNotIn("manual_reason", question(title))
        self.assertNotIn("manual_reason", question("解释HTML注释的作用。"))

    def test_manual_upload_does_not_query_ai_or_submit_and_following_resources_still_run(self):
        client = Chaoxing(tiku=Mock(DISABLE=False))
        session = Mock()
        session.get.return_value = response("<form></form>")
        decoded = {"questions": [{
            "id": "upload", "type": "shortanswer", "manual_reason": "FILE_UPLOAD_REQUIRED",
        }]}
        with patch("api.base.SessionManager.get_session", return_value=session), \
                patch("api.base.decode_questions_info", return_value=decoded):
            result = client.study_work(COURSE, {"jobid": "work-upload", "enc": "test"}, {
                "knowledgeid": "test", "ktoken": "test", "cpi": "test",
            })
        self.assertEqual(result, StudyResult.MANUAL)
        session.post.assert_not_called()
        client.tiku.query.assert_not_called()
        chapter_client = Mock()
        chapter_client.get_job_list.return_value = (
            [{"type": "workid", "jobid": "work-upload"}, {"type": "document", "jobid": "doc"}], {},
        )
        with patch("main.process_job", side_effect=[StudyResult.MANUAL, StudyResult.SUCCESS]) as process:
            self.assertEqual(
                main.process_chapter(chapter_client, COURSE, POINT, 1),
                main.ChapterResult.MANUAL,
            )
        self.assertEqual(process.call_count, 2)


if __name__ == "__main__":
    unittest.main()
