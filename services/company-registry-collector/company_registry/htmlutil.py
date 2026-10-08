"""Shared small HTML/text helpers reused by the providers."""
from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script.*?</script>", re.S)
_STYLE_RE = re.compile(r"<style.*?</style>", re.S)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"1[3-9]\d{9}|0\d{2,3}-?\d{7,8}")


def clean_text(s: str) -> str:
    s = _SCRIPT_RE.sub("", s)
    s = _STYLE_RE.sub("", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()
