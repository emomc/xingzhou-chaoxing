"""Bounded AI concurrency with durable deduplication and validated shared results."""

import logging
import json
import threading
import time

log = logging.getLogger(__name__)

from api.webai_client import (
    InvalidAnswerError, VisionUnavailableError, WebAIClient, question_bank_key,
    validated_completion, validate_question,
)
from api.question_images import image_manifest
from api.webai_errors import RateLimitedError, VisionReviewError


class AnswerBroker:
    CHANNEL_KEYS = {"text": "ai_channel", "vision": "ai_vision_channel"}

    def __init__(self, store, config, workers=1, wait_timeout=3600, client_factory=None,
                 api_config=None, provider=None, recorder=None, vision_provider=None):
        self.store = store
        self.config = dict(config)
        self._static_api_config = dict(api_config) if api_config else None
        self.provider = provider
        self.vision_provider = vision_provider
        self.recorder = recorder
        self.workers = workers
        self.wait_timeout = wait_timeout
        self.client_factory = client_factory or (lambda: WebAIClient(self.config))
        self.stop_event = threading.Event()
        self.threads = []
        self.cooldown_until = 0
        self.lock = threading.Lock()

    @property
    def api_config(self):
        if self.provider is not None:
            return self.provider.client_config()
        return self._static_api_config

    def route_api_config(self, route):
        """Image questions may use their own API endpoint/model; empty means share the text one."""
        if route == "vision" and self.vision_provider is not None:
            return self.vision_provider.client_config() or self.api_config
        return self.api_config

    def channel(self, route="text"):
        """Channel for one question route: text questions and image questions are separate."""
        kind = "vision" if route == "vision" else "text"
        value = self.store.setting(self.CHANNEL_KEYS[kind], "")
        if kind == "vision" and value not in {"web", "api"}:
            # 图片题通道没单独设置时跟随文本通道，避免出现"没人管"的第三状态
            return self.channel("text")
        if value not in {"web", "api"}:
            value = "web"
        return "api" if value == "api" and self.route_api_config(kind) else "web"

    def channel_chain(self, route):
        """Channels to try in order; image questions fall back to the other channel."""
        primary = self.channel(route)
        if route != "vision" or self.store.setting("ai_vision_fallback", "1") != "1":
            return [primary]
        other = "web" if primary == "api" else "api"
        if other == "api" and not self.route_api_config("vision"):
            return [primary]
        return [primary, other]

    def channel_status(self):
        api = self.api_config or {}
        vision_api = self.route_api_config("vision") or {}
        vision_setting = self.store.setting(self.CHANNEL_KEYS["vision"], "")
        status = {
            "channel": self.channel("text"),
            "vision_channel": self.channel("vision"),
            "vision_channel_explicit": vision_setting in {"web", "api"},
            "vision_enabled": self.store.setting("ai_vision_enabled", "0") == "1",
            "vision_fallback": self.store.setting("ai_vision_fallback", "1") == "1",
            "vision_direct": self.direct_vision(),
            "vision_chain": self.channel_chain("vision"),
            "web": {"configured": True, "model": self.config.get("webai_model", "")},
            "api": {
                "configured": bool(self.api_config),
                "model": api.get("webai_model", ""),
                "vision_model": api.get("webai_vision_model", ""),
                "protocol": api.get("webai_protocol", "chat"),
                "endpoint": api.get("webai_endpoint", ""),
                "editable": self.provider is not None,
            },
            "vision_api": {
                "configured": bool(self.route_api_config("vision")),
                "separate": bool(self.vision_provider is not None
                                 and self.vision_provider.client_config()),
                "model": vision_api.get("webai_model", ""),
                "vision_model": vision_api.get("webai_vision_model", ""),
                "protocol": vision_api.get("webai_protocol", "chat"),
                "endpoint": vision_api.get("webai_endpoint", ""),
            },
        }
        if self.provider is not None:
            status["api"]["source"] = self.provider.status()["source"]
        if self.vision_provider is not None:
            status["vision_api"]["source"] = self.vision_provider.status()["source"]
        return status

    def set_channel(self, channel, kind="text"):
        if kind not in {"text", "vision"}:
            raise ValueError("Invalid AI channel kind")
        if channel == "auto":
            if kind != "vision":
                raise ValueError("Invalid AI channel")
            self.store.set_setting(self.CHANNEL_KEYS["vision"], "")
            return self.channel_status()
        if channel not in {"web", "api"}:
            raise ValueError("Invalid AI channel")
        if channel == "api" and not self.route_api_config(kind):
            raise ValueError("API AI channel is not configured")
        self.store.set_setting(self.CHANNEL_KEYS[kind], channel)
        return self.channel_status()

    def set_vision_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Invalid image question switch")
        self.store.set_setting("ai_vision_enabled", "1" if enabled else "0")
        return self.channel_status()

    def set_vision_fallback(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Invalid image fallback switch")
        self.store.set_setting("ai_vision_fallback", "1" if enabled else "0")
        return self.channel_status()

    def direct_vision(self):
        """Image questions on the API channel answer in one vision request by default."""
        return self.store.setting("ai_vision_direct", "1") == "1"

    def set_vision_direct(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Invalid image answer mode")
        self.store.set_setting("ai_vision_direct", "1" if enabled else "0")
        return self.channel_status()

    def key(self, question):
        return question_bank_key(question)

    def request(self, job_id, question):
        validate_question(question)
        clean = {name: question[name] for name in ("title", "type", "options", "has_images", "images")
                 if name in question}
        clean["course_id"] = self.store.answer_course(job_id, question.get("course_id"))
        result = self.store.request_answer(job_id, self.key(clean), clean)
        log.info(
            "Question bank: source=%s course=%s type=%s images=%s request=%s",
            result["source"], clean["course_id"], clean["type"],
            len(clean.get("images", [])), result["id"][:12],
        )
        return result

    def start(self):
        for index in range(self.workers):
            thread = threading.Thread(target=self._run, name=f"ai-{index}", daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()
        # Upstream calls have their own deadline; no new call starts during shutdown.
        for thread in self.threads:
            thread.join(timeout=1)

    @staticmethod
    def failure_code(exc):
        if isinstance(exc, VisionReviewError):
            return exc.code
        if isinstance(exc, VisionUnavailableError):
            return "AI_VISION_UNAVAILABLE"
        if isinstance(exc, RateLimitedError):
            return "AI_RATE_LIMITED"
        return f"AI_INVALID_ANSWER: {exc}"[:180]

    def _build_client(self, channel, route="text"):
        if channel == "api":
            if route == "vision" and self.vision_provider is not None:
                config = dict(self.vision_provider.client_config() or self.api_config or {})
            else:
                config = dict(self.api_config or {})
            if self.recorder is not None:
                config.setdefault("webai_recorder", self.recorder)
            return WebAIClient(config)
        return self.client_factory()

    def _run(self):
        # 文本题和图片题各走各的通道，因此按通道分别保温一个客户端，切换时不必反复重连
        clients = {}
        try:
            while not self.stop_event.wait(0.25):
                with self.lock:
                    cooling = time.monotonic() < self.cooldown_until
                if cooling:
                    continue
                task = self.store.claim_answer(self.wait_timeout)
                if task is None:
                    continue
                started = time.monotonic()
                route = "vision" if image_manifest(task["question"]) else "text"
                if route == "vision":
                    task["question"] = self.store.hydrate_answer_images(task["question"])
                generation = (
                    self.provider.generation if self.provider is not None else 0,
                    self.vision_provider.generation if self.vision_provider is not None else 0,
                )
                chain = self.channel_chain(route)
                attempt_errors = []
                answered = False
                for channel in chain:
                    pool_key = (channel, route if channel == "api" else "")
                    cached = clients.get(pool_key)
                    if cached is not None and cached[0] != generation:
                        cached[1].close()
                        clients.pop(pool_key, None)
                        cached = None
                    client = cached[1] if cached is not None else None
                    try:
                        if client is None:
                            client = self._build_client(channel, route)
                            clients[pool_key] = (generation, client)
                        try:
                            client.context = {
                                "job_id": task.get("job_id", ""), "request_id": task["id"],
                                "channel": channel,
                            }
                        except AttributeError:
                            pass
                        if isinstance(getattr(client, "last_diagnostics", None), dict):
                            client.last_diagnostics = {}
                        log.info(
                            "AI request started: request=%s channel=%s route=%s images=%s attempt=%s/%s",
                            task["id"][:12], channel, route,
                            len(task["question"].get("images", [])),
                            chain.index(channel) + 1, len(chain),
                        )
                        def rejected(exc, raw, attempt, attempts):
                            log.warning(
                                "AI answer rejected: request=%s type=%s attempt=%s/%s reason=%s response_chars=%s",
                                task["id"][:12], task["question"]["type"],
                                attempt, attempts, exc, len(raw) if isinstance(raw, str) else 0,
                            )

                        if route == "vision" and channel == "api" and self.direct_vision():
                            # 图片 API 一次直答：实测比三步流水线又快又准，失败由通道链兜底
                            answers = client.complete_direct(task["question"])
                            diagnostics = getattr(client, "last_diagnostics", None)
                            if not isinstance(diagnostics, dict):
                                diagnostics = {}
                            diagnostics.update(stage="direct", quality="candidate")
                            client.last_diagnostics = diagnostics
                            self.store.answer_stage(task["id"], "direct")
                        else:
                            answers = validated_completion(
                                client, task["question"], on_reject=rejected,
                                cancelled=self.stop_event.is_set,
                                on_stage=lambda stage: self.store.answer_stage(task["id"], stage),
                            )
                    except (VisionReviewError, InvalidAnswerError, VisionUnavailableError,
                            RateLimitedError) as exc:
                        if self.stop_event.is_set():
                            return
                        code = self.failure_code(exc)
                        attempt_errors.append((channel, code))
                        diagnostics = getattr(client, "last_diagnostics", None)
                        if chain.index(channel) + 1 < len(chain):
                            log.warning(
                                "AI attempt failed, trying the next channel: request=%s channel=%s reason=%s",
                                task["id"][:12], channel, code,
                            )
                            continue
                        log.warning("AI attempt failed: request=%s channel=%s reason=%s",
                                    task["id"][:12], channel, code)
                        self.store.finish_answer(
                            task["id"], error=code,
                            diagnostics=diagnostics if isinstance(diagnostics, dict) else None,
                        )
                        continue
                    except Exception as exc:  # noqa: BLE001 - upstream failure
                        if self.stop_event.is_set():
                            return
                        attempt_errors.append((channel, type(exc).__name__))
                        log.warning("AI request failed: channel=%s %s: %s",
                                    channel, type(exc).__name__, str(exc)[:180])
                        diagnostics = getattr(client, "last_diagnostics", None)
                        if chain.index(channel) + 1 < len(chain):
                            continue
                        self.store.finish_answer(
                            task["id"], error="AI_UNAVAILABLE",
                            diagnostics=diagnostics if isinstance(diagnostics, dict) else None,
                        )
                        with self.lock:
                            self.cooldown_until = time.monotonic() + 30
                        continue
                    answered = True
                    break
                if not answered:
                    continue
                if self.stop_event.is_set():
                    return
                diagnostics = getattr(client, "last_diagnostics", None)
                diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
                if route == "vision" and len(chain) > 1:
                    diagnostics["channel_chain"] = [name for name, _ in attempt_errors] + [channel]
                quality = diagnostics.get("quality")
                quality = quality if quality == "vision_cross_checked" else "format_checked"
                log.info(
                    "AI answer checked: request=%s channel=%s model=%s elapsed=%.2fs quality=%s",
                    task["id"][:12], channel, getattr(client, "selected_model", None),
                    time.monotonic() - started, quality,
                )
                self.store.finish_answer(
                    task["id"], json.dumps({"Answer": answers}, ensure_ascii=False),
                    quality=quality, diagnostics=diagnostics,
                )
        finally:
            for _, cached_client in clients.values():
                try:
                    cached_client.close()
                except Exception:  # noqa: BLE001 - shutdown must not raise
                    log.debug("Client close failed during shutdown", exc_info=True)
