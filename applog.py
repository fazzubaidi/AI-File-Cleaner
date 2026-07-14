"""Application logging: every destructive action lands in app.log."""
from __future__ import annotations

import logging

from config import LOG_FILE, ensure_app_dirs

_logger = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        ensure_app_dirs()
        _logger = logging.getLogger("ai_file_cleaner")
        _logger.setLevel(logging.INFO)
        if not _logger.handlers:
            try:
                handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s %(message)s"))
                _logger.addHandler(handler)
            except OSError:
                _logger.addHandler(logging.NullHandler())
    return _logger
