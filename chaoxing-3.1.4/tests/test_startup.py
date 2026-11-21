import unittest
from unittest.mock import Mock, patch

import main


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.factory = Mock()
        self.factory.get_tiku_from_config.return_value = self.provider
        self.account = Mock()
        self.chaoxing = Mock()
        self.input = Mock(return_value="test-account")
        self.password = Mock(return_value="test-only-password")
        for name, replacement in (
            ("Tiku", Mock(return_value=self.factory)),
            ("Account", self.account),
            ("Chaoxing", self.chaoxing),
            ("input", self.input),
            ("getpass", self.password),
        ):
            patcher = patch.object(main, name, replacement, create=name == "input")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_interactive_password_is_not_echoed(self):
        config = {"provider": "WebAI", "delay": 1}
        main.init_chaoxing({}, config)
        self.input.assert_called_once()
        self.password.assert_called_once()
        self.account.assert_called_once_with("test-account", "test-only-password")
        self.factory.config_set.assert_called_once_with(config)
        self.provider.init_tiku.assert_called_once()
        self.chaoxing.assert_called_once_with(
            account=self.account.return_value, tiku=self.provider, query_delay=1
        )

    def test_configured_credentials_do_not_prompt(self):
        main.init_chaoxing({"username": "configured", "password": "test-secret"}, {})
        self.input.assert_not_called()
        self.password.assert_not_called()
        self.account.assert_called_once_with("configured", "test-secret")

    def test_cookie_login_does_not_prompt(self):
        main.init_chaoxing({"use_cookies": True}, {})
        self.input.assert_not_called()
        self.password.assert_not_called()
        self.account.assert_called_once_with("", "")

    def test_video_only_mode_does_not_initialize_answer_provider(self):
        main.init_chaoxing({"use_cookies": True, "videos_only": True}, {"provider": "WebAI"})
        self.factory.get_tiku_from_config.assert_not_called()
        self.factory.config_set.assert_not_called()
        self.provider.init_tiku.assert_not_called()
        self.assertTrue(self.factory.DISABLE)
        self.chaoxing.assert_called_once_with(
            account=self.account.return_value, tiku=self.factory, query_delay=0
        )


if __name__ == "__main__":
    unittest.main()
