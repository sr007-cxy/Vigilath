"""Output layer: structured i18n emit + legacy print shim.

Migrated from /geo_checker.py lines 68–143 (LANG / _ZH / _tr / _pad / print
shim / emit_check) and backend/geo_checker/__main__.py lines 70–125
(emit_check / emit_fix).

Design:
- `emit_check(status, key, message, params)` — canonical check output. When
  GEO_EMIT_STRUCTURED=1 (set by backend before import), a machine-readable
  key marker is appended so the backend's parse_geo_output can attach
  message_key / message_params to CheckResult. CLI users don't set the env
  var and see clean English output.
- `emit_fix(key, message, params)` — the analog for fix recommendations.
  Collapses multi-line messages onto a single line (joined by \\x02) so the
  line-based parser handles it as one row; the backend splits them back out.
- `fix(message)` — legacy helper that routes to emit_fix with no i18n key.
  Kept so un-migrated call sites still work.
- `print(...)` — shadows builtin print; when LANG != "en" it runs each arg
  through _tr() before delegating. CLI users pass --lang zh to trigger.
- `_tr(text)` / `_pad` / `_display_width` — i18n helpers for CLI-mode
  Chinese output. _ZH is populated from /docs (or whatever the upstream ZH
  table source is) at module import; see the legacy root file for the table.
"""

import json
import os as _os
import re as _re
import sys as _sys

from .state import SHOW_FIX as _SHOW_FIX_IMPORT
from . import state as _state
from .constants import FIX

# ---------------------------------------------------------------------------
# i18n  — output-level translation (CLI mode)
# ---------------------------------------------------------------------------
LANG = "en"

# Pre-scan sys.argv so everything downstream sees the right language.
# Only meaningful when invoked as CLI (`geo-checker --lang zh ...`); backend
# never passes --lang.
if "--lang" in _sys.argv:
    _idx = _sys.argv.index("--lang")
    if _idx + 1 < len(_sys.argv) and _sys.argv[_idx + 1] in ("en", "zh"):
        LANG = _sys.argv[_idx + 1]

# _ZH maps English substrings → Chinese. Populated at end of translations
# setup (currently done by a separate module or at package load); entries
# are tried longest-first via _ZH_SORTED to avoid partial matches.
_ZH: dict = {}
_ZH_SORTED: list = []  # [(en, zh)] sorted by len(en) descending; built once


def _tr(text):
    """Translate English substrings in *text* to Chinese using _ZH table."""
    global _ZH_SORTED
    if LANG == "en" or not _ZH:
        return text
    if not _ZH_SORTED:
        _ZH_SORTED = sorted(_ZH.items(), key=lambda kv: len(kv[0]), reverse=True)
    for en, zh in _ZH_SORTED:
        if en in text:
            text = text.replace(en, zh)
    return text


def _display_width(s):
    """Return the number of terminal columns *s* occupies. CJK chars are
    double-width; ANSI escapes are zero-width."""
    import unicodedata
    s = _re.sub(r'\033\[[0-9;]*m', '', s)  # strip ANSI
    w = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ('W', 'F'):
            w += 2
        else:
            w += 1
    return w


def _pad(s, width):
    """Left-align *s* in a field of *width* terminal columns. Translates *s*
    first so padding accounts for CJK display width."""
    s = _tr(s)
    return s + ' ' * max(0, width - _display_width(s))


# Keep a reference to the real print so the shim can delegate.
_builtin_print = print


def print(*args, **kwargs):            # noqa: A001 — intentional shadow
    """Module-level print override. Only translates when LANG != 'en'."""
    if LANG != "en" and args:
        args = tuple(_tr(str(a)) if isinstance(a, str) else a for a in args)
    _builtin_print(*args, **kwargs)


# ---------------------------------------------------------------------------
# Structured-emit layer for backend i18n
# ---------------------------------------------------------------------------
# When the backend imports us it sets GEO_EMIT_STRUCTURED=1 in the env
# BEFORE importing. emit_check / emit_fix then embed a machine-parseable
# marker at the end of each printed line carrying the i18n key + params.
# The backend's parse_geo_output extracts the marker and attaches
# message_key / message_params fields to CheckResult, enabling frontend
# t(key, params) rendering. Standalone CLI users don't set the env var and
# see clean English output, exactly as the original upstream behavior.

_EMIT_STRUCTURED = _os.environ.get("GEO_EMIT_STRUCTURED") == "1"
_KEY_MARKER_START = "\x01GK\x01"
_KEY_MARKER_END = "\x01GE\x01"


def emit_check(status_tag, key, message, params=None):
    """Print a check result line and (optionally) embed i18n metadata.

    status_tag: one of PASS / WARN / FAIL / INFO (the ANSI-wrapped constants)
    key:        i18n key path under result.checks.* on the frontend
    message:    human-readable English fallback (also drives CLI output)
    params:     dict of interpolation values (e.g. {"count": 12})
    """
    line = f"  [{status_tag}] {message}"
    if _EMIT_STRUCTURED and key:
        meta = json.dumps({"k": key, "p": params or {}}, ensure_ascii=False)
        line += f"{_KEY_MARKER_START}{meta}{_KEY_MARKER_END}"
    print(line)


def emit_fix(key, message, params=None):
    """Print a fix recommendation (multi-line) with optional i18n metadata.

    Emits the entire message as a single line (joined with sentinel \\x02
    that the backend parser splits back out) so the structured marker only
    appears once and the parser can assign fix_key / fix_params to the
    preceding check. When SHOW_FIX is off the call is a no-op.
    """
    if not _state.SHOW_FIX:
        return
    one_liner = message.strip().replace("\n", "\x02")
    line = f"  [{FIX}] {one_liner}"
    if _EMIT_STRUCTURED and key:
        meta = json.dumps({"k": key, "p": params or {}}, ensure_ascii=False)
        line += f"{_KEY_MARKER_START}{meta}{_KEY_MARKER_END}"
    print(line)


def fix(message):
    """Legacy fix() helper — routes to emit_fix() with no i18n key so
    un-migrated call sites still work. New code should call emit_fix()
    directly with a stable key under result.fixes.*.
    """
    emit_fix(None, message)
