import unittest
from pathlib import Path
from unittest.mock import patch

from tg_setup.bot_setup import (
    _configured_targets,
    _read_token,
    _updated_env,
)
from tg_setup.errors import SetupError


class NotificationBotSetupTests(unittest.TestCase):
    def test_token_prompt_explains_botfather_creation(self) -> None:
        with (
            patch(
                "tg_setup.bot_setup._configured_token",
                return_value=None,
            ),
            patch(
                "tg_setup.bot_setup.getpass.getpass",
                return_value="secret-token",
            ) as prompt,
        ):
            token = _read_token(Path(".env"))

        self.assertEqual(token, "secret-token")
        prompt_text = prompt.call_args.args[0]
        self.assertIn("@BotFather", prompt_text)
        self.assertIn("/newbot", prompt_text)
        self.assertIn("input is hidden", prompt_text)

    def test_updates_notification_values_without_changing_other_settings(self) -> None:
        original = (
            "TELEGRAM_API_ID=123\n"
            "TELEGRAM_NOTIFICATION_BOT_TOKEN=old-token\n"
            "LOG_LEVEL=INFO\n"
        )

        updated = _updated_env(original, {"TELEGRAM_NOTIFICATION_BOT_TOKEN": "new-token"})

        self.assertIn("TELEGRAM_API_ID=123\n", updated)
        self.assertIn("LOG_LEVEL=INFO\n", updated)
        self.assertIn("TELEGRAM_NOTIFICATION_BOT_TOKEN=new-token\n", updated)
        self.assertNotIn("old-token", updated)
        self.assertEqual(updated.count("TELEGRAM_NOTIFICATION_BOT_TOKEN="), 1)

    def test_accepts_a_general_notification_target_without_forwarder_target(self) -> None:
        with patch(
            "tg_setup.bot_setup.dotenv_values",
            return_value={"TELEGRAM_GENERAL_NOTIFICATION_CHAT": "@notifications"},
        ):
            targets = _configured_targets(Path(".env"))

        self.assertEqual(
            targets,
            (("TELEGRAM_GENERAL_NOTIFICATION_CHAT", "@notifications"),),
        )

    def test_requires_at_least_one_target(self) -> None:
        with (
            patch("tg_setup.bot_setup.dotenv_values", return_value={}),
            self.assertRaisesRegex(SetupError, "At least one"),
        ):
            _configured_targets(Path(".env"))


if __name__ == "__main__":
    unittest.main()
