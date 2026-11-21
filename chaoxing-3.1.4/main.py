# -*- coding: utf-8 -*-
import argparse
import configparser
import enum
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from getpass import getpass
from queue import PriorityQueue

try:
    from queue import ShutDown
except ImportError:  # Python < 3.13 compatibility
    class ShutDown(Exception):
        """Fallback exception for older Python queue implementations."""

        pass
from threading import RLock
from typing import Any

from tqdm import tqdm

from api.answer import Tiku
from api.base import Chaoxing, Account, StudyResult
from api.course_points import has_explicit_task_count, task_counts
from api.exceptions import LoginError, InputFormatError
from api.logger import logger
from api.notification import Notification
from api import pacing
from api.runtime import report


class ChapterResult(enum.Enum):
    SUCCESS=0,
    ERROR=1,
    NOT_OPEN=2,
    PENDING=3
    MANUAL=4
    INCOMPLETE=5


def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except BaseException as e:
            logger.error(f"Error in thread {threading.current_thread().name}: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    return wrapper


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Samueli924/chaoxing",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--use-cookies", action="store_true", help="使用cookies登录")
    parser.add_argument("--videos-only", action="store_true", help="只复查并处理未完成视频，不处理测验")
    parser.add_argument("--recheck-completed", action="store_true", help="复查标为已完成章节中的视频")
    parser.add_argument(
        "--no-human-pacing", action="store_true",
        help="关闭拟人节奏（随机等待、随机分组、午休/晚休/夜间暂停），按最快速度跑",
    )

    parser.add_argument(
        "-c", "--config", type=str, default=None, help="使用配置文件运行程序"
    )
    parser.add_argument("-u", "--username", type=str, default=None, help="手机号账号")
    parser.add_argument("-p", "--password", type=str, default=None, help="登录密码")
    parser.add_argument(
        "-l", "--list", type=str, default=None, help="要学习的课程ID列表, 以 , 分隔"
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=1.0, help="视频播放倍速 (默认1, 最大2)"
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=4, help="同时进行的章节数 (默认4, 如果一个章节有多个任务点，不会限制同时处理任务点的数量)"
    )

    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        help="启用调试模式, 输出DEBUG级别日志",
    )
    parser.add_argument(
        "-a", "--notopen-action", type=str, default="retry",
        choices=["retry", "ask", "continue"],
        help="遇到关闭任务点时的行为: retry-重试, ask-询问, continue-继续"
    )

    # 在解析之前捕获 -h 的行为
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def load_config_from_file(config_path):
    """从配置文件加载设置"""
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path, encoding="utf8")

    common_config: dict[str, Any] = {}
    tiku_config: dict[str, Any] = {}
    notification_config: dict[str, Any] = {}

    # 检查并读取common节
    if config.has_section("common"):
        common_config = dict(config.items("common"))
        # 处理course_list，将字符串转换为列表
        if "course_list" in common_config and common_config["course_list"]:
            common_config["course_list"] = [item.strip() for item in common_config["course_list"].split(",") if item.strip()]
        # 处理speed，将字符串转换为浮点数
        if "speed" in common_config:
            common_config["speed"] = float(common_config["speed"])
        if "jobs" in common_config:
            common_config["jobs"] = int(common_config["jobs"])
        # 处理notopen_action，设置默认值为retry
        if "notopen_action" not in common_config:
            common_config["notopen_action"] = "retry"
        for flag in ("use_cookies", "videos_only", "recheck_completed", "human_pacing"):
            if flag in common_config:
                common_config[flag] = str_to_bool(common_config[flag])
        if "username" in common_config and common_config["username"] is not None:
            common_config["username"] = common_config["username"].strip()
        if "password" in common_config and common_config["password"] is not None:
            common_config["password"] = common_config["password"].strip()

    # 检查并读取tiku节
    if config.has_section("tiku"):
        tiku_config = dict(config.items("tiku"))
        # 处理数值类型转换
        for key in ["delay", "cover_rate"]:
            if key in tiku_config:
                tiku_config[key] = float(tiku_config[key])

    # 检查并读取notification节
    if config.has_section("notification"):
        notification_config = dict(config.items("notification"))

    return common_config, tiku_config, notification_config


def build_config_from_args(args):
    """从命令行参数构建配置"""
    common_config = {
        "use_cookies": args.use_cookies,
        "username": args.username,
        "password": args.password,
        "course_list": [item.strip() for item in args.list.split(",") if item.strip()] if args.list else None,
        "speed": args.speed if args.speed else 1.0,
        "jobs": args.jobs,
        "notopen_action": args.notopen_action if args.notopen_action else "retry",
        "human_pacing": not args.no_human_pacing,
    }
    return common_config, {}, {}


def init_config():
    """初始化配置"""
    args = parse_args()

    configs = load_config_from_file(args.config) if args.config else build_config_from_args(args)
    common_config = configs[0]
    if args.videos_only:
        common_config["videos_only"] = True
        common_config["recheck_completed"] = True
    elif args.recheck_completed:
        common_config["recheck_completed"] = True
    return configs


def init_chaoxing(common_config, tiku_config):
    """初始化超星实例"""
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    use_cookies = common_config.get("use_cookies", False)

    # 如果没有提供用户名密码，从命令行获取
    if (not username or not password) and not use_cookies:
        username = input("请输入你的手机号, 按回车确认\n手机号:")
        password = getpass("请输入你的密码, 按回车确认\n密码:")

    account = Account(username, password)

    # 设置题库
    tiku = Tiku()
    if common_config.get("videos_only", False):
        tiku.DISABLE = True
        logger.info("视频补漏模式：不会生成、保存或提交测验答案")
    else:
        tiku.config_set(tiku_config)  # 载入配置
        tiku = tiku.get_tiku_from_config()  # 载入题库
        tiku.init_tiku()  # 初始化题库

    # 获取查询延迟设置
    query_delay = tiku_config.get("delay", 0)

    # 实例化超星API
    chaoxing = Chaoxing(account=account, tiku=tiku, query_delay=query_delay)

    return chaoxing


def process_job(
    chaoxing: Chaoxing, course: dict, job: dict, job_info: dict, speed: float,
    chapter: str = "",
) -> StudyResult:
    """处理单个任务点"""
    job_info = {**job_info, **job.get("_job_info", {})}
    chapter = chapter or str(job_info.get("_progress_chapter", ""))
    report(
        "task", course_id=course["courseId"], course=course["title"],
        chapter_id=job_info.get("_progress_chapter_id", ""),
        chapter=chapter, phase=job["type"], name=job.get("name", ""),
        job_id=job.get("jobid", ""),
        ai_state="", ai_question="", ai_type="", ai_error="",
    )
    # 视频任务
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        video_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type="Video"
        )
        if video_result == StudyResult.NOT_VIDEO:
            logger.info("媒体元数据确认为音频，使用音频上报")
            video_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type="Audio")
        if video_result.is_failure():
            logger.warning(
                f"视频任务未完成 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 将重试并保留失败记录"
            )
        return video_result
    # 文档任务
    elif job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_document(course, job)
    # 测验任务
    elif job["type"] == "workid":
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        return chaoxing.study_work(course, job, job_info)
    # 阅读任务
    elif job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        return chaoxing.study_read(course, job, job_info)

    logger.error("Unknown job type: %s", job["type"])
    return StudyResult.ERROR


@dataclass(order=True)
class ChapterTask:
    index: int
    point: dict[str, Any]
    result: ChapterResult = ChapterResult.PENDING
    tries: int = 0
    expect_tasks: bool = False

class JobProcessor:
    def __init__(self, chaoxing: Chaoxing, course: dict[str, Any], tasks: list[ChapterTask], config: dict[str, Any]):
        self.chaoxing = chaoxing
        self.course = course
        self.speed = config["speed"]
        self.max_tries = 5
        self.tasks = tasks
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.wait_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.worker_num = config["jobs"]
        self.config = config

    def run(self):
        for task in self.tasks:
            self.task_queue.put(task)

        for i in range(self.worker_num):
            thread = threading.Thread(target=self.worker_thread, daemon=True)
            self.threads.append(thread)
            thread.start()

        retry_worker = threading.Thread(target=self.retry_thread, daemon=True)
        retry_worker.start()

        self.task_queue.join()
        self.task_queue.shutdown()
        self.retry_queue.shutdown()
        for thread in self.threads:
            thread.join()
        retry_worker.join()
        return not self.failed_tasks


    @log_error
    def worker_thread(self):
        tqdm.set_lock(tqdm.get_lock())
        while True:
            try:
                task = self.task_queue.get()
            except ShutDown:
                logger.info("Queue shut down")
                return

            try:
                # 进入章节前先看是不是赶上饭点/深夜，是就整段停一次
                pacing.current().rest_if_needed()
                task.result = process_chapter(
                    self.chaoxing, self.course, task.point, self.speed,
                    recheck_completed=self.config.get("recheck_completed", False),
                    videos_only=self.config.get("videos_only", False),
                    expect_tasks=task.expect_tasks,
                    book_log=self.config.get("book_log"),
                )
            except Exception as exc:
                logger.error("章节处理异常，将保留为失败并重试：{} ({})", task.point["title"], type(exc).__name__)
                task.result = ChapterResult.ERROR

            match task.result:
                case ChapterResult.SUCCESS:
                    logger.debug("Task success: {}", task.point["title"])
                    self.task_queue.task_done()
                    logger.debug(f"unfinished task: {self.task_queue.unfinished_tasks}")

                case ChapterResult.NOT_OPEN:
                    task.tries += 1
                    if self.config["notopen_action"] == "continue":
                        logger.warning("章节未开启: {}, 正在跳过", task.point["title"])
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                    elif task.tries >= self.max_tries:
                        logger.error(
                            "章节未开启: {} 可能由于上一章节的章节检测未完成, 也可能由于该章节因为时效已关闭，"
                            "请手动检查完成并提交再重试。或者在配置中配置(自动跳过关闭章节/开启题库并启用提交)"
                        , task.point["title"])
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                    else:
                        self.retry_queue.put(task)

                case ChapterResult.ERROR:
                    task.tries += 1
                    logger.warning("Retrying task {} ({}/{} attempts)", task.point["title"], task.tries,
                                   self.max_tries)
                    if task.tries >= self.max_tries:
                        logger.error("Max retries reached for task: {}", task.point["title"])
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                    else:
                        self.retry_queue.put(task)

                case ChapterResult.MANUAL | ChapterResult.INCOMPLETE:
                    self.failed_tasks.append(task)
                    self.task_queue.task_done()

                case _:
                    logger.error("Invalid task state {} for task {}", task.result, task.point["title"])
                    self.failed_tasks.append(task)
                    self.task_queue.task_done()
            report(
                "chapter_result", course_id=self.course["courseId"],
                chapter_id=task.point.get("id", str(task.index)),
                chapter=task.point["title"], result=task.result.name,
            )
            if self.config.get("publish_outline"):
                try:
                    publish_course_outline(self.course, self.chaoxing.get_course_point(
                        self.course["courseId"], self.course["clazzId"], self.course["cpi"],
                    ))
                except Exception as exc:
                    logger.warning("章节进度回读失败: {}", type(exc).__name__)
            self.pace_between_chapters()

    def pace_between_chapters(self):
        """章节之间的随机停顿，偶尔来一次"走开几分钟"。"""
        pacer = pacing.current()
        pacer.between_chapters()
        pacer.maybe_micro_break()

    @log_error
    def retry_thread(self):
        try:
            while True:
                task = self.retry_queue.get()
                self.task_queue.put(task)
                self.task_queue.task_done() # task_done is not called when a task failed and needs to be retried, so if is reput into the queue, the task num will increase by one and become more than the real task number
                time.sleep(1)
        except ShutDown:
            pass


def process_chapter(
    chaoxing: Chaoxing, course:dict[str, Any], point:dict[str, Any], speed:float,
    *, recheck_completed=False, videos_only=False, expect_tasks=False, book_log=None,
) -> ChapterResult:
    """处理单个章节"""
    report(
        "chapter", course_id=course["courseId"], course=course["title"],
        chapter_id=point.get("id", ""), chapter=point["title"], phase="chapter",
    )
    logger.info(f'当前章节: {point["title"]}')
    completed = bool(point.get("has_finished", False)) and not expect_tasks
    if completed and not (recheck_completed or videos_only):
        logger.info(f'章节：{point["title"]} 已完成所有任务点')
        return ChapterResult.SUCCESS
    if not videos_only and task_counts(point)[0] == 0:
        logger.info("章节列表明确无任务点，跳过：{}", point["title"])
        return ChapterResult.SUCCESS

    # 随机等待，避免请求过快
    chaoxing.rate_limiter.limit_rate(random_time=True,random_min=0, random_max=0.2)

    # 获取当前章节的所有任务点
    if completed:
        logger.info("章节标记已完成，正在复查未完成视频：{}", point["title"])
    jobs, job_info = chaoxing.get_job_list(
        course, point, visit_empty=not (videos_only or completed or expect_tasks)
    )

    # 发现未开放章节, 根据配置处理
    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    # 图书任务点：平台要求真的打开阅读器读，脚本没有这条链路。
    # 单独登记后交给人工，绝不能让它把整章标成"需人工"、把课程卡成未确认。
    books = job_info.get("book_tasks") or []
    if books:
        if book_log is not None:
            book_log[str(point.get("id", ""))] = books
        logger.warning(
            "本章有 {} 个图书任务点，脚本不处理，需人工阅读：{}",
            len(books), "；".join(str(book.get("title", "")) for book in books),
        )
        if not videos_only:
            report(
                "phase", course_id=course["courseId"], course=course["title"],
                chapter_id=point.get("id", ""), chapter=point["title"], phase="chapter",
                name="需人工阅读图书：" + "、".join(str(book.get("title", "")) for book in books[:6]),
                manual_tasks=[{
                    "type": "book", "title": str(book.get("title", ""))[:200],
                    "card_num": str(book.get("card_num", ""))[:6],
                } for book in books],
            )

    if job_info.get("empty") and not jobs and not job_info.get("untracked_videos"):
        if videos_only or completed:
            logger.info("暂无内容，没有视频需要复查：{}", point["title"])
            return ChapterResult.SUCCESS
        return confirm_empty_chapter(chaoxing, course, point, job_info)

    if videos_only or (completed and recheck_completed and not expect_tasks):
        jobs = [job for job in jobs if job["type"] == "video"]

    progress_job_info = dict(job_info)
    progress_job_info["_progress_chapter"] = point["title"]
    progress_job_info["_progress_chapter_id"] = point.get("id", "")

    # Keep one chapter's quiz session and progress ordered. A failed job must
    # not discard its following documents, readings, or quizzes.
    ordered_jobs = [job for job in jobs if job["type"] == "video"]
    ordered_jobs += [job for job in jobs if job["type"] != "video"]
    job_results = []
    previous_job = None
    for job in ordered_jobs:
        # 视频看完先缓 1–15 秒再答题；其它任务点之间也有一个短间隔
        if previous_job is not None:
            if previous_job["type"] == "video":
                pacing.current().after_video()
            else:
                pacing.current().between_jobs()
        try:
            result = process_job(chaoxing, course, job, progress_job_info, speed)
        except Exception as exc:
            logger.error(
                "任务处理异常，继续其它任务: chapter={}, type={}, job={}, error={}",
                point["title"], job["type"], job.get("jobid", ""), type(exc).__name__,
            )
            result = StudyResult.ERROR
        previous_job = job
        job_results.append(result)
    if any(result == StudyResult.TIMEOUT for result in job_results):
        return ChapterResult.INCOMPLETE
    if any(result.is_failure() and result != StudyResult.MANUAL for result in job_results):
        return ChapterResult.ERROR
    if StudyResult.MANUAL in job_results:
        return ChapterResult.MANUAL

    if job_info.get("untracked_videos"):
        logger.warning(
            "以下视频缺少可自动处理的任务标识，需要手动检查：{}",
            "；".join(job_info["untracked_videos"]),
        )
        return ChapterResult.MANUAL
    if job_info.get("unresolved_tasks") and not videos_only:
        tasks = job_info["unresolved_tasks"][:40]
        manual_tasks = [{
            "type": str(task.get("type", ""))[:40],
            "title": str(task.get("title", "待检查任务"))[:200],
            "card_num": str(task.get("card_num", ""))[:6],
        } for task in tasks if isinstance(task, dict)]
        logger.warning(
            "章节仍有需要人工处理的任务卡片: chapter={}, tasks={}",
            point["title"],
            "；".join(f"{task['type']}:{task['title']} (card={task['card_num']}, job={task['jobid']})"
                     for task in tasks),
        )
        report(
            "phase", course_id=course["courseId"], chapter_id=point.get("id", ""),
            chapter=point["title"], phase="chapter",
            name="需要人工处理：" + "；".join(f"{task['title']} [{task['type']}]" for task in tasks),
            manual_tasks=manual_tasks,
        )
        return ChapterResult.MANUAL
    if expect_tasks and not jobs and not books:
        latest = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
        refreshed = next((
            item for item in latest.get("points", []) if str(item.get("id")) == str(point["id"])
        ), None)
        if refreshed is None or point_remaining(refreshed) > 0 or refreshed.get("need_unlock"):
            logger.warning("章节列表仍有剩余任务，但页面没有可读任务：{}", point["title"])
            return ChapterResult.INCOMPLETE
    if books and not jobs:
        # 只剩图书任务：可自动处理的部分已经做完，剩下的登记给人工，不算章节失败
        logger.info("本章只剩图书任务点，已登记待人工阅读：{}", point["title"])
    return ChapterResult.SUCCESS




def confirm_empty_chapter(chaoxing, course, point, job_info):
    # Missing counters used to default to one task. Only the platform's empty
    # placeholder and a fresh outline can resolve that fallback safely.
    if not has_explicit_task_count(point):
        if "empty_visit_succeeded" in job_info:
            visited = job_info["empty_visit_succeeded"]
        else:
            visited = chaoxing.study_emptypage(course, point) == StudyResult.SUCCESS
        if not visited:
            logger.warning("空章节访问失败，保留未完成：{}", point["title"])
            return ChapterResult.ERROR
    latest = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
    refreshed = next((
        item for item in latest.get("points", []) if str(item.get("id")) == str(point["id"])
    ), None)
    if refreshed is None or point_remaining(refreshed) > 0:
        logger.warning(
            "页面暂无内容，但平台列表仍有任务或未确认访问状态，保留未完成：chapter={}, listed={}, known={}",
            point["title"], point_remaining(point), has_explicit_task_count(point),
        )
        return ChapterResult.INCOMPLETE
    if refreshed.get("need_unlock"):
        return ChapterResult.NOT_OPEN
    report(
        "phase", course_id=course["courseId"], chapter_id=point["id"],
        chapter=point["title"], phase="empty_chapter",
    )
    logger.info("空章节已由平台回读确认，无需视频或答题：{}", point["title"])
    return ChapterResult.SUCCESS


def point_remaining(point):
    """Use the same completion counts shown by the fetched chapter list."""
    return task_counts(point)[2]


def publish_course_outline(course, payload):
    from backend.course_worker import public_course_detail
    try:
        detail = public_course_detail(course, payload)
    except (ValueError, TypeError):
        return
    report("course_outline", course_id=course["courseId"], detail=detail)


def process_course(chaoxing: Chaoxing, course:dict[str, Any], config: dict):
    """处理单个课程"""
    logger.info(f"开始学习课程: {course['title']}")

    # 获取当前课程的所有章节
    point_list = chaoxing.get_course_point(
        course["courseId"], course["clazzId"], course["cpi"]
    )
    if not point_list.get("points"):
        logger.error("未读到课程章节，不能认定课程已完成")
        return False
    # 任务里点名了章节就只学那几节，没点名照旧整门课
    wanted = [str(item) for item in (config.get("point_ids") or [])]
    points = point_list["points"]
    if wanted:
        asked = set(wanted)
        points = [point for point in points if str(point.get("id", "")) in asked]
        missing = asked - {str(point.get("id", "")) for point in points}
        if missing:
            logger.warning(
                "任务指定的章节已不在课程列表里，保留未完成：course={}, missing={}",
                course["title"], ",".join(sorted(missing)),
            )
            return False
        logger.info("只处理任务指定的 {} 个章节：{}", len(points), course["title"])
    report(
        "course", course_id=course["courseId"], course=course["title"],
        phase="loading_chapters", total=len(points),
    )
    publish_course_outline(course, point_list)

    # 为了支持课程任务回滚, 采用下标方式遍历任务点

    _old_format_sizeof = tqdm.format_sizeof
    tqdm.format_sizeof = format_time
    tqdm.set_lock(RLock())

    tasks=[]
    videos_only = config.get("videos_only", False)

    for i, point in enumerate(points):
        remaining = point_remaining(point)
        if wanted:
            # 点名的章节即使平台已标记完成，也走"复查"而不是直接跳过
            tasks.append(ChapterTask(point=point, index=i, expect_tasks=remaining > 0))
            continue
        if not videos_only and remaining <= 0:
            report(
                "chapter_result", course_id=course["courseId"], course=course["title"],
                chapter_id=point.get("id", str(i)), chapter=point["title"],
                result=ChapterResult.SUCCESS.name, phase="skipped",
            )
            continue
        tasks.append(ChapterTask(
            point=point, index=i, expect_tasks=not videos_only and remaining > 0,
        ))
    run_config = dict(config)
    if wanted:
        # 点名章节时强制复查，避免"选了却什么都没做"
        run_config["recheck_completed"] = True
    # 图书任务点脚本做不了，处理时登记到这张表，回读时按它扣掉
    collector = config.get("book_log")
    if isinstance(collector, dict):
        book_log = collector.setdefault(str(course["courseId"]), {})
    else:
        book_log = {}
    run_config["book_log"] = book_log
    p = JobProcessor(chaoxing, course, tasks, run_config)
    try:
        complete = p.run()
    finally:
        tqdm.format_sizeof = _old_format_sizeof
    for task in p.failed_tasks:
        logger.warning("课程仍有未完成章节：{} [{}]", task.point["title"], task.result.name)
    if videos_only:
        return complete
    # 图书任务点（insertbook）：平台要真打开阅读器，脚本没有这条链路。
    # 登记出来交给人工，同时从"未完成"里扣掉，别让整门课/整个账号卡住。
    books = [book for items in book_log.values() for book in items]
    manual_books = [{
        "type": "book", "title": str(book.get("title", ""))[:200],
        "card_num": str(book.get("card_num", ""))[:6],
    } for book in books[:40]]
    if books:
        logger.warning(
            "课程有 {} 个图书任务点脚本不处理，需人工阅读：{}",
            len(books), "、".join(dict.fromkeys(str(book.get("title", "")) for book in books)),
        )
    try:
        latest = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
        fresh_points = latest.get("points", [])
        publish_course_outline(course, latest)
        expected_ids = {str(point["id"]) for point in points}

        def leftover(point):
            """扣除本章已知的图书任务点后，还剩多少脚本能做的任务。"""
            count = point_remaining(point)
            known_books = len(book_log.get(str(point.get("id")), []))
            return max(0, count - known_books)

        if not fresh_points or not expected_ids.issubset({str(point["id"]) for point in fresh_points}):
            logger.warning("课程结束回读缺少章节，保留未完成：{}", course["title"])
            return False
        remaining = [
            point for point in fresh_points
            if leftover(point) > 0 and str(point.get("id")) in expected_ids
        ]
        unfinished = [point for point in fresh_points if leftover(point) > 0]
        # 只学了几节时，即使这几节都完成了，也不能把整门课记成已完成
        partial = len(unfinished) > len(remaining)
        report(
            "course_result", course_id=course["courseId"], course=course["title"],
            phase="course_confirmed" if not remaining and not partial else "course_unconfirmed",
            remaining_chapters=len(unfinished) if partial else len(remaining),
            manual_tasks=manual_books,
        )
        if remaining:
            logger.warning(
                "平台回读仍有{}个未完成章节：{}",
                len(remaining), "；".join(point["title"] for point in remaining),
            )
            return False
        if partial:
            logger.info(
                "任务指定的章节已完成，课程其余章节未处理：{}（还剩{}节）",
                course["title"], len(unfinished),
            )
            return True
        logger.info("平台章节列表确认课程任务点全部完成：{}", course["title"])
        return True
    except Exception as exc:
        logger.warning("课程结束回读失败，保留未完成：{} ({})", course["title"], type(exc).__name__)
        return False

    """
    while __point_index < len(point_list["points"]):
        point = point_list["points"][__point_index]
        logger.debug(f"当前章节 __point_index: {__point_index}")
        
        result, auto_skip_notopen = process_chapter(
            chaoxing, course, point, RB, notopen_action, speed, auto_skip_notopen
        )
        
        if result == -1:  # 退出当前课程
            break
        elif result == 0:  # 重试前一章节
            __point_index -= 1  # 默认第一个任务总是开放的
        else:  # 继续下一章节
            __point_index += 1
    """



def filter_courses(all_course, course_list):
    """过滤要学习的课程"""
    if not course_list:
        # 手动输入要学习的课程ID列表
        print("*" * 10 + "课程列表" + "*" * 10)
        for course in all_course:
            print(f"ID: {course['courseId']} 课程名: {course['title']}")
        print("*" * 28)
        try:
            course_list = input(
                "请输入想要学习的课程列表,以逗号分隔,例: 2151141,189191,198198\n"
            ).split(",")
        except Exception as e:
            raise InputFormatError("输入格式错误") from e

    # 筛选需要学习的课程
    course_task = []
    course_ids = []
    for course in all_course:
        if course["courseId"] in course_list and course["courseId"] not in course_ids:
            course_task.append(course)
            course_ids.append(course["courseId"])

    # 如果没有指定课程，则学习所有课程
    if not course_task:
        course_task = all_course

    return course_task


def format_time(num, suffix='', divisor=''):
    total_time = round(num)
    sec = total_time % 60
    mins = (total_time % 3600) // 60
    hrs = total_time // 3600

    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"

    return f"{mins:02d}:{sec:02d}"


def main():
    """主程序入口"""
    try:
        # 初始化配置
        common_config, tiku_config, notification_config = init_config()

        # 强制播放按照配置文件调节
        common_config["speed"] = min(2.0, max(1.0, common_config.get("speed", 1.0)))
        common_config["notopen_action"] = common_config.get("notopen_action", "retry")

        # 拟人节奏：命令行/配置文件默认开启，可用 CHAOXING_PACING=0 关闭
        pacing.configure(
            {"enabled": str_to_bool(common_config.get("human_pacing", True))},
            reporter=report, logger=logger,
        )

        # 初始化超星实例
        chaoxing = init_chaoxing(common_config, tiku_config)

        # 设置外部通知
        notification = Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()

        # 检查当前登录状态
        _login_state = chaoxing.login(login_with_cookies=common_config.get("use_cookies", False))
        if not _login_state["status"]:
            raise LoginError(_login_state["msg"])
        # 大休息时退出登录、结束后再登录（拟人节奏关闭时是空操作）
        chaoxing.bind_pacing_session()

        # 获取所有的课程列表
        all_course = chaoxing.get_course_list()

        # 过滤要学习的课程
        course_task = filter_courses(all_course, common_config.get("course_list"))

        # 开始学习
        logger.info(f"课程列表过滤完毕, 当前课程任务数量: {len(course_task)}")
        from backend.worker import run_courses
        _, failed_courses = run_courses(
            lambda course: process_course(chaoxing, course, common_config), course_task,
        )

        if failed_courses:
            logger.warning("本次处理结束，以下课程仍有未完成或需人工检查的任务：{}", "、".join(failed_courses))
            notification.send("chaoxing : 本次仍有未完成任务，请检查日志")
        else:
            logger.info("本次可处理课程任务已完成")
            notification.send("chaoxing : 本次可处理课程任务已完成")

    except SystemExit as e:
        if e.code != 0:
            logger.error(f"错误: 程序异常退出, 返回码: {e.code}")
        sys.exit(e.code)
    except KeyboardInterrupt as e:
        logger.error(f"错误: 程序被用户手动中断, {e}")
    except BaseException as e:
        logger.error(f"错误: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        try:
            notification.send(f"chaoxing : 出现错误 {type(e).__name__}: {e}\n{traceback.format_exc()}")
        except Exception:
            pass  # 如果通知发送失败，忽略异常
        raise e


if __name__ == "__main__":
    main()
