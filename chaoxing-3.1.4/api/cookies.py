# -*- coding: utf-8 -*-
import os.path
import os
from pathlib import Path
import tempfile

import requests

from api.config import GlobalConst as gc


def save_cookies(session: requests.Session):
    path = Path(gc.COOKIES_PATH)
    fd, temporary = tempfile.mkstemp(prefix=".cookies-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(";".join(f"{key}={value}" for key, value in session.cookies.items()))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def remove_cookies_file():
    """退出登录后清掉本地 cookie 文件，避免别的线程又把旧会话读回来。"""
    try:
        os.unlink(gc.COOKIES_PATH)
    except FileNotFoundError:
        pass


def use_cookies() -> dict:
    if not os.path.exists(gc.COOKIES_PATH):
        return {}

    cookies={}
    with open(gc.COOKIES_PATH, "r", encoding="utf-8") as f:
        buffer = f.read().strip()
        for item in buffer.split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key:
                cookies[key] = value

    return cookies
