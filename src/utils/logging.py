"""Logging configuration"""

import logging
import sys
from pathlib import Path
from typing import Optional

_loggers: dict[str, logging.Logger] = {}


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
) -> None:
    """Configure logging for the application"""
    root_logger = logging.getLogger("admission")
    root_logger.setLevel(getattr(logging, level.upper()))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module"""
    if name not in _loggers:
        _loggers[name] = logging.getLogger(f"admission.{name}")
    return _loggers[name]
