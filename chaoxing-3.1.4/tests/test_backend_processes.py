import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from backend.client import BrokerClient
from backend.service import Service, Settings
from backend.store import process_exists
from api.webai_client import WebAIError
from tests.test_vision import prepared_question


class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hold = False
        self.addCleanup(self.temp.cleanup)
        fixture = Path(__file__).parent / "fixtures" / "worker_stub.py"

        def factory(_args, **kwargs):
            kwargs["env"]["OFFLINE_FIXTURE_HOLD"] = "1" if self.hold else "0"
            return subprocess.Popen([sys.executable, str(fixture)], **kwargs)

        self.service = Service(Settings(state_dir=Path(self.temp.name)), process_factory=factory)
        self.addCleanup(self.service.close)
        account = self.service.store.save_account("Offline process", "fake", "not-real")
        self.job, _ = self.service.store.create_job(account["id"], {
            "course_ids": ["1"], "videos_only": True, "submit": False, "preview": False,
        })

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.service.scheduler.tick()
            if predicate():
                return
            time.sleep(0.03)
        self.fail("Offline child lifecycle timed out")

    def test_real_child_protocol_and_success_confirmation(self):
        self.wait(lambda: self.service.store.job(self.job["id"])["status"] == "succeeded")
        job = self.service.store.job(self.job["id"])
        self.assertEqual(job["progress"]["completed"], 1)
        self.assertEqual(job["attempts"], 1)

    def test_closing_parent_pipe_stops_actual_child(self):
        self.hold = True
        self.service.scheduler.tick()
        running = self.service.scheduler.workers[self.job["id"]]
        self.wait(lambda: self.service.store.job(self.job["id"])["progress"].get("phase") == "offline_fixture")
        running.process.stdin.close()
        self.wait(lambda: running.process.poll() is not None)
        self.assertEqual(running.process.returncode, 75)
        self.assertFalse(process_exists(running.process.pid))
        self.assertEqual(self.service.store.job(self.job["id"])["status"], "retry_wait")

    def test_pause_does_not_leave_an_actual_child_running(self):
        self.hold = True
        self.service.scheduler.tick()
        running = self.service.scheduler.workers[self.job["id"]]
        self.service.store.action(self.job["id"], "pause")
        self.wait(lambda: self.service.store.job(self.job["id"])["status"] == "paused")
        self.assertIsNotNone(running.process.poll())
        self.assertEqual(self.service.store.job(self.job["id"])["failures"], 0)


class BrokerClientTests(unittest.TestCase):
    def test_short_polling_protocol(self):
        client = BrokerClient({
            "webai_broker_endpoint": "http://127.0.0.1:3100/internal",
            "webai_broker_token": "offline-only", "webai_broker_timeout": 30,
        })
        self.addCleanup(client.close)
        answer = '{"Answer":["B"]}'
        client._request = Mock(side_effect=[
            {"id": "request-id", "state": "queued"}, {"state": "done", "answer": answer},
        ])
        with patch("backend.client.time.sleep"):
            self.assertEqual(client.complete({
                "type": "single", "title": "Offline question", "options": "A. 1\nB. 2",
            }), answer)
        self.assertEqual(client._request.call_args_list[0].args[:2], ("POST", "/answers"))
        self.assertEqual(client._request.call_args_list[1].args[:2], ("GET", "/answers/request-id"))

    def test_ready_course_bank_answer_does_not_wait_for_ai(self):
        client = BrokerClient({
            "webai_broker_endpoint": "http://127.0.0.1:3100/internal",
            "webai_broker_token": "offline-only", "webai_broker_timeout": 30,
        })
        self.addCleanup(client.close)
        answer = '{"Answer":["B. 2"]}'
        client._request = Mock(return_value={"id": "request-id", "state": "done", "answer": answer})
        with patch("backend.client.report") as progress:
            self.assertEqual(client.complete({
                "type": "single", "title": "1【单选题】Offline question",
                "options": "A. 1\nB. 2", "course_id": "267851788",
            }), answer)
        self.assertEqual(client.last_source, "bank")
        self.assertEqual([call.kwargs["ai_state"] for call in progress.call_args_list], ["cached"])
        self.assertEqual(client._request.call_count, 1)

    def test_short_polling_reports_answer_states_without_question_leakage(self):
        client = BrokerClient({
            "webai_broker_endpoint": "http://127.0.0.1:3100/internal",
            "webai_broker_token": "offline-only", "webai_broker_timeout": 30,
        })
        self.addCleanup(client.close)
        client._request = Mock(side_effect=[
            {"id": "request-id", "state": "queued"},
            {"state": "running"},
            {"state": "done", "answer": '{"Answer":["B"]}'},
        ])
        with patch("backend.client.time.sleep"), patch("backend.client.report") as progress:
            client.complete({
                "type": "single", "title": "Offline question", "options": "A. 1\nB. 2",
            })
        states = [call.kwargs["ai_state"] for call in progress.call_args_list]
        self.assertEqual(states, ["queued", "running", "answered"])
        self.assertTrue(all("Offline question" == call.kwargs["ai_question"] for call in progress.call_args_list))

    def test_joining_an_inflight_question_reports_shared_wait_and_shared_result(self):
        client = BrokerClient({
            "webai_broker_endpoint": "http://127.0.0.1:3100/internal",
            "webai_broker_token": "offline-only",
        })
        self.addCleanup(client.close)
        client._request = Mock(side_effect=[
            {"id": "request-id", "state": "running", "source": "shared"},
            {"state": "done", "answer": '{"Answer":["B. 2"]}'},
        ])
        with patch("backend.client.time.sleep"), patch("backend.client.report") as progress:
            client.complete({"type": "single", "title": "Question", "options": "A. 1\nB. 2"})
        self.assertEqual(client.last_source, "shared")
        self.assertEqual(
            [call.kwargs["ai_state"] for call in progress.call_args_list], ["shared_wait", "shared"],
        )

    def test_worker_broker_cannot_send_token_to_remote_address(self):
        with self.assertRaises(WebAIError):
            BrokerClient({
                "webai_broker_endpoint": "https://example.org/internal",
                "webai_broker_token": "offline-only",
            })

    def test_image_polling_reports_all_three_review_stages(self):
        client = BrokerClient({
            "webai_broker_endpoint": "http://127.0.0.1:3100/internal",
            "webai_broker_token": "offline-only",
        })
        self.addCleanup(client.close)
        client._request = Mock(side_effect=[
            {"id": "request-id", "state": "queued"},
            *({"state": "running", "stage": stage}
              for stage in ("recognizing", "solving", "verifying")),
            {"state": "done", "answer": '{"Answer":["B"]}'},
        ])
        with patch("backend.client.time.sleep"), patch("backend.client.report") as progress:
            client.complete(prepared_question())
        self.assertEqual(
            [call.kwargs["ai_state"] for call in progress.call_args_list],
            ["vision_queued", "vision_recognizing", "vision_solving", "vision_verifying", "answered"],
        )
        self.assertTrue(all(call.kwargs["ai_image_count"] == 1 for call in progress.call_args_list))

    def test_image_review_error_preserves_actionable_progress_message(self):
        for code, message in (
            ("AI_VISION_UNREADABLE", "图片细节不清楚，未猜答"),
            ("AI_VISION_READING_MISMATCH", "原图与识别内容不一致，需人工核对"),
            ("AI_VISION_DISAGREEMENT", "两路答案不一致，未提交或写入共享题库"),
        ):
            with self.subTest(code=code):
                client = BrokerClient({
                    "webai_broker_endpoint": "http://127.0.0.1:3100/internal",
                    "webai_broker_token": "offline-only",
                })
                self.addCleanup(client.close)
                client._request = Mock(return_value={
                    "id": "request-id", "state": "error", "error": code,
                })
                with patch("backend.client.report") as progress, self.assertRaisesRegex(WebAIError, message):
                    client.complete(prepared_question())
                self.assertEqual(progress.call_count, 1)
                self.assertEqual(progress.call_args.kwargs["ai_state"], "vision_review")
                self.assertEqual(progress.call_args.kwargs["ai_error"], message)


if __name__ == "__main__":
    unittest.main()
