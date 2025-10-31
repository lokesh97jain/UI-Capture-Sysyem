# src/utils/logger.py
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

from rich.console import Console
from rich.logging import RichHandler

from src.utils.config import get_settings, LogLevel


__all__ = [
    "get_logger",
    "set_log_level",
    "bind",
    "unbind",
    "log_with_context",
    "attach_file_logger",
    "detach_file_logger",
]


# ------------- Internal state -------------

_config_lock = threading.Lock()
_configured = False
_global_extra: Dict[str, Any] = {}  # optional global context attached to every record


# ------------- JSON Formatter (for file logs) -------------

class JsonFormatter(logging.Formatter):
    """
    Minimal JSON formatter.
    Keeps message as `msg` (string) and merges record.extra if present.
    """

    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"

    def format(self, record: logging.LogRecord) -> str:
        # Base structure
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(self.default_time_format),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Add exception info if any
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        # Merge context (added via LoggerAdapter / extra)
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            payload.update(record.extra)

        # Add thread/process info (useful in parallel runs)
        payload["thread"] = record.threadName
        payload["process"] = record.process

        return json.dumps(payload, ensure_ascii=False)


# ------------- Helpers -------------

_LEVEL_MAP = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}


def _ensure_configured() -> None:
    """
    Configure root logging once based on settings.
    Subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return

    with _config_lock:
        if _configured:
            return

        settings = get_settings()
        level = _LEVEL_MAP.get(settings.LOG_LEVEL, logging.INFO)

        # Always start from a clean slate (avoid duplicate handlers in notebooks / re-runs)
        root = logging.getLogger()
        root.setLevel(level)
        for h in list(root.handlers):
            root.removeHandler(h)

        # Console handler (Rich)
        console = Console(stderr=True, force_jupyter=False, color_system="auto")
        rich_handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
            markup=settings.COLORIZED_OUTPUT,
            omit_repeated_times=False,
        )
        # Keep console formatting readable
        rich_fmt = logging.Formatter("%(message)s")
        rich_handler.setFormatter(rich_fmt)
        rich_handler.setLevel(level)
        root.addHandler(rich_handler)

        # Optional rotating file handler (JSON)
        if settings.LOG_TO_FILE:
            settings.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                filename=str(settings.LOG_FILE),
                maxBytes=5 * 1024 * 1024,  # 5MB per file
                backupCount=5,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(JsonFormatter())
            root.addHandler(file_handler)

        # Reduce noise from third-party modules unless debugging
        noisy = ["asyncio", "urllib3", "httpx", "playwright"]
        for n in noisy:
            logging.getLogger(n).setLevel(max(level, logging.WARNING))

        _configured = True


def get_logger(name: Optional[str] = None) -> logging.LoggerAdapter:
    """
    Get a configured logger wrapped with a LoggerAdapter
    that injects `_global_extra` into every log record.
    """
    _ensure_configured()
    base = logging.getLogger(name if name else "ui-capture")
    return logging.LoggerAdapter(base, extra={"extra": _global_extra})


def set_log_level(level: LogLevel | str) -> None:
    """
    Dynamically adjust log level at runtime.
    """
    _ensure_configured()
    lvl = level if isinstance(level, str) else level.value
    py_level = getattr(logging, lvl.upper(), logging.INFO)
    logging.getLogger().setLevel(py_level)
    for h in logging.getLogger().handlers:
        h.setLevel(py_level)


def bind(**kwargs: Any) -> None:
    """
    Bind global context (e.g., run_id="2025-10-26_12-00-00", site_key="linear.app").
    Will be attached to every subsequent log line (file JSON + console).
    """
    _global_extra.update(kwargs)


def unbind(*keys: str) -> None:
    """
    Remove keys from global context.
    """
    for k in keys:
        _global_extra.pop(k, None)


def log_with_context(logger: logging.LoggerAdapter, **kwargs: Any):
    """
    Return a new LoggerAdapter that merges additional context for a scoped section.
    Usage:
        log = get_logger(__name__)
        with_user = log_with_context(log, user="alice")
        with_user.info("doing stuff")
    """
    merged = dict(_global_extra)
    merged.update(kwargs)
    return logging.LoggerAdapter(logger.logger, extra={"extra": merged})


# ------------- Dynamic per-run file logging -------------

def attach_file_logger(path: os.PathLike | str, level: Optional[int] = None) -> logging.Handler:
    """
    Attach a JSON file handler at runtime (e.g., per-run file under the run directory).
    Returns the handler so the caller can later detach it via detach_file_logger.
    """
    _ensure_configured()
    root = logging.getLogger()
    lvl = level if level is not None else root.level
    p = os.fspath(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fh = RotatingFileHandler(filename=p, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8", delay=True)
    fh.setLevel(lvl)
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    return fh


def detach_file_logger(handler: logging.Handler) -> None:
    """Remove a previously attached handler returned by attach_file_logger."""
    try:
        root = logging.getLogger()
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    except Exception:
        pass


# ------------- Example (manual test) -------------

if __name__ == "__main__":
    # Minimal self-check when run directly
    from src.utils.config import get_settings

    s = get_settings()
    bind(run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), mode="selftest")

    log = get_logger(__name__)
    log.debug("Debug example (might be hidden if LOG_LEVEL > DEBUG)")
    log.info("Logger initialized")
    try:
        1 / 0
    except ZeroDivisionError:
        log.exception("Example exception with traceback")
    unbind("mode")
    log.info("Done")
