"""Supervise bounded, account-isolated subprocesses with durable lifecycle states."""

from dataclasses import dataclass
import hmac
import json
import logging
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
MAX_EVENT_BYTES = 4 * 1024 * 1024


@dataclass
class Running:
    process: object
    token: str
    started: float
    reader: object = None
    result: object = None
    stopping: float = 0
    failure: str = ""


class Scheduler:
    def __init__(self, store, state_dir, internal_url, max_workers=30, ai_wait_timeout=3600,
                 process_factory=None, max_runtime=259200, private_env_names=()):
        self.store = store
        self.state_dir = Path(state_dir)
        self.internal_url = internal_url
        self.max_workers = max_workers
        self.ai_wait_timeout = ai_wait_timeout
        self.max_runtime = max_runtime
        self.process_factory = process_factory or subprocess.Popen
        self.private_env_names = tuple(private_env_names)
        self.workers = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None

    def start(self):
        self.store.recover()
        self.thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self.thread.start()

    def authenticate_worker(self, token):
        if not token or not token.isascii() or len(token) > 256:
            return None
        with self.lock:
            entries = list(self.workers.items())
        for job_id, running in entries:
            if hmac.compare_digest(token, running.token) and running.process.poll() is None:
                if self.store.worker_state(job_id)["desired"] == "run":
                    return job_id
        return None

    def _spawn(self, job_id):
        directory = self.state_dir / "workers" / job_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        token = secrets.token_urlsafe(36)
        try:
            payload = self.store.payload(job_id)
            payload.update(
                worker_token=token, internal_url=self.internal_url,
                ai_wait_timeout=self.ai_wait_timeout,
                video_guard=self.store.video_guard(),
            )
            env = dict(os.environ)
            for key in ("WEBAI_API_KEY", "WEBAI_VISION_API_KEY", "CHAOXING_SERVICE_TOKEN",
                        *self.private_env_names):
                env.pop(key, None)
            env.update(
                PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                CHAOXING_HEADLESS="1", CHAOXING_LOG_FILE=str(directory / "worker.log"),
                OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
            )
            process = self.process_factory(
                [sys.executable, "-m", "backend.worker"], cwd=str(directory), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            running = Running(process, token, time.monotonic())
            with self.lock:
                self.workers[job_id] = running
            self.store.set_state(job_id, "running", pid=process.pid)
            running.reader = threading.Thread(
                target=self._read, args=(job_id, running), name=f"events-{job_id[:8]}", daemon=True,
            )
            running.reader.start()
            process.stdin.write(json.dumps(payload, ensure_ascii=True).encode() + b"\n")
            process.stdin.flush()
        except Exception:
            with self.lock:
                running = self.workers.get(job_id)
            if running is not None:
                running.failure = "START_FAILED"
                self._request_stop(running)
            else:
                self._retry_or_fail(job_id, "START_FAILED")
            log.error("Worker startup failed for job %s", job_id)

    def _read(self, job_id, running):
        for raw in iter(lambda: running.process.stdout.readline(MAX_EVENT_BYTES + 1), b""):
            if len(raw) > MAX_EVENT_BYTES:
                continue
            try:
                packet = json.loads(raw)
                kind, fields = packet["event"], packet["data"]
                if kind not in {
                    "heartbeat", "phase", "task", "chapter", "chapter_result",
                    "course", "video", "ai", "quiz", "course_result", "course_outline", "result",
                    "video_rollback",
                } or not isinstance(fields, dict):
                    continue
                clean = {}
                for key, value in fields.items():
                    if key not in {
                        "phase", "course", "course_id", "chapter", "name", "position", "duration",
                        "completed", "total", "result", "outcome", "error_code",
                        "ai_state", "ai_question", "ai_type", "ai_error", "failed_courses",
                        "ai_mode", "ai_image_count",
                        "chapter_id", "job_id", "video_state", "video_error", "replay_count",
                        "quiz_state", "quiz_answer", "remaining_chapters", "detail", "manual_tasks",
                        "rollback_from", "rollback_to", "rollback_dropped", "rollback_count",
                        "rollback_action",
                    }:
                        continue
                    if key == "detail" and kind == "course_outline":
                        from backend.course_worker import validate_public_course_detail
                        clean[key] = validate_public_course_detail(value)
                    elif key == "manual_tasks":
                        clean[key] = [
                            {
                                "type": str(task.get("type", ""))[:40],
                                "title": str(task.get("title", "待检查任务"))[:200],
                                "card_num": str(task.get("card_num", ""))[:6],
                            }
                            for task in (value if isinstance(value, list) else [])[:40]
                            if isinstance(task, dict)
                        ]
                    elif isinstance(value, str):
                        clean[key] = value[:300]
                    elif isinstance(value, (int, float)) and math.isfinite(value):
                        clean[key] = max(0, min(value, 10**9))
                if kind == "result":
                    with self.lock:
                        running.result = clean
                self.store.event(job_id, kind, clean)
            except (ValueError, KeyError, TypeError):
                continue

    @staticmethod
    def _request_stop(running):
        if running.stopping:
            return
        running.stopping = time.monotonic()
        try:
            running.process.stdin.close()
        except (OSError, ValueError):
            pass

    def _retry_or_fail(self, job_id, code):
        self.store.record_failure(job_id, code)

    def _finish(self, job_id, running):
        if running.reader is not None:
            running.reader.join(timeout=2)
        state = self.store.worker_state(job_id)
        if state["desired"] == "pause":
            self.store.set_state(job_id, "paused")
        elif state["desired"] == "cancel":
            self.store.set_state(job_id, "cancelled")
        elif self.stop_event.is_set():
            self.store.set_state(job_id, "queued", error="SERVICE_RESTART")
        elif running.failure:
            self.store.set_state(job_id, "needs_attention", error=running.failure)
        elif running.result and running.result.get("outcome") == "succeeded" \
                and running.process.returncode == 0:
            self.store.set_state(job_id, "succeeded")
        elif running.result and running.result.get("outcome") == "needs_attention":
            self.store.set_state(
                job_id, "needs_attention", error=running.result.get("error_code", "MANUAL_CHECK")
            )
        else:
            self._retry_or_fail(job_id, "WORKER_EXIT")
        for stream in (running.process.stdin, running.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        with self.lock:
            self.workers.pop(job_id, None)

    def tick(self):
        with self.lock:
            entries = list(self.workers.items())
        for job_id, running in entries:
            if running.process.poll() is not None:
                self._finish(job_id, running)
                continue
            state = self.store.worker_state(job_id)
            if state["desired"] != "run" or self.stop_event.is_set():
                self._request_stop(running)
            if time.monotonic() - running.started > self.max_runtime:
                running.failure = "RUN_TIMEOUT"
                self._request_stop(running)
            if state["heartbeat"] and time.time() - state["heartbeat"] > 90:
                running.failure = "HEARTBEAT_TIMEOUT"
                self._request_stop(running)
            if running.stopping and time.monotonic() - running.stopping > 3:
                running.process.kill()
        for _ in range(4):
            if self.stop_event.is_set():
                break
            with self.lock:
                if len(self.workers) >= self.max_workers:
                    break
            job_id = self.store.claim()
            if job_id is None:
                break
            self._spawn(job_id)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.error("Scheduler iteration failed; durable jobs were retained")
            self.wake.wait(0.5)
            self.wake.clear()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout=15)
        with self.lock:
            entries = list(self.workers.items())
        for _, running in entries:
            self._request_stop(running)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(r.process.poll() is None for _, r in entries):
            time.sleep(0.1)
        for job_id, running in entries:
            if running.process.poll() is None:
                running.process.kill()
            running.process.wait(timeout=5)
            self._finish(job_id, running)
