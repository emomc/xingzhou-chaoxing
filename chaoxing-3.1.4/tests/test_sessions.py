from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests

from api.base import SessionManager
from api.cookies import save_cookies, use_cookies


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.local = patch.object(SessionManager, "_local", threading.local())
        self.cookies = patch.object(SessionManager, "_cookies", None)
        self.version = patch.object(SessionManager, "_cookie_version", 0)
        self.local.start()
        self.cookies.start()
        self.version.start()
        self.loader = patch("api.base.use_cookies", return_value={"test": "cookie"})
        self.load = self.loader.start()
        self.addCleanup(self.local.stop)
        self.addCleanup(self.cookies.stop)
        self.addCleanup(self.version.stop)
        self.addCleanup(self.loader.stop)
        self.addCleanup(SessionManager.close_current)

    def test_same_thread_reuses_connection_pool_and_loads_cookies_once(self):
        session = SessionManager.get_session()
        self.assertIs(session, SessionManager.get_session())
        self.assertEqual(session.cookies["test"], "cookie")
        self.load.assert_called_once()

    def test_different_threads_do_not_share_requests_session(self):
        original = SessionManager.get_session()
        sessions = []

        def run():
            sessions.append(SessionManager.get_session())
            SessionManager.close_current()

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        self.assertIsNot(original, sessions[0])

    def test_refresh_updates_existing_session_without_recreating_it(self):
        session = SessionManager.get_session()
        self.load.return_value = {"test": "refreshed"}
        SessionManager.update_cookies()
        self.assertIs(session, SessionManager.get_session())
        self.assertEqual(session.cookies["test"], "refreshed")

    def test_default_timeout_is_bounded_and_post_responses_are_not_retried(self):
        session = SessionManager.get_session()
        self.assertEqual(session.request.keywords["timeout"], (5, 20))
        retry = session.get_adapter("https://").max_retries
        self.assertEqual(retry.read, 0)
        self.assertEqual(retry.status, 0)
        self.assertNotIn("POST", retry.allowed_methods)


class CookieTests(unittest.TestCase):
    def test_cookie_values_with_equals_survive_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.txt"
            with patch("api.cookies.gc.COOKIES_PATH", str(path)), requests.Session() as session:
                session.cookies.set("token", "abc==")
                session.cookies.set("uid", "offline")
                save_cookies(session)
                self.assertEqual(use_cookies(), {"token": "abc==", "uid": "offline"})
                session.cookies.set("token", "updated=value=")
                save_cookies(session)
                self.assertEqual(use_cookies()["token"], "updated=value=")
                self.assertEqual([item.name for item in path.parent.iterdir()], ["cookies.txt"])


if __name__ == "__main__":
    unittest.main()
