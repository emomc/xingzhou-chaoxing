# -*- coding: utf-8 -*-
"""按章节下单：任务只处理勾选的那几节，其余章节不受影响。"""

import unittest
from unittest.mock import Mock, patch

import main
from api.base import StudyResult


COURSE = {"courseId": "101", "clazzId": "class", "cpi": "cpi", "title": "Course"}


def point(point_id, title, task_count=1, completed=0, finished=False, locked=False):
    return {
        "id": point_id, "title": title, "jobCount": task_count, "jobCountKnown": True,
        "completedJobCount": completed, "has_finished": finished, "need_unlock": locked,
    }


def client(points, readback=None):
    """首次读章节给未完成，之后回读默认假设"刚学过的都算完成了"。"""
    if readback is None:
        readback = [
            dict(item, completedJobCount=item["jobCount"], has_finished=True) for item in points
        ]
    calls = {"count": 0}

    def outline(*args, **kwargs):
        calls["count"] += 1
        source = points if calls["count"] == 1 else readback
        return {"points": [dict(item) for item in source]}

    mock = Mock()
    mock.get_course_point.side_effect = outline
    return mock


class PointSelectionTests(unittest.TestCase):
    def run_course(self, fake, point_ids=None, config=None):
        settings = {"speed": 1, "jobs": 1, "notopen_action": "continue",
                    "publish_outline": False}
        if point_ids is not None:
            settings["point_ids"] = point_ids
        settings.update(config or {})
        processed = []
        reporter = Mock()

        def chapter(chaoxing, course, target, speed, **kwargs):
            processed.append(str(target["id"]))
            return main.ChapterResult.SUCCESS

        with patch("main.process_chapter", side_effect=chapter), \
                patch("main.report", reporter), patch("main.tqdm"):
            result = main.process_course(fake, COURSE, settings)
        return result, processed, reporter

    @staticmethod
    def phases(reporter, event):
        return [call.kwargs.get("phase") for call in reporter.call_args_list
                if call.args == (event,)]

    def test_only_the_requested_chapters_are_processed(self):
        points = [point("1", "one"), point("2", "two"), point("3", "three")]
        result, processed, _ = self.run_course(client(points), point_ids=["2"])
        self.assertTrue(result)
        self.assertEqual(processed, ["2"])

    def test_no_selection_still_processes_the_whole_course(self):
        points = [point("1", "one"), point("2", "two")]
        result, processed, _ = self.run_course(client(points))
        self.assertTrue(result)
        self.assertEqual(processed, ["1", "2"])

    def test_requested_chapter_is_rechecked_even_when_platform_says_finished(self):
        points = [point("1", "done", completed=1, finished=True), point("2", "todo")]
        result, processed, _ = self.run_course(client(points), point_ids=["1"])
        self.assertTrue(result)
        self.assertEqual(processed, ["1"], "点名的章节不能因为已完成就被跳过")

    def test_a_chapter_that_disappeared_fails_instead_of_silently_skipping(self):
        points = [point("1", "one")]
        result, processed, _ = self.run_course(client(points), point_ids=["1", "404"])
        self.assertFalse(result)
        self.assertEqual(processed, [])

    def test_partial_selection_does_not_claim_the_whole_course_is_finished(self):
        points = [point("1", "one"), point("2", "two")]
        # 回读时第 1 节已完成，第 2 节仍未完成
        readback = [point("1", "one", completed=1, finished=True), point("2", "two")]
        result, processed, reporter = self.run_course(
            client(points, readback=readback), point_ids=["1"],
        )
        self.assertTrue(result, "只学的那一节完成了就算这个任务成功")
        self.assertEqual(processed, ["1"])
        self.assertEqual(self.phases(reporter, "course_result"), ["course_unconfirmed"])

    def test_finishing_every_chapter_still_confirms_the_course(self):
        points = [point("1", "one")]
        readback = [point("1", "one", completed=1, finished=True)]
        result, _, reporter = self.run_course(client(points, readback=readback))
        self.assertTrue(result)
        self.assertEqual(self.phases(reporter, "course_result"), ["course_confirmed"])

    def test_reported_chapter_total_matches_the_selection(self):
        points = [point("1", "one"), point("2", "two"), point("3", "three")]
        _, _, reporter = self.run_course(client(points), point_ids=["1", "3"])
        totals = [call.kwargs.get("total") for call in reporter.call_args_list
                  if call.args == ("course",)]
        self.assertEqual(totals, [2])


if __name__ == "__main__":
    unittest.main()
