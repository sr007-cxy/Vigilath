"""Engine selector/parameter configuration (externalized) - browser-agent self-healing Phase 1.

Each engine's input/send/answer selectors, completion API and home URL are taken from here instead of being hard-coded,
so when an engine redesigns, only config changes (overridable via ENGINE_SELECTORS_FILE, hot-reloaded at runtime), without code changes
and redeploying 5 machines; it is also the write target of Phase 2 (LLM self-healing).

Read order: built-in defaults <- ENGINE_SELECTORS_FILE (JSON, merged per engine).
"""
from __future__ import annotations

import json
import os

_DEFAULTS: dict = {
    "qwen": {
        "url": "https://www.qianwen.com/",
        "input_sels": [
            "div[contenteditable='true'][role='textbox']",
            "div[contenteditable='true']",
            "textarea",
        ],
        "send_sels": [
            "button[aria-label*='发送']",
            "button[aria-label*='Send']",
            "button[class*='send']",
        ],
        "answer_sels": [
            "[data-chat-answers-wrap]",
            ".chat-answers-card-wrap",
            ".answer-common-card",
            "[class*='answer']",
            "[class*='markdown']",
        ],
        # completion SSE API carrying the web citations (substring match on response.url)
        "chat_api": "chat2.qianwen.com/api/v2/chat",
    },
}

_OVERRIDE_PATH = os.environ.get("ENGINE_SELECTORS_FILE", "").strip()
# Central config (backend service): self-healing apply writes to the backend, and each worker pulls from this URL -> redesign fixes automatically cover the whole fleet.
_CENTRAL_URL = os.environ.get("ENGINE_SELECTORS_URL", "").strip()


def _fetch_central() -> dict:
    if not _CENTRAL_URL:
        return {}
    try:
        import urllib.request
        with urllib.request.urlopen(_CENTRAL_URL, timeout=5) as r:
            return json.loads(r.read().decode("utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def get(engine: str) -> dict:
    """Get an engine's selector config: built-in defaults <- local override file <- central config (backend, written by self-healing)."""
    cfg = {k: (list(v) if isinstance(v, list) else v)
           for k, v in _DEFAULTS.get(engine, {}).items()}
    if _OVERRIDE_PATH and os.path.exists(_OVERRIDE_PATH):
        try:
            with open(_OVERRIDE_PATH, encoding="utf-8") as f:
                ov = json.load(f)
            if isinstance(ov.get(engine), dict):
                cfg.update(ov[engine])
        except Exception:  # noqa: BLE001
            pass
    central = _fetch_central()
    if isinstance(central.get(engine), dict):
        cfg.update(central[engine])
    return cfg
