import logging
import tempfile
import unittest
from pathlib import Path

from telegram_notifications.cli import _configure_logging


class NotificationCliTests(unittest.TestCase):
    def test_configures_an_append_log_file(self) -> None:
        root_logger = logging.getLogger()
        previous_handlers = root_logger.handlers[:]
        with tempfile.TemporaryDirectory() as directory:
            try:
                root_logger.handlers.clear()
                log_path = Path(directory) / "logs" / "notifications.log"
                _configure_logging("INFO", log_path)
                logging.getLogger("telegram_notifications.test").info("test entry")

                self.assertIn("test entry", log_path.read_text(encoding="utf-8"))
            finally:
                for handler in root_logger.handlers:
                    handler.close()
                root_logger.handlers.clear()
                root_logger.handlers.extend(previous_handlers)


if __name__ == "__main__":
    unittest.main()
