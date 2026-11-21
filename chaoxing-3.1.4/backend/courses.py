"""Bounded course discovery with private subprocesses and short-polling results."""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from backend.course_worker import public_courses, validate_public_course_detail

ROOT = Path(__file__).resolve().parents[1]
MAX_OUTPUT = 2 * 1024 * 1024
ERRORS = {"LOGIN_REQUIRED", "COURSE_FETCH_FAILED"}
DETAIL_ERRORS = ERRORS | {"COURSE_NOT_FOUND", "COURSE_DETAIL_FETCH_FAILED"}
log = logging.getLogger(__name__)


@dataclass
class Lookup:
    account_id: str
    request_id: str
    process: object
    directory: object
    started: float
    reader: object = None
    packet: object = None
    error: str = ""


@dataclass
class DetailLookup:
    account_id: str
    course_id: str
    request_id: str
    process: object
    directory: object
    started: float
    reader: object = None
    packet: object = None
    error: str = ""


class CourseCatalog:
    def __init__(self, store, state_dir, process_factory=None, max_workers=2, timeout=300,
                 private_env_names=()):
        self.store = store
        self.directory = Path(state_dir) / "course-requests"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.process_factory = process_factory or subprocess.Popen
        self.private_env_names = tuple(private_env_names)
        self.max_workers, self.timeout = max_workers, timeout
        self.workers = {}
        self.detail_workers = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.store.recover_courses()
        self.store.recover_course_details()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="course-catalog", daemon=True)
        self.thread.start()

    def request(self, account_id):
        result = self.store.request_courses(account_id)
        self.wake.set()
        return result

    def details(self, account_id, course_id):
        return self.store.course_detail(account_id, course_id)

    def request_details(self, account_id, course_id):
        result = self.store.request_course_detail(account_id, course_id)
        self.wake.set()
        return result

    @staticmethod
    def _read(running):
        try:
            raw = running.process.stdout.read(MAX_OUTPUT + 1)
            if len(raw) <= MAX_OUTPUT:
                running.packet = json.loads(raw)
        except (OSError, ValueError, UnicodeError):
            pass

    def _spawn(self, request):
        directory = running = None
        try:
            directory = tempfile.TemporaryDirectory(
                prefix=request["request_id"] + "-", dir=self.directory,
            )
            payload = {"credentials": self.store.account_credentials(request["account_id"])}
            env = dict(os.environ)
            for key in ("WEBAI_API_KEY", "WEBAI_VISION_API_KEY", "CHAOXING_SERVICE_TOKEN",
                        *self.private_env_names):
                env.pop(key, None)
            env.update(
                PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                CHAOXING_HEADLESS="1", CHAOXING_LOG_FILE=str(Path(directory.name) / "fetch.log"),
                OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
            )
            process = self.process_factory(
                [sys.executable, "-m", "backend.course_worker"], cwd=directory.name, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            running = Lookup(
                request["account_id"], request["request_id"], process, directory, time.monotonic()
            )
            with self.lock:
                self.workers[running.request_id] = running
            self.store.course_worker_pid(running.account_id, running.request_id, process.pid)
            running.reader = threading.Thread(
                target=self._read, args=(running,), name="course-result", daemon=True,
            )
            running.reader.start()
            process.stdin.write(json.dumps(payload, ensure_ascii=True).encode() + b"\n")
            process.stdin.flush()
        except Exception:
            if running:
                running.error = "LOOKUP_START_FAILED"
                if running.process.poll() is None:
                    running.process.kill()
            else:
                if directory:
                    directory.cleanup()
                self.store.finish_courses(
                    request["account_id"], request["request_id"], error="LOOKUP_START_FAILED",
                )
            log.error("Course lookup startup failed; sensitive details suppressed")

    def _spawn_detail(self, request):
        directory = running = None
        try:
            directory = tempfile.TemporaryDirectory(
                prefix=request["request_id"] + "-", dir=self.directory,
            )
            payload = {
                "mode": "detail", "course_id": request["course_id"],
                "credentials": self.store.account_credentials(request["account_id"]),
            }
            env = dict(os.environ)
            for key in ("WEBAI_API_KEY", "WEBAI_VISION_API_KEY", "CHAOXING_SERVICE_TOKEN",
                        *self.private_env_names):
                env.pop(key, None)
            env.update(
                PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                CHAOXING_HEADLESS="1", CHAOXING_LOG_FILE=str(Path(directory.name) / "fetch.log"),
                OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
            )
            process = self.process_factory(
                [sys.executable, "-m", "backend.course_worker"], cwd=directory.name, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            running = DetailLookup(
                request["account_id"], request["course_id"], request["request_id"],
                process, directory, time.monotonic(),
            )
            with self.lock:
                self.detail_workers[running.request_id] = running
            self.store.course_detail_worker_pid(
                running.account_id, running.course_id, running.request_id, process.pid,
            )
            running.reader = threading.Thread(
                target=self._read, args=(running,), name="course-detail-result", daemon=True,
            )
            running.reader.start()
            process.stdin.write(json.dumps(payload, ensure_ascii=True).encode() + b"\n")
            process.stdin.flush()
        except Exception:
            if running:
                running.error = "DETAIL_LOOKUP_START_FAILED"
                if running.process.poll() is None:
                    running.process.kill()
            else:
                if directory:
                    directory.cleanup()
                self.store.finish_course_detail(
                    request["account_id"], request["course_id"], request["request_id"],
                    error="DETAIL_LOOKUP_START_FAILED",
                )
            log.error("Course detail lookup startup failed; sensitive details suppressed")

    def _finish(self, running):
        if running.reader:
            running.reader.join(timeout=2)
        error = running.error or "COURSE_FETCH_FAILED"
        courses = None
        packet = running.packet
        if not running.error and isinstance(packet, dict):
            if packet.get("state") == "done" and running.process.returncode == 0:
                try:
                    courses = public_courses(packet.get("courses"))
                    error = ""
                except ValueError:
                    pass
            elif isinstance(packet.get("error_code"), str) and packet["error_code"] in ERRORS:
                error = packet["error_code"]
        self.store.finish_courses(
            running.account_id, running.request_id, courses=courses, error=error,
        )
        for stream in (running.process.stdin, running.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        running.directory.cleanup()
        with self.lock:
            self.workers.pop(running.request_id, None)

    def _finish_detail(self, running):
        if running.reader:
            running.reader.join(timeout=2)
        error = running.error or "COURSE_DETAIL_FETCH_FAILED"
        detail = None
        packet = running.packet
        if not running.error and isinstance(packet, dict):
            if packet.get("state") == "done" and running.process.returncode == 0:
                try:
                    detail = validate_public_course_detail(packet)
                    error = ""
                except ValueError:
                    pass
            elif isinstance(packet.get("error_code"), str) and packet["error_code"] in DETAIL_ERRORS:
                error = packet["error_code"]
        self.store.finish_course_detail(
            running.account_id, running.course_id, running.request_id,
            detail=detail, error=error,
        )
        for stream in (running.process.stdin, running.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        running.directory.cleanup()
        with self.lock:
            self.detail_workers.pop(running.request_id, None)

    def tick(self):
        with self.lock:
            entries = list(self.workers.values())
        for running in entries:
            if running.process.poll() is not None:
                self._finish(running)
            elif time.monotonic() - running.started > self.timeout:
                running.error = "LOOKUP_TIMEOUT"
                running.process.kill()
        with self.lock:
            detail_entries = list(self.detail_workers.values())
        for running in detail_entries:
            if running.process.poll() is not None:
                self._finish_detail(running)
            elif time.monotonic() - running.started > self.timeout:
                running.error = "DETAIL_LOOKUP_TIMEOUT"
                running.process.kill()
        while not self.stop_event.is_set():
            with self.lock:
                if len(self.workers) >= self.max_workers:
                    break
            request = self.store.claim_courses()
            if request is None:
                break
            self._spawn(request)
        while not self.stop_event.is_set():
            with self.lock:
                if len(self.detail_workers) >= 1:
                    break
            request = self.store.claim_course_details()
            if request is None:
                break
            self._spawn_detail(request)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.error("Course lookup iteration failed; sensitive details suppressed")
            self.wake.wait(0.25)
            self.wake.clear()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=15)
        with self.lock:
            entries = list(self.workers.values())
            detail_entries = list(self.detail_workers.values())
        for running in entries:
            running.error = "LOOKUP_INTERRUPTED"
            try:
                running.process.stdin.close()
            except (OSError, ValueError):
                pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(r.process.poll() is None for r in entries):
            time.sleep(0.05)
        for running in entries:
            if running.process.poll() is None:
                running.process.kill()
            running.process.wait(timeout=5)
            self._finish(running)
        for running in detail_entries:
            running.error = "DETAIL_LOOKUP_INTERRUPTED"
            try:
                running.process.stdin.close()
            except (OSError, ValueError):
                pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(
                running.process.poll() is None for running in detail_entries
        ):
            time.sleep(0.05)
        for running in detail_entries:
            if running.process.poll() is None:
                running.process.kill()
            running.process.wait(timeout=5)
            self._finish_detail(running)
        self.store.recover_courses()
        self.store.recover_course_details()
