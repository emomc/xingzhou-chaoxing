import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.service import Service, Settings, create_app
from backend.store import ACTIVE, Conflict, NotFound


class AccountManagementCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.number = 0
        self.open()

    def open(self):
        self.service = Service(Settings(state_dir=self.directory))
        self.addCleanup(self.service.close)
        self.client = create_app(self.service).test_client()
        self.headers = {"Authorization": "Bearer " + self.service.secrets.token}
        self.store = self.service.store

    def account(self):
        self.number += 1
        return self.store.save_account(
            "Offline " + str(self.number), "offline-login-" + str(self.number), "offline-password",
        )

    def job(self, account=None, **options):
        account = account or self.account()
        job, _ = self.store.create_job(account["id"], {
            "course_ids": ["1", "2", "3"], "videos_only": False, "preview": False,
            "submit": True, **options,
        })
        return job

    def confirm(self, job, course_id="1", **fields):
        self.store.event(job["id"], "course_result", {
            "course_id": course_id, "course": "Offline course " + course_id,
            "phase": "course_confirmed", "remaining_chapters": 0, **fields,
        })

    def summary(self, account_id):
        response = self.client.get("/v1/accounts", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return next(item for item in response.json["accounts"] if item["id"] == account_id)

    def delete(self, account_id):
        return self.client.delete("/v1/accounts/" + account_id, headers=self.headers)

    def test_completion_counts_unique_whole_courses_per_account(self):
        first, second = self.job(), self.job()
        self.store.event(first["id"], "chapter_result", {"completed": 300, "total": 300})
        self.assertEqual(self.summary(first["account_id"])["completed_courses"], 0)
        self.confirm(first)
        self.confirm(first)
        self.confirm(first, "2")
        self.confirm(second)
        self.store.set_state(first["id"], "needs_attention")
        self.assertEqual(self.summary(first["account_id"])["completed_courses"], 2)
        self.assertEqual(self.summary(second["account_id"])["completed_courses"], 1)
        output = json.dumps(self.client.get("/v1/accounts", headers=self.headers).json)
        self.assertNotIn("offline-password", output)
        self.assertNotIn("offline-login", output)
        self.assertNotIn("fingerprint", output)

    def test_preview_video_review_and_success_without_confirmation_are_not_counted(self):
        for options in ({"videos_only": True}, {"preview": True}):
            job = self.job(**options)
            self.confirm(job)
            self.assertEqual(self.summary(job["account_id"])["completed_courses"], 0)
        job = self.job()
        self.store.set_state(job["id"], "succeeded")
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 0)

    def test_only_selected_fully_confirmed_courses_are_recorded(self):
        job = self.job()
        for course_id, fields in (
            ("1", {"phase": "course_unconfirmed"}),
            ("2", {"remaining_chapters": 1}),
            ("3", {"remaining_chapters": False}),
            ("999", {}),
            ("invalid", {}),
        ):
            self.confirm(job, course_id, **fields)
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 0)
        self.confirm(job, "2")
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 1)

    def test_completed_courses_survive_event_pruning_deleted_jobs_and_restart(self):
        job = self.job()
        self.confirm(job)
        for index in range(501):
            self.store.event(job["id"], "phase", {"position": index})
        self.store.set_state(job["id"], "succeeded")
        self.store.delete_job(job["id"])
        self.service.close()
        self.open()
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 1)
        next_job, _ = self.store.create_job(job["account_id"], job["options"])
        self.confirm(next_job)
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 1)

    def test_legacy_backfill_uses_explicit_events_and_current_progress_only_once(self):
        job = self.job()
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET account_fingerprint='',completion_index_version=0,"
                "status='succeeded',progress=? WHERE id=?",
                (json.dumps({
                    "course_id": "3", "phase": "course_confirmed", "remaining_chapters": 0,
                }), job["id"]),
            )
            for course_id, phase in (("1", "course_confirmed"), ("2", "course_unconfirmed")):
                db.execute(
                    "INSERT INTO events(job_id,kind,data,created_at) VALUES (?,'course_result',?,100)",
                    (job["id"], json.dumps({
                        "course_id": course_id, "phase": phase, "remaining_chapters": 0,
                    })),
                )
        self.service.close()
        self.open()
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 2)
        self.store.delete_job(job["id"])
        self.service.close()
        self.open()
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 2)

    def test_legacy_edited_identity_is_not_guessed_from_old_events(self):
        job = self.job()
        self.store.set_state(job["id"], "succeeded")
        self.store.save_account("Renamed", None, None, account_id=job["account_id"])
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET account_fingerprint='',completion_index_version=0,progress=? WHERE id=?",
                (json.dumps({
                    "course_id": "1", "phase": "course_confirmed", "remaining_chapters": 0,
                }), job["id"]),
            )
        self.service.close()
        self.open()
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 0)
        self.store.action(job["id"], "retry")
        self.assertEqual(self.store.claim(), job["id"])
        self.confirm(job)
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 1)

    def test_changing_login_does_not_transfer_counts_or_retried_job_identity(self):
        job = self.job()
        self.confirm(job)
        self.store.set_state(job["id"], "succeeded")
        self.store.save_account(
            "New identity", "other-offline-login", "new-offline-password",
            account_id=job["account_id"],
        )
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 0)
        self.store.action(job["id"], "retry")
        self.store.claim()
        self.confirm(job, "2")
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 1)
        self.store.set_state(job["id"], "succeeded")
        self.store.save_account(
            "Original identity", "offline-login-1", "offline-password",
            account_id=job["account_id"],
        )
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 1)

    def test_partial_edit_keeps_encrypted_credentials_and_counts(self):
        job = self.job()
        self.confirm(job)
        response = self.client.put(
            "/v1/accounts/" + job["account_id"], headers=self.headers,
            json={"label": "New label", "username": "", "password": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.account_credentials(job["account_id"]), {
            "username": "offline-login-1", "password": "offline-password",
        })
        self.assertEqual(self.summary(job["account_id"])["completed_courses"], 1)
        self.assertEqual(self.client.put(
            "/v1/accounts/" + job["account_id"], headers=self.headers,
            json={"label": "Changed identity", "username": "another-login"},
        ).status_code, 400)

    def test_account_cards_include_live_activity_and_delete_protection(self):
        job = self.job()
        self.store.set_state(job["id"], "running")
        self.store.event(job["id"], "video", {
            "course_id": "1", "course": "Offline course", "phase": "video",
            "name": "Offline.mp4", "position": 120, "duration": 360,
        })
        summary = self.summary(job["account_id"])
        self.assertTrue(summary["is_studying"])
        self.assertFalse(summary["can_delete"])
        self.assertEqual(summary["delete_blocked_reason"], "ACCOUNT_HAS_ACTIVE_JOB")
        self.assertEqual(summary["current_job"]["id"], job["id"])
        self.assertEqual(summary["current_job"]["progress"]["position"], 120)
        self.store.set_state(job["id"], "paused")
        summary = self.summary(job["account_id"])
        self.assertFalse(summary["is_studying"])
        self.assertEqual(summary["current_job"]["status"], "paused")
        self.assertFalse(summary["can_delete"])

    def test_all_resumable_states_and_living_workers_block_deletion(self):
        for status in ACTIVE:
            with self.subTest(status=status):
                job = self.job()
                self.store.set_state(job["id"], status)
                self.assertEqual(self.delete(job["account_id"]).status_code, 409)
                self.assertFalse(self.summary(job["account_id"])["can_delete"])
                self.store.account_credentials(job["account_id"])
        job = self.job()
        self.store.set_state(job["id"], "failed", pid=os.getpid())
        self.assertEqual(self.delete(job["account_id"]).status_code, 409)
        self.assertFalse(self.summary(job["account_id"])["can_delete"])

    def test_course_lookups_block_delete_and_failed_lookup_does_not(self):
        for detail in (False, True):
            with self.subTest(detail=detail):
                account = self.account()
                if detail:
                    self.store.request_course_detail(account["id"], "1", queue_limit=100)
                else:
                    self.store.request_courses(account["id"])
                self.assertEqual(self.delete(account["id"]).status_code, 409)
                self.assertEqual(
                    self.summary(account["id"])["delete_blocked_reason"], "ACCOUNT_LOOKUP_BUSY",
                )
                table = "course_details" if detail else "course_fetches"
                with self.store.transaction() as db:
                    db.execute(
                        f"UPDATE {table} SET state='error',worker_pid=? WHERE account_id=?",
                        (os.getpid(), account["id"]),
                    )
                self.assertEqual(self.delete(account["id"]).status_code, 409)
                with self.store.transaction() as db:
                    db.execute(
                        f"UPDATE {table} SET worker_pid=NULL WHERE account_id=?", (account["id"],),
                    )
                self.assertEqual(self.delete(account["id"]).status_code, 204)

    def test_delete_cleans_account_records_without_deleting_shared_question_bank(self):
        first, second = self.job(), self.job()
        self.confirm(first)
        question = {"title": "Offline", "type": "single", "options": "A. One\nB. Two", "course_id": "1"}
        answer = self.store.request_answer(first["id"], "offline-bank-key", question)
        self.store.finish_answer(answer["id"], '{"Answer":["A"]}')
        self.store.request_answer(second["id"], "offline-bank-key", question)
        self.store.set_state(first["id"], "cancelled")
        response = self.delete(first["account_id"])
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.data, b"")
        with self.assertRaises(NotFound):
            self.store.account_credentials(first["account_id"])
        with self.assertRaises(NotFound):
            self.store.job(first["id"])
        self.assertEqual(self.store.answer(second["id"], answer["id"])["state"], "done")
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM course_completions").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.delete(first["account_id"]).status_code, 404)

    def test_account_delete_validates_ids_and_authentication(self):
        self.assertEqual(self.client.delete("/v1/accounts/" + "a" * 32).status_code, 401)
        self.assertEqual(self.delete("not-an-id").status_code, 400)
        self.assertEqual(self.delete("a" * 32).status_code, 404)

    def test_concurrent_create_and_delete_cannot_leave_orphaned_jobs(self):
        account = self.account()
        barrier = threading.Barrier(2)

        def create():
            barrier.wait()
            try:
                self.job(account)
                return "created"
            except NotFound:
                return "deleted_first"

        def delete():
            barrier.wait()
            try:
                self.store.delete_account(account["id"])
                return "deleted"
            except Conflict:
                return "created_first"

        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(create), pool.submit(delete)]
            outcomes = {future.result() for future in futures}
        self.assertIn(outcomes, ({"created", "created_first"}, {"deleted", "deleted_first"}))
        with self.store.transaction() as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


class StaleJobTests(unittest.TestCase):
    """卡在"等人工处理"的旧任务不该把这个账号锁死，否则永远开不了新任务。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.open()

    def open(self):
        self.service = Service(Settings(state_dir=self.directory))
        self.addCleanup(self.service.close)
        self.store = self.service.store

    def account(self):
        return self.store.save_account("Offline stale", "offline-stale", "offline-password")

    def job(self, account):
        job, _ = self.store.create_job(account["id"], {
            "course_ids": ["1"], "videos_only": False, "preview": False, "submit": True,
        })
        return job

    def test_a_job_waiting_for_attention_does_not_lock_the_account(self):
        account = self.account()
        stale = self.job(account)
        self.store.set_state(stale["id"], "needs_attention", error="INCOMPLETE_TASKS")
        fresh, _ = self.store.create_job(account["id"], stale["options"])
        self.assertEqual(fresh["status"], "queued")

    def test_two_live_jobs_for_one_account_are_still_refused(self):
        account = self.account()
        self.job(account)
        with self.assertRaises(Conflict):
            self.job(account)

    def test_an_old_index_is_migrated_when_the_service_starts(self):
        account = self.account()
        stale = self.job(account)
        self.store.set_state(stale["id"], "needs_attention", error="INCOMPLETE_TASKS")
        with self.store.transaction() as db:
            db.execute("DROP INDEX one_active_job_per_account")
            db.execute(
                "CREATE UNIQUE INDEX one_active_job_per_account ON jobs(account_id) WHERE status IN "
                "('queued','starting','running','retry_wait','stopping','paused','needs_attention')"
            )
        self.service.close()
        self.open()
        fresh, _ = self.store.create_job(account["id"], stale["options"])
        self.assertEqual(fresh["status"], "queued")


if __name__ == "__main__":
    unittest.main()
