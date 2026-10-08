"""Dedicated file log for one-click full diagnosis progress.

Writes to backend/logger/full_diagnosis.log by default (the backend runs with CWD = backend).
Records only one-click diagnosis orchestration and key states; never API keys, tokens, raw LLM answers or other sensitive/large fields.
"""
from __future__ import annotations

import json
import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Optional

_LOG = logging.getLogger("geo.full_diagnosis.process")
_LOG.setLevel(logging.INFO)
_LOG.propagate = False

_LOG_FILE: Optional[str] = None
_DEFAULT_LOG_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "logger", "full_diagnosis.log")
)
_SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password", "authorization", "cookie")
_DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def _log_enabled() -> bool:
    raw = os.getenv("FULL_DIAGNOSIS_LOG_ENABLED", "1").strip().lower()
    return raw not in _DISABLED_VALUES


def _safe_key(key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(key or "field")).strip("_") or "field"


def _safe_value(key: str, value: Any, *, limit: int = 300) -> str:
    lower = key.lower()
    if any(part in lower for part in _SENSITIVE_KEY_PARTS):
        return "***"
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        text = f"{text[:limit]}..."
    if not text or re.search(r"\s|[=\"']", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def configure_full_diagnosis_log(log_file: str | None = None,
                                backup_count: int | None = None) -> str:
    """Configure the one-click diagnosis file logger and create its folder.

    FULL_DIAGNOSIS_LOG_FILE can override the default path. The default is
    backend/logger/full_diagnosis.log, a project-local folder dedicated to this
    process log.
    """
    global _LOG_FILE
    if not _log_enabled():
        return ""
    if _LOG_FILE is not None:
        return _LOG_FILE

    path = (log_file or os.getenv("FULL_DIAGNOSIS_LOG_FILE") or _DEFAULT_LOG_FILE).strip()
    backup_days_raw = os.getenv("FULL_DIAGNOSIS_LOG_BACKUP_DAYS", "30").strip()
    try:
        backup_days = int(backup_count if backup_count is not None else backup_days_raw)
    except (TypeError, ValueError):
        backup_days = 30

    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)

    for handler in _LOG.handlers:
        if isinstance(handler, TimedRotatingFileHandler) and handler.baseFilename == abs_path:
            _LOG_FILE = abs_path
            return _LOG_FILE

    handler = TimedRotatingFileHandler(
        abs_path,
        when="midnight",
        interval=1,
        backupCount=backup_days,
        utc=True,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.addHandler(handler)
    _LOG_FILE = abs_path
    return _LOG_FILE


def full_diag_log_event(report_id: int, module: str, stage: str,
                        status: str = "info", level: int = logging.INFO,
                        message: str | None = None, **meta: Any) -> None:
    """Write one concise key=value line to full_diagnosis.log.

    Logging errors are swallowed so observability never blocks report generation.
    """
    try:
        if not _log_enabled():
            return
        configure_full_diagnosis_log()
        fields: list[tuple[str, Any]] = [
            ("report_id", report_id),
            ("module", module),
            ("stage", stage),
            ("status", status),
        ]
        if message:
            fields.append(("message", message))
        fields.extend((k, v) for k, v in meta.items() if v is not None)
        line = " ".join(
            f"{_safe_key(k)}={_safe_value(_safe_key(k), v)}" for k, v in fields
        )
        _LOG.log(level, line)
    except Exception:  # noqa: BLE001
        pass
