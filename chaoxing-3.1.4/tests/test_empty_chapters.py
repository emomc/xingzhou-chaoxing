import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import main
from api.base import Chaoxing, StudyResult
from api.course_points import has_explicit_task_count, task_counts
from api.decode import decode_course_card, decode_course_point
from api.exceptions import MaxRetryExceeded
from backend.course_worker import (
    public_course_detail, summary_from_detail, validate_public_course_detail,
)
from backend.worker import main as worker_main


COURSE = {"courseId": "101", "clazzId": "test-class", "cpi": "test-cpi", "title": "Offline"}
POINT = {
    "id": "9001", "title": "Empty chapter", "jobCount": 1, "jobCountKnown": False,
    "completedJobCount": 0, "has_finished": False, "need_unlock": False,
}
EMPTY_HTML = (
    '<div id="pageDiv" class="ans-cc"><div class="nullpage">'
    '<dl><dt><img src="/empty.png"></dt><dd>暂无内容</dd></dl></div></div>'
)


def outline_html(count=None, tip="", badge=None):
    counter = "" if count is None else f'<input class="knowledgeJobCount" value="{count}">'
    marker = "" if badge is None else (
        '<div class="catalog_jindu catalog_tishi120">'
        f'<span class="catalog_points_yi">{badge}</span></div>'
    )
    return (
        '<div class="chapter_unit"><ul><li><div id="cur9001">'
        '<a class="clicktitle">Empty chapter</a><div class="catalog_task">'
        + counter + marker + f'<span class="bntHoverTips">{tip}</span></div></div></li></ul></div>'
    )


def card_html(cards):
    return "var mArg = " + json.dumps({"attachments": cards, "defaults": {}}) + ";"


def response(html=EMPTY_HTML, status=200):
    return Mock(status_code=status, text=html, url="https://mooc1.chaoxing.com/mooc-ans/knowledge/cards")


class EmptyOutlineTests(unittest.TestCase):
    def point(self, count=None, tip=""):
        return decode_course_point(outline_html(count, tip))["points"][0]

    def test_explicit_zero_is_preserved_in_queue_and_public_outline(self):
        point = self.point("0")
        self.assertEqual(task_counts(point), (0, 0, 0))
        self.assertTrue(has_explicit_task_count(point))
        self.assertEqual(main.point_remaining(point), 0)
        detail = public_course_detail(COURSE, {"points": [point]})
        self.assertEqual(detail["totals"], {
            "task_count": 0, "completed_count": 0, "remaining_count": 0, "point_count": 1,
        })
        self.assertFalse(detail["points"][0]["completed"])
        self.assertEqual(validate_public_course_detail(detail), detail)
        self.assertEqual(summary_from_detail(detail)["remaining_count"], 0)

    def test_zero_completion_counts_do_not_create_a_phantom_task(self):
        for tip in ("已完成 0/0", "已完成0个，未完成0个"):
            with self.subTest(tip=tip):
                self.assertEqual(task_counts(self.point(tip=tip)), (0, 0, 0))

    def test_platform_zero_badge_is_explicit_evidence_of_no_task_points(self):
        for badge in ("0", " 0 ", "\n0\n"):
            with self.subTest(badge=badge):
                point = decode_course_point(outline_html(badge=badge))["points"][0]
                self.assertEqual(task_counts(point), (0, 0, 0))
                self.assertTrue(has_explicit_task_count(point))
                detail = public_course_detail(COURSE, {"points": [point]})
                self.assertEqual(detail["totals"]["task_count"], 0)
                self.assertEqual(detail["totals"]["remaining_count"], 0)
                self.assertFalse(detail["points"][0]["completed"])
                self.assertEqual(validate_public_course_detail(detail), detail)

    def test_zero_badge_cannot_override_a_positive_task_counter(self):
        point = decode_course_point(outline_html("2", badge="0"))["points"][0]
        self.assertEqual(task_counts(point), (2, 0, 2))

    def test_zero_badge_cannot_override_positive_completion_or_pending_counts(self):
        for tip in ("已完成 1/2", "已完成1个，未完成1个", "2个待完成任务点"):
            with self.subTest(tip=tip):
                point = decode_course_point(outline_html(tip=tip, badge="0"))["points"][0]
                self.assertGreater(main.point_remaining(point), 0)

    def test_missing_invalid_or_nonzero_badge_does_not_prove_no_tasks(self):
        for badge in (None, "", "invalid", "-1", "1.5", "1", "2"):
            with self.subTest(badge=badge):
                point = decode_course_point(outline_html(badge=badge))["points"][0]
                self.assertGreater(main.point_remaining(point), 0)

    def test_zero_outside_the_task_indicator_does_not_prove_no_tasks(self):
        html = outline_html().replace(
            '<a class="clicktitle">Empty chapter</a>',
            '<a class="clicktitle">Empty chapter <span class="catalog_points_yi">0</span></a>',
        )
        point = decode_course_point(html)["points"][0]
        self.assertEqual(task_counts(point), (1, 0, 1))
        self.assertFalse(has_explicit_task_count(point))

    def test_zero_badge_preserves_a_locked_chapters_state(self):
        point = decode_course_point(
            outline_html(tip="完成前一章节后解锁", badge="0"),
        )["points"][0]
        self.assertEqual(task_counts(point), (0, 0, 0))
        self.assertTrue(point["need_unlock"])
        self.assertFalse(point["has_finished"])

    def test_missing_or_malformed_counter_is_not_evidence_of_zero_tasks(self):
        for count in (None, "", "invalid", "-1", "1.5"):
            with self.subTest(count=count):
                point = self.point(count)
                self.assertEqual(task_counts(point), (1, 0, 1))
                self.assertFalse(has_explicit_task_count(point))

    def test_pending_task_tip_without_input_is_explicit_positive_evidence(self):
        point = self.point(tip="2个待完成任务点")
        self.assertEqual(task_counts(point), (2, 0, 2))
        self.assertTrue(has_explicit_task_count(point))

    def test_positive_tip_cannot_be_overridden_by_a_zero_input(self):
        point = self.point("0", "2个待完成任务点")
        self.assertEqual(task_counts(point), (2, 0, 2))

    def test_lock_is_preserved_even_when_counter_is_present(self):
        point = self.point("2", "完成前一章节后解锁")
        self.assertTrue(point["need_unlock"])
        self.assertFalse(point["has_finished"])

    def test_invalid_raw_counts_remain_unknown_in_shared_normalization(self):
        for value in (-1, True, False, 0.5, "invalid", None):
            with self.subTest(value=value):
                point = POINT | {"jobCount": value}
                self.assertEqual(task_counts(point), (1, 0, 1))
                self.assertEqual(
                    public_course_detail(COURSE, {"points": [point]})["points"][0]["remaining_count"], 1,
                )

    def test_zero_task_point_cannot_claim_a_platform_pass(self):
        detail = public_course_detail(COURSE, {"points": [self.point("0")]})
        detail["points"][0]["completed"] = True
        with self.assertRaises(ValueError):
            validate_public_course_detail(detail)


class EmptyCardDecodeTests(unittest.TestCase):
    def test_real_no_content_placeholder_is_recognized_without_marg(self):
        jobs, info = decode_course_card(EMPTY_HTML)
        self.assertEqual(jobs, [])
        self.assertTrue(info["empty"])
        self.assertEqual(info["card_state"], "empty")

    def test_marker_in_script_or_normal_text_is_not_an_empty_page(self):
        for html in (
            "<script>const message = '暂无内容';</script>",
            "<p>这道题为什么显示暂无内容？</p>",
            "<body>服务异常，暂无内容</body>",
            "<form action='/login'><input type='password'></form>",
            "", "Bad gateway",
        ):
            with self.subTest(html=html):
                self.assertEqual(decode_course_card(html)[1]["card_state"], "unrecognized")

    def test_hidden_placeholder_is_not_an_empty_page(self):
        for html in (
            EMPTY_HTML.replace('class="nullpage"', 'class="nullpage" hidden'),
            EMPTY_HTML.replace('class="nullpage"', 'class="nullpage" aria-hidden="true"'),
            EMPTY_HTML.replace('class="nullpage"', 'class="nullpage" style="display: none"'),
            f"<template>{EMPTY_HTML}</template>",
        ):
            with self.subTest(html=html):
                self.assertFalse(decode_course_card(html)[1]["empty"])

    def test_media_or_text_content_prevents_empty_recognition(self):
        for content in ("<iframe></iframe>", "<video></video>", "<img src='/lesson.png'>", "<p>Lesson text</p>"):
            with self.subTest(content=content):
                html = EMPTY_HTML.replace('<div class="nullpage">', content + '<div class="nullpage">')
                self.assertFalse(decode_course_card(html)[1]["empty"])

    def test_empty_attachment_list_is_not_itself_proof_of_empty_content(self):
        _, info = decode_course_card(card_html([]))
        self.assertFalse(info["empty"])
        self.assertEqual(info["card_state"], "no_tasks")

    def test_task_data_wins_over_an_empty_placeholder(self):
        video = {
            "type": "video", "job": True, "jobid": "test-job", "objectId": "test-video",
            "property": {"name": "Test video"},
        }
        jobs, info = decode_course_card(EMPTY_HTML + card_html([video]))
        self.assertEqual(len(jobs), 1)
        self.assertFalse(info["empty"])

    def test_malformed_task_data_is_not_an_empty_page(self):
        for data in ({}, {"attachments": None}, {"attachments": [None]},
                     {"attachments": [], "defaults": None}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_course_card(EMPTY_HTML + "var mArg = " + json.dumps(data) + ";")

    def test_closed_empty_page_remains_closed(self):
        _, info = decode_course_card(EMPTY_HTML + "<p>章节未开放</p>")
        self.assertTrue(info["notOpen"])


class EmptyCardFetchTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.get.return_value = response()
        self.client = Chaoxing()
        self.client.rate_limiter = Mock()
        self.client._chapter_card_count = Mock(return_value=None)
        self.client.study_emptypage = Mock(return_value=StudyResult.SUCCESS)
        patcher = patch("api.base.SessionManager.get_session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_cards_must_be_explicitly_empty(self):
        jobs, info = self.client.get_job_list(COURSE, POINT, visit_empty=False)
        self.assertEqual(jobs, [])
        self.assertTrue(info["empty"])
        self.assertEqual(self.session.get.call_count, 7)
        self.client.study_emptypage.assert_not_called()

    def test_unknown_tail_card_prevents_an_empty_result(self):
        self.session.get.side_effect = [response()] * 6 + [response("<p>Session expired</p>")]
        with self.assertRaises(MaxRetryExceeded):
            self.client.get_job_list(COURSE, POINT)
        self.client.study_emptypage.assert_not_called()

    def test_all_passed_attachments_are_not_an_empty_chapter(self):
        self.session.get.return_value = response(card_html([{
            "type": "video", "job": True, "jobid": "test-job", "isPassed": True,
        }]))
        jobs, info = self.client.get_job_list(COURSE, POINT)
        self.assertEqual(jobs, [])
        self.assertFalse(info["empty"])
        self.client.study_emptypage.assert_not_called()

    def test_positive_list_count_prevents_automatic_empty_visit(self):
        _, info = self.client.get_job_list(COURSE, POINT | {"jobCountKnown": True})
        self.assertTrue(info["empty"])
        self.client.study_emptypage.assert_not_called()

    def test_failed_empty_visit_is_retained_for_the_caller(self):
        self.client.study_emptypage.return_value = StudyResult.ERROR
        _, info = self.client.get_job_list(COURSE, POINT)
        self.assertFalse(info["empty_visit_succeeded"])

    def test_visit_redirect_to_login_is_not_successful(self):
        self.session.get.return_value.url = "https://passport2.chaoxing.com/login"
        self.assertEqual(Chaoxing.study_emptypage(self.client, COURSE, POINT), StudyResult.ERROR)

    def test_successful_http_with_no_recognizable_outline_is_not_a_zero_task_course(self):
        for html in ("", "<p>服务异常</p>", "<form><input type='password'></form>"):
            with self.subTest(html=html):
                self.session.get.return_value = response(html)
                with self.assertRaises(MaxRetryExceeded):
                    self.client.get_course_point("101", "test-class", "test-cpi")


class EmptyChapterProcessingTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.get_job_list.return_value = ([], {"empty": True, "card_state": "empty"})
        self.client.study_emptypage.return_value = StudyResult.SUCCESS
        self.client.get_course_point.return_value = {
            "points": [POINT | {"has_finished": True, "completedJobCount": 1}],
        }

    def run_chapter(self, point=None, **options):
        return main.process_chapter(
            self.client, COURSE, point or POINT, 1, expect_tasks=True, **options,
        )

    def test_empty_chapter_visits_once_then_requires_fresh_platform_confirmation(self):
        self.assertEqual(self.run_chapter(), main.ChapterResult.SUCCESS)
        self.client.study_emptypage.assert_called_once_with(COURSE, POINT)
        self.client.get_course_point.assert_called_once_with("101", "test-class", "test-cpi")

    def test_unconfirmed_empty_chapter_remains_incomplete(self):
        self.client.get_course_point.return_value = {"points": [POINT]}
        self.assertEqual(self.run_chapter(), main.ChapterResult.INCOMPLETE)

    def test_explicit_positive_count_and_empty_page_remain_incomplete_without_visiting(self):
        point = POINT | {"jobCountKnown": True}
        self.client.get_course_point.return_value = {"points": [point]}
        self.assertEqual(self.run_chapter(point), main.ChapterResult.INCOMPLETE)
        self.client.study_emptypage.assert_not_called()

    def test_stale_positive_count_can_only_be_resolved_by_a_fresh_finished_outline(self):
        self.assertEqual(self.run_chapter(POINT | {"jobCountKnown": True}), main.ChapterResult.SUCCESS)
        self.client.study_emptypage.assert_not_called()

    def test_failed_empty_visit_does_not_confirm_completion(self):
        self.client.study_emptypage.return_value = StudyResult.ERROR
        self.assertEqual(self.run_chapter(), main.ChapterResult.ERROR)
        self.client.get_course_point.assert_not_called()

    def test_readback_failure_is_not_successful_empty_completion(self):
        self.client.get_course_point.side_effect = MaxRetryExceeded("Offline read failure")
        with self.assertRaises(MaxRetryExceeded):
            self.run_chapter()

    def test_locked_readback_remains_locked(self):
        self.client.get_course_point.return_value["points"][0]["need_unlock"] = True
        self.assertEqual(self.run_chapter(), main.ChapterResult.NOT_OPEN)

    def test_missing_chapter_in_confirmation_remains_incomplete(self):
        self.client.get_course_point.return_value = {"points": []}
        self.assertEqual(self.run_chapter(), main.ChapterResult.INCOMPLETE)

    def test_recheck_does_not_mark_an_empty_page_read(self):
        self.assertEqual(self.run_chapter(videos_only=True), main.ChapterResult.SUCCESS)
        self.client.study_emptypage.assert_not_called()
        self.client.get_course_point.assert_not_called()

    def test_explicit_zero_is_skipped_without_opening_the_chapter(self):
        self.assertEqual(self.run_chapter(POINT | {"jobCount": 0}), main.ChapterResult.SUCCESS)
        self.client.get_job_list.assert_not_called()
        self.client.study_emptypage.assert_not_called()

    def test_zero_badge_is_skipped_without_opening_non_task_resources(self):
        point = decode_course_point(outline_html(badge="0"))["points"][0]
        self.assertEqual(self.run_chapter(point), main.ChapterResult.SUCCESS)
        self.client.get_job_list.assert_not_called()
        self.client.study_emptypage.assert_not_called()

    def test_video_recheck_still_opens_a_zero_task_chapter_when_explicitly_requested(self):
        point = decode_course_point(outline_html(badge="0"))["points"][0]
        self.assertEqual(self.run_chapter(point, videos_only=True), main.ChapterResult.SUCCESS)
        self.client.get_job_list.assert_called_once()
        self.client.study_emptypage.assert_not_called()

    def test_non_task_video_reading_and_survey_do_not_block_a_finished_course(self):
        html = "".join(
            outline_html(badge="0").replace("cur9001", f"cur{index}").replace(
                "Empty chapter", title,
            )
            for index, title in enumerate(("Optional video", "Reading", "Survey"), start=9001)
        )
        points = decode_course_point(html)["points"] + [
            POINT | {"id": "9010", "jobCountKnown": True,
                     "has_finished": True, "completedJobCount": 1},
        ]
        self.client.get_course_point.return_value = {"points": points}
        with patch("main.report") as report:
            self.assertTrue(main.process_course(self.client, COURSE, {
                "speed": 1, "jobs": 1, "notopen_action": "continue",
            }))
        self.client.get_job_list.assert_not_called()
        self.client.study_emptypage.assert_not_called()
        skipped = [call for call in report.call_args_list
                   if call.args == ("chapter_result",) and call.kwargs.get("phase") == "skipped"]
        self.assertEqual(len(skipped), 4)
        detail = public_course_detail(COURSE, {"points": points})
        self.assertEqual(detail["totals"], {
            "task_count": 1, "completed_count": 1, "remaining_count": 0, "point_count": 4,
        })

    def test_empty_chapter_is_not_a_course_failure_after_the_platform_confirms_it(self):
        finished = POINT | {"has_finished": True, "completedJobCount": 1}
        self.client.get_course_point.side_effect = [
            {"points": [POINT]}, {"points": [finished]}, {"points": [finished]},
        ]
        self.assertTrue(main.process_course(self.client, COURSE, {
            "speed": 1, "jobs": 1, "notopen_action": "continue",
        }))
        self.client.study_emptypage.assert_called_once()


class CourseRecheckResultTests(unittest.TestCase):
    def test_recheck_replaces_stale_failure_details_even_when_no_courses_fail(self):
        from api.config import GlobalConst

        payload = {
            "credentials": {"username": "offline-login", "password": "not-real"},
            "spec": {"course_ids": ["101"], "videos_only": False, "submit": False, "preview": False,
                     "human_pacing": False},
            "internal_url": "http://127.0.0.1:3100", "worker_token": "offline-token",
            "ai_wait_timeout": 60,
        }
        client = Mock()
        client.login.return_value = {"status": True}
        client.get_course_list.return_value = [COURSE]
        for success in (True, False):
            with self.subTest(success=success):
                output = io.StringIO()
                stdin = Mock(buffer=io.BytesIO(json.dumps(payload).encode() + b"\n"))
                with patch("backend.worker.sys.stdin", stdin), \
                        patch("backend.worker.sys.stdout", output), \
                        patch("backend.worker.threading.Thread"), \
                        patch.dict(os.environ), \
                        patch.object(GlobalConst, "COOKIES_PATH"), \
                        patch.object(GlobalConst, "THRESHOLD"), \
                        patch("api.runtime.set_progress_callback"), \
                        patch("api.base.SessionManager.close_current"), \
                        patch("main.init_chaoxing", return_value=client), \
                        patch("main.process_course", return_value=success):
                    self.assertEqual(worker_main(), 0 if success else 20)
                packets = [json.loads(line) for line in output.getvalue().splitlines()]
                failures = [packet["data"]["failed_courses"] for packet in packets
                            if packet["event"] == "phase" and "failed_courses" in packet["data"]]
                self.assertEqual(failures, ["" if success else COURSE["title"]])
                self.assertEqual(
                    packets[-1]["data"]["outcome"], "succeeded" if success else "needs_attention",
                )


if __name__ == "__main__":
    unittest.main()
