"""Env loading for crontab entry points — `python -m geo.services.xxx` does not read .env automatically.

Origin of the pitfall: `load_dotenv()` is only called in `geo/main.py` (the FastAPI app), and the systemd unit brings its own
EnvironmentFile, so **only the HTTP path** has env. When crontab runs a service module directly via `python -m`,
the environment is clean — DEEPSEEK_API_KEY and the like are all missing.

This causes silent wrong behavior rather than an obvious crash. The worst case: gen_retry run from cron has no
key, `regenerate_doc` raises "none configured", and that error is classified as no_key = terminal, no retry →
**the entire retry queue is quietly cleared**, looking like "the retry ran" while not a single article was saved.

So every crontab entry point's `__main__` must call `load_env_for_cron()` first.

Also, .env has unquoted values (FROM_EMAIL=GEO Readiness Checker <noreply@…>),
and shell `set -a; . .env` hits a syntax error — which is also why it must be loaded in python rather than
sourced in the cron line.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# geo/cron_env.py → parents[1] = backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]


def load_env_for_cron(*, required: tuple[str, ...] = ()) -> bool:
    """Load backend/.env (without overriding real environment variables already set).

    Returns False when none of the keys in required were obtained — the caller uses it to decide to "skip this round" rather than
    proceed with half-broken config (see the gen_retry example above).
    """
    env_path = BACKEND_DIR / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        # Parse manually when python-dotenv is not installed: KEY=VALUE, no processing of values beyond stripping quotes
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:  # noqa: BLE001
        log.warning("[cron-env] failed to load %s", env_path, exc_info=True)

    if required and not any(os.environ.get(k, "").strip() for k in required):
        log.error("[cron-env] none of %s configured (looked in %s) — this round should be skipped, "
                  "otherwise a missing key will be recorded as a business failure", " / ".join(required), env_path)
        return False
    return True
