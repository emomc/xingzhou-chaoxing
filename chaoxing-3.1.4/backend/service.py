"""Authenticated JSON API. No UI, terminal prompts, or platform credentials in responses."""

from dataclasses import dataclass, field
import hmac
import ipaddress
import logging
import os
from pathlib import Path
import re
import time

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from api.webai_client import WebAIError, model_fallbacks
from api.question_images import MAX_IMAGE_REQUEST_BYTES
from backend import vision_bench
from backend.ai_provider import AIProvider, ProviderError
from backend.broker import AnswerBroker
from backend.courses import CourseCatalog
from backend.image_review import ImageReview
from backend.question_bank import QuestionBank
from backend.resources import ResourceMonitor
from backend.scheduler import Scheduler
from backend.security import Secrets, StateLock
from backend.store import Conflict, NotFound, Store

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[a-f0-9]{32}$")


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: ROOT / ".backend-state")
    host: str = "127.0.0.1"
    port: int = 3100
    max_workers: int = 30
    ai_workers: int = 1
    ai_wait_timeout: int = 3600
    max_runtime: int = 259200
    token: str | None = None
    ai_endpoint: str = "http://127.0.0.1:3001/v1"
    ai_model: str = "deepseek-search"
    ai_model_fallbacks: tuple[str, ...] = ()
    ai_vision_endpoint: str = ""
    ai_vision_model: str = "auto"
    ai_vision_model_fallbacks: tuple[str, ...] = ()
    ai_vision_key_env: str = "WEBAI_VISION_API_KEY"
    ai_api_endpoint: str = ""
    ai_api_model: str = ""
    ai_api_model_fallbacks: tuple[str, ...] = ()
    ai_api_vision_model: str = ""
    ai_api_vision_model_fallbacks: tuple[str, ...] = ()
    ai_api_key_env: str = "CHAOXING_AI_API_KEY"

    def __post_init__(self):
        self.state_dir = Path(self.state_dir).resolve()
        if not 0 <= self.port <= 65535:
            raise ValueError("Invalid port")
        if not 1 <= self.max_workers <= 64 or not 1 <= self.ai_workers <= 4:
            raise ValueError("Worker limits must be 1..64 (course) and 1..4 (AI)")
        if not 30 <= self.ai_wait_timeout <= 14400 or self.max_runtime < 30:
            raise ValueError("Invalid task deadlines")
        self.ai_model_fallbacks = model_fallbacks(self.ai_model_fallbacks, "ai_model_fallbacks")
        self.ai_vision_model_fallbacks = model_fallbacks(
            self.ai_vision_model_fallbacks, "ai_vision_model_fallbacks",
        )
        self.ai_api_model_fallbacks = model_fallbacks(
            self.ai_api_model_fallbacks, "ai_api_model_fallbacks",
        )
        self.ai_api_vision_model_fallbacks = model_fallbacks(
            self.ai_api_vision_model_fallbacks, "ai_api_vision_model_fallbacks",
        )


class Service:
    def __init__(self, settings, *, process_factory=None, client_factory=None,
                 course_process_factory=None):
        self.settings = settings
        settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_lock = StateLock(settings.state_dir)
        try:
            self.secrets = Secrets(settings.state_dir, settings.token)
            self.store = Store(settings.state_dir, self.secrets)
            self.courses = CourseCatalog(self.store, settings.state_dir, course_process_factory,
                                         private_env_names=tuple(filter(None, (
                                             settings.ai_vision_key_env, settings.ai_api_key_env,
                                         ))))
            self.question_bank = QuestionBank(self.store)
            self.image_review = ImageReview(self.store)
            self.resources = ResourceMonitor(settings.state_dir)
        except Exception:
            self.state_lock.close()
            raise
        self.started_at = time.time()
        self.api_ai_config = None
        env_api_config = None
        if settings.ai_api_endpoint and settings.ai_api_model \
                and os.environ.get(settings.ai_api_key_env):
            vision_model = settings.ai_api_vision_model or settings.ai_api_model
            env_api_config = {
                "webai_endpoint": settings.ai_api_endpoint,
                "webai_model": settings.ai_api_model,
                "webai_model_fallbacks": settings.ai_api_model_fallbacks,
                "webai_key_env": settings.ai_api_key_env,
                "webai_timeout": "180",
                "webai_stream": "false",
                "min_interval_seconds": "0",
                "webai_vision_model": vision_model,
                "webai_vision_model_fallbacks": settings.ai_api_vision_model_fallbacks,
                "webai_vision_force": "true",
            }
        self.api_ai_config = env_api_config
        self.api_provider = AIProvider(
            self.store, self.secrets, env_api_config, recorder=self.store.record_ai_conversation,
        )
        # 图片题可以单独配置一套 API（端点/模型/key 留空则沿用文本那套）
        self.vision_provider = AIProvider(
            self.store, self.secrets, None, recorder=self.store.record_ai_conversation,
            prefix="ai_vision_api_", base=self.api_provider,
        )
        self.scheduler = Scheduler(
            self.store, settings.state_dir, f"http://127.0.0.1:{settings.port}",
            settings.max_workers, settings.ai_wait_timeout, process_factory, settings.max_runtime,
            private_env_names=tuple(filter(None, (
                settings.ai_vision_key_env, settings.ai_api_key_env,
            ))),
        )
        self.broker = AnswerBroker(self.store, {
            "webai_endpoint": settings.ai_endpoint, "webai_model": settings.ai_model,
            "webai_model_fallbacks": settings.ai_model_fallbacks,
            "webai_key_env": "WEBAI_API_KEY", "webai_timeout": "240",
            "webai_stream": "true", "min_interval_seconds": "3",
            "webai_vision_endpoint": settings.ai_vision_endpoint,
            "webai_vision_model": settings.ai_vision_model,
            "webai_vision_model_fallbacks": settings.ai_vision_model_fallbacks,
            "webai_vision_key_env": settings.ai_vision_key_env if settings.ai_vision_endpoint else "",
            "webai_recorder": self.store.record_ai_conversation,
        }, settings.ai_workers, settings.ai_wait_timeout, client_factory,
            provider=self.api_provider, recorder=self.store.record_ai_conversation,
            vision_provider=self.vision_provider)
        self.closed = False

    def start(self, internal_url=None):
        if internal_url:
            self.scheduler.internal_url = internal_url
        self.courses.start()
        self.scheduler.start()
        self.broker.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.scheduler.stop()
        self.courses.stop()
        self.broker.stop()
        self.store.close()
        self.state_lock.close()


def create_app(service):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=65536, JSON_SORT_KEYS=False)
    app.json.ensure_ascii = False

    @app.before_request
    def authenticate():
        if request.path == "/healthz" and request.method == "GET":
            return None
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        if request.path.startswith("/internal/"):
            try:
                local = ipaddress.ip_address(request.remote_addr).is_loopback
            except ValueError:
                local = False
            g.job_id = service.scheduler.authenticate_worker(token) if local else None
            if not g.job_id:
                return jsonify(error="Worker authentication failed"), 401
            if request.path == "/internal/answers" and request.method == "POST":
                request.max_content_length = MAX_IMAGE_REQUEST_BYTES
        elif not token or not token.isascii() or len(token) > 512 \
                or not hmac.compare_digest(token, service.secrets.token):
            return jsonify(error="Authentication required"), 401
        return None

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(NotFound)
    def not_found(error):
        return jsonify(error=str(error)), 404

    @app.errorhandler(Conflict)
    def conflict(error):
        return jsonify(error=str(error)), 409

    @app.errorhandler(ProviderError)
    def provider_error(error):
        return jsonify(error=error.code), 400

    @app.errorhandler(ValueError)
    def invalid(_error):
        return jsonify(error="Invalid request fields"), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name), error.code

    @app.errorhandler(Exception)
    def unexpected(_error):
        logging.getLogger(__name__).error("API request failed; sensitive details suppressed")
        return jsonify(error="Internal service error"), 500

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value

    def account_fields(value, *, update=False):
        fields = []
        for name, limit in (("label", 80), ("username", 128), ("password", 512)):
            item = value.get(name)
            if update and name != "label" and (item is None or item == ""):
                fields.append(None)
                continue
            if not isinstance(item, str) or not item or len(item) > limit:
                raise ValueError(name)
            if name != "password":
                item = item.strip()
                if not item or any(ord(char) < 32 for char in item):
                    raise ValueError(name)
            fields.append(item)
        return fields

    @app.get("/healthz")
    def liveness():
        return jsonify(status="ok", service="chaoxing-backend")

    @app.get("/v1/health")
    def health():
        thread = service.scheduler.thread
        ai_alive = sum(thread.is_alive() for thread in service.broker.threads)
        status = "starting" if thread is None else "degraded"
        if thread and thread.is_alive() and ai_alive == service.settings.ai_workers \
                and service.courses.thread and service.courses.thread.is_alive() \
                and not service.closed:
            status = "ok"
        provider = service.api_provider.status()
        vision_provider = service.vision_provider.status()
        return jsonify(
            status=status, version=1, max_workers=service.settings.max_workers,
            ai_workers=service.settings.ai_workers,
            ai_workers_alive=ai_alive,
            ai_configured=bool(os.environ.get("WEBAI_API_KEY") or provider["configured"]),
            ai_model=service.settings.ai_model,
            ai_model_fallbacks=service.settings.ai_model_fallbacks,
            ai_vision_model=service.settings.ai_vision_model,
            ai_vision_model_fallbacks=service.settings.ai_vision_model_fallbacks,
            ai_vision_separate_endpoint=bool(service.settings.ai_vision_endpoint),
            ai_channel=service.broker.channel("text"),
            ai_vision_channel=service.broker.channel("vision"),
            ai_vision_enabled=service.store.setting("ai_vision_enabled", "0") == "1",
            ai_api_configured=provider["configured"],
            ai_api_model=provider["model"] if provider["configured"] else "",
            ai_api_vision_model=provider["vision_model"] if provider["configured"] else "",
            ai_api_protocol=provider["protocol"],
            ai_api_source=provider["source"],
            ai_vision_api_configured=vision_provider["configured"],
            ai_vision_api_separate=vision_provider["source"] == "console",
            ai_vision_api_model=vision_provider["model"] if vision_provider["configured"] else "",
            ai_vision_api_protocol=vision_provider["protocol"],
            ai_vision_fallback=service.store.setting("ai_vision_fallback", "1") == "1",
            ai_vision_direct=service.store.setting("ai_vision_direct", "1") == "1",
            video_guard=service.store.video_guard(),
            video_rollback_count=service.store.video_rollbacks(limit=1)["count"],
            storage=service.store.storage_summary(),
            uptime_seconds=int(time.time() - service.started_at), **service.store.stats(),
        )

    @app.get("/v1/accounts")
    def accounts():
        return jsonify(accounts=service.store.accounts())

    @app.get("/v1/pacing")
    def pacing_status():
        from api import pacing

        return jsonify(pacing=pacing.snapshot({"enabled": True}), resting=service.store.resting_jobs())

    @app.get("/v1/video/guard")
    def video_guard_status():
        return jsonify(**service.store.video_guard(), rollbacks=service.store.video_rollbacks())

    @app.put("/v1/video/guard")
    def save_video_guard():
        return jsonify(service.store.save_video_guard(body()))

    @app.get("/v1/ai/channel")
    def ai_channel():
        return jsonify(service.broker.channel_status())

    @app.put("/v1/ai/channel")
    def set_ai_channel():
        value = body()
        if set(value) - {"channel", "kind"} or not isinstance(value.get("channel"), str):
            raise ValueError("Invalid AI channel")
        kind = value.get("kind", "text")
        if kind not in {"text", "vision"}:
            raise ValueError("Invalid AI channel kind")
        return jsonify(service.broker.set_channel(value["channel"], kind))

    @app.put("/v1/ai/vision")
    def set_ai_vision():
        value = body()
        if set(value) != {"enabled"}:
            raise ValueError("Invalid image question switch")
        return jsonify(service.broker.set_vision_enabled(value["enabled"]))

    @app.put("/v1/ai/vision-fallback")
    def set_ai_vision_fallback():
        value = body()
        if set(value) != {"enabled"}:
            raise ValueError("Invalid image fallback switch")
        return jsonify(service.broker.set_vision_fallback(value["enabled"]))

    @app.put("/v1/ai/vision-direct")
    def set_ai_vision_direct():
        value = body()
        if set(value) != {"enabled"}:
            raise ValueError("Invalid image answer mode")
        return jsonify(service.broker.set_vision_direct(value["enabled"]))

    @app.get("/v1/ai/provider")
    def ai_provider():
        return jsonify(service.api_provider.status())

    @app.put("/v1/ai/provider")
    def save_ai_provider():
        return jsonify(service.api_provider.save(body()))

    @app.get("/v1/ai/vision-provider")
    def ai_vision_provider():
        return jsonify(service.vision_provider.status())

    @app.put("/v1/ai/vision-provider")
    def save_ai_vision_provider():
        return jsonify(service.vision_provider.save(body()))

    @app.post("/v1/ai/vision-bench")
    def ai_vision_bench():
        value = request.get_json(silent=True) if request.get_data() else {}
        value = value or {}
        if set(value) - {"limit", "mode"}:
            raise ValueError("Invalid vision bench request")
        try:
            limit = int(value.get("limit", 6))
        except (TypeError, ValueError):
            raise ValueError("Invalid vision bench request") from None
        mode = value.get("mode", "direct")
        if not 1 <= limit <= 20 or mode not in {"direct", "pipeline"}:
            raise ValueError("Invalid vision bench request")
        config = service.vision_provider.client_config() or service.api_provider.client_config()
        if not config:
            raise ValueError("API AI channel is not configured")
        return jsonify(vision_bench.run(service.store, config, limit, mode))

    @app.get("/v1/ai/models")
    def ai_models():
        if set(request.args) - {"route"} or request.args.get("route", "text") not in {"text", "vision"}:
            raise ValueError("Invalid model query")
        if request.args.get("route") == "vision" and service.vision_provider.client_config():
            return jsonify(service.vision_provider.list_models())
        return jsonify(service.api_provider.list_models())

    @app.post("/v1/ai/test")
    def ai_test():
        if request.get_data() and not isinstance(request.get_json(silent=True), dict):
            raise ValueError("Invalid provider test")
        value = request.get_json(silent=True) or {}
        if set(value) - {"question"}:
            raise ValueError("Invalid provider test")
        question = value.get("question")
        if question is not None and not isinstance(question, dict):
            raise ValueError("Invalid provider test")
        return jsonify(service.api_provider.test(question))

    @app.get("/v1/ai/conversations")
    def ai_conversations():
        if set(request.args) - {"after", "limit", "job_id"}:
            raise ValueError("Invalid conversation query")
        try:
            after = int(request.args.get("after", 0) or 0)
            limit = int(request.args.get("limit", 50) or 50)
        except ValueError:
            raise ValueError("Invalid conversation query") from None
        job_id = request.args.get("job_id", "").strip()
        if after < 0 or (job_id and not ID.fullmatch(job_id)):
            raise ValueError("Invalid conversation query")
        rows = service.store.ai_conversations(after, limit, job_id)
        return jsonify(
            conversations=rows, latest=rows[0]["id"] if rows else 0,
            retention=service.store.AI_CONVERSATION_KEEP,
        )

    @app.get("/v1/resources")
    def resources():
        return jsonify(service.resources.snapshot())

    @app.get("/v1/question-bank/stats")
    def question_bank_stats():
        return jsonify(service.question_bank.stats())

    @app.get("/v1/question-bank")
    def question_bank_list():
        return jsonify(service.question_bank.list(request.args))

    @app.get("/v1/question-bank/<answer_id>")
    def question_bank_detail(answer_id):
        return jsonify(service.question_bank.detail(answer_id))

    @app.get("/v1/question-bank/<answer_id>/images/<image_id>")
    def question_bank_image(answer_id, image_id):
        return jsonify(service.question_bank.image(answer_id, image_id))

    @app.get("/v1/image-questions")
    def image_question_list():
        return jsonify(service.image_review.list(request.args))

    @app.get("/v1/image-questions/<answer_id>")
    def image_question_detail(answer_id):
        return jsonify(service.image_review.detail(answer_id))

    @app.get("/v1/image-questions/<answer_id>/images/<image_id>")
    def image_question_image(answer_id, image_id):
        return jsonify(service.image_review.image(answer_id, image_id))

    @app.post("/v1/accounts")
    def create_account():
        return jsonify(service.store.save_account(*account_fields(body()))), 201

    @app.put("/v1/accounts/<account_id>")
    def update_account(account_id):
        if not ID.fullmatch(account_id):
            raise ValueError("Invalid account")
        return jsonify(service.store.save_account(
            *account_fields(body(), update=True), account_id=account_id,
        ))

    @app.delete("/v1/accounts/<account_id>")
    def delete_account(account_id):
        if not ID.fullmatch(account_id):
            raise ValueError("Invalid account")
        service.store.delete_account(account_id)
        return "", 204

    @app.get("/v1/accounts/<account_id>/courses")
    def account_courses(account_id):
        if not ID.fullmatch(account_id):
            raise ValueError("Invalid account")
        return jsonify(service.store.courses(account_id))

    @app.post("/v1/accounts/<account_id>/courses")
    def fetch_account_courses(account_id):
        if not ID.fullmatch(account_id) or (request.get_data() and body()):
            raise ValueError("Invalid course lookup")
        result = service.courses.request(account_id)
        return jsonify(result), 202

    @app.get("/v1/accounts/<account_id>/courses/<course_id>")
    def account_course_detail(account_id, course_id):
        if not ID.fullmatch(account_id) or not re.fullmatch(r"[0-9]{1,20}", course_id):
            raise ValueError("Invalid course detail")
        return jsonify(service.courses.details(account_id, course_id))

    @app.post("/v1/accounts/<account_id>/courses/<course_id>")
    def fetch_account_course_detail(account_id, course_id):
        if not ID.fullmatch(account_id) or not re.fullmatch(r"[0-9]{1,20}", course_id) \
                or (request.get_data() and body()):
            raise ValueError("Invalid course detail")
        result = service.courses.request_details(account_id, course_id)
        return jsonify(result), 202

    @app.get("/v1/jobs")
    def jobs():
        if set(request.args) - {"limit", "archived"}:
            raise ValueError("Invalid job query")
        limit = int(request.args.get("limit", 200))
        archived = request.args.get("archived", "0")
        if not 1 <= limit <= 1000 or archived not in {"0", "1"}:
            raise ValueError("Invalid limit")
        return jsonify(jobs=service.store.jobs(limit, archived=archived == "1"))

    @app.post("/v1/jobs")
    def create_job():
        value = body()
        if set(value) - {
            "account_id", "course_ids", "videos_only", "submit", "preview",
            "experimental_vision", "human_pacing", "point_ids",
        }:
            raise ValueError("Unknown fields")
        account_id, courses = value.get("account_id"), value.get("course_ids")
        if not isinstance(account_id, str) or not ID.fullmatch(account_id):
            raise ValueError("Invalid account")
        if not isinstance(courses, list) or not 1 <= len(courses) <= 50:
            raise ValueError("Explicit course IDs are required")
        if any(not isinstance(item, str) or not re.fullmatch(r"[0-9]{1,20}", item) for item in courses):
            raise ValueError("Invalid course IDs")
        if len(set(courses)) != len(courses):
            raise ValueError("Duplicate course IDs")
        # 可按章节下发：{课程ID: [章节ID, ...]}，没列出的课程照旧整门课
        selection = value.get("point_ids") or {}
        if not isinstance(selection, dict):
            raise ValueError("Invalid chapter selection")
        points = {}
        for course_id, chapter_ids in selection.items():
            if not isinstance(course_id, str) or course_id not in courses:
                raise ValueError("Invalid chapter selection")
            if not isinstance(chapter_ids, list) or not 1 <= len(chapter_ids) <= 2000:
                raise ValueError("Invalid chapter IDs")
            if any(not isinstance(item, str) or not re.fullmatch(r"[0-9]{1,20}", item)
                   for item in chapter_ids):
                raise ValueError("Invalid chapter IDs")
            if len(set(chapter_ids)) != len(chapter_ids):
                raise ValueError("Duplicate chapter IDs")
            points[course_id] = chapter_ids
        submit = value.get("submit", False)
        preview = value.get("preview", not submit)
        videos_only = value.get("videos_only", False)
        experimental_vision = value.get("experimental_vision", False)
        human_pacing = value.get("human_pacing", True)
        if any(type(item) is not bool for item in (
            submit, preview, videos_only, experimental_vision, human_pacing,
        )):
            raise ValueError("Expected boolean options")
        if submit and (preview or videos_only):
            raise ValueError("Contradictory submission options")
        key = request.headers.get("Idempotency-Key") or None
        if key is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key):
            raise ValueError("Invalid idempotency key")
        spec = {
            "course_ids": courses, "submit": submit, "preview": preview,
            "videos_only": videos_only, "experimental_vision": experimental_vision,
            "human_pacing": human_pacing,
        }
        if points:
            spec["point_ids"] = points
        job, created = service.store.create_job(account_id, spec, key)
        service.scheduler.wake.set()
        return jsonify(job), 201 if created else 200

    @app.get("/v1/jobs/<job_id>")
    def job(job_id):
        return jsonify(service.store.job(job_id))

    @app.delete("/v1/jobs/<job_id>")
    def delete_job(job_id):
        service.store.delete_job(job_id)
        return "", 204

    @app.get("/v1/jobs/<job_id>/events")
    def events(job_id):
        after = int(request.args.get("after", 0))
        if after < 0:
            raise ValueError("Invalid event cursor")
        return jsonify(events=service.store.events(job_id, after))

    @app.post("/v1/jobs/batch")
    def batch_jobs():
        value = body()
        action, job_ids = value.get("action"), value.get("job_ids")
        actions = {
            "pause": {"queued", "starting", "running", "retry_wait"},
            "resume": {"paused"},
            "cancel": {"queued", "starting", "running", "retry_wait", "paused", "needs_attention"},
            "retry": {"succeeded", "failed", "cancelled", "needs_attention"},
            "delete": {"succeeded", "failed", "cancelled", "needs_attention"},
            "archive": {"succeeded", "failed", "cancelled"},
            "unarchive": {"succeeded", "failed", "cancelled"},
        }
        if set(value) != {"action", "job_ids"} or not isinstance(action, str) or action not in actions:
            raise ValueError("Invalid batch action")
        if not isinstance(job_ids, list) or not 1 <= len(job_ids) <= 1000 \
                or any(not isinstance(item, str) or not ID.fullmatch(item) for item in job_ids) \
                or len(set(job_ids)) != len(job_ids):
            raise ValueError("Explicit unique job IDs are required")
        results = []
        for job_id in job_ids:
            try:
                if action == "delete":
                    service.store.delete_job(job_id)
                    results.append({"id": job_id, "ok": True, "deleted": True})
                elif action == "archive":
                    job = service.store.archive_job(job_id)
                    results.append({"id": job_id, "ok": True, "job": job})
                elif action == "unarchive":
                    job = service.store.unarchive_job(job_id)
                    results.append({"id": job_id, "ok": True, "job": job})
                else:
                    job = service.store.action(job_id, action, allowed_statuses=actions[action])
                    results.append({"id": job_id, "ok": True, "job": job})
            except (Conflict, NotFound) as exc:
                results.append({
                    "id": job_id, "ok": False, "error": str(exc),
                    "code": "NOT_FOUND" if isinstance(exc, NotFound) else "STATE_CONFLICT",
                })
        succeeded = sum(item["ok"] for item in results)
        if succeeded and action not in {"delete", "archive", "unarchive"}:
            service.scheduler.wake.set()
        return jsonify(
            action=action, requested=len(job_ids), succeeded=succeeded,
            failed=len(results) - succeeded, results=results,
        )

    @app.post("/v1/jobs/<job_id>/<action>")
    def action(job_id, action):
        if action not in {"pause", "resume", "cancel", "retry", "archive", "unarchive"}:
            return jsonify(error="Unknown action"), 404
        if action == "archive":
            result = service.store.archive_job(job_id)
        elif action == "unarchive":
            result = service.store.unarchive_job(job_id)
        else:
            result = service.store.action(job_id, action)
            service.scheduler.wake.set()
        return jsonify(result)

    @app.post("/internal/answers")
    def request_answer():
        result = service.broker.request(g.job_id, body())
        return jsonify(result), 200 if result["state"] in {"done", "error"} else 202

    @app.get("/internal/answers/<answer_id>")
    def answer(answer_id):
        result = service.store.answer(g.job_id, answer_id)
        return jsonify(result), 200 if result["state"] in {"done", "error"} else 202

    return app
