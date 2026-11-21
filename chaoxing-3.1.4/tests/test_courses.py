from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from backend.course_worker import fetch_course_detail, fetch_courses, public_courses
from backend.courses import MAX_OUTPUT
from backend.service import Service, Settings, create_app
from backend.store import Conflict, NotFound, process_exists
from test_backend import FakeAI, FakeProcess


COURSES = [
    {"id": "101", "title": "Offline course one", "teacher": "Offline teacher"},
    {"id": "202", "title": "Offline course two", "teacher": ""},
]


class CourseProcess(FakeProcess):
    packets = []

    def __init__(self, args, **kwargs):
        super().__init__(args, **kwargs)
        packet = self.packets.pop(0) if self.packets else {"state": "done", "courses": COURSES}
        raw = packet if isinstance(packet, bytes) else json.dumps(packet).encode()
        self.stdout = io.BytesIO(raw)


class CourseAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        CourseProcess.packets = []
        self.service = Service(
            Settings(state_dir=self.directory), process_factory=FakeProcess,
            client_factory=FakeAI, course_process_factory=CourseProcess,
        )
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.service.close)
        self.client = create_app(self.service).test_client()
        self.headers = {"Authorization": "Bearer " + self.service.secrets.token}
        self.account = self.service.store.save_account("Offline", "offline-login", "offline-password")
        self.url = "/v1/accounts/" + self.account["id"] + "/courses"

    def request(self, account=None):
        url = "/v1/accounts/" + account["id"] + "/courses" if account else self.url
        return self.client.post(url, headers=self.headers, json={})

    def complete(self, code=0):
        self.service.courses.tick()
        for running in list(self.service.courses.workers.values()):
            running.process.returncode = code
        self.service.courses.tick()
        return self.client.get(self.url, headers=self.headers).json

    def test_read_requires_auth_and_never_logs_in_or_creates_jobs(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.post(self.url, json={}).status_code, 401)
        result = self.client.get(self.url, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["state"], "idle")
        self.assertEqual(result.json["courses"], [])
        self.assertFalse(self.service.courses.workers)
        self.assertEqual(self.service.store.jobs(), [])

    def test_account_validation_and_empty_request_body(self):
        self.assertEqual(self.client.get(
            "/v1/accounts/invalid/courses", headers=self.headers,
        ).status_code, 400)
        self.assertEqual(self.client.get(
            "/v1/accounts/" + "a" * 32 + "/courses", headers=self.headers,
        ).status_code, 404)
        for body in ({"username": "not-allowed"}, [], "invalid"):
            self.assertEqual(self.client.post(
                self.url, headers=self.headers, json=body,
            ).status_code, 400)
        self.assertEqual(self.client.post(self.url, headers=self.headers).status_code, 202)

    def test_async_fetch_is_deduplicated_and_returns_only_public_course_fields(self):
        first, repeated = self.request(), self.request()
        self.assertEqual((first.status_code, repeated.status_code), (202, 202))
        self.assertEqual(first.json["request_id"], repeated.json["request_id"])
        result = self.complete()
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["courses"], COURSES)
        self.assertEqual(self.service.store.jobs(), [])
        self.assertNotIn("offline-password", json.dumps(result))
        self.assertNotIn("offline-login", json.dumps(result))
        self.assertNotIn("worker_pid", result)
        self.assertNotEqual(self.request().json["request_id"], first.json["request_id"])

    def test_course_detail_is_async_and_returns_completion_counts(self):
        detail = {
            "state": "done",
            "course": {"courseId": "101", "title": "Offline course one", "teacher": "Teacher"},
            "points": [
                {
                    "id": "9001", "title": "第一章 视频", "task_count": 2,
                    "completed_count": 1, "remaining_count": 1,
                    "completed": False, "locked": False,
                },
                {
                    "id": "9002", "title": "第二章 测验", "task_count": 1,
                    "completed_count": 1, "remaining_count": 0,
                    "completed": True, "locked": False,
                },
            ],
            "totals": {"task_count": 3, "completed_count": 2,
                       "remaining_count": 1, "point_count": 2},
        }
        CourseProcess.packets = [detail]
        detail_url = self.url + "/101"
        started = self.client.post(detail_url, headers=self.headers, json={})
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json["state"], "queued")
        self.assertEqual(self.client.get(detail_url, headers=self.headers).json["state"], "queued")
        self.service.courses.tick()
        for running in list(self.service.courses.detail_workers.values()):
            running.process.returncode = 0
        self.service.courses.tick()
        result = self.client.get(detail_url, headers=self.headers).json
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["detail"]["totals"]["remaining_count"], 1)
        self.assertEqual(result["detail"]["points"][0]["completed_count"], 1)
        self.assertEqual(self.service.store.jobs(), [])

    def test_course_detail_requests_are_bounded_to_one_and_validate_course_id(self):
        first = self.client.post(self.url + "/101", headers=self.headers, json={})
        self.assertEqual(first.status_code, 202)
        other = self.service.store.save_account("Other", "other-login", "not-real")
        self.assertEqual(self.client.post(
            "/v1/accounts/" + other["id"] + "/courses/202",
            headers=self.headers, json={},
        ).status_code, 409)
        self.assertEqual(self.client.get(self.url + "/not-a-course", headers=self.headers).status_code, 400)

        with self.assertRaises(Conflict):
            self.service.store.create_job(self.account["id"], {
                "course_ids": ["101"], "submit": False, "preview": False, "videos_only": True,
            })

    def test_course_detail_keeps_zero_task_chapters_without_marking_a_task_passed(self):
        CourseProcess.packets = [{
            "state": "done", "course": COURSES[0],
            "points": [{
                "id": "9001", "title": "No task points", "task_count": 0,
                "completed_count": 0, "remaining_count": 0, "completed": False, "locked": False,
            }],
            "totals": {"task_count": 0, "completed_count": 0, "remaining_count": 0, "point_count": 1},
        }]
        detail_url = self.url + "/101"
        self.assertEqual(self.client.post(detail_url, headers=self.headers, json={}).status_code, 202)
        self.service.courses.tick()
        for running in self.service.courses.detail_workers.values():
            running.process.returncode = 0
        self.service.courses.tick()
        result = self.client.get(detail_url, headers=self.headers).json
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["detail"]["points"][0]["task_count"], 0)
        self.assertFalse(result["detail"]["points"][0]["completed"])

    def test_refresh_keeps_last_successful_courses_until_new_result(self):
        self.request()
        self.complete()

        refreshed = self.request().json
        self.assertEqual(refreshed["state"], "queued")
        self.assertEqual(refreshed["courses"], COURSES)

        running = self.service.store.claim_courses()
        self.service.store.finish_courses(
            self.account["id"], running["request_id"], error="COURSE_FETCH_FAILED",
        )
        result = self.service.store.courses(self.account["id"])
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["courses"], COURSES)

    def test_account_caches_and_subprocesses_are_isolated_and_bounded(self):
        accounts = [self.account] + [
            self.service.store.save_account(f"Offline {index}", f"login-{index}", "not-real")
            for index in range(4)
        ]
        for account in accounts:
            self.assertEqual(self.request(account).status_code, 202)
        self.service.courses.private_env_names = ("OFFLINE_PRIVATE_VISION",)
        with patch.dict(os.environ, {"WEBAI_API_KEY": "do-not-inherit", "CHAOXING_SERVICE_TOKEN": "private",
                                     "WEBAI_VISION_API_KEY": "private-vision", "OFFLINE_PRIVATE_VISION": "private-custom"}):
            self.service.courses.tick()
        workers = list(self.service.courses.workers.values())
        self.assertEqual(len(workers), 2)
        self.assertEqual(self.service.store.stats()["course_fetches"], {"running": 2, "queued": 3})
        directories = [Path(running.directory.name) for running in workers]
        self.assertEqual(len(set(directories)), 2)
        for running in workers:
            self.assertNotIn("password", str(running.process.args))
            self.assertNotIn("WEBAI_API_KEY", running.process.kwargs["env"])
            self.assertNotIn("WEBAI_VISION_API_KEY", running.process.kwargs["env"])
            self.assertNotIn("OFFLINE_PRIVATE_VISION", running.process.kwargs["env"])
            self.assertNotIn("CHAOXING_SERVICE_TOKEN", running.process.kwargs["env"])
            self.assertEqual(json.loads(running.process.stdin.getvalue()), {
                "credentials": self.service.store.account_credentials(running.account_id),
            })
            running.process.returncode = 0
        self.service.courses.tick()
        self.assertTrue(all(not directory.exists() for directory in directories))
        self.assertEqual(self.service.store.courses(accounts[0]["id"])["courses"], COURSES)
        self.assertEqual(self.service.store.courses(accounts[4]["id"])["courses"], [])

    def test_pending_lookup_blocks_job_creation_and_account_updates(self):
        self.request()
        spec = {"account_id": self.account["id"], "course_ids": ["101"]}
        self.assertEqual(self.client.post("/v1/jobs", headers=self.headers, json=spec).status_code, 409)
        self.assertEqual(self.client.put(
            "/v1/accounts/" + self.account["id"], headers=self.headers,
            json={"label": "Updated", "username": "offline-login", "password": "new-password"},
        ).status_code, 409)
        self.assertEqual(self.service.store.jobs(), [])

    def test_running_job_must_be_paused_before_refresh_and_cannot_resume_during_fetch(self):
        job, _ = self.service.store.create_job(self.account["id"], {
            "course_ids": ["101"], "submit": False, "preview": False, "videos_only": True,
        })
        self.assertEqual(self.request().status_code, 409)
        self.service.store.action(job["id"], "pause")
        self.assertEqual(self.request().status_code, 202)
        with self.assertRaises(Conflict):
            self.service.store.action(job["id"], "resume")
        self.complete()
        self.assertEqual(self.service.store.action(job["id"], "resume")["status"], "queued")
        self.assertEqual(self.client.get(self.url, headers=self.headers).json["courses"], COURSES)

    def test_updating_credentials_invalidates_cached_courses_and_increments_revision(self):
        self.request()
        self.complete()
        updated = self.service.store.save_account(
            "Offline", "offline-login", "changed-password", self.account["id"],
        )
        self.assertEqual(updated["revision"], self.account["revision"] + 1)
        self.assertEqual(self.client.get(self.url, headers=self.headers).json["state"], "idle")
        self.assertEqual(self.service.store.account_credentials(updated["id"])["password"], "changed-password")
        with self.assertRaises(NotFound):
            self.service.store.account_credentials("unknown")

    def test_timeouts_stop_only_owned_lookup_and_release_account(self):
        self.request()
        self.service.courses.tick()
        running = next(iter(self.service.courses.workers.values()))
        running.started -= 301
        self.service.courses.tick()
        self.assertEqual(running.process.returncode, -9)
        self.service.courses.tick()
        result = self.client.get(self.url, headers=self.headers).json
        self.assertEqual(result["error_code"], "LOOKUP_TIMEOUT")
        self.assertEqual(result["courses"], [])
        self.assertFalse(self.service.courses.workers)
        self.assertEqual(self.request().status_code, 202)

    def test_untrusted_worker_output_and_errors_are_not_exposed(self):
        for packet, code, expected in (
            ({"state": "error", "error_code": "LOGIN_REQUIRED", "details": "offline-password"}, 10, "LOGIN_REQUIRED"),
            ({"state": "done", "courses": COURSES, "cookie": "private"}, 1, "COURSE_FETCH_FAILED"),
            ({"state": "done", "courses": [{"id": "bad", "title": "invalid"}]}, 0, "COURSE_FETCH_FAILED"),
            ({"state": "error", "error_code": "secret-in-error"}, 10, "COURSE_FETCH_FAILED"),
            ({"state": "error", "error_code": {"private": "not-a-code"}}, 10, "COURSE_FETCH_FAILED"),
            (b"x" * (MAX_OUTPUT + 1), 0, "COURSE_FETCH_FAILED"),
            (b"not-json", 0, "COURSE_FETCH_FAILED"),
        ):
            with self.subTest(expected=expected, code=code):
                CourseProcess.packets = [packet]
                self.request()
                result = self.complete(code)
                self.assertEqual(result["error_code"], expected)
                self.assertEqual(result["courses"], [])
                self.assertNotIn("offline-password", json.dumps(result))
                self.assertNotIn("secret-in-error", json.dumps(result))

    def test_process_start_failure_is_sanitized_and_does_not_stick_running(self):
        self.service.courses.process_factory = Mock(side_effect=OSError("private-platform-url"))
        self.request()
        self.service.courses.tick()
        result = self.client.get(self.url, headers=self.headers).json
        self.assertEqual(result["error_code"], "LOOKUP_START_FAILED")
        self.assertNotIn("private-platform-url", json.dumps(result))
        self.assertFalse(self.service.courses.workers)

    def test_temporary_directory_failure_releases_the_account(self):
        self.request()
        with patch("backend.courses.tempfile.TemporaryDirectory", side_effect=OSError("disk-error")):
            self.service.courses.tick()
        self.assertEqual(
            self.service.store.courses(self.account["id"])["error_code"], "LOOKUP_START_FAILED",
        )
        self.assertFalse(self.service.courses.workers)
        self.assertEqual(self.request().status_code, 202)

    def test_thirty_parallel_requests_for_one_account_share_a_single_lookup(self):
        with ThreadPoolExecutor(max_workers=30) as executor:
            results = list(executor.map(
                lambda _: self.service.store.request_courses(self.account["id"]), range(30),
            ))
        self.assertEqual(len({result["request_id"] for result in results}), 1)
        self.service.courses.tick()
        self.assertEqual(len(self.service.courses.workers), 1)
        self.assertEqual(self.service.store.jobs(), [])

    def test_lookup_queue_is_bounded(self):
        self.service.store.request_courses(self.account["id"], queue_limit=1)
        other = self.service.store.save_account("Other", "other-login", "not-real")
        with self.assertRaises(Conflict):
            self.service.store.request_courses(other["id"], queue_limit=1)

    def test_late_result_cannot_overwrite_a_new_request(self):
        first = self.service.store.request_courses(self.account["id"])
        self.service.store.claim_courses()
        self.service.store.finish_courses(self.account["id"], first["request_id"], COURSES)
        second = self.service.store.request_courses(self.account["id"])
        self.service.store.claim_courses()
        self.service.store.finish_courses(self.account["id"], first["request_id"], COURSES)
        current = self.service.store.courses(self.account["id"])
        self.assertEqual(current["request_id"], second["request_id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["courses"], COURSES)

    def test_recovery_does_not_kill_or_reuse_a_live_unknown_process(self):
        lookup = self.service.store.request_courses(self.account["id"])
        self.service.store.claim_courses()
        self.service.store.course_worker_pid(self.account["id"], lookup["request_id"], os.getpid())
        self.service.store.recover_courses()
        result = self.service.store.courses(self.account["id"])
        self.assertEqual(result["error_code"], "PREVIOUS_LOOKUP")
        with self.assertRaises(Conflict):
            self.service.store.request_courses(self.account["id"])
        self.assertTrue(process_exists(os.getpid()))

    def test_cached_courses_survive_service_restart(self):
        self.request()
        self.complete()
        self.service.close()
        reopened = Service(Settings(state_dir=self.directory), course_process_factory=CourseProcess)
        try:
            self.assertEqual(reopened.store.courses(self.account["id"])["courses"], COURSES)
        finally:
            reopened.close()


class CourseWorkerTests(unittest.TestCase):
    def test_fetch_course_detail_uses_original_course_point_api(self):
        client = Mock()
        client.login.return_value = {"status": True}
        client.get_course_list.return_value = [{
            "courseId": "101", "clazzId": "class", "cpi": "cpi",
            "title": "Offline course", "teacher": "Teacher",
        }]
        client.get_course_point.return_value = {"points": [{
            "id": "9001", "title": "第一章", "jobCount": 2,
            "completedJobCount": 1, "has_finished": False, "need_unlock": False,
        }]}
        result = fetch_course_detail(
            {"username": "fake", "password": "not-real"}, "101", lambda _: client,
        )
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["totals"], {
            "task_count": 2, "completed_count": 1, "remaining_count": 1, "point_count": 1,
        })
        self.assertEqual([call[0] for call in client.method_calls],
                         ["login", "get_course_list", "get_course_point"])

    def test_fetch_only_calls_login_and_course_listing_and_strips_private_fields(self):
        client = Mock()
        client.login.return_value = {"status": True}
        client.get_course_list.return_value = [
            {"courseId": "101", "title": "  Offline\ncourse ", "teacher": "Test teacher",
             "cpi": "private", "clazzId": "private", "cookie": "private"},
            {"courseId": "101", "title": "Duplicate", "teacher": ""},
        ]
        client.get_course_point.return_value = {"points": [{
            "id": "9001", "title": "Lesson", "jobCount": 1,
            "completedJobCount": 0, "has_finished": False, "need_unlock": False,
        }]}
        result = fetch_courses({"username": "fake", "password": "not-real"}, lambda _: client)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["courses"][0]["id"], "101")
        self.assertEqual(result["courses"][0]["summary"]["status"], "ready")
        self.assertEqual(result["courses"][0]["summary"]["remaining_count"], 1)
        self.assertNotIn("cookie", result["courses"][0])
        self.assertEqual([call[0] for call in client.method_calls],
                         ["login", "get_course_list", "get_course_point"])

    def test_login_failure_does_not_fetch_courses_or_return_platform_message(self):
        client = Mock()
        client.login.return_value = {"status": False, "msg": "private-login-detail"}
        result = fetch_courses({}, lambda _: client)
        self.assertEqual(result, {"state": "error", "error_code": "LOGIN_REQUIRED"})
        client.get_course_list.assert_not_called()

    def test_empty_list_and_invalid_course_fields(self):
        self.assertEqual(public_courses([]), [])
        for courses in (
            {}, [None], [{"id": "1", "title": ""}], [{"id": "x", "title": "invalid"}],
            [{"id": "1", "title": "invalid", "teacher": {}}], COURSES * 251,
        ):
            with self.assertRaises(ValueError):
                public_courses(courses)

    def test_platform_http_error_is_not_reported_as_an_empty_course_list(self):
        from api.base import Chaoxing
        session = Mock()
        session.post.return_value.raise_for_status.side_effect = RuntimeError("HTTP failure")
        with patch("api.base.SessionManager.get_session", return_value=session), \
                self.assertRaises(RuntimeError):
            Chaoxing().get_course_list()
        session.get.assert_not_called()

    def test_platform_login_redirect_is_not_reported_as_an_empty_course_list(self):
        from api.base import Chaoxing
        from api.exceptions import MaxRetryExceeded
        session = Mock()
        session.post.return_value.url = "https://passport2.chaoxing.com/login"
        with patch("api.base.SessionManager.get_session", return_value=session), \
                self.assertRaises(MaxRetryExceeded):
            Chaoxing().get_course_list()
        session.get.assert_not_called()


    def test_one_outline_failure_keeps_the_other_course(self):
        client = Mock()
        client.login.return_value = {"status": True}
        client.get_course_list.return_value = [
            {"courseId": "101", "clazzId": "a", "cpi": "a", "title": "Broken", "teacher": ""},
            {"courseId": "202", "clazzId": "b", "cpi": "b", "title": "Ready", "teacher": "Teacher"},
        ]
        def points(course_id, _clazz, _cpi):
            if course_id == "101":
                raise RuntimeError("offline failure")
            return {"points": [{
                "id": "1", "title": "Lesson", "jobCount": 2,
                "completedJobCount": 2, "has_finished": True, "need_unlock": False,
            }]}
        client.get_course_point.side_effect = points
        result = fetch_courses({}, lambda _: client)
        self.assertEqual([course["id"] for course in result["courses"]], ["101", "202"])
        self.assertEqual(result["courses"][0]["summary"]["status"], "failed")
        self.assertEqual(result["courses"][1]["summary"]["remaining_count"], 0)

class CourseProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hold = False
        fixture = Path(__file__).parent / "fixtures" / "course_stub.py"

        def factory(_args, **kwargs):
            kwargs["env"]["OFFLINE_FIXTURE_HOLD"] = "1" if self.hold else "0"
            return subprocess.Popen([sys.executable, str(fixture)], **kwargs)

        self.service = Service(Settings(state_dir=Path(self.temp.name)), course_process_factory=factory)
        self.addCleanup(self.service.close)
        self.account = self.service.store.save_account("Offline", "fake-login", "not-real")

    def test_actual_child_returns_courses_without_creating_a_learning_job(self):
        self.service.courses.request(self.account["id"])
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.service.courses.tick()
            result = self.service.store.courses(self.account["id"])
            if result["state"] == "done":
                self.assertEqual(result["courses"][0]["id"], "101")
                self.assertEqual(self.service.store.jobs(), [])
                return
            time.sleep(0.03)
        self.fail("Offline course lookup child timed out")

    def test_stopping_service_closes_actual_lookup_child(self):
        self.hold = True
        self.service.courses.request(self.account["id"])
        self.service.courses.tick()
        running = next(iter(self.service.courses.workers.values()))
        self.service.close()
        self.assertFalse(process_exists(running.process.pid))
        self.assertEqual(running.process.returncode, 75)
        self.assertFalse(Path(running.directory.name).exists())


if __name__ == "__main__":
    unittest.main()
