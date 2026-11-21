import configparser
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from api.answer import CacheDAO, Tiku, WebAI
from api.base import Chaoxing, StudyResult
from api.decode import decode_questions_info
from api.webai_client import (
    WebAIClient,
    WebAIError,
    answer_prompt,
    parse_answers,
    platform_answer,
    question_bank_key,
    InvalidAnswerError,
    validated_completion,
)
import requests
from webai_check import main as check_main
from api.webai_errors import QuestionImageUnavailable, RateLimitedError


ROOT = Path(__file__).resolve().parents[1]
QUESTION = {"title": "2026 practice: 1 + 1 = ?", "type": "single", "options": "A. 1\nB. 2"}
TOKEN = "local-test-token-not-a-real-credential"


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_body(self, status, content, content_type="application/json"):
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def authorized(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_body(401, '{"error":{"code":"UNAUTHORIZED"}}')
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path != "/v1/models":
            self.send_body(404, "{}")
            return
        self.send_body(200, json.dumps({"data": [{"id": value} for value in self.server.models]}))

    def do_POST(self):
        if not self.authorized():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received.append(body)
        if self.path != "/v1/chat/completions":
            self.send_body(404, "{}")
            return
        status = self.server.model_failures.get(body["model"], self.server.status)
        if status != 200:
            self.send_body(status, '{"error":{"message":"do not echo remote secrets"}}')
            return
        content = self.server.answer
        if body["stream"] and self.server.use_sse:
            if self.server.error:
                events = [":keepalive", "", "data: " + json.dumps({"error": self.server.error})]
            else:
                midpoint = len(content) // 2
                chunks = [content[:midpoint], content[midpoint:]]
                events = [":keepalive", ""]
                events.append("data: " + json.dumps({
                    "choices": [{"index": 0, "delta": {"reasoning_content": "not an answer"}}]
                }))
                for index, chunk in enumerate(chunks):
                    events.extend(["data: " + json.dumps({
                        "choices": [{"index": 0, "delta": {"content": chunk},
                                     "finish_reason": "stop" if index == 1 else None}]
                    }, ensure_ascii=False), ""])
            if self.server.done:
                events.extend(["data: [DONE]", ""])
            self.send_body(200, "\n".join(events) + "\n", "text/event-stream")
        else:
            payload = {"choices": [{"message": {"content": content},
                                    "finish_reason": self.server.finish_reason}]}
            self.send_body(200, json.dumps(payload, ensure_ascii=False))


class BridgeCase(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        self.server.daemon_threads = True
        self.server.models = ["deepseek", "deepseek-search"]
        self.server.received = []
        self.server.status = 200
        self.server.model_failures = {}
        self.server.answer = '{"Answer":["B"]}'
        self.server.use_sse = True
        self.server.done = True
        self.server.error = None
        self.server.finish_reason = "stop"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {
            "provider": "WebAI", "submit": "false", "cover_rate": "1",
            "true_list": "true", "false_list": "false",
            "webai_endpoint": f"http://127.0.0.1:{self.server.server_port}/v1",
            "webai_key_env": "UNUSED_WEBAI_TEST_KEY", "webai_key": TOKEN,
            "webai_model": "deepseek-search", "webai_stream": "true",
            "webai_timeout": "10", "min_interval_seconds": "0",
            "webai_cache": str(Path(self.temp.name) / "cache.json"),
        }
        self.env = patch.dict(os.environ, {"UNUSED_WEBAI_TEST_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def client(self, **updates):
        client = WebAIClient(self.config | updates)
        self.addCleanup(client.close)
        return client

    def provider(self, **updates):
        tiku = Tiku()
        tiku.config_set(self.config | updates)
        provider = tiku.get_tiku_from_config()
        provider.init_tiku()
        self.addCleanup(provider.client.close)
        return provider

    def test_models_and_streaming_round_trip(self):
        client = self.client()
        self.assertIn("deepseek-search", client.models())
        result = client.complete(QUESTION)
        self.assertEqual(platform_answer(result, QUESTION), "B")
        request = self.server.received[0]
        self.assertEqual(request["model"], "deepseek-search")
        self.assertTrue(request["stream"])
        self.assertEqual(request["messages"][0]["role"], "user")
        self.assertIn("2026 practice", request["messages"][0]["content"])
        self.assertIn('{"Answer":["answer"]}', request["messages"][0]["content"])
        self.assertFalse(client.session.trust_env)

    def test_utf8_streaming_and_thinking_are_separate(self):
        question = QUESTION | {"options": "A. \u5317\u4eac\nB. \u4e0a\u6d77"}
        self.server.answer = '{"Answer":["\u4e0a\u6d77"]}'
        self.assertEqual(platform_answer(self.client().complete(question), question), "B")

    def test_nonstreaming_and_json_fallback(self):
        for stream, use_sse in [("false", True), ("true", False)]:
            with self.subTest(stream=stream, use_sse=use_sse):
                self.server.use_sse = use_sse
                self.assertEqual(platform_answer(self.client(webai_stream=stream).complete(QUESTION), QUESTION), "B")

    def test_authentication_and_redirects_fail_closed(self):
        for status in [301, 401, 403, 429, 503]:
            with self.subTest(status=status):
                self.server.status = status
                with self.assertRaises(WebAIError) as error:
                    self.client().complete(QUESTION)
                self.assertNotIn("remote secrets", str(error.exception))
        with self.assertRaises(WebAIError):
            self.client(webai_key="wrong-token").models()

    def test_sse_error_does_not_leak_remote_data(self):
        self.server.error = {"message": "captcha login expired secret-cookie"}
        with self.assertRaisesRegex(WebAIError, "manual login") as error:
            self.client().complete(QUESTION)
        self.assertNotIn("secret-cookie", str(error.exception))

    def test_remote_endpoint_repeats_its_own_error_reason(self):
        """远端 API 的 404 自带原因（例如模型下线），要转述给用户。"""

        class FakeResponse:
            status_code = 404

            @staticmethod
            def json():
                return {"error": {"message": (
                    "The free launch period ended. The `:free` variant has been retired; "
                    "use minimax/minimax-m3.1-flash instead."
                )}}

        detail = WebAIClient._upstream_detail(FakeResponse())
        self.assertIn("retired", detail)
        self.assertIn("minimax/minimax-m3.1-flash", detail)
        with self.assertRaises(WebAIError) as raised:
            WebAIClient._check_status(FakeResponse(), remote=True)
        self.assertIn("the AI endpoint said:", str(raised.exception))
        self.assertIn("retired", str(raised.exception))

    def test_local_bridge_errors_still_point_at_the_logs(self):
        with self.assertRaises(WebAIError) as raised:
            WebAIClient._check_status(SimpleNamespace(status_code=404, json=lambda: {}))
        self.assertIn("check bridge logs", str(raised.exception))
        self.assertNotIn("the AI endpoint said", str(raised.exception))

    def test_upstream_detail_redacts_credentials_and_stays_short(self):
        class FakeResponse:
            status_code = 402

            @staticmethod
            def json():
                return {"error": {"message": (
                    "Insufficient credits: add funds with key sk-or-v1-0123456789abcdef "
                    + "x" * 400
                )}}

        detail = WebAIClient._upstream_detail(FakeResponse())
        self.assertNotIn("sk-or-v1-0123456789abcdef", detail)
        self.assertIn("<redacted>", detail)
        self.assertLessEqual(len(detail), 200)

    def test_unreadable_error_body_falls_back_to_the_logs(self):
        class FakeResponse:
            status_code = 502

            @staticmethod
            def json():
                raise ValueError("not json")

        self.assertEqual(WebAIClient._upstream_detail(FakeResponse()), "")
        with self.assertRaises(WebAIError) as raised:
            WebAIClient._check_status(FakeResponse(), remote=True)
        self.assertIn("check bridge logs", str(raised.exception))

    def test_upstream_rate_limit_never_falls_back_or_exposes_remote_data(self):
        client = self.client(webai_model_fallbacks=["deepseek"])
        for error in (
            {"code": "RATE_LIMITED", "message": "private-upstream-session"},
            {"code": "GENERATION_FAILED", "message": "wrapped RATE_LIMITED: private-upstream-session"},
        ):
            self.server.received.clear()
            self.server.error = error
            with self.subTest(error=error), self.assertRaises(RateLimitedError) as raised:
                validated_completion(client, QUESTION)
            self.assertNotIn("private-upstream-session", str(raised.exception))
            self.assertEqual([item["model"] for item in self.server.received], ["deepseek-search"])
        self.server.error = None
        self.server.status = 429
        self.server.received.clear()
        with self.assertRaises(RateLimitedError):
            client.complete(QUESTION)
        self.assertEqual(len(self.server.received), 1)

    def test_truncated_stream_and_generation_failures_are_rejected(self):
        self.server.done = False
        with self.assertRaisesRegex(WebAIError, "completion marker"):
            self.client().complete(QUESTION)
        self.server.use_sse = False
        self.server.finish_reason = "length"
        with self.assertRaisesRegex(WebAIError, "truncated"):
            self.client().complete(QUESTION)

    def test_total_deadline_includes_heartbeats(self):
        response = Mock()
        response.iter_lines.return_value = iter([":keepalive", "data: [DONE]"])
        with patch("api.webai_client.time.monotonic", return_value=100):
            with self.assertRaisesRegex(WebAIError, "deadline"):
                self.client()._stream_content(response, started=0)

    def test_rate_limit_is_applied_before_the_next_request(self):
        client = self.client(min_interval_seconds="3")
        client._last_finished = 100
        with patch("api.webai_client.time.monotonic", return_value=101), \
                patch("api.webai_client.time.sleep") as sleep:
            client.complete(QUESTION)
        sleep.assert_called_once_with(2)

    def test_transport_failure_is_not_retried_or_exposed(self):
        client = self.client()
        with patch.object(client.session, "post", side_effect=requests.Timeout("private-token")) as post:
            with self.assertRaisesRegex(WebAIError, "timed out") as error:
                client.complete(QUESTION)
        self.assertNotIn("private-token", str(error.exception))
        post.assert_called_once()

    def test_slow_connect_is_retried_before_the_question_is_dropped(self):
        client = self.client(webai_timeout="120", webai_connect_attempts="2",
                             webai_connect_retry_delay="0")
        self.assertEqual(client.connect_timeout, 25)
        failures = [requests.exceptions.ConnectTimeout("handshake stalled")]

        def post(*args, **kwargs):
            if failures:
                raise failures.pop()
            raise requests.exceptions.ReadTimeout("upstream stayed silent")

        with patch("api.webai_client.time.sleep") as sleep, \
                patch.object(client.session, "post", side_effect=post) as call:
            with self.assertRaisesRegex(WebAIError, "timed out"):
                client.complete(QUESTION)
        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once()
        # The connect timeout is the widened one, the read timeout still follows the deadline.
        self.assertEqual(call.call_args.kwargs["timeout"][0], 25)
        self.assertGreater(call.call_args.kwargs["timeout"][1], 25)

    def test_connect_timeout_never_exceeds_the_remaining_deadline(self):
        client = self.client(webai_connect_attempts="2", webai_connect_retry_delay="0")
        with patch("api.webai_client.time.sleep"), \
                patch.object(client.session, "post",
                             side_effect=requests.exceptions.ConnectTimeout("no route")) as call:
            with self.assertRaises(WebAIError):
                client.complete(QUESTION)
        self.assertLessEqual(call.call_args.kwargs["timeout"][0], client.timeout)

    def test_exhausted_connect_retries_reach_the_conversation_log(self):
        records = []
        client = self.client(webai_connect_attempts="2", webai_connect_retry_delay="0",
                             webai_recorder=records.append)
        with patch("api.webai_client.time.sleep"), \
                patch.object(client.session, "post",
                             side_effect=requests.exceptions.ConnectTimeout("no route")):
            with self.assertRaises(WebAIError):
                client.complete(QUESTION)
        self.assertEqual(len(records), 1)
        self.assertIn("连接 AI 接口超时", records[0]["error"])
        self.assertIn("已试 2 次", records[0]["error"])
        self.assertEqual(records[0]["response"], "")

    def test_single_connect_attempt_configuration_is_honoured(self):
        client = self.client(webai_connect_attempts="1", webai_connect_retry_delay="0")
        with patch("api.webai_client.time.sleep") as sleep, \
                patch.object(client.session, "post",
                             side_effect=requests.exceptions.ConnectTimeout("no route")) as post:
            with self.assertRaises(WebAIError):
                client.complete(QUESTION)
        post.assert_called_once()
        sleep.assert_not_called()

    def test_connect_policy_is_validated_and_tunable_from_the_environment(self):
        for extra in ({"webai_connect_timeout": "0"}, {"webai_connect_timeout": "600"},
                      {"webai_connect_attempts": "0"}, {"webai_connect_attempts": "5"},
                      {"webai_connect_attempts": "1.5"},
                      {"webai_connect_retry_delay": "-1"}, {"webai_connect_timeout": "soon"}):
            with self.subTest(extra=extra), self.assertRaises(WebAIError):
                WebAIClient(self.config | extra)
        with patch.dict(os.environ, {"CHAOXING_AI_CONNECT_TIMEOUT": "40",
                                     "CHAOXING_AI_CONNECT_ATTEMPTS": "3",
                                     "CHAOXING_AI_CONNECT_RETRY_DELAY": "7"}):
            tuned = self.client()
        self.assertEqual((tuned.connect_timeout, tuned.connect_attempts, tuned.connect_retry_delay),
                         (40, 3, 7))
        self.assertEqual(self.client(webai_connect_timeout="12").connect_timeout, 12)

    def test_configured_text_fallback_preserves_request_and_redacts_errors(self):
        self.server.model_failures["deepseek-search"] = 503
        client = self.client(webai_model_fallbacks='["deepseek","deepseek"]')
        with self.assertLogs("api.webai_client", level="INFO") as logs:
            self.assertEqual(platform_answer(client.complete(QUESTION), QUESTION), "B")
        self.assertEqual([item["model"] for item in self.server.received], ["deepseek-search", "deepseek"])
        self.assertEqual(self.server.received[0]["messages"], self.server.received[1]["messages"])
        self.assertEqual(client.selected_model, "deepseek")
        self.assertNotIn("remote secrets", str(logs.output))
        self.assertNotIn(TOKEN, str(logs.output))
        self.assertIn("fallback=True", str(logs.output))

    def test_authentication_and_redirect_errors_never_try_another_model(self):
        for status in (301, 401, 403):
            self.server.received.clear()
            self.server.status = status
            with self.subTest(status=status), self.assertRaises(WebAIError):
                self.client(webai_model_fallbacks=["deepseek"]).complete(QUESTION)
            self.assertEqual([item["model"] for item in self.server.received], ["deepseek-search"])

    def test_transport_fallbacks_share_one_total_deadline(self):
        client = self.client(webai_model_fallbacks=["deepseek"])
        clock = [100.0]
        def timeout(*args, **kwargs):
            clock[0] += client.timeout + 1
            raise requests.Timeout("private-upstream-secret")
        with patch("api.webai_client.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(client.session, "post", side_effect=timeout) as post:
            with self.assertRaisesRegex(WebAIError, "deadline") as error:
                client.complete(QUESTION)
        post.assert_called_once()
        self.assertNotIn("private-upstream-secret", str(error.exception))

    def test_local_cache_identity_includes_only_its_routes_fallback_configuration(self):
        baseline = self.client()
        fallback = self.client(webai_model_fallbacks='["deepseek"]')
        other_route = self.client(webai_vision_model_fallbacks=["vision-backup"])
        self.assertTrue(fallback.cache_key(QUESTION).startswith("webai:v3:"))
        self.assertNotEqual(fallback.cache_key(QUESTION), baseline.cache_key(QUESTION))
        self.assertEqual(other_route.cache_key(QUESTION), baseline.cache_key(QUESTION))

    def test_malformed_json_responses_fail_as_bridge_errors(self):
        client = self.client(webai_stream="false")
        for payload in [
            {"choices": ["not-a-choice"]},
            {"choices": {"0": {"message": {"content": "bad"}}}},
            {"choices": [{"message": "not-a-message"}]},
            {"choices": []},
        ]:
            with self.subTest(payload=payload):
                response = Mock()
                response.status_code = 200
                response.headers = {"Content-Type": "application/json"}
                response.json.return_value = payload
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                with patch.object(client.session, "post", return_value=response):
                    with self.assertRaises(WebAIError):
                        client.complete(QUESTION)

    def test_provider_registration_and_cache_key_isolation(self):
        provider = self.provider()
        self.assertIsInstance(provider, WebAI)
        self.assertTrue(provider.PREVIEW_ONLY)
        result = provider.query(QUESTION)
        self.assertEqual(provider.to_platform_answer(QUESTION, result), "B")
        self.assertEqual(provider.query(QUESTION), result)
        self.assertEqual(len(self.server.received), 1)
        changed_options = QUESTION | {"options": "A. 2\nB. 1"}
        provider.query(changed_options)
        self.assertEqual(len(self.server.received), 2)
        client = provider.client
        self.assertNotEqual(client.cache_key(QUESTION), client.cache_key(changed_options))
        other = self.client(webai_model="deepseek")
        self.assertNotEqual(client.cache_key(QUESTION), other.cache_key(QUESTION))
        self.assertTrue(client.cache_key(QUESTION).startswith("webai:v2:"))

    def test_invalid_answer_is_not_cached(self):
        provider = self.provider()
        self.server.answer = '{"Answer":["C"]}'
        self.assertIsNone(provider.query(QUESTION))
        self.assertIsNone(CacheDAO(provider.cache_file).get_cache(provider.client.cache_key(QUESTION)))
        self.server.answer = '{"Answer":["2"]}'
        self.assertIsNotNone(provider.query(QUESTION))
        self.assertEqual(len(self.server.received), 4)

    def test_image_options_are_preserved_but_untrusted_sources_are_not_sent(self):
        html = """
        <form>
          <div class="singleQuesId" data="1">
            <div class="TiMu" data="0">
              <div class="Zy_TItle">2026: Which diagram matches?</div>
              <ul>
                <li aria-label="A. one"><img src="https://example.invalid/one.png"></li>
                <li aria-label="B. two">B. two</li>
              </ul>
            </div>
          </div>
        </form>
        """
        question = decode_questions_info(html)["questions"][0]
        self.assertNotIn("<img", question["options"])
        self.assertTrue(question["has_images"])
        self.assertIn("A. one [image_1]", question["options"])
        self.assertEqual(question["images"], [
            {"id": "image_1", "source": "https://example.invalid/one.png"},
        ])
        provider = self.provider()
        self.assertIsNone(provider.query(question))
        self.assertEqual(self.server.received, [])
        with self.assertRaisesRegex(WebAIError, "fingerprints"):
            provider.client.cache_key(question)
        self.assertFalse(Path(provider.cache_file).exists())

    def test_bad_cached_answer_is_revalidated(self):
        provider = self.provider()
        CacheDAO(provider.cache_file).add_cache(provider.client.cache_key(QUESTION), '{"Answer":["Z"]}')
        self.assertEqual(provider.to_platform_answer(QUESTION, provider.query(QUESTION)), "B")
        self.assertEqual(len(self.server.received), 1)

    def test_concurrent_identical_questions_only_use_one_request(self):
        provider = self.provider()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(provider.query, [QUESTION] * 4))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.server.received), 1)

    def test_broker_mode_always_uses_sqlite_instead_of_a_worker_json_cache(self):
        provider = self.provider(
            webai_broker_endpoint="http://127.0.0.1:3100/internal", webai_broker_token="offline-only",
        )
        provider.client._request = Mock(return_value={
            "id": "bank-answer", "state": "done", "source": "bank", "answer": '{"Answer":["B. 2"]}',
        })
        with patch("api.answer.CacheDAO", side_effect=AssertionError("Worker JSON cache was accessed")):
            self.assertEqual(provider.to_platform_answer(QUESTION, provider.query(QUESTION)), "B")
            self.assertEqual(provider.to_platform_answer(QUESTION, provider.query(QUESTION)), "B")
        self.assertEqual(provider.client._request.call_count, 2)
        self.assertEqual(self.server.received, [])
        self.assertFalse(Path(provider.cache_file).exists())

    def test_check_cli_does_not_contact_course_platform(self):
        path = Path(self.temp.name) / "config.ini"
        config = configparser.ConfigParser()
        config["tiku"] = self.config
        with path.open("w", encoding="utf-8") as stream:
            config.write(stream)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(check_main(["--config", str(path), "--models"]), 0)
        self.assertTrue(json.loads(output.getvalue())["model_available"])
        self.assertEqual(self.server.received, [])
        output = io.StringIO()
        with redirect_stdout(output):
            status = check_main(["--config", str(path), "--question", QUESTION["title"],
                                 "--option", "A. 1", "--option", "B. 2"])
        self.assertEqual(status, 0)
        self.assertFalse(json.loads(output.getvalue())["course_changed"])
        self.assertNotIn(TOKEN, output.getvalue())
        self.server.models = []
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(check_main(["--config", str(path), "--models"]), 1)


class QuestionBankIdentityTests(unittest.TestCase):
    def setUp(self):
        self.question = QUESTION | {"course_id": "267851788"}

    def key(self, **updates):
        return question_bank_key(self.question | updates)

    def test_type_prefix_and_question_number_do_not_split_the_same_question(self):
        expected = self.key()
        for title in (
            "1【单选题】" + QUESTION["title"],
            "9【单选题】  " + QUESTION["title"] + "  ",
            "【单项选择题】" + QUESTION["title"],
        ):
            with self.subTest(title=title):
                self.assertEqual(self.key(title=title), expected)
        self.assertEqual(self.key(options=["A. 1", "B. 2"]), expected)
        self.assertEqual(self.key(options="A 1\r\nB 2"), expected)

    def test_course_type_and_complete_ordered_options_are_part_of_the_identity(self):
        original = self.key()
        for change in (
            {"course_id": "100"}, {"type": "multiple"}, {"options": "A. 2\nB. 1"},
            {"options": "A. 1\nB. 3"}, {"options": "B. 2\nA. 1"},
        ):
            with self.subTest(change=change):
                self.assertNotEqual(self.key(**change), original)

    def test_meaningful_brackets_numbers_math_and_code_whitespace_are_preserved(self):
        for left, right in (
            ("【案例甲】 question", "【案例乙】 question"),
            ("1 question", "2 question"),
            ("x² = ?", "x2 = ?"),
            ("print('a  b')", "print('a b')"),
            ("A\nB", "A B"),
            ("【判断题】 question", "question"),
            ("【单选题】【单选题】 question", "【单选题】 question"),
        ):
            with self.subTest(left=left, right=right):
                self.assertNotEqual(self.key(title=left), self.key(title=right))
        self.assertNotEqual(
            self.key(options="A. x²\nB. 2"), self.key(options="A. x2\nB. 2"),
        )

    def test_missing_or_invalid_course_context_and_image_questions_are_rejected(self):
        for course_id in ("", "１", "1 ", "x", "1" * 21, None, 1, []):
            with self.subTest(course_id=course_id), self.assertRaises(WebAIError):
                self.key(course_id=course_id)
        with self.assertRaises(WebAIError):
            self.key(has_images=True)
        with self.assertRaises(WebAIError):
            self.key(options=["A. 1", 2])


class AnswerValidationTests(unittest.TestCase):
    def test_choice_labels_content_and_fences(self):
        for value in ["B", "2", "B. 2", "B\u30012"]:
            with self.subTest(value=value):
                payload = json.dumps({"Answer": [value]})
                self.assertEqual(platform_answer(payload, QUESTION), "B")
                self.assertEqual(platform_answer("```json\n" + payload + "\n```", QUESTION), "B")
        multi = QUESTION | {"type": "multiple", "options": "A. red\nB. blue\nC. green"}
        self.assertEqual(platform_answer('{"Answer":["C","A"]}', multi), "AC")

    def test_bad_json_shape_and_ambiguous_answers(self):
        invalid = [
            "Answer: B", '{"Answer":[]}', '{"Answer":"B"}', '{"Answer":[true]}',
            '{"Answer":[2]}', '{"Answer":[""]}', '{"Answer":["A","B"]}',
            '{"Answer":["C"]}', '{"Answer":["B. 1"]}', '{}',
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(WebAIError):
                    parse_answers(payload, QUESTION)
        ambiguous = QUESTION | {"options": "A. same\nB. same"}
        # 裸数组/单引号/包裹语都算同一种答案，别因为格式差异丢掉正确答案
        for payload in ('["B"]', "{'Answer': ['B']}", '答案是 {"Answer": ["B"]}。'):
            with self.subTest(payload=payload):
                self.assertEqual(parse_answers(payload, QUESTION), ["B. 2"])
        with self.assertRaises(WebAIError):
            parse_answers('{"Answer":["same"]}', ambiguous)
        self.assertEqual(platform_answer('{"Answer":["B"]}', ambiguous), "B")
        multi = QUESTION | {"type": "multiple"}
        with self.assertRaises(WebAIError):
            parse_answers('{"Answer":["B","B"]}', multi)

    def test_judgement_and_text_question_types(self):
        question = QUESTION | {"type": "judgement", "options": ""}
        for word in ["true", "correct", "\u6b63\u786e", "\u5bf9"]:
            self.assertEqual(platform_answer(json.dumps({"Answer": [word]}), question), "true")
        self.assertEqual(platform_answer('{"Answer":["false"]}', question), "false")
        with self.assertRaises(WebAIError):
            parse_answers('{"Answer":["maybe"]}', question)
        completion = QUESTION | {"type": "completion", "options": ""}
        self.assertEqual(platform_answer('{"Answer":["one","two"]}', completion), "one\ntwo")
        short = QUESTION | {"type": "shortanswer", "options": ""}
        self.assertEqual(platform_answer('{"Answer":["A short explanation."]}', short), "A short explanation.")

    def test_judgement_accepts_actual_labeled_options_and_json_booleans(self):
        question = QUESTION | {"type": "judgement", "options": "\u5bf9\n\u9519"}
        for value, expected in [
            ("B. \u9519", "false"), ("A. \u5bf9", "true"), ("B", "false"),
            ("\uff22\uff0e \u9519", "false"), (True, "true"), (False, "false"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(platform_answer(json.dumps({"Answer": [value]}), question), expected)
        reversed_options = question | {"options": "A. \u9519\nB. \u5bf9"}
        self.assertEqual(platform_answer('{"Answer":["A"]}', reversed_options), "false")
        self.assertEqual(platform_answer('{"Answer":["B. \u5bf9"]}', reversed_options), "true")
        for value in ("A. \u9519", "B. \u5bf9", "\u5bf9\u6216\u9519", "probably true"):
            with self.subTest(value=value), self.assertRaises(WebAIError):
                parse_answers(json.dumps({"Answer": [value]}), question)
        with self.assertRaises(WebAIError):
            parse_answers('{"Answer":["B"]}', question | {"options": ""})

    def test_invalid_generations_retry_but_transport_failures_do_not(self):
        client = Mock()
        client.complete.side_effect = ['not-json', '{"Answer":["B"]}']
        self.assertEqual(validated_completion(client, QUESTION), ["B. 2"])
        self.assertEqual(client.complete.call_count, 2)
        client.reset_mock()
        client.complete.side_effect = WebAIError("transport unavailable")
        with self.assertRaises(WebAIError):
            validated_completion(client, QUESTION)
        client.complete.assert_called_once()
        client.reset_mock()
        client.complete.side_effect = None
        client.complete.return_value = '{"Answer":["Z"]}'
        with self.assertRaises(InvalidAnswerError):
            validated_completion(client, QUESTION)
        self.assertEqual(client.complete.call_count, 3)

    def test_cancelled_generation_does_not_start_another_format_retry(self):
        cancelled = threading.Event()
        client = Mock()
        def complete(_):
            cancelled.set()
            return "invalid"
        client.complete.side_effect = complete
        with self.assertRaisesRegex(WebAIError, "cancelled"):
            validated_completion(client, QUESTION, cancelled=cancelled.is_set)
        client.complete.assert_called_once()

    def test_images_unknown_types_and_missing_options_do_not_reach_ai(self):
        cases = [
            QUESTION | {"title": '<img src="anything">'},
            QUESTION | {"type": "unknown"},
            QUESTION | {"options": ""},
            QUESTION | {"options": ["A. one", 2]},
            QUESTION | {"title": ""},
            QUESTION | {"options": "A. one\nA. two"},
        ]
        for question in cases:
            with self.subTest(question=question):
                with self.assertRaises(WebAIError):
                    answer_prompt(question)

    def test_invalid_configuration_and_environment_key(self):
        base = {"webai_key": TOKEN, "webai_key_env": "UNUSED_WEBAI_TEST_KEY"}
        for extra in [
            {"webai_endpoint": "ftp://localhost"},
            {"webai_endpoint": "http://user:password@localhost/v1"},
            {"webai_endpoint": "http://localhost/v1?token=secret"},
            {"webai_endpoint": "http://localhost/v1/chat/completions"},
            {"webai_timeout": "-1"}, {"webai_timeout": "nan"},
            {"min_interval_seconds": "-1"}, {"webai_stream": "maybe"}, {"webai_model": ""},
            {"webai_model": "unsafe\nmodel"}, {"webai_model_fallbacks": "backup"},
            {"webai_model_fallbacks": {}}, {"webai_model_fallbacks": [""]},
            {"webai_model_fallbacks": ["one", "two", "three", "four", "five", "six", "seven"]},
            {"webai_vision_model_fallbacks": '["unsafe\\nmodel"]'},
        ]:
            with self.subTest(extra=extra), patch.dict(os.environ, {"UNUSED_WEBAI_TEST_KEY": ""}):
                with self.assertRaises(WebAIError):
                    WebAIClient(base | extra)
        with patch.dict(os.environ, {"UNUSED_WEBAI_TEST_KEY": ""}):
            with self.assertRaises(WebAIError):
                WebAIClient({"webai_key_env": "UNUSED_WEBAI_TEST_KEY"})
        with patch.dict(os.environ, {"UNUSED_WEBAI_TEST_KEY": TOKEN}):
            client = WebAIClient({"webai_key_env": "UNUSED_WEBAI_TEST_KEY"})
            self.assertEqual(client.endpoint, "http://127.0.0.1:3000/v1")
            client.close()


class CourseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.get.return_value.status_code = 200
        self.session.get.return_value.text = "<form>local test fixture</form>"
        self.session.post.return_value.status_code = 200
        self.session.post.return_value.json.return_value = {"status": True, "msg": "ok"}
        self.question = QUESTION | {
            "id": "1", "answerField": {"answer1": "", "answertype1": "0"},
        }
        self.tiku = SimpleNamespace(
            DISABLE=False, STRICT_ANSWERS=True, PREVIEW_ONLY=True, COVER_RATE=1,
            query=Mock(return_value='{"Answer":["B. 2"]}'),
            to_platform_answer=Mock(return_value="B"),
            get_submit_params=Mock(return_value="1"),
        )
        self.session_patch = patch("api.base.SessionManager.get_session", return_value=self.session)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.decode_patch = patch("api.base.decode_questions_info", return_value={
            "questions": [copy.deepcopy(self.question)]
        })
        self.decode_patch.start()
        self.addCleanup(self.decode_patch.stop)

    def run_work(self):
        return Chaoxing(tiku=self.tiku).study_work(
            {"courseId": "test-course", "clazzId": "test-class"},
            {"jobid": "work-test", "enc": "test-enc"},
            {"knowledgeid": "test-chapter", "ktoken": "test-ktoken", "cpi": "test-cpi"},
        )

    def test_preview_does_not_save_or_submit(self):
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.session.post.assert_not_called()

    def test_missing_or_unmapped_answer_never_saves_even_if_submit_is_enabled(self):
        self.tiku.PREVIEW_ONLY = False
        self.tiku.get_submit_params.return_value = ""
        self.tiku.query.return_value = None
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_not_called()
        self.tiku.query.return_value = '{"Answer":["not-an-option"]}'
        self.tiku.to_platform_answer.return_value = ""
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_not_called()

    def test_valid_answer_saves_only_when_preview_is_disabled(self):
        self.tiku.PREVIEW_ONLY = False
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        payload = self.session.post.call_args.kwargs["data"]
        self.assertEqual(payload["answer1"], "B")
        self.assertEqual(payload["pyFlag"], "1")

    def test_later_failure_discards_all_platform_writes(self):
        self.tiku.PREVIEW_ONLY = False
        self.tiku.get_submit_params.return_value = ""
        self.tiku.query.side_effect = ['{"Answer":["B. 2"]}', None]
        second = self.question | {
            "id": "2",
            "title": "Second practice question",
            "answerField": {"answer2": "", "answertype2": "0"},
        }
        with patch("api.base.decode_questions_info", return_value={
            "questions": [copy.deepcopy(self.question), second]
        }):
            self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.assertEqual(self.tiku.query.call_count, 2)
        self.session.post.assert_not_called()

    def test_explicit_submission_and_legacy_provider_remain_supported(self):
        self.tiku.PREVIEW_ONLY = False
        self.tiku.get_submit_params.return_value = ""
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.assertEqual(self.session.post.call_args.kwargs["data"]["pyFlag"], "")
        self.tiku = SimpleNamespace(
            DISABLE=False, COVER_RATE=1, query=Mock(return_value="2"),
            get_submit_params=Mock(return_value="1"),
        )
        self.decode_patch.stop()
        self.decode_patch = patch("api.base.decode_questions_info", return_value={
            "questions": [copy.deepcopy(self.question)]
        })
        self.decode_patch.start()
        self.addCleanup(self.decode_patch.stop)
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.assertEqual(self.session.post.call_args.kwargs["data"]["answer1"], "B")

    def test_disabled_provider_is_not_called(self):
        self.tiku.DISABLE = True
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.session.get.assert_not_called()
        self.tiku = None
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)

    def test_an_unreadable_question_image_is_skipped_not_fatal(self):
        """一张题图取不回来时，跳过这题并照常交卷，而不是让整章永久卡死。"""
        self.tiku.PREVIEW_ONLY = False
        self.tiku.get_submit_params.return_value = ""
        self.tiku.COVER_RATE = 1
        broken = copy.deepcopy(self.question)
        broken["id"] = "1"
        second = self.question | {
            "id": "2",
            "title": "Second practice question",
            "answerField": {"answer2": "", "answertype2": "0"},
        }

        def answer(q_info):
            if q_info["id"] == "1":
                self.tiku.last_error = QuestionImageUnavailable(
                    "Question image download did not return an image")
                return None
            self.tiku.last_error = None
            return '{"Answer":["B. 2"]}'

        self.tiku.query = Mock(side_effect=answer)
        with patch("api.base.decode_questions_info", return_value={
            "questions": [broken, second]
        }):
            self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        payload = self.session.post.call_args.kwargs["data"]
        self.assertEqual(payload["answer1"], "")
        self.assertEqual(payload["answer2"], "B")

    def test_a_missing_answer_is_still_fatal_without_the_image_excuse(self):
        self.tiku.PREVIEW_ONLY = False
        self.tiku.get_submit_params.return_value = ""
        self.tiku.last_error = None
        self.tiku.query.return_value = None
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_not_called()

    def test_false_string_submission_status_is_not_success(self):
        self.tiku.PREVIEW_ONLY = False
        self.tiku.get_submit_params.return_value = ""
        self.session.post.return_value.json.return_value = {"status": "false", "msg": "rejected"}
        self.assertEqual(self.run_work(), StudyResult.ERROR)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("linux_setup", ROOT / "deploy/linux/setup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.initialize = module.initialize

    def test_generated_configs_share_one_private_key_and_preview_is_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "deployment"
            targets = self.initialize(folder, port=13000)
            env = dict(line.split("=", 1) for line in targets[0].read_text().splitlines())
            bridge = json.loads(targets[1].read_text(encoding="utf-8"))
            config = configparser.ConfigParser()
            config.read(targets[2], encoding="utf-8")
            self.assertEqual(bridge["server"]["auth"], env["WEBAI_API_KEY"])
            self.assertGreater(len(env["WEBAI_API_KEY"]), 32)
            self.assertEqual(env["WEBAI_PORT"], "13000")
            self.assertEqual(bridge["backend"]["pool"]["instances"][0]["workers"][0]["type"], "deepseek_text")
            self.assertEqual(bridge["queue"]["imageLimit"], 10)
            self.assertEqual(config["tiku"]["webai_vision_model"], "auto")
            self.assertEqual(config["tiku"]["provider"], "WebAI")
            self.assertTrue(config.getboolean("tiku", "webai_preview"))
            self.assertFalse(config.getboolean("tiku", "submit"))
            self.assertEqual(config["common"]["password"], "")
            self.assertNotIn(env["WEBAI_API_KEY"], targets[2].read_text(encoding="utf-8"))
            original = [path.read_bytes() for path in targets]
            with self.assertRaises(FileExistsError):
                self.initialize(folder)
            self.assertEqual([path.read_bytes() for path in targets], original)
            if os.name == "posix":
                for path in targets:
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_port_does_not_create_deployment(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "deployment"
            with self.assertRaises(ValueError):
                self.initialize(folder, port=0)
            self.assertFalse(folder.exists())

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is not installed")
    def test_compose_config_is_private_and_uses_service_dns(self):
        with tempfile.TemporaryDirectory() as temp:
            targets = self.initialize(Path(temp) / "deployment")
            env = os.environ.copy()
            env.pop("WEBAI_API_KEY", None)
            env.pop("WEBAI_PORT", None)
            process = subprocess.run(
                ["docker", "compose", "--env-file", str(targets[0]),
                 "--file", str(ROOT / "deploy/linux/compose.yaml"),
                 "--profile", "tools", "config", "--format", "json"],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            services = json.loads(process.stdout)["services"]
            bridge = services["webai"]
            self.assertEqual(bridge["ports"][0]["host_ip"], "127.0.0.1")
            self.assertEqual(len(bridge["ports"]), 1)
            self.assertTrue(bridge["build"]["context"].replace("\\", "/").endswith("/WebAI2API"))
            course = services["course"]
            self.assertEqual(course["profiles"], ["tools"])
            self.assertEqual(course["depends_on"]["webai"]["condition"], "service_healthy")
            self.assertTrue(any(mount["target"] == "/state/resource" and mount["read_only"]
                                for mount in course["volumes"]))


if __name__ == "__main__":
    unittest.main()
