"""
Application logger — async, colored, file + console.

Architecture
------------
All loggers in the application route records through a single Queue-based
handler. A background QueueListener thread drains the queue and dispatches
records to two real handlers:

    logger.info(...)
        → QueueHandler  (non-blocking, puts record in queue)
            → QueueListener (background thread)
                ├─ FileHandler (INFO+)    → logs/app.log
                └─ StreamHandler (DEBUG+) → stdout with ANSI colors

This makes every log call in a request handler return immediately — the
actual I/O happens in the background thread.

Usage
-----
    # In main.py lifespan:
    app_logger = AppLogger(LOGGING_CONFIG_PATH)
    app_logger.setup()
    ...
    app_logger.shutdown()   # flushes queue on shutdown

    # In every module (drop-in for logging.getLogger):
    from app.logger import get_logger
    logger = get_logger(__name__)
    logger.debug("hello")   # cyan on console, not written to file
    logger.info("hello")    # green on console, written to file
    logger.warning("hello") # yellow
    logger.error("hello")   # red
    logger.critical("hello")# purple
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# ANSI color codes
# ---------------------------------------------------------------------------

_ANSI: dict[str, str] = {
    "cyan":    "\033[96m",
    "green":   "\033[92m",
    "yellow":  "\033[93m",
    "red":     "\033[91m",
    "purple":  "\033[95m",
    "reset":   "\033[0m",
}

# Map log level names to ANSI color strings.
# Defaults; overridden by logging_config.yaml at runtime.
_DEFAULT_LEVEL_COLORS: dict[str, str] = {
    "DEBUG":    _ANSI["cyan"],
    "INFO":     _ANSI["green"],
    "WARNING":  _ANSI["yellow"],
    "ERROR":    _ANSI["red"],
    "CRITICAL": _ANSI["purple"],
}


# ---------------------------------------------------------------------------
# Colored formatter
# ---------------------------------------------------------------------------


class ColorFormatter(logging.Formatter):
    """
    Formatter that wraps the entire log line in an ANSI color code based on
    the record's level name. Falls back to no color on non-TTY streams.
    """

    def __init__(self, fmt: str, level_colors: dict[str, str]) -> None:
        super().__init__(fmt=fmt)
        self._level_colors = level_colors

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        color = self._level_colors.get(record.levelname, "")
        if color and sys.stdout.isatty():
            return f"{color}{line}{_ANSI['reset']}"
        return line


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class LoggingConfig:
    log_file: str = "logs/app.log"
    console_level: str = "DEBUG"
    file_level: str = "INFO"
    format: str = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    colors: dict[str, str] = field(
        default_factory=lambda: {
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "purple",
        }
    )


def _load_logging_config(path: Path) -> LoggingConfig:
    if not path.exists():
        return LoggingConfig()
    with path.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return LoggingConfig(**{k: v for k, v in raw.items() if k in LoggingConfig.__dataclass_fields__})


# ---------------------------------------------------------------------------
# AppLogger
# ---------------------------------------------------------------------------


class AppLogger:
    """
    Wrapper that configures the full async logging stack from logging_config.yaml.

    Call setup() once at application startup (before any module-level loggers
    are used), and shutdown() at application teardown.
    """

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._listener: logging.handlers.QueueListener | None = None
        self._queue: queue.Queue = queue.Queue()

    def setup(self) -> None:
        """
        Configure the root logger with async QueueHandler + background listener.

        After this call, every `get_logger(name).xxx()` call routes through
        the queue asynchronously.
        """
        cfg = _load_logging_config(self._config_path)

        # Resolve ANSI color strings from the human-readable color names.
        level_colors: dict[str, str] = {
            level: _ANSI.get(color_name, "")
            for level, color_name in cfg.colors.items()
        }

        # --- File handler ---
        log_path = Path(cfg.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(cfg.file_level.upper())
        file_handler.setFormatter(logging.Formatter(cfg.format))

        # --- Console handler (stdout, colored) ---
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(cfg.console_level.upper())
        console_handler.setFormatter(ColorFormatter(cfg.format, level_colors))

        # --- Queue listener (background thread) ---
        self._listener = logging.handlers.QueueListener(
            self._queue,
            file_handler,
            console_handler,
            respect_handler_level=True,
        )
        self._listener.start()

        # --- Root logger → QueueHandler ---
        queue_handler = logging.handlers.QueueHandler(self._queue)
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(queue_handler)
        root.setLevel(logging.DEBUG)  # pass everything to the queue; handlers filter

        # Silence overly chatty third-party loggers at WARNING to keep output clean.
        for noisy in ("httpx", "httpcore", "googleapiclient", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    def shutdown(self) -> None:
        """Stop the background listener, flushing any remaining queued records."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger. Drop-in replacement for logging.getLogger().

    The returned logger automatically routes through the async queue once
    AppLogger.setup() has been called.
    """
    return logging.getLogger(name)
