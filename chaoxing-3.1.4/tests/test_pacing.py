"""拟人节奏的单元测试：区间、随机分组、饭点/夜间窗口、开关与降级。"""

import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from api import pacing


def local_epoch(year, month, day, hour, minute, utc=False):
    if utc:
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


class Sleeper:
    """记录所有 sleep 调用，绝不真的等。"""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(round(float(seconds), 3))

    @property
    def total(self):
        return round(sum(self.calls), 3)


def build(**kwargs):
    sleeper = kwargs.pop("sleeper", None) or Sleeper()
    options = {
        "enabled": True,
        "seed": 20261022,
        "sleep": sleeper,
        "group_size": (2, 3),
        "micro_break_chance": 0.0,
        "windows": (),
    }
    options.update(kwargs)
    pacer = pacing.Pacer(**options)
    return pacer, sleeper


class PacerRangeTest(unittest.TestCase):
    def test_disabled_pacer_never_sleeps(self):
        pacer, sleeper = build(enabled=False)
        self.assertFalse(pacer.after_video())
        self.assertFalse(pacer.before_answer())
        self.assertFalse(pacer.between_courses())
        self.assertFalse(pacer.rest_if_needed())
        self.assertEqual(sleeper.calls, [])

    def test_after_video_defaults_to_one_to_fifteen_seconds(self):
        for seed in range(12):
            pacer, sleeper = build(seed=seed)
            pause = pacer.after_video()
            self.assertGreaterEqual(pause.seconds, 1.0)
            self.assertLessEqual(pause.seconds, 15.0)
            self.assertAlmostEqual(sleeper.total, pause.seconds, places=2)

    def test_every_gap_respects_its_configured_range(self):
        gaps = {"answer": (1.0, 3.0), "job": (2.0, 4.0), "chapter": (5.0, 7.0)}
        pacer, _ = build(gaps=gaps)
        for _ in range(20):
            self.assertLessEqual(1.0, pacer.before_answer().seconds)
            self.assertLessEqual(pacer.before_answer().seconds, 3.0)
            self.assertLessEqual(2.0, pacer.between_jobs().seconds)
            self.assertLessEqual(pacer.between_jobs().seconds, 4.0)
            self.assertLessEqual(5.0, pacer.between_chapters().seconds)
            self.assertLessEqual(pacer.between_chapters().seconds, 7.0)

    def test_minimum_floor_wins_over_random_draw(self):
        pacer, sleeper = build(gaps={"answer": (0.0, 0.0)})
        pause = pacer.before_answer(minimum=7.5)
        self.assertAlmostEqual(pause.seconds, 7.5, places=2)
        self.assertAlmostEqual(sleeper.total, 7.5, places=2)

    def test_sleep_is_split_into_small_slices(self):
        pacer, sleeper = build(gaps={"chapter": (12.0, 12.0)})
        pacer.between_chapters()
        self.assertEqual(sleeper.total, 12.0)
        self.assertTrue(all(slice_ <= pacing.SLEEP_SLICE for slice_ in sleeper.calls))

    def test_same_seed_repeats_the_same_rhythm(self):
        first, _ = build(seed=7)
        second, _ = build(seed=7)
        left = [first.after_video().seconds for _ in range(5)]
        right = [second.after_video().seconds for _ in range(5)]
        self.assertEqual(left, right)

    def test_micro_break_uses_its_own_range(self):
        pacer, _ = build(micro_break_chance=1.0, gaps={"micro": (60.0, 120.0)})
        pause = pacer.maybe_micro_break()
        self.assertGreaterEqual(pause.seconds, 60.0)
        self.assertLessEqual(pause.seconds, 120.0)

    def test_reporter_receives_pacing_phase(self):
        events = []

        def reporter(event, **data):
            events.append((event, data))

        pacer, _ = build(reporter=reporter)
        pacer.after_video()
        self.assertTrue(events)
        event, data = events[0]
        self.assertEqual(event, "phase")
        self.assertEqual(data["phase"], "pacing")
        self.assertIn("秒", data["name"])


class PacerWindowTest(unittest.TestCase):
    def test_lunch_window_pauses_until_thirteen_hundred(self):
        now = local_epoch(2026, 10, 22, 12, 10)
        pacer, sleeper = build(
            windows=(("lunch", (11, 40), (13, 0), 1.0, 3600.0),), now=lambda: now,
        )
        pause = pacer.rest_if_needed()
        self.assertTrue(pause)
        self.assertAlmostEqual(pause.seconds, 50 * 60, delta=1)
        self.assertIn("午饭", pause.reason)
        self.assertFalse(pause.capped)
        self.assertAlmostEqual(sleeper.total, pause.seconds, delta=1)

    def test_the_same_window_only_pauses_once_a_day(self):
        now = local_epoch(2026, 10, 22, 12, 10)
        pacer, _ = build(
            windows=(("lunch", (11, 40), (13, 0), 1.0, 3600.0),), now=lambda: now,
        )
        self.assertTrue(pacer.rest_if_needed())
        self.assertFalse(pacer.rest_if_needed())

    def test_night_window_wraps_past_midnight_and_is_capped(self):
        now = local_epoch(2026, 10, 23, 2, 0)
        pacer, _ = build(
            windows=(("night", (23, 30), (7, 0), 1.0, 5400.0),), now=lambda: now,
        )
        pause = pacer.rest_if_needed()
        self.assertTrue(pause)
        self.assertAlmostEqual(pause.seconds, 5400.0, delta=1)
        self.assertTrue(pause.capped)

    def test_outside_the_window_nothing_happens(self):
        now = local_epoch(2026, 10, 22, 9, 15)
        pacer, sleeper = build(
            windows=(("lunch", (11, 40), (13, 0), 1.0, 3600.0),), now=lambda: now,
        )
        self.assertFalse(pacer.rest_if_needed())
        self.assertEqual(sleeper.calls, [])

    def test_zero_chance_window_never_pauses(self):
        now = local_epoch(2026, 10, 22, 12, 10)
        pacer, _ = build(
            windows=(("lunch", (11, 40), (13, 0), 0.0, 3600.0),), now=lambda: now,
        )
        self.assertFalse(pacer.rest_if_needed())

    def test_default_windows_cover_lunch_dinner_and_night(self):
        pacer = pacing.Pacer(enabled=True, seed=1, sleep=lambda seconds: None)
        names = [item[0] for item in pacer.windows]
        self.assertEqual(names, ["lunch", "dinner", "night"])

    def test_the_default_night_window_rests_until_morning(self):
        # 凌晨还醒着就应该一觉睡到 07:00，而不是停 90 分钟又接着刷
        now = local_epoch(2026, 10, 23, 0, 30)
        sleeper = Sleeper()
        pacer = pacing.Pacer(enabled=True, seed=1, sleep=sleeper, now=lambda: now)
        pause = pacer.rest_if_needed()
        self.assertTrue(pause)
        self.assertEqual(pause.reason.count("夜间"), 1)
        self.assertGreater(pause.seconds, 6 * 3600)

    def test_timezone_offset_is_used_for_windows(self):
        # 04:10 UTC 就是北京 12:10，应该命中午饭窗口
        now = local_epoch(2026, 10, 22, 4, 10, utc=True)
        pacer, _ = build(
            windows=(("lunch", (11, 40), (13, 0), 1.0, 3600.0),),
            now=lambda: now, tz="+08:00",
        )
        pause = pacer.rest_if_needed()
        self.assertTrue(pause)
        self.assertAlmostEqual(pause.seconds, 50 * 60, delta=1)

    def test_same_instant_without_timezone_does_not_hit_lunch(self):
        now = local_epoch(2026, 10, 22, 4, 10, utc=True)
        pacer, _ = build(
            windows=(("lunch", (11, 40), (13, 0), 1.0, 3600.0),),
            now=lambda: now, tz="+00:00",
        )
        self.assertFalse(pacer.rest_if_needed())

    def test_iana_timezone_resolves_on_linux(self):
        zone = pacing.resolve_timezone("Asia/Shanghai")
        if zone is None:
            self.skipTest("当前环境没有 tzdata")
        self.assertIsNotNone(zone)

    def test_clock_offsets_parse_common_spellings(self):
        for text in ("UTC+8", "+08:00", "8", "GMT+8"):
            with self.subTest(text=text):
                zone = pacing.resolve_timezone(text)
                self.assertIsNotNone(zone)
                self.assertEqual(zone.utcoffset(None), timezone(timedelta(hours=8)).utcoffset(None))
        self.assertIsNone(pacing.resolve_timezone("not a zone"))


class PacerPlanTest(unittest.TestCase):
    def test_plan_keeps_every_course_and_groups_them(self):
        courses = [{"courseId": str(index)} for index in range(8)]
        pacer, _ = build(group_size=(2, 2))
        plan = pacer.plan_courses(courses)
        self.assertEqual(
            sorted(item[0]["courseId"] for item in plan),
            [course["courseId"] for course in courses],
        )
        self.assertIsNone(plan[0][1])
        waits = [wait for _, wait in plan]
        self.assertNotIn(None, waits[1:])
        self.assertEqual(waits.count("group"), 3)
        self.assertEqual(waits.count("course"), 4)

    def test_disabled_plan_keeps_original_order(self):
        courses = [{"courseId": "b"}, {"courseId": "a"}]
        pacer, _ = build(enabled=False)
        self.assertEqual(pacer.plan_courses(courses), [(courses[0], None), (courses[1], None)])

    def test_plan_shuffles_course_order(self):
        courses = [{"courseId": str(index)} for index in range(12)]
        pacer, _ = build(seed=5)
        order = [item[0]["courseId"] for item in pacer.plan_courses(courses)]
        self.assertNotEqual(order, [course["courseId"] for course in courses])


class ChapterPacingTest(unittest.TestCase):
    """确认节奏真的插进了刷课流程，而不只是一个孤立的工具类。"""

    course = {"courseId": "course", "clazzId": "class", "cpi": "test", "title": "Test course"}
    point = {"id": "chapter", "title": "Test chapter", "has_finished": False}
    video = {"type": "video", "jobid": "video-job", "objectid": "video", "name": "Test video", "playTime": 0}
    quiz = {"type": "workid", "jobid": "work-test"}

    def run_chapter(self, pacer):
        from unittest.mock import Mock, patch
        import main
        from api.base import StudyResult

        chaoxing = Mock()
        chaoxing.get_job_list.return_value = ([self.quiz.copy(), self.video.copy()], {})
        chaoxing.get_course_point.return_value = {"points": [self.point]}
        with patch("main.pacing.current", return_value=pacer), \
                patch("main.process_job", return_value=StudyResult.SUCCESS):
            return main.process_chapter(chaoxing, self.course, self.point, 1)

    def test_video_to_quiz_transition_pauses(self):
        pacer, sleeper = build(gaps={"answer": (1.0, 15.0), "job": (2.0, 18.0)})
        self.run_chapter(pacer)
        self.assertTrue(sleeper.calls, "视频看完到答题之间应该有一段时间停顿")
        self.assertGreaterEqual(sleeper.total, 1.0)
        self.assertLessEqual(sleeper.total, 15.0 + 18.0)

    def test_disabled_pacing_keeps_the_batch_fast(self):
        pacer, sleeper = build(enabled=False)
        self.run_chapter(pacer)
        self.assertEqual(sleeper.calls, [])


class RunCoursesPacingTest(unittest.TestCase):
    def test_courses_are_grouped_and_paced(self):
        from backend.worker import run_courses

        pacer, sleeper = build(group_size=(2, 2), gaps={"start": (10.0, 10.0),
                                                        "course": (120.0, 120.0),
                                                        "group": (600.0, 600.0)})
        courses = [{"courseId": str(index), "title": f"c{index}"} for index in range(5)]
        processed = []
        success, failed = run_courses(
            lambda course: processed.append(course["courseId"]) or True, courses, pacer=pacer,
        )
        self.assertTrue(success)
        self.assertEqual(failed, [])
        self.assertEqual(sorted(processed), [str(index) for index in range(5)])
        # 开始前的准备 + 组内间隔 + 组间大休息
        self.assertGreaterEqual(sleeper.total, 10.0 + 120.0 + 120.0 + 600.0)

    def test_disabled_pacing_keeps_course_order(self):
        from backend.worker import run_courses

        pacer, sleeper = build(enabled=False)
        courses = [{"courseId": str(index), "title": f"c{index}"} for index in range(4)]
        processed = []
        run_courses(lambda course: processed.append(course["courseId"]) or True, courses, pacer=pacer)
        self.assertEqual(processed, [str(index) for index in range(4)])
        self.assertEqual(sleeper.calls, [])


class ConfigureTest(unittest.TestCase):
    def setUp(self):
        self.previous = {key: os.environ.get(key) for key in
                         ("CHAOXING_PACING", "CHAOXING_PACING_ANSWER", "CHAOXING_PACING_SEED",
                          "CHAOXING_PACING_TZ", "CHAOXING_PACING_EXIT",
                          "CHAOXING_PACING_EXIT_AFTER")}
        pacing.reset()

    def tearDown(self):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        pacing.reset()

    def test_configure_turns_pacing_on_by_default(self):
        pacer = pacing.configure({}, sleep=lambda seconds: None)
        self.assertTrue(pacer.enabled)
        self.assertIs(pacing.current(), pacer)

    def test_environment_can_disable_pacing(self):
        os.environ["CHAOXING_PACING"] = "0"
        self.assertFalse(pacing.configure({"enabled": True}, sleep=lambda seconds: None).enabled)

    def test_environment_can_override_one_gap(self):
        os.environ["CHAOXING_PACING_ANSWER"] = "30,40"
        pacer = pacing.configure({}, sleep=lambda seconds: None)
        self.assertEqual(pacer.gaps["answer"], (30.0, 40.0))

    def test_environment_timezone_is_used(self):
        os.environ["CHAOXING_PACING_TZ"] = "+08:00"
        pacer = pacing.configure({}, sleep=lambda seconds: None)
        self.assertEqual(pacer.describe()["timezone"], "+08:00")
        self.assertIsNotNone(pacer.tz)

    def test_reset_restores_the_disabled_default(self):
        pacing.configure({}, sleep=lambda seconds: None)
        self.assertFalse(pacing.reset().enabled)

    def test_describe_lists_ranges_for_diagnostics(self):
        pacer = pacing.configure({}, sleep=lambda seconds: None)
        described = pacer.describe()
        self.assertEqual(described["gaps"]["answer"], [1.0, 15.0])
        self.assertEqual([item["name"] for item in described["windows"]],
                         ["lunch", "dinner", "night"])

    def test_environment_can_disable_logout_on_long_breaks(self):
        os.environ["CHAOXING_PACING_EXIT"] = "0"
        self.assertFalse(pacing.configure({}, sleep=lambda seconds: None).exit_on_long_pause)

    def test_environment_can_change_the_logout_threshold(self):
        os.environ["CHAOXING_PACING_EXIT_AFTER"] = "120"
        self.assertEqual(pacing.configure({}, sleep=lambda seconds: None).exit_after, 120.0)


class SnapshotTest(unittest.TestCase):
    """控制台"拟人节奏"页面依赖的只读快照。"""

    def tearDown(self):
        pacing.reset()

    def test_timeline_marks_the_active_window(self):
        now = local_epoch(2026, 10, 22, 4, 10, utc=True)  # 北京 12:10
        data = pacing.snapshot({"enabled": True, "tz": "+08:00", "windows": (
            ("lunch", (11, 40), (13, 0), 1.0, 3600.0),
            ("night", (23, 30), (7, 0), 1.0, 5400.0),
        )}, now=now)
        windows = {item["name"]: item for item in data["windows"]}
        self.assertTrue(windows["lunch"]["active"])
        self.assertEqual(windows["lunch"]["start"], "11:40")
        self.assertEqual(windows["lunch"]["end"], "13:00")
        self.assertEqual(windows["lunch"]["start_minute"], 700)
        self.assertEqual(windows["lunch"]["end_minute"], 780)
        self.assertEqual(windows["lunch"]["starts_in_minutes"], 0)
        self.assertFalse(windows["night"]["active"])
        self.assertTrue(windows["night"]["wraps"])
        self.assertEqual(windows["night"]["cap_minutes"], 90)
        self.assertEqual(windows["night"]["starts_in_minutes"], (23 * 60 + 30) - 730)
        self.assertEqual(data["clock"]["time"], "12:10")
        self.assertEqual(data["clock"]["minute_of_day"], 730)
        self.assertEqual(data["clock"]["timezone"], "+08:00")

    def test_snapshot_does_not_replace_the_current_pacer(self):
        pacing.reset()
        before = pacing.current()
        data = pacing.snapshot({"enabled": True})
        self.assertIs(pacing.current(), before)
        self.assertTrue(data["config"]["enabled"])
        self.assertIn("answer", data["gaps"])
        self.assertEqual(data["labels"]["gaps"]["group"], "这组刷完了歇一会")

    def test_snapshot_honours_the_environment_kill_switch(self):
        os.environ["CHAOXING_PACING"] = "0"
        self.addCleanup(os.environ.pop, "CHAOXING_PACING", None)
        self.assertFalse(pacing.snapshot({"enabled": True})["config"]["enabled"])


class LongBreakLogoutTest(unittest.TestCase):
    """大休息要真的退出登录，短停顿不能。"""

    def tearDown(self):
        pacing.reset()

    def _pacer(self, calls, **kwargs):
        options = {
            "enabled": True,
            "seed": 7,
            "windows": (),
            "sleep": lambda seconds: None,
            "suspend": lambda: calls.append("out"),
            "resume": lambda: calls.append("in"),
        }
        options.update(kwargs)
        return pacing.Pacer(**options)

    def test_group_break_logs_out_and_back_in(self):
        calls = []
        pacer = self._pacer(calls, gaps={"group": (600.0, 600.0)})
        result = pacer.between_groups()
        self.assertEqual(result.seconds, 600.0)
        self.assertEqual(calls, ["out", "in"])

    def test_short_gaps_never_log_out(self):
        for method in ("after_video", "between_answers", "between_jobs", "between_chapters",
                       "between_courses", "start_jitter"):
            with self.subTest(method=method):
                calls = []
                pacer = self._pacer(calls)
                getattr(pacer, method)()
                self.assertEqual(calls, [])

    def test_rest_windows_log_out(self):
        calls = []
        now = local_epoch(2026, 10, 22, 4, 10, utc=True)  # 北京 12:10
        pacer = self._pacer(
            calls,
            windows=(("lunch", (11, 40), (13, 0), 1.0, 3600.0),),
            now=lambda: now, tz="+08:00",
        )
        result = pacer.rest_if_needed()
        self.assertTrue(result)
        self.assertEqual(calls, ["out", "in"])

    def test_short_group_gap_stays_below_the_threshold(self):
        calls = []
        pacer = self._pacer(calls, gaps={"group": (60.0, 60.0)})
        pacer.between_groups()
        self.assertEqual(calls, [])

    def test_threshold_and_kinds_are_configurable(self):
        calls = []
        pacer = self._pacer(calls, gaps={"group": (600.0, 600.0)}, exit_after=0,
                            exit_kinds=("course",))
        pacer.between_groups()
        self.assertEqual(calls, [])
        pacer.between_courses()
        self.assertEqual(calls, ["out", "in"])

    def test_logout_can_be_switched_off(self):
        calls = []
        pacer = self._pacer(calls, gaps={"group": (600.0, 600.0)}, exit_on_long_pause=False)
        pacer.between_groups()
        self.assertEqual(calls, [])

    def test_broken_logout_hook_does_not_break_pacing(self):
        def boom():
            raise RuntimeError("network down")

        events = []
        pacer = pacing.Pacer(
            enabled=True, seed=7, gaps={"group": (600.0, 600.0)},
            sleep=lambda seconds: None, suspend=boom, resume=lambda: None,
            reporter=lambda event, **data: events.append(data.get("name")),
        )
        result = pacer.between_groups()
        self.assertEqual(result.seconds, 600.0)
        self.assertTrue(any("退出登录失败" in (name or "") for name in events))

    def test_bind_session_attaches_hooks_later(self):
        calls = []
        pacer = pacing.Pacer(enabled=True, seed=7, gaps={"group": (600.0, 600.0)},
                             sleep=lambda seconds: None)
        pacer.between_groups()
        self.assertEqual(calls, [])
        pacer.bind_session(suspend=lambda: calls.append("out"), resume=lambda: calls.append("in"))
        self.assertTrue(pacer.describe()["session_hooks"])
        pacer.between_groups()
        self.assertEqual(calls, ["out", "in"])

    def test_chaoxing_binds_its_own_logout_and_login(self):
        from api.base import Chaoxing

        calls = []
        account = Chaoxing()
        account.logout = lambda: calls.append("out")
        account.resume_session = lambda: calls.append("in") or True
        pacer = pacing.configure({"enabled": True}, sleep=lambda seconds: None,
                                 gaps={"group": (900.0, 900.0)}, windows=())
        account.bind_pacing_session()
        pacer.between_groups()
        self.assertEqual(calls, ["out", "in"])

    def test_failed_relogin_is_reported_without_raising(self):
        from api.base import Chaoxing

        events = []
        account = Chaoxing()
        account.logout = lambda: None
        account.resume_session = lambda: False
        pacer = pacing.configure({"enabled": True}, sleep=lambda seconds: None,
                                 gaps={"group": (900.0, 900.0)}, windows=(),
                                 reporter=lambda event, **data: events.append(data.get("name")))
        account.bind_pacing_session()
        pacer.between_groups()
        self.assertTrue(any("重新登录失败" in (name or "") for name in events))

    def test_resume_session_requires_a_usable_session(self):
        from unittest.mock import patch

        from api.base import Chaoxing

        account = Chaoxing()
        attempts = []

        def fake_login(*args, **kwargs):
            attempts.append("login")
            return {"status": True}

        account.login = fake_login
        account._validate_cookie_session = lambda: len(attempts) >= 2
        with patch("api.base.time.sleep"):
            self.assertTrue(account.resume_session(attempts=3))
        self.assertEqual(len(attempts), 2)

        account2 = Chaoxing()
        account2.login = fake_login
        account2._validate_cookie_session = lambda: False
        with patch("api.base.time.sleep"):
            self.assertFalse(account2.resume_session(attempts=2))


class PacingWindowEnvTest(unittest.TestCase):
    """夜间休息可以单独关掉：关掉后夜里照常刷，饭点仍然休息。"""

    def setUp(self):
        # 这里只测休息时段开关，先把所有 CHAOXING_PACING* 清干净，避免受别的用例影响
        self.saved = {key: value for key, value in os.environ.items()
                      if key.startswith("CHAOXING_PACING")}
        for key in list(self.saved):
            os.environ.pop(key, None)

    def tearDown(self):
        for key in [key for key in os.environ if key.startswith("CHAOXING_PACING")]:
            os.environ.pop(key, None)
        os.environ.update(self.saved)

    def window(self, pacer, name):
        return next(item for item in pacer.windows if item[0] == name)

    def test_night_is_on_by_default(self):
        pacer = pacing._build({"enabled": True})
        self.assertEqual(self.window(pacer, "night")[3], 1.0)

    def test_allowlist_keeps_only_the_named_windows(self):
        os.environ["CHAOXING_PACING_WINDOWS"] = "lunch,dinner"
        pacer = pacing._build({"enabled": True})
        self.assertEqual([item[0] for item in pacer.windows], ["lunch", "dinner", "night"])
        self.assertEqual(self.window(pacer, "night")[3], 0.0)
        self.assertEqual(self.window(pacer, "lunch")[3], 0.7)
        self.assertEqual(self.window(pacer, "dinner")[3], 0.7)

    def test_single_window_switch_turns_night_off(self):
        os.environ["CHAOXING_PACING_WINDOW_NIGHT"] = "0"
        pacer = pacing._build({"enabled": True})
        self.assertEqual(self.window(pacer, "night")[3], 0.0)
        self.assertEqual(self.window(pacer, "lunch")[3], 0.7)

    def test_a_single_switch_overrides_the_allowlist(self):
        os.environ["CHAOXING_PACING_WINDOWS"] = "lunch,dinner"
        os.environ["CHAOXING_PACING_WINDOW_LUNCH"] = "0"
        pacer = pacing._build({"enabled": True})
        self.assertEqual(self.window(pacer, "lunch")[3], 0.0)
        self.assertEqual(self.window(pacer, "dinner")[3], 0.7)

    def test_disabled_night_never_pauses_but_lunch_still_does(self):
        os.environ["CHAOXING_PACING_WINDOWS"] = "lunch,dinner"
        # 概率固定为 1.0，断言才不受默认 70% 抽签影响
        windows = (("lunch", (11, 40), (13, 0), 1.0, 3600.0),
                   ("night", (23, 30), (7, 0), 1.0, 5400.0))
        night = local_epoch(2026, 10, 23, 2, 0)
        pacer = pacing._build({"enabled": True, "now": lambda: night, "sleep": lambda s: None,
                               "windows": windows})
        self.assertFalse(pacer.rest_if_needed(), "关掉之后夜里不应再整段停")

        lunch = local_epoch(2026, 10, 23, 12, 10)
        pacer = pacing._build({"enabled": True, "now": lambda: lunch, "sleep": lambda s: None,
                               "windows": windows})
        pause = pacer.rest_if_needed()
        self.assertTrue(pause)
        self.assertIn("午饭", pause.reason)

    def test_snapshot_reports_the_night_window_as_disabled(self):
        os.environ["CHAOXING_PACING_WINDOWS"] = "lunch,dinner"
        night = local_epoch(2026, 10, 23, 2, 0)
        data = pacing.snapshot({"enabled": True}, now=night)
        night_window = next(item for item in data["windows"] if item["name"] == "night")
        self.assertEqual(night_window["chance"], 0.0)
        self.assertTrue(night_window["active"], "夜里仍然是夜间时段的显示范围，只是不再休息")
        self.assertEqual([item["name"] for item in data["windows"]], ["lunch", "dinner", "night"])

    def test_none_disables_every_window(self):
        os.environ["CHAOXING_PACING_WINDOWS"] = "none"
        pacer = pacing._build({"enabled": True})
        self.assertTrue(all(item[3] == 0.0 for item in pacer.windows))


if __name__ == "__main__":
    unittest.main()
