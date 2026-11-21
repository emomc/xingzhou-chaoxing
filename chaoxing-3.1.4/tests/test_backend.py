from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from backend.broker import AnswerBroker
from backend.service import Service, Settings, create_app
from backend.security import Secrets
from backend.store import Conflict, process_exists
from backend.worker import run_courses, select_courses


QUESTION = {"title": "Offline only: 1 + 1 = ?", "type": "single", "options": "A. 1\nB. 2"}


class FakeInput(io.BytesIO):
    def __init__(self, process):
        super().__init__()
        self.process = process

    def close(self):
        if self.process.returncode is None:
            self.process.returncode = 75
        super().close()


class FakeProcess:
    sequence = 0

    def __init__(self, args, **kwargs):
        type(self).sequence += 1
        self.pid = 900000000 + self.sequence
        self.args, self.kwargs = args, kwargs
        self.returncode = None
        self.stdin = FakeInput(self)
        self.stdout = io.BytesIO()

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class FakeAI:
    calls = 0
    active = 0
    peak = 0
    lock = threading.Lock()
    response = '{"Answer":["B"]}'

    def complete(self, question):
        with self.lock:
            type(self).calls += 1
            type(self).active += 1
            type(self).peak = max(type(self).peak, type(self).active)
        try:
            time.sleep(0.02)
            return type(self).response
        finally:
            with self.lock:
                type(self).active -= 1

    def close(self):
        pass


class BackendCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        FakeAI.calls = FakeAI.active = FakeAI.peak = 0
        FakeAI.response = '{"Answer":["B"]}'
        self.service = Service(
            Settings(state_dir=self.directory), process_factory=FakeProcess, client_factory=FakeAI
        )
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.service.close)
        self.app = create_app(self.service)
        self.client = self.app.test_client()
        self.headers = {"Authorization": "Bearer " + self.service.secrets.token}
        self.number = 0

    def account(self):
        self.number += 1
        return self.service.store.save_account(
            "Offline account " + str(self.number), "test-login-" + str(self.number),
            "test-password-%-not-real",
        )

    def job(self, *, running=False, course_ids=None):
        job, _ = self.service.store.create_job(self.account()["id"], {
            "course_ids": course_ids or ["1"], "videos_only": True, "submit": False, "preview": False,
        })
        if running:
            self.service.store.set_state(job["id"], "running")
        return job

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for offline test state")

    def reopen(self):
        self.service.close()
        self.service = Service(
            Settings(state_dir=self.directory), process_factory=FakeProcess, client_factory=FakeAI,
        )
        self.addCleanup(self.service.close)

    def legacy_answer(self, question, jobs, answer='{"Answer":["B"]}', state="done"):
        answer_id, now = uuid.uuid4().hex, time.time()
        with self.service.store.transaction() as db:
            db.execute(
                "INSERT INTO answers(id,cache_key,question,state,answer,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (answer_id, "legacy:" + answer_id, json.dumps(question), state, answer, now, now),
            )
            db.executemany("INSERT INTO answer_waiters VALUES (?,?)", [
                (answer_id, job["id"]) for job in jobs
            ])
        return answer_id

    def test_authentication_and_public_liveness(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/v1/jobs").status_code, 401)
        self.assertEqual(self.client.get("/v1/jobs", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/v1/jobs", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post("/internal/answers", headers=self.headers, json=QUESTION).status_code, 401)

    def test_credentials_are_encrypted_and_never_returned(self):
        account = self.account()
        job = self.job()
        output = json.dumps({
            "accounts": self.service.store.accounts(), "jobs": self.service.store.jobs(),
        })
        self.assertNotIn("test-password", output)
        self.assertNotIn("test-login", output)
        payload = self.service.store.payload(job["id"])
        self.assertEqual(payload["credentials"]["password"], "test-password-%-not-real")
        with self.service.store.transaction() as db:
            encrypted = db.execute(
                "SELECT credentials FROM accounts WHERE id=?", (account["id"],)
            ).fetchone()[0]
        self.assertNotIn(b"test-password", encrypted)

    def test_single_service_instance_per_state_directory(self):
        with self.assertRaises(RuntimeError):
            Service(Settings(state_dir=self.directory))

    def test_missing_encryption_key_is_not_silently_replaced(self):
        self.account()
        self.service.close()
        key = self.directory / "credentials.key"
        key.unlink()
        with self.assertRaises(ValueError):
            Service(Settings(state_dir=self.directory))
        self.assertFalse(key.exists())

    def test_tokens_must_fit_an_ascii_authorization_header(self):
        for index, token in enumerate(("short", "x" * 513, "\u4e2d" * 32, "x" * 32 + "\x7f")):
            with self.subTest(index=index), self.assertRaises(ValueError):
                Secrets(self.directory / str(index), token)

    def test_pacing_endpoint_lists_windows_and_resting_jobs(self):
        job = self.job(running=True)
        self.service.store.event(job["id"], "phase", {
            "phase": "pacing", "name": "午饭时间，模拟休息，约 50 分钟",
            "course": "军事理论", "chapter": "1.1 第一节",
            "pacing_kind": "window", "pacing_until": time.time() + 600, "pacing_minutes": 50,
        })
        response = self.client.get("/v1/pacing", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json
        self.assertEqual([window["name"] for window in body["pacing"]["windows"]],
                         ["lunch", "dinner", "night"])
        self.assertTrue(body["pacing"]["config"]["enabled"])
        self.assertEqual(body["pacing"]["gaps"]["answer"], [1.0, 15.0])
        self.assertEqual(body["pacing"]["labels"]["windows"]["night"], "夜间休息")
        self.assertEqual(len(body["resting"]), 1)
        resting = body["resting"][0]
        self.assertEqual(resting["id"], job["id"])
        self.assertEqual(resting["kind"], "window")
        self.assertEqual(resting["reason"], "午饭时间，模拟休息，约 50 分钟")
        self.assertAlmostEqual(resting["minutes"], 50)

    def test_pacing_endpoint_needs_a_token(self):
        self.assertEqual(self.client.get("/v1/pacing").status_code, 401)

    def test_health_reports_an_unavailable_ai_worker(self):
        self.service.start()
        healthy = self.client.get("/v1/health", headers=self.headers)
        self.assertEqual(healthy.json["status"], "ok")
        self.service.broker.stop()
        degraded = self.client.get("/v1/health", headers=self.headers)
        self.assertEqual(degraded.json["status"], "degraded")
        self.assertEqual(degraded.json["ai_workers_alive"], 0)

    def test_api_requires_explicit_course_ids_and_real_booleans(self):
        account_id = self.account()["id"]
        for data in (
            {}, {"course_ids": []}, {"course_ids": ["unknown"]},
            {"course_ids": ["1"], "submit": "false"},
            {"course_ids": ["1"], "experimental_vision": "true"},
            {"course_ids": ["1"], "human_pacing": "true"},
            {"course_ids": ["1"], "submit": True, "preview": True},
            {"course_ids": ["1"], "videos_only": True, "submit": True},
        ):
            response = self.client.post(
                "/v1/jobs", headers=self.headers, json={"account_id": account_id, **data},
            )
            self.assertEqual(response.status_code, 400, response.json)

    def test_preview_is_default_and_submission_is_explicit(self):
        response = self.client.post("/v1/jobs", headers=self.headers, json={
            "account_id": self.account()["id"], "course_ids": ["1"],
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json["options"]["preview"])
        self.assertFalse(response.json["options"]["submit"])
        self.assertFalse(response.json["options"]["experimental_vision"])
        self.assertTrue(response.json["options"]["human_pacing"])

        enabled = self.client.post("/v1/jobs", headers=self.headers, json={
            "account_id": self.account()["id"], "course_ids": ["1"],
            "experimental_vision": True,
        })
        self.assertEqual(enabled.status_code, 201)
        self.assertTrue(enabled.json["options"]["experimental_vision"])

        paced_off = self.client.post("/v1/jobs", headers=self.headers, json={
            "account_id": self.account()["id"], "course_ids": ["1"],
            "human_pacing": False,
        })
        self.assertEqual(paced_off.status_code, 201)
        self.assertFalse(paced_off.json["options"]["human_pacing"])

    def test_jobs_can_be_limited_to_specific_chapters(self):
        account_id = self.account()["id"]
        response = self.client.post("/v1/jobs", headers=self.headers, json={
            "account_id": account_id, "course_ids": ["1", "2"],
            "point_ids": {"1": ["9001", "9002"]},
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["options"]["point_ids"], {"1": ["9001", "9002"]})
        # 没点名的课程照旧整门课，不写进 point_ids
        self.assertNotIn("2", response.json["options"]["point_ids"])

    def test_chapter_selection_must_point_at_the_same_courses(self):
        account_id = self.account()["id"]
        for point_ids in (
            {"2": ["9001"]},              # 课程没在 course_ids 里
            {"1": []},                    # 空列表
            {"1": "9001"},                # 不是列表
            {"1": ["abc"]},               # 不是数字 ID
            {"1": ["9001", "9001"]},      # 重复
            {"1": ["9001"] * 2001},       # 太多
            ["9001"],                     # 不是映射
        ):
            response = self.client.post("/v1/jobs", headers=self.headers, json={
                "account_id": account_id, "course_ids": ["1"], "point_ids": point_ids,
            })
            self.assertEqual(response.status_code, 400, (point_ids, response.json))

    def test_duplicate_job_and_idempotency_key(self):
        account_id = self.account()["id"]
        body = {"account_id": account_id, "course_ids": ["1"]}
        headers = {**self.headers, "Idempotency-Key": "offline-request"}
        first = self.client.post("/v1/jobs", headers=headers, json=body)
        second = self.client.post("/v1/jobs", headers=headers, json=body)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json["id"], second.json["id"])
        different = self.client.post(
            "/v1/jobs", headers=headers, json={**body, "course_ids": ["2"]}
        )
        self.assertEqual(different.status_code, 409)
        self.assertEqual(self.client.post("/v1/jobs", headers=self.headers, json=body).status_code, 409)

    def test_pause_resume_and_cancel_queued_job(self):
        job = self.job()
        for action, status in (("pause", "paused"), ("resume", "queued"), ("cancel", "cancelled")):
            response = self.client.post(
                f"/v1/jobs/{job['id']}/{action}", headers=self.headers, json={},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["status"], status)

    def test_job_list_order_uses_creation_time_not_progress_updates(self):
        older, newer = self.job(), self.job()
        with self.service.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET created_at=100,updated_at=900 WHERE id=?", (older["id"],)
            )
            db.execute(
                "UPDATE jobs SET created_at=200,updated_at=300 WHERE id=?", (newer["id"],)
            )
        first = self.client.get("/v1/jobs", headers=self.headers).json["jobs"]
        self.assertEqual([job["id"] for job in first], [newer["id"], older["id"]])

        with self.service.store.transaction() as db:
            db.execute("UPDATE jobs SET updated_at=1000 WHERE id=?", (older["id"],))
        second = self.client.get("/v1/jobs", headers=self.headers).json["jobs"]
        self.assertEqual([job["id"] for job in second], [newer["id"], older["id"]])

    def test_batch_pause_and_resume_only_explicit_eligible_jobs(self):
        queued, running, untouched = self.job(), self.job(running=True), self.job()
        ids = [queued["id"], running["id"]]
        paused = self.client.post("/v1/jobs/batch", headers=self.headers, json={
            "action": "pause", "job_ids": ids,
        })
        self.assertEqual(paused.status_code, 200)
        self.assertEqual((paused.json["succeeded"], paused.json["failed"]), (2, 0))
        self.assertEqual([item["job"]["status"] for item in paused.json["results"]],
                         ["paused", "stopping"])
        self.assertEqual(self.service.store.job(untouched["id"])["status"], "queued")
        resumed = self.client.post("/v1/jobs/batch", headers=self.headers, json={
            "action": "resume", "job_ids": ids + ["f" * 32],
        })
        self.assertEqual((resumed.json["succeeded"], resumed.json["failed"]), (1, 2))
        self.assertEqual(resumed.json["results"][1]["code"], "STATE_CONFLICT")
        self.assertEqual(resumed.json["results"][2]["code"], "NOT_FOUND")
        self.assertEqual(self.service.store.job(running["id"])["status"], "stopping")

    def test_batch_cannot_restart_finished_or_attention_jobs_or_reverse_cancellation(self):
        for state in ("succeeded", "failed", "cancelled", "needs_attention", "stopping"):
            job = self.job()
            self.service.store.set_state(job["id"], state)
            for action in ("resume", "pause"):
                response = self.client.post("/v1/jobs/batch", headers=self.headers, json={
                    "action": action, "job_ids": [job["id"]],
                })
                self.assertEqual(response.json["failed"], 1)
                self.assertEqual(self.service.store.job(job["id"])["status"], state)

    def test_archive_hides_finished_jobs_and_restore_does_not_start_them(self):
        job = self.job()
        self.service.store.set_state(job["id"], "succeeded")

        archived = self.client.post(
            f"/v1/jobs/{job['id']}/archive", headers=self.headers, json={},
        )
        self.assertEqual(archived.status_code, 200)
        self.assertIsNotNone(archived.json["archived_at"])
        self.assertEqual(self.client.get("/v1/jobs", headers=self.headers).json["jobs"], [])
        archived_list = self.client.get(
            "/v1/jobs?archived=1", headers=self.headers,
        )
        self.assertEqual([item["id"] for item in archived_list.json["jobs"]], [job["id"]])
        self.assertEqual(self.client.get("/v1/health", headers=self.headers).json["archived_jobs"], 1)
        self.assertNotIn("succeeded", self.client.get(
            "/v1/health", headers=self.headers,
        ).json["visible_jobs"])
        self.assertEqual(self.client.get(
            "/v1/jobs?archived=invalid", headers=self.headers,
        ).status_code, 400)

        retry = self.client.post(
            f"/v1/jobs/{job['id']}/retry", headers=self.headers, json={},
        )
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(self.service.store.job(job["id"])["status"], "succeeded")

        restored = self.client.post(
            f"/v1/jobs/{job['id']}/unarchive", headers=self.headers, json={},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertIsNone(restored.json["archived_at"])
        self.assertEqual(
            self.client.get("/v1/jobs", headers=self.headers).json["jobs"][0]["id"],
            job["id"],
        )
        self.assertEqual(self.service.store.job(job["id"])["status"], "succeeded")

    def test_only_finished_jobs_can_be_archived_and_batch_archive_is_partial(self):
        active, finished, cancelled = self.job(), self.job(), self.job()
        self.service.store.set_state(finished["id"], "failed")
        self.service.store.set_state(cancelled["id"], "cancelled")
        response = self.client.post("/v1/jobs/batch", headers=self.headers, json={
            "action": "archive",
            "job_ids": [active["id"], finished["id"], cancelled["id"], "f" * 32],
        })
        self.assertEqual((response.json["succeeded"], response.json["failed"]), (2, 2))
        self.assertEqual(response.json["results"][0]["code"], "STATE_CONFLICT")
        self.assertEqual(response.json["results"][3]["code"], "NOT_FOUND")
        self.assertEqual(self.service.store.job(active["id"])["archived_at"], None)
        self.assertIsNotNone(self.service.store.job(finished["id"])["archived_at"])
        self.assertIsNotNone(self.service.store.job(cancelled["id"])["archived_at"])

    def test_batch_validates_entire_request_before_changing_any_job(self):
        job = self.job()
        for body in (
            {}, {"action": "unknown", "job_ids": [job["id"]]},
            {"action": [], "job_ids": [job["id"]]},
            {"action": "pause", "job_ids": []},
            {"action": "pause", "job_ids": [job["id"], "invalid"]},
            {"action": "pause", "job_ids": [job["id"], job["id"]]},
            {"action": "pause", "job_ids": [job["id"], 1]},
            {"action": "pause", "job_ids": [job["id"]], "all": True},
            {"action": "pause", "job_ids": [f"{index:032x}" for index in range(1001)]},
        ):
            response = self.client.post("/v1/jobs/batch", headers=self.headers, json=body)
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(self.service.store.job(job["id"])["status"], "queued")
        self.assertEqual(self.client.post("/v1/jobs/batch", json={
            "action": "pause", "job_ids": [job["id"]],
        }).status_code, 401)

    def test_batch_cancel_preserves_unselected_and_stopping_jobs(self):
        queued, paused, stopping, untouched = [self.job() for _ in range(4)]
        self.service.store.action(paused["id"], "pause")
        self.service.store.set_state(stopping["id"], "stopping")
        response = self.client.post("/v1/jobs/batch", headers=self.headers, json={
            "action": "cancel", "job_ids": [queued["id"], paused["id"], stopping["id"]],
        })
        self.assertEqual((response.json["succeeded"], response.json["failed"]), (2, 1))
        self.assertEqual(self.service.store.job(queued["id"])["status"], "cancelled")
        self.assertEqual(self.service.store.job(paused["id"])["status"], "cancelled")
        self.assertEqual(self.service.store.job(stopping["id"])["status"], "stopping")
        self.assertEqual(self.service.store.job(untouched["id"])["status"], "queued")

    def test_batch_retry_handles_account_conflicts_without_losing_other_results(self):
        first, second, paused = self.job(), self.job(), self.job()
        self.service.store.set_state(first["id"], "failed")
        self.service.store.set_state(second["id"], "needs_attention")
        self.service.store.action(paused["id"], "pause")
        self.service.store.create_job(first["account_id"], {
            "course_ids": ["2"], "videos_only": True, "submit": False, "preview": False,
        })
        response = self.client.post("/v1/jobs/batch", headers=self.headers, json={
            "action": "retry", "job_ids": [first["id"], second["id"], paused["id"]],
        })
        self.assertEqual((response.json["succeeded"], response.json["failed"]), (1, 2))
        self.assertEqual(response.json["results"][0]["code"], "STATE_CONFLICT")
        self.assertEqual(response.json["results"][1]["job"]["status"], "queued")
        self.assertEqual(self.service.store.job(first["id"])["status"], "failed")
        self.assertEqual(self.service.store.job(paused["id"])["status"], "paused")

    def test_batch_delete_only_removes_finished_jobs_and_not_accounts_or_bank(self):
        finished, active, untouched = self.job(), self.job(), self.job()
        self.service.store.action(finished["id"], "cancel")
        before_accounts = len(self.service.store.accounts())
        response = self.client.post("/v1/jobs/batch", headers=self.headers, json={
            "action": "delete", "job_ids": [finished["id"], active["id"], "f" * 32],
        })
        self.assertEqual((response.json["succeeded"], response.json["failed"]), (1, 2))
        self.assertTrue(response.json["results"][0]["deleted"])
        self.assertEqual(response.json["results"][1]["code"], "STATE_CONFLICT")
        self.assertEqual(response.json["results"][2]["code"], "NOT_FOUND")
        self.assertEqual(len(self.service.store.accounts()), before_accounts)
        self.assertEqual(self.service.store.job(untouched["id"])["status"], "queued")
        with self.assertRaises(ValueError):
            self.service.store.job(finished["id"])

    def test_cancelled_job_can_be_deleted_but_active_job_cannot(self):
        cancelled = self.job()
        self.service.store.action(cancelled["id"], "cancel")
        response = self.client.delete(
            f"/v1/jobs/{cancelled['id']}", headers=self.headers,
        )
        self.assertEqual(response.status_code, 204)
        with self.assertRaises(ValueError):
            self.service.store.job(cancelled["id"])

        running = self.job(running=True)
        response = self.client.delete(
            f"/v1/jobs/{running['id']}", headers=self.headers,
        )
        self.assertEqual(response.status_code, 409)

    def test_active_account_login_identity_cannot_change(self):
        job = self.job()
        response = self.client.put(
            "/v1/accounts/" + job["account_id"], headers=self.headers,
            json={"label": "Different", "username": "another-login", "password": "test-only"},
        )
        self.assertEqual(response.status_code, 409)

    def test_events_are_bounded_and_cursor_based(self):
        job_id = self.job()["id"]
        for index in range(505):
            self.service.store.event(job_id, "phase", {"completed": index})
        events = self.service.store.events(job_id)
        self.assertEqual(len(events), 200)
        self.assertGreater(events[0]["id"], 1)
        later = self.service.store.events(job_id, events[-1]["id"])
        self.assertTrue(all(item["id"] > events[-1]["id"] for item in later))
        with self.service.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 500)

    def test_new_chapter_and_task_clear_stale_video_and_ai_progress(self):
        job_id = self.job()["id"]
        self.service.store.event(job_id, "video", {
            "course_id": "1", "chapter_id": "old", "phase": "video",
            "position": 120, "duration": 120, "video_state": "passed",
            "ai_state": "error", "quiz_answer": "B",
        })
        self.service.store.event(job_id, "video_rollback", {
            "course_id": "1", "chapter_id": "old", "phase": "video",
            "rollback_from": 427, "rollback_to": 61, "rollback_dropped": 366,
            "rollback_count": 1, "rollback_action": "slow",
        })
        self.service.store.event(job_id, "phase", {
            "phase": "chapter", "manual_tasks": [{
                "type": "hyperlink", "title": "Old task", "card_num": "2",
            }],
        })
        self.service.store.event(job_id, "chapter", {
            "course_id": "1", "chapter_id": "new", "phase": "chapter",
        })
        progress = self.service.store.job(job_id)["progress"]
        self.assertEqual(progress["chapter_id"], "new")
        self.assertFalse(set(progress) & {
            "position", "duration", "video_state", "ai_state", "quiz_answer", "manual_tasks",
            # 回退是单个任务点的事，换章节就得清掉，否则界面一直写着"平台已回退 N 次"
            "rollback_from", "rollback_to", "rollback_dropped", "rollback_count",
            "rollback_action",
        })

    def test_worker_outline_updates_shared_cache_without_a_second_account_login(self):
        job = self.job(running=True)
        detail = {
            "course": {"id": "1", "title": "Offline", "teacher": ""},
            "points": [{
                "id": "10", "title": "Lesson", "task_count": 1, "completed_count": 1,
                "remaining_count": 0, "completed": True, "locked": False,
            }],
            "totals": {"task_count": 1, "completed_count": 1, "remaining_count": 0, "point_count": 1},
        }
        self.service.store.event(job["id"], "course_outline", {
            "course_id": "1", "detail": detail, "completed": 1, "total": 1,
        })
        cached = self.service.store.course_detail(job["account_id"], "1")
        self.assertEqual(cached["detail"], detail)
        self.assertEqual(cached["state"], "done")
        requested = self.service.courses.request_details(job["account_id"], "1")
        self.assertEqual(requested["request_id"], cached["request_id"])
        self.assertEqual(requested["state"], "done")
        self.service.courses.tick()
        self.assertFalse(self.service.courses.detail_workers)
        progress = self.service.store.job(job["id"])["progress"]
        self.assertGreater(progress["outline_updated_at"], 0)
        self.assertNotIn("detail", progress)
        self.assertNotIn("detail", self.service.store.events(job["id"])[0]["data"])
        with self.assertRaises(ValueError):
            self.service.store.event(job["id"], "course_outline", {
                "course_id": "2", "detail": detail,
            })

    def test_worker_protocol_keeps_video_confirmation_quiz_and_chapter_fields(self):
        job = self.job()
        packets = [
            {"event": "video", "data": {
                "course_id": "1", "chapter_id": "10", "video_state": "confirming",
                "video_error": "", "position": 20, "duration": 20,
            }},
            {"event": "quiz", "data": {
                "phase": "workid", "quiz_state": "submitted", "quiz_answer": "false",
            }},
            {"event": "course_result", "data": {
                "phase": "course_unconfirmed", "remaining_chapters": 1,
            }},
        ]
        running = SimpleNamespace(process=SimpleNamespace(stdout=io.BytesIO(
            b"\n".join(json.dumps(packet).encode() for packet in packets) + b"\n",
        )))
        self.service.scheduler._read(job["id"], running)
        progress = self.service.store.job(job["id"])["progress"]
        self.assertEqual(progress["video_state"], "confirming")
        self.assertEqual(progress["chapter_id"], "10")
        self.assertEqual(progress["quiz_state"], "submitted")
        self.assertEqual(progress["quiz_answer"], "false")
        self.assertEqual(progress["remaining_chapters"], 1)

    def test_worker_protocol_keeps_manual_book_tasks(self):
        job = self.job()
        packets = [{"event": "phase", "data": {
            "phase": "books", "name": "需人工阅读图书：动画概论",
            "manual_tasks": [
                {"type": "book", "title": "动画概论", "card_num": "1", "jtoken": "private"},
                {"title": "x" * 400, "type": "book"},
                "not-a-dict",
            ],
        }}]
        running = SimpleNamespace(process=SimpleNamespace(stdout=io.BytesIO(
            b"\n".join(json.dumps(packet).encode() for packet in packets) + b"\n",
        )))
        self.service.scheduler._read(job["id"], running)
        tasks = self.service.store.job(job["id"])["progress"]["manual_tasks"]
        self.assertEqual(tasks[0], {"type": "book", "title": "动画概论", "card_num": "1"})
        self.assertEqual(len(tasks[0]), 3, "只保留类型、标题、卡片号")
        self.assertEqual(len(tasks[1]["title"]), 200)
        self.assertEqual(len(tasks), 2)

    def test_scheduler_enforces_thirty_process_limit_and_isolated_bootstrap(self):
        jobs = [self.job() for _ in range(35)]
        for _ in range(10):
            self.service.scheduler.tick()
        workers = self.service.scheduler.workers
        self.assertEqual(len(workers), 30)
        self.assertEqual(self.service.store.stats()["jobs"]["queued"], 5)
        directories = set()
        for job_id, running in workers.items():
            directories.add(running.process.kwargs["cwd"])
            self.assertNotIn("test-password", str(running.process.args))
            payload = json.loads(running.process.stdin.getvalue())
            self.assertEqual(payload["spec"]["course_ids"], ["1"])
            self.assertNotIn("WEBAI_API_KEY", running.process.kwargs["env"])
            self.assertEqual(self.service.store.job(job_id)["status"], "running")
        self.assertEqual(len(directories), 30)
        job_id, running = next(iter(workers.items()))
        running.result = {"outcome": "succeeded"}
        running.process.returncode = 0
        self.service.scheduler.tick()
        self.assertEqual(len(workers), 30)
        self.assertEqual(self.service.store.job(job_id)["status"], "succeeded")
        self.assertEqual(self.service.store.stats()["jobs"]["queued"], 4)

    def test_visual_credentials_do_not_enter_workers_and_visual_events_are_preserved(self):
        job = self.job()
        self.service.scheduler.private_env_names = ("OFFLINE_PRIVATE_VISION",)
        with patch.dict(os.environ, {
            "WEBAI_API_KEY": "private-text", "WEBAI_VISION_API_KEY": "private-vision",
            "OFFLINE_PRIVATE_VISION": "private-custom",
        }):
            self.service.scheduler.tick()
        running = self.service.scheduler.workers[job["id"]]
        for name in ("WEBAI_API_KEY", "WEBAI_VISION_API_KEY", "OFFLINE_PRIVATE_VISION"):
            self.assertNotIn(name, running.process.kwargs["env"])
        running.process.stdout = io.BytesIO(json.dumps({
            "event": "ai", "data": {
                "ai_mode": "vision", "ai_state": "vision_running", "ai_image_count": 3,
                "images": [{"data": "must-not-enter-progress"}],
            },
        }).encode() + b"\n")
        self.service.scheduler._read(job["id"], running)
        progress = self.service.store.job(job["id"])["progress"]
        self.assertEqual(progress["ai_mode"], "vision")
        self.assertEqual(progress["ai_image_count"], 3)
        self.assertNotIn("images", progress)

    def test_experimental_vision_option_reaches_worker_bootstrap(self):
        account = self.account()
        job, _ = self.service.store.create_job(account["id"], {
            "course_ids": ["1"], "videos_only": False, "submit": False,
            "preview": True, "experimental_vision": True,
        })
        self.service.scheduler.tick()
        running = self.service.scheduler.workers[job["id"]]
        payload = json.loads(running.process.stdin.getvalue())
        self.assertIs(payload["spec"]["experimental_vision"], True)

    def test_human_pacing_option_reaches_worker_bootstrap(self):
        account = self.account()
        job, _ = self.service.store.create_job(account["id"], {
            "course_ids": ["1"], "videos_only": False, "submit": False,
            "preview": True, "human_pacing": False,
        })
        self.service.scheduler.tick()
        running = self.service.scheduler.workers[job["id"]]
        payload = json.loads(running.process.stdin.getvalue())
        self.assertIs(payload["spec"]["human_pacing"], False)

    def test_running_pause_waits_for_worker_exit_before_resume(self):
        job_id = self.job()["id"]
        self.service.scheduler.tick()
        self.assertEqual(self.service.store.action(job_id, "pause")["status"], "stopping")
        with self.assertRaises(Conflict):
            self.service.store.action(job_id, "resume")
        self.service.scheduler.tick()
        self.service.scheduler.tick()
        self.assertEqual(self.service.store.job(job_id)["status"], "paused")
        self.assertEqual(self.service.store.action(job_id, "resume")["status"], "queued")

    def test_exit_without_success_packet_is_not_success(self):
        job_id = self.job()["id"]
        self.service.scheduler.tick()
        self.service.scheduler.workers[job_id].process.returncode = 0
        self.service.scheduler.tick()
        self.assertEqual(self.service.store.job(job_id)["status"], "retry_wait")

    def test_pausing_does_not_exhaust_the_bounded_failure_retry_budget(self):
        job_id = self.job()["id"]
        for _ in range(4):
            self.service.scheduler.tick()
            self.service.store.action(job_id, "pause")
            self.service.scheduler.tick()
            self.service.scheduler.tick()
            self.assertEqual(self.service.store.job(job_id)["status"], "paused")
            self.service.store.action(job_id, "resume")
        self.assertEqual(self.service.store.job(job_id)["failures"], 0)
        for failures in range(1, 4):
            self.service.scheduler.tick()
            self.service.scheduler.workers[job_id].process.returncode = 1
            self.service.scheduler.tick()
            job = self.service.store.job(job_id)
            self.assertEqual(job["failures"], failures)
            self.assertEqual(job["status"], "retry_wait" if failures < 3 else "failed")
            with self.service.store.transaction() as db:
                db.execute("UPDATE jobs SET next_run=0 WHERE id=?", (job_id,))
        self.assertEqual(self.service.store.job(job_id)["attempts"], 7)
        self.assertEqual(self.service.store.action(job_id, "retry")["failures"], 0)

    def test_shutdown_requeues_running_job(self):
        job_id = self.job()["id"]
        self.service.scheduler.tick()
        self.service.scheduler.stop()
        self.assertEqual(self.service.store.job(job_id)["status"], "queued")

    def test_recovery_does_not_launch_over_a_live_process(self):
        job_id = self.job()["id"]
        self.service.store.set_state(job_id, "running", pid=os.getpid())
        self.service.store.recover()
        self.assertEqual(self.service.store.job(job_id)["status"], "needs_attention")
        with self.assertRaises(Conflict):
            self.service.store.action(job_id, "retry")
        self.assertTrue(process_exists(os.getpid()))

    def test_recovered_job_can_be_cancelled_after_its_previous_process_exits(self):
        job_id = self.job()["id"]
        self.service.store.set_state(job_id, "needs_attention", pid=900000000)
        with patch("backend.store.process_exists", return_value=True):
            with self.assertRaises(Conflict):
                self.service.store.action(job_id, "cancel")
        with patch("backend.store.process_exists", return_value=False):
            result = self.service.store.action(job_id, "cancel")
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(self.service.store.worker_state(job_id)["worker_pid"])

    def test_dead_worker_is_recovered_and_missing_course_never_means_all(self):
        job_id = self.job()["id"]
        self.service.store.set_state(job_id, "running", pid=900000000)
        self.service.store.recover()
        self.assertEqual(self.service.store.job(job_id)["status"], "queued")
        with self.assertRaises(ValueError):
            select_courses([{"courseId": "1"}], ["2"])
        self.assertEqual(select_courses([{"courseId": "1"}, {"courseId": "2"}], ["2"]), [{"courseId": "2"}])
        self.assertEqual(
            [course["courseId"] for course in select_courses(
                [{"courseId": "1"}, {"courseId": "2"}], ["2", "1"],
            )],
            ["2", "1"],
        )
        seen = []

        def process(course):
            seen.append(course["courseId"])
            return course["courseId"] != "1"

        success, failed = run_courses(process, [
            {"courseId": "1", "title": "First"}, {"courseId": "2", "title": "Second"},
        ])
        self.assertFalse(success)
        self.assertEqual(seen, ["1", "2"])
        self.assertEqual(failed, ["First"])

    def test_thirty_identical_ai_requests_are_deduplicated(self):
        jobs = [self.job(running=True) for _ in range(30)]
        with ThreadPoolExecutor(max_workers=30) as executor:
            answers = list(executor.map(
                lambda job: self.service.broker.request(job["id"], QUESTION), jobs,
            ))
        self.assertEqual(len({answer["id"] for answer in answers}), 1)
        self.service.broker.start()
        answer_id = answers[0]["id"]
        self.wait_for(lambda: self.service.store.answer(jobs[0]["id"], answer_id)["state"] == "done")
        self.assertEqual(FakeAI.calls, 1)
        self.assertEqual(FakeAI.peak, 1)
        cached = self.service.broker.request(jobs[1]["id"], QUESTION)
        self.assertEqual(cached["state"], "done")
        self.assertEqual(json.loads(cached["answer"]), {"Answer": ["B. 2"]})

    def test_same_course_bank_reuses_numbered_questions_but_not_other_courses(self):
        first = self.job(running=True, course_ids=["267851788", "100"])
        second = self.job(running=True, course_ids=["267851788", "100"])
        original = QUESTION | {
            "course_id": "267851788",
            "title": "1【单选题】Offline only: 1 + 1 = ?",
        }
        same_course = QUESTION | {
            "course_id": "267851788",
            "title": "2【单选题】  Offline only: 1 + 1 = ?",
        }
        other_course = original | {"course_id": "100"}
        different_options = same_course | {"options": "A. 2\nB. 1"}
        first_answer = self.service.broker.request(first["id"], original)
        self.service.broker.start()
        self.wait_for(lambda: self.service.store.answer(
            first["id"], first_answer["id"])["state"] == "done")
        self.service.broker.stop()
        reused = self.service.broker.request(second["id"], same_course)
        self.assertEqual(reused["id"], first_answer["id"])
        self.assertEqual(reused["state"], "done")
        separate = self.service.broker.request(second["id"], other_course)
        changed = self.service.broker.request(second["id"], different_options)
        self.assertNotEqual(separate["id"], first_answer["id"])
        self.assertNotEqual(changed["id"], first_answer["id"])
        self.assertEqual(separate["state"], "queued")
        self.assertEqual(FakeAI.calls, 1)

    def test_thirty_numbered_variants_share_one_generation_and_report_sources(self):
        jobs = [self.job(running=True) for _ in range(30)]
        def ask(pair):
            index, job = pair
            return self.service.broker.request(job["id"], QUESTION | {
                "course_id": "1", "title": f"{index + 1}【单选题】 " + QUESTION["title"],
            })
        with ThreadPoolExecutor(max_workers=30) as executor:
            results = list(executor.map(ask, enumerate(jobs)))
        self.assertEqual(len({result["id"] for result in results}), 1)
        self.assertEqual(sum(result["source"] == "ai" for result in results), 1)
        self.assertEqual(sum(result["source"] == "shared" for result in results), 29)
        answer_id = results[0]["id"]
        self.service.broker.start()
        self.wait_for(lambda: self.service.store.answer(jobs[0]["id"], answer_id)["state"] == "done")
        self.service.broker.stop()
        for job in jobs:
            result = self.service.broker.request(job["id"], QUESTION)
            self.assertEqual((result["id"], result["source"]), (answer_id, "bank"))
            self.assertEqual(json.loads(result["answer"]), {"Answer": ["B. 2"]})
        self.assertEqual(FakeAI.calls, 1)
        self.assertEqual(self.service.store.stats()["question_bank"], {
            "entries": 1, "hits": 30, "shared_waits": 29,
        })

    def test_bank_survives_deleted_job_restart_and_an_offline_different_ai(self):
        job = self.job(running=True)
        answer = self.service.broker.request(job["id"], QUESTION)
        self.service.store.finish_answer(answer["id"], '{"Answer":["B"]}')
        self.service.store.set_state(job["id"], "cancelled")
        self.service.store.delete_job(job["id"])
        self.reopen()
        other = AnswerBroker(self.service.store, {
            "webai_endpoint": "http://127.0.0.1:9/v1", "webai_model": "different-model",
        }, client_factory=lambda: self.fail("A bank hit must not initialize the AI client"))
        second = self.job(running=True)
        result = other.request(second["id"], QUESTION)
        self.assertEqual((result["id"], result["source"]), (answer["id"], "bank"))
        self.assertEqual(FakeAI.calls, 0)
        self.reopen()
        self.assertEqual(self.service.store.stats()["question_bank"], {
            "entries": 1, "hits": 1, "shared_waits": 0,
        })

    def test_legacy_single_course_results_are_indexed_without_losing_ids_or_waiters(self):
        job = self.job()
        answer_id = self.legacy_answer(
            QUESTION | {"title": "1【单选题】" + QUESTION["title"]}, [job],
        )
        self.reopen()
        self.assertEqual(self.service.store.answer(job["id"], answer_id)["state"], "done")
        other = self.job(running=True)
        result = self.service.broker.request(other["id"], QUESTION | {
            "title": "9【单选题】 " + QUESTION["title"], "course_id": "1",
        })
        self.assertEqual((result["id"], result["source"]), (answer_id, "bank"))
        with self.service.store.transaction() as db:
            row = db.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone()
            self.assertEqual(json.loads(row["question"])["course_id"], "1")
            self.assertEqual(row["bank_index_version"], 1)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM answer_waiters WHERE answer_id=?", (answer_id,),
            ).fetchone()[0], 2)
        self.reopen()
        self.assertEqual(self.service.store.stats()["question_bank"]["entries"], 1)
        self.assertEqual(self.service.store.stats()["question_bank"]["hits"], 1)

    def test_ambiguous_and_invalid_legacy_results_are_retained_but_not_shared(self):
        first = self.job(course_ids=["1", "2"])
        second = self.job()
        ambiguous = self.legacy_answer(QUESTION, [first, second])
        invalid = self.legacy_answer(QUESTION | {"title": "Invalid old answer"}, [second], '{"Answer":["Z"]}')
        missing = self.legacy_answer(QUESTION | {"title": "No remaining job"}, [])
        self.reopen()
        for answer_id in (ambiguous, invalid, missing):
            with self.service.store.transaction() as db:
                row = db.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone()
                self.assertIsNone(row["bank_key"])
                self.assertEqual(row["state"], "done")
        self.assertEqual(self.service.store.stats()["question_bank"]["entries"], 0)
        result = self.service.broker.request(second["id"], QUESTION)
        self.assertNotEqual(result["id"], ambiguous)
        self.assertEqual(result["source"], "ai")

    def test_duplicate_legacy_answers_choose_a_valid_completed_result_once(self):
        jobs = [self.job() for _ in range(3)]
        first = self.legacy_answer(QUESTION | {"title": "1【单选题】" + QUESTION["title"]}, [jobs[0]])
        second = self.legacy_answer(QUESTION | {"title": "2【单选题】" + QUESTION["title"]}, [jobs[1]])
        pending = self.legacy_answer(QUESTION, [jobs[2]], answer=None, state="queued")
        self.reopen()
        result = self.service.broker.request(jobs[2]["id"], QUESTION)
        self.assertIn(result["id"], (first, second))
        self.assertEqual(result["source"], "bank")
        self.assertEqual(self.service.store.answer(jobs[2]["id"], pending)["error"], "AI_UPGRADE_RETRY")
        self.assertIsNone(self.service.store.claim_answer(3600))
        self.assertEqual(self.service.store.storage_summary()["records"]["answers"], 3)
        self.assertEqual(self.service.store.stats()["question_bank"]["entries"], 1)

    def test_invalid_stored_bank_answer_regenerates_instead_of_poisoning_other_accounts(self):
        first, second = self.job(running=True), self.job(running=True)
        answer = self.service.broker.request(first["id"], QUESTION)
        self.service.store.finish_answer(answer["id"], '{"Answer":["Z"]}')
        result = self.service.broker.request(second["id"], QUESTION)
        self.assertEqual((result["id"], result["state"], result["source"]), (answer["id"], "queued", "ai"))
        self.assertIsNone(result["answer"])
        self.assertEqual(self.service.store.stats()["question_bank"]["hits"], 0)
        self.service.broker.start()
        self.wait_for(lambda: self.service.store.answer(second["id"], answer["id"])["state"] == "done")
        self.assertEqual(FakeAI.calls, 1)
        self.assertEqual(self.service.broker.request(first["id"], QUESTION)["source"], "bank")

    def test_answer_context_must_belong_to_the_requesting_job(self):
        job = self.job(running=True, course_ids=["1", "2"])
        for course_id in ("3", "１", "1 ", 1, [], False):
            with self.subTest(course_id=course_id), self.assertRaises(ValueError):
                self.service.broker.request(job["id"], QUESTION | {"course_id": course_id})
        with self.assertRaises(ValueError):
            self.service.broker.request(job["id"], QUESTION)
        self.service.store.event(job["id"], "course", {"course_id": "2"})
        inferred = self.service.broker.request(job["id"], QUESTION)
        explicit = self.service.broker.request(job["id"], QUESTION | {"course_id": "2"})
        self.assertEqual(inferred["id"], explicit["id"])
        different = self.service.broker.request(job["id"], QUESTION | {"course_id": "1"})
        self.assertNotEqual(inferred["id"], different["id"])

    def test_a_bank_hit_cannot_bypass_image_validation_or_changed_question_type(self):
        job = self.job()
        answer = self.service.broker.request(job["id"], QUESTION)
        self.service.store.finish_answer(answer["id"], '{"Answer":["B"]}')
        with self.assertRaises(ValueError):
            self.service.broker.request(job["id"], QUESTION | {"has_images": True})
        other = self.service.broker.request(job["id"], QUESTION | {"type": "multiple"})
        self.assertNotEqual(other["id"], answer["id"])
        self.assertEqual(other["source"], "ai")

    def test_one_course_exception_does_not_discard_the_next_selected_course(self):
        seen = []
        def process(course):
            seen.append(course["courseId"])
            if course["courseId"] == "1":
                raise ValueError("not logged")
            return True
        self.assertEqual(run_courses(process, [
            {"courseId": "1", "title": "First"}, {"courseId": "2", "title": "Second"},
        ]), (False, ["First"]))
        self.assertEqual(seen, ["1", "2"])

    def test_old_invalid_ai_request_can_regenerate_immediately(self):
        job_id = self.job(running=True)["id"]
        first = self.service.broker.request(job_id, QUESTION)
        self.service.store.finish_answer(first["id"], error="AI_INVALID_ANSWER: old parser")
        second = self.service.broker.request(job_id, QUESTION)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["state"], "queued")

    def test_broker_retries_a_malformed_generation_without_a_chapter_retry(self):
        job_id = self.job(running=True)["id"]
        client = FakeAI()
        with patch.object(client, "complete", side_effect=['not-json', '{"Answer":["B"]}']) as complete:
            self.service.broker.client_factory = lambda: client
            answer = self.service.broker.request(job_id, QUESTION)
            self.service.broker.start()
            self.wait_for(lambda: self.service.store.answer(job_id, answer["id"])["state"] == "done")
            self.assertEqual(complete.call_count, 2)

    def test_invalid_ai_answer_is_not_a_cache_hit(self):
        FakeAI.response = '{"Answer":["unmatched"]}'
        job_id = self.job(running=True)["id"]
        answer = self.service.broker.request(job_id, QUESTION)
        self.service.broker.start()
        self.wait_for(lambda: self.service.store.answer(job_id, answer["id"])["state"] == "error")
        result = self.service.store.answer(job_id, answer["id"])
        self.assertIsNone(result["answer"])
        self.assertTrue(str(result["error"]).startswith("AI_INVALID_ANSWER"))

    def test_cancelled_job_does_not_trigger_new_ai_request(self):
        job_id = self.job(running=True)["id"]
        self.service.broker.request(job_id, QUESTION)
        self.service.store.action(job_id, "cancel")
        self.assertIsNone(self.service.store.claim_answer(3600))

    def test_paused_ai_request_is_requeued_when_its_job_resumes(self):
        job_id = self.job(running=True)["id"]
        first = self.service.broker.request(job_id, QUESTION)
        self.service.store.action(job_id, "pause")
        self.assertIsNone(self.service.store.claim_answer(3600))
        self.assertEqual(self.service.store.answer(job_id, first["id"])["error"], "NO_ACTIVE_WAITERS")
        self.service.store.set_state(job_id, "paused")
        self.service.store.action(job_id, "resume")
        self.service.store.set_state(job_id, "running")
        resumed = self.service.broker.request(job_id, QUESTION)
        self.assertEqual(resumed["id"], first["id"])
        self.assertEqual(resumed["state"], "queued")
        self.assertEqual(self.service.store.claim_answer(3600)["id"], first["id"])

    def test_worker_tokens_cannot_read_other_jobs_answers(self):
        first, second = self.job(), self.job()
        self.service.scheduler.tick()
        running = self.service.scheduler.workers[first["id"]]
        headers = {"Authorization": "Bearer " + running.token}
        answer = self.client.post("/internal/answers", headers=headers, json=QUESTION)
        self.assertEqual(answer.status_code, 202)
        other = self.service.broker.request(second["id"], QUESTION | {"title": "Different"})
        denied = self.client.get("/internal/answers/" + other["id"], headers=headers)
        self.assertEqual(denied.status_code, 404)

    def test_internal_api_returns_bank_hits_immediately_and_drops_unneeded_payload_fields(self):
        first, second = self.job(), self.job()
        self.service.scheduler.tick()
        first_headers = {
            "Authorization": "Bearer " + self.service.scheduler.workers[first["id"]].token,
        }
        initial = self.client.post("/internal/answers", headers=first_headers, json=QUESTION | {
            "course_id": "1", "password": "never-save-this", "answerField": {"private": "discard"},
        })
        self.assertEqual(initial.status_code, 202)
        self.assertEqual(initial.json["source"], "ai")
        self.service.store.finish_answer(initial.json["id"], '{"Answer":["B"]}')
        second_headers = {
            "Authorization": "Bearer " + self.service.scheduler.workers[second["id"]].token,
        }
        cached = self.client.post("/internal/answers", headers=second_headers, json=QUESTION | {
            "course_id": "1", "title": "9【单选题】" + QUESTION["title"],
        })
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(cached.json["source"], "bank")
        self.assertEqual(cached.json["id"], initial.json["id"])
        with self.service.store.transaction() as db:
            saved = db.execute("SELECT question FROM answers WHERE id=?", (initial.json["id"],)).fetchone()[0]
        self.assertNotIn("never-save-this", saved)
        self.assertNotIn("answerField", saved)
        denied = self.client.post("/internal/answers", headers=second_headers, json=QUESTION | {"course_id": "2"})
        self.assertEqual(denied.status_code, 400)

    def test_state_survives_reopening_without_credentials_in_job_response(self):
        job_id = self.job()["id"]
        self.service.close()
        reopened = Service(Settings(state_dir=self.directory), process_factory=FakeProcess)
        try:
            self.assertEqual(reopened.store.job(job_id)["status"], "queued")
            self.assertEqual(reopened.store.payload(job_id)["credentials"]["password"], "test-password-%-not-real")
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
