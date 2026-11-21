"""Read one account's course list without importing or running learning tasks."""

import json
import os
from pathlib import Path
import re
import sys
import threading

from api.course_points import task_counts
from backend.worker import watch_parent



def empty_summary(status):
    return {
        "status": status, "point_count": 0, "task_count": 0, "completed_count": 0,
        "remaining_count": 0, "locked_count": 0,
    }


def summary_from_detail(detail):
    totals = detail["totals"]
    return {
        "status": "ready",
        "point_count": totals["point_count"],
        "task_count": totals["task_count"],
        "completed_count": totals["completed_count"],
        "remaining_count": totals["remaining_count"],
        "locked_count": sum(1 for point in detail["points"] if point["locked"]),
    }


def validate_summary(summary):
    if not isinstance(summary, dict):
        raise ValueError("Invalid course summary")
    status = summary.get("status")
    names = ("point_count", "task_count", "completed_count", "remaining_count", "locked_count")
    values = [summary.get(name) for name in names]
    if status not in {"ready", "failed"} or any(type(item) is not int for item in values):
        raise ValueError("Invalid course summary")
    point_count, task_count, completed_count, remaining_count, locked_count = values
    if min(values) < 0 or completed_count > task_count or remaining_count != task_count - completed_count \
            or locked_count > point_count or point_count > 2000 or task_count > 200000:
        raise ValueError("Invalid course summary")
    return {
        "status": status, "point_count": point_count, "task_count": task_count,
        "completed_count": completed_count, "remaining_count": remaining_count,
        "locked_count": locked_count,
    }


def public_courses(available):
    if not isinstance(available, list) or len(available) > 500:
        raise ValueError("Invalid course list")
    result, seen = [], set()
    for course in available:
        if not isinstance(course, dict):
            raise ValueError("Invalid course")
        course_id = str(course.get("courseId", course.get("id", "")))
        title = course.get("title")
        teacher = course.get("teacher", "")
        if not re.fullmatch(r"[0-9]{1,20}", course_id) or not isinstance(title, str) \
                or not title.strip() or not isinstance(teacher, str):
            raise ValueError("Invalid course fields")
        if course_id in seen:
            continue
        seen.add(course_id)
        item = {
            "id": course_id,
            "title": " ".join(title.split())[:300],
            "teacher": " ".join(teacher.split())[:120],
        }
        if "summary" in course:
            item["summary"] = validate_summary(course["summary"])
        result.append(item)
    return result


def public_course_detail(course, point_payload):
    """Return only the safe outline fields needed by the external UI."""
    public_course = public_courses([course])[0]
    points = point_payload.get("points") if isinstance(point_payload, dict) else None
    if not isinstance(points, list) or len(points) > 2000:
        raise ValueError("Invalid course points")

    result = []
    total_tasks = completed_tasks = 0
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("Invalid course point")
        point_id = str(point.get("id", ""))
        title = point.get("title")
        if not re.fullmatch(r"[0-9]{1,20}", point_id) or not isinstance(title, str) \
                or not title.strip():
            raise ValueError("Invalid course point fields")
        task_count, completed_count, remaining_count = task_counts(point)
        locked = bool(point.get("need_unlock"))
        result.append({
            "id": point_id,
            "title": " ".join(title.split())[:300],
            "task_count": task_count,
            "completed_count": completed_count,
            "remaining_count": remaining_count,
            "completed": task_count > 0 and remaining_count == 0 and not locked,
            "locked": locked,
        })
        total_tasks += task_count
        completed_tasks += completed_count
    return {
        "course": public_course,
        "points": result,
        "totals": {
            "task_count": total_tasks,
            "completed_count": completed_tasks,
            "remaining_count": max(0, total_tasks - completed_tasks),
            "point_count": len(result),
        },
    }


def validate_public_course_detail(value):
    """Validate and normalize a detail packet returned by the private child."""
    if not isinstance(value, dict):
        raise ValueError("Invalid course detail")
    course = public_courses([value.get("course")])[0]
    points = value.get("points")
    totals = value.get("totals")
    if not isinstance(points, list) or len(points) > 2000 or not isinstance(totals, dict):
        raise ValueError("Invalid course detail")

    normalized = []
    total_tasks = completed_tasks = 0
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("Invalid course point")
        point_id = str(point.get("id", ""))
        title = point.get("title")
        if not re.fullmatch(r"[0-9]{1,20}", point_id) or not isinstance(title, str) \
                or not title.strip():
            raise ValueError("Invalid course point fields")
        counts = [point.get(name) for name in ("task_count", "completed_count", "remaining_count")]
        if any(type(item) is not int for item in counts):
            raise ValueError("Invalid course point counts")
        task_count, completed_count, remaining_count = counts
        if not 0 <= task_count <= 10000 or not 0 <= completed_count <= task_count \
                or remaining_count != task_count - completed_count:
            raise ValueError("Invalid course point counts")
        if type(point.get("completed")) is not bool or type(point.get("locked")) is not bool:
            raise ValueError("Invalid course point state")
        if point["completed"] != (task_count > 0 and remaining_count == 0 and not point["locked"]):
            raise ValueError("Invalid course point state")
        normalized.append({
            "id": point_id,
            "title": " ".join(title.split())[:300],
            "task_count": task_count,
            "completed_count": completed_count,
            "remaining_count": remaining_count,
            "completed": point["completed"],
            "locked": point["locked"],
        })
        total_tasks += task_count
        completed_tasks += completed_count

    expected = {
        "task_count": total_tasks,
        "completed_count": completed_tasks,
        "remaining_count": total_tasks - completed_tasks,
        "point_count": len(normalized),
    }
    if any(type(totals.get(name)) is not int for name in expected):
        raise ValueError("Invalid course detail totals")
    if totals != expected:
        raise ValueError("Invalid course detail totals")
    return {"course": course, "points": normalized, "totals": expected}


def fetch_courses(credentials, factory=None):
    if factory is None:
        from api.base import Account, Chaoxing
        factory = lambda value: Chaoxing(account=Account(value["username"], value["password"]))
    client = factory(credentials)
    if client.login().get("status") is not True:
        return {"state": "error", "error_code": "LOGIN_REQUIRED"}
    enriched, seen = [], set()
    for course in client.get_course_list():
        if not isinstance(course, dict):
            enriched.append(course)
            continue
        course_id = str(course.get("courseId", course.get("id", "")))
        if course_id in seen:
            continue
        seen.add(course_id)
        item = dict(course)
        if "summary" not in item:
            try:
                outline = client.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
                item["summary"] = summary_from_detail(public_course_detail(course, outline))
            except Exception:
                item["summary"] = empty_summary("failed")
        enriched.append(item)
    return {"state": "done", "courses": public_courses(enriched)}


def fetch_course_detail(credentials, course_id, factory=None):
    if factory is None:
        from api.base import Account, Chaoxing
        factory = lambda value: Chaoxing(account=Account(value["username"], value["password"]))
    client = factory(credentials)
    if client.login().get("status") is not True:
        return {"state": "error", "error_code": "LOGIN_REQUIRED"}
    available = client.get_course_list()
    selected = next((course for course in available if str(course.get("courseId")) == str(course_id)), None)
    if selected is None:
        return {"state": "error", "error_code": "COURSE_NOT_FOUND"}
    outline = client.get_course_point(
        selected["courseId"], selected["clazzId"], selected["cpi"]
    )
    return {"state": "done", **public_course_detail(selected, outline)}


def main():
    raw = sys.stdin.buffer.readline(65537)
    if not raw or len(raw) > 65536:
        return 30
    threading.Thread(target=watch_parent, args=(sys.stdin.buffer,), daemon=True).start()
    os.environ["CHAOXING_HEADLESS"] = "1"
    from api.config import GlobalConst
    from api.base import SessionManager
    from api.logger import logger

    logger.remove()
    GlobalConst.COOKIES_PATH = str(Path.cwd() / "cookies.txt")
    try:
        payload = json.loads(raw)
        if payload.get("mode") == "detail":
            packet = fetch_course_detail(payload["credentials"], payload["course_id"])
        else:
            packet = fetch_courses(payload["credentials"])
    except Exception:
        packet = {"state": "error", "error_code": "COURSE_FETCH_FAILED"}
    finally:
        SessionManager.close_current()
        logger.complete()
    print(json.dumps(packet, ensure_ascii=True), flush=True)
    return 0 if packet["state"] == "done" else 10


if __name__ == "__main__":
    raise SystemExit(main())
