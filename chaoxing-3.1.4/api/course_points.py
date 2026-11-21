"""Completion counts shared by the chapter queue and public course outline."""


def nonnegative_count(value, default=0):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def task_counts(point):
    task_count = nonnegative_count(point.get("jobCount"), 1)
    completed_count = min(task_count, nonnegative_count(
        point.get("completedJobCount"), task_count if point.get("has_finished") else 0
    ))
    return task_count, completed_count, task_count - completed_count


def has_explicit_task_count(point):
    return point.get("jobCountKnown") is not False and nonnegative_count(
        point.get("jobCount"), None
    ) is not None
