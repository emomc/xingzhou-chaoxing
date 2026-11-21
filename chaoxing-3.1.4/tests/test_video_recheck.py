import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import main
from api.base import Chaoxing, StudyResult
from api.decode import decode_course_card, decode_course_point, is_task_passed
from api.exceptions import MaxRetryExceeded


COURSE = {"courseId": "course", "clazzId": "class", "cpi": "test", "title": "Test course"}
POINT = {"id": "chapter", "title": "Test chapter", "has_finished": False}
VIDEO = {"type": "video", "jobid": "video-job", "objectid": "video", "name": "Test video", "playTime": 0}
QUIZ = {"type": "workid", "jobid": "work-test"}


def setUpModule():
    """夜间静默按真实时钟判断：关掉它，否则半夜跑测试会一路等到 06:00。"""
    from api import video_guard

    video_guard.configure({"quiet_hours": False})


def tearDownModule():
    from api import video_guard

    video_guard.reset()


def video_card(**updates):
    return {
        "type": "video", "job": True, "jobid": "video-job", "objectId": "video",
        "isPassed": False, "property": {"name": "A video with spaces"},
    } | updates


def card_html(cards, defaults=None):
    return "var mArg = \n" + json.dumps({"attachments": cards, "defaults": defaults or {}}, indent=2) + ";"


class VideoDecodingTests(unittest.TestCase):
    def test_multiline_card_without_mid_keeps_video_and_name(self):
        jobs, _ = decode_course_card(card_html([video_card()]))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["name"], "A video with spaces")
        self.assertEqual(jobs[0]["mid"], "")
        self.assertEqual(jobs[0]["objectid"], "video")

    def test_missing_play_time_remains_unknown(self):
        jobs, _ = decode_course_card(card_html([video_card()]))
        self.assertIsNone(jobs[0]["playTime"])

    def test_invalid_play_time_is_unknown_but_real_zero_is_preserved(self):
        for value in (None, "", "not-a-number", -1, 1.5, True):
            with self.subTest(value=value):
                jobs, _ = decode_course_card(card_html([video_card(playTime=value)]))
                self.assertIsNone(jobs[0]["playTime"])
        jobs, _ = decode_course_card(card_html([video_card(playTime=0)]))
        self.assertEqual(jobs[0]["playTime"], 0)

    def test_false_strings_do_not_hide_unfinished_videos(self):
        for value in (False, "false", "False", 0, "0", None):
            with self.subTest(value=value):
                jobs, _ = decode_course_card(card_html([video_card(isPassed=value)]))
                self.assertEqual(len(jobs), 1)
        for value in (True, "true", " TRUE ", 1, "1"):
            with self.subTest(value=value):
                jobs, _ = decode_course_card(card_html([video_card(isPassed=value)]))
                self.assertEqual(jobs, [])
                self.assertTrue(is_task_passed(value))

    def test_missing_job_marker_does_not_discard_valid_video(self):
        jobs, _ = decode_course_card(card_html([video_card(job=None)]))
        self.assertEqual(len(jobs), 1)

    def test_missing_video_identifiers_are_reported_for_manual_check(self):
        jobs, info = decode_course_card(card_html([video_card(jobid="")]))
        self.assertEqual(jobs, [])
        self.assertEqual(info["untracked_videos"], ["A video with spaces"])

    def test_partly_finished_chapter_is_not_completed(self):
        for text, expected in (("已完成 1/2", False), ("已完成 2/2", True),
                               ("已完成1个，未完成1个", False)):
            with self.subTest(text=text):
                html = (
                    '<div class="chapter_unit"><ul><li><div id="cur123">'
                    '<a class="clicktitle">Lesson</a><span class="bntHoverTips">'
                    + text + "</span></div></li></ul></div>"
                )
                self.assertEqual(decode_course_point(html)["points"][0]["has_finished"], expected)


class ChapterVideoTests(unittest.TestCase):
    def setUp(self):
        self.chaoxing = Mock()
        self.chaoxing.get_job_list.return_value = ([QUIZ.copy(), VIDEO.copy()], {})
        self.chaoxing.get_course_point.return_value = {"points": [POINT]}

    def run_chapter(self, **options):
        return main.process_chapter(self.chaoxing, COURSE, POINT, 1, **options)

    def test_video_finishes_before_quiz_starts(self):
        order = []
        with patch("main.process_job", side_effect=lambda _, __, job, *args:
                   order.append(job["type"]) or StudyResult.SUCCESS):
            self.assertEqual(self.run_chapter(), main.ChapterResult.SUCCESS)
        self.assertEqual(order, ["video", "workid"])

    def test_failed_video_still_attempts_the_quiz_and_keeps_the_chapter_open(self):
        with patch("main.process_job", return_value=StudyResult.ERROR) as process:
            self.assertEqual(self.run_chapter(), main.ChapterResult.ERROR)
        self.assertEqual([call.args[2]["type"] for call in process.call_args_list], ["video", "workid"])

    def test_video_exception_does_not_discard_following_quiz(self):
        with patch("main.process_job", side_effect=[ValueError("test"), StudyResult.SUCCESS]) as process:
            self.assertEqual(self.run_chapter(), main.ChapterResult.ERROR)
        self.assertEqual([call.args[2]["type"] for call in process.call_args_list], ["video", "workid"])

    def test_video_timeout_does_not_retry_as_audio(self):
        self.chaoxing.study_video.return_value = StudyResult.TIMEOUT
        self.assertEqual(main.process_job(self.chaoxing, COURSE, VIDEO, {}, 1), StudyResult.TIMEOUT)
        self.chaoxing.study_video.assert_called_once()
        self.assertEqual(self.chaoxing.study_video.call_args.kwargs["_type"], "Video")

    def test_explicit_audio_metadata_retains_audio_support(self):
        self.chaoxing.study_video.side_effect = [StudyResult.NOT_VIDEO, StudyResult.SUCCESS]
        self.assertEqual(main.process_job(self.chaoxing, COURSE, VIDEO, {}, 1), StudyResult.SUCCESS)
        self.assertEqual(
            [call.kwargs["_type"] for call in self.chaoxing.study_video.call_args_list],
            ["Video", "Audio"],
        )

    def test_video_only_mode_never_processes_quizzes(self):
        with patch("main.process_job", return_value=StudyResult.SUCCESS) as process:
            self.assertEqual(self.run_chapter(videos_only=True), main.ChapterResult.SUCCESS)
        self.assertEqual(process.call_count, 1)
        self.chaoxing.get_job_list.assert_called_once_with(COURSE, POINT, visit_empty=False)

    def test_completed_chapter_rechecks_video_without_repeating_quiz(self):
        point = POINT | {"has_finished": True}
        with patch("main.process_job", return_value=StudyResult.SUCCESS) as process:
            result = main.process_chapter(self.chaoxing, COURSE, point, 1, recheck_completed=True)
        self.assertEqual(result, main.ChapterResult.SUCCESS)
        self.assertEqual(process.call_count, 1)
        self.chaoxing.get_job_list.assert_called_once_with(COURSE, point, visit_empty=False)

    def test_untracked_video_is_not_reported_as_completed(self):
        self.chaoxing.get_job_list.return_value = ([], {"untracked_videos": ["Manual video"]})
        self.assertEqual(self.run_chapter(videos_only=True), main.ChapterResult.MANUAL)

    def test_video_flag_is_applied_when_loading_config(self):
        args = SimpleNamespace(config="test.ini", videos_only=True, recheck_completed=False)
        configs = ({}, {"provider": "WebAI"}, {})
        with patch("main.parse_args", return_value=args), patch(
            "main.load_config_from_file", return_value=configs
        ):
            common, _, _ = main.init_config()
        self.assertTrue(common["videos_only"])
        self.assertTrue(common["recheck_completed"])


class VideoCardFetchTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.get.return_value.status_code = 200
        self.session.get.return_value.url = "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
        self.session.get.return_value.text = card_html([])
        self.chaoxing = Chaoxing()
        self.chaoxing.rate_limiter = Mock()
        self.chaoxing._chapter_card_count = Mock(return_value=None)
        self.chaoxing.study_emptypage = Mock()
        patcher = patch("api.base.SessionManager.get_session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_video_recheck_does_not_mark_empty_chapter_read(self):
        jobs, _ = self.chaoxing.get_job_list(COURSE, POINT, visit_empty=False)
        self.assertEqual(jobs, [])
        self.chaoxing.study_emptypage.assert_not_called()

    def test_failed_card_read_is_not_an_empty_completed_chapter(self):
        self.session.get.return_value.status_code = 503
        with self.assertRaises(MaxRetryExceeded):
            self.chaoxing.get_job_list(COURSE, POINT)
        self.chaoxing.study_emptypage.assert_not_called()

    def test_untracked_videos_from_all_cards_are_preserved(self):
        self.session.get.return_value.text = card_html([video_card(jobid="")])
        jobs, info = self.chaoxing.get_job_list(COURSE, POINT)
        self.assertEqual(jobs, [])
        self.assertEqual(info["untracked_videos"], ["A video with spaces"])
        self.chaoxing.study_emptypage.assert_not_called()

    def test_each_card_keeps_its_own_quiz_parameters(self):
        def response(text):
            return Mock(status_code=200, text=text, url="https://mooc1.chaoxing.com/mooc-ans/knowledge/cards")
        quiz = {"type": "workid", "job": True, "jobid": "work-test", "property": {}}
        self.session.get.side_effect = [
            response(card_html([video_card()], {"ktoken": "video-token", "knowledgeid": "chapter"})),
            response(card_html([quiz], {"ktoken": "quiz-token", "knowledgeid": "chapter"})),
        ] + [response(card_html([])) for _ in range(5)]
        jobs, _ = self.chaoxing.get_job_list(COURSE, POINT)
        self.assertEqual([job["_card_num"] for job in jobs], ["0", "1"])
        self.assertEqual([job["_job_info"]["ktoken"] for job in jobs], ["video-token", "quiz-token"])

    def test_card_confirmation_requires_explicit_target_passed_state(self):
        for cards, expected in [
            ([], False), ([video_card(isPassed=False)], False),
            ([video_card(isPassed="true")], True),
            ([video_card(jobid="another", isPassed=True)], False),
        ]:
            with self.subTest(cards=cards):
                self.session.get.return_value.text = card_html(cards)
                self.assertEqual(
                    self.chaoxing._video_card_passed(COURSE, VIDEO, {"knowledgeid": "chapter"}), expected,
                )


class ChapterRetryTests(unittest.TestCase):
    def processor(self):
        return main.JobProcessor(Mock(), COURSE, [main.ChapterTask(0, POINT)], {
            "speed": 1, "jobs": 1, "notopen_action": "retry",
            "videos_only": True, "recheck_completed": True,
        })

    def test_chapter_exception_is_retried_and_reported(self):
        processor = self.processor()
        with patch("main.process_chapter", side_effect=ValueError("test failure")) as process, patch(
            "main.time.sleep"
        ):
            self.assertFalse(processor.run())
        self.assertEqual(process.call_count, 5)
        self.assertEqual(len(processor.failed_tasks), 1)
        self.assertTrue(all(not thread.is_alive() for thread in processor.threads))

    def test_closed_chapter_retries_are_bounded(self):
        processor = self.processor()
        with patch("main.process_chapter", return_value=main.ChapterResult.NOT_OPEN) as process, patch(
            "main.time.sleep"
        ):
            self.assertFalse(processor.run())
        self.assertEqual(process.call_count, 5)
        self.assertEqual(len(processor.failed_tasks), 1)

    def test_manual_video_is_reported_without_repeated_requests(self):
        processor = self.processor()
        with patch("main.process_chapter", return_value=main.ChapterResult.MANUAL) as process:
            self.assertFalse(processor.run())
        process.assert_called_once()
        self.assertEqual(len(processor.failed_tasks), 1)

    def test_empty_course_is_not_reported_as_completed(self):
        chaoxing = Mock()
        chaoxing.get_course_point.return_value = {"points": []}
        self.assertFalse(main.process_course(chaoxing, COURSE, {"speed": 1, "jobs": 1}))


class VideoProgressTests(unittest.TestCase):
    def setUp(self):
        from api import video_guard

        # 夜间静默按真实时钟判断：测试里关掉，否则半夜跑测试会先等到 06:00
        video_guard.configure({"quiet_hours": False})
        self.session = Mock()
        self.session.get.return_value.json.return_value = {
            "status": "success", "duration": 2, "dtoken": "test-token",
        }
        self.chaoxing = Chaoxing()
        self.chaoxing.VIDEO_CONFIRM_TIMEOUT = 6
        self.chaoxing.VIDEO_CONFIRM_INTERVAL = 2
        self.chaoxing._refresh_video_status = Mock(return_value={
            "status": "success", "duration": 2, "dtoken": "test-token",
        })
        self.chaoxing._video_card_passed = Mock(return_value=False)
        self.chaoxing.get_fid = Mock(return_value="test-fid")
        self.chaoxing.video_progress_log = Mock(side_effect=[(False, 200), (True, 200)])
        self.chaoxing._recover_after_forbidden = Mock(return_value={"dtoken": "refreshed"})
        self.now = 0
        for name, options in (
            ("api.base.SessionManager.get_session", {"return_value": self.session}),
            ("api.base.tqdm", {}),
            ("api.base.time.sleep", {"side_effect": self.sleep}),
            ("api.base.time.monotonic", {"side_effect": lambda: self.now}),
        ):
            patcher = patch(name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.now += seconds

    def run_video(self, job=None):
        return self.chaoxing.study_video(COURSE, job or VIDEO, {})

    def positions(self):
        return [call.args[6] for call in self.chaoxing.video_progress_log.call_args_list]

    def test_end_position_is_not_reported_before_elapsed_playback(self):
        calls_at_report = []

        def report(*args, **kwargs):
            calls_at_report.append(self.now)
            return (len(calls_at_report) > 1, 200)

        self.chaoxing.video_progress_log.side_effect = report
        self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        self.assertEqual(self.positions(), [0, 2])
        self.assertGreaterEqual(calls_at_report[1], 2)

    def test_missing_completion_acknowledgement_remains_incomplete(self):
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 200)
        self.assertEqual(self.run_video(), StudyResult.TIMEOUT)
        self.assertEqual(self.positions(), [0, 2, 2, 2, 2])
        self.assertGreaterEqual(self.now, 8)

    def test_late_completion_acknowledgement_after_three_reports_is_accepted(self):
        self.chaoxing.video_progress_log.side_effect = [(False, 200)] * 4 + [(True, 200)]
        self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        self.assertEqual(self.positions(), [0, 2, 2, 2, 2])

    def test_card_completion_acknowledgement_is_accepted(self):
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 200)
        self.chaoxing._video_card_passed.return_value = True
        self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        self.assertEqual(self.positions(), [0, 2])

    def test_full_position_without_completion_is_replayed_once(self):
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 200)
        self.assertEqual(self.run_video(VIDEO | {"playTime": 2000}), StudyResult.TIMEOUT)
        self.assertEqual(self.positions(), [2, 2, 2, 2, 0, 2, 2, 2, 2])

    def test_progress_ticks_without_extra_upstream_requests(self):
        with patch("api.base.report") as progress:
            self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        events = [call.kwargs for call in progress.call_args_list]
        self.assertTrue(any(event["position"] == 1 for event in events))
        self.assertTrue(all(event["course_id"] == "course" for event in events))
        self.assertEqual(events[-1]["video_state"], "passed")
        self.assertEqual(self.positions(), [0, 2])

    def test_existing_completion_uses_only_current_position(self):
        self.chaoxing.video_progress_log.side_effect = [(True, 200)]
        self.assertEqual(self.run_video(VIDEO | {"playTime": 1000}), StudyResult.SUCCESS)
        self.assertEqual(self.positions(), [1])

    def test_expired_video_status_is_refreshed_before_retry(self):
        self.chaoxing.video_progress_log.side_effect = [(False, 403), (True, 200)]
        self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        self.chaoxing._recover_after_forbidden.assert_called_once()
        self.assertEqual(self.positions(), [0, 0])

    def test_non_success_response_does_not_complete_video(self):
        self.chaoxing.video_progress_log.side_effect = [(False, 400)]
        self.assertEqual(self.run_video(), StudyResult.ERROR)
        self.assertEqual(self.positions(), [0])

    def test_temporary_report_failure_keeps_position_and_retries_until_acknowledged(self):
        self.chaoxing.video_progress_log.side_effect = [
            (False, 200), (False, 502), (False, 0), (True, 200),
        ]
        self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        self.assertEqual(self.positions(), [0, 2, 2, 2])

    def test_report_backoff_does_not_count_as_new_playback(self):
        self.chaoxing.video_progress_log.side_effect = [(False, 503), (True, 200)]
        self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        self.assertEqual(self.positions(), [0, 0])

    def test_temporary_report_failures_are_bounded_and_never_mean_success(self):
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 503)
        self.assertEqual(self.run_video(), StudyResult.ERROR)
        self.assertEqual(len(self.positions()), 4)

    def test_card_confirmation_after_network_error_does_not_repeat_a_passed_video(self):
        self.chaoxing.video_progress_log.side_effect = [(False, 200), (False, 0)]
        self.chaoxing._video_card_passed.return_value = True
        self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        self.assertEqual(self.positions(), [0, 2])

    def test_audio_filename_is_identified_without_a_failed_video_report(self):
        self.chaoxing._refresh_video_status.return_value["filename"] = "lesson.mp3"
        self.assertEqual(self.run_video(), StudyResult.NOT_VIDEO)
        self.chaoxing.video_progress_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class ListedChapterTests(unittest.TestCase):
    def config(self, **extra):
        return {"speed": 1, "jobs": 1, "notopen_action": "continue", "videos_only": False, "recheck_completed": False} | extra

    def points(self):
        return {"points": [
            {"id": "1", "title": "Finished", "jobCount": 1, "completedJobCount": 1, "has_finished": True},
            {"id": "2", "title": "Remaining", "jobCount": 2, "completedJobCount": 0, "has_finished": False},
        ]}

    def test_finished_chapters_are_not_opened_and_remaining_chapters_keep_every_job(self):
        chaoxing = Mock()
        finished = {"points": [
            point | {"has_finished": True, "completedJobCount": point["jobCount"]}
            for point in self.points()["points"]
        ]}
        chaoxing.get_course_point.side_effect = [self.points(), finished]
        chaoxing.get_job_list.return_value = ([QUIZ.copy(), VIDEO.copy()], {})
        with patch("main.process_job", return_value=StudyResult.SUCCESS) as process:
            self.assertTrue(main.process_course(chaoxing, COURSE, self.config()))
        chaoxing.get_job_list.assert_called_once()
        self.assertEqual(chaoxing.get_job_list.call_args.args[1]["id"], "2")
        self.assertEqual([call.args[2]["type"] for call in process.call_args_list], ["video", "workid"])

    def test_successful_job_calls_do_not_override_unfinished_platform_outline(self):
        chaoxing = Mock()
        chaoxing.get_course_point.return_value = self.points()
        chaoxing.get_job_list.return_value = ([VIDEO.copy()], {})
        with patch("main.process_job", return_value=StudyResult.SUCCESS):
            self.assertFalse(main.process_course(chaoxing, COURSE, self.config()))
        self.assertEqual(chaoxing.get_course_point.call_count, 2)

    def test_remaining_chapter_without_jobs_does_not_complete_the_course(self):
        chaoxing = Mock()
        chaoxing.get_course_point.return_value = {"points": [self.points()["points"][1]]}
        chaoxing.get_job_list.return_value = ([], {})
        with patch("main.process_job", return_value=StudyResult.SUCCESS):
            self.assertFalse(main.process_course(chaoxing, COURSE, self.config()))
        chaoxing.get_job_list.assert_called_once()

    def test_video_only_still_rechecks_finished_chapters(self):
        chaoxing = Mock()
        chaoxing.get_course_point.return_value = {"points": [self.points()["points"][0]]}
        chaoxing.get_job_list.return_value = ([VIDEO.copy(), QUIZ.copy()], {})
        with patch("main.process_job", return_value=StudyResult.SUCCESS) as process:
            self.assertTrue(main.process_course(chaoxing, COURSE, self.config(videos_only=True, recheck_completed=True)))
        chaoxing.get_job_list.assert_called_once()
        self.assertEqual([call.args[2]["type"] for call in process.call_args_list], ["video"])


class VideoGuardLoopTests(unittest.TestCase):
    """看护开关真的接进了播放循环：倍速、夜间静默、回退对齐。"""

    DURATION = 300

    def setUp(self):
        from api import video_guard

        video_guard.reset()
        # 夜间静默按真实时钟判断；测试里关掉，否则半夜跑测试会真的等到 06:00
        video_guard.configure({"quiet_hours": False})
        self.addCleanup(video_guard.reset)
        self.session = Mock()
        status = {"status": "success", "duration": self.DURATION, "dtoken": "test-token"}
        self.session.get.return_value.json.return_value = status
        self.chaoxing = Chaoxing()
        self.chaoxing.VIDEO_CONFIRM_TIMEOUT = 6
        self.chaoxing.VIDEO_CONFIRM_INTERVAL = 2
        self.chaoxing._refresh_video_status = Mock(return_value=dict(status))
        self.chaoxing._video_card_passed = Mock(return_value=False)
        self.chaoxing._read_card_play_time = Mock(return_value=None)
        self.chaoxing.get_fid = Mock(return_value="test-fid")
        self.chaoxing.video_progress_log = Mock(side_effect=[(False, 200), (True, 200)])
        self.now = 0
        for name, options in (
            ("api.base.SessionManager.get_session", {"return_value": self.session}),
            ("api.base.tqdm", {}),
            ("api.base.time.sleep", {"side_effect": self.sleep}),
            ("api.base.time.monotonic", {"side_effect": lambda: self.now}),
            ("api.video_guard.time.sleep", {"side_effect": self.sleep}),
        ):
            patcher = patch(name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.now += seconds

    def run_video(self, job=None, speed=1.0):
        return self.chaoxing.study_video(COURSE, job or VIDEO, {}, _speed=speed)

    def positions(self):
        return [call.args[6] for call in self.chaoxing.video_progress_log.call_args_list]

    def test_requested_speed_is_capped_before_playback(self):
        from api import video_guard

        with patch.object(video_guard, "clamp_speed", wraps=video_guard.clamp_speed) as clamp, patch(
            "api.base.report"
        ):
            self.assertEqual(self.run_video(speed=8), StudyResult.SUCCESS)
        self.assertEqual(clamp.call_args.args[0], 8)
        self.assertEqual(video_guard.settings()["max_speed"], 0.95,
                         "默认 0.95 慢放，请求 8 倍也只能 0.95")

    def test_quiet_hours_are_announced_before_a_new_video(self):
        from api import video_guard

        def wait(**kwargs):
            kwargs["on_wait"]("夜间静默时段")
            return 600

        with patch.object(video_guard, "wait_for_window", side_effect=wait), patch(
            "api.base.report"
        ) as progress:
            self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        states = [call.kwargs.get("video_state") for call in progress.call_args_list]
        self.assertIn("waiting", states)

    def test_rollback_resyncs_to_the_platform_position_and_slows_down(self):
        from api import video_guard

        video_guard.configure({"recheck": 1, "auto_pause": False})
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 200)
        # 已经看到 120s，平台却把进度清回 0：这是真回退
        video_guard.note("course:video", 120)
        self.chaoxing._read_card_play_time.return_value = 0
        with patch("api.base.report") as progress:
            self.assertEqual(self.run_video(), StudyResult.TIMEOUT)
        rollbacks = [call for call in progress.call_args_list if call.args[0] == "video_rollback"]
        self.assertGreaterEqual(len(rollbacks), 1)
        self.assertEqual(rollbacks[0].kwargs["rollback_action"], "slow")
        self.assertEqual(rollbacks[0].kwargs["rollback_to"], 0)
        self.assertGreater(rollbacks[0].kwargs["rollback_dropped"], video_guard.TOLERANCE)
        states = [call.kwargs.get("video_state") for call in progress.call_args_list]
        self.assertIn("rolled_back", states)
        self.assertIn(0, self.positions()[1:], "回退后必须从平台记录的位置重新上报")

    def test_a_transient_low_reading_does_not_rewind_playback_or_emit_rollback(self):
        from api import video_guard

        video_guard.configure({"recheck": 1, "auto_pause": False})
        self.chaoxing.video_progress_log.side_effect = [(False, 200), (True, 200)]
        video_guard.note("course:video", 120)
        readings = iter([0, 120])
        self.chaoxing._read_card_play_time.side_effect = lambda *a, **k: next(readings, 120)
        with patch("api.base.report") as progress:
            self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        rollbacks = [call for call in progress.call_args_list if call.args[0] == "video_rollback"]
        self.assertEqual(rollbacks, [])
        self.assertEqual(video_guard.rollback_count("course:video"), 0)
        positions = self.positions()
        self.assertEqual(positions, sorted(positions), "瞬时异常不能让本地播放位置倒退")

    def test_auto_pause_waits_before_resuming(self):
        from api import video_guard

        video_guard.configure({"recheck": 1, "auto_pause": True})
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 200)
        video_guard.note("course:video", 120)
        self.chaoxing._read_card_play_time.return_value = 0
        with patch.object(video_guard, "pause_seconds", return_value=120), patch("api.base.report"):
            self.assertEqual(self.run_video(), StudyResult.TIMEOUT)
        self.assertGreaterEqual(self.now, 120, "回退后应暂停再继续")

    def test_repeated_rollbacks_give_up_instead_of_looping(self):
        from api import video_guard

        video_guard.configure({"recheck": 1, "auto_pause": False})
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 200)
        video_guard.note("course:video", 400)
        # 每次回退都需要一对连续、独立的低位读数确认。
        readings = iter([200, 200, 150, 150, 100, 100, 0, 0])
        self.chaoxing._read_card_play_time.side_effect = lambda *a, **k: next(readings, 0)
        with patch("api.base.report"):
            self.assertEqual(self.run_video(), StudyResult.TIMEOUT)
        self.assertEqual(video_guard.rollback_count("course:video"), video_guard.MAX_ROLLBACKS + 1)

    def test_missing_or_invalid_card_play_time_is_unknown_not_zero(self):
        reader = Chaoxing()
        for card in (
            {"jobid": "video-job"},
            {"jobid": "video-job", "playTime": None},
            {"jobid": "video-job", "playTime": ""},
            {"jobid": "video-job", "playTime": "not-a-number"},
            {"jobid": "video-job", "playTime": -1000},
        ):
            with self.subTest(card=card):
                reader._read_cards = Mock(return_value=([card], {}))
                self.assertIsNone(
                    reader._read_card_play_time(COURSE, VIDEO, {}),
                )
        reader._read_cards = Mock(return_value=([{
            "jobid": "video-job", "playTime": "7000",
        }], {}))
        self.assertEqual(reader._read_card_play_time(COURSE, VIDEO, {}), 7)

    def test_unreadable_platform_position_never_breaks_playback(self):
        self.chaoxing._read_card_play_time.return_value = None
        with patch("api.base.report"):
            self.assertEqual(self.run_video(), StudyResult.SUCCESS)

    def test_a_non_json_report_refreshes_the_media_token(self):
        """回来的不是 JSON 基本就是 dtoken 过期：要换 token，不是换 rt 或硬重试。"""
        self.chaoxing.video_progress_log.side_effect = [(False, 426), (True, 200)]
        with patch("api.base.report"):
            self.assertEqual(self.run_video(), StudyResult.SUCCESS)
        # 1 次是开头取 token，1 次是 426 之后换 token
        self.assertEqual(self.chaoxing._refresh_video_status.call_count, 2)

    def test_a_permanently_stale_token_gives_up_after_two_refreshes(self):
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 426)
        with patch("api.base.report"):
            self.assertEqual(self.run_video(), StudyResult.ERROR)
        self.assertEqual(self.chaoxing._refresh_video_status.call_count, 3,
                         "开头 1 次 + 最多 2 次换 token，之后就放弃这个任务点")

    def test_a_platform_reading_one_report_cycle_behind_is_not_a_rollback(self):
        """线上真实形态：刚上报完就回读，卡片里还是上一轮的位置。

        404 -> 346 正好是一个上报周期（60s × 0.95 ≈ 57s），以前被当成平台回退，
        白白倒回去重看一分钟。滞后必须算进容差。
        """
        from api import video_guard

        video_guard.configure({"recheck": 1, "auto_pause": False})
        self.chaoxing.video_progress_log.side_effect = None
        self.chaoxing.video_progress_log.return_value = (False, 200)

        def one_cycle_behind(*_args, **_kwargs):
            reported = self.positions()
            return max(0, (reported[-1] if reported else 0) - 60)

        self.chaoxing._read_card_play_time.side_effect = one_cycle_behind
        with patch("api.base.report") as progress:
            self.run_video()
        rollbacks = [call for call in progress.call_args_list if call.args[0] == "video_rollback"]
        self.assertEqual(rollbacks, [], "自己的上报节奏不能算成平台回退")
        self.assertEqual(video_guard.rollback_count("course:video"), 0)
