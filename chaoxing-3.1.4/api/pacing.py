"""拟人节奏：随机等待、随机分组、午休/晚休/夜间暂停。

刷课脚本最大的破绽是"太准时"：视频一结束立刻答题、章节之间零间隔、一天到晚
不间断。这个模块把所有"人一定会有的停顿"抽出来集中配置：

* 视频看完 → 开始答题（也是每题之间的间隔），默认 1–15 秒
* 同一章节里不同任务点之间（视频/文档/测验），默认 2–18 秒
* 章节之间，默认 8–45 秒，偶尔来一次 1–5 分钟的"走开"
* 课程之间，默认 2–10 分钟
* 一组课程（2–3 门）之后，默认 10–30 分钟的大休息
* 午饭（11:40–13:00）、晚饭（17:30–19:00）、夜里（23:30–07:00）按概率整段停

长休息时不只是干等：如果上层把退出登录/重新登录的钩子接上（backend/worker.py
会接），暂停前后会真的注销会话，平台侧看来就是人关了页面、过一阵再回来。

默认关闭（测试与本地命令行不受影响），`backend/worker.py` 会按任务选项打开它。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import random
import threading
import time

# 每种停顿的默认区间（秒）。键名同时用于环境变量覆盖：CHAOXING_PACING_<KEY>
DEFAULT_GAPS = {
    "answer": (1.0, 15.0),
    "job": (2.0, 18.0),
    "chapter": (8.0, 45.0),
    "micro": (60.0, 300.0),
    "course": (120.0, 600.0),
    "group": (600.0, 1800.0),
    "start": (10.0, 120.0),
}

# name, 起, 止, 命中概率, 最长暂停（秒）
DEFAULT_WINDOWS = (
    ("lunch", (11, 40), (13, 0), 0.7, 3600.0),
    ("dinner", (17, 30), (19, 0), 0.7, 3600.0),
    # 夜间是一整觉：23:30 睡到 07:00（7.5 小时），不是停一会儿再接着刷
    ("night", (23, 30), (7, 0), 1.0, 27000.0),
)

WINDOW_LABELS = {"lunch": "午饭", "dinner": "晚饭", "night": "夜间休息"}

GAP_LABELS = {
    "answer": "看题思考",
    "job": "切换任务点",
    "chapter": "翻到下一节",
    "micro": "离开一下",
    "course": "换一门课",
    "group": "这组刷完了歇一会",
    "start": "开始学习",
}

SLEEP_SLICE = 5.0

# 只有当停顿属于"大休息"且够长时，才退出登录（答题、章节这种短停顿原地等）
EXIT_KINDS = frozenset({"group", "window"})
EXIT_THRESHOLD = 300.0


@dataclass(frozen=True)
class Pause:
    """一次停顿：实际睡了多少秒、为什么、是否被截断、属于哪类停顿。"""

    seconds: float
    reason: str = ""
    capped: bool = False
    kind: str = ""

    def __bool__(self):
        return self.seconds > 0


def _env_flag(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_gaps():
    gaps = {}
    for key, (low, high) in DEFAULT_GAPS.items():
        raw = os.environ.get(f"CHAOXING_PACING_{key.upper()}")
        if not raw:
            continue
        try:
            parts = [float(item) for item in raw.replace("~", ",").replace("-", ",").split(",")]
        except ValueError:
            continue
        if len(parts) == 1:
            parts.append(parts[0])
        if len(parts) != 2 or parts[0] < 0 or parts[1] < parts[0]:
            continue
        gaps[key] = (parts[0], parts[1])
    return gaps


def _normalise_gaps(gaps):
    merged = dict(DEFAULT_GAPS)
    for key, value in (gaps or {}).items():
        if key not in merged or value is None:
            continue
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError, IndexError):
            continue
        if low < 0 or high < low:
            continue
        merged[key] = (low, high)
    return merged


def _env_windows(windows):
    """按环境变量关掉某些休息时段（被关掉的时段仍然展示，只是概率为 0）。

    * CHAOXING_PACING_WINDOWS=lunch,dinner  只保留列出的时段（写 none/off/0 全关）
    * CHAOXING_PACING_WINDOW_NIGHT=0        单独关掉夜间休息
    """
    items = list(windows if windows is not None else DEFAULT_WINDOWS)
    allow = os.environ.get("CHAOXING_PACING_WINDOWS")
    enabled = None
    if allow is not None and allow.strip():
        names = {part.strip().lower() for part in allow.replace(";", ",").split(",") if part.strip()}
        enabled = set() if names & {"none", "off", "0", "no"} else names
    merged = []
    for item in items:
        try:
            name, start, end, chance, cap = item
        except (TypeError, ValueError):
            merged.append(item)
            continue
        name = str(name)
        keep = enabled is None or name.lower() in enabled
        flag = os.environ.get(f"CHAOXING_PACING_WINDOW_{name.upper()}")
        if flag is not None and flag.strip():
            keep = flag.strip().lower() not in {"0", "false", "no", "off"}
        merged.append((name, start, end, chance if keep else 0.0, cap))
    return tuple(merged)


def _normalise_windows(windows):
    merged = []
    for item in windows if windows is not None else DEFAULT_WINDOWS:
        try:
            name, start, end, chance, cap = item
            start = (int(start[0]), int(start[1]))
            end = (int(end[0]), int(end[1]))
            chance = min(1.0, max(0.0, float(chance)))
            cap = max(0.0, float(cap))
        except (TypeError, ValueError, IndexError):
            continue
        if not 0 <= start[0] <= 23 or not 0 <= end[0] <= 23:
            continue
        merged.append((str(name), start, end, chance, cap))
    return tuple(merged)


def _minutes(value):
    return value[0] * 60 + value[1]


def _offset_zone(text):
    """把 UTC+8 / +08:00 / -5 / 0530 这类写法解析成固定时差。"""
    body = text.strip().upper().replace("UTC", "").replace("GMT", "").strip()
    if not body:
        return None
    sign = 1
    if body.startswith("-"):
        sign, body = -1, body[1:]
    elif body.startswith("+"):
        body = body[1:]
    body = body.replace(":", "")
    try:
        if len(body) <= 2:
            hours, minutes = int(body), 0
        elif len(body) == 3:
            hours, minutes = int(body[0]), int(body[1:])
        elif len(body) == 4:
            hours, minutes = int(body[:2]), int(body[2:])
        else:
            return None
    except ValueError:
        return None
    if hours > 23 or minutes > 59:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def resolve_timezone(name):
    """解析 IANA 名称或固定时差；无法解析时返回 None（用进程本地时间）。"""
    if not name:
        return None
    name = str(name).strip()
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        pass
    return _offset_zone(name)


def human_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {rest} 秒" if rest else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"


class Pacer:
    """线程安全的拟人节奏控制器；关闭时所有方法立即返回零停顿。"""

    def __init__(
        self,
        *,
        enabled=False,
        gaps=None,
        windows=None,
        group_size=(2, 3),
        micro_break_chance=0.05,
        seed=None,
        reporter=None,
        logger=None,
        sleep=None,
        now=None,
        rng=None,
        tz=None,
        exit_on_long_pause=True,
        exit_after=EXIT_THRESHOLD,
        exit_kinds=None,
        suspend=None,
        resume=None,
    ):
        self.enabled = bool(enabled)
        self.gaps = _normalise_gaps(gaps)
        self.windows = _normalise_windows(windows)
        self.tz_name = str(tz).strip() if tz else ""
        self.tz = resolve_timezone(self.tz_name)
        try:
            low, high = (int(group_size[0]), int(group_size[1]))
        except (TypeError, ValueError, IndexError):
            low, high = 2, 3
        self.group_size = (max(1, min(low, high)), max(1, max(low, high)))
        self.micro_break_chance = min(1.0, max(0.0, float(micro_break_chance)))
        self.seed = seed
        self._rng = rng or random.Random(seed)
        self._reporter = reporter
        self._logger = logger
        self._sleep = sleep or time.sleep
        self._now = now or time.time
        self._lock = threading.Lock()
        self._rested = set()
        self.exit_on_long_pause = bool(exit_on_long_pause)
        try:
            self.exit_after = max(0.0, float(exit_after))
        except (TypeError, ValueError):
            self.exit_after = EXIT_THRESHOLD
        kinds = EXIT_KINDS if exit_kinds is None else exit_kinds
        self.exit_kinds = frozenset(str(item) for item in kinds)
        self._suspend = suspend
        self._resume = resume

    # ---------------------------------------------------------------- 基础
    def bind_session(self, *, suspend=None, resume=None):
        """接上"退出登录/重新登录"钩子；传 None 表示断开。"""
        self._suspend = suspend
        self._resume = resume
        return self

    def _should_exit(self, pause):
        if not self.exit_on_long_pause or self._suspend is None or self._resume is None:
            return False
        if pause.kind not in self.exit_kinds:
            return False
        return pause.seconds >= self.exit_after

    def _say(self, text):
        if self._logger is not None:
            try:
                self._logger.info("拟人节奏：{}", text)
            except Exception:
                pass
        if self._reporter is None:
            return
        try:
            self._reporter("phase", phase="pacing", name=text)
        except Exception:
            pass

    def _hook(self, function, label):
        if function is None:
            return False
        try:
            function()
            return True
        except Exception:
            # 异常文本可能带地址或凭据，只报结论
            self._say(f"{label}失败，继续按当前会话跑")
            if self._logger is not None:
                try:
                    self._logger.warning("拟人节奏：{}失败", label)
                except Exception:
                    pass
            return False

    def _apply(self, pause):
        """通知 → 必要时先退出登录 → 等待 → 必要时重新登录。"""
        if not pause.seconds:
            return pause
        leaving = self._should_exit(pause)
        if leaving:
            self._say("这会儿不在，先退出登录")
            self._hook(self._suspend, "退出登录")
        self._notify(pause)
        self._sleep_seconds(pause.seconds)
        if leaving:
            self._say("人回来了，重新登录继续")
            self._hook(self._resume, "重新登录")
        return pause

    def describe(self):
        return {
            "enabled": self.enabled,
            "timezone": self.tz_name or "local",
            "exit_on_long_pause": self.exit_on_long_pause,
            "exit_after": self.exit_after,
            "exit_kinds": sorted(self.exit_kinds),
            "session_hooks": self._suspend is not None and self._resume is not None,
            "gaps": {key: list(value) for key, value in self.gaps.items()},
            "windows": [
                {"name": name, "start": start, "end": end, "chance": chance, "cap": cap}
                for name, start, end, chance, cap in self.windows
            ],
            "group_size": list(self.group_size),
            "micro_break_chance": self.micro_break_chance,
        }

    def _draw(self, key):
        low, high = self.gaps[key]
        if high <= low:
            return low
        return self._rng.uniform(low, high)

    def _notify(self, pause):
        if not pause.seconds:
            return
        if self._logger is not None:
            try:
                self._logger.info(
                    "拟人节奏：{}，等待 {:.0f} 秒", pause.reason or "停顿", pause.seconds
                )
            except Exception:  # 日志坏掉不能影响刷课
                pass
        if self._reporter is None:
            return
        label = pause.reason or "停顿"
        text = f"{label}，约 {human_duration(pause.seconds)}"
        if pause.capped:
            text += "（已按上限截断）"
        try:
            self._reporter(
                "phase", phase="pacing", name=text,
                pacing_kind=pause.kind or "",
                pacing_until=round(self._now() + pause.seconds, 1),
                pacing_minutes=round(pause.seconds / 60, 1),
            )
        except Exception:
            pass

    def _sleep_seconds(self, seconds):
        remaining = float(seconds)
        while remaining > 0:
            chunk = min(remaining, SLEEP_SLICE)
            self._sleep(chunk)
            remaining -= chunk

    def pause(self, key, *, reason="", scale=1.0, smallest=0.0):
        """按区间随机停一次；关闭时返回零停顿。"""
        if not self.enabled:
            return Pause(0.0, reason, False)
        seconds = self._draw(key) * float(scale)
        seconds = max(seconds, float(smallest or 0.0))
        if seconds <= 0:
            return Pause(0.0, reason, False)
        result = Pause(round(seconds, 2), reason or GAP_LABELS.get(key, key), False, key)
        return self._apply(result)

    # ---------------------------------------------------------------- 各场景
    def start_jitter(self):
        return self.pause("start", reason="开始前的准备")

    def after_video(self):
        return self.pause("answer", reason="视频看完，先缓一下再答题")

    def before_answer(self, *, minimum=0.0):
        return self.pause("answer", reason="思考这道题", smallest=minimum)

    def between_answers(self):
        return self.pause("answer", reason="下一题")

    def between_jobs(self):
        return self.pause("job", reason="切换任务点")

    def between_chapters(self):
        return self.pause("chapter", reason="翻到下一节")

    def maybe_micro_break(self):
        if not self.enabled or self.micro_break_chance <= 0:
            return Pause(0.0, "", False)
        if self._rng.random() >= self.micro_break_chance:
            return Pause(0.0, "", False)
        return self.pause("micro", reason="起来倒杯水")

    def between_courses(self):
        return self.pause("course", reason="换一门课")

    def between_groups(self):
        return self.pause("group", reason="这组刷完了，歇一会")

    # ---------------------------------------------------------------- 休息时段
    def window_pause(self, *, now=None):
        """命中午休/晚休/夜间就整段停下来，同一时段一天只停一次。"""
        if not self.enabled or not self.windows:
            return Pause(0.0, "", False)
        current = self._now() if now is None else float(now)
        seconds, reason, capped = self._window_wait(current)
        if seconds <= 0:
            return Pause(0.0, "", False)
        result = Pause(round(seconds, 2), reason, capped, "window")
        return self._apply(result)

    def _window_wait(self, current):
        # 服务器常跑在 UTC；饭点和深夜必须按人所在时区算
        clock = datetime.fromtimestamp(current, self.tz) if self.tz \
            else datetime.fromtimestamp(current)
        minute_of_day = clock.hour * 60 + clock.minute
        day = (clock.year, clock.timetuple().tm_yday)
        for name, start, end, chance, cap in self.windows:
            begin, finish = _minutes(start), _minutes(end)
            wraps = finish <= begin
            inside = (minute_of_day >= begin or minute_of_day < finish) if wraps \
                else begin <= minute_of_day < finish
            if not inside:
                continue
            key = (name, day)
            with self._lock:
                if key in self._rested:
                    continue
                if self._rng.random() >= chance:
                    self._rested.add(key)
                    continue
                self._rested.add(key)
            remaining = (finish - minute_of_day) % (24 * 60) or 0
            seconds = remaining * 60
            capped = False
            if seconds > cap:
                seconds, capped = cap, True
            label = WINDOW_LABELS.get(name, name)
            return seconds, f"{label}时间，模拟休息", capped
        return 0.0, "", False

    def rest_if_needed(self):
        return self.window_pause()

    # ---------------------------------------------------------------- 控制台展示
    def clock(self, now=None):
        """当前时刻（按节奏时区）。"""
        current = self._now() if now is None else float(now)
        moment = datetime.fromtimestamp(current, self.tz) if self.tz \
            else datetime.fromtimestamp(current)
        return {
            "now": round(current, 1),
            "time": moment.strftime("%H:%M"),
            "date": moment.strftime("%Y-%m-%d"),
            "minute_of_day": moment.hour * 60 + moment.minute,
            "timezone": self.tz_name or "local",
        }

    def timeline(self, now=None):
        """三个休息时段在一天里的位置，以及现在是否正处于其中。"""
        current = self._now() if now is None else float(now)
        moment = datetime.fromtimestamp(current, self.tz) if self.tz \
            else datetime.fromtimestamp(current)
        minute_of_day = moment.hour * 60 + moment.minute
        windows = []
        for name, start, end, chance, cap in self.windows:
            begin, finish = _minutes(start), _minutes(end)
            wraps = finish <= begin
            inside = (minute_of_day >= begin or minute_of_day < finish) if wraps \
                else begin <= minute_of_day < finish
            windows.append({
                "name": name,
                "label": WINDOW_LABELS.get(name, name),
                "start": f"{start[0]:02d}:{start[1]:02d}",
                "end": f"{end[0]:02d}:{end[1]:02d}",
                "start_minute": begin,
                "end_minute": finish,
                "wraps": wraps,
                "chance": chance,
                "cap_minutes": round(cap / 60),
                "active": inside,
                "starts_in_minutes": 0 if inside else (begin - minute_of_day) % (24 * 60),
            })
        return windows

    # ---------------------------------------------------------------- 课程编排
    def plan_courses(self, courses):
        """打乱顺序并按组切分：返回 [(course, 进入前的停顿类型)]。"""
        items = list(courses)
        if not self.enabled or not items:
            return [(course, None) for course in items]
        order = items[:]
        self._rng.shuffle(order)
        size = self._rng.randint(*self.group_size)
        plan = []
        for index, course in enumerate(order):
            wait = None
            if index:
                wait = "group" if index % size == 0 else "course"
            plan.append((course, wait))
        return plan


_DEFAULT = Pacer()
_CURRENT = _DEFAULT
_CURRENT_LOCK = threading.Lock()


def _build(options):
    """按配置 + 环境变量造一个控制器，不改变当前实例。"""
    options = dict(options or {})
    # 环境变量优先级最高，便于线上临时关掉节奏而不改任务
    options["enabled"] = _env_flag("CHAOXING_PACING", bool(options.get("enabled", True)))
    if options["enabled"]:
        gaps = dict(_env_gaps())
        gaps.update(options.pop("gaps", None) or {})
        options["gaps"] = gaps
        if options.get("seed") is None:
            seed = os.environ.get("CHAOXING_PACING_SEED")
            if seed:
                try:
                    options["seed"] = int(seed)
                except ValueError:
                    pass
    if options.get("tz") is None:
        options["tz"] = os.environ.get("CHAOXING_PACING_TZ") or None
    options["exit_on_long_pause"] = _env_flag(
        "CHAOXING_PACING_EXIT", bool(options.get("exit_on_long_pause", True))
    )
    if os.environ.get("CHAOXING_PACING_EXIT_AFTER"):
        try:
            options["exit_after"] = float(os.environ["CHAOXING_PACING_EXIT_AFTER"])
        except ValueError:
            pass
    options["windows"] = _env_windows(options.get("windows"))
    return Pacer(**options)


def configure(settings=None, **kwargs):
    """替换全局节奏控制器，返回新的实例。"""
    global _CURRENT
    options = dict(settings or {})
    options.update(kwargs)
    pacer = _build(options)
    with _CURRENT_LOCK:
        _CURRENT = pacer
    return pacer


def snapshot(settings=None, now=None):
    """只读快照：给控制台画"拟人节奏"页面，不替换全局实例。"""
    pacer = _build(settings)
    clock = pacer.clock(now)
    return {
        "clock": clock,
        "config": pacer.describe(),
        "windows": pacer.timeline(now),
        "gaps": {key: list(value) for key, value in pacer.gaps.items()},
        "labels": {"windows": dict(WINDOW_LABELS), "gaps": dict(GAP_LABELS)},
        "group_size": list(pacer.group_size),
        "micro_break_chance": pacer.micro_break_chance,
    }


def current():
    return _CURRENT


def reset():
    """测试用：恢复到默认（关闭）状态。"""
    global _CURRENT
    with _CURRENT_LOCK:
        _CURRENT = _DEFAULT
    return _CURRENT
