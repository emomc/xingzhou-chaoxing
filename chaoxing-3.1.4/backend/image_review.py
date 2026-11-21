"""Read-only image questions, including failed and in-flight AI requests."""

import base64
import json
import re
import time

from api.question_images import IMAGE_ID
from api.webai_client import WebAIError, parse_answers
from backend.question_bank import QuestionBank
from backend.store import NotFound


IMAGES = (
    "CASE WHEN json_valid(question) THEN "
    "json_type(question,'$.images')='array' "
    "AND json_array_length(question,'$.images')>0 ELSE 0 END"
)
REVIEW = (
    "(state='error' OR (state='done' AND "
    "(bank_key IS NULL OR answer_quality NOT IN ('vision_cross_checked','confirmed'))))"
)
STATES = {
    "": "1", "review": REVIEW, "active": "state IN ('queued','running')",
    "done": "state='done'", "error": "state='error'",
}


class ImageReview:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _filters(arguments):
        allowed = {"search", "course_id", "status", "page", "page_size"}
        if set(arguments) - allowed or any(len(arguments.getlist(key)) != 1 for key in arguments):
            raise ValueError("Invalid image question query")
        search = arguments.get("search", "").strip()
        course_id, status = arguments.get("course_id", ""), arguments.get("status", "")
        page, size = int(arguments.get("page", "1")), int(arguments.get("page_size", "20"))
        if len(search) > 200 or status not in STATES or not 1 <= page <= 1000000 \
                or not 1 <= size <= 100:
            raise ValueError("Invalid image question query")
        if course_id and not re.fullmatch(r"[0-9]{1,20}", course_id):
            raise ValueError("Invalid course ID")
        conditions, values = [IMAGES, STATES[status]], []
        if search:
            conditions.append("(instr(lower(json_extract(question,'$.title')),lower(?))>0 "
                              "OR instr(lower(COALESCE(error,'')),lower(?))>0)")
            values.extend((search, search))
        if course_id:
            conditions.append("json_extract(question,'$.course_id')=?")
            values.append(course_id)
        return " AND ".join(conditions), values, page, size

    @staticmethod
    def _item(row, names, detail=False):
        question = json.loads(row["question"])
        try:
            answers = parse_answers(row["answer"], question) if row["answer"] else []
        except WebAIError:
            answers = []
        historical = row["bank_key"] is None
        result = {
            "id": row["id"], "course_id": question.get("course_id", ""),
            "course_title": names.get(question.get("course_id"), ""),
            "type": question.get("type", ""), "state": row["state"],
            "title": question.get("title", "") if detail else question.get("title", "")[:500],
            "answer_preview": " / ".join(answers)[:220],
            "image_count": len(question["images"]), "error": row["error"],
            "validation": row["answer_quality"], "historical": historical,
            "needs_review": row["state"] == "error" or (
                row["state"] == "done" and (
                    historical or row["answer_quality"] not in {"vision_cross_checked", "confirmed"}
                )
            ),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        try:
            diagnostics = json.loads(row["diagnostics"])
        except (ValueError, TypeError):
            diagnostics = {}
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        result["stage"] = diagnostics.get("stage", "")
        if detail:
            result.update(
                options=question.get("options", ""), answers=answers,
                images=question["images"], diagnostics=diagnostics,
            )
        return result

    def list(self, arguments):
        where, values, page, size = self._filters(arguments)
        with self.store.transaction() as db:
            total = db.execute(f"SELECT count(*) FROM answers WHERE {where}", values).fetchone()[0]
            names = QuestionBank._course_names(db)
            rows = db.execute(
                f"SELECT * FROM answers WHERE {where} ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                (*values, size, (page - 1) * size),
            ).fetchall()
            counts = dict(db.execute(f"SELECT state,count(*) FROM answers WHERE {IMAGES} GROUP BY state"))
            review = db.execute(
                f"SELECT count(*) FROM answers WHERE {IMAGES} AND {REVIEW}",
            ).fetchone()[0]
            courses = [
                {"id": row[0], "title": names.get(row[0], ""), "entries": row[1]}
                for row in db.execute(
                    f"SELECT json_extract(question,'$.course_id'),count(*) FROM answers WHERE {IMAGES} "
                    "GROUP BY 1 ORDER BY count(*) DESC,1",
                ) if isinstance(row[0], str)
            ]
        return {
            "items": [self._item(row, names) for row in rows], "total": total,
            "page": page, "page_size": size, "pages": (total + size - 1) // size,
            "counts": {"total": sum(counts.values()), "review": review,
                       "active": counts.get("queued", 0) + counts.get("running", 0),
                       "done": counts.get("done", 0), "error": counts.get("error", 0)},
            "courses": courses, "sampled_at": time.time(),
        }

    def detail(self, answer_id):
        if not re.fullmatch(r"[a-f0-9]{32}", answer_id):
            raise ValueError("Invalid image question ID")
        with self.store.transaction() as db:
            row = db.execute(
                f"SELECT * FROM answers WHERE id=? AND {IMAGES}", (answer_id,),
            ).fetchone()
            if row is None:
                raise NotFound("Image question not found")
            return self._item(row, QuestionBank._course_names(db), detail=True)

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
