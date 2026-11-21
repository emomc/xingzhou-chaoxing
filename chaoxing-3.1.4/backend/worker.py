"""One account per process. Bootstrap secrets travel over stdin, not command arguments."""

import json
import os
from pathlib import Path
import sys
import threading


def watch_parent(stream):
    # A buffered stdin read in a daemon thread can block interpreter shutdown.
    os.read(stream.fileno(), 1)
    os._exit(75)


def select_courses(available, course_ids):
    by_id = {}
    for course in available:
        by_id.setdefault(str(course["courseId"]), course)
    if any(str(course_id) not in by_id for course_id in course_ids):
        raise ValueError("A requested course was not found")
    return [by_id[str(course_id)] for course_id in course_ids]


def run_courses(process, courses, pacer=None):
    """逐门课程执行；开启拟人节奏时打乱顺序并在课程/分组之间停顿。"""
    from api import pacing
    pacer = pacer or pacing.current()
    failed = []
    plan = pacer.plan_courses(courses)
    if plan:
        pacer.start_jitter()
    for index, (course, wait) in enumerate(plan):
        if index and wait == "group":
            pacer.between_groups()
        elif index and wait == "course":
            pacer.between_courses()
        try:
            success = process(course)
        except Exception as exc:
            from api.logger import logger
            logger.error(
                "课程处理异常，继续后续课程: course={}, error={}",
                course.get("courseId", ""), type(exc).__name__,
            )
            success = False
        if not success:
            failed.append(str(course.get("title") or course.get("courseId") or "course")[:80])
    return not failed, failed


def main():
    bootstrap = sys.stdin.buffer.readline(65537)
    if not bootstrap or len(bootstrap) > 65536:
        return 30
    payload = json.loads(bootstrap)
    output_lock = threading.Lock()
    stopped = threading.Event()

    def emit(event, data):
        with output_lock:
            sys.stdout.write(json.dumps(
                {"event": event, "data": data}, ensure_ascii=False, separators=(",", ":")
            ) + "\n")
            sys.stdout.flush()

    # Closing this pipe also stops a child after an abrupt supervisor crash.
    threading.Thread(target=watch_parent, args=(sys.stdin.buffer,), daemon=True).start()

    def heartbeat():
        while not stopped.wait(10):
            emit("heartbeat", {})

    threading.Thread(target=heartbeat, daemon=True).start()
    os.environ["CHAOXING_HEADLESS"] = "1"
    os.environ["CHAOXING_LOG_FILE"] = str(Path.cwd() / "worker.log")
    from api.config import GlobalConst
    from api.runtime import set_progress_callback
    from api.base import SessionManager
    from api.logger import logger
    import main as course_main

    GlobalConst.THRESHOLD = 1
    GlobalConst.COOKIES_PATH = str(Path.cwd() / "cookies.txt")
    totals, completed = {}, set()

    def progress(event, data):
        if event == "course_outline":
            course_id = str(data["course_id"])
            points = data["detail"]["points"]
            totals[course_id] = len(points)
            completed.difference_update({key for key in completed if key[0] == course_id})
            completed.update((course_id, point["id"]) for point in points if point["remaining_count"] == 0)
            emit(event, {
                **data, "completed": len(completed), "total": sum(totals.values()),
            })
            return
        if event == "course":
            totals[data["course_id"]] = data["total"]
        if event == "chapter_result" and data.get("result") == "SUCCESS":
            completed.add((str(data.get("course_id", "")), str(data.get("chapter_id", ""))))
        if data.get("phase") == "skipped":
            emit(event, {
                "course_id": str(data.get("course_id", ""))[:40],
                "completed": len(completed), "total": sum(totals.values()),
            })
            return
        fields = {
            key: value for key, value in data.items()
            if key in {
                "course", "chapter", "phase", "name", "position", "duration", "result",
                "ai_state", "ai_question", "ai_type", "ai_error", "course_id",
                "failed_courses", "chapter_id", "job_id", "video_state", "video_error",
                "replay_count", "quiz_state", "quiz_answer", "remaining_chapters", "manual_tasks",
                "pacing_kind", "pacing_until", "pacing_minutes",
                "rollback_from", "rollback_to", "rollback_dropped", "rollback_count",
                "rollback_action",
            }
        }
        if "manual_tasks" in fields:
            tasks = fields["manual_tasks"]
            fields["manual_tasks"] = [{
                "type": str(task.get("type", ""))[:40],
                "title": str(task.get("title", "待检查任务"))[:200],
                "card_num": str(task.get("card_num", ""))[:6],
            } for task in tasks[:40] if isinstance(task, dict)] if isinstance(tasks, list) else []
        if "course_id" in fields:
            fields["course_id"] = str(fields["course_id"])[:40]
        fields.update(completed=len(completed), total=sum(totals.values()))
        emit(event, fields)

    set_progress_callback(progress)
    credentials, spec = payload["credentials"], payload["spec"]
    point_ids = spec.get("point_ids") or {}
    common = {
        "username": credentials["username"], "password": credentials["password"],
        "use_cookies": False, "jobs": 1, "speed": 1,
        "notopen_action": "continue", "videos_only": spec["videos_only"],
        "recheck_completed": bool(spec["videos_only"]), "publish_outline": True,
    }
    tiku = {
        "provider": "WebAI", "submit": str(spec["submit"]).lower(), "cover_rate": "1",
        "delay": 0, "true_list": "true", "false_list": "false",
        "webai_preview": str(spec["preview"]).lower(),
        "experimental_vision": str(spec.get("experimental_vision", False)).lower(),
        "webai_cache": str(Path.cwd() / "answers.json"),
        "webai_broker_endpoint": payload["internal_url"] + "/internal",
        "webai_broker_token": payload["worker_token"],
        "webai_broker_timeout": payload["ai_wait_timeout"],
    }
    from api import pacing
    from api import video_guard
    video_guard.configure(
        payload.get("video_guard") or {},
        reporter=lambda event, data: progress(event, data),
    )
    pacer = pacing.configure(
        {"enabled": bool(spec.get("human_pacing", True))},
        reporter=lambda event, **data: progress(event, data),
        logger=logger,
    )
    # 图书任务点（insertbook）脚本做不了，各课程登记到这里，跑完统一交给人工
    book_log = {}
    try:
        progress("phase", {"phase": "pacing", "name": (
            "拟人节奏已开启：随机等待、随机分组、午休/晚休/夜间暂停"
            if pacer.enabled else "拟人节奏已关闭：按最快速度执行"
        )})
        emit("phase", {"phase": "login"})
        chaoxing = course_main.init_chaoxing(common, tiku)
        login = chaoxing.login()
        if not login.get("status"):
            emit("result", {"outcome": "needs_attention", "error_code": "LOGIN_REQUIRED"})
            return 10
        # 大休息时真的退出登录、休息结束再登录，平台侧就是"人走了"
        chaoxing.bind_pacing_session()
        emit("phase", {"phase": "loading_courses"})
        available = chaoxing.get_course_list()
        try:
            selected = select_courses(available, spec["course_ids"])
        except ValueError:
            emit("result", {"outcome": "needs_attention", "error_code": "COURSE_NOT_FOUND"})
            return 20
        success, failed = run_courses(
            lambda course: course_main.process_course(chaoxing, course, {
                **common,
                # 任务里点名了章节就只学那几节，没点名照旧整门课
                "point_ids": point_ids.get(str(course["courseId"])) or [],
                "book_log": book_log,
            }), selected,
        )
        emit("phase", {"failed_courses": "、".join(failed)[:300]})
        books = [
            book for chapters in book_log.values()
            for items in chapters.values() for book in items
        ]
        if books:
            # 同一本书会出现在多个章节里，按书名合并成一条，标上涉及几节
            merged = {}
            for book in books:
                title = str(book.get("title", "")).strip() or "图书"
                entry = merged.setdefault(title, {"title": title, "card_num": str(book.get("card_num", ""))[:6], "chapters": 0})
                entry["chapters"] += 1
            tasks = [{
                "type": "book",
                "title": (entry["title"] if entry["chapters"] == 1
                          else f"{entry['title']}（共 {entry['chapters']} 节）")[:200],
                "card_num": entry["card_num"],
            } for entry in merged.values()]
            progress("phase", {
                "phase": "books",
                "name": "需人工阅读图书：" + "、".join(merged)[:300],
                "manual_tasks": tasks[:40],
            })
        if success and not spec["videos_only"] and spec["preview"]:
            emit("result", {"outcome": "needs_attention", "error_code": "PREVIEW_ONLY"})
            return 20
        outcome = "succeeded" if success else "needs_attention"
        emit("result", {"outcome": outcome, "error_code": "" if success else "INCOMPLETE_TASKS"})
        return 0 if success else 20
    except Exception:
        # Exception text may contain platform URLs, cookies, or credentials.
        logger.error("Worker failed; sensitive exception details were suppressed")
        emit("result", {"outcome": "failed", "error_code": "WORKER_ERROR"})
        return 30
    finally:
        stopped.set()
        set_progress_callback(None)
        SessionManager.close_current()
        # 一个进程只跑一个任务；退出时把全局节奏恢复默认，避免同进程内的后续调用继续等待。
        pacing.reset()
        logger.complete()


if __name__ == "__main__":
    raise SystemExit(main())
