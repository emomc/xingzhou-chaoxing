# -*- coding: utf-8 -*-
"""
超星学习通数据解析模块

该模块负责解析超星学习通平台的课程、章节、任务点等各种数据，
并转换为程序内部使用的结构化数据格式。
"""
import json
import re
from typing import List, Dict, Tuple, Any, Optional

from bs4 import BeautifulSoup, NavigableString

from api.course_points import nonnegative_count
from api.font_decoder import FontDecoder
from api.logger import logger


def decode_course_list(html_text: str) -> List[Dict[str, str]]:
    """
    解析课程列表页面，提取课程信息

    Args:
        html_text: 课程列表页面的HTML内容

    Returns:
        课程信息列表，每个课程包含id、title、teacher等信息
    """
    logger.trace("开始解码课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("div.course")
    course_list = []

    for course in raw_courses:
        # 跳过未开放课程
        if course.select_one("a.not-open-tip") or course.select_one("div.not-open-tip"):
            continue

        course_detail = {
            "id": course.attrs["id"],
            "info": course.attrs["info"],
            "roleid": course.attrs["roleid"],
            "clazzId": course.select_one("input.clazzId").attrs["value"],
            "courseId": course.select_one("input.courseId").attrs["value"],
            "cpi": re.findall(r"cpi=(.*?)&", course.select_one("a").attrs["href"])[0],
            "title": course.select_one("span.course-name").attrs["title"],
            "desc": course.select_one("p.margint10").attrs["title"] if course.select_one("p.margint10") else "",
            "teacher": course.select_one("p.color3").attrs["title"]
        }
        course_list.append(course_detail)

    return course_list


def decode_course_folder(html_text: str) -> List[Dict[str, str]]:
    """
    解析二级课程列表页面，提取文件夹信息

    Args:
        html_text: 二级课程列表页面的HTML内容

    Returns:
        课程文件夹信息列表
    """
    logger.trace("开始解码二级课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("ul.file-list>li")
    course_folder_list = []

    for course in raw_courses:
        if not course.attrs.get("fileid"):
            continue

        course_folder_detail = {
            "id": course.attrs["fileid"],
            "rename": course.select_one("input.rename-input").attrs["value"]
        }
        course_folder_list.append(course_folder_detail)

    return course_folder_list


def decode_course_point(html_text: str) -> Dict[str, Any]:
    """
    解析章节列表页面，提取章节点信息

    Args:
        html_text: 章节列表页面的HTML内容

    Returns:
        章节信息字典，包含是否锁定状态和章节点列表
    """
    logger.trace("开始解码章节列表...")
    soup = BeautifulSoup(html_text, "lxml")
    course_point = {
        "hasLocked": False,  # 用于判断该课程任务是否是需要解锁
        "points": [],
    }

    for chapter_unit in soup.find_all("div", class_="chapter_unit"):
        points = _extract_points_from_chapter(chapter_unit)
        # 检查是否有锁定内容
        for point in points:
            if point.get("need_unlock", False):
                course_point["hasLocked"] = True

        course_point["points"].extend(points)

    return course_point


def _extract_points_from_chapter(chapter_unit) -> List[Dict[str, Any]]:
    """
    从章节单元中提取章节点信息

    Args:
        chapter_unit: BeautifulSoup对象，表示一个章节单元

    Returns:
        章节点信息列表
    """
    point_list = []
    raw_points = chapter_unit.find_all("li")

    for raw_point in raw_points:
        point = raw_point.div
        if point is None:
            continue
        point_id_match = re.fullmatch(r"cur([0-9]{1,20})", point.get("id", ""))
        if not point_id_match:
            continue

        point_id = point_id_match[1]
        point_title = point.select_one("a.clicktitle").text.replace("\n", "").strip()

        # 提取任务数量
        job_count = 1
        job_count_known = False
        completed_job_count = 0
        counter = point.select_one("input.knowledgeJobCount")
        if counter:
            parsed_count = nonnegative_count(counter.get("value"), None)
            if parsed_count is not None:
                job_count = parsed_count
                job_count_known = True

        # Non-task resources use a visible zero badge instead of the hidden counter.
        badge = point.select_one(".catalog_task .catalog_jindu > span.catalog_points_yi")
        if not job_count_known and badge is not None \
                and nonnegative_count(badge.get_text(strip=True), None) == 0:
            job_count = 0
            job_count_known = True

        # 判断是否已完成
        tip = point.select_one("span.bntHoverTips")
        completion_text = tip.get_text(" ", strip=True) if tip else ""
        need_unlock = "解锁" in completion_text
        is_finished = "已完成" in completion_text and "未完成" not in completion_text
        completed_count = re.search(r"(\d+)\s*/\s*(\d+)", completion_text)
        if completed_count:
            completed_job_count = int(completed_count[1])
            job_count = max(completed_job_count, int(completed_count[2]))
            job_count_known = True
            is_finished = completed_job_count >= job_count
        else:
            partial_count = re.search(
                r"已完成\s*(\d+)\s*个?.*?未完成\s*(\d+)\s*个?", completion_text
            )
            if partial_count:
                completed_job_count = int(partial_count[1])
                job_count = completed_job_count + int(partial_count[2])
                job_count_known = True
                is_finished = int(partial_count[2]) == 0
            elif is_finished:
                completed_job_count = job_count
            else:
                pending_count = re.search(r"(\d+)\s*个\s*待完成任务点", completion_text)
                if pending_count:
                    job_count = max(job_count if job_count_known else 0, int(pending_count[1]))
                    job_count_known = True
        if job_count_known and job_count == 0:
            is_finished = not need_unlock

        point_detail = {
            "id": point_id,
            "title": point_title,
            "jobCount": job_count,
            "jobCountKnown": job_count_known,
            "completedJobCount": min(completed_job_count, job_count),
            "has_finished": is_finished,
            "need_unlock": need_unlock
        }
        point_list.append(point_detail)

    return point_list


def decode_course_card(html_text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    解析任务点列表页面，提取任务点信息

    Args:
        html_text: 任务点列表页面的HTML内容

    Returns:
        任务点列表和任务信息的元组
    """
    logger.trace("开始解码任务点列表...")

    # 检查章节是否未开放
    if "章节未开放" in html_text:
        soup = BeautifulSoup(html_text, "lxml")
        if "章节未开放" in soup.get_text(" ", strip=True):
            return [], {"notOpen": True}

    # 提取mArg参数
    assignment = re.search(r"\bmArg\s*=\s*(?=\{)", html_text)
    if not assignment:
        state = "empty" if _is_empty_course_card(html_text) else "unrecognized"
        return [], {"card_state": state, "empty": state == "empty"}

    cards_data, _ = json.JSONDecoder().raw_decode(html_text[assignment.end():])
    if not isinstance(cards_data, dict):
        raise ValueError("Invalid task card data")

    cards = cards_data.get("attachments")
    if not isinstance(cards, list) or any(not isinstance(card, dict) for card in cards):
        raise ValueError("Invalid task card attachments")
    if not isinstance(cards_data.get("defaults", {}), dict):
        raise ValueError("Invalid task card defaults")

    # 提取任务信息
    job_info = _extract_job_info(cards_data)
    job_info["empty"] = not cards and _is_empty_course_card(html_text)
    job_info["card_state"] = "tasks" if cards else "empty" if job_info["empty"] else "no_tasks"

    # 处理所有附件任务
    job_info["task_status"] = {
        str(card["jobid"]): is_task_passed(card.get("isPassed"))
        for card in cards if isinstance(card, dict) and card.get("jobid")
    }
    untracked_videos, unresolved_tasks, book_tasks = [], [], []
    job_list = _process_attachment_cards(cards, untracked_videos, unresolved_tasks, book_tasks)
    if untracked_videos:
        job_info["untracked_videos"] = untracked_videos
    if unresolved_tasks:
        job_info["unresolved_tasks"] = unresolved_tasks
    if book_tasks:
        job_info["book_tasks"] = book_tasks

    return job_list, job_info


def decode_course_card_count(html_text: str) -> Optional[int]:
    soup = BeautifulSoup(html_text, "lxml")
    values = {
        nonnegative_count(node.get("value"), None)
        for node in soup.select('input[name="cardcount"], input#cardcount')
    }
    if not values or None in values or len(values) != 1:
        return None
    count = values.pop()
    if count is not None and count > 100:
        raise ValueError("Task card count exceeds the supported safety limit")
    return count if count else None


def _is_empty_course_card(html_text: str) -> bool:
    soup = BeautifulSoup(html_text, "lxml")
    content = soup.select_one("#pageDiv") or soup.body or soup
    if content.select_one("iframe, video, audio, object, embed, [objectid], [data-objectid]"):
        return False
    if any(not image.find_parent(class_="nullpage") for image in content.select("img")):
        return False
    for placeholder in content.select(".nullpage"):
        if any(
            node.name in {"script", "style", "template", "noscript"}
            or node.has_attr("hidden") or node.get("aria-hidden") == "true"
            or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", node.get("style", ""), re.I)
            for node in [placeholder, *placeholder.parents] if node.name
        ):
            continue
        text = placeholder.get_text("", strip=True)
        if text in {"暂无内容", "本章节暂无内容", "本节暂无内容"} \
                and content.get_text("", strip=True) == text:
            return True
    return False


def _extract_job_info(cards_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从卡片数据中提取任务基本信息

    Args:
        cards_data: 卡片数据字典

    Returns:
        任务基本信息字典
    """
    defaults = cards_data.get("defaults", {})
    if not defaults:
        return {}

    return {
        "ktoken": defaults.get("ktoken", ""),
        "mtEnc": defaults.get("mtEnc", ""),
        "reportTimeInterval": defaults.get("reportTimeInterval", 60),
        "defenc": defaults.get("defenc", ""),
        "cardid": defaults.get("cardid", ""),
        "cpi": defaults.get("cpi", ""),
        "qnenc": defaults.get("qnenc", ""),
        "knowledgeid": defaults.get("knowledgeid", "")
    }


def is_task_passed(value: Any) -> bool:
    return value is True or value == 1 or (
        isinstance(value, str) and value.strip().lower() in {"true", "1"}
    )


def _process_attachment_cards(
    cards: List[Dict[str, Any]], untracked_videos: Optional[list] = None,
    unresolved_tasks: Optional[list] = None, book_tasks: Optional[list] = None,
) -> List[Dict[str, Any]]:
    """
    处理所有附件任务卡片

    Args:
        cards: 附件任务卡片列表
        untracked_videos: 缺少任务标识、需要人工确认的视频
        unresolved_tasks: 完全不认识的任务类型（会让章节标成"需人工"）
        book_tasks: 图书任务点；类型明确但脚本做不了，单独收集，不算"不认识"

    Returns:
        处理后的任务列表
    """
    job_list = []

    for card in cards:
        # 跳过已通过的任务
        if is_task_passed(card.get("isPassed", False)):
            continue

        # 图书任务点（insertbook）没有 type 字段，只能靠 property.module 认。
        # 它必须排在"不认识的任务"之前，否则整章会被标成人工处理，课程永远确认不了。
        if _is_book_card(card):
            book = _process_book_task(card)
            if book is not None and book_tasks is not None:
                book_tasks.append(book)
            continue

        # 处理不同类型的任务
        if card.get("job") is None and card.get("type") != "video" \
                and not (card.get("jobid") and "isPassed" in card):
            # 处理阅读类型任务
            read_job = _process_read_task(card)
            if read_job:
                job_list.append(read_job)
            continue

        # 一开始就把超星api的屎山处理掉，不要用一个屎山行为掩盖另一个屎山 (指根据otherInfo中是否有courseId决定url拼接方式😂)
        if "otherInfo" in card:
            logger.trace("Fixing other info...")
            card["otherInfo"] = card["otherInfo"].split("&")[0]
            logger.trace(f"New info: {card['otherInfo']}")


        # 根据任务类型处理
        card_type = card.get("type", "")
        if card_type == "video":
            video_job = _process_video_task(card)
            if video_job:
                job_list.append(video_job)
            elif untracked_videos is not None:
                untracked_videos.append(card.get("property", {}).get("name", "Unnamed video"))
        elif card_type == "document":
            doc_job = _process_document_task(card)
            if doc_job:
                job_list.append(doc_job)
        elif card_type == "workid":
            work_job = _process_work_task(card)
            if work_job:
                job_list.append(work_job)
        elif card_type == "read":
            read_job = _process_read_task(card)
            if read_job:
                job_list.append(read_job)
        else:
            task = {
                "type": str(card_type)[:40], "jobid": str(card.get("jobid", ""))[:100],
                "title": str(card.get("property", {}).get("title") or
                             card.get("property", {}).get("name") or card_type)[:200],
            }
            if unresolved_tasks is not None:
                unresolved_tasks.append(task)
            logger.warning(
                "Unsupported task card requires manual review: type={}, job={}",
                task["type"], task["jobid"],
            )

    return job_list


def _is_book_card(card: Dict[str, Any]) -> bool:
    """图书任务点：property.module 是 insertbook，且没有 type 字段。"""
    if card.get("type") == "book":
        return True
    properties = card.get("property") or {}
    return isinstance(properties, dict) and properties.get("module") == "insertbook"


def _process_book_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """图书任务点：脚本没有阅读器上报链路，只登记书名，交给人工读。"""
    properties = card.get("property") or {}
    if not isinstance(properties, dict):
        properties = {}
    title = properties.get("bookname") or properties.get("name") or properties.get("title") or "图书"
    return {
        "type": "book",
        "jobid": str(card.get("jobid", ""))[:100],
        "title": str(title)[:200],
        "author": str(properties.get("author") or "")[:120],
        "publisher": str(properties.get("publisher") or "")[:120],
    }


def _process_read_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理阅读类型任务"""
    if not (card.get("type") == "read" and not card.get("property", {}).get("read", False)):
        return None

    return {
        "title": card.get("property", {}).get("title", ""),
        "type": "read",
        "id": card.get("property", {}).get("id", ""),
        "jobid": card.get("jobid", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def _process_video_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理视频类型任务"""
    properties = card.get("property", {})
    object_id = card.get("objectId") or properties.get("objectid", "")
    job_id = card.get("jobid", "")
    if not object_id or not job_id:
        logger.warning(
            "视频缺少可用的任务标识，需要在学习通手动检查：{}",
            properties.get("name", "Unnamed video"),
        )
        return None
    return {
        "type": "video",
        "jobid": job_id,
        "name": properties.get("name", ""),
        "otherinfo": card.get("otherInfo", ""),
        "mid": card.get("mid", ""),
        "objectid": object_id,
        "aid": card.get("aid", ""),
        # 缺失、空值或非法值保留为未知，不能伪装成真实的 0 秒。
        "playTime": parse_video_play_time(card.get("playTime")),
        "rt": properties.get("rt", ""),
        "attDuration": card.get("attDuration", ""),
        "attDurationEnc": card.get("attDurationEnc", ""),
        "videoFaceCaptureEnc": card.get("videoFaceCaptureEnc", ""),
    }


def parse_video_play_time(value: Any) -> Optional[int]:
    """解析卡片的毫秒进度；无效数据返回 None，真实的零进度仍保留为 0。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or not value.isdecimal():
            return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return milliseconds if milliseconds >= 0 else None


def _process_document_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理文档类型任务"""
    properties = card.get("property", {})
    return {
        "type": "document",
        "name": str(properties.get("name") or properties.get("title") or "")[:240],
        "jobid": card.get("jobid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", ""),
        "objectid": card.get("property", {}).get("objectid", "")
    }


def _process_work_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理作业类型任务"""
    return {
        "type": "workid",
        "jobid": card.get("jobid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def decode_questions_info(html_content: str) -> Dict[str, Any]:
    """
    解析题目信息，提取表单数据和问题列表

    Args:
        html_content: 题目页面HTML内容

    Returns:
        包含表单数据和问题列表的字典
    """
    soup = BeautifulSoup(html_content, "lxml")
    form_data = _extract_form_data(soup)

    # 检查是否存在字体加密
    has_font_encryption = bool(soup.find("style", id="cxSecretStyle"))
    font_decoder = None

    if has_font_encryption:
        font_decoder = FontDecoder(html_content)
    else:
        logger.warning("未找到字体文件，可能是未加密的题目不进行解密")

    # 处理所有问题
    questions = []
    for div_tag in soup.find("form").find_all("div", class_="singleQuesId"):
        question = _process_question(div_tag, font_decoder)
        if question:
            questions.append(question)

    # 更新表单数据
    form_data["questions"] = questions
    form_data["answerwqbid"] = ",".join([q["id"] for q in questions]) + ","

    return form_data


def _extract_form_data(soup: BeautifulSoup) -> Dict[str, Any]:
    """从BeautifulSoup对象中提取表单数据"""
    form_data = {}
    form_tag = soup.find("form")

    if not form_tag:
        return form_data

    # 提取所有非答案字段的input
    for input_tag in form_tag.find_all("input"):
        if "name" not in input_tag.attrs or "answer" in input_tag.attrs["name"]:
            continue
        form_data[input_tag.attrs["name"]] = input_tag.attrs.get("value", "")

    return form_data


def _process_question(div_tag, font_decoder=None) -> Dict[str, Any]:
    """处理单个问题"""
    # 提取问题ID和题目类型
    question_id = div_tag.attrs.get("data", "")
    q_type_code = div_tag.find("div", class_="TiMu").attrs.get("data", "")
    q_type = _get_question_type(q_type_code)

    # 提取题目内容和选项
    title_div = div_tag.find("div", class_="Zy_TItle")
    options_list = div_tag.find("ul").find_all("li") if div_tag.find("ul") else []

    # 解析题目和选项
    images = []
    q_title = _extract_title(title_div, font_decoder, images)
    q_options = []
    for index, li in enumerate(options_list):
        q_options.append(_extract_choices(li, font_decoder, images, chr(65 + index)))
    # 排序选项
    q_options.sort()
    q_options = '\n'.join(q_options)
    upload = q_type == "shortanswer" and (
        div_tag.select_one('input[type="file"]') is not None or (
            re.search(
                r"(?:请|必须|需要|要求)\s*(?:你|您)?\s*(?:将|把|上传|提交)", q_title,
            ) is not None and re.search(
                r"(?:请|必须|需要|要求).{0,100}(?:上传|提交).{0,25}(?:文件|附件|作品|视频|照片|截图|录音)"
                r"|(?:请|必须|需要|要求).{0,100}(?:文件|附件|作品|视频|照片|截图|录音).{0,25}(?:上传|提交)",
                q_title,
            ) is not None
        )
    )

    return {
        "id": question_id,
        "title": q_title,
        "options": q_options,
        "type": q_type,
        "has_images": bool(images),
        **({"images": images} if images else {}),
        **({"manual_reason": "FILE_UPLOAD_REQUIRED"} if upload else {}),
        "answerField": {
            f"answer{question_id}": "",
            f"answertype{question_id}": q_type_code,
        },
    }


def _get_question_type(type_code: str) -> str:
    """根据题型代码返回题型名称"""
    type_map = {
        "0": "single",      # 单选题
        "1": "multiple",    # 多选题
        "2": "completion",  # 填空题
        "3": "judgement",   # 判断题
        "4": "shortanswer", # 简答题
    }

    if type_code in type_map:
        return type_map[type_code]

    logger.info(f"未知题型代码 -> {type_code}")
    return "unknown"


def _extract_title(element, font_decoder=None, images=None) -> str:
    """提取标题内容，支持解码加密字体"""
    if not element:
        return ""

    # 收集元素中的所有文本和图片
    content = []
    for item in element.descendants:
        if isinstance(item, NavigableString):
            content.append(item.string or "")
        elif item.name == "img":
            img_url = next((
                item.get(attribute) for attribute in
                ("data-src", "data-original", "data-actualsrc", "data-lazy-src", "src")
                if item.get(attribute)
            ), "")
            if images is None:
                content.append(f'<img src="{img_url}">')
            else:
                image_id = f"image_{len(images) + 1}"
                images.append({"id": image_id, "source": img_url})
                content.append(f"[{image_id}]")

    raw_content = "".join(content)
    cleaned_content = raw_content.replace("\r", "").replace("\t", "").replace("\n", "")

    # 如果有字体解码器，进行解码
    if font_decoder:
        return font_decoder.decode(cleaned_content)

    return cleaned_content

def _extract_choices(element, font_decoder=None, images=None, default_label=None) -> str:
    """提取选项内容，支持解码加密字体"""
    if not element:
        return ""

    # 提取aria-label属性值作为选项，解决#474
    if images is not None and element.find("img"):
        choice = _extract_title(element, None, images)
        accessible = element.get("aria-label", "").strip()
        choice = re.sub(r"^\s*([A-Za-z])(?=\[image_)", r"\1. ", choice)
        if accessible and not re.match(r"^\s*[A-Za-z](?:[.、．:：)）]|\s)", choice):
            choice = accessible + " " + choice
        if default_label and not re.match(r"^\s*[A-Za-z](?:[.、．:：)）]|\s)", choice):
            choice = default_label + ". " + choice
    else:
        choice = element.get("aria-label") or element.get_text()
    if not choice:
        return ""

    cleaned_content = re.sub(r"[\r\t\n]", "", choice)

    if font_decoder:
        cleaned_content = font_decoder.decode(cleaned_content)

    cleaned_content = cleaned_content.strip()
    if cleaned_content.endswith("选择"):
        cleaned_content = cleaned_content[:-2].rstrip()

    return cleaned_content
