# -*- coding: utf-8 -*-
"""视频进度看护：倍速上限、夜间静默、上报闸门、平台进度回退检测。"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from api import video_guard


class GuardSettingsTests(unittest.TestCase):
    def setUp(self):
        video_guard.reset()

    def tearDown(self):
        video_guard.reset()

    def test_defaults_are_conservative(self):
        settings = video_guard.settings()
        self.assertEqual(settings["max_speed"], 0.95, "默认 0.95 倍速慢放")
        self.assertTrue(settings["quiet_hours"])
        self.assertFalse(settings["auto_pause"])
        self.assertEqual(settings["recheck"], 8)

    def test_configure_clamps_speed_and_flags(self):
        settings = video_guard.configure({
            "max_speed": 4, "quiet_hours": 0, "auto_pause": "yes", "recheck": 999,
        })
        self.assertEqual(settings["max_speed"], video_guard.MAX_SPEED)
        self.assertFalse(settings["quiet_hours"])
        self.assertTrue(settings["auto_pause"])
        self.assertEqual(settings["recheck"], 50)

    def test_configure_rejects_nonsense_instead_of_crashing(self):
        settings = video_guard.configure({"max_speed": "fast", "recheck": None})
        self.assertEqual(settings["max_speed"], video_guard.DEFAULT_SPEED)
        self.assertEqual(settings["recheck"], 8)

    def test_configure_only_touches_the_keys_it_is_given(self):
        """第二次只改 recheck 时，不能把 quiet_hours/auto_pause 悄悄恢复成默认。"""
        video_guard.configure({"quiet_hours": False, "auto_pause": True, "max_speed": 1.2})
        settings = video_guard.configure({"recheck": 3})
        self.assertEqual(settings["recheck"], 3)
        self.assertFalse(settings["quiet_hours"])
        self.assertTrue(settings["auto_pause"])
        self.assertEqual(settings["max_speed"], 1.2)

    def test_reset_restores_defaults(self):
        video_guard.configure({"max_speed": 1.4, "quiet_hours": False})
        video_guard.reset()
        self.assertEqual(video_guard.settings(), dict(video_guard._DEFAULTS))


class SpeedClampTests(unittest.TestCase):
    def setUp(self):
        video_guard.reset()

    def tearDown(self):
        video_guard.reset()

    def test_speed_is_capped_at_the_configured_limit(self):
        video_guard.configure({"max_speed": 1.2})
        self.assertEqual(video_guard.clamp_speed(3), 1.2)
        self.assertEqual(video_guard.clamp_speed(1), 1.0)
        self.assertEqual(video_guard.clamp_speed(0), 1.0)
        self.assertEqual(video_guard.clamp_speed("abc"), 1.0)

    def test_slow_playback_below_one_survives_the_clamp(self):
        """配置成 0.95 慢放时，请求 1 倍要真的被压到 0.95。"""
        video_guard.configure({"max_speed": 0.95})
        self.assertEqual(video_guard.clamp_speed(1), 0.95)
        self.assertEqual(video_guard.clamp_speed(1.5), 0.95)
        self.assertEqual(video_guard.clamp_speed(0.8), 0.8)

    def test_the_floor_is_enforced(self):
        self.assertEqual(video_guard.clamp_speed(0.01), video_guard.FLOOR_SPEED)


class QuietWindowTests(unittest.TestCase):
    def setUp(self):
        video_guard.reset()

    def tearDown(self):
        video_guard.reset()

    def test_the_small_hours_are_quiet(self):
        for hour in (0, 3, 5):
            self.assertEqual(
                video_guard.quiet_until(datetime(2026, 10, 22, hour, 30)),
                datetime(2026, 10, 22, 6, 0),
                f"{hour} 点应处于静默窗口",
            )

    def test_outside_the_window_is_not_quiet(self):
        for hour in (6, 9, 14, 23):
            self.assertIsNone(video_guard.quiet_until(datetime(2026, 10, 22, hour, 0)))

    def test_turning_the_switch_off_ends_the_window(self):
        video_guard.configure({"quiet_hours": False})
        self.assertIsNone(video_guard.quiet_until(datetime(2026, 10, 22, 2, 0)))

    def test_wait_for_window_sleeps_in_slices_until_the_end(self):
        slept = []
        clock = [datetime(2026, 10, 22, 5, 0)]

        def fake_sleep(seconds):
            slept.append(round(seconds, 1))
            clock[0] += timedelta(seconds=seconds)

        waited = video_guard.wait_for_window(sleep=fake_sleep, now=lambda: clock[0])
        self.assertAlmostEqual(waited, 3600, delta=5)
        self.assertEqual(clock[0].hour, 6)
        self.assertTrue(all(seconds <= 300 for seconds in slept))

    def test_wait_for_window_returns_zero_outside_the_window(self):
        slept = []
        waited = video_guard.wait_for_window(
            sleep=slept.append, now=lambda: datetime(2026, 10, 22, 14, 0))
        self.assertEqual(waited, 0.0)
        self.assertEqual(slept, [])

    def test_wait_for_window_honours_the_cap(self):
        slept = []
        waited = video_guard.wait_for_window(
            sleep=slept.append, now=lambda: datetime(2026, 10, 22, 2, 0), cap=600)
        self.assertAlmostEqual(waited, 600, delta=1)

    def test_progress_is_announced_while_waiting(self):
        messages = []
        clock = [datetime(2026, 10, 22, 4, 0)]

        def fake_sleep(seconds):
            clock[0] += timedelta(seconds=seconds)

        video_guard.wait_for_window(
            sleep=fake_sleep, now=lambda: clock[0], on_wait=messages.append)
        self.assertTrue(messages, "长等待期间应该汇报进度")
        self.assertIn("夜间静默", messages[0])

    def test_wait_for_window_uses_the_local_clock_by_default(self):
        """默认必须跟着 CHAOXING_PACING_TZ 走，不能再用服务器本地的 UTC。"""
        beijing = timezone(timedelta(hours=8))
        clock = [datetime(2026, 10, 26, 3, 0, tzinfo=beijing)]

        def fake_sleep(seconds):
            clock[0] += timedelta(seconds=seconds)

        with patch.object(video_guard, "local_now", side_effect=lambda: clock[0]):
            waited = video_guard.wait_for_window(sleep=fake_sleep)
        self.assertAlmostEqual(waited, 3 * 3600, delta=5)
        self.assertEqual(clock[0].hour, 6, "应该在北京时间 06:00 结束，而不是 14:00")


class QuietTimezoneTests(unittest.TestCase):
    """服务器跑在 UTC：夜间静默必须按人所在时区算，否则 08:00-14:00 会被当成深夜。"""

    def setUp(self):
        video_guard.reset()

    def tearDown(self):
        video_guard.reset()

    def test_the_zone_comes_from_the_pacing_timezone(self):
        with patch.dict(os.environ, {"CHAOXING_PACING_TZ": "Asia/Shanghai"}):
            moment = video_guard.local_now()
        self.assertEqual(moment.utcoffset(), timedelta(hours=8))

    def test_without_a_configured_zone_it_falls_back_to_beijing(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CHAOXING_PACING_TZ")}
        env.pop("CHAOXING_TZ", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(video_guard.local_now().utcoffset(), timedelta(hours=8))

    def test_two_in_the_morning_utc_is_not_the_small_hours_in_beijing(self):
        """这条就是线上故障的复现：UTC 02:00 = 北京 10:00，绝不能再静默。"""
        with patch.object(video_guard, "local_now",
                          return_value=datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc)):
            self.assertIsNone(video_guard.quiet_until())

    def test_beijing_two_in_the_morning_is_still_quiet(self):
        beijing = timezone(timedelta(hours=8))
        with patch.object(video_guard, "local_now",
                          return_value=datetime(2026, 10, 26, 2, 0, tzinfo=beijing)):
            end = video_guard.quiet_until()
        self.assertIsNotNone(end)
        self.assertEqual((end.hour, end.minute), (6, 0))
        self.assertEqual(end.utcoffset(), timedelta(hours=8))


class RollbackTests(unittest.TestCase):
    def setUp(self):
        video_guard.reset()

    def tearDown(self):
        video_guard.reset()

    def test_a_drop_beyond_tolerance_is_a_rollback(self):
        video_guard.note("course:video", 120)
        candidate = video_guard.inspect("course:video", 60)
        self.assertTrue(candidate["_rollback_candidate"])
        self.assertEqual(video_guard.rollback_count("course:video"), 0,
                         "候选读数不能提前增加回退次数")
        self.assertEqual(video_guard.high_water("course:video"), 120,
                         "候选读数不能提前覆盖本地水位")
        finding = video_guard.confirm("course:video", candidate, 60)
        self.assertEqual(finding, {"high": 120, "server": 60, "dropped": 60, "count": 1})

    def test_a_transient_low_reading_is_discarded_when_confirmation_recovers(self):
        video_guard.note("course:video", 120)
        candidate = video_guard.inspect("course:video", 0)
        self.assertIsNotNone(candidate)
        self.assertIsNone(video_guard.confirm("course:video", candidate, 120))
        self.assertEqual(video_guard.rollback_count("course:video"), 0)
        self.assertEqual(video_guard.high_water("course:video"), 120)

    def test_an_invalid_confirmation_discards_the_candidate_without_mutation(self):
        video_guard.note("course:video", 120)
        candidate = video_guard.inspect("course:video", 0)
        self.assertIsNone(video_guard.confirm("course:video", candidate, None))
        self.assertEqual(video_guard.rollback_count("course:video"), 0)
        self.assertEqual(video_guard.high_water("course:video"), 120)

    def test_small_jitter_is_not_a_rollback(self):
        video_guard.note("course:video", 120)
        self.assertIsNone(video_guard.inspect("course:video", 120 - video_guard.TOLERANCE))

    def test_alignment_is_not_reported_twice(self):
        video_guard.note("course:video", 300)
        candidate = video_guard.inspect("course:video", 120)
        self.assertIsNotNone(video_guard.confirm("course:video", candidate, 120))
        self.assertIsNone(video_guard.inspect("course:video", 120),
                          "对齐之后不应该反复报同一次回退")

    def test_repeated_rollbacks_are_counted(self):
        for _ in range(video_guard.MAX_ROLLBACKS + 1):
            video_guard.note("course:video", 600)
            video_guard.inspect("course:video", 600)
            candidate = video_guard.inspect("course:video", 100)
            video_guard.confirm("course:video", candidate, 100)
        self.assertEqual(video_guard.rollback_count("course:video"),
                         video_guard.MAX_ROLLBACKS + 1)

    def test_high_water_only_moves_forward(self):
        video_guard.note("course:video", 300)
        video_guard.note("course:video", 120)
        self.assertEqual(video_guard.high_water("course:video"), 300)

    def test_a_missing_or_broken_reading_is_ignored(self):
        video_guard.note("course:video", 300)
        for value in (None, "abc"):
            self.assertIsNone(video_guard.inspect("course:video", value))
        self.assertEqual(video_guard.rollback_count("course:video"), 0)
        self.assertIsNone(video_guard.note("course:video", "abc"))
        self.assertEqual(video_guard.high_water("course:video"), 300)

    def test_one_report_cycle_of_lag_is_not_a_rollback(self):
        # 线上实测：404 -> 346，正好差一个上报周期（60s × 0.95 = 57s）。
        # 卡片里是上一轮上报落库的位置，刚报完就回读必然差这么多。
        video_guard.note("course:video", 404)
        self.assertIsNone(video_guard.inspect("course:video", 346, lag=57),
                          "自己的上报节奏不能算成平台回退")
        self.assertEqual(video_guard.rollback_count("course:video"), 0)

    def test_a_real_clear_is_still_a_rollback_with_lag_allowed(self):
        video_guard.note("course:video", 427)
        candidate = video_guard.inspect("course:video", 61, lag=57)
        finding = video_guard.confirm("course:video", candidate, 61)
        self.assertEqual(finding["dropped"], 366)
        self.assertEqual(finding["count"], 1)

    def test_lag_does_not_hide_a_drop_bigger_than_one_cycle(self):
        video_guard.note("course:video", 400)
        position = 400 - 58 - 57 - 1
        candidate = video_guard.inspect("course:video", position, lag=57)
        finding = video_guard.confirm("course:video", candidate, position)
        self.assertIsNotNone(finding)
        self.assertEqual(finding["server"], position)

    def test_lag_defaults_to_the_plain_tolerance(self):
        video_guard.note("course:video", 404)
        candidate = video_guard.inspect("course:video", 346)
        self.assertIsNotNone(candidate, "不传 lag 时保持原来的严格判定")
        self.assertIsNotNone(video_guard.confirm("course:video", candidate, 346))

    def test_a_reading_that_keeps_climbing_is_never_a_rollback(self):
        """线上实测：卡片 346 卡了 5 分钟，一次跳到 636，再 60 秒到 694。

        平台读数一直在往前走，只是落库慢。只要没倒退，就不是回退。
        """
        video_guard.note("course:video", 349)
        self.assertIsNone(video_guard.inspect("course:video", 346))
        video_guard.note("course:video", 646)
        self.assertIsNone(video_guard.inspect("course:video", 346),
                          "落后但不倒退 = 平台还没存到，不是回退")
        video_guard.note("course:video", 700)
        self.assertIsNone(video_guard.inspect("course:video", 636))
        video_guard.note("course:video", 760)
        self.assertIsNone(video_guard.inspect("course:video", 694))
        self.assertEqual(video_guard.rollback_count("course:video"), 0)

    def test_a_reading_that_actually_goes_backwards_is_a_rollback(self):
        video_guard.note("course:video", 427)
        self.assertIsNone(video_guard.inspect("course:video", 427))
        candidate = video_guard.inspect("course:video", 61)
        finding = video_guard.confirm("course:video", candidate, 61)
        self.assertIsNotNone(finding, "平台读数从 427 掉到 61，这是真回退")
        self.assertEqual(finding["dropped"], 366)
        self.assertEqual(finding["count"], 1)

    def test_forget_clears_every_trace_of_one_video(self):
        video_guard.note("course:video", 300)
        candidate = video_guard.inspect("course:video", 100)
        video_guard.confirm("course:video", candidate, 100)
        video_guard.forget("course:video")
        self.assertIsNone(video_guard.high_water("course:video"))
        self.assertEqual(video_guard.rollback_count("course:video"), 0)

    def test_pause_seconds_is_between_fifteen_and_thirty_minutes(self):
        for _ in range(20):
            seconds = video_guard.pause_seconds()
            self.assertGreaterEqual(seconds, 15 * 60)
            self.assertLessEqual(seconds, 30 * 60)


class ReporterTests(unittest.TestCase):
    def setUp(self):
        video_guard.reset()

    def tearDown(self):
        video_guard.reset()

    def test_notify_forwards_to_the_configured_reporter(self):
        seen = []
        video_guard.configure(reporter=lambda event, fields: seen.append((event, fields)))
        video_guard.notify("video_rollback", rollback_dropped=30)
        self.assertEqual(seen, [("video_rollback", {"rollback_dropped": 30})])

    def test_a_broken_reporter_never_breaks_playback(self):
        def boom(event, fields):
            raise RuntimeError("上报炸了")

        video_guard.configure(reporter=boom)
        video_guard.notify("video_rollback", rollback_dropped=1)  # 不应抛出

    def test_notify_without_a_reporter_is_a_no_op(self):
        video_guard.notify("video_rollback", rollback_dropped=1)


class ReportGateTests(unittest.TestCase):
    def setUp(self):
        video_guard.reset()

    def tearDown(self):
        video_guard.reset()

    def test_the_same_key_gets_the_same_gate(self):
        self.assertIs(video_guard.report_gate("course:video"),
                      video_guard.report_gate("course:video"))
        self.assertIsNot(video_guard.report_gate("course:video"),
                         video_guard.report_gate("course:other"))

    def test_the_gate_waits_out_the_minimum_gap(self):
        gate = video_guard.report_gate("course:video", min_gap=5.0)
        with patch("api.video_guard.time.sleep") as sleep, \
                patch("api.video_guard.time.monotonic", side_effect=[0.0, 0.0, 2.0, 2.0]):
            with gate:
                pass
            with gate:
                pass
        sleep.assert_called()

    def test_a_zero_gap_never_sleeps(self):
        gate = video_guard.report_gate("course:video", min_gap=0.0)
        with patch("api.video_guard.time.sleep") as sleep:
            with gate:
                pass
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
