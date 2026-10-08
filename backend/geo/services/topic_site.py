"""Where a topic's "official website" actually lives -- the single criterion.

The column name `ai_telemetry_topics.target` looks like the official website; **it isn't**. It's "the search term being audited",
backfilled at topic creation from `profile.company_short_name` (the "company / brand short name" on the form) --
see the lines `t.target = payload.company_short_name` in `api/ai_telemetry.py`.

The real official website lives in only one place: `profile_json.website` (`BrandProfile.website`, form label
"brand official website (optional)").

The two were mixed up before, and the consequences went beyond displaying one wrong field:
  - the agent's `get_topic` / `list_topics` put the "official website" label on target,
    so the "official website" users saw was their own brand short name;
  - `run_full_diagnosis` passed `topic.target` as the URL to the four site-side checks,
    so website health check / answer engine optimization / crawler reachability / off-site authority all ran with a string that isn't a URL
    -- the report still came out, but those four sections were garbage;
  - `create_topic(url=...)` conversely wrote the URL into target, turning the search term into a URL,
    and every batch run asked AI "how is https://xxx.com".

So reading and writing the official website **goes only through this module**.
"""
from __future__ import annotations

import json
import re

# Allowed bare-domain forms: a.com / a.b.cn / xn--fiq.中国 also allowed (final say left to the backend's validate_url)
_BARE_DOMAIN = re.compile(r"^[\w一-龥-]+(\.[\w一-龥-]+)+(/.*)?$")


def looks_like_url(value: str) -> bool:
    """Whether it looks like a URL. Used for the fallback check "old data where target happens to store a URL"."""
    v = (value or "").strip()
    if not v or " " in v:
        return False
    if v.lower().startswith(("http://", "https://")):
        return True
    return bool(_BARE_DOMAIN.match(v))


def normalize_url(value: str) -> str:
    """Add a scheme + strip surrounding whitespace. An empty string still returns empty (no official website is a valid state)."""
    v = (value or "").strip().strip("，,;；")
    if not v:
        return ""
    if not v.lower().startswith(("http://", "https://")):
        v = "https://" + v
    return v


def _profile(topic) -> dict:
    try:
        d = json.loads(getattr(topic, "profile_json", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def topic_website(topic) -> str:
    """This topic's official website. Returns an empty string if none -- **empty is valid**; don't substitute target.

    There's only one fallback: old data where target really does store a URL (create_topic once wrote url into
    target). In that case use it as the URL; otherwise always return empty.
    """
    if topic is None:
        return ""
    site = str(_profile(topic).get("website") or "").strip()
    if site:
        return normalize_url(site)
    legacy = str(getattr(topic, "target", "") or "").strip()
    return normalize_url(legacy) if looks_like_url(legacy) else ""


def topic_brand_term(topic) -> str:
    """This topic's **search term / brand short name** (the true semantics of target)."""
    if topic is None:
        return ""
    term = str(getattr(topic, "target", "") or "").strip()
    if term and looks_like_url(term):
        # Old data wrote a URL into target -- then it's not a search term; fall back to the topic name
        return str(getattr(topic, "name", "") or "").strip()
    return term or str(getattr(topic, "name", "") or "").strip()


def set_topic_website(topic, url: str) -> str:
    """Write the official website into profile_json.website (preserving other fields, including the admin section).

    **Does not commit**; follows the caller's transaction. Returns the normalized URL.
    """
    site = normalize_url(url)
    prof = _profile(topic)
    prof["website"] = site
    topic.profile_json = json.dumps(prof, ensure_ascii=False)
    return site
