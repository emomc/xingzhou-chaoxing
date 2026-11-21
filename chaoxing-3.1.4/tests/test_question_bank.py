import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from werkzeug.datastructures import MultiDict

from api.webai_client import question_bank_key
from backend.question_bank import QuestionBank
from backend.service import Service, Settings, create_app


class QuestionBankTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Service(Settings(state_dir=Path(self.temp.name)))
        self.addCleanup(self.service.close)
        self.store = self.service.store
        self.client = create_app(self.service).test_client()
        self.headers = {"Authorization": "Bearer " + self.service.secrets.token}
        account = self.store.save_account("Offline", "private-test-login", "private-test-password")
        fetch = self.store.request_courses(account["id"])
        self.store.claim_courses()
        self.store.finish_courses(account["id"], fetch["request_id"], [
            {"id": "101", "title": "\u519b\u4e8b\u8bfe\u7a0b", "teacher": "Teacher"},
            {"id": "202", "title": "Other course"},
        ])
        self.job, _ = self.store.create_job(account["id"], {
            "course_ids": ["101", "202"], "videos_only": False, "submit": False, "preview": True,
        })
        self.bank = self.service.question_bank

    def entry(self, title="Choice question", course="101", kind="single", answer=None,
              hits=0, created=100, updated=200, done=True):
        question = {
            "title": title, "course_id": course, "type": kind,
            "options": "A. First\nB. Second" if kind in {"single", "multiple"} else "",
        }
        result = self.store.request_answer(self.job["id"], question_bank_key(question), question)
        if done:
            value = answer if answer is not None else (
                ["true"] if kind == "judgement" else ["B. Second"]
                if kind in {"single", "multiple"} else ["Text answer"]
            )
            self.store.finish_answer(result["id"], json.dumps({"Answer": value}, ensure_ascii=False))
        with self.store.transaction() as db:
            db.execute("UPDATE answers SET bank_hits=?,shared_waits=2,created_at=?,updated_at=? WHERE id=?",
                       (hits, created, updated, result["id"]))
        return result["id"]

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def test_new_endpoints_require_authentication_and_are_read_only(self):
        answer_id = self.entry()
        for path in ("/v1/resources", "/v1/question-bank/stats", "/v1/question-bank",
                     f"/v1/question-bank/{answer_id}"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                response = self.get(path)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(self.client.post(path, headers=self.headers).status_code, 405)
                self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 405)

    def test_empty_bank_has_zero_counts_and_valid_storage(self):
        result = self.bank.stats()
        self.assertEqual(result["entries"], 0)
        self.assertEqual(result["course_count"], 0)
        self.assertEqual(result["storage"]["content_bytes"], 0)
        self.assertGreater(result["storage"]["database_bytes"], 0)
        self.assertEqual(self.bank.list(MultiDict())["pages"], 0)

    def test_statistics_separate_utf8_content_from_shared_database_size(self):
        self.entry(title="\u4e2d\u6587\u9898\u76ee", hits=7)
        self.entry(title="Judgement", course="202", kind="judgement", hits=3)
        self.entry(title="Still queued", done=False)
        result = self.bank.stats()
        with self.store.transaction() as db:
            rows = db.execute("SELECT question,answer FROM answers WHERE state='done'").fetchall()
        question_bytes = sum(len(row["question"].encode("utf-8")) for row in rows)
        answer_bytes = sum(len(row["answer"].encode("utf-8")) for row in rows)
        self.assertEqual(result["entries"], 2)
        self.assertEqual(result["hits"], 10)
        self.assertEqual(result["shared_waits"], 4)
        self.assertEqual(result["course_count"], 2)
        self.assertEqual(result["types"], {"single": 1, "judgement": 1})
        storage = result["storage"]
        self.assertEqual(storage["question_bytes"], question_bytes)
        self.assertEqual(storage["answer_bytes"], answer_bytes)
        self.assertEqual(storage["content_bytes"], question_bytes + answer_bytes)
        self.assertEqual(storage["database_bytes"], storage["database_main_bytes"] + storage["database_wal_bytes"])
        self.assertEqual(storage["scope"], "shared_backend_database")
        self.assertEqual(result["courses"][0]["title"], "\u519b\u4e8b\u8bfe\u7a0b")

    def test_list_filters_sorting_and_pagination(self):
        first = self.entry(title="Alpha choice", hits=10, created=10, updated=30)
        second = self.entry(title="Beta choice", hits=1, created=20, updated=50)
        self.entry(title="Alpha judgement", course="202", kind="judgement", created=30, updated=40)
        page = self.bank.list(MultiDict({"page_size": "1", "page": "2"}))
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["pages"], 3)
        self.assertEqual(page["items"][0]["title"], "Alpha judgement")
        by_hits = self.bank.list(MultiDict({"sort": "hits"}))
        self.assertEqual(by_hits["items"][0]["id"], first)
        oldest = self.bank.list(MultiDict({"sort": "oldest"}))
        self.assertEqual(oldest["items"][0]["id"], first)
        filtered = self.bank.list(MultiDict({"search": "ALPHA", "type": "single", "course_id": "101"}))
        self.assertEqual([item["id"] for item in filtered["items"]], [first])
        by_answer = self.bank.list(MultiDict({"search": "Second", "course_id": "101"}))
        self.assertEqual({item["id"] for item in by_answer["items"]}, {first, second})
        self.assertEqual(self.bank.list(MultiDict({"page": "100"}))["items"], [])

    def test_search_is_literal_and_cannot_inject_sql_or_wildcards(self):
        first = self.entry(title="Literal % _ and ' punctuation")
        self.entry(title="Unrelated choice")
        self.assertEqual(self.bank.list(MultiDict({"search": "% _"}))["items"][0]["id"], first)
        self.assertEqual(self.bank.list(MultiDict({"search": "' OR 1=1 --"}))["total"], 0)

    def test_invalid_queries_and_ids_return_400(self):
        for query in (
            "page=0", "page=-1", "page=not-int", "page=1000001", "page_size=101",
            "page_size=0", "sort=bank_hits+DESC", "course_id=bad", "course_id=" + "1" * 21,
            "type=unknown", "password=anything", "page=1&page=2", "search=" + "a" * 201,
        ):
            with self.subTest(query=query):
                self.assertEqual(self.get("/v1/question-bank?" + query).status_code, 400)
        self.assertEqual(self.get("/v1/question-bank/bad").status_code, 400)
        self.assertEqual(self.get("/v1/question-bank/" + uuid.uuid4().hex).status_code, 404)

    def test_detail_includes_full_question_and_validated_answers_without_secrets(self):
        title = "<script>not executable</script>" + "Title" * 160
        answer_id = self.entry(title=title, hits=5)
        detail = self.get("/v1/question-bank/" + answer_id).json
        self.assertEqual(detail["title"], title)
        self.assertEqual(detail["answers"], ["B. Second"])
        self.assertEqual(detail["options"], "A. First\nB. Second")
        self.assertEqual(detail["validation"], "format_checked")
        self.assertEqual(detail["course_title"], "\u519b\u4e8b\u8bfe\u7a0b")
        self.assertEqual(len(self.get("/v1/question-bank").json["items"][0]["title"]), 500)
        output = json.dumps(detail) + json.dumps(self.get("/v1/question-bank/stats").json)
        for secret in ("private-test-password", "private-test-login", self.service.secrets.token,
                       "credentials", "api-token"):
            self.assertNotIn(secret, output)

    def test_unindexed_error_queued_and_invalid_json_are_not_exposed(self):
        ready = self.entry()
        queued = self.entry(title="Queued", done=False)
        error = self.entry(title="Error")
        unindexed = self.entry(title="Unindexed")
        malformed = self.entry(title="Malformed")
        with self.store.transaction() as db:
            db.execute("UPDATE answers SET state='error' WHERE id=?", (error,))
            db.execute("UPDATE answers SET bank_key=NULL WHERE id=?", (unindexed,))
            db.execute("UPDATE answers SET question='not-json' WHERE id=?", (malformed,))
        result = self.get("/v1/question-bank")
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual([item["id"] for item in result.json["items"]], [ready])
        self.assertEqual(self.get("/v1/question-bank/stats").json["entries"], 1)
        for answer_id in (queued, error, unindexed, malformed):
            self.assertEqual(self.get("/v1/question-bank/" + answer_id).status_code, 404)

    def test_reading_the_bank_does_not_change_hits_waiters_or_start_ai(self):
        answer_id = self.entry()
        with self.store.transaction() as db:
            before = db.total_changes
        with patch.object(self.service.broker, "request") as ai, \
                patch.object(self.service.courses, "request") as course:
            for path in ("/v1/resources", "/v1/question-bank", "/v1/question-bank/stats",
                         "/v1/question-bank/" + answer_id):
                self.assertEqual(self.get(path).status_code, 200)
            ai.assert_not_called()
            course.assert_not_called()
        with self.store.transaction() as db:
            self.assertEqual(db.total_changes, before)

    def test_stats_cache_limits_scans_and_returns_detached_values(self):
        now = [10]
        bank = QuestionBank(self.store, clock=lambda: now[0])
        self.entry()
        first = bank.stats()
        first["storage"]["content_bytes"] = -1
        self.entry(title="New entry")
        now[0] = 14.99
        cached = bank.stats()
        self.assertEqual(cached["entries"], 1)
        self.assertGreater(cached["storage"]["content_bytes"], 0)
        now[0] = 15
        self.assertEqual(bank.stats()["entries"], 2)


if __name__ == "__main__":
    unittest.main()
