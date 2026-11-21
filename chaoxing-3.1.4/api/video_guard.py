# -*- coding: utf-8 -*-
"""视频进度看护。

平台在服务端拿两次上报的 playingTime 增量除以真实时间得到实际倍速，再结合 isdrag
记录判断这次播放是否异常；进度回退、清空都发生在服务端，客户端能做的只有两件事：
不做异常动作，以及一发现回退立刻以服务端为准重新对齐并降速。

这个模块把这三件事集中起来：
  * clamp_speed：倍速硬上限，控制台填多大都不会真的超速播放；
  * wait_for_window：夜间静默窗口，00:00-06:00 不新开视频（已开始的照常播完）；
  * report_gate：同一 (账号, 课程) 的上报串行且有最小间隔，避免同一秒内多次上报；
  * note / inspect：本地最高水位与平台读数的比对，发现回退时给出可执行的动作。
"""

import os
import random
import threading
import time
from datetime import datetime, timedelta

MAX_SPEED = 1.5          # 硬上限；社区一致结论是超过 2 倍速最容易被清空
DEFAULT_SPEED = 0.95     # 默认比原速慢一点：平台压一次进度就要重看一分钟，不值当
FLOOR_SPEED = 0.25
TOLERANCE = 8            # 回读进度允许的误差（秒），避免把正常抖动当成回退
MAX_ROLLBACKS = 3        # 同一视频最多容忍几次回退，再多就交回人工
QUIET_START = (0, 0)     # 夜间静默开始（按人所在时区）
QUIET_END = (6, 0)       # 夜间静默结束
DEFAULT_TIMEZONE = "Asia/Shanghai"   # 没配 CHAOXING_PACING_TZ 时也不能按服务器 UTC 算

_DEFAULTS = {"max_speed": DEFAULT_SPEED, "quiet_hours": True, "auto_pause": False, "recheck": 8}

_lock = threading.RLock()
_state = dict(_DEFAULTS)
_reporter = None
_zone_cache = {"key": None, "zone": None}
_high_water = {}
_rollback_counts = {}
_server_seen = {}
_gates = {}


def configure(settings=None, *, reporter=None):
    """装载后端下发的看护配置；重复调用会覆盖上一次的值（每次起 worker 都会调）。

    只更新传进来的键：没传的键保持当前值，避免第二次只改 recheck 时把
    quiet_hours 之类的开关悄悄恢复成默认。
    """
    global _reporter
    with _lock:
        if reporter is not None:
            _reporter = reporter
        if settings:
            if settings.get("max_speed") is not None:
                _state["max_speed"] = _number(settings.get("max_speed"), _DEFAULTS["max_speed"],
                                              low=FLOOR_SPEED, high=MAX_SPEED)
            if settings.get("quiet_hours") is not None:
                _state["quiet_hours"] = _flag(settings.get("quiet_hours"), _DEFAULTS["quiet_hours"])
            if settings.get("auto_pause") is not None:
                _state["auto_pause"] = _flag(settings.get("auto_pause"), _DEFAULTS["auto_pause"])
            if settings.get("recheck") is not None:
                _state["recheck"] = int(_number(settings.get("recheck"), _DEFAULTS["recheck"],
                                                low=1, high=50))
        return dict(_state)


def settings():
    with _lock:
        return dict(_state)


def reset():
    """测试用：恢复默认配置并清空水位。"""
    global _reporter
    with _lock:
        _state.clear()
        _state.update(_DEFAULTS)
        _reporter = None
        _high_water.clear()
        _rollback_counts.clear()
        _server_seen.clear()
        _gates.clear()


def notify(event, **fields):
    with _lock:
        reporter = _reporter
    if reporter is None:
        return
    try:
        reporter(event, fields)
    except Exception:  # noqa: BLE001 - 上报不能影响播放
        pass


def _number(value, default, *, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return round(min(high, max(low, number)), 2)


def _flag(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def clamp_speed(value, *, default=1.0):
    """把请求的倍速夹到 [FLOOR_SPEED, 当前上限]，非法值退回默认值。"""
    with _lock:
        limit = _state["max_speed"]
    try:
        speed = float(value)
    except (TypeError, ValueError):
        speed = float(default)
    if speed != speed or speed <= 0:
        speed = float(default)
    return round(min(limit, max(FLOOR_SPEED, speed)), 2)


def timezone_name():
    """夜间静默按哪个时区算：跟拟人节奏共用 CHAOXING_PACING_TZ。"""
    return (os.environ.get("CHAOXING_PACING_TZ")
            or os.environ.get("CHAOXING_TZ")
            or DEFAULT_TIMEZONE)


def _zone():
    """解析并缓存时区；服务器常跑在 UTC，直接 datetime.now() 会把 00:00 算错。"""
    key = timezone_name()
    with _lock:
        if _zone_cache["key"] == key:
            return _zone_cache["zone"]
    zone = None
    if key:
        try:
            from api.pacing import resolve_timezone

            zone = resolve_timezone(key)
        except Exception:  # noqa: BLE001 - 时区解析失败就退回本机时间
            zone = None
    with _lock:
        _zone_cache["key"] = key
        _zone_cache["zone"] = zone
    return zone


def local_now():
    """当前的"人所在时区"时间。"""
    zone = _zone()
    return datetime.now(zone) if zone else datetime.now()


def quiet_until(now=None):
    """处于夜间静默窗口时返回窗口结束时刻，否则返回 None。"""
    with _lock:
        enabled = _state["quiet_hours"]
    if not enabled:
        return None
    current = now or local_now()
    zone = _zone()
    if zone is not None and current.tzinfo is not None:
        # 不管调用方给的是 UTC 还是别的时区，一律换算成"人所在时区"再看几点
        current = current.astimezone(zone)
    begin = QUIET_START[0] * 60 + QUIET_START[1]
    finish = QUIET_END[0] * 60 + QUIET_END[1]
    minute = current.hour * 60 + current.minute
    if begin <= finish:
        inside = begin <= minute < finish
    else:  # 跨零点
        inside = minute >= begin or minute < finish
    if not inside:
        return None
    end = current.replace(hour=QUIET_END[0], minute=QUIET_END[1], second=0, microsecond=0)
    if end <= current:
        end += timedelta(days=1)
    return end


def wait_for_window(*, sleep=None, now=None, on_wait=None, cap=6 * 3600):
    """夜间静默期内按分钟分片等待，返回实际等待的秒数（0 表示不在窗口内）。

    sleep 在调用时才解析，这样测试或上层替换 time.sleep 后能立刻生效
    （默认参数会在 import 时绑死，替换不掉，真的会睡满整个窗口）。

    now 不传时用 local_now()（按 CHAOXING_PACING_TZ），不能用 datetime.now()：
    服务器跑在 UTC 时，00:00-06:00 会被算成北京时间的 08:00-14:00。
    """
    sleep = sleep or time.sleep
    if now is None:
        clock = local_now
    elif callable(now):
        clock = now
    else:
        fixed = now
        clock = lambda: fixed  # noqa: E731 - 固定时刻，便于测试
    end = quiet_until(clock())
    if end is None:
        return 0.0
    waited = 0.0
    announced = 0.0
    while True:
        remaining = (end - clock()).total_seconds()
        if remaining <= 0 or waited >= cap:
            return waited
        slice_seconds = min(300.0, remaining, cap - waited)
        if on_wait is not None and waited - announced >= 900:
            announced = waited
            on_wait(f"夜间静默时段（{QUIET_END[0]:02d}:{QUIET_END[1]:02d} 后继续），"
                    f"本轮等待 {int(waited)} 秒，剩余约 {int(remaining)} 秒")
        sleep(slice_seconds)
        waited += slice_seconds


class _Gate:
    """同一 (账号, 课程) 的上报串行执行，并保证两次之间有最小间隔。"""

    def __init__(self, min_gap):
        self.lock = threading.Lock()
        self.min_gap = min_gap
        self.last = 0.0

    def __enter__(self):
        self.lock.acquire()
        gap = self.min_gap - (time.monotonic() - self.last)
        if gap > 0:
            time.sleep(gap)
        return self

    def __exit__(self, *exc_info):
        self.last = time.monotonic()
        self.lock.release()
        return False


def report_gate(key, *, min_gap=1.0):
    with _lock:
        gate = _gates.get(key)
        if gate is None:
            gate = _gates[key] = _Gate(min_gap)
        return gate


def note(key, position):
    """记录本地已上报的最高位置（只看不回退的值）。"""
    try:
        value = int(position)
    except (TypeError, ValueError):
        return None
    with _lock:
        high = _high_water.get(key)
        if high is None or value > high:
            _high_water[key] = high = value
        return high


def high_water(key):
    with _lock:
        return _high_water.get(key)


def rollback_count(key):
    with _lock:
        return _rollback_counts.get(key, 0)


def forget(key):
    with _lock:
        _high_water.pop(key, None)
        _rollback_counts.pop(key, None)
        _server_seen.pop(key, None)
        _gates.pop(key, None)


def _position(value):
    """解析有效的平台秒数；空值和非法值不能伪装成 0。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or not value.isdecimal():
            return None
    try:
        position = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return position if position >= 0 else None


def _dropped_for_rollback(high, previous, server, tolerance, allowance):
    """返回一次读数满足回退条件时的下降量，否则返回 None。"""
    if previous is None:
        dropped = high - server
        if high <= 0 or dropped <= allowance:
            return None
        return dropped

    if server >= previous - tolerance:
        return None
    dropped = high - server
    if high <= 0 or dropped <= tolerance:
        return None
    return dropped


def _remember_non_rollback(key, server, high, previous, tolerance):
    """把非回退读数并入平台读数基线，保留原有的向前对齐行为。"""
    if previous is None:
        _server_seen[key] = server
        if high <= 0 or server < high:
            _high_water[key] = max(high, server)
    elif server >= previous - tolerance:
        _server_seen[key] = max(previous, server)
        if high <= 0 or server < high:
            _high_water[key] = max(high, server)
    else:
        dropped = high - server
        if high <= 0 or dropped <= tolerance:
            _server_seen[key] = server


def inspect(key, server_position, *, tolerance=TOLERANCE, lag=0):
    """检查平台读数；异常读数只返回待确认候选，不提交回退状态。

    判定分两层：

    1. **倒退**：平台这次读数比上一次读数还低，才算回退。这是"回退"的字面含义，
       也是唯一有意义的判据。
    2. **落后不算**：平台读数是上一次上报落库的值，不是实时的。只要它在往前走，
       哪怕比本地计数器落后几分钟，也只是"平台还没存到那儿"，不是回退。
       实测：本地 646s 时卡片还写着 346s，302 秒后一次跳到 636s —— 期间它一直在
       往前，只是没落库。把它当回退，等于每看几分钟就自己倒回去重看一遍。

    首次出现明显回退时返回候选，调用方必须在下一次进度上报前再独立读一次，
    并把新读数交给 confirm()。这样一次缓存抖动或乱序不会改变本地播放位置、
    回退计数或正式告警。
    """
    server = _position(server_position)
    if server is None:
        return None
    try:
        tolerance = max(0, int(tolerance))
        allowance = max(0, int(lag)) + tolerance
    except (TypeError, ValueError, OverflowError):
        return None
    with _lock:
        high = _high_water.get(key, 0)
        previous = _server_seen.get(key)
        dropped = _dropped_for_rollback(high, previous, server, tolerance, allowance)
        if dropped is None:
            _remember_non_rollback(key, server, high, previous, tolerance)
            return None
        return {
            "high": high,
            "server": server,
            "dropped": dropped,
            "_rollback_candidate": True,
            "_previous": previous,
            "_tolerance": tolerance,
            "_allowance": allowance,
        }


def confirm(key, candidate, server_position):
    """使用紧接着的独立平台读数确认回退候选；无效或恢复读数会丢弃候选。"""
    if not isinstance(candidate, dict) or not candidate.get("_rollback_candidate"):
        return None
    server = _position(server_position)
    if server is None:
        return None

    try:
        high = int(candidate["high"])
        first_server = int(candidate["server"])
        tolerance = max(0, int(candidate["_tolerance"]))
        allowance = max(0, int(candidate["_allowance"]))
        previous = candidate.get("_previous")
        if previous is not None:
            previous = int(previous)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    with _lock:
        current_previous = _server_seen.get(key)
        if current_previous != previous:
            # 另一个检查已推进基线，不能再用旧候选确认；保留新读数作为观察值。
            _remember_non_rollback(
                key, server, _high_water.get(key, 0), current_previous, tolerance,
            )
            return None

        dropped = _dropped_for_rollback(
            high, previous, server, tolerance, allowance,
        )
        # 第二次读数仍处在同一明显低位，或比候选更低，才确认；恢复到明显更高位置
        # 则视为一次性异常，不回退本地位置。
        if dropped is None or server > first_server + tolerance:
            _remember_non_rollback(
                key, server, _high_water.get(key, 0), previous, tolerance,
            )
            return None

        count = _rollback_counts.get(key, 0) + 1
        _rollback_counts[key] = count
        _high_water[key] = server
        _server_seen[key] = server
        return {"high": high, "server": server, "dropped": dropped, "count": count}


def pause_seconds(*, rng=random):
    """回退后暂停 15-30 分钟：模仿“发现问题先停一会儿”，避免继续撞判定。"""
    return round(rng.uniform(15 * 60, 30 * 60), 1)
