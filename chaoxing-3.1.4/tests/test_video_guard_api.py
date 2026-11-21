# -*- coding: utf-8 -*-
"""看护设置的后端接口：读取、保存、范围校验与健康检查字段。"""

import tempfile
import unittest
from pathlib import Path

from backend.service import Service, Settings, create_app


class FakeAI:
    def complete(self, question):
        return '{"Answer":["B"]}'

    def close(self):
        pass


class VideoGuardApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = Service(Settings(state_dir=Path(self.directory.name)), client_factory=FakeAI)
        self.addCleanup(self.service.close)
        self.client = create_app(self.service).test_client()
        self.headers = {"Authorization": "Bearer " + self.service.secrets.token}

    def test_defaults_are_readable(self):
        body = self.client.get("/v1/video/guard", headers=self.headers).json
        self.assertEqual(body["max_speed"], 0.95)
        self.assertTrue(body["quiet_hours"])
        self.assertFalse(body["auto_pause"])
        self.assertEqual(body["recheck"], 8)
        self.assertEqual(body["rollbacks"]["count"], 0)

    def test_saving_round_trips_and_reaches_health(self):
        response = self.client.put("/v1/video/guard", headers=self.headers, json={
            "max_speed": 1.2, "quiet_hours": False, "auto_pause": True, "recheck": 5,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["max_speed"], 1.2)
        self.assertFalse(response.json["quiet_hours"])
        self.assertTrue(response.json["auto_pause"])
        self.assertEqual(response.json["recheck"], 5)
        health = self.client.get("/v1/health", headers=self.headers).json
        self.assertEqual(health["video_guard"], {
            "max_speed": 1.2, "quiet_hours": False, "auto_pause": True, "recheck": 5,
        })
        self.assertEqual(health["video_rollback_count"], 0)

    def test_out_of_range_values_are_rejected(self):
        for payload in (
            {"max_speed": 2}, {"max_speed": "1.5"}, {"recheck": 0}, {"recheck": 99},
            {"quiet_hours": "1"}, {"unknown": True}, {},
        ):
            with self.subTest(payload=payload):
                response = self.client.put("/v1/video/guard", headers=self.headers, json=payload)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.get("/v1/video/guard", headers=self.headers).json["max_speed"], 0.95,
        )

    def test_rollback_events_surface_in_the_guard_read(self):
        account = self.service.store.save_account("Offline", "offline-user", "offline-pass")
        job, _ = self.service.store.create_job(account["id"], {
            "course_ids": ["101"], "videos_only": False, "submit": False, "preview": True,
        })
        self.service.store.set_state(job["id"], "running")
        self.service.store.event(job["id"], "video_rollback", {
            "course": "军事理论", "name": "1.2 中国国防的历史",
            "rollback_from": 620, "rollback_to": 0, "rollback_dropped": 620,
            "rollback_count": 1, "rollback_action": "slow",
        })
        body = self.client.get("/v1/video/guard", headers=self.headers).json
        self.assertEqual(body["rollbacks"]["count"], 1)
        item = body["rollbacks"]["items"][0]
        self.assertEqual(item["job_id"], job["id"])
        self.assertEqual(item["from_position"], 620)
        self.assertEqual(item["to_position"], 0)
        self.assertEqual(item["dropped"], 620)
        self.assertEqual(item["action"], "slow")
        self.assertEqual(item["course"], "军事理论")
        health = self.client.get("/v1/health", headers=self.headers).json
        self.assertEqual(health["video_rollback_count"], 1)

    def test_rollback_records_survive_event_pruning(self):
        """普通事件只留 500 条，但回退记录不能被顺手删掉——否则面板永远显示 0 次。"""
        account = self.service.store.save_account("Offline", "offline-user", "offline-pass")
        job, _ = self.service.store.create_job(account["id"], {
            "course_ids": ["101"], "videos_only": False, "submit": False, "preview": True,
        })
        self.service.store.set_state(job["id"], "running")
        self.service.store.event(job["id"], "video_rollback", {
            "course": "军事理论", "rollback_from": 430, "rollback_to": 368,
            "rollback_dropped": 62, "rollback_count": 1, "rollback_action": "slow",
        })
        for step in range(600):
            self.service.store.event(job["id"], "video", {"phase": "video", "position": step})
        body = self.client.get("/v1/video/guard", headers=self.headers).json
        self.assertEqual(body["rollbacks"]["count"], 1)
        self.assertEqual(body["rollbacks"]["items"][0]["dropped"], 62)

    def test_the_endpoint_requires_the_service_token(self):
        self.assertEqual(self.client.get("/v1/video/guard").status_code, 401)


if __name__ == "__main__":
    unittest.main()
