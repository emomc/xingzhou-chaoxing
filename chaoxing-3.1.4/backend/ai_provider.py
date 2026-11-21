"""Console-editable OpenAI compatible provider: validated, encrypted, in SQLite."""

import json
import math
import os
import re
import time
from urllib.parse import urlsplit

from api.webai_client import WebAIClient, WebAIError, model_fallbacks

PROTOCOLS = {"chat": "chat", "responses": "responses"}
PROTOCOL_LABELS = {"chat": "Chat Completions", "responses": "Responses API"}
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,119}")
DEFAULT_TIMEOUT = "180"
DEFAULT_STREAM = "false"
MIN_TIMEOUT = 10
MAX_TIMEOUT = 900
MAX_KEY_CHARS = 512
MAX_OUTPUT_TOKENS = 32_000
FIELDS = (
    "endpoint", "protocol", "model", "model_fallbacks",
    "vision_model", "vision_model_fallbacks", "timeout", "stream", "proxy", "max_tokens",
)
TEST_QUESTION = {"title": "1 + 1 = ?", "type": "single", "options": "A. 1\nB. 2"}


class ProviderError(ValueError):
    """A provider configuration or probe failure with a stable public code."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _text(value, name, *, limit=200, allow_empty=False):
    if not isinstance(value, str):
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", name)
    value = value.strip()
    if not value and allow_empty:
        return ""
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", name)
    return value


CLOSING_SEGMENTS = {"chat", "completions", "completion", "responses", "messages", "embeddings"}


def _proxy(value):
    """Validate an optional HTTP(S) proxy used only by the API answer channel."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProviderError("AI_PROVIDER_INVALID_PROXY")
    value = value.strip()
    if not value:
        return ""
    if len(value) > 200 or any(char.isspace() for char in value):
        raise ProviderError("AI_PROVIDER_INVALID_PROXY")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname \
            or parsed.username is not None or parsed.password is not None \
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ProviderError("AI_PROVIDER_INVALID_PROXY")
    try:
        port = parsed.port
    except ValueError as error:
        raise ProviderError("AI_PROVIDER_INVALID_PROXY") from error
    if not port:
        raise ProviderError("AI_PROVIDER_INVALID_PROXY")
    return value.rstrip("/")


def _endpoint(value):
    """Accept a base URL such as https://host, https://host/v1 or https://host/api/v1.

    The client appends /chat/completions or /responses itself, so a URL that
    already points at an operation is rejected instead of silently misused.
    """
    if not isinstance(value, str):
        raise ProviderError("AI_PROVIDER_INVALID_ENDPOINT")
    value = value.strip().rstrip("/")
    if not value or len(value) > 200 or any(char.isspace() for char in value):
        raise ProviderError("AI_PROVIDER_INVALID_ENDPOINT")
    parsed = urlsplit(value)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.scheme not in {"http", "https"} or not parsed.hostname \
            or parsed.username is not None or parsed.password is not None \
            or parsed.query or parsed.fragment or ".." in segments \
            or (segments and segments[-1].lower() in CLOSING_SEGMENTS):
        raise ProviderError("AI_PROVIDER_INVALID_ENDPOINT")
    try:
        parsed.port
    except ValueError as error:
        raise ProviderError("AI_PROVIDER_INVALID_ENDPOINT") from error
    return value if segments else value + "/v1"


def _model(value, name, *, allow_empty=False):
    if not isinstance(value, str):
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", name)
    value = value.strip()
    if not value and allow_empty:
        return ""
    if not MODEL_NAME.fullmatch(value):
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", name)
    return value


def _fallbacks(value, name):
    try:
        return list(model_fallbacks(value if value is not None else (), name))
    except WebAIError as exc:
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", name) from exc


def _timeout(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", "timeout") from exc
    if not math.isfinite(seconds) or not MIN_TIMEOUT <= seconds <= MAX_TIMEOUT:
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", "timeout")
    return str(int(seconds))


def _max_tokens(value):
    """Optional per-channel output budget.

    各家上限差得很远（同一个智谱账号，文本可以 8192，识图只许 1024），
    所以留空表示沿用环境变量/默认值，填了就以它为准。0 表示不设上限。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", "max_tokens")
    try:
        tokens = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", "max_tokens") from exc
    if not 0 <= tokens <= MAX_OUTPUT_TOKENS:
        raise ProviderError("AI_PROVIDER_INVALID_FIELD", "max_tokens")
    return str(tokens)


def _stream(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if str(value).strip().lower() in {"true", "1", "yes", "on"}:
        return "true"
    if str(value).strip().lower() in {"false", "0", "no", "off"}:
        return "false"
    raise ProviderError("AI_PROVIDER_INVALID_FIELD", "stream")


class AIProvider:
    """Reads and writes an API answering provider used by the answer broker.

    A second instance with `prefix="ai_vision_api_"` and `base=<text provider>` gives image
    questions their own endpoint/model/key; anything left empty is inherited from the base,
    so a single key can drive two different models.
    """

    def __init__(self, store, secrets, env_config=None, recorder=None, prefix="ai_api_", base=None):
        self.store = store
        self.secrets = secrets
        self.env_config = dict(env_config) if env_config else None
        self.recorder = recorder
        self.prefix = prefix
        self.base = base
        self.generation = 0

    # -- storage -----------------------------------------------------------
    def _stored(self):
        return self.store.provider_settings(self.prefix)

    def _decrypt(self, value):
        if not value:
            return ""
        try:
            return self.secrets.cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except Exception:  # A rotated or damaged key must not break answering.
            return ""

    def _encrypt(self, value):
        return self.secrets.cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def _stored_key(self, stored=None):
        stored = self._stored() if stored is None else stored
        return self._decrypt(stored.get("api_key", "")).strip()

    def _env_key(self):
        env_name = str((self.env_config or {}).get("webai_key_env", "")).strip()
        if env_name:
            return os.environ.get(env_name, "").strip()
        return str((self.env_config or {}).get("webai_key", "")).strip()

    # -- public API --------------------------------------------------------
    def _base_config(self):
        if self.base is None:
            return self.env_config
        return self.base.client_config()

    def client_config(self):
        """Return a WebAIClient config for the API channel, or None if unusable."""
        stored = self._stored()
        base = self._base_config() or {}
        if not stored:
            # 图片通道没单独配置时沿用文本通道（同一个 key、同一个端点）
            return None if self.base is not None else self.env_config
        key = self._stored_key(stored) or self._env_key() or str(base.get("webai_key", ""))
        endpoint = stored.get("endpoint", "").strip() or str(base.get("webai_endpoint", ""))
        model = stored.get("model", "").strip() or str(base.get("webai_model", ""))
        if not endpoint or not model or not key:
            return None
        config = {
            "webai_endpoint": endpoint,
            "webai_model": model,
            "webai_model_fallbacks": stored.get("model_fallbacks", "[]") or "[]",
            "webai_key": key,
            "webai_key_env": "",
            "webai_protocol": stored.get("protocol", "").strip()
            or str(base.get("webai_protocol", "chat") or "chat"),
            "webai_timeout": stored.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT,
            "webai_stream": stored.get("stream", DEFAULT_STREAM) or DEFAULT_STREAM,
            "min_interval_seconds": "0",
            "webai_vision_model": stored.get("vision_model", "").strip() or model,
            # 图片通道的备用模型就是它自己的备用列表
            "webai_vision_model_fallbacks": stored.get("vision_model_fallbacks")
            or (stored.get("model_fallbacks", "[]") if self.base is not None else "[]") or "[]",
            "webai_vision_force": "true",
        }
        max_tokens = str(stored.get("max_tokens", "") or base.get("webai_max_tokens", "") or "")
        if max_tokens:
            # 每个通道各自的输出预算：识图模型往往只允许一两千 token
            config["webai_max_tokens"] = max_tokens
        proxy = stored.get("proxy", "").strip()
        if proxy:
            # Only this channel leaves through the proxy; course traffic never does.
            config["http_proxy"] = proxy
        if self.recorder is not None:
            config["webai_recorder"] = self.recorder
        return config

    def status(self):
        stored = self._stored()
        env = self.env_config or {}
        base = self._base_config() or {}
        config = self.client_config()
        key = self._stored_key(stored) or self._env_key() or str(base.get("webai_key", ""))
        protocol = stored.get("protocol", "").strip() or str(
            base.get("webai_protocol", "") or "chat"
        )
        inherited = bool(self.base is not None and not stored)
        return {
            "configured": bool(config),
            "editable": True,
            "inherited": inherited,
            "source": "console" if stored else ("environment" if env else ("inherited" if inherited else "")),
            "endpoint": stored.get("endpoint", "") or str(env.get("webai_endpoint", ""))
            or str(base.get("webai_endpoint", "")),
            "protocol": protocol,
            "protocol_label": PROTOCOL_LABELS.get(protocol, protocol),
            "model": stored.get("model", "") or str(env.get("webai_model", ""))
            or str(base.get("webai_model", "")),
            "model_fallbacks": self._fallback_list(
                stored.get("model_fallbacks") or env.get("webai_model_fallbacks") or "[]",
            ),
            "vision_model": stored.get("vision_model", "")
            or str(env.get("webai_vision_model", "")),
            "vision_model_fallbacks": self._fallback_list(
                stored.get("vision_model_fallbacks", "[]"),
            ),
            "timeout": int(stored.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
            "stream": (stored.get("stream", DEFAULT_STREAM) or DEFAULT_STREAM) == "true",
            "proxy": stored.get("proxy", "") or str(base.get("http_proxy", "")),
            "max_tokens": int(stored.get("max_tokens") or base.get("webai_max_tokens") or 0),
            "has_key": bool(key),
            "key_source": "console" if self._stored_key(stored)
            else ("environment" if (self._env_key() or base.get("webai_key")) else ""),
        }

    @staticmethod
    def _fallback_list(value):
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    def save(self, payload):
        if not isinstance(payload, dict):
            raise ProviderError("AI_PROVIDER_INVALID_FIELD")
        unknown = set(payload) - set(FIELDS) - {"api_key", "clear_key"}
        if unknown:
            raise ProviderError("AI_PROVIDER_INVALID_FIELD", sorted(unknown)[0])
        stored = self._stored()
        values = {}
        if "endpoint" in payload:
            raw_endpoint = str(payload["endpoint"] or "").strip()
            # 图片通道留空表示沿用文本通道的地址
            values["endpoint"] = None if (self.base is not None and not raw_endpoint) \
                else _endpoint(payload["endpoint"])
        if "protocol" in payload:
            protocol = str(payload["protocol"]).strip().lower()
            if protocol not in PROTOCOLS:
                raise ProviderError("AI_PROVIDER_INVALID_PROTOCOL")
            values["protocol"] = protocol
        if "model" in payload:
            values["model"] = _model(payload["model"], "model")
        if "vision_model" in payload:
            values["vision_model"] = _model(payload["vision_model"], "vision_model", allow_empty=True)
        if "model_fallbacks" in payload:
            values["model_fallbacks"] = json.dumps(_fallbacks(payload["model_fallbacks"], "model_fallbacks"))
        if "vision_model_fallbacks" in payload:
            values["vision_model_fallbacks"] = json.dumps(
                _fallbacks(payload["vision_model_fallbacks"], "vision_model_fallbacks"),
            )
        if "timeout" in payload:
            values["timeout"] = _timeout(payload["timeout"])
        if "stream" in payload:
            values["stream"] = _stream(payload["stream"])
        if "proxy" in payload:
            values["proxy"] = _proxy(payload["proxy"]) or None
        if "max_tokens" in payload:
            values["max_tokens"] = _max_tokens(payload["max_tokens"])
        merged = {
            "endpoint": values.get("endpoint", stored.get("endpoint", "")),
            "model": values.get("model", stored.get("model", "")),
        }
        if self.base is not None and not merged["endpoint"]:
            merged["endpoint"] = self._base_config().get("webai_endpoint", "") if self._base_config() else ""
        if not merged["endpoint"] or not merged["model"]:
            raise ProviderError("AI_PROVIDER_INCOMPLETE")
        key = payload.get("api_key")
        clear = payload.get("clear_key") is True
        if clear:
            self.store.save_provider_settings(values, clear=("api_key",), prefix=self.prefix)
        elif key is not None:
            key = _text(key, "api_key", limit=MAX_KEY_CHARS)
            if len(key) < 8:
                raise ProviderError("AI_PROVIDER_INVALID_KEY")
            self.store.save_provider_settings(
                {**values, "api_key": self._encrypt(key)}, prefix=self.prefix,
            )
        else:
            self.store.save_provider_settings(values, prefix=self.prefix)
        self.generation += 1
        return self.status()

    def client(self):
        config = self.client_config()
        if not config:
            raise ProviderError("AI_PROVIDER_NOT_CONFIGURED")
        try:
            return WebAIClient(config)
        except WebAIError as exc:
            raise ProviderError("AI_PROVIDER_INVALID_CONFIG") from exc

    def list_models(self):
        client = self.client()
        try:
            details = client.model_details()
        except WebAIError as exc:
            raise ProviderError("AI_PROVIDER_UNREACHABLE") from exc
        finally:
            client.close()
        models = []
        for item in details:
            name = item.get("id")
            if not isinstance(name, str) or not name:
                continue
            architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
            inputs = architecture.get("input_modalities")
            inputs = inputs if isinstance(inputs, list) else []
            models.append({
                "id": name,
                "type": item.get("type") if isinstance(item.get("type"), str) else "",
                "image_policy": item.get("image_policy")
                if isinstance(item.get("image_policy"), str) else "",
                # OpenRouter-style catalogues describe modalities, not image_policy.
                "image": "image" in inputs or item.get("image_policy") in {"optional", "required"},
            })
        return {
            "models": models, "endpoint": client.endpoint, "protocol": client.protocol,
            "image_capable": sum(1 for item in models if item["image"]),
        }

    def test(self, question=None):
        """Send one question through the saved provider and return the raw reply."""
        client = self.client()
        payload = TEST_QUESTION
        if isinstance(question, dict) and question:
            payload = {
                "title": _text(question.get("title"), "title", limit=4000),
                "type": _text(question.get("type"), "type", limit=32),
                "options": _text(question.get("options"), "options", limit=8000, allow_empty=True),
            }
        client.context = {"channel": "api", "job_id": "", "request_id": ""}
        started = time.monotonic()
        try:
            reply = client.complete(payload)
        except WebAIError as exc:
            raise ProviderError("AI_PROVIDER_TEST_FAILED", str(exc)) from exc
        finally:
            client.close()
        return {
            "ok": True, "model": client.selected_model or client.model,
            "protocol": client.protocol, "endpoint": client.endpoint,
            "reply": reply, "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        }
