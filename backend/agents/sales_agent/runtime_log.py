"""Sales Agent runtime JSONL log.

Written by default to backend/agents/sales_agent/logs/model_calls.jsonl.
Records model calls and SearchPro company search diagnostics; never writes sensitive values like API Key / Authorization.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR_ENV = "SALES_AGENT_LOG_DIR"
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"
_MODEL_CALL_LOG = "model_calls.jsonl"
_SECRET_RE = re.compile(
    r"(bearer\s+)[a-z0-9._\-]+|(api[_-]?key\s*[=:]\s*)[^\s,;]+|(sk-[a-z0-9_\-]{12,})|(tokenhub_[a-z0-9_\-]{12,})",
    re.IGNORECASE,
)


def new_model_call_id() -> str:
    return uuid.uuid4().hex[:12]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def now_monotonic() -> float:
    return time.perf_counter()


def duration_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _log_dir() -> Path:
    configured = os.environ.get(_LOG_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_LOG_DIR


def _redact(value: Any) -> str:
    text = str(value or "")
    return _SECRET_RE.sub(lambda m: f"{m.group(1) or m.group(2) or ''}<redacted>", text)


def _provider(base_url: str, model_name: str, via_openrouter: bool | None) -> str:
    base = (base_url or "").lower()
    model = (model_name or "").lower()
    if via_openrouter:
        return "openrouter"
    if "deepseek" in base or model.startswith("deepseek"):
        return "deepseek"
    if any(k in base for k in ("tencent", "hunyuan", "maas", "tokenhub")) or model.startswith("hy"):
        return "hunyuan"
    return "openai_compatible"


def model_config_fields(cfg: Any | None) -> dict[str, Any]:
    if cfg is None:
        return {}
    model_name = str(getattr(cfg, "model_name", "") or "")
    base_url = str(getattr(cfg, "base_url", "") or "")
    via_openrouter = bool(getattr(cfg, "via_openrouter", False))
    return {
        "provider": _provider(base_url, model_name, via_openrouter),
        "model": model_name,
        "base_url": base_url,
        "via_openrouter": via_openrouter,
    }


def write_sales_agent_log(record: dict[str, Any]) -> None:
    """Append one Sales Agent runtime log entry to model_calls.jsonl."""
    try:
        directory = _log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        data = {"ts": utc_now_iso(), **record}
        if "error" in data:
            data["error"] = _redact(data["error"])
        if "message_preview" in data:
            data["message_preview"] = _redact(data["message_preview"])
        with (directory / _MODEL_CALL_LOG).open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    except Exception:
        return


def _usage_value(usage: Any, key: str) -> Any:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def _usage_dict(usage: Any | None) -> dict[str, Any]:
    """Normalize pydantic-ai / OpenAI-compatible usage, so log fields never affect the main flow in reverse."""
    if usage is None:
        return {}
    if isinstance(usage, dict) and isinstance(usage.get("usage"), dict):
        usage = usage["usage"]
    keys = (
        "requests",
        "request_count",
        "request_tokens",
        "response_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
    )
    data = {key: _usage_value(usage, key) for key in keys if _usage_value(usage, key) is not None}
    details = _usage_value(usage, "details")
    if details is not None:
        data["details"] = details
    if "total_tokens" not in data:
        input_tokens = data.get("input_tokens") if data.get("input_tokens") is not None else data.get("prompt_tokens")
        if input_tokens is None:
            input_tokens = data.get("request_tokens")
        output_tokens = data.get("output_tokens") if data.get("output_tokens") is not None else data.get("completion_tokens")
        if output_tokens is None:
            output_tokens = data.get("response_tokens")
        if input_tokens is not None or output_tokens is not None:
            try:
                data["total_tokens"] = int(input_tokens or 0) + int(output_tokens or 0)
            except Exception:  # noqa: BLE001
                pass
    return data


def write_model_call_log(
    *,
    request_id: str,
    event: str,
    status: str,
    scene: str,
    user_id: int | None = None,
    session_public_id: str = "",
    intent: str = "",
    message: str = "",
    duration: int | None = None,
    model_config: dict[str, Any] | None = None,
    usage: Any | None = None,
    error: BaseException | str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one Sales Agent model call log entry.

    Failures don't affect the main flow; the log keeps only message lengths and short previews, never secrets.
    """
    record: dict[str, Any] = {
        "request_id": request_id,
        "event": event,
        "status": status,
        "scene": scene,
        "user_id": user_id,
        "session_id": session_public_id,
        "intent": intent,
        "message_len": len(message or ""),
    }
    if message:
        record["message_preview"] = message[:160]
    if duration is not None:
        record["duration_ms"] = duration
    if model_config:
        record.update(model_config)
    usage_data = _usage_dict(usage)
    if usage_data:
        record["usage"] = usage_data
    if error is not None:
        record["error_type"] = type(error).__name__ if isinstance(error, BaseException) else "Error"
        record["error"] = str(error)
    if extra:
        record.update(extra)
    write_sales_agent_log(record)
