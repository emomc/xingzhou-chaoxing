"""Read-only, bounded inspection of the existing validated shared question bank."""

import copy
import base64
import json
from pathlib import Path
import re
import threading
import time

from api.webai_client import QUESTION_TYPES, parse_answers
from api.question_images import IMAGE_ID
from backend.store import NotFound


READY = (
    "(bank_key IS NOT NULL OR cache_key='retired-image:'||id) "
    "AND state='done' AND json_valid(question)"
)
REUSABLE = (
    "bank_key IS NOT NULL AND "
    "(COALESCE(json_array_length(question,'$.images'),0)=0 "
    "OR answer_quality IN ('vision_cross_checked','confirmed'))"
)
SORTS = {"updated": "updated_at DESC,id", "hits": "bank_hits DESC,updated_at DESC,id",
         "oldest": "created_at,id"}


class QuestionBank:
    def __init__(self, store, *, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.lock = threading.Lock()
        self.cached = None
        self.sampled_at = None

    @staticmethod
    def _course_names(db):
        names = {}
        for row in db.execute("SELECT courses FROM course_fetches ORDER BY updated_at DESC"):
            try:
                courses = json.loads(row[0])
                for course in courses if isinstance(courses, list) else []:
                    course_id, title = course.get("id"), course.get("title")
                    if isinstance(course_id, str) and isinstance(title, str) and title.strip():
                        names.setdefault(course_id, title[:300])
            except (ValueError, TypeError, AttributeError):
                continue
        return names

    def stats(self):
        with self.lock:
            now = self.clock()
            if self.cached is None or not 0 <= now - self.sampled_at < 5:
                self.cached = self._stats()
                self.sampled_at = now
            return copy.deepcopy(self.cached)

    def _stats(self):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT count(*),COALESCE(sum(bank_hits),0),COALESCE(sum(shared_waits),0),"
                "COALESCE(sum(length(CAST(question AS BLOB))),0),"
                "COALESCE(sum(length(CAST(answer AS BLOB))),0),max(updated_at) "
                f"FROM answers WHERE {READY}"
            ).fetchone()
            names = self._course_names(db)
            courses = [
                {"id": item[0], "title": names.get(item[0], ""), "entries": item[1], "hits": item[2]}
                for item in db.execute(
                    "SELECT json_extract(question,'$.course_id'),count(*),sum(bank_hits) "
                    f"FROM answers WHERE {READY} GROUP BY 1 ORDER BY count(*) DESC,1"
                )
            ]
            types = dict(db.execute(
                f"SELECT json_extract(question,'$.type'),count(*) FROM answers WHERE {READY} GROUP BY 1"
            ))
            image_row = db.execute(
                "SELECT count(*),COALESCE(sum(byte_count),0) FROM answer_images"
            ).fetchone()
            reusable = db.execute(
                f"SELECT count(*) FROM answers WHERE {READY} AND ({REUSABLE})"
            ).fetchone()[0]
            sizes = {}
            for name, path in (
                ("database_main_bytes", self.store.path),
                ("database_wal_bytes", Path(str(self.store.path) + "-wal")),
            ):
                try:
                    sizes[name] = path.stat().st_size
                except FileNotFoundError:
                    sizes[name] = 0
            return {
                "entries": row[0], "hits": row[1], "shared_waits": row[2],
                "reusable_entries": reusable, "candidate_entries": row[0] - reusable,
                "course_count": len(courses), "courses": courses, "types": types,
                "updated_at": row[5], "sampled_at": time.time(),
                "storage": {
                    "scope": "shared_backend_database", "question_bytes": row[3],
                    "answer_bytes": row[4], "content_bytes": row[3] + row[4] + image_row[1],
                    "image_count": image_row[0], "image_bytes": image_row[1],
                    "database_bytes": sum(sizes.values()), **sizes,
                },
            }

    @staticmethod
    def _filters(arguments):
        allowed = {"search", "course_id", "type", "sort", "page", "page_size"}
        if set(arguments) - allowed or any(len(arguments.getlist(key)) != 1 for key in arguments):
            raise ValueError("Invalid question bank query")
        search = arguments.get("search", "").strip()
        course_id, question_type = arguments.get("course_id", ""), arguments.get("type", "")
        sort = arguments.get("sort", "updated")
        page, page_size = int(arguments.get("page", "1")), int(arguments.get("page_size", "20"))
        if len(search) > 200 or sort not in SORTS or not 1 <= page <= 1000000 \
                or not 1 <= page_size <= 100:
            raise ValueError("Invalid question bank query")
        if course_id and not re.fullmatch(r"[0-9]{1,20}", course_id):
            raise ValueError("Invalid course ID")
        if question_type and question_type not in QUESTION_TYPES:
            raise ValueError("Invalid question type")
        conditions, values = [READY], []
        if search:
            conditions.append("(instr(lower(json_extract(question,'$.title')),lower(?))>0 "
                              "OR instr(lower(answer),lower(?))>0)")
            values.extend((search, search))
        if course_id:
            conditions.append("json_extract(question,'$.course_id')=?")
            values.append(course_id)
        if question_type:
            conditions.append("json_extract(question,'$.type')=?")
            values.append(question_type)
        return " AND ".join(conditions), values, sort, page, page_size

    @staticmethod
    def _item(row, names, detail=False):
        question = json.loads(row["question"])
        answers = parse_answers(row["answer"], question)
        content_bytes = len(row["question"].encode("utf-8")) + len(row["answer"].encode("utf-8"))
        content_bytes += sum(image["byte_count"] for image in question.get("images", []))
        result = {
            "id": row["id"], "course_id": question.get("course_id", ""),
            "course_title": names.get(question.get("course_id"), ""),
            "type": question["type"], "title": question["title"] if detail else question["title"][:500],
            "answer_preview": " / ".join(answers)[:220], "hits": row["bank_hits"],
            "shared_waits": row["shared_waits"], "content_bytes": content_bytes,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "validation": row["answer_quality"],
            "reusable": row["bank_key"] is not None and (
                not question.get("images") or row["answer_quality"] in {
                    "vision_cross_checked", "confirmed",
                }
            ),
            "historical": row["bank_key"] is None,
            "image_count": len(question.get("images", [])),
        }
        if detail:
            result.update(options=question.get("options", ""), answers=answers,
                          images=question.get("images", []), diagnostics=json.loads(row["diagnostics"]))
        return result

    def list(self, arguments):
        where, values, sort, page, page_size = self._filters(arguments)
        with self.store.transaction() as db:
            total = db.execute(f"SELECT count(*) FROM answers WHERE {where}", values).fetchone()[0]
            names = self._course_names(db)
            rows = db.execute(
                "SELECT id,question,answer,bank_hits,shared_waits,created_at,updated_at,"
                "bank_key,answer_quality,diagnostics FROM answers "
                f"WHERE {where} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                (*values, page_size, (page - 1) * page_size),
            ).fetchall()
            return {
                "items": [self._item(row, names) for row in rows], "total": total, "page": page,
                "page_size": page_size, "pages": (total + page_size - 1) // page_size,
            }

    def detail(self, answer_id):
        if not re.fullmatch(r"[a-f0-9]{32}", answer_id):
            raise ValueError("Invalid question ID")
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT id,question,answer,bank_hits,shared_waits,created_at,updated_at,"
                "bank_key,answer_quality,diagnostics FROM answers "
                f"WHERE id=? AND {READY}", (answer_id,),
            ).fetchone()
            if row is None:
                raise NotFound("Question bank entry not found")
            return self._item(row, self._course_names(db), detail=True)

    def image(self, answer_id, image_id):
        if not IMAGE_ID.fullmatch(image_id):
            raise ValueError("Invalid question image ID")
        detail = self.detail(answer_id)
        image = next((item for item in detail["images"] if item["id"] == image_id), None)
        if image is None:
            raise NotFound("Question image not found")
        raw = self.store.image_blob(image)
        return {
            "id": image_id, "sha256": image["sha256"], "mime_type": image["mime_type"],
            "byte_count": image["byte_count"],
            "data_url": f"data:{image['mime_type']};base64," + base64.b64encode(raw).decode("ascii"),
        }
