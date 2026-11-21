"""图书任务点（insertbook）：脚本做不了，但不能把课程卡成"需人工"。"""

import json
import unittest
from unittest.mock import Mock, patch

import main
from api.base import StudyResult
from api.decode import decode_course_card


COURSE = {"courseId": "254937002", "clazzId": "126668808", "cpi": "test-cpi", "title": "动画艺术片赏析"}


def card_html(cards):
    return "var mArg = " + json.dumps({"attachments": cards, "defaults": {}}) + ";"


def book_card(**overrides):
    """线上抓到的真实形状：没有 type 字段，只能靠 property.module 认。"""
    card = {
        "job": True,
        "jobid": "1582620650126456",
        "jtoken": "private-token",
        "aid": "2053938328",
        "mid": "10814018665051582620649444",
        "property": {
            "module": "insertbook",
            "bookname": "动画概论",
            "author": "聂欣如编著",
            "publisher": "上海：复旦大学出版社",
            "readurl": "https://resapi.chaoxing.com/realReadNew?secret=fixture",
            "timing": "40",
        },
    }
    card.update(overrides)
    return card


def point(chapter_id="1030165658", **overrides):
    value = {
        "id": chapter_id, "title": f"章节 {chapter_id}",
        "jobCount": 1, "jobCountKnown": True,
        "completedJobCount": 0, "has_finished": False, "need_unlock": False,
    }
    value.update(overrides)
    return value


class BookCardDecodingTests(unittest.TestCase):
    def test_book_card_is_recognised_and_not_treated_as_unsupported(self):
        jobs, info = decode_course_card(card_html([book_card()]))
        self.assertEqual(jobs, [])
        self.assertNotIn("unresolved_tasks", info)
        self.assertEqual(info["book_tasks"], [{
            "type": "book", "jobid": "1582620650126456", "title": "动画概论",
            "author": "聂欣如编著", "publisher": "上海：复旦大学出版社",
        }])

    def test_book_card_does_not_leak_tokens_or_read_urls(self):
        _, info = decode_course_card(card_html([book_card()]))
        payload = json.dumps(info, ensure_ascii=False)
        self.assertNotIn("private-token", payload)
        self.assertNotIn("realReadNew", payload)
        self.assertNotIn("secret", payload)

    def test_passed_book_card_is_not_collected(self):
        _, info = decode_course_card(card_html([book_card(isPassed=True)]))
        self.assertNotIn("book_tasks", info)

    def test_book_card_alongside_other_tasks_keeps_them_runnable(self):
        video = {
            "type": "video", "job": True, "jobid": "video-job",
            "property": {"name": "01动画的起源1.mp4", "objectid": "video-object"},
        }
        jobs, info = decode_course_card(card_html([video, book_card()]))
        self.assertEqual([job["type"] for job in jobs], ["video"])
        self.assertEqual([book["title"] for book in info["book_tasks"]], ["动画概论"])

    def test_book_without_bookname_still_gets_a_readable_title(self):
        _, info = decode_course_card(card_html([book_card(property={"module": "insertbook"})]))
        self.assertEqual(info["book_tasks"][0]["title"], "图书")


class BookChapterTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.book = {"type": "book", "jobid": "book-job", "title": "动画概论", "card_num": "1"}
        self.client.get_job_list.return_value = ([], {"book_tasks": [self.book]})
        self.client.get_course_point.return_value = {"points": [point()]}

    def test_book_only_chapter_is_not_marked_for_manual_review(self):
        book_log = {}
        result = main.process_chapter(
            self.client, COURSE, point(), 1, expect_tasks=True, book_log=book_log,
        )
        self.assertEqual(result, main.ChapterResult.SUCCESS)
        self.assertEqual(book_log, {"1030165658": [self.book]})

    def test_book_only_chapter_is_recorded_for_the_operator(self):
        book_log = {}
        with patch("main.report") as report:
            main.process_chapter(
                self.client, COURSE, point(), 1, expect_tasks=True, book_log=book_log,
            )
        tasks = [call for call in report.call_args_list if call.kwargs.get("manual_tasks")]
        self.assertTrue(tasks)
        self.assertEqual(tasks[0].kwargs["manual_tasks"][0]["type"], "book")
        self.assertEqual(tasks[0].kwargs["manual_tasks"][0]["title"], "动画概论")

    def test_book_chapter_still_processes_its_videos(self):
        video = {"type": "video", "jobid": "video-job", "name": "01动画的起源1.mp4"}
        self.client.get_job_list.return_value = (
            [video], {"book_tasks": [self.book]},
        )
        self.client.study_video.return_value = StudyResult.SUCCESS
        book_log = {}
        result = main.process_chapter(
            self.client, COURSE, point(), 1, expect_tasks=True, book_log=book_log,
        )
        self.assertEqual(result, main.ChapterResult.SUCCESS)
        self.client.study_video.assert_called_once()
        self.assertIn("1030165658", book_log)

    def test_unknown_card_is_still_manual(self):
        self.client.get_job_list.return_value = ([], {"unresolved_tasks": [{
            "type": "", "title": "奇怪卡片", "card_num": "2", "jobid": "x",
        }]})
        self.assertEqual(
            main.process_chapter(self.client, COURSE, point(), 1, expect_tasks=True),
            main.ChapterResult.MANUAL,
        )


class BookCourseTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.book = {"type": "book", "jobid": "book-job", "title": "动画概论", "card_num": "1"}
        self.client.get_job_list.return_value = ([], {"book_tasks": [self.book]})

    def test_course_with_book_chapters_confirms_instead_of_failing(self):
        chapters = [point("1001"), point("1002")]
        self.client.get_course_point.return_value = {"points": chapters}
        collector = {}
        with patch("main.report"):
            complete = main.process_course(self.client, COURSE, {
                "speed": 1, "jobs": 1, "notopen_action": "continue", "book_log": collector,
            })
        self.assertTrue(complete, "只剩图书任务的课程不能判成未完成")
        self.assertEqual(set(collector[str(COURSE["courseId"])]), {"1001", "1002"})

    def test_books_are_only_deducted_for_chapters_that_reported_them(self):
        # 1001 只剩书，1002 还有 1 个脚本没做的任务
        chapters = [point("1001"), point("1002")]
        self.client.get_course_point.return_value = {"points": chapters}
        self.client.get_job_list.side_effect = [
            ([], {"book_tasks": [self.book]}),
            ([], {"unresolved_tasks": [{"type": "workid", "title": "章节测验", "jobid": "w"}]}),
        ]
        collector = {}
        with patch("main.report"):
            complete = main.process_course(self.client, COURSE, {
                "speed": 1, "jobs": 1, "notopen_action": "continue", "book_log": collector,
            })
        self.assertFalse(complete, "还有别的未完成任务时课程不能算完成")

    def test_book_course_reports_the_books_to_the_operator(self):
        self.client.get_course_point.return_value = {"points": [point("1001")]}
        with patch("main.report") as report:
            main.process_course(self.client, COURSE, {
                "speed": 1, "jobs": 1, "notopen_action": "continue", "book_log": {},
            })
        manual = [call for call in report.call_args_list if call.kwargs.get("manual_tasks")]
        self.assertTrue(manual)
        self.assertEqual(manual[-1].kwargs["manual_tasks"][0]["title"], "动画概论")

    def test_without_a_collector_the_course_still_finishes(self):
        self.client.get_course_point.return_value = {"points": [point("1001")]}
        with patch("main.report"):
            complete = main.process_course(self.client, COURSE, {
                "speed": 1, "jobs": 1, "notopen_action": "continue",
            })
        self.assertTrue(complete)


if __name__ == "__main__":
    unittest.main()
