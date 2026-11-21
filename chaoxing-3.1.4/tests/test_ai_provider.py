"""Protocol switching, editable provider configuration, and conversation history."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit

from api.webai_client import (
    DEFAULT_MAX_TOKENS, MAX_FALLBACK_MODELS, WebAIClient, WebAIError,
)
from api.webai_errors import GenerationError, RateLimitedError
from backend.service import Service, Settings, create_app

TOKEN = "provider-test-key-not-a-real-credential"
ANSWER = '{"Answer":["A"]}'
QUESTION = {"title": "2026 practice: 1 + 1 = ?", "type": "single", "options": "A. 1\nB. 2"}


class ProviderHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_body(self, status, content, content_type="application/json"):
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self.server.received.append(("GET", self.path, None))
        if self.path.replace("/api/v1", "/v1", 1) != "/v1/models":
            return self.send_body(404, "{}")
        if self.server.models_status != 200:
            return self.send_body(self.server.models_status, '{"error":{"message":"no models"}}')
        self.send_body(200, json.dumps({
            "data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "vision-a"}
                     ] if self.server.model_capabilities else [{"id": "model-a"}, {"id": "model-b"}]}),
        )

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.received.append(("POST", self.path, body))
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.send_body(401, '{"error":{"code":"UNAUTHORIZED"}}')
        # Also act as a local forward-proxy fixture (absolute request targets).
        path = urlsplit(self.path).path.replace("/api/v1", "/v1", 1)
        if path == "/v1/chat/completions":
            failure = (self.server.failures or {}).get(str(body.get("model") or ""))
            if failure:
                status, message = failure
                return self.send_body(status, json.dumps({"error": {"message": message, "code": status}}))
            if self.server.responses:
                return self.send_body(200, json.dumps({
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": self.server.responses.pop(0)}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }))
            if body.get("stream") and self.server.stream:
                events = [
                    "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": '{"Answer":'}}]}),
                    "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": '["A"]}'}}]}),
                    "data: [DONE]",
                ]
                return self.send_body(200, "\n\n".join(events) + "\n\n", "text/event-stream")
            return self.send_body(200, json.dumps({
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": ANSWER}}],
            }))
        if path == "/v1/responses":
            if body.get("stream") and self.server.stream:
                events = [
                    {"type": "response.created"},
                    {"type": "response.output_text.delta", "delta": '{"Answer":'},
                    {"type": "response.output_text.delta", "delta": '["A"]}'},
                    {"type": "response.completed", "response": {"status": "completed"}},
                ]
                return self.send_body(
                    200, "\n\n".join("data: " + json.dumps(event) for event in events) + "\n\n",
                    "text/event-stream",
                )
            return self.send_body(200, json.dumps({
                "status": "completed",
                "output": [{"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": ANSWER},
                ]}],
            }))
        self.send_body(404, "{}")


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        cls.server.received = []
        cls.server.stream = True
        cls.server.models_status = 200
        cls.server.model_capabilities = False
        cls.server.failures = {}
        cls.server.responses = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.server.received.clear()
        self.server.stream = True
        self.server.models_status = 200
        self.server.model_capabilities = False
        self.server.failures = {}
        self.server.responses = []

    def config(self, **overrides):
        config = {
            "webai_endpoint": self.base, "webai_model": "model-a", "webai_key": TOKEN,
            "webai_protocol": "chat", "webai_stream": "false", "webai_timeout": "30",
            "min_interval_seconds": "0", "webai_vision_model": "model-a",
            "webai_vision_force": "true",
        }
        config.update(overrides)
        return config

    def paths(self):
        return [path for _method, path, _body in self.server.received]

    def bodies(self):
        return [body for _method, _path, body in self.server.received if body is not None]


class ProtocolTests(ServerCase):
    def complete(self, **overrides):
        client = WebAIClient(self.config(**overrides))
        self.addCleanup(client.close)
        return client.complete(QUESTION)

    def test_chat_completions_keeps_the_existing_contract(self):
        self.assertEqual(self.complete(), ANSWER)
        self.assertEqual(self.paths(), ["/v1/chat/completions"])
        body = self.bodies()[0]
        self.assertIn("QUESTION_DATA", body["messages"][0]["content"])
        self.assertNotIn("input", body)

    def test_streamed_chat_completions_is_unchanged(self):
        self.assertEqual(self.complete(webai_stream="true"), ANSWER)

    def test_responses_protocol_uses_input_parts_and_parses_output_text(self):
        self.assertEqual(self.complete(webai_protocol="responses"), ANSWER)
        self.assertEqual(self.paths(), ["/v1/responses"])
        body = self.bodies()[0]
        self.assertEqual(body["input"][0]["role"], "user")
        self.assertEqual(body["input"][0]["content"][0]["type"], "input_text")
        self.assertNotIn("messages", body)

    def test_streamed_responses_protocol_reads_text_deltas(self):
        self.assertEqual(self.complete(webai_protocol="responses", webai_stream="true"), ANSWER)

    def test_requests_carry_a_bounded_output_budget(self):
        """不设上限时 OpenRouter 按模型满上下文预估花费，余额不足直接 402。"""
        self.complete()
        self.assertEqual(self.bodies()[0]["max_tokens"], DEFAULT_MAX_TOKENS)
        self.server.received.clear()
        self.complete(webai_protocol="responses")
        self.assertEqual(self.bodies()[0]["max_output_tokens"], DEFAULT_MAX_TOKENS)
        self.assertNotIn("max_tokens", self.bodies()[0])

    def test_output_budget_is_configurable_and_can_be_disabled(self):
        self.complete(webai_max_tokens="512")
        self.assertEqual(self.bodies()[0]["max_tokens"], 512)
        self.server.received.clear()
        self.complete(webai_max_tokens="0")
        self.assertNotIn("max_tokens", self.bodies()[0])
        self.server.received.clear()
        self.complete(webai_protocol="responses", webai_max_tokens="0")
        self.assertNotIn("max_output_tokens", self.bodies()[0])

    def test_invalid_output_budget_is_rejected(self):
        for value in ("-1", "32001", "lots"):
            with self.subTest(value=value), self.assertRaises(WebAIError):
                WebAIClient(self.config(webai_max_tokens=value))

    def test_free_reasoning_models_get_their_chain_of_thought_excluded(self):
        """免费推理模型不排掉思维链就只回空正文，答案永远解析不出来。"""
        self.complete(webai_reasoning_exclude="true")
        self.assertEqual(self.bodies()[0]["reasoning"], {"exclude": True})
        self.server.received.clear()
        self.complete(webai_reasoning_exclude="false")
        self.assertNotIn("reasoning", self.bodies()[0])
        self.server.received.clear()
        self.complete(webai_protocol="responses", webai_reasoning_exclude="true")
        self.assertNotIn("reasoning", self.bodies()[0], "Responses API 用另一套字段")

    def test_reasoning_exclude_defaults_to_openrouter_only(self):
        # 本机桥接不认这个字段，默认不发；OpenRouter 默认发
        self.complete()
        self.assertNotIn("reasoning", self.bodies()[0])
        client = WebAIClient(self.config(webai_endpoint="https://openrouter.ai/api/v1"))
        self.addCleanup(client.close)
        self.assertTrue(client.reasoning_exclude)
        client = WebAIClient(self.config(
            webai_endpoint="https://openrouter.ai/api/v1", webai_reasoning_exclude="false",
        ))
        self.addCleanup(client.close)
        self.assertFalse(client.reasoning_exclude)

    def test_invalid_reasoning_exclude_setting_is_rejected(self):
        with self.assertRaises(WebAIError):
            WebAIClient(self.config(webai_reasoning_exclude="maybe"))

    def test_a_long_fallback_chain_is_allowed(self):
        chain = ["model-%d" % index for index in range(MAX_FALLBACK_MODELS)]
        client = WebAIClient(self.config(webai_model_fallbacks=chain))
        self.addCleanup(client.close)
        self.assertEqual(list(client.model_fallbacks), chain)
        with self.assertRaises(WebAIError):
            WebAIClient(self.config(webai_model_fallbacks=chain + ["one-too-many"]))

    def test_responses_protocol_translates_image_parts(self):
        parts = WebAIClient._response_parts([
            {"type": "text", "text": "read this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"}},
        ])
        self.assertEqual(parts[0], {"type": "input_text", "text": "read this"})
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertEqual(parts[1]["image_url"], "data:image/png;base64,AAAA")
        self.assertEqual(parts[1]["detail"], "high")
        with self.assertRaises(WebAIError):
            WebAIClient._response_parts([{"type": "audio", "data": "x"}])

    def test_incomplete_and_failed_responses_are_not_silently_accepted(self):
        with self.assertRaises(GenerationError):
            WebAIClient._responses_text({
                "status": "incomplete",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "A"}]}],
            })
        with self.assertRaises(WebAIError):
            WebAIClient._responses_text({"status": "completed", "output": []})
        with self.assertRaises(GenerationError):
            WebAIClient._responses_text({
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
            })

    def test_failed_stream_event_maps_to_the_shared_error_types(self):
        with self.assertRaises(RateLimitedError):
            WebAIClient._responses_failure({
                "type": "response.failed",
                "response": {"status": "failed", "error": {"code": "rate_limit_exceeded",
                                                          "message": "too many requests"}},
            })
        with self.assertRaises(WebAIError):
            WebAIClient._responses_failure({
                "type": "response.failed",
                "response": {"error": {"code": "invalid_api_key", "message": "bad key"}},
            })
        with self.assertRaises(GenerationError):
            WebAIClient._responses_failure({
                "type": "error", "error": {"code": "server_error", "message": "secret payload"},
            })

    def test_unknown_protocol_is_rejected(self):
        with self.assertRaises(WebAIError):
            WebAIClient(self.config(webai_protocol="grpc"))

    def test_conversation_recorder_captures_prompt_response_and_context(self):
        records = []
        client = WebAIClient(self.config(webai_protocol="responses", webai_recorder=records.append))
        self.addCleanup(client.close)
        client.context = {"channel": "api", "job_id": "j" * 32, "request_id": "r" * 32}
        self.assertEqual(client.complete(QUESTION), ANSWER)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["channel"], "api")
        self.assertEqual(record["protocol"], "responses")
        self.assertEqual(record["model"], "model-a")
        self.assertEqual(record["response"], ANSWER)
        self.assertEqual(record["error"], "")
        self.assertEqual(record["images"], 0)
        self.assertIn("QUESTION_DATA", record["prompt"])
        self.assertGreaterEqual(record["elapsed_ms"], 0)


class RateLimitFallbackTests(ServerCase):
    """免费模型常被上游单独限流，换下一个模型比整单失败划算。"""

    def client(self, **overrides):
        client = WebAIClient(self.config(**overrides))
        self.addCleanup(client.close)
        # 本地测试服务冒充远端 API：只有远端才会读 429 的正文来分辨原因
        client.remote_endpoint = True
        return client

    def test_a_crowded_model_moves_on_to_the_next_one(self):
        self.server.failures = {"model-a": (429, "Provider returned error")}
        answer = self.client(webai_model_fallbacks=["model-b"]).complete(QUESTION)
        self.assertEqual(answer, ANSWER)
        self.assertEqual([body["model"] for body in self.bodies()], ["model-a", "model-b"])

    def test_a_model_that_refuses_the_image_count_is_skipped(self):
        """智谱 glm-4v-flash 一次只吃 5 张图；6 个选项的题得换模型答。"""
        self.server.failures = {"model-a": (400, "输入图片数量超过限制")}
        answer = self.client(webai_model_fallbacks=["model-b"]).complete(QUESTION)
        self.assertEqual(answer, ANSWER)
        self.assertEqual([body["model"] for body in self.bodies()], ["model-a", "model-b"])

    def test_exhausted_daily_quota_stops_instead_of_burning_models(self):
        self.server.failures = {"model-a": (
            429, "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock "
                 "1000 free model requests per day",
        )}
        with self.assertRaises(RateLimitedError):
            self.client(webai_model_fallbacks=["model-b"]).complete(QUESTION)
        self.assertEqual(len(self.bodies()), 1, "整号额度用完就别再换模型空转")


class DirectRetryTests(ServerCase):
    """模型跑题写成一整段解题过程时，再要一次纯 JSON，而不是把整题丢掉。"""

    QUESTION = {"title": "2 + 2 = ?", "type": "single", "options": "A. 3\nB. 4"}

    def client(self, **overrides):
        client = WebAIClient(self.config(**overrides))
        self.addCleanup(client.close)
        return client

    def test_a_drifting_reply_is_asked_once_more_for_json(self):
        self.server.responses = [
            "Based on the given information, the derivative is 2x, so integrating gives y = x^2 + C.",
            '{"Answer": ["B"]}',
        ]
        answers = self.client(webai_vision_model="model-a").complete_direct(self.QUESTION)
        self.assertEqual(answers, ["B. 4"])
        bodies = self.bodies()
        self.assertEqual(len(bodies), 2, "跑题只重试一次")
        self.assertNotIn("FORMAT_RETRY", bodies[0]["messages"][0]["content"])
        self.assertIn("FORMAT_RETRY", bodies[1]["messages"][0]["content"])

    def test_two_bad_replies_fail_instead_of_looping(self):
        self.server.responses = ["still not json", "nope"]
        with self.assertRaises(WebAIError):
            self.client(webai_vision_model="model-a").complete_direct(self.QUESTION)
        self.assertEqual(len(self.bodies()), 2)

    def test_a_rate_limited_primary_is_not_retried_as_a_format_problem(self):
        self.server.failures = {"model-a": (
            429, "Rate limit exceeded: free-models-per-day. Add 10 credits",
        )}
        with self.assertRaises(RateLimitedError):
            self.client(webai_vision_model="model-a").complete_direct(self.QUESTION)
        self.assertEqual(len(self.bodies()), 1)


class ProviderConfigurationTests(ServerCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.service = Service(Settings(state_dir=Path(directory.name)), client_factory=Mock)
        self.addCleanup(self.service.close)
        self.client = create_app(self.service).test_client()
        self.headers = {"Authorization": "Bearer " + self.service.secrets.token}

    def save(self, **payload):
        body = {"endpoint": self.base, "model": "model-a", "api_key": TOKEN}
        body.update(payload)
        return self.client.put("/v1/ai/provider", headers=self.headers, json=body)

    def test_saved_provider_is_encrypted_and_usable_immediately(self):
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["configured"])
        self.assertTrue(response.json["has_key"])
        self.assertEqual(response.json["source"], "console")
        self.assertNotIn(TOKEN, response.text)
        stored = self.service.store.provider_settings()
        self.assertNotEqual(stored["api_key"], TOKEN)
        self.assertNotIn(TOKEN, json.dumps(stored))
        self.assertEqual(self.service.api_provider._stored_key(), TOKEN)
        health = self.client.get("/v1/health", headers=self.headers)
        self.assertTrue(health.json["ai_api_configured"])
        self.assertEqual(health.json["ai_api_protocol"], "chat")
        self.assertEqual(health.json["ai_api_model"], "model-a")
        self.assertNotIn(TOKEN, health.text)
        switched = self.client.put(
            "/v1/ai/channel", headers=self.headers, json={"channel": "api"},
        )
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json["api"]["protocol"], "chat")
        self.assertTrue(switched.json["api"]["editable"])
        self.assertEqual(self.service.broker.api_config["webai_endpoint"], self.base)
        self.assertNotIn(TOKEN, json.dumps(switched.json))

    def test_proxy_is_stored_and_used_only_by_the_api_channel(self):
        proxy = self.base.removesuffix("/v1")
        response = self.save(proxy=proxy)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json["proxy"], proxy)
        config = self.service.broker.api_config
        self.assertEqual(config["http_proxy"], proxy)
        client = self.service.api_provider.client()
        self.addCleanup(client.close)
        self.assertEqual(client.session.proxies["https"], proxy)
        self.assertEqual(client.vision_session.proxies["https"], proxy)
        tested = self.client.post("/v1/ai/test", headers=self.headers, json={})
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertTrue(any(method == "POST" and path.startswith(self.base)
                            for method, path, _ in self.server.received))
        self.assertNotIn(proxy, self.client.get("/v1/health", headers=self.headers).text)
        cleared = self.save(proxy="")
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json["proxy"], "")
        self.assertNotIn("http_proxy", self.service.broker.api_config)

    def test_output_budget_can_differ_per_channel(self):
        """同一个账号文本能开 8192，识图模型可能只许 1024，必须分开设。"""
        response = self.save(max_tokens=4096)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json["max_tokens"], 4096)
        self.assertEqual(self.service.broker.api_config["webai_max_tokens"], "4096")
        vision = self.client.put("/v1/ai/vision-provider", headers=self.headers, json={
            "endpoint": self.base, "model": "vision-a", "max_tokens": 1024,
        })
        self.assertEqual(vision.status_code, 200, vision.text)
        self.assertEqual(vision.json["max_tokens"], 1024)
        self.assertEqual(self.service.broker.api_config["webai_max_tokens"], "4096")
        vision_config = self.service.vision_provider.client_config()
        self.assertEqual(vision_config["webai_max_tokens"], "1024")
        text_client = self.service.api_provider.client()
        self.addCleanup(text_client.close)
        text_client.complete(QUESTION)
        self.assertEqual(self.bodies()[0]["max_tokens"], 4096)
        self.server.received.clear()
        cleared = self.save(max_tokens="")
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertNotIn("webai_max_tokens", self.service.broker.api_config)

    def test_invalid_proxy_is_rejected(self):
        for value in ("127.0.0.1:7890", "socks5://127.0.0.1:7890", "http://127.0.0.1",
                      "http://127.0.0.1:7890/proxy", "http://user:pass@127.0.0.1:7890"):
            with self.subTest(proxy=value):
                response = self.save(proxy=value)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["error"], "AI_PROVIDER_INVALID_PROXY")

    def test_protocol_can_be_switched_persistently(self):
        self.save()
        response = self.save(protocol="responses")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["protocol"], "responses")
        self.assertEqual(response.json["protocol_label"], "Responses API")
        self.assertEqual(self.service.broker.api_config["webai_protocol"], "responses")
        result = self.client.post("/v1/ai/test", headers=self.headers, json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json["protocol"], "responses")
        self.assertEqual(self.paths()[-1], "/v1/responses")
        state_dir = self.service.settings.state_dir
        self.service.close()
        service = Service(Settings(state_dir=state_dir), client_factory=Mock)
        self.addCleanup(service.close)
        self.assertEqual(service.api_provider.status()["protocol"], "responses")

    def test_invalid_provider_fields_are_rejected_with_stable_codes(self):
        cases = [
            ({"protocol": "grpc"}, "AI_PROVIDER_INVALID_PROTOCOL"),
            ({"endpoint": "https://api.example.com/v1/chat/completions"},
             "AI_PROVIDER_INVALID_ENDPOINT"),
            ({"endpoint": "ftp://api.example.com/v1"}, "AI_PROVIDER_INVALID_ENDPOINT"),
            ({"endpoint": "https://user:pass@api.example.com/v1"},
             "AI_PROVIDER_INVALID_ENDPOINT"),
            ({"endpoint": "https://api.example.com/v1/responses"},
             "AI_PROVIDER_INVALID_ENDPOINT"),
            ({"endpoint": "https://api.example.com/v1/../v2"},
             "AI_PROVIDER_INVALID_ENDPOINT"),
            ({"endpoint": "https://api.example.com:99999/v1"},
             "AI_PROVIDER_INVALID_ENDPOINT"),
            ({"endpoint": "https://api.example.com/v1?key=1"},
             "AI_PROVIDER_INVALID_ENDPOINT"),
            ({"model": "bad model with spaces"}, "AI_PROVIDER_INVALID_FIELD"),
            ({"model": ""}, "AI_PROVIDER_INVALID_FIELD"),
            ({"timeout": 10 ** 6}, "AI_PROVIDER_INVALID_FIELD"),
            ({"max_tokens": 10 ** 6}, "AI_PROVIDER_INVALID_FIELD"),
            ({"max_tokens": -1}, "AI_PROVIDER_INVALID_FIELD"),
            ({"max_tokens": "lots"}, "AI_PROVIDER_INVALID_FIELD"),
            ({"model_fallbacks": ["a", "b", "c", "d", "e", "f", "g"]},
             "AI_PROVIDER_INVALID_FIELD"),
            ({"api_key": "short"}, "AI_PROVIDER_INVALID_KEY"),
            ({"unknown": 1}, "AI_PROVIDER_INVALID_FIELD"),
        ]
        for payload, code in cases:
            with self.subTest(payload=payload):
                response = self.save(**payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["error"], code)
        self.assertFalse(self.service.api_provider.status()["configured"])

    def test_endpoint_is_normalised_to_v1(self):
        response = self.client.put("/v1/ai/provider", headers=self.headers, json={
            "endpoint": self.base[:-3], "model": "model-a", "api_key": TOKEN,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json["endpoint"], self.base)

    def test_endpoint_path_prefixes_are_preserved(self):
        for endpoint in (
            "https://openrouter.ai/api/v1",
            "https://ark.cn-beijing.volces.com/api/v3",
            "https://gateway.example.com/openai/v1",
            "https://api.example.com/v1/",
        ):
            with self.subTest(endpoint=endpoint):
                response = self.save(endpoint=endpoint)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json["endpoint"], endpoint.rstrip("/"))
                self.assertTrue(response.json["configured"])

    def test_prefixed_endpoint_reaches_the_upstream(self):
        prefixed = self.base.replace("/v1", "/api/v1")
        response = self.save(endpoint=prefixed)
        self.assertEqual(response.status_code, 200, response.text)
        models = self.client.get("/v1/ai/models", headers=self.headers)
        self.assertEqual(models.status_code, 200, models.text)
        self.assertEqual([item["id"] for item in models.json["models"]],
                         ["model-a", "model-b"])
        tested = self.client.post("/v1/ai/test", headers=self.headers, json={})
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertTrue(tested.json["ok"])
        self.assertEqual(tested.json["endpoint"], prefixed)
        self.assertIn("/api/v1/models", self.paths())
        self.assertIn("/api/v1/chat/completions", self.paths())

    def test_model_list_comes_from_the_saved_provider(self):
        self.save()
        response = self.client.get("/v1/ai/models", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["id"] for item in response.json["models"]],
                         ["model-a", "model-b"])
        self.assertEqual(response.json["protocol"], "chat")
        self.assertIn("/v1/models", self.paths())

    def test_model_list_reports_an_unreachable_provider(self):
        self.save()
        self.server.models_status = 500
        response = self.client.get("/v1/ai/models", headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "AI_PROVIDER_UNREACHABLE")

    def test_single_question_test_and_conversation_history(self):
        self.save()
        result = self.client.post("/v1/ai/test", headers=self.headers, json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json["ok"])
        self.assertEqual(result.json["reply"], ANSWER)
        self.assertEqual(result.json["model"], "model-a")
        history = self.client.get("/v1/ai/conversations", headers=self.headers)
        self.assertEqual(history.status_code, 200, history.text)
        rows = history.json["conversations"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["channel"], "api")
        self.assertEqual(row["protocol"], "chat")
        self.assertEqual(row["route"], "text")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["response"], ANSWER)
        self.assertIn("1 + 1", row["prompt"])
        self.assertNotIn(TOKEN, history.text)

    def test_test_endpoint_requires_a_provider(self):
        response = self.client.post("/v1/ai/test", headers=self.headers, json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "AI_PROVIDER_NOT_CONFIGURED")

    def test_channel_switch_requires_configuration_then_clears_with_the_key(self):
        self.assertEqual(
            self.client.put("/v1/ai/channel", headers=self.headers, json={"channel": "api"}).status_code,
            400,
        )
        self.save()
        self.assertEqual(
            self.client.put("/v1/ai/channel", headers=self.headers, json={"channel": "api"}).status_code,
            200,
        )
        cleared = self.save(clear_key=True)
        self.assertFalse(cleared.json["has_key"])
        self.assertFalse(cleared.json["configured"])
        self.assertEqual(self.service.broker.channel(), "web")

    def test_conversation_query_limits_are_validated(self):
        for query in ("?limit=0", "?limit=500", "?after=-1", "?job_id=bad", "?unknown=1"):
            with self.subTest(query=query):
                response = self.client.get("/v1/ai/conversations" + query, headers=self.headers)
                self.assertEqual(response.status_code, 400)


class ConversationStoreTests(ServerCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.service = Service(Settings(state_dir=Path(directory.name)), client_factory=Mock)
        self.addCleanup(self.service.close)
        self.store = self.service.store

    def record(self, index, **overrides):
        payload = {
            "job_id": "a" * 32, "request_id": "b" * 32, "channel": "web", "protocol": "chat",
            "endpoint": "http://127.0.0.1:3001/v1", "model": "deepseek-search",
            "route": "text", "stage": "", "prompt": f"question {index}",
            "response": f"answer {index}", "images": 0, "error": "", "elapsed_ms": 5,
        }
        payload.update(overrides)
        self.store.record_ai_conversation(payload)

    def test_newest_first_order_and_cursor(self):
        for index in range(3):
            self.record(index)
        newest = self.store.ai_conversations(0, 2)
        self.assertEqual([row["prompt"] for row in newest],
                         ["question 2", "question 1"])
        older = self.store.ai_conversations(0, 10)
        self.assertEqual([row["prompt"] for row in older],
                         ["question 2", "question 1", "question 0"])
        forward = self.store.ai_conversations(older[-1]["id"], 10)
        self.assertEqual([row["prompt"] for row in forward],
                         ["question 2", "question 1"])

    def test_retention_keeps_the_latest_entries(self):
        keep = self.store.AI_CONVERSATION_KEEP
        for index in range(keep + 5):
            self.record(index)
        rows = self.store.ai_conversations(0, 1)
        self.assertEqual(rows[0]["prompt"], f"question {keep + 4}")
        self.assertEqual(
            self.store.storage_summary()["records"]["ai_conversations"], keep,
        )

    def test_oversized_content_is_truncated_and_flagged(self):
        self.record(0, prompt="x" * 30000, response="y" * 30000)
        row = self.store.ai_conversations(0, 1)[0]
        self.assertEqual(row["truncated"], 1)
        self.assertEqual(row["prompt_chars"], 30000)
        self.assertEqual(len(row["prompt"]), self.store.AI_CONVERSATION_PROMPT_CHARS)
        self.assertEqual(len(row["response"]), self.store.AI_CONVERSATION_RESPONSE_CHARS)

    def test_failed_request_is_recorded_as_an_error(self):
        self.record(0, response="", error="AI_RATE_LIMITED")
        row = self.store.ai_conversations(0, 1)[0]
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "AI_RATE_LIMITED")

    def test_job_filter_only_returns_that_job(self):
        self.record(0)
        self.record(1, job_id="c" * 32)
        rows = self.store.ai_conversations(0, 10, "c" * 32)
        self.assertEqual([row["prompt"] for row in rows], ["question 1"])


if __name__ == "__main__":
    unittest.main()
