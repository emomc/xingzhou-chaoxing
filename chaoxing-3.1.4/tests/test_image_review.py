import json
import unittest
from unittest.mock import patch
import uuid

from werkzeug.datastructures import MultiDict

from tests.test_vision import ImageBankFixtures, prepared_question


class ImageReviewTests(ImageBankFixtures, unittest.TestCase):
    def entry(self, title="Image practice", state="queued", course="101", **fields):
        question = prepared_question(count=2, course_id=course)
        question["title"] = title + " [image_1] [image_2]"
        answer = self.service.broker.request(self.job([course])["id"], question)
        with self.store.transaction() as db:
            db.execute("UPDATE answers SET state=? WHERE id=?", (state, answer["id"]))
            for key, value in fields.items():
                self.assertIn(key, {"bank_key", "answer_quality", "diagnostics", "error", "answer", "updated_at"})
                db.execute(f"UPDATE answers SET {key}=? WHERE id=?", (value, answer["id"]))
        return answer["id"]

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def test_all_endpoints_are_authenticated_read_only_and_do_not_change_learning(self):
        answer_id = self.entry(state="error", error="AI_VISION_DISAGREEMENT")
        paths = ("/v1/image-questions", f"/v1/image-questions/{answer_id}",
                 f"/v1/image-questions/{answer_id}/images/image_1")
        with self.store.transaction() as db:
            before = db.total_changes
        with patch.object(self.service.broker, "request") as ai, \
                patch.object(self.service.courses, "request") as courses:
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 401)
                    response = self.get(path)
                    self.assertEqual(response.status_code, 200, response.json)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    for method in ("post", "put", "delete"):
                        self.assertEqual(getattr(self.client, method)(path, headers=self.headers).status_code, 405)
            ai.assert_not_called()
            courses.assert_not_called()
        with self.store.transaction() as db:
            self.assertEqual(db.total_changes, before)
        self.assertEqual(self.calls, [])

    def test_list_includes_errors_active_and_retired_candidates_with_exact_counts(self):
        queued = self.entry("Queued")
        running = self.entry("Running", "running", diagnostics='{"stage":"solving"}')
        failed = self.entry("Failed", "error", error="AI_VISION_UNREADABLE")
        historical = self.entry("Historical", "done", bank_key=None, answer='{"Answer":["A"]}')
        checked = self.entry("Checked", "done", answer_quality="vision_cross_checked", answer='{"Answer":["B"]}')
        result = self.get("/v1/image-questions").json
        self.assertEqual({item["id"] for item in result["items"]}, {queued, running, failed, historical, checked})
        self.assertEqual(result["counts"], {"total": 5, "review": 2, "active": 2, "done": 2, "error": 1})
        self.assertEqual(self.get("/v1/image-questions?status=review").json["total"], 2)
        self.assertEqual(self.get("/v1/image-questions?status=active").json["total"], 2)
        self.assertEqual(self.get("/v1/image-questions?status=done").json["total"], 2)
        self.assertEqual(self.get("/v1/image-questions?status=error").json["items"][0]["id"], failed)
        self.assertEqual(self.get(f"/v1/image-questions/{running}").json["stage"], "solving")
        self.assertTrue(self.get(f"/v1/image-questions/{historical}").json["historical"])

    def test_failed_detail_keeps_originals_and_both_ai_candidates_without_secrets(self):
        diagnostics = {
            "stage": "needs_review", "readings": [{"id": "image_1", "text": "x + 1"}],
            "solver_answers": ["B. two"], "verifier_answers": ["A. one"],
            "agreement": False, "verification": {"readable": True, "reading_matches": True},
        }
        answer_id = self.entry(state="error", error="AI_VISION_DISAGREEMENT",
                               diagnostics=json.dumps(diagnostics))
        detail = self.get(f"/v1/image-questions/{answer_id}").json
        self.assertEqual(detail["answers"], [])
        self.assertEqual(detail["diagnostics"], diagnostics)
        self.assertEqual(detail["image_count"], 2)
        self.assertEqual(detail["options"], "A. one\nB. two")
        image = self.get(f"/v1/image-questions/{answer_id}/images/image_2").json
        expected = prepared_question(count=2)["images"][1]
        self.assertEqual(image["data_url"], "data:image/png;base64," + expected["data"])
        serialized = json.dumps(detail)
        for secret in ("offline-only", self.service.secrets.token, "data_url", "base64", "source"):
            self.assertNotIn(secret, serialized)

    def test_search_course_and_pagination_are_literal_and_bounded(self):
        first = self.entry("Alpha % _ '", updated_at=10)
        self.entry("Beta", "error", course="202", error="AI_VISION_UNREADABLE", updated_at=20)
        filtered = self.get("/v1/image-questions?search=%25+_&course_id=101").json
        self.assertEqual([item["id"] for item in filtered["items"]], [first])
        self.assertEqual(self.get("/v1/image-questions?search=ai_vision_unreadable").json["total"], 1)
        self.assertEqual(self.get("/v1/image-questions?search=%27+OR+1=1+--").json["total"], 0)
        page = self.get("/v1/image-questions?page=2&page_size=1").json
        self.assertEqual(page["pages"], 2)
        self.assertEqual(page["items"][0]["id"], first)
        self.assertEqual(self.get("/v1/image-questions?page=100").json["items"], [])
        self.assertEqual(self.service.image_review.list(MultiDict())["courses"][0]["entries"], 1)

    def test_invalid_queries_and_missing_images_do_not_escape_scope(self):
        answer_id = self.entry()
        for query in ("page=0", "page=-1", "page=1000001", "page=bad", "page_size=101", "page_size=0",
                      "status=unknown", "course_id=bad", "course_id=" + "1" * 21, "token=not-allowed",
                      "page=1&page=2", "search=" + "x" * 201):
            with self.subTest(query=query):
                self.assertEqual(self.get("/v1/image-questions?" + query).status_code, 400)
        for suffix in ("bad", answer_id + "/images/image_0", answer_id + "/images/image_11"):
            self.assertEqual(self.get("/v1/image-questions/" + suffix).status_code, 400)
        for suffix in (uuid.uuid4().hex, answer_id + "/images/image_3"):
            self.assertEqual(self.get("/v1/image-questions/" + suffix).status_code, 404)

    def test_text_and_malformed_questions_are_hidden_and_broken_diagnostics_are_tolerated(self):
        valid = self.entry("Valid", diagnostics="not-json")
        invalid = self.entry("Malformed")
        text = self.service.broker.request(self.job()["id"], {
            "course_id": "101", "title": "Text only", "type": "single", "options": "A. one\nB. two",
        })
        with self.store.transaction() as db:
            db.execute("UPDATE answers SET question='not-json' WHERE id=?", (invalid,))
        for query in ("", "?search=valid", "?course_id=101"):
            response = self.get("/v1/image-questions" + query)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual([item["id"] for item in response.json["items"]], [valid])
        for answer_id in (invalid, text["id"]):
            self.assertEqual(self.get(f"/v1/image-questions/{answer_id}").status_code, 404)
        self.assertEqual(self.get(f"/v1/image-questions/{valid}").json["diagnostics"], {})


if __name__ == "__main__":
    unittest.main()
