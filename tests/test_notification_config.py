import os
import unittest
from pathlib import Path
from unittest.mock import patch

from tg_notification.config import NotificationConfig


class NotificationConfigTests(unittest.TestCase):
    def test_explicit_app_server_executable_does_not_require_path(self) -> None:
        environment = {
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "token",
            "TELEGRAM_GENERAL_NOTIFICATION_CHAT": "-100123",
            "CODEX_APP_SERVER_EXECUTABLE": r"D:\Tools\Codex\codex.exe",
            "CODEX_NOTIFICATION_STATE": r"D:\runtime\state.json",
            "TELEGRAM_NOTIFICATION_LOG": r"D:\runtime\notifications.log",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch(
                "tg_notification.config.find_dotenv", return_value=""
            ), patch("tg_notification.config.load_dotenv"):
                config = NotificationConfig.from_env()

        self.assertEqual(
            config.codex_app_server_command,
            (r"D:\Tools\Codex\codex.exe", "app-server"),
        )
        self.assertEqual(config.target_chat, -100123)
        self.assertEqual(config.state_path, Path(r"D:\runtime\state.json"))
        self.assertEqual(config.log_path, Path(r"D:\runtime\notifications.log"))

    def test_command_setting_is_used_when_no_executable_is_configured(self) -> None:
        environment = {
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "token",
            "TELEGRAM_GENERAL_NOTIFICATION_CHAT": "general-alerts",
            "CODEX_APP_SERVER_COMMAND": "codex app-server",
            "CODEX_NOTIFICATION_STATE": r"D:\runtime\state.json",
            "TELEGRAM_NOTIFICATION_LOG": r"D:\runtime\notifications.log",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch(
                "tg_notification.config.find_dotenv", return_value=""
            ), patch("tg_notification.config.load_dotenv"):
                config = NotificationConfig.from_env()

        self.assertEqual(config.codex_app_server_command, ("codex", "app-server"))


if __name__ == "__main__":
    unittest.main()
