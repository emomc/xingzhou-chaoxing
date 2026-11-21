from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import backend.__main__ as cli
from backend.security import StateInUse, StateLock
from backend.service import Settings


TOKEN = "offline-startup-test-token-not-a-real-secret"
HEALTH = {"version": 1, "status": "ok", "max_workers": 30, "ai_workers": 1}


class ExistingBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(state_dir=Path(self.temp.name), token=TOKEN)
        factory = patch("backend.__main__.http.client.HTTPConnection")
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.connection = self.factory.return_value

    def responses(self, private=None, public=None, status=200):
        public = {"service": "chaoxing-backend", "status": "ok"} if public is None else public
        private = HEALTH if private is None else private
        self.connection.getresponse.side_effect = [
            Mock(status=200, read=Mock(return_value=json.dumps(public).encode())),
            Mock(status=status, read=Mock(return_value=json.dumps(private).encode())),
        ]

    def test_authenticated_existing_service_is_reused_without_echoing_token(self):
        self.responses()
        result = cli.running_backend(self.settings)
        self.assertEqual(result, ("http://127.0.0.1:3100", HEALTH))
        requests = self.connection.request.call_args_list
        self.assertEqual(requests[0].args, ("GET", "/healthz"))
        self.assertEqual(requests[0].kwargs["headers"], {})
        self.assertEqual(requests[1].kwargs["headers"]["Authorization"], "Bearer " + TOKEN)
        self.connection.close.assert_called_once()

    def test_unrelated_service_does_not_receive_the_operator_token(self):
        for public in ({}, {"service": "another-app"}, []):
            with self.subTest(public=public):
                self.connection.reset_mock()
                self.responses(public=public)
                self.assertIsNone(cli.running_backend(self.settings))
                self.assertEqual(self.connection.request.call_count, 1)
                self.assertEqual(self.connection.request.call_args.kwargs["headers"], {})

    def test_wrong_token_or_invalid_health_is_not_success(self):
        for status, health in ((401, {}), (200, []), (200, {"version": 2, "status": "ok"})):
            with self.subTest(status=status, health=health):
                self.responses(private=health, status=status)
                self.assertIsNone(cli.running_backend(self.settings))

    def test_probe_does_not_create_missing_state_or_secret_files(self):
        self.settings.token = None
        self.assertIsNone(cli.running_backend(self.settings))
        self.factory.assert_not_called()
        self.assertEqual(list(self.settings.state_dir.iterdir()), [])
        token_file = self.settings.state_dir / "api-token"
        token_file.write_text(TOKEN + "\n", encoding="ascii")
        self.responses()
        self.assertIsNotNone(cli.running_backend(self.settings))
        self.assertEqual([path.name for path in self.settings.state_dir.iterdir()], ["api-token"])

    def test_probe_never_sends_a_token_to_a_non_loopback_host(self):
        for host in ("example.org", "192.168.1.10", "10.0.0.1"):
            self.settings.host = host
            self.assertIsNone(cli.running_backend(self.settings))
        self.factory.assert_not_called()

    def test_wildcard_and_ipv6_hosts_probe_only_loopback(self):
        for host, expected in (("0.0.0.0", "127.0.0.1"), ("::", "::1"), ("localhost", "127.0.0.1")):
            with self.subTest(host=host):
                self.settings.host = host
                self.responses()
                result = cli.running_backend(self.settings)
                self.assertIsNotNone(result)
                self.factory.assert_called_with(expected, 3100, timeout=2)

    def test_network_error_or_oversized_response_is_not_success(self):
        self.connection.request.side_effect = OSError("offline")
        self.assertIsNone(cli.running_backend(self.settings))
        self.connection.request.side_effect = None
        for raw in (b"invalid-json", b"x" * 16385):
            response = Mock(status=200, read=Mock(return_value=raw))
            self.connection.getresponse.side_effect = [response]
            self.assertIsNone(cli.running_backend(self.settings))
            response.read.assert_called_once_with(16385)

    def test_redirect_does_not_forward_credentials(self):
        self.connection.getresponse.side_effect = [Mock(status=302)]
        self.assertIsNone(cli.running_backend(self.settings))
        self.assertEqual(self.connection.request.call_count, 1)


class StartupLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.lock = StateLock(self.directory)
        self.addCleanup(self.lock.close)

    def run_cli(self, running):
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.argv", [
            "backend", "--state-dir", str(self.directory), "--port", "3100",
        ]), patch("backend.__main__.running_backend", return_value=running), \
                redirect_stdout(output), redirect_stderr(errors):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def test_repeated_start_succeeds_and_retains_the_original_lock(self):
        code, output, errors = self.run_cli(("http://127.0.0.1:3100", HEALTH))
        self.assertEqual(code, 0)
        self.assertIn("Backend is already running", output)
        self.assertNotIn(TOKEN, output + errors)
        self.assertEqual(errors, "")
        self.assertTrue((self.directory / "service.lock").exists())
        self.assertFalse((self.directory / "service.sqlite3").exists())
        with self.assertRaises(StateInUse):
            StateLock(self.directory)

    def test_unverified_lock_owner_gets_a_clear_error_without_deleting_lock(self):
        code, output, errors = self.run_cli(None)
        self.assertEqual(code, 2)
        self.assertIn("Keep service.lock intact", errors)
        self.assertNotIn("Traceback", output + errors)
        self.assertTrue((self.directory / "service.lock").exists())

    def test_unhealthy_existing_service_is_not_reported_as_success(self):
        code, output, errors = self.run_cli((
            "http://127.0.0.1:3100", {**HEALTH, "status": "degraded"},
        ))
        self.assertEqual(code, 2)
        self.assertIn("already running", output)
        self.assertIn("not ready", errors)


if __name__ == "__main__":
    unittest.main()
