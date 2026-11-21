import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from api.vision_pipeline import (
    IMAGE_QUALITY, MAX_READING_CHARS, answer_identity, json_object, read_images, reading_prompt,
)
from api.webai_client import WebAIClient, validated_completion
from api.webai_errors import (
    GenerationError, InvalidAnswerError, RateLimitedError, VisionReviewError, WebAIError,
)
from backend.client import BrokerClient
from backend.security import Secrets
from backend.store import Conflict, Store
from tests.test_vision import ImageBankFixtures, prepared_question


def reading(question):
    return json.dumps({"images": [
        {"id": image["id"], "readable": True, "text": "literal " + image["id"]}
        for image in question["images"]
    ]})


def verified(answer="B", **flags):
    return json.dumps({"readable": True, "reading_matches": True, "Answer": [answer]} | flags)


class ScriptedClient(WebAIClient):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.selected_model = None
        self.last_diagnostics = {}
        self.vision_pipeline_timeout = 600

    def complete_stage(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        self.selected_model = "doubao" if kwargs["vision"] else "deepseek"
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


class VisionPipelineTests(unittest.TestCase):
    def test_rate_limit_is_not_a_format_retry_or_a_generic_generation_failure(self):
        client = ScriptedClient([RateLimitedError("Upstream rate limited")])
        with self.assertRaises(RateLimitedError):
            validated_completion(client, prepared_question(), attempts=5)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.last_diagnostics["error"], "AI_RATE_LIMITED")
        self.assertEqual(client.last_diagnostics["stages"][0]["status"], "generation_failed")

    def test_worker_reports_a_clear_rate_limit_without_an_answer_or_resubmission(self):
        client = BrokerClient({
            "webai_broker_endpoint": "http://127.0.0.1:3100/internal",
            "webai_broker_token": "offline-test-token",
        })
        self.addCleanup(client.close)
        with patch.object(client, "_request", return_value={
            "id": "offline-test", "state": "error", "error": "AI_RATE_LIMITED",
        }) as request, patch.object(client, "_report") as report:
            with self.assertRaises(RateLimitedError):
                client.complete(prepared_question())
        request.assert_called_once()
        self.assertEqual(report.call_args.args[1], "error")
        self.assertIn("限流", report.call_args.args[2])

    def test_routes_reading_solving_and_independent_original_image_check(self):
        question = prepared_question(count=2)
        client = ScriptedClient([reading(question), '{"Answer":["B"]}', verified()])
        stages = []
        with self.assertLogs("api.vision_pipeline", level="INFO") as logs:
            result = validated_completion(client, question, on_stage=stages.append)
        self.assertEqual(result, ["B. two"])
        self.assertEqual(stages, ["recognizing", "solving", "verifying"])
        self.assertEqual([kwargs["vision"] for _, kwargs in client.calls], [True, False, True])
        self.assertIs(client.calls[0][1]["question"], question)
        self.assertIsNone(client.calls[1][1]["question"])
        self.assertIs(client.calls[2][1]["question"], question)
        self.assertIn("literal image_2", client.calls[1][0])
        self.assertIn("literal image_2", client.calls[2][0])
        self.assertNotIn('{"Answer":["B"]}', client.calls[2][0])
        self.assertEqual(client.last_diagnostics["quality"], IMAGE_QUALITY)
        self.assertTrue(client.last_diagnostics["agreement"])
        self.assertEqual([item["model"] for item in client.last_diagnostics["stages"]],
                         ["doubao", "deepseek", "doubao"])
        self.assertNotIn("literal image_2", str(logs.output))

    def test_swapped_option_images_and_reused_markers_keep_their_identity(self):
        question = prepared_question(count=3) | {
            "title": "Stem [image_1]", "options": "A. [image_3]\nB. [image_2] [image_1]",
        }
        client = ScriptedClient([reading(question), '{"Answer":["A"]}', verified("A")])
        self.assertEqual(validated_completion(client, question), ["A. [image_3]"])
        self.assertIn("A. [image_3]", client.calls[1][0])
        self.assertIn("B. [image_2] [image_1]", client.calls[1][0])
        self.assertEqual(len(client.last_diagnostics["readings"]), 3)

    def test_readings_allow_reordered_json_but_cover_every_original_image_once(self):
        question = prepared_question(count=2)
        items = json.loads(reading(question))["images"]
        result = read_images(json.dumps({"images": list(reversed(items))}), question)
        self.assertEqual([item["id"] for item in result], ["image_1", "image_2"])
        for changed in (items[:1], items + [items[0]], [items[0], items[0]],
                        [items[0], items[1] | {"id": "image_3"}]):
            with self.subTest(changed=changed), self.assertRaises(WebAIError):
                read_images(json.dumps({"images": changed}), question)

    def test_missing_flags_empty_oversized_and_nonobject_readings_fail_closed(self):
        question = prepared_question()
        item = json.loads(reading(question))["images"][0]
        for changed in ({"id": item["id"], "text": "text"},
                        item | {"readable": 1}, item | {"text": ""},
                        item | {"text": "x" * (MAX_READING_CHARS + 1)}, "not-an-object"):
            with self.subTest(changed=str(changed)[:70]), self.assertRaises(WebAIError):
                read_images(json.dumps({"images": [changed]}), question)
        for payload in ("null", "[]", "not-json"):
            with self.subTest(payload=payload), self.assertRaises(WebAIError):
                json_object(payload)

    def test_unreadable_image_stops_before_text_generation_without_retry(self):
        question = prepared_question()
        payload = json.loads(reading(question))
        payload["images"][0]["readable"] = False
        client = ScriptedClient([json.dumps(payload)])
        with self.assertRaises(VisionReviewError) as error:
            validated_completion(client, question, attempts=8)
        self.assertEqual(error.exception.code, "AI_VISION_UNREADABLE")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.last_diagnostics["stage"], "needs_review")

    def test_independent_disagreement_is_not_retried_or_marked_reusable(self):
        question = prepared_question()
        client = ScriptedClient([reading(question), '{"Answer":["B"]}', verified("A")])
        with self.assertRaises(VisionReviewError) as error:
            validated_completion(client, question, attempts=10)
        self.assertEqual(error.exception.code, "AI_VISION_DISAGREEMENT")
        self.assertEqual(len(client.calls), 3)
        self.assertFalse(client.last_diagnostics["agreement"])
        self.assertEqual(client.last_diagnostics["quality"], "candidate")
        self.assertEqual(client.last_diagnostics["solver_answers"], ["B. two"])
        self.assertEqual(client.last_diagnostics["verifier_answers"], ["A. one"])
        self.assertEqual(client.last_diagnostics["verification"], {"readable": True, "reading_matches": True})

    def test_failed_original_image_check_rejects_even_matching_answers(self):
        for flags, code in (
            ({"readable": False}, "AI_VISION_UNREADABLE"),
            ({"reading_matches": False}, "AI_VISION_READING_MISMATCH"),
        ):
            question = prepared_question()
            client = ScriptedClient([reading(question), '{"Answer":["B"]}', verified(**flags)])
            with self.subTest(flags=flags), self.assertRaises(VisionReviewError) as error:
                validated_completion(client, question)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(len(client.calls), 3)

    def test_only_malformed_stage_is_retried_and_the_retry_is_bounded(self):
        question = prepared_question()
        client = ScriptedClient([
            "not-json", reading(question), '{"Answer":["B"]}', verified(),
        ])
        rejected = Mock()
        self.assertEqual(validated_completion(client, question, on_reject=rejected), ["B. two"])
        self.assertEqual([kwargs["stage"] for _, kwargs in client.calls],
                         ["recognizing", "recognizing", "solving", "verifying"])
        rejected.assert_called_once()
        client = ScriptedClient([reading(question), "bad-output", "bad-output"])
        with self.assertRaises(InvalidAnswerError):
            validated_completion(client, question, attempts=50)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.last_diagnostics["stages"][-1]["status"], "invalid_format")

    def test_answer_wrapped_in_a_sentence_is_still_read(self):
        """glm-4v-flash 会写"Based on the images, the answer is {...}"，别把正确答案扔掉。"""
        from api.vision_pipeline import image_answers
        question = {"title": "1 + 1 = ?", "type": "single", "options": "A. 1\nB. 2"}
        for raw in (
            'Based on the provided images, the answer is {"Answer": ["B"]}.',
            '```json\n{\n    "Answer": [\n        "B"\n    ]\n}\n```',
            '{"Answer": ["B"], "note": "see {border} in image_1"}',
            '{"Answer": ["B"]} 希望有帮助',
            '```json\n{"Answer": ["B"]}\n```\n以上是答案',
            # glm-4v-flash 真的会输出 Python 风格的单引号
            "Based on the given images, the answer is {'Answer': ['B']}.",
            # glm-4.1v-thinking-flash 会把答案包在标记里、并且只给一个数组
            '<|begin_of_box|>["B"]<|end_of_box|>',
            '["B"]',
            # 被截断的回复里答案数组本身是完整的，能救就救
            'the answer is {"Answer": ["B"]',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(image_answers(raw, question, (), ()), ["B. 2"])

    def test_broken_json_is_still_rejected(self):
        from api.vision_pipeline import image_answers
        question = {"title": "1 + 1 = ?", "type": "single", "options": "A. 1\nB. 2"}
        for raw in ('no json at all', '{"Answer": "B"', '{"Answer": ["B", ]}', '[]',
                    '["B", "C"', '答案见上'):
            with self.subTest(raw=raw), self.assertRaises(WebAIError):
                image_answers(raw, question, (), ())

    def test_formula_json_escaping_is_explained_and_invalid_latex_gets_format_feedback(self):
        question = prepared_question()
        bad = r'{"images":[{"id":"image_1","readable":true,"text":"y=\ln \dfrac{1}{1-x}"}]}'
        good = json.dumps({"images": [
            {"id": "image_1", "readable": True, "text": r"y=\ln \dfrac{1}{1-x}, x \neq 1"},
        ]})
        client = ScriptedClient([bad, good, '{"Answer":["B"]}', verified()])
        self.assertEqual(validated_completion(client, question), ["B. two"])
        prompt, retry = client.calls[0][0], client.calls[1][0]
        self.assertIn(r"\\ln", prompt)
        self.assertIn(r"\\neq", prompt)
        self.assertIn("FORMAT_RETRY", retry)
        self.assertIn("Image stage must return one JSON object", retry)
        self.assertIn(r"\\ln", client.calls[2][0])
        rejected = client.last_diagnostics["stages"][0]
        self.assertEqual(rejected["validation_error"], "Image stage must return one JSON object")
        self.assertNotIn(bad, str(rejected))

    def test_silent_json_control_escapes_cannot_corrupt_formula_readings(self):
        question = prepared_question()
        for command in (r"\neq", r"\frac", r"\theta", r"\beta", r"\rho"):
            payload = (
                r'{"images":[{"id":"image_1","readable":true,"text":"x '
                + command + r' 1"}]}'
            )
            with self.subTest(command=command), self.assertRaisesRegex(WebAIError, "control characters"):
                read_images(payload, question)
        self.assertIn("DOUBLE every backslash", reading_prompt(question))

    def test_formula_answer_cannot_silently_lose_its_leading_latex_escape(self):
        question = prepared_question() | {"type": "completion"}
        bad = r'{"Answer":["\neq 1"]}'
        good = json.dumps({"Answer": [r"\neq 1"]})
        client = ScriptedClient([reading(question), bad, good, verified(Answer=[r"\neq 1"])])
        self.assertEqual(validated_completion(client, question), [r"\neq 1"])
        self.assertIn("control characters", client.last_diagnostics["stages"][1]["validation_error"])

    def test_verifier_must_supply_explicit_boolean_checks(self):
        question = prepared_question()
        client = ScriptedClient([
            reading(question), '{"Answer":["B"]}', '{"Answer":["B"]}',
            verified(reading_matches="true"),
        ])
        with self.assertRaises(InvalidAnswerError):
            validated_completion(client, question)
        self.assertEqual(len(client.calls), 4)

    def test_generation_failure_is_not_retried_and_keeps_stage_diagnostics(self):
        question = prepared_question()
        client = ScriptedClient([reading(question), GenerationError("Synthetic timeout")])
        with self.assertRaises(GenerationError):
            validated_completion(client, question)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.last_diagnostics["stages"][-1]["stage"], "solving")
        self.assertEqual(client.last_diagnostics["stages"][-1]["status"], "generation_failed")

    def test_expired_pipeline_never_accepts_a_late_answer(self):
        question = prepared_question()
        client = ScriptedClient([reading(question)])
        now = [0]
        original = client.complete_stage
        def slow(*args, **kwargs):
            answer = original(*args, **kwargs)
            now[0] = 601
            return answer
        client.complete_stage = slow
        with patch("api.vision_pipeline.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(GenerationError):
                validated_completion(client, question)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.last_diagnostics["quality"], "candidate")

    def test_cancellation_stops_before_first_request_and_between_stages(self):
        question = prepared_question()
        client = ScriptedClient([reading(question)])
        with self.assertRaises(WebAIError):
            validated_completion(client, question, cancelled=lambda: True)
        self.assertEqual(client.calls, [])
        stages = []
        with self.assertRaises(WebAIError):
            validated_completion(client, question, cancelled=lambda: len(stages) > 1,
                                 on_stage=stages.append)
        self.assertEqual(len(client.calls), 1)

    def test_multiple_choice_order_is_ignored_but_text_symbols_are_not(self):
        question = prepared_question() | {"type": "multiple"}
        client = ScriptedClient([
            reading(question), '{"Answer":["A","B"]}',
            '{"readable":true,"reading_matches":true,"Answer":["B","A"]}',
        ])
        self.assertEqual(validated_completion(client, question), ["A. one", "B. two"])
        question["type"] = "completion"
        self.assertNotEqual(answer_identity(["X"], question), answer_identity(["x"], question))
        self.assertNotEqual(answer_identity(["x - 1"], question), answer_identity(["x + 1"], question))

    def test_all_question_types_use_the_same_original_image_checks(self):
        for kind, answers, expected in (
            ("judgement", [True], ["true"]), ("completion", ["5", "9"], ["5", "9"]),
            ("shortanswer", ["A short explanation"], ["A short explanation"]),
        ):
            question = prepared_question() | {"type": kind}
            client = ScriptedClient([
                reading(question), json.dumps({"Answer": answers}), verified(Answer=answers),
            ])
            with self.subTest(kind=kind):
                self.assertEqual(validated_completion(client, question), expected)
                self.assertEqual(client.last_diagnostics["quality"], IMAGE_QUALITY)

    def test_text_questions_keep_the_existing_single_call_path(self):
        question = {"title": "Practice", "type": "single", "options": "A. one\nB. two"}
        client = Mock(complete=Mock(return_value='{"Answer":["B"]}'))
        self.assertEqual(validated_completion(client, question), ["B. two"])
        client.complete.assert_called_once_with(question)


class VisionBankTests(ImageBankFixtures, unittest.TestCase):
    def test_rate_limited_images_do_not_block_independent_text_questions(self):
        job = self.job()
        client = ScriptedClient([RateLimitedError("Upstream rate limited")])
        client.complete = Mock(return_value='{"Answer":["B"]}')
        self.service.broker.client_factory = lambda: client
        image = self.service.broker.request(job["id"], prepared_question())
        text = self.service.broker.request(job["id"], {
            "title": "Independent text", "type": "single", "options": "A. one\nB. two",
        })
        self.service.broker.start()
        value = self.wait_answer(job["id"], image["id"])
        self.assertEqual(value["error"], "AI_RATE_LIMITED")
        self.assertIsNone(value["answer"])
        self.assertEqual(self.wait_answer(job["id"], text["id"])["state"], "done")
        self.assertEqual(self.service.broker.cooldown_until, 0)
        self.assertEqual(self.service.image_review.detail(image["id"])["diagnostics"]["error"],
                         "AI_RATE_LIMITED")

    def test_crosschecked_result_is_shared_without_another_generation(self):
        first, second = self.job(), self.job()
        question = prepared_question()
        result = self.service.broker.request(first["id"], question)
        self.store.finish_answer(
            result["id"], '{"Answer":["B"]}', quality=IMAGE_QUALITY,
            diagnostics={"quality": IMAGE_QUALITY, "stage": "done", "agreement": True},
        )
        reused = self.service.broker.request(second["id"], question)
        self.assertEqual((reused["id"], reused["source"], reused["validation"]),
                         (result["id"], "bank", IMAGE_QUALITY))
        detail = self.service.question_bank.detail(result["id"])
        self.assertTrue(detail["reusable"])
        self.assertTrue(detail["diagnostics"]["agreement"])
        self.assertEqual(self.calls, [])

    def test_retired_candidates_retain_old_waiters_history_and_original_images(self):
        first, second = self.job(), self.job()
        question = prepared_question()
        old = self.service.broker.request(first["id"], question)
        self.store.finish_answer(old["id"], '{"Answer":["A"]}')
        new = self.service.broker.request(second["id"], question)
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(json.loads(self.store.answer(first["id"], old["id"])["answer"]), {"Answer": ["A"]})
        detail = self.service.question_bank.detail(old["id"])
        self.assertTrue(detail["historical"])
        self.assertFalse(detail["reusable"])
        self.assertEqual(self.service.question_bank.image(old["id"], "image_1")["byte_count"],
                         question["images"][0]["byte_count"])
        self.assertEqual(self.service.question_bank.stats()["candidate_entries"], 1)
        self.store.finish_answer(new["id"], '{"Answer":["B"]}', quality=IMAGE_QUALITY)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM answers").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM answer_images").fetchone()[0], 1)

    def test_full_queue_rolls_back_candidate_retirement(self):
        job = self.job()
        question = prepared_question()
        old = self.service.broker.request(job["id"], question)
        self.store.finish_answer(old["id"], '{"Answer":["B"]}')
        with self.assertRaises(Conflict):
            self.store.request_answer(job["id"], self.service.broker.key(question), question, queue_limit=0)
        with self.store.transaction() as db:
            row = db.execute("SELECT bank_key,cache_key FROM answers WHERE id=?", (old["id"],)).fetchone()
        self.assertEqual(row["bank_key"], self.service.broker.key(question))
        self.assertEqual(row["cache_key"], row["bank_key"])

    def test_disagreement_does_not_save_an_answer_or_block_the_text_queue(self):
        job = self.job()
        question = prepared_question()
        client = ScriptedClient([reading(question), '{"Answer":["B"]}', verified("A")])
        client.complete = Mock(return_value='{"Answer":["B"]}')
        self.service.broker.client_factory = lambda: client
        image = self.service.broker.request(job["id"], question)
        text = self.service.broker.request(job["id"], {
            "title": "Practice", "type": "single", "options": "A. one\nB. two",
        })
        self.service.broker.start()
        value = self.wait_answer(job["id"], image["id"])
        self.assertEqual(value["error"], "AI_VISION_DISAGREEMENT")
        self.assertIsNone(value["answer"])
        self.assertEqual(self.wait_answer(job["id"], text["id"])["state"], "done")
        self.assertEqual(self.service.broker.cooldown_until, 0)


class VisionMigrationTests(unittest.TestCase):
    def test_additive_migration_keeps_existing_answers_ids_and_quality_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets = Secrets(directory)
            path = Path(directory) / "service.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "CREATE TABLE answers(id TEXT PRIMARY KEY,cache_key TEXT UNIQUE NOT NULL,"
                    "question TEXT NOT NULL,state TEXT NOT NULL,answer TEXT,error TEXT NOT NULL DEFAULT '',"
                    "created_at REAL NOT NULL,updated_at REAL NOT NULL)"
                )
                db.execute(
                    "INSERT INTO answers VALUES('legacy','legacy-key',?,'done',?,'',1,2)",
                    (json.dumps({"type": "single", "title": "Legacy", "options": "A. one\nB. two",
                                 "course_id": "101"}), '{"Answer":["B"]}'),
                )
            store = Store(directory, secrets)
            try:
                with store.transaction() as db:
                    row = db.execute("SELECT * FROM answers WHERE id='legacy'").fetchone()
                self.assertEqual(row["answer_quality"], "format_checked")
                self.assertEqual(row["diagnostics"], "{}")
                self.assertEqual(json.loads(row["answer"]), {"Answer": ["B. two"]})
                self.assertIsNotNone(row["bank_key"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
