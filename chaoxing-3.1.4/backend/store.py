"""Short SQLite transactions for durable jobs, progress, and shared AI results."""

from contextlib import contextmanager
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from api.webai_client import WebAIError, parse_answers, question_bank_key
from api.question_images import image_manifest, split_image_data


log = logging.getLogger(__name__)

ACTIVE = ("queued", "starting", "running", "retry_wait", "stopping", "paused", "needs_attention")
TERMINAL = ("succeeded", "failed", "cancelled", "needs_attention")
ARCHIVABLE = ("succeeded", "failed", "cancelled")


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


def _number_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def process_exists(pid):
    if not pid:
        return False
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            handle = kernel.OpenProcess(0x1000, False, int(pid))
            if handle:
                try:
                    code = wintypes.DWORD()
                    return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
                finally:
                    kernel.CloseHandle(handle)
            return ctypes.get_last_error() == 5
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Store:
    def __init__(self, directory, secrets):
        self.secrets = secrets
        self.lock = threading.RLock()
        path = Path(directory) / "service.sqlite3"
        if path.is_symlink():
            raise ValueError("The database cannot be a symbolic link")
        self.connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
        os.chmod(path, 0o600)
        self.connection.row_factory = sqlite3.Row
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            self.connection.close()
            raise ValueError("The database requires a newer backend")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL,
                credentials BLOB NOT NULL, masked TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
                spec TEXT NOT NULL, status TEXT NOT NULL, desired TEXT NOT NULL DEFAULT 'run',
                progress TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                worker_pid INTEGER, next_run REAL NOT NULL DEFAULT 0, heartbeat REAL,
                error_code TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT UNIQUE, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_account
                ON jobs(account_id) WHERE status IN
                ('queued','starting','running','retry_wait','stopping','paused');
            CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, next_run, created_at);
            CREATE INDEX IF NOT EXISTS account_jobs ON jobs(account_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
                kind TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS job_events ON events(job_id, id);
            CREATE TABLE IF NOT EXISTS answers (
                id TEXT PRIMARY KEY, cache_key TEXT UNIQUE NOT NULL, question TEXT NOT NULL,
                state TEXT NOT NULL, answer TEXT, error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS answers_queue ON answers(state, created_at);
            CREATE TABLE IF NOT EXISTS answer_images (
                sha256 TEXT PRIMARY KEY, mime_type TEXT NOT NULL,
                content BLOB NOT NULL, byte_count INTEGER NOT NULL CHECK(byte_count > 0)
            );
            CREATE TABLE IF NOT EXISTS answer_waiters (
                answer_id TEXT NOT NULL REFERENCES answers(id),
                job_id TEXT NOT NULL REFERENCES jobs(id),
                PRIMARY KEY(answer_id, job_id)
            );
            CREATE TABLE IF NOT EXISTS course_fetches (
                account_id TEXT PRIMARY KEY REFERENCES accounts(id),
                request_id TEXT NOT NULL, state TEXT NOT NULL,
                courses TEXT NOT NULL DEFAULT '[]', error_code TEXT NOT NULL DEFAULT '',
                worker_pid INTEGER, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS course_details (
                account_id TEXT NOT NULL REFERENCES accounts(id),
                course_id TEXT NOT NULL,
                request_id TEXT NOT NULL, state TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '',
                worker_pid INTEGER, updated_at REAL NOT NULL,
                PRIMARY KEY(account_id, course_id)
            );
            CREATE INDEX IF NOT EXISTS course_detail_queue
                ON course_details(state, updated_at);
            CREATE TABLE IF NOT EXISTS course_completions (
                account_id TEXT NOT NULL REFERENCES accounts(id),
                account_fingerprint TEXT NOT NULL, course_id TEXT NOT NULL,
                course_title TEXT NOT NULL, confirmed_at REAL NOT NULL,
                PRIMARY KEY(account_id, account_fingerprint, course_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                job_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT 'web',
                protocol TEXT NOT NULL DEFAULT 'chat',
                endpoint TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                route TEXT NOT NULL DEFAULT 'text',
                stage TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                response TEXT NOT NULL DEFAULT '',
                prompt_chars INTEGER NOT NULL DEFAULT 0,
                response_chars INTEGER NOT NULL DEFAULT 0,
                images INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'error',
                error TEXT NOT NULL DEFAULT '',
                elapsed_ms INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ai_conversations_recent
                ON ai_conversations(id DESC);
            CREATE INDEX IF NOT EXISTS ai_conversations_job
                ON ai_conversations(job_id, id DESC);
        """)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(jobs)")}
        for name, definition in (
            ("failures", "INTEGER NOT NULL DEFAULT 0"),
            ("account_fingerprint", "TEXT NOT NULL DEFAULT ''"),
            ("completion_index_version", "INTEGER NOT NULL DEFAULT 0"),
            ("archived_at", "REAL"),
        ):
            if name not in columns:
                self.connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS jobs_archived ON jobs(archived_at, created_at DESC)"
        )
        # needs_attention 是"等人工看一眼"的终态，不该把这个账号永久锁死：
        # 老库里这条唯一索引还带着它，启动时重建一次。
        active_index = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='one_active_job_per_account'"
        ).fetchone()
        if active_index is not None and "needs_attention" in str(active_index[0] or ""):
            self.connection.execute("DROP INDEX one_active_job_per_account")
            self.connection.execute(
                "CREATE UNIQUE INDEX one_active_job_per_account ON jobs(account_id) WHERE status "
                "IN ('queued','starting','running','retry_wait','stopping','paused')"
            )
        account_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(accounts)")}
        if "revision" not in account_columns:
            self.connection.execute("ALTER TABLE accounts ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        answer_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(answers)")}
        for name, definition in (
            ("bank_key", "TEXT"),
            ("bank_index_version", "INTEGER NOT NULL DEFAULT 0"),
            ("bank_hits", "INTEGER NOT NULL DEFAULT 0"),
            ("shared_waits", "INTEGER NOT NULL DEFAULT 0"),
            ("answer_quality", "TEXT NOT NULL DEFAULT 'format_checked'"),
            ("diagnostics", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if name not in answer_columns:
                self.connection.execute(f"ALTER TABLE answers ADD COLUMN {name} {definition}")
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS answers_bank_key "
            "ON answers(bank_key) WHERE bank_key IS NOT NULL"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS answers_bank_pending_index "
            "ON answers(bank_index_version) WHERE bank_index_version=0"
        )
        self.connection.execute("PRAGMA user_version=1")
        self.path = path
        self.connection.commit()
        self._index_answer_bank()
        self._index_course_completions()

    @contextmanager
    def transaction(self):
        with self.lock, self.connection:
            yield self.connection

    def close(self):
        with self.lock:
            self.connection.close()

    @staticmethod
    def _record_course_completion(db, job, data, confirmed_at):
        spec = json.loads(job["spec"])
        course_id = data.get("course_id")
        if (
            not job["account_fingerprint"]
            or spec.get("videos_only") or spec.get("preview")
            or data.get("phase") != "course_confirmed"
            or type(data.get("remaining_chapters")) is not int
            or data["remaining_chapters"] != 0
            or not isinstance(course_id, str)
            or not re.fullmatch(r"[0-9]{1,20}", course_id)
            or course_id not in spec["course_ids"]
        ):
            return
        db.execute(
            "INSERT OR IGNORE INTO course_completions"
            "(account_id,account_fingerprint,course_id,course_title,confirmed_at) "
            "VALUES (?,?,?,?,?)",
            (job["account_id"], job["account_fingerprint"], course_id,
             str(data.get("course") or course_id)[:500], confirmed_at),
        )

    def _index_course_completions(self):
        """Backfill explicit platform confirmations, never chapter totals or job status."""
        while True:
            with self.transaction() as db:
                jobs = db.execute(
                    "SELECT j.*,a.fingerprint,a.revision FROM jobs j "
                    "JOIN accounts a ON a.id=j.account_id "
                    "WHERE j.completion_index_version=0 ORDER BY j.created_at LIMIT 200"
                ).fetchall()
                if not jobs:
                    break
                for row in jobs:
                    job = dict(row)
                    # Older edited records have no reliable login-identity snapshot.
                    if not job["account_fingerprint"] and row["revision"] == 1:
                        job["account_fingerprint"] = row["fingerprint"]
                    for event in db.execute(
                        "SELECT data,created_at FROM events WHERE job_id=? "
                        "AND kind='course_result' ORDER BY id", (row["id"],),
                    ).fetchall():
                        self._record_course_completion(
                            db, job, json.loads(event["data"]), event["created_at"],
                        )
                    self._record_course_completion(
                        db, job, json.loads(row["progress"]), row["updated_at"],
                    )
                    db.execute(
                        "UPDATE jobs SET account_fingerprint=?,completion_index_version=1 WHERE id=?",
                        (job["account_fingerprint"], row["id"]),
                    )

    def _index_answer_bank(self):
        """Index legacy results once, retaining IDs, waiters, and ambiguous history."""
        indexed = skipped = 0
        while True:
            with self.transaction() as db:
                rows = db.execute(
                    "SELECT * FROM answers WHERE bank_index_version=0 "
                    "ORDER BY CASE WHEN state='done' THEN 0 ELSE 1 END,updated_at DESC,id LIMIT 200"
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    key, question, answer = None, None, row["answer"]
                    try:
                        question = json.loads(row["question"])
                        if not isinstance(question, dict):
                            raise ValueError("Invalid legacy question")
                        if question.get("course_id") is None or question.get("course_id") == "":
                            courses = set()
                            specs = db.execute(
                                "SELECT j.spec FROM answer_waiters w JOIN jobs j ON j.id=w.job_id "
                                "WHERE w.answer_id=?", (row["id"],),
                            ).fetchall()
                            for spec in specs:
                                selected = json.loads(spec["spec"])["course_ids"]
                                if not isinstance(selected, list) or len(selected) != 1:
                                    raise ValueError("Ambiguous legacy course")
                                courses.add(selected[0])
                            if len(courses) != 1:
                                raise ValueError("Unknown legacy course")
                            question["course_id"] = courses.pop()
                        key = question_bank_key(question)
                        if row["state"] == "done":
                            answer = json.dumps(
                                {"Answer": parse_answers(answer, question)}, ensure_ascii=False,
                            )
                    except (ValueError, TypeError, KeyError):
                        key = None
                    if key and db.execute(
                        "SELECT 1 FROM answers WHERE bank_key=?", (key,),
                    ).fetchone():
                        key = None
                    if row["state"] in {"queued", "running"}:
                        # Interrupted old queues must not race the new canonical queue.
                        db.execute(
                            "UPDATE answers SET state='error',answer=NULL,error='AI_UPGRADE_RETRY',"
                            "updated_at=? WHERE id=?", (time.time(), row["id"]),
                        )
                        answer = None
                    db.execute(
                        "UPDATE answers SET bank_key=?,bank_index_version=1,"
                        "question=COALESCE(?,question),answer=? WHERE id=?",
                        (key, json.dumps(question, ensure_ascii=False) if key else None,
                         answer, row["id"]),
                    )
                    indexed += key is not None
                    skipped += key is None
        if indexed or skipped:
            log.info("Question bank legacy index: indexed=%s retained_unindexed=%s", indexed, skipped)

    def answer_course(self, job_id, course_id=None):
        with self.transaction() as db:
            row = db.execute("SELECT spec,progress FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFound("Job not found")
            selected = json.loads(row["spec"])["course_ids"]
            if course_id is None or course_id == "":
                current = json.loads(row["progress"]).get("course_id")
                if current in selected:
                    course_id = current
                elif len(selected) == 1:
                    course_id = selected[0]
                else:
                    raise ValueError("A course ID is required for a multi-course answer request")
            if not isinstance(course_id, str) or not re.fullmatch(r"[0-9]{1,20}", course_id):
                raise ValueError("Invalid question course ID")
            if course_id not in selected:
                raise ValueError("Question does not belong to a selected course")
            return course_id

    def setting(self, name, default=""):
        with self.transaction() as db:
            row = db.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
            return row["value"] if row is not None else default

    def set_setting(self, name, value):
        if not isinstance(name, str) or not name or len(name) > 80:
            raise ValueError("Invalid setting name")
        if not isinstance(value, str) or len(value) > 1000:
            raise ValueError("Invalid setting value")
        with self.transaction() as db:
            db.execute(
                "INSERT INTO settings(name,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (name, value, time.time()),
            )

    VIDEO_GUARD_DEFAULTS = {
        # 默认按 0.95 倍速慢放：平台每次"压进度"都要重看一分钟，快这点不值得
        "max_speed": "0.95", "quiet_hours": "1", "rollback_pause": "0", "rollback_recheck": "8",
    }

    def video_guard(self):
        """视频进度看护配置：倍速上限、夜间静默、回退自动暂停、回读间隔。"""
        values = {
            key: self.setting(f"video_{key}", default)
            for key, default in self.VIDEO_GUARD_DEFAULTS.items()
        }
        try:
            max_speed = float(values["max_speed"])
        except (TypeError, ValueError):
            max_speed = 0.95
        try:
            recheck = int(values["rollback_recheck"])
        except (TypeError, ValueError):
            recheck = 8
        return {
            "max_speed": round(min(1.5, max(0.5, max_speed)), 2),
            "quiet_hours": values["quiet_hours"] == "1",
            "auto_pause": values["rollback_pause"] == "1",
            "recheck": max(1, min(50, recheck)),
        }

    def save_video_guard(self, values):
        """校验并保存看护配置，返回生效后的配置。"""
        if not isinstance(values, dict) or not values:
            raise ValueError("Empty video guard settings")
        if set(values) - {"max_speed", "quiet_hours", "auto_pause", "recheck"}:
            raise ValueError("Unknown video guard settings")
        if "max_speed" in values:
            speed = values["max_speed"]
            if type(speed) not in {int, float} or not 0.5 <= speed <= 1.5:
                raise ValueError("Invalid maximum speed")
            self.set_setting("video_max_speed", f"{float(speed):.2f}")
        for key, name in (("quiet_hours", "video_quiet_hours"),
                          ("auto_pause", "video_rollback_pause")):
            if key in values:
                if type(values[key]) is not bool:
                    raise ValueError("Expected boolean video guard switches")
                self.set_setting(name, "1" if values[key] else "0")
        if "recheck" in values:
            recheck = values["recheck"]
            if type(recheck) is not int or not 1 <= recheck <= 50:
                raise ValueError("Invalid rollback recheck interval")
            self.set_setting("video_rollback_recheck", str(recheck))
        return self.video_guard()

    def video_rollbacks(self, limit=5):
        """最近 7 天的进度回退事件，供控制台显示平台到底回退了多少。"""
        since = time.time() - 7 * 24 * 3600
        with self.transaction() as db:
            rows = db.execute(
                "SELECT job_id,data,created_at FROM events WHERE kind='video_rollback' "
                "AND created_at>=? ORDER BY id DESC LIMIT ?", (since, max(1, min(50, limit))),
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) AS count FROM events WHERE kind='video_rollback' AND created_at>=?",
                (since,),
            ).fetchone()["count"]
        items = []
        for row in rows:
            data = json.loads(row["data"])
            items.append({
                "job_id": row["job_id"], "created_at": row["created_at"],
                "course": str(data.get("course", ""))[:80],
                "name": str(data.get("name", ""))[:120],
                "from_position": int(data.get("rollback_from") or 0),
                "to_position": int(data.get("rollback_to") or 0),
                "dropped": int(data.get("rollback_dropped") or 0),
                "action": str(data.get("rollback_action", "")),
            })
        return {"count": total, "items": items}

    def provider_settings(self, prefix="ai_api_"):
        """Return the stored API provider fields without their prefix."""
        with self.transaction() as db:
            rows = db.execute(
                "SELECT name,value FROM settings WHERE name LIKE ?", (prefix + "%",)
            ).fetchall()
        return {row["name"][len(prefix):]: row["value"] for row in rows}

    def save_provider_settings(self, values, *, clear=(), prefix="ai_api_"):
        now = time.time()
        with self.transaction() as db:
            for name, value in values.items():
                if not isinstance(name, str) or not name or len(name) > 60:
                    raise ValueError("Invalid provider setting")
                if value is None:
                    db.execute("DELETE FROM settings WHERE name=?", (prefix + name,))
                    continue
                if not isinstance(value, str) or len(value) > 2000:
                    raise ValueError("Invalid provider value")
                db.execute(
                    "INSERT INTO settings(name,value,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                    (prefix + name, value, now),
                )
            for name in clear:
                db.execute("DELETE FROM settings WHERE name=?", (prefix + name,))

    AI_CONVERSATION_KEEP = 500
    AI_CONVERSATION_PROMPT_CHARS = 20000
    AI_CONVERSATION_RESPONSE_CHARS = 20000

    def record_ai_conversation(self, record):
        """Store one upstream AI exchange; image bytes are never persisted here."""
        if not isinstance(record, dict):
            raise ValueError("Invalid AI conversation record")
        prompt = str(record.get("prompt") or "")
        answer = str(record.get("response") or "")
        prompt_chars = len(prompt)
        response_chars = len(answer)
        truncated = int(
            prompt_chars > self.AI_CONVERSATION_PROMPT_CHARS
            or response_chars > self.AI_CONVERSATION_RESPONSE_CHARS
        )
        try:
            images = max(0, int(record.get("images") or 0))
            elapsed = max(0, int(record.get("elapsed_ms") or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid AI conversation counters") from exc
        error = str(record.get("error") or "")[:200]
        status = "error" if error or not answer else "ok"
        with self.transaction() as db:
            db.execute(
                "INSERT INTO ai_conversations(created_at,job_id,request_id,channel,protocol,endpoint,"
                "model,route,stage,prompt,response,prompt_chars,response_chars,images,status,error,"
                "elapsed_ms,truncated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), str(record.get("job_id") or "")[:32],
                    str(record.get("request_id") or "")[:32],
                    str(record.get("channel") or "web")[:16],
                    str(record.get("protocol") or "chat")[:16],
                    str(record.get("endpoint") or "")[:200],
                    str(record.get("model") or "")[:120],
                    str(record.get("route") or "text")[:16],
                    str(record.get("stage") or "")[:24],
                    prompt[:self.AI_CONVERSATION_PROMPT_CHARS],
                    answer[:self.AI_CONVERSATION_RESPONSE_CHARS],
                    prompt_chars, response_chars, images, status, error, elapsed, truncated,
                ),
            )
            db.execute(
                "DELETE FROM ai_conversations WHERE id <= "
                "(SELECT max(id) FROM ai_conversations) - ?",
                (self.AI_CONVERSATION_KEEP,),
            )

    def ai_conversations(self, after=0, limit=50, job_id=""):
        limit = 50 if limit is None else int(limit)
        if not 1 <= limit <= 200:
            raise ValueError("Invalid conversation limit")
        clauses, args = [], []
        if after:
            clauses.append("id>?")
            args.append(int(after))
        if job_id:
            clauses.append("job_id=?")
            args.append(job_id)
        query = (
            "SELECT id,created_at,job_id,request_id,channel,protocol,endpoint,model,route,stage,"
            "prompt,response,prompt_chars,response_chars,images,status,error,elapsed_ms,truncated "
            "FROM ai_conversations"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.transaction() as db:
            rows = db.execute(query, args).fetchall()
        return [dict(row) for row in rows]

    def storage_summary(self):
        with self.transaction() as db:
            counts = {}
            for table in (
                "accounts", "jobs", "events", "answers", "course_fetches", "course_details",
                "course_completions", "answer_images", "ai_conversations",
            ):
                counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            size = 0
            for item in (self.path, Path(str(self.path) + "-wal")):
                try:
                    size += item.stat().st_size
                except FileNotFoundError:
                    pass
            return {
                "engine": "sqlite", "status": "ok", "journal_mode": "wal",
                "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
                "database_bytes": size, "records": counts,
            }

    @staticmethod
    def _account(row):
        return {
            "id": row["id"], "label": row["label"], "username_masked": row["masked"],
            "created_at": row["created_at"], "revision": row["revision"],
        }

    def accounts(self):
        with self.transaction() as db:
            blocked = {}
            for table in ("course_fetches", "course_details"):
                for row in db.execute(
                    f"SELECT account_id,state,worker_pid FROM {table} "
                    "WHERE state IN ('queued','running') OR worker_pid IS NOT NULL"
                ):
                    if row["state"] in {"queued", "running"} or process_exists(row["worker_pid"]):
                        blocked[row["account_id"]] = "ACCOUNT_LOOKUP_BUSY"
            for row in db.execute(
                "SELECT account_id,worker_pid FROM jobs WHERE worker_pid IS NOT NULL"
            ):
                if process_exists(row["worker_pid"]):
                    blocked[row["account_id"]] = "ACCOUNT_WORKER_ACTIVE"
            result = []
            for row in db.execute(
                "SELECT a.*,c.completed_courses,c.last_completed_at,j.id AS job_id,"
                "j.status AS job_status,j.progress AS job_progress,j.updated_at AS job_updated_at "
                "FROM accounts a LEFT JOIN ("
                "SELECT account_id,account_fingerprint,count(*) AS completed_courses,"
                "max(confirmed_at) AS last_completed_at FROM course_completions "
                "GROUP BY account_id,account_fingerprint"
                ") c ON c.account_id=a.id AND c.account_fingerprint=a.fingerprint "
                "LEFT JOIN jobs j ON j.id=COALESCE("
                "(SELECT id FROM jobs WHERE account_id=a.id AND status IN "
                "('queued','starting','running','retry_wait','stopping','paused','needs_attention')),"
                "(SELECT id FROM jobs WHERE account_id=a.id ORDER BY created_at DESC LIMIT 1)) "
                "ORDER BY a.created_at DESC LIMIT 1000"
            ).fetchall():
                reason = blocked.get(row["id"], "")
                if row["job_status"] in ACTIVE:
                    reason = "ACCOUNT_HAS_ACTIVE_JOB"
                current = None
                if row["job_id"]:
                    progress = json.loads(row["job_progress"])
                    current = {
                        "id": row["job_id"], "status": row["job_status"],
                        "updated_at": row["job_updated_at"],
                        "progress": {key: progress[key] for key in (
                            "course_id", "course", "chapter", "phase", "name", "position",
                            "duration", "ai_state", "quiz_state", "video_state",
                            "pacing_kind", "pacing_until", "pacing_minutes",
                        ) if key in progress},
                    }
                result.append({
                    **self._account(row), "completed_courses": row["completed_courses"] or 0,
                    "last_completed_at": row["last_completed_at"],
                    "is_studying": row["job_status"] == "running", "current_job": current,
                    "can_delete": not reason, "delete_blocked_reason": reason,
                })
            return result

    def resting_jobs(self, limit=200):
        """正在拟人节奏里等待的任务（控制台"拟人节奏"页面用）。"""
        result = []
        with self.transaction() as db:
            rows = db.execute(
                "SELECT j.id,j.account_id,a.label,a.masked,j.progress,j.updated_at "
                "FROM jobs j JOIN accounts a ON a.id=j.account_id "
                "WHERE j.status='running' ORDER BY j.updated_at DESC LIMIT ?", (int(limit),),
            ).fetchall()
        for row in rows:
            try:
                progress = json.loads(row["progress"])
            except (TypeError, ValueError):
                continue
            if progress.get("phase") != "pacing":
                continue
            result.append({
                "id": row["id"],
                "account_id": row["account_id"],
                "account": row["label"],
                "masked": row["masked"],
                "course": str(progress.get("course", ""))[:80],
                "chapter": str(progress.get("chapter", ""))[:80],
                "reason": str(progress.get("name", ""))[:120],
                "kind": str(progress.get("pacing_kind", ""))[:20],
                "until": _number_or_none(progress.get("pacing_until")),
                "minutes": _number_or_none(progress.get("pacing_minutes")),
                "updated_at": row["updated_at"],
            })
        return result

    def account_credentials(self, account_id):
        with self.transaction() as db:
            row = db.execute("SELECT credentials FROM accounts WHERE id=?", (account_id,)).fetchone()
            if row is None:
                raise NotFound("Account not found")
            return json.loads(self.secrets.cipher.decrypt(row["credentials"]))

    @staticmethod
    def _ensure_course_fetch_idle(db, account_id):
        row = db.execute(
            "SELECT state,worker_pid FROM course_fetches WHERE account_id=?", (account_id,),
        ).fetchone()
        if row and (row["state"] in {"queued", "running"} or process_exists(row["worker_pid"])):
            raise Conflict("Course lookup is in progress for this account")

    @staticmethod
    def _ensure_course_details_idle(db, account_id):
        rows = db.execute(
            "SELECT state,worker_pid FROM course_details WHERE account_id=?", (account_id,)
        ).fetchall()
        if any(row["state"] in {"queued", "running"} or process_exists(row["worker_pid"])
               for row in rows):
            raise Conflict("Course detail lookup is in progress for this account")

    def save_account(self, label, username, password, account_id=None):
        now = time.time()
        try:
            with self.transaction() as db:
                if account_id:
                    self._ensure_course_fetch_idle(db, account_id)
                    self._ensure_course_details_idle(db, account_id)
                    previous = db.execute(
                        "SELECT fingerprint,credentials FROM accounts WHERE id=?", (account_id,)
                    ).fetchone()
                    if previous is None:
                        raise NotFound("Account not found")
                    credentials = json.loads(self.secrets.cipher.decrypt(previous["credentials"]))
                    username = credentials["username"] if username is None else username
                    if password is None and self.secrets.fingerprint(username) != previous["fingerprint"]:
                        raise ValueError("A password is required when changing the login identity")
                    password = credentials["password"] if password is None else password
                    if previous["fingerprint"] != self.secrets.fingerprint(username):
                        active = db.execute(
                            "SELECT 1 FROM jobs WHERE account_id=? AND status IN "
                            "('queued','starting','running','retry_wait','stopping','paused','needs_attention')",
                            (account_id,),
                        ).fetchone()
                        if active:
                            raise Conflict("Cancel the account's job before changing its login identity")
                if not username or not password:
                    raise ValueError("Login credentials are required")
                encrypted = self.secrets.cipher.encrypt(json.dumps(
                    {"username": username, "password": password}
                ).encode("utf-8"))
                masked = username[:2] + "***" + username[-2:] if len(username) > 4 else "***"
                if account_id:
                    result = db.execute(
                        "UPDATE accounts SET label=?,fingerprint=?,credentials=?,masked=?,"
                        "revision=revision+1 WHERE id=?",
                        (label, self.secrets.fingerprint(username), encrypted, masked, account_id),
                    )
                    if not result.rowcount:
                        raise NotFound("Account not found")
                    db.execute("DELETE FROM course_fetches WHERE account_id=?", (account_id,))
                    db.execute("DELETE FROM course_details WHERE account_id=?", (account_id,))
                else:
                    account_id = uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO accounts(id,label,fingerprint,credentials,masked,created_at) "
                        "VALUES (?,?,?,?,?,?)", (
                        account_id, label, self.secrets.fingerprint(username), encrypted, masked, now,
                    ))
                return self._account(db.execute(
                    "SELECT * FROM accounts WHERE id=?", (account_id,)
                ).fetchone())
        except sqlite3.IntegrityError:
            raise Conflict("This account already exists") from None

    def delete_account(self, account_id):
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise NotFound("Account not found")
            self._ensure_course_fetch_idle(db, account_id)
            self._ensure_course_details_idle(db, account_id)
            jobs = db.execute(
                "SELECT status,worker_pid FROM jobs WHERE account_id=?", (account_id,),
            ).fetchall()
            if any(row["status"] in ACTIVE or process_exists(row["worker_pid"]) for row in jobs):
                raise Conflict("Cancel or delete this account's jobs before deleting the account")
            for table in ("answer_waiters", "events"):
                db.execute(
                    f"DELETE FROM {table} WHERE job_id IN (SELECT id FROM jobs WHERE account_id=?)",
                    (account_id,),
                )
            for table in ("jobs", "course_fetches", "course_details", "course_completions"):
                db.execute(f"DELETE FROM {table} WHERE account_id=?", (account_id,))
            db.execute("DELETE FROM accounts WHERE id=?", (account_id,))

    def courses(self, account_id):
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise NotFound("Account not found")
            row = db.execute(
                "SELECT * FROM course_fetches WHERE account_id=?", (account_id,),
            ).fetchone()
            return {
                "account_id": account_id,
                "request_id": row["request_id"] if row else None,
                "state": row["state"] if row else "idle",
                "courses": json.loads(row["courses"]) if row else [],
                "error_code": row["error_code"] if row else "",
                "updated_at": row["updated_at"] if row else None,
            }

    def request_courses(self, account_id, queue_limit=100):
        with self.transaction() as db:
            current = self.courses(account_id)
            if current["state"] in {"queued", "running"}:
                return current
            self._ensure_course_fetch_idle(db, account_id)
            jobs = db.execute(
                "SELECT status,worker_pid FROM jobs WHERE account_id=? AND status IN "
                "('queued','starting','running','retry_wait','stopping','paused','needs_attention')",
                (account_id,),
            ).fetchall()
            if any(row["status"] not in {"paused", "needs_attention"}
                   or process_exists(row["worker_pid"]) for row in jobs):
                raise Conflict("Pause or finish this account's job before fetching courses")
            count = db.execute(
                "SELECT count(*) FROM course_fetches WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if count >= queue_limit:
                raise Conflict("The course lookup queue is full")
            db.execute(
                "INSERT INTO course_fetches(account_id,request_id,state,updated_at) "
                "VALUES (?,?,'queued',?) ON CONFLICT(account_id) DO UPDATE SET "
                "request_id=excluded.request_id,state='queued',error_code='',"
                "worker_pid=NULL,updated_at=excluded.updated_at",
                (account_id, uuid.uuid4().hex, time.time()),
            )
            return self.courses(account_id)

    def claim_courses(self):
        with self.transaction() as db:
            row = db.execute(
                "SELECT account_id,request_id FROM course_fetches WHERE state='queued' "
                "ORDER BY updated_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE course_fetches SET state='running',updated_at=? WHERE account_id=?",
                (time.time(), row["account_id"]),
            )
            return dict(row)

    def course_worker_pid(self, account_id, request_id, pid):
        with self.transaction() as db:
            db.execute(
                "UPDATE course_fetches SET worker_pid=? WHERE account_id=? AND request_id=? "
                "AND state='running'", (pid, account_id, request_id),
            )

    def finish_courses(self, account_id, request_id, courses=None, error=""):
        with self.transaction() as db:
            state = "error" if error else "done"
            if courses is None:
                # Keep the last successful list visible when a refresh fails.
                db.execute(
                    "UPDATE course_fetches SET state=?,error_code=?,worker_pid=NULL,"
                    "updated_at=? WHERE account_id=? AND request_id=? AND state='running'",
                    (state, error, time.time(), account_id, request_id),
                )
            else:
                db.execute(
                    "UPDATE course_fetches SET state=?,courses=?,error_code=?,"
                    "worker_pid=NULL,updated_at=? WHERE account_id=? AND request_id=? "
                    "AND state='running'",
                    (state, json.dumps(courses, ensure_ascii=False), error, time.time(),
                     account_id, request_id),
                )

    def recover_courses(self):
        with self.transaction() as db:
            for row in db.execute(
                "SELECT account_id,worker_pid FROM course_fetches WHERE state IN ('queued','running')"
            ).fetchall():
                alive = process_exists(row["worker_pid"])
                db.execute(
                    "UPDATE course_fetches SET state='error',error_code=?,"
                    "worker_pid=?,updated_at=? WHERE account_id=?",
                    ("PREVIOUS_LOOKUP" if alive else "LOOKUP_INTERRUPTED",
                     row["worker_pid"] if alive else None, time.time(), row["account_id"]),
                )

    @staticmethod
    def _ensure_course_detail_idle(db, account_id, course_id):
        row = db.execute(
            "SELECT state,worker_pid FROM course_details "
            "WHERE account_id=? AND course_id=?", (account_id, course_id),
        ).fetchone()
        if row and (row["state"] in {"queued", "running"} or process_exists(row["worker_pid"])):
            raise Conflict("Course detail lookup is in progress for this course")

    def course_detail(self, account_id, course_id):
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise NotFound("Account not found")
            row = db.execute(
                "SELECT * FROM course_details WHERE account_id=? AND course_id=?",
                (account_id, course_id),
            ).fetchone()
            return {
                "account_id": account_id,
                "course_id": course_id,
                "request_id": row["request_id"] if row else None,
                "state": row["state"] if row else "idle",
                "detail": json.loads(row["detail"]) if row else None,
                "error_code": row["error_code"] if row else "",
                "updated_at": row["updated_at"] if row else None,
            }

    def request_course_detail(self, account_id, course_id, queue_limit=1):
        with self.transaction() as db:
            current = self.course_detail(account_id, course_id)
            if db.execute(
                "SELECT 1 FROM jobs WHERE account_id=? AND status IN ('starting','running')",
                (account_id,),
            ).fetchone():
                # A second login must not interfere with the learning session.
                return current
            if current["state"] in {"queued", "running"}:
                return current
            self._ensure_course_detail_idle(db, account_id, course_id)
            count = db.execute(
                "SELECT count(*) FROM course_details WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if count >= queue_limit:
                raise Conflict("The course detail lookup queue is full")
            db.execute(
                "INSERT INTO course_details(account_id,course_id,request_id,state,updated_at) "
                "VALUES (?,?,?,'queued',?) ON CONFLICT(account_id,course_id) DO UPDATE SET "
                "request_id=excluded.request_id,state='queued',error_code='',"
                "worker_pid=NULL,updated_at=excluded.updated_at",
                (account_id, course_id, uuid.uuid4().hex, time.time()),
            )
            return self.course_detail(account_id, course_id)

    def claim_course_details(self):
        with self.transaction() as db:
            row = db.execute(
                "SELECT account_id,course_id,request_id FROM course_details "
                "WHERE state='queued' ORDER BY updated_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE course_details SET state='running',updated_at=? "
                "WHERE account_id=? AND course_id=? AND request_id=?",
                (time.time(), row["account_id"], row["course_id"], row["request_id"]),
            )
            return dict(row)

    def course_detail_worker_pid(self, account_id, course_id, request_id, pid):
        with self.transaction() as db:
            db.execute(
                "UPDATE course_details SET worker_pid=? WHERE account_id=? AND course_id=? "
                "AND request_id=? AND state='running'",
                (pid, account_id, course_id, request_id),
            )

    def finish_course_detail(self, account_id, course_id, request_id, detail=None, error=""):
        with self.transaction() as db:
            state = "error" if error else "done"
            if detail is None:
                db.execute(
                    "UPDATE course_details SET state=?,error_code=?,worker_pid=NULL,"
                    "updated_at=? WHERE account_id=? AND course_id=? AND request_id=? "
                    "AND state='running'",
                    (state, error, time.time(), account_id, course_id, request_id),
                )
            else:
                db.execute(
                    "UPDATE course_details SET state=?,detail=?,error_code=?,worker_pid=NULL,"
                    "updated_at=? WHERE account_id=? AND course_id=? AND request_id=? "
                    "AND state='running'",
                    (state, json.dumps(detail, ensure_ascii=False), error, time.time(),
                     account_id, course_id, request_id),
                )

    def recover_course_details(self):
        with self.transaction() as db:
            for row in db.execute(
                "SELECT account_id,course_id,worker_pid FROM course_details "
                "WHERE state IN ('queued','running')"
            ).fetchall():
                alive = process_exists(row["worker_pid"])
                db.execute(
                    "UPDATE course_details SET state='error',error_code=?,worker_pid=?,"
                    "updated_at=? WHERE account_id=? AND course_id=?",
                    ("PREVIOUS_DETAIL" if alive else "DETAIL_INTERRUPTED",
                     row["worker_pid"] if alive else None, time.time(),
                     row["account_id"], row["course_id"]),
                )

    @staticmethod
    def _job(row):
        return {
            "id": row["id"], "account_id": row["account_id"],
            "label": row["label"], "username_masked": row["masked"],
            "course_ids": json.loads(row["spec"])["course_ids"],
            "options": json.loads(row["spec"]),
            "status": row["status"], "progress": json.loads(row["progress"]),
            "attempts": row["attempts"], "error_code": row["error_code"],
            "failures": row["failures"],
            "message": row["message"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "heartbeat": row["heartbeat"],
            "archived_at": row["archived_at"],
        }

    def jobs(self, limit=200, *, archived=None):
        with self.transaction() as db:
            where = ""
            params = [limit]
            if archived is not None:
                where = "WHERE (j.archived_at IS NOT NULL)=? "
                params = [bool(archived), limit]
            query = (
                "SELECT j.*,a.label,a.masked FROM jobs j JOIN accounts a ON a.id=j.account_id "
                + where
                + "ORDER BY j.created_at DESC,j.id DESC LIMIT ?"
            )
            return [self._job(row) for row in db.execute(
                query,
                params,
            )]

    def job(self, job_id):
        with self.transaction() as db:
            row = db.execute(
                "SELECT j.*,a.label,a.masked FROM jobs j JOIN accounts a ON a.id=j.account_id "
                "WHERE j.id=?", (job_id,),
            ).fetchone()
            if row is None:
                raise NotFound("Job not found")
            return self._job(row)

    def delete_job(self, job_id):
        with self.transaction() as db:
            row = db.execute(
                "SELECT status,worker_pid FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("Job not found")
            if row["status"] not in TERMINAL or process_exists(row["worker_pid"]):
                raise Conflict("Only finished or cancelled jobs can be deleted")
            db.execute("DELETE FROM answer_waiters WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM events WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def archive_job(self, job_id):
        with self.transaction() as db:
            row = db.execute(
                "SELECT status,worker_pid,archived_at FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("Job not found")
            if row["status"] not in ARCHIVABLE or process_exists(row["worker_pid"]):
                raise Conflict("Only finished or cancelled jobs can be archived")
            if row["archived_at"] is not None:
                raise Conflict("Job is already archived")
            db.execute(
                "UPDATE jobs SET archived_at=?,updated_at=? WHERE id=?",
                (time.time(), time.time(), job_id),
            )
        return self.job(job_id)

    def unarchive_job(self, job_id):
        with self.transaction() as db:
            row = db.execute(
                "SELECT status,worker_pid,archived_at FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("Job not found")
            if row["status"] not in ARCHIVABLE or process_exists(row["worker_pid"]):
                raise Conflict("Only finished or cancelled jobs can be restored")
            if row["archived_at"] is None:
                raise Conflict("Job is not archived")
            db.execute(
                "UPDATE jobs SET archived_at=NULL,updated_at=? WHERE id=?",
                (time.time(), job_id),
            )
        return self.job(job_id)

    def create_job(self, account_id, spec, idempotency_key=None, queue_limit=1000):
        encoded = json.dumps(spec, sort_keys=True)
        job_id, now = uuid.uuid4().hex, time.time()
        try:
            with self.transaction() as db:
                if idempotency_key:
                    old = db.execute(
                        "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
                    ).fetchone()
                    if old:
                        if old["account_id"] != account_id or old["spec"] != encoded:
                            raise Conflict("Idempotency key was used with a different request")
                        job_id = old["id"]
                        return self.job(job_id), False
                account = db.execute(
                    "SELECT fingerprint FROM accounts WHERE id=?", (account_id,),
                ).fetchone()
                if account is None:
                    raise NotFound("Account not found")
                self._ensure_course_fetch_idle(db, account_id)
                self._ensure_course_details_idle(db, account_id)
                count = db.execute(
                    "SELECT count(*) FROM jobs WHERE status IN ('queued','retry_wait')"
                ).fetchone()[0]
                if count >= queue_limit:
                    raise Conflict("The job queue is full")
                db.execute(
                    "INSERT INTO jobs(id,account_id,account_fingerprint,completion_index_version,"
                    "spec,status,idempotency_key,created_at,updated_at) "
                    "VALUES (?,?,?,1,?,'queued',?,?,?)",
                    (job_id, account_id, account["fingerprint"], encoded, idempotency_key, now, now),
                )
        except sqlite3.IntegrityError:
            raise Conflict("This account already has an active or paused job") from None
        return self.job(job_id), True

    def claim(self):
        now = time.time()
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','retry_wait') AND next_run<=? "
                "AND desired='run' AND NOT EXISTS (SELECT 1 FROM course_fetches c "
                "WHERE c.account_id=jobs.account_id AND c.state IN ('queued','running')) "
                "AND NOT EXISTS (SELECT 1 FROM course_details d "
                "WHERE d.account_id=jobs.account_id AND d.state IN ('queued','running')) "
                "ORDER BY created_at LIMIT 1", (now,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='starting',attempts=attempts+1,updated_at=?,"
                "error_code='',message='',account_fingerprint="
                "(SELECT fingerprint FROM accounts WHERE id=jobs.account_id) WHERE id=?",
                (now, row["id"]),
            )
            return row["id"]

    def payload(self, job_id):
        # 全局图片题开关在事务外读取，避免嵌套事务提前提交
        vision_enabled = self.setting("ai_vision_enabled", "0") == "1"
        with self.transaction() as db:
            row = db.execute(
                "SELECT j.spec,a.credentials FROM jobs j JOIN accounts a ON a.id=j.account_id "
                "WHERE j.id=?", (job_id,),
            ).fetchone()
            if row is None:
                raise NotFound("Job not found")
            credentials = json.loads(self.secrets.cipher.decrypt(row["credentials"]))
            spec = json.loads(row["spec"])
            if vision_enabled and not spec.get("experimental_vision"):
                # 全局开关打开时，所有任务（含已存在的任务）都按图片题走视觉通道处理
                spec["experimental_vision"] = True
            return {"credentials": credentials, "spec": spec}

    def set_state(self, job_id, status, *, error="", message="", pid=None, delay=0):
        with self.transaction() as db:
            db.execute(
                "UPDATE jobs SET status=?,error_code=?,message=?,worker_pid=?,next_run=?,"
                "updated_at=?,heartbeat=? WHERE id=?",
                (status, error, message, pid, time.time() + delay, time.time(), time.time(), job_id),
            )

    def worker_state(self, job_id):
        with self.transaction() as db:
            row = db.execute(
                "SELECT status,desired,attempts,failures,worker_pid,heartbeat FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("Job not found")
            return dict(row)

    def record_failure(self, job_id, code):
        with self.transaction() as db:
            row = db.execute("SELECT failures FROM jobs WHERE id=?", (job_id,)).fetchone()
            failures = row["failures"] + 1
            status = "retry_wait" if failures < 3 else "failed"
            db.execute(
                "UPDATE jobs SET status=?,failures=?,worker_pid=NULL,error_code=?,next_run=?,"
                "updated_at=? WHERE id=?",
                (status, failures, code, time.time() + min(60, 5 * 2 ** failures), time.time(), job_id),
            )

    def action(self, job_id, action, *, allowed_statuses=None):
        try:
            with self.transaction() as db:
                row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is None:
                    raise NotFound("Job not found")
                if row["archived_at"] is not None:
                    raise Conflict("Restore an archived job before changing its state")
                if allowed_statuses is not None and row["status"] not in allowed_statuses:
                    raise Conflict("This action is not valid for the current job state")
                if action in {"pause", "cancel"}:
                    if row["status"] not in ACTIVE:
                        raise Conflict("This job is not active")
                    if row["status"] == "needs_attention" and process_exists(row["worker_pid"]):
                        raise Conflict("Check the previous worker before changing this job")
                    running = row["status"] in {"starting", "running", "stopping"}
                    status = "stopping" if running else ("paused" if action == "pause" else "cancelled")
                    desired = action
                elif action == "resume" and row["status"] == "paused":
                    status, desired = "queued", "run"
                elif action == "retry" and row["status"] in TERMINAL:
                    if process_exists(row["worker_pid"]):
                        raise Conflict("The previous worker may still be alive")
                    status, desired = "queued", "run"
                else:
                    raise Conflict("This action is not valid for the current job state")
                if desired == "run":
                    self._ensure_course_fetch_idle(db, row["account_id"])
                db.execute(
                    "UPDATE jobs SET status=?,desired=?,next_run=0,updated_at=?,error_code='',"
                    "message='',worker_pid=?,"
                    "failures=CASE WHEN ?='retry' THEN 0 ELSE failures END WHERE id=?",
                    (status, desired, time.time(),
                     row["worker_pid"] if status == "stopping" else None, action, job_id),
                )
                if action == "retry":
                    db.execute(
                        "UPDATE answers SET state='queued',answer=NULL,error='',created_at=?,updated_at=? "
                        "WHERE state='error' AND id IN "
                        "(SELECT answer_id FROM answer_waiters WHERE job_id=?)",
                        (time.time(), time.time(), job_id),
                    )
        except sqlite3.IntegrityError:
            raise Conflict("Another job is already active for this account") from None
        return self.job(job_id)

    def event(self, job_id, kind, data):
        now = time.time()
        with self.transaction() as db:
            row = db.execute(
                "SELECT progress,account_id,spec,account_fingerprint FROM jobs WHERE id=?", (job_id,),
            ).fetchone()
            if row is None:
                return
            progress = json.loads(row["progress"])
            if kind != "heartbeat":
                if kind == "course_result":
                    self._record_course_completion(db, row, data, now)
                if kind == "course_outline":
                    from backend.course_worker import summary_from_detail, validate_public_course_detail
                    detail = validate_public_course_detail(data.get("detail"))
                    course_id = str(data.get("course_id", ""))
                    if detail["course"]["id"] != course_id or course_id not in json.loads(row["spec"])["course_ids"]:
                        raise ValueError("Worker outline does not belong to a selected course")
                    db.execute(
                        "INSERT INTO course_details(account_id,course_id,request_id,state,detail,updated_at) "
                        "VALUES (?,?,?,'done',?,?) ON CONFLICT(account_id,course_id) DO UPDATE SET "
                        "detail=excluded.detail,updated_at=excluded.updated_at,error_code='',"
                        "state=CASE WHEN course_details.state IN ('queued','running') "
                        "THEN course_details.state ELSE 'done' END",
                        (row["account_id"], course_id, uuid.uuid4().hex,
                         json.dumps(detail, ensure_ascii=False), now),
                    )
                    catalog = db.execute(
                        "SELECT courses FROM course_fetches WHERE account_id=?", (row["account_id"],),
                    ).fetchone()
                    if catalog:
                        courses = json.loads(catalog["courses"])
                        for course in courses:
                            if course["id"] == course_id:
                                course["summary"] = summary_from_detail(detail)
                        db.execute(
                            "UPDATE course_fetches SET courses=? WHERE account_id=?",
                            (json.dumps(courses, ensure_ascii=False), row["account_id"]),
                        )
                    progress["outline_updated_at"] = now
                if kind in {"course", "chapter", "task"}:
                    for field in (
                        "name", "position", "duration", "video_state", "video_error", "replay_count",
                        "ai_state", "ai_question", "ai_type", "ai_error",
                        "ai_mode", "ai_image_count",
                        "quiz_state", "quiz_answer", "job_id", "result", "manual_tasks",
                        # 回退是"这一个视频"的事：换下一个任务点还挂着上一次的次数，
                        # 界面上就会一直写着"平台已回退 N 次"，看着像到处都在回退。
                        "rollback_from", "rollback_to", "rollback_dropped", "rollback_count",
                        "rollback_action",
                    ):
                        progress.pop(field, None)
                    if kind == "course":
                        progress.pop("chapter", None)
                        progress.pop("chapter_id", None)
                elif kind == "phase" and data.get("phase") in {"login", "loading_courses"}:
                    progress.pop("manual_tasks", None)
                progress.update({key: value for key, value in data.items() if key != "detail"})
                db.execute(
                    "INSERT INTO events(job_id,kind,data,created_at) VALUES (?,?,?,?)",
                    (job_id, kind, json.dumps(
                        {key: value for key, value in data.items() if key != "detail"},
                        ensure_ascii=False,
                    ), now),
                )
                db.execute(
                    "DELETE FROM events WHERE job_id=? AND kind<>'video_rollback' AND id NOT IN "
                    "(SELECT id FROM events WHERE job_id=? AND kind<>'video_rollback' "
                    "ORDER BY id DESC LIMIT 500)",
                    (job_id, job_id),
                )
                # 回退记录是"平台到底回退过几次"的唯一凭据，单独留 200 条，不跟着普通事件被裁掉
                db.execute(
                    "DELETE FROM events WHERE job_id=? AND kind='video_rollback' AND id NOT IN "
                    "(SELECT id FROM events WHERE job_id=? AND kind='video_rollback' "
                    "ORDER BY id DESC LIMIT 200)",
                    (job_id, job_id),
                )
            db.execute(
                "UPDATE jobs SET progress=?,heartbeat=?,updated_at=? WHERE id=?",
                (json.dumps(progress, ensure_ascii=False), now, now, job_id),
            )

    def events(self, job_id, after=0):
        self.job(job_id)
        with self.transaction() as db:
            return [
                {"id": row["id"], "kind": row["kind"], "data": json.loads(row["data"]),
                 "created_at": row["created_at"]}
                for row in db.execute(
                    "SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT 200",
                    (job_id, after),
                )
            ]

    def recover(self):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE status IN ('starting','running','stopping')"
            ).fetchall()
            for row in rows:
                if process_exists(row["worker_pid"]):
                    status, error = "needs_attention", "PREVIOUS_WORKER"
                elif row["desired"] == "cancel":
                    status, error = "cancelled", ""
                elif row["desired"] == "pause":
                    status, error = "paused", ""
                else:
                    status, error = "queued", "SERVICE_RESTART"
                db.execute(
                    "UPDATE jobs SET status=?,error_code=?,worker_pid=?,updated_at=? WHERE id=?",
                    (status, error, row["worker_pid"] if status == "needs_attention" else None,
                     time.time(), row["id"]),
                )
            db.execute("UPDATE answers SET state='queued' WHERE state='running'")

    def stats(self):
        with self.transaction() as db:
            jobs = {row[0]: row[1] for row in db.execute(
                "SELECT status,count(*) FROM jobs GROUP BY status"
            )}
            visible_jobs = {row[0]: row[1] for row in db.execute(
                "SELECT status,count(*) FROM jobs WHERE archived_at IS NULL GROUP BY status"
            )}
            archived_jobs = db.execute(
                "SELECT count(*) FROM jobs WHERE archived_at IS NOT NULL"
            ).fetchone()[0]
            answers = {row[0]: row[1] for row in db.execute(
                "SELECT state,count(*) FROM answers GROUP BY state"
            )}
            course_fetches = {row[0]: row[1] for row in db.execute(
                "SELECT state,count(*) FROM course_fetches GROUP BY state"
            )}
            bank = db.execute(
                "SELECT count(CASE WHEN bank_key IS NOT NULL AND state='done' THEN 1 END),"
                "COALESCE(sum(bank_hits),0),COALESCE(sum(shared_waits),0) FROM answers"
            ).fetchone()
            return {
                "jobs": jobs, "visible_jobs": visible_jobs, "archived_jobs": archived_jobs,
                "answers": answers, "course_fetches": course_fetches,
                "question_bank": {"entries": bank[0], "hits": bank[1], "shared_waits": bank[2]},
            }

    def request_answer(self, job_id, key, question, queue_limit=500):
        question, blobs = split_image_data(question)
        if blobs and key != question_bank_key(question):
            raise WebAIError("Image question identity does not match its content")
        now = time.time()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM answers WHERE bank_key=?", (key,)).fetchone()
            invalid = False
            canonical = None
            if row is not None and row["state"] == "done":
                try:
                    canonical = json.dumps(
                        {"Answer": parse_answers(row["answer"], question)}, ensure_ascii=False,
                    )
                except WebAIError:
                    invalid = True
                    log.warning("Question bank answer failed validation: request=%s", row["id"][:12])
                if image_manifest(question) and row["answer_quality"] not in {
                    "vision_cross_checked", "confirmed",
                }:
                    # Keep the old result and waiters immutable while replacing its bank slot.
                    db.execute(
                        "UPDATE answers SET bank_key=NULL,cache_key=? WHERE id=?",
                        ("retired-image:" + row["id"], row["id"]),
                    )
                    log.info("Unreviewed image candidate retained: request=%s", row["id"][:12])
                    row, canonical, invalid = None, None, False
            requeue = row is not None and row["state"] == "error" and (
                now - row["updated_at"] >= 60
                or str(row["error"]).startswith("AI_INVALID_ANSWER")
                or row["error"] in {"NO_ACTIVE_WAITERS", "AI_UNAVAILABLE", "AI_UPGRADE_RETRY"}
                or row["error"] == "AI_VISION_UNAVAILABLE"
                or str(row["error"]).startswith("AI_VISION_")
            )
            if row is None or invalid or requeue:
                count = db.execute(
                    "SELECT count(*) FROM answers WHERE state IN ('queued','running')"
                ).fetchone()[0]
                if count >= queue_limit:
                    raise Conflict("The AI queue is full")
            if blobs:
                db.executemany(
                    "INSERT OR IGNORE INTO answer_images(sha256,mime_type,content,byte_count) "
                    "VALUES (?,?,?,?)", blobs,
                )
            if row is None:
                answer_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO answers(id,cache_key,bank_key,bank_index_version,question,state,"
                    "created_at,updated_at) VALUES (?,?,?,1,?,'queued',?,?)",
                    (answer_id, key, key, json.dumps(question, ensure_ascii=False), now, now),
                )
                source = "ai"
            else:
                answer_id = row["id"]
                if invalid or requeue:
                    db.execute(
                        "UPDATE answers SET state='queued',answer=NULL,error='',question=?,"
                        "answer_quality='format_checked',diagnostics='{}',created_at=?,updated_at=? WHERE id=?",
                        (json.dumps(question, ensure_ascii=False), now, now, answer_id),
                    )
                    source = "ai"
                elif canonical is not None:
                    db.execute(
                        "UPDATE answers SET answer=?,bank_hits=bank_hits+1 WHERE id=?",
                        (canonical, answer_id),
                    )
                    source = "bank"
                else:
                    source = "shared" if row["state"] in {"queued", "running"} else "ai"
            waiter = db.execute(
                "INSERT OR IGNORE INTO answer_waiters VALUES (?,?)", (answer_id, job_id),
            )
            if source == "shared" and waiter.rowcount:
                db.execute("UPDATE answers SET shared_waits=shared_waits+1 WHERE id=?", (answer_id,))
            result = self._answer(db.execute(
                "SELECT * FROM answers WHERE id=?", (answer_id,),
            ).fetchone())
            result["source"] = source
            return result

    def image_blob(self, image):
        with self.transaction() as db:
            row = db.execute(
                "SELECT mime_type,content,byte_count FROM answer_images WHERE sha256=?",
                (image["sha256"],),
            ).fetchone()
            if row is None:
                raise WebAIError("Stored question image is missing")
            raw = bytes(row["content"])
            if row["mime_type"] != image["mime_type"] or row["byte_count"] != image["byte_count"] \
                    or len(raw) != image["byte_count"] \
                    or hashlib.sha256(raw).hexdigest() != image["sha256"]:
                raise WebAIError("Stored question image failed its integrity check")
            return raw

    def hydrate_answer_images(self, question):
        images = image_manifest(question, require_hash=True)
        if not images:
            return question
        return dict(question, images=[
            dict(image, data=base64.b64encode(self.image_blob(image)).decode("ascii"))
            for image in images
        ])

    def image_answers_with_reference(self, limit=6):
        """Image questions that already have an accepted answer — the benchmark set."""
        with self.transaction() as db:
            rows = db.execute(
                "SELECT id,question,answer,answer_quality,"
                "COALESCE(json_extract(question,'$.course_id'),'') AS course_id FROM answers "
                "WHERE state='done' AND answer IS NOT NULL AND answer != '' "
                "AND json_valid(question) "
                "AND COALESCE(json_type(question,'$.images'),'null')='array' "
                "AND json_array_length(question,'$.images')>0 "
                "ORDER BY updated_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"], "question": json.loads(row["question"]),
                "answer": row["answer"], "quality": row["answer_quality"] or "",
                "course_id": row["course_id"] or "",
            }
            for row in rows
        ]

    def claim_answer(self, max_wait):
        now = time.time()
        with self.transaction() as db:
            db.execute(
                "UPDATE answers SET state='error',error='NO_ACTIVE_WAITERS',updated_at=? "
                "WHERE state='queued' AND NOT EXISTS "
                "(SELECT 1 FROM answer_waiters w JOIN jobs j ON j.id=w.job_id "
                "WHERE w.answer_id=answers.id AND j.status='running' AND j.desired='run')",
                (now,),
            )
            db.execute(
                "UPDATE answers SET state='error',error='AI_QUEUE_TIMEOUT',updated_at=? "
                "WHERE state='queued' AND created_at<?", (now, now - max_wait),
            )
            row = db.execute(
                "SELECT a.* FROM answers a WHERE a.state='queued' AND EXISTS "
                "(SELECT 1 FROM answer_waiters w JOIN jobs j ON j.id=w.job_id "
                "WHERE w.answer_id=a.id AND j.status='running' AND j.desired='run') "
                "ORDER BY a.created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE answers SET state='running',updated_at=? WHERE id=?", (now, row["id"]),
            )
            waiter = db.execute(
                "SELECT w.job_id FROM answer_waiters w JOIN jobs j ON j.id=w.job_id "
                "WHERE w.answer_id=? AND j.status='running' AND j.desired='run' LIMIT 1",
                (row["id"],),
            ).fetchone()
            return {
                "id": row["id"], "question": json.loads(row["question"]),
                "job_id": waiter["job_id"] if waiter is not None else "",
            }

    def answer_stage(self, answer_id, stage):
        if stage not in {"recognizing", "solving", "verifying", "direct"}:
            raise ValueError("Invalid image pipeline stage")
        with self.transaction() as db:
            db.execute(
                "UPDATE answers SET diagnostics=?,updated_at=? WHERE id=? AND state='running'",
                (json.dumps({"stage": stage}), time.time(), answer_id),
            )

    def finish_answer(self, answer_id, answer=None, error="", *, quality="format_checked", diagnostics=None):
        if quality not in {"format_checked", "vision_cross_checked", "confirmed"}:
            raise ValueError("Invalid answer quality")
        if diagnostics is not None and not isinstance(diagnostics, dict):
            raise ValueError("Answer diagnostics must be an object")
        diagnostic_json = json.dumps(diagnostics or {}, ensure_ascii=False)
        if len(diagnostic_json) > 100000:
            raise ValueError("Answer diagnostics are oversized")
        with self.transaction() as db:
            db.execute(
                "UPDATE answers SET state=?,answer=?,error=?,answer_quality=?,diagnostics=?,updated_at=? WHERE id=?",
                ("error" if error else "done", answer, error, quality, diagnostic_json, time.time(), answer_id),
            )

    @staticmethod
    def _answer(row):
        diagnostics = json.loads(row["diagnostics"])
        return {
            "id": row["id"], "state": row["state"], "answer": row["answer"], "error": row["error"],
            "validation": row["answer_quality"], "stage": diagnostics.get("stage", ""),
        }

    def answer(self, job_id, answer_id):
        with self.transaction() as db:
            row = db.execute(
                "SELECT a.* FROM answers a JOIN answer_waiters w ON w.answer_id=a.id "
                "WHERE a.id=? AND w.job_id=?", (answer_id, job_id),
            ).fetchone()
            if row is None:
                raise NotFound("Answer request not found")
            return self._answer(row)
