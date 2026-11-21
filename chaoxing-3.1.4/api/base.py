# -*- coding: utf-8 -*-
import functools
import os
import random
import re
import threading
import time
from enum import Enum
from hashlib import md5
from typing import Self, Optional, Literal

import requests
from loguru import logger
from requests import RequestException
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

from api.answer import *
from api.answer_check import cut
from api.cipher import AESCipher
from api.config import GlobalConst as gc
from api.cookies import remove_cookies_file, save_cookies, use_cookies
from api.course_points import has_explicit_task_count, task_counts
from api.decode import (
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_card_count,
    decode_course_folder,
    decode_questions_info,
    is_task_passed,
    parse_video_play_time,
)
from api.exceptions import MaxRetryExceeded
from api.runtime import report
from api.webai_errors import QuestionImageUnavailable
from api import video_guard


def get_timestamp():
    return str(int(time.time() * 1000))


class SessionManager:
    _local = threading.local()
    _cookies_lock = threading.RLock()
    _cookies = None
    _cookie_version = 0

    def __new__(cls, *args, **kwargs):
        if not hasattr(cls._local, "instance"):
            cls._local.instance = super().__new__(cls)
        return cls._local.instance

    def __init__(self):
        if not hasattr(self, "_session"):
            self._session = requests.Session()
            retry = Retry(
                total=2, connect=2, read=0, status=0, backoff_factor=0.5,
                allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
            )
            for scheme in ("https://", "http://"):
                self._session.mount(
                    scheme, HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
                )
            self._session.request = functools.partial(
                self._session.request, timeout=(5, 20)
            )
            self._session.headers.clear()
            self._session.headers.update(gc.HEADERS)
            self._version = -1
        with self._cookies_lock:
            if type(self)._cookies is None:
                type(self)._cookies = use_cookies()
            if self._version != type(self)._cookie_version:
                self._session.cookies.clear()
                self._session.cookies.update(type(self)._cookies)
                self._version = type(self)._cookie_version

    @classmethod
    def get_instance(cls) -> Self:
        return cls()

    @classmethod
    def get_session(cls) -> requests.Session:
        instance = cls.get_instance()
        return instance._session

    @classmethod
    def update_cookies(cls):
        with cls._cookies_lock:
            cls._cookies = use_cookies()
            cls._cookie_version += 1
        cls.get_instance()

    @classmethod
    def clear_cookies(cls):
        """丢掉本地会话：内存里的 cookie 和 cookie 文件都清掉。"""
        with cls._cookies_lock:
            cls._cookies = {}
            cls._cookie_version += 1
        try:
            remove_cookies_file()
        except OSError:
            logger.warning("Cookie 文件清理失败，继续使用内存状态")
        cls.get_instance()

    @classmethod
    def close_current(cls):
        instance = getattr(cls._local, "instance", None)
        if instance is not None:
            instance._session.close()
            del cls._local.instance


class Account:
    username = None
    password = None
    last_login = None
    isSuccess = None

    def __init__(self, _username, _password):
        self.username = _username
        self.password = _password


class RateLimiter:
    def __init__(self, call_interval):
        self.last_call = time.time()
        self.lock = threading.Lock()
        self.call_interval = call_interval

    def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
        with self.lock:
            if random_time:
                wait_time = random.uniform(random_min, random_max)
                time.sleep(wait_time)
            now = time.time()
            time_elapsed = now - self.last_call
            if time_elapsed <= self.call_interval:
                time.sleep(self.call_interval - time_elapsed)
                self.last_call = time.time()
                return

            self.last_call = now
            return


class StudyResult(Enum):
    SUCCESS = 0
    FORBIDDEN = 1  # 403
    ERROR = 2
    TIMEOUT = 3
    NOT_VIDEO = 4
    MANUAL = 5

    def is_success(self):
        return self == StudyResult.SUCCESS
    def is_failure(self):
        return self != StudyResult.SUCCESS

class Chaoxing:
    VIDEO_CONFIRM_TIMEOUT = 180
    VIDEO_CONFIRM_INTERVAL = 30

    def __init__(self, account: Account = None, tiku: Tiku = None, **kwargs):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.kwargs = kwargs
        self.rollback_times = 0
        self.rate_limiter = RateLimiter(0.5) # 其他接口速率限制比较松
        self.video_log_limiter = RateLimiter(2) # 上报进度极其容易卡验证码，限制2s一次

    def login(self, login_with_cookies=False):
        if login_with_cookies:
            logger.info("Logging in with cookies")
            SessionManager.update_cookies()
            logger.debug("Cookie session loaded")
            if not self._validate_cookie_session():
                logger.warning("Cookie 登录校验失败，尝试使用账号密码重新登录")
                if self.account and self.account.username and self.account.password:
                    return self.login(login_with_cookies=False)
                return {"status": False, "msg": "cookies 已失效，请更新 cookies 或提供账号密码"}
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}

        _session = requests.Session()
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }
        logger.trace("正在尝试登录...")
        try:
            resp = _session.post(_url, headers=gc.HEADERS, data=_data, timeout=(5, 20))
            resp.raise_for_status()
            result = resp.json()
            if result.get("status") is True:
                save_cookies(_session)
                SessionManager.update_cookies()
                logger.info("登录成功...")
                return {"status": True, "msg": "登录成功"}
            return {"status": False, "msg": str(result.get("msg2", "Login failed"))}
        finally:
            _session.close()

    def logout(self):
        """模拟关掉页面：通知平台退出、丢弃本地会话。

        长休息（组间、午休、晚饭、夜间）时由拟人节奏调用。实测超星的注销接口不会
        让已发出去的 SSO cookie 立即失效，所以真正起作用的是"本地丢弃会话 + 休息
        期间一个请求都不发"，休息结束时重新登录，平台看到的就是一次新的登录。
        """
        session = SessionManager.get_session()
        try:
            # 不跟随跳转：注销接口会 302 到首页，跟随反而容易报错
            session.get("https://passport2.chaoxing.com/logout", timeout=(5, 15),
                        allow_redirects=False)
        except RequestException:
            logger.warning("注销请求未成功，改为只清理本地会话")
        try:
            SessionManager.clear_cookies()
        except Exception:
            logger.warning("本地会话清理失败，重新登录时会覆盖")
        logger.info("已退出登录，模拟用户离开")

    def resume_session(self, attempts=3):
        """休息结束后重新登录并用一次真实请求确认会话可用。"""
        for attempt in range(1, attempts + 1):
            try:
                outcome = self.login()
            except Exception:
                outcome = {"status": False}
            if outcome.get("status") and self._validate_cookie_session():
                logger.info("休息结束，重新登录成功")
                return True
            logger.warning("休息结束重新登录失败（第 {} 次）", attempt)
            if attempt < attempts:
                time.sleep(3 * attempt + random.uniform(0, 2))
        return False

    def bind_pacing_session(self):
        """让拟人节奏在长休息时注销会话、休息结束再重新登录。"""
        from api import pacing

        def _resume():
            if not self.resume_session():
                raise RuntimeError("休息结束后重新登录失败")

        pacing.current().bind_session(suspend=self.logout, resume=_resume)

    def _validate_cookie_session(self) -> bool:
        session = SessionManager.get_instance()._session
        if not session.cookies.get("_uid"):
            return False

        test_session = requests.Session()
        test_session.headers.update(gc.HEADERS)
        test_session.cookies.update(session.cookies.get_dict())

        try:
            resp = test_session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                timeout=8,
            )
        except RequestException as exc:
            logger.debug("Cookie validation request failed: {}", exc)
            return False

        if resp.status_code != 200:
            return False

        if "passport2.chaoxing.com" in resp.text or "login" in resp.text.lower():
            return False

        return True

    def get_fid(self):
        _session = SessionManager.get_session()
        return _session.cookies.get("fid")

    def get_uid(self):
        s = SessionManager.get_session()
        if "_uid" in s.cookies:
            return s.cookies["_uid"]
        if "UID" in s.cookies:
            return s.cookies["UID"]
        raise ValueError("Cannot get uid !")

    def get_course_list(self):
        _session = SessionManager.get_session()
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        logger.trace("正在读取所有的课程列表...")

        # 接口突然抽风, 增加headers
        # 有可能只是referer的问题
        _headers = {
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
        }

        def checked(response):
            response.raise_for_status()
            if "passport2.chaoxing.com" in str(response.url):
                raise MaxRetryExceeded("Course session expired; please sign in again")
            return response

        _resp = checked(_session.post(_url, headers=_headers, data=_data))
        # logger.trace(f"原始课程列表内容:\n{_resp.text}")
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(_resp.text)

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = checked(_session.get(_interaction_url))
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0,
            }
            _resp = checked(_session.post(_url, data=_data))
            course_list += decode_course_list(_resp.text)
        return course_list

    def get_course_point(self, _courseid, _clazzid, _cpi):
        _session = SessionManager.get_session()
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)
        _resp.raise_for_status()
        if "passport2.chaoxing.com" in str(_resp.url):
            raise MaxRetryExceeded("Course session expired; please sign in again")
        # logger.trace(f"原始章节列表内容:\n{_resp.text}")
        outline = decode_course_point(_resp.text)
        if not outline["points"]:
            raise MaxRetryExceeded("Course outline contained no recognizable chapters")
        logger.info("课程章节读取成功...")
        return outline

    def get_job_list(self, course: dict, point: dict, *, visit_empty=True) -> tuple[list[dict], dict]:
        _session = SessionManager.get_session()
        self.rate_limiter.limit_rate()
        job_list = []
        job_info = {}
        untracked_videos = []
        unresolved_tasks = []
        book_tasks = []
        task_status = {}
        card_states = []
        cards_params = {
            "clazzid": course["clazzId"],
            "courseid": course["courseId"],
            "knowledgeid": point["id"],
            "ut": "s",
            "cpi": course["cpi"],
            "v": "2025-0424-1038-3",
            "mooc2": 1
        }

        count = self._chapter_card_count(_session, course, point)
        for number in range(count or 7):
            _possible_num = str(number)

            logger.trace("开始读取章节所有任务点...")

            cards_params.update({"num": _possible_num})
            _resp = _session.get("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards", params=cards_params)
            if _resp.status_code != 200:
                raise MaxRetryExceeded(f"Task card {_possible_num} returned HTTP {_resp.status_code}")
            if "passport2.chaoxing.com" in str(_resp.url):
                raise MaxRetryExceeded("Course session expired; please sign in again")

            _job_list, _job_info = decode_course_card(_resp.text)
            if _job_info.get("notOpen", False):
                # 直接返回, 节省一次请求
                logger.info("该章节未开放")
                return [], _job_info
            if _job_info.get("card_state") == "unrecognized":
                logger.warning(
                    "任务卡片未识别，不能当作空章节：chapter={}, card={}",
                    point["id"], _possible_num,
                )
                raise MaxRetryExceeded(f"Unrecognized task card {_possible_num}")
            card_states.append(_job_info.get("card_state", "unrecognized"))

            for job in _job_list:
                job["_card_num"] = _possible_num
                job["_job_info"] = {
                    **_job_info, "knowledgeid": _job_info.get("knowledgeid") or point["id"],
                }
            job_list += _job_list
            task_status.update(_job_info.get("task_status", {}))
            untracked_videos.extend(_job_info.pop("untracked_videos", []))
            unresolved_tasks.extend(
                {**task, "card_num": _possible_num}
                for task in _job_info.pop("unresolved_tasks", [])
            )
            book_tasks.extend(
                {**task, "card_num": _possible_num}
                for task in _job_info.pop("book_tasks", [])
            )
            job_info.update(_job_info)

        if untracked_videos:
            job_info["untracked_videos"] = list(dict.fromkeys(untracked_videos))
        if unresolved_tasks:
            job_info["unresolved_tasks"] = unresolved_tasks
        if book_tasks:
            job_info["book_tasks"] = book_tasks
        job_info["card_count"] = len(card_states)
        job_info["card_count_known"] = count is not None
        job_info["task_status"] = task_status
        job_info["empty"] = bool(card_states) and all(state == "empty" for state in card_states)
        job_info["card_state"] = (
            "empty" if job_info["empty"] else "tasks" if "tasks" in card_states else "no_tasks"
        )
        if job_info["empty"]:
            logger.info("识别到平台“暂无内容”占位页：{}，已检查{}张卡片", point["title"], len(card_states))
        if job_info["empty"] and visit_empty and not (
            has_explicit_task_count(point) and task_counts(point)[2] > 0
        ):
            job_info["empty_visit_succeeded"] = self.study_emptypage(course, point) == StudyResult.SUCCESS
        # logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        logger.info(
            "章节任务点读取成功: chapter={}, cards={}, count_known={}, runnable={}, unsupported={}, books={}",
            point["id"], len(card_states), count is not None, len(job_list),
            len(unresolved_tasks), len(book_tasks),
        )

        return job_list, job_info

    def _chapter_card_count(self, session, course, point):
        try:
            response = session.get(
                "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
                params={
                    "courseId": course["courseId"], "clazzid": course["clazzId"],
                    "chapterId": point["id"], "cpi": course["cpi"], "mooc2": 1,
                },
            )
        except RequestException as exc:
            logger.warning("章节卡片数量读取失败，使用兼容扫描: {}", type(exc).__name__)
            return None
        if "passport2.chaoxing.com" in str(response.url):
            raise MaxRetryExceeded("Course session expired; please sign in again")
        if response.status_code == 200:
            count = decode_course_card_count(response.text)
            if count is not None:
                return count
        logger.warning("未取得章节卡片数量，使用兼容扫描: chapter={}", point["id"])
        return None

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]".encode()
        ).hexdigest()

    def video_progress_log(
            self,
            _session,
            _course,
            _job,
            _job_info,
            _dtoken,
            _duration,
            _playingTime,
            _type: str = "Video",
            headers: Optional[dict] = None,
    ) -> tuple[bool, int]:

        if headers is None:
            logger.warning("null headers")
            headers = gc.VIDEO_HEADERS

        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        if "courseId" in _job["otherinfo"]:
            logger.error(_job["otherinfo"])
            raise RuntimeError("this is not possible")

        enc = self.get_enc(_course["clazzId"], _job["jobid"], _job["objectid"], _playingTime, _duration, self.get_uid())
        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": _job["objectid"],
            "otherInfo": _job["otherinfo"],
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": self.get_uid(),
            "isdrag": "3",
            "view": "pc",
            "enc": enc,
            "dtype": _type
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/"
            f"{_dtoken}"
        )


        face_capture_enc = _job["videoFaceCaptureEnc"]
        att_duration = _job["attDuration"]
        att_duration_enc = _job["attDurationEnc"]

        if face_capture_enc:
            params["videoFaceCaptureEnc"] = face_capture_enc
        if att_duration:
            params["attDuration"] = att_duration
        if att_duration_enc:
            params["attDurationEnc"] = att_duration_enc

        rt = _job['rt']
        if not rt:
            rt_search = re.search(r"-rt_([1d])", _job['otherinfo'])
            if rt_search:
                rt_char = rt_search.group(1)
                rt = "0.9" if rt_char == "d" else "1"
                logger.trace(f"Got rt from otherinfo: {rt}")

        candidates = []
        if rt:
            candidates.append(str(rt))
        else:
            logger.warning("Failed to get rt")
        for fallback in ("0.9", "1"):
            if fallback not in candidates:
                candidates.append(fallback)
        resp = None
        for candidate in candidates:
            params.update({"rt": candidate, "_t": get_timestamp()})
            try:
                with video_guard.report_gate(f"report:{_course['courseId']}"):
                    resp = _session.get(_url, params=params, headers=headers)
            except RequestException as exc:
                logger.warning(
                    "媒体上报网络异常: job={}, type={}, position={}/{}, error={}",
                    _job.get("jobid"), _type, _playingTime, _duration, type(exc).__name__,
                )
                return False, 0
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = None
                if not isinstance(payload, dict) or "isPassed" not in payload:
                    # 常见成因是 dtoken 过期：URL 落到了别的页面，回来的不是 JSON。
                    # 换 rt 没用，得换 dtoken，所以单独给一个状态让上层刷新会话。
                    logger.warning(
                        "媒体上报响应缺少完成状态: job={}, http=200, body={}",
                        _job.get("jobid"), str(resp.text or "")[:120].replace("\n", " "),
                    )
                    return False, 426
                passed = is_task_passed(payload["isPassed"])
                logger.debug(
                    "媒体上报: job={}, type={}, position={}/{}, rt={}, http=200, isPassed={}",
                    _job.get("jobid"), _type, _playingTime, _duration, candidate, passed,
                )
                if _playingTime >= _duration:
                    logger.info(
                        "片尾上报响应: job={}, type={}, position={}/{}, http=200, isPassed={}",
                        _job.get("jobid"), _type, _playingTime, _duration, passed,
                    )
                return passed, 200
            if resp.status_code == 403:
                logger.warning("出现403报错, 正常尝试切换rt")
                continue
            logger.warning("媒体上报失败: job={}, http={}", _job.get("jobid"), resp.status_code)
            break

        if resp.status_code == 403:
            logger.warning(
                "媒体上报被拒绝，交由会话恢复处理: job={}, type={}, position={}/{}",
                _job.get("jobid"), _type, _playingTime, _duration,
            )
            return False, 403

        return False, resp.status_code


    def _refresh_video_status(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]) -> Optional[dict]:
        self.rate_limiter.limit_rate(random_time=True, random_max=0.2)
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info_url = (
            f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?"
            f"k={self.get_fid()}&flag=normal"
        )
        try:
            resp = session.get(info_url, timeout=8, headers=headers)
        except RequestException as exc:
            logger.debug("刷新视频状态失败: {}", type(exc).__name__)
            return None

        if resp.status_code != 200:
            logger.debug("刷新视频状态返回码异常: {}"% resp.status_code)
            return None

        try:
            data = resp.json()
        except ValueError as exc:
            logger.debug("解析视频状态响应失败: {}", exc)
            return None

        if isinstance(data, dict) and data.get("status") == "success":
            return data

        return None

    def _recover_after_forbidden(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]):
        SessionManager.update_cookies()
        refreshed = self._refresh_video_status(session, job, _type)
        if refreshed:
            return refreshed

        # FIXME: Temporarily disabled for multithreading support
        if False and self.account and self.account.username and self.account.password:
            login_result = self.login(login_with_cookies=False)
            if login_result.get("status"):
                SessionManager.update_cookies()
                return self._refresh_video_status(session, job, _type)
            logger.warning("账号密码登录失败: {}", login_result.get("msg"))

        return None


    def _read_cards(self, course, job, job_info):
        """回读当前章节的任务点卡片；读不到返回 (None, None)。"""
        knowledge_id = job_info.get("knowledgeid") or job_info.get("_progress_chapter_id")
        if not knowledge_id:
            return None, None
        try:
            self.rate_limiter.limit_rate()
            response = SessionManager.get_session().get(
                "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards",
                params={
                    "clazzid": course["clazzId"], "courseid": course["courseId"],
                    "knowledgeid": knowledge_id, "cpi": course["cpi"],
                    "num": job.get("_card_num", "0"), "ut": "s", "mooc2": 1,
                    "v": "2025-0424-1038-3",
                },
                timeout=10,
            )
            if response.status_code != 200 or "passport2.chaoxing.com" in str(response.url):
                return None, None
            jobs, info = decode_course_card(response.text)
            return jobs, info
        except (RequestException, ValueError) as exc:
            logger.debug("任务卡片回读失败: {}", type(exc).__name__)
            return None, None

    def _video_card_passed(self, course, job, job_info):
        """A missing card is not a completion acknowledgement."""
        jobs, info = self._read_cards(course, job, job_info)
        if info is None:
            return False
        return info.get("task_status", {}).get(str(job["jobid"])) is True

    def _read_card_play_time(self, course, job, job_info):
        """平台记录的观看位置（秒）；读不到返回 None，不影响播放。"""
        jobs, _ = self._read_cards(course, job, job_info)
        for item in jobs or []:
            if str(item.get("jobid")) == str(job.get("jobid")):
                milliseconds = parse_video_play_time(item.get("playTime"))
                if milliseconds is None:
                    return None
                return milliseconds // 1000
        return None

    def study_video(self, _course, _job, _job_info, _speed: float = 1.0, _type: Literal["Video", "Audio"] = "Video") -> StudyResult:
        _session = SessionManager.get_session()
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info = self._refresh_video_status(_session, _job, _type)
        if not info:
            logger.error("无法读取媒体状态: job={}", _job.get("jobid"))
            return StudyResult.ERROR
        kind = str(info.get("type", "")).lower()
        filename = str(info.get("filename") or _job.get("name", "")).lower()
        if _type == "Video" and (
            kind == "audio" or kind.startswith("audio/")
            or filename.endswith((".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"))
        ):
            return StudyResult.NOT_VIDEO
        try:
            duration = int(info["duration"])
            token = info["dtoken"]
            resume = max(0, min(duration, int(_job.get("playTime") or 0) // 1000))
            interval = max(15, min(300, int(_job_info.get("reportTimeInterval") or 60)))
        except (KeyError, TypeError, ValueError):
            logger.error("媒体状态字段无效: job={}", _job.get("jobid"))
            return StudyResult.ERROR
        if duration <= 0 or not token:
            logger.error("媒体时长或令牌无效: job={}", _job.get("jobid"))
            return StudyResult.ERROR

        play_time = float(resume)
        last_tick = next_report = time.monotonic()
        confirming_since = None
        refreshed_confirmation = False
        forbidden_retry = replays = transient_retry = stale_token_retry = 0
        confirm_interval = min(interval, self.VIDEO_CONFIRM_INTERVAL)
        guard_key = f"{_course['courseId']}:{_job.get('objectid', '')}"
        video_guard.note(guard_key, resume)
        requested_speed = _speed
        _speed = video_guard.clamp_speed(_speed, default=1.0)
        if _speed != round(float(requested_speed or 1.0), 2):
            logger.warning("播放倍速被看护上限收敛: 请求{} -> 实际{}", requested_speed, _speed)
        recheck_every = video_guard.settings()["recheck"]
        reports_since_check = 0
        context = {
            "course_id": _course["courseId"], "course": _course.get("title", ""),
            "chapter_id": _job_info.get("_progress_chapter_id") or _job_info.get("knowledgeid", ""),
            "chapter": _job_info.get("_progress_chapter", ""),
            "phase": "video", "name": _job.get("name", ""), "job_id": _job.get("jobid", ""),
        }

        def progress(state, error=""):
            report(
                "video", **context, position=int(play_time), duration=duration,
                video_state=state, video_error=error, replay_count=replays,
            )

        video_guard.wait_for_window(
            on_wait=lambda message: progress("waiting", message),
        )

        logger.info("开始任务: {}, 总时长: {}s, 已进行: {}s", _job["name"], duration, resume)
        with tqdm(total=duration, initial=resume, desc=_job["name"],
                  unit_scale=True, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}',
                  disable=os.environ.get("CHAOXING_HEADLESS") == "1") as pbar:
            while True:
                now = time.monotonic()
                play_time = min(duration, play_time + max(0, now - last_tick) * _speed)
                last_tick = now
                at_end = play_time >= duration
                progress("confirming" if at_end else "replaying" if replays else "playing")
                pbar.n = int(play_time)
                pbar.refresh()

                if now >= next_report or (at_end and confirming_since is None):
                    passed, state = self.video_progress_log(
                        _session, _course, _job, _job_info, token, duration,
                        int(play_time), _type, headers=headers,
                    )
                    now = time.monotonic()
                    if passed:
                        progress("passed")
                        logger.info("平台确认任务完成: {}", _job["name"])
                        return StudyResult.SUCCESS
                    if state == 403:
                        if forbidden_retry >= 2:
                            progress("error", "平台拒绝进度上报，会话恢复后仍未通过")
                            return StudyResult.FORBIDDEN
                        forbidden_retry += 1
                        progress("retrying", "正在刷新媒体会话")
                        logger.warning("媒体上报403，刷新状态（第{}次）: {}", forbidden_retry, _job["name"])
                        time.sleep(random.uniform(2, 4))
                        refreshed = self._recover_after_forbidden(_session, _job, _type)
                        if not refreshed:
                            progress("error", "媒体会话刷新失败")
                            return StudyResult.FORBIDDEN
                        token = refreshed.get("dtoken") or token
                        last_tick = next_report = time.monotonic()
                        continue
                    if state == 426:
                        # 回来的不是 JSON：多半是 dtoken 过期，URL 落到了别的页面。
                        # 换 rt 无效，必须重新取一次播放状态换 dtoken，然后接着报。
                        if stale_token_retry >= 2:
                            progress("error", "媒体会话反复失效，暂时放弃这个任务点")
                            logger.warning(
                                "媒体上报响应持续异常，保留未完成: job={}, name={}",
                                _job.get("jobid"), _job["name"],
                            )
                            return StudyResult.ERROR
                        stale_token_retry += 1
                        progress("retrying", "正在刷新媒体会话")
                        logger.warning(
                            "媒体上报响应异常，刷新播放状态（第{}次）: {}", stale_token_retry, _job["name"],
                        )
                        time.sleep(random.uniform(3, 6))
                        refreshed = self._refresh_video_status(_session, _job, _type)
                        if refreshed:
                            token = refreshed.get("dtoken") or token
                            transient_retry = 0
                        last_tick = next_report = time.monotonic()
                        continue
                    if state in {0, 408, 429, 500, 502, 503, 504}:
                        if at_end and self._video_card_passed(_course, _job, _job_info):
                            progress("passed")
                            logger.info("上报异常后任务卡片回读确认完成: {}", _job["name"])
                            return StudyResult.SUCCESS
                        if transient_retry < 3:
                            transient_retry += 1
                            progress("retrying", f"进度上报暂时失败，正在重试 {transient_retry}/3")
                            logger.warning(
                                "媒体上报短暂失败，保留播放位置重试: job={}, http={}, retry={}/3",
                                _job.get("jobid"), state, transient_retry,
                            )
                            time.sleep(min(10, 2 ** transient_retry))
                            last_tick = next_report = time.monotonic()
                            continue
                    if state != 200:
                        progress("error", f"进度上报失败，HTTP {state or '网络异常'}")
                        return StudyResult.ERROR
                    transient_retry = 0
                    video_guard.note(guard_key, int(play_time))
                    reports_since_check += 1
                    if (recheck_every and reports_since_check >= recheck_every and not at_end):
                        reports_since_check = 0
                        server_position = self._read_card_play_time(_course, _job, _job_info)
                        # 卡片里的 playTime 是上一次上报落库的值：刚报完就回读，差值天然
                        # 等于一个上报周期。把这段滞后算进容差，避免误判成平台回退。
                        finding = video_guard.inspect(
                            guard_key, server_position, lag=int(max(0.0, interval * _speed)),
                        )
                        if finding is not None:
                            # 不因单次异常读数倒回播放位置：紧接着再独立读取一次卡片，
                            # 只有同一低位仍成立时才提交回退、告警并对齐。
                            confirmation_position = self._read_card_play_time(
                                _course, _job, _job_info,
                            )
                            finding = video_guard.confirm(
                                guard_key, finding, confirmation_position,
                            )
                        if finding is not None:
                            action = "pause" if video_guard.settings()["auto_pause"] else "slow"
                            report(
                                "video_rollback", **context,
                                rollback_from=finding["high"], rollback_to=finding["server"],
                                rollback_dropped=finding["dropped"], rollback_count=finding["count"],
                                rollback_action=action,
                            )
                            logger.warning(
                                "平台进度回退: job={}, {}s -> {}s (-{}s), 第{}次, 处理={}",
                                _job.get("jobid"), finding["high"], finding["server"],
                                finding["dropped"], finding["count"], action,
                            )
                            if finding["count"] > video_guard.MAX_ROLLBACKS:
                                progress("unconfirmed", "平台连续回退播放进度，已停止这个任务点")
                                logger.warning("回退次数超过上限，保留未完成: {}", _job["name"])
                                return StudyResult.TIMEOUT
                            play_time = float(max(0, finding["server"]))
                            confirming_since = None
                            last_tick = next_report = time.monotonic()
                            if action == "pause":
                                seconds = video_guard.pause_seconds()
                                progress("rolled_back", f"平台进度回退，先停 {int(seconds // 60)} 分钟")
                                waited = 0.0
                                while waited < seconds:
                                    slice_seconds = min(60.0, seconds - waited)
                                    time.sleep(slice_seconds)
                                    waited += slice_seconds
                                    last_tick = next_report = time.monotonic()
                                progress("replaying", "回退暂停结束，从平台记录的位置重新看")
                            else:
                                _speed = min(_speed, 1.0)
                                progress("rolled_back", "平台进度回退，已降到 1 倍速从平台位置继续")
                            continue
                    if at_end:
                        if confirming_since is None:
                            confirming_since = now
                            logger.info(
                                "已到片尾，等待平台确认: job={}, name={}, 最长等待{}s",
                                _job.get("jobid"), _job["name"], self.VIDEO_CONFIRM_TIMEOUT,
                            )
                        if self._video_card_passed(_course, _job, _job_info):
                            progress("passed")
                            logger.info("任务卡片回读确认完成: {}", _job["name"])
                            return StudyResult.SUCCESS
                        elapsed = now - confirming_since
                        logger.info(
                            "片尾确认未通过: job={}, position={}/{}, 已等待{}s, 重播次数={}",
                            _job.get("jobid"), int(play_time), duration, int(elapsed), replays,
                        )
                        if elapsed >= confirm_interval and not refreshed_confirmation:
                            refreshed = self._refresh_video_status(_session, _job, _type)
                            if refreshed:
                                token = refreshed.get("dtoken") or token
                            refreshed_confirmation = True
                        if elapsed >= self.VIDEO_CONFIRM_TIMEOUT:
                            if resume > 0 and replays == 0:
                                # An old endpoint report can leave a full-position bookmark
                                # without the watch time required by the platform.
                                replays += 1
                                play_time = 0
                                confirming_since = None
                                refreshed_confirmation = False
                                last_tick = next_report = time.monotonic()
                                progress("replaying", "原断点未通过确认，正在从头补看一次")
                                logger.warning("片尾断点未确认，从头补看一次: {}", _job["name"])
                                continue
                            progress("unconfirmed", "播放已结束，但平台仍未确认任务点通过")
                            logger.warning("媒体仍未通过平台确认，保留未完成: {}", _job["name"])
                            return StudyResult.TIMEOUT
                        next_report = now + confirm_interval
                    else:
                        next_report = now + interval
                time.sleep(max(0.1, min(1, gc.THRESHOLD)))

    def study_document(self, _course, _job) -> StudyResult:
        """
        Study a document in Chaoxing platform.

        This method makes a GET request to fetch document information for a given course and job.

        Args:
            _course (dict): Dictionary containing course information with keys:
                - courseId: ID of the course
                - clazzId: ID of the class
            _job (dict): Dictionary containing job information with keys:
                - jobid: ID of the job
                - otherinfo: String containing node information
                - jtoken: Authentication token for the job

        Returns:
            requests.Response: Response object from the GET request

        Note:
            This method requires the following helper functions:
            - init_session(): To initialize a new session
            - get_timestamp(): To get current timestamp
            - re module for regular expression matching
        """
        _session = SessionManager.get_session()
        _url = f"https://mooc1.chaoxing.com/ananas/job/document?jobid={_job['jobid']}&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}&courseid={_course['courseId']}&clazzid={_course['clazzId']}&jtoken={_job['jtoken']}&_dc={get_timestamp()}"
        _resp = _session.get(_url)
        if _resp.status_code != 200:
            return StudyResult.ERROR
        else:
            return StudyResult.SUCCESS

    def study_work(self, _course, _job, _job_info) -> StudyResult:
        # FIXME: 这一块可以单独搞一个类出来了，方法里面又套方法，每一次调用都会创建新的方法，十分浪费
        if not self.tiku or self.tiku.DISABLE:
            return StudyResult.SUCCESS
        _ORIGIN_HTML_CONTENT = ""  # 用于配合输出网页源码, 帮助修复#391错误

        def random_answer(options: str) -> str:
            answer = ""
            if not options:
                return answer

            if q["type"] == "multiple":
                logger.debug(f"当前选项列表[cut前] -> {options}")
                _op_list = multi_cut(options)
                logger.debug(f"当前选项列表[cut后] -> {_op_list}")

                if not _op_list:
                    logger.error(
                        "选项为空, 未能正确提取题目选项信息! 请反馈并提供以上信息"
                    )
                    return answer

                available_options = len(_op_list)
                select_count = 0

                # 根据可用选项数量调整可能选择的选项数
                if available_options <= 1:
                    select_count = available_options
                else:
                    max_possible = min(4, available_options)
                    min_possible = min(2, available_options)

                    weights_map = {
                        2: [1.0],
                        3: [0.3, 0.7],
                        4: [0.1, 0.5, 0.4],
                        5: [0.1, 0.4, 0.3, 0.2],
                    }

                    weights = weights_map.get(max_possible, [0.3, 0.4, 0.3])
                    possible_counts = list(range(min_possible, max_possible + 1))

                    weights = weights[:len(possible_counts)]

                    weights_sum = sum(weights)
                    if weights_sum > 0:
                        weights = [w / weights_sum for w in weights]

                    select_count = random.choices(possible_counts, weights=weights, k=1)[0]

                selected_options = random.sample(_op_list, select_count) if select_count > 0 else []

                for option in selected_options:
                    answer += option[:1]  # 取首字为答案，例如A或B

                answer = "".join(sorted(answer))
            elif q["type"] == "single":
                answer = random.choice(options.split("\n"))[:1]  # 取首字为答案, 例如A或B
            # 判断题处理
            elif q["type"] == "judgement":
                # answer = self.tiku.jugement_select(_answer)
                answer = "true" if random.choice([True, False]) else "false"
            logger.info(f"随机选择 -> {answer}")
            return answer

        def multi_cut(answer: str):
            """
            将多选题答案字符串按特定字符进行切割, 并返回切割后的答案列表

            参数:
            answer(str): 多选题答案字符串.

            返回:
            list[str]: 切割后的答案列表,如果无法切割, 则返回默认的选项列表None

            注意:
            如果无法从网页中提取题目信息,将记录警告日志并返回None
            """
            # cut_char = [',','，','|','\n','\r','\t','#','*','-','_','+','@','~','/','\\','.','&',' ']    # 多选答案切割符
            # ',' 在常规被正确划分的, 选项中出现, 导致 multi_cut 无法正确划分选项 #391
            # IndexError: Cannot choose from an empty sequence #391
            # 同时为了避免没有考虑到的 case, 应该先按照 '\n' 匹配, 匹配不到再按照其他字符匹配
            cut_char = [
                "\n",
                ",",
                "，",
                "|",
                "\r",
                "\t",
                "#",
                "*",
                "-",
                "_",
                "+",
                "@",
                "~",
                "/",
                "\\",
                ".",
                "&",
                " ",
                "、",
            ]  # 多选答案切割符
            res = cut(answer)
            if res is None:
                logger.warning(
                    f"未能从网页中提取题目信息, 以下为相关信息：\n\t{answer}\n\n{_ORIGIN_HTML_CONTENT}\n"
                )  # 尝试输出网页内容和选项信息
                logger.warning("未能正确提取题目选项信息! 请反馈并提供以上信息")
                return None
            else:
                return res

        def clean_res(res):
            cleaned_res = []
            if isinstance(res, str):
                res = [res]
            for c in res:
                cleaned = re.sub(r'^[A-Za-z]|[.,!?;:，。！？；：]', '', c)
                cleaned_res.append(cleaned.strip())

            return cleaned_res

        def is_subsequence(a, o):
            iter_o = iter(o)
            return all(c in iter_o for c in a)

        # FIXME: Use tenacity for retrying
        def with_retry(max_retries=3, delay=1):
            def decorator(func):
                def wrapper(*args, **kwargs):
                    retries = 0
                    while retries < max_retries:
                        try:
                            _resp = func(*args, **kwargs)

                            # 未创建完成该测验则不进行答题，目前遇到的情况是未创建完成等同于没题目
                            if '教师未创建完成该测验' in _resp.text:
                                raise PermissionError("教师未创建完成该测验")

                            questions = decode_questions_info(_resp.text)

                            if _resp.status_code == 200 and questions.get("questions"):
                                return (_resp, questions)

                            logger.warning(
                                f"无效响应 (Code: {getattr(_resp, 'status_code', 'Unknown')}), 重试中... ({retries + 1}/{max_retries})")

                        except requests.exceptions.RequestException as e:
                            logger.warning(f"请求失败: {str(e)[:50]}, 重试中... ({retries + 1}/{max_retries})")
                        retries += 1
                        time.sleep(delay * (2 ** retries))
                    raise MaxRetryExceeded(f"超过最大重试次数 ({max_retries})")

                return wrapper

            return decorator

        # 学习通这里根据参数差异能重定向至两个不同接口, 需要定向至https://mooc1.chaoxing.com/mooc-ans/workHandle/handle
        _session = SessionManager.get_session()

        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"

        @with_retry(max_retries=3, delay=1)
        def fetch_response():
            return _session.get(
                _url,
                params={
                    "api": "1",
                    "workId": _job["jobid"].replace("work-", ""),
                    "jobid": _job["jobid"],
                    "originJobId": _job["jobid"],
                    "needRedirect": "true",
                    "skipHeader": "true",
                    "knowledgeid": str(_job_info["knowledgeid"]),
                    "ktoken": _job_info["ktoken"],
                    "cpi": _job_info["cpi"],
                    "ut": "s",
                    "clazzId": _course["clazzId"],
                    "type": "",
                    "enc": _job["enc"],
                    "mooc2": "1",
                    "courseid": _course["courseId"],
                }
            )

        final_resp = {}
        questions = {}

        try:
            final_resp, questions = fetch_response()
        except Exception as e:
            logger.error(f"请求失败: {e}")
            return StudyResult.ERROR

        _ORIGIN_HTML_CONTENT = final_resp.text  # 用于配合输出网页源码, 帮助修复#391错误

        uploads = [q for q in questions["questions"] if q.get("manual_reason") == "FILE_UPLOAD_REQUIRED"]
        if uploads:
            logger.warning(
                "测验要求人工上传文件，不调用AI、不保存或提交空答案: job={}, questions={}",
                _job["jobid"], ",".join(str(q["id"]) for q in uploads),
            )
            report(
                "quiz", phase="workid", quiz_state="error", quiz_answer="",
                name="需要人工上传文件", ai_error="此测验要求上传文件，需要人工完成",
            )
            return StudyResult.MANUAL

        # 搜题
        total_questions = len(questions["questions"])
        found_answers = 0
        skipped_images = 0
        for q in questions["questions"]:
            logger.debug("当前题目信息 -> id={} type={} title={} options={} images={}",
                         q["id"], q["type"], q["title"][:500], q["options"][:1000],
                         len(q.get("images", [])))
            report("quiz", phase="workid", quiz_state="querying", quiz_answer="")
            # 拟人节奏：每题前随机停顿（视频刚看完到这道题也算在内）；query_delay 作为下限保留
            query_delay = self.kwargs.get("query_delay", 0)
            from api import pacing
            pacing.current().before_answer()
            if query_delay:
                time.sleep(float(query_delay))
            asked = dict(q)
            if isinstance(getattr(final_resp, "url", None), str):
                asked["_image_base_url"] = final_resp.url
            course_id = str(_course.get("courseId", "")) if isinstance(_course, dict) else ""
            if re.fullmatch(r"[0-9]{1,20}", course_id):
                asked["course_id"] = course_id
            res = self.tiku.query(asked)
            answer = ""
            if getattr(self.tiku, "STRICT_ANSWERS", False):
                answer = self.tiku.to_platform_answer(q, res) if res else ""
                if not answer and isinstance(
                        getattr(self.tiku, "last_error", None), QuestionImageUnavailable):
                    # 平台那张题图取不回来（404/502/不是图片），这一题没得读。留空跳过，
                    # 其余照常作答提交，别为一张坏图把整章测验永久卡死。
                    logger.warning(
                        "题图无法下载，跳过这一题并留空：{} -> {}",
                        str(q.get("title", ""))[:80], str(getattr(self.tiku, "last_error", ""))[:120],
                    )
                    report("quiz", quiz_state="skipped", quiz_answer="")
                    q["answerField"][f'answer{q["id"]}'] = ""
                    q[f'answerSource{q["id"]}'] = "missing"
                    skipped_images += 1
                    continue
                if not answer:
                    logger.error("WebAI 未取得可校验的答案，本次测验不保存、不提交，也不随机填答")
                    report("quiz", quiz_state="error", quiz_answer="")
                    return StudyResult.ERROR
                q[f'answerSource{q["id"]}'] = "cover"
                found_answers += 1
            elif not res:
                # 随机答题
                answer = random_answer(q["options"])
                q[f'answerSource{q["id"]}'] = "random"
            else:
                # 根据响应结果选择答案
                if q["type"] == "multiple":
                    # 多选处理
                    options_list = multi_cut(q["options"])
                    res_list = multi_cut(res)
                    if res_list is not None and options_list is not None:
                        for _a in clean_res(res_list):
                            for o in options_list:
                                if (
                                        is_subsequence(_a, o)  # 去掉各种符号和前面ABCD的答案应当是选项的子序列
                                ):
                                    answer += o[:1]
                        # 对答案进行排序, 否则会提交失败
                        answer = "".join(sorted(answer))
                    # else 如果分割失败那么就直接到下面去随机选
                elif q["type"] == "single":
                    # 单选也进行切割，主要是防止返回的答案有异常字符
                    options_list = multi_cut(q["options"])
                    if options_list is not None:
                        t_res = clean_res(res)
                        for o in options_list:
                            if is_subsequence(t_res[0], o):
                                answer = o[:1]
                                break
                elif q["type"] == "judgement":
                    answer = "true" if self.tiku.judgement_select(res) else "false"
                elif q["type"] == "completion":
                    if isinstance(res, list):
                        answer = "".join(answer)
                    elif isinstance(res, str):
                        answer = res
                else:
                    # 其他类型直接使用答案 （目前仅知有简答题，待补充处理）
                    answer = res

                if not answer:  # 检查 answer 是否为空
                    logger.warning(f"找到答案但答案未能匹配 -> {res}\t随机选择答案")
                    answer = random_answer(q["options"])  # 如果为空，则随机选择答案
                    q[f'answerSource{q["id"]}'] = "random"
                else:
                    logger.info(f"成功获取到答案：{answer}")
                    q[f'answerSource{q["id"]}'] = "cover"
                    found_answers += 1
            # 填充答案
            q["answerField"][f'answer{q["id"]}'] = answer
            logger.info(f'{q["title"]} 填写答案为 {answer}')
            report("quiz", quiz_state="answered", quiz_answer=str(answer)[:200])
        if getattr(self.tiku, "PREVIEW_ONLY", False):
            logger.info("WebAI 预览模式：答案仅保存在本地缓存，未向学习通保存或提交")
            report("quiz", quiz_state="preview")
            return StudyResult.SUCCESS
        # 覆盖率：故意留空的坏图题也算"处理过"，否则一张坏图就永远交不了卷
        covered = found_answers + skipped_images
        cover_rate = (covered / total_questions) * 100
        if skipped_images:
            logger.info(f"本次有 {skipped_images} 道题因题图无法下载而留空，其余照常提交")
        logger.info(f"章节检测题库覆盖率： {cover_rate:.0f}%")
        # 提交模式  现在与题库绑定,留空直接提交, 1保存但不提交
        if self.tiku.get_submit_params() == "1":
            questions["pyFlag"] = "1"
        elif cover_rate >= self.tiku.COVER_RATE * 100 or self.rollback_times >= 1:
            questions["pyFlag"] = ""
        else:
            questions["pyFlag"] = "1"
            logger.info(f"章节检测题库覆盖率低于{self.tiku.COVER_RATE * 100:.0f}%，不予提交")
        # 组建提交表单
        if questions["pyFlag"] == "1":
            for q in questions["questions"]:
                questions.update(
                    {
                        f'answer{q["id"]}':
                            q["answerField"][f'answer{q["id"]}'] if q[f'answerSource{q["id"]}'] == "cover" else '',
                        f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                    }
                )
        else:
            for q in questions["questions"]:
                questions.update(
                    {
                        f'answer{q["id"]}': q["answerField"][f'answer{q["id"]}'],
                        f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                    }
                )

        del questions["questions"]

        # 答完题先复核一眼再交卷
        from api import pacing
        pacing.current().pause("answer", reason="检查一遍再交卷")
        report("quiz", quiz_state="submitting" if questions["pyFlag"] == "" else "saving")
        res = _session.post(
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            data=questions,
            headers={
                "Host": "mooc1.chaoxing.com",
                "sec-ch-ua-platform": '"Windows"',
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": "https://mooc1.chaoxing.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                # "Referer": "https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId=246831735&workAnswerId=52680423&workId=37778125&api=1&knowledgeid=913820156&classId=107515845&oldWorkId=07647c38d8de4c648a9277c5bed7075a&jobid=work-07647c38d8de4c648a9277c5bed7075a&type=&isphone=false&submit=false&enc=1d826aab06d44a1198fc983ed3d243b1&cpi=338350298&mooc2=1&skipHeader=true&originJobId=work-07647c38d8de4c648a9277c5bed7075a",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
            },
        )
        if res.status_code == 200:
            try:
                res_json = res.json()
            except ValueError:
                res_json = {}
            if isinstance(res_json, dict) and is_task_passed(res_json.get("status")):
                logger.info(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题成功 -> {res_json.get("msg", "ok")}')
                report("quiz", quiz_state="submitted" if questions["pyFlag"] == "" else "saved")
            else:
                logger.error("测验提交或保存未被平台接受: job={}", _job.get("jobid"))
                report("quiz", quiz_state="error")
                return StudyResult.ERROR
        else:
            logger.error("测验提交或保存失败: job={}, http={}", _job.get("jobid"), res.status_code)
            report("quiz", quiz_state="error")
            return StudyResult.ERROR
        return StudyResult.SUCCESS

    def study_read(self, _course, _job, _job_info) -> StudyResult:
        """
        阅读任务学习, 仅完成任务点, 并不增长时长
        """
        _session = SessionManager.get_session()
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                "jobid": _job["jobid"],
                "knowledgeid": _job_info["knowledgeid"],
                "jtoken": _job["jtoken"],
                "courseid": _course["courseId"],
                "clazzid": _course["clazzId"],
            },
        )
        if _resp.status_code != 200:
            logger.error(f"阅读任务学习失败 -> [{_resp.status_code}]{_resp.text}")
            return StudyResult.ERROR
        else:
            _resp_json = _resp.json()
            logger.info(f"阅读任务学习 -> {_resp_json['msg']}")
            return StudyResult.SUCCESS

    def study_emptypage(self, _course, point):
        _session = SessionManager.get_session()
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": point["id"],
                "cpi": _course["cpi"],
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
        )
        if _resp.status_code != 200 or "passport2.chaoxing.com" in str(_resp.url):
            logger.error(f"空页面任务失败 -> [{_resp.status_code}]{point['title']}")
            return StudyResult.ERROR
        else:
            logger.info(f"空章节访问请求已返回，仍需回读平台状态 -> {point['title']}")
            return StudyResult.SUCCESS
