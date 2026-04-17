"""Top-level runner: CHECK_REGISTRY + generate_score + run_silent.

Migrated from backend/geo_checker/__main__.py lines 3143-3305 (CHECK_REGISTRY,
_run_checks, generate_score, run_silent). The C-sourced version carries
`allowed_categories` support that A (root) lacks; the backend
services/geo_checker.py already depends on this signature.

CHECK_REGISTRY is the single source of truth for:
- Which checks run and in what order
- The `Category` string that check_* functions print in their `--- Category ---`
  header (backend's parse_geo_output keys on this)
- The `allowed_check_categories` values in the DB tier table
"""

import io as _io
import sys

from urllib.parse import urlparse

from . import state as _state
from .checks import (
    check_https, check_robots_txt, check_llms_txt, check_well_known,
    check_sitemap, check_search_engine_registration, check_structured_data,
    check_meta_tags, check_content_accessibility, check_ai_crawl_readiness,
    check_content_quality, check_technical_crawlability, check_authority_trust,
    check_brand_entity_kg, check_trust_safety, check_ai_optimization,
    check_social_signals, check_ai_answer_formats, check_schema_knowledge,
    check_mobile_and_weight, check_url_normalization, check_outbound_and_media,
    check_multilingual_depth, check_cross_platform, check_multi_page,
)
from .output import print
from .state import (
    _scores, reset_state, get_ai_visibility_score, get_grade,
)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
# Ordered (section_header_label, check_callable) for every check.
# The label strings MUST match the `--- X ---` section headers printed by the
# corresponding check_* function, because the backend parser derives
# `checks[].category` from those headers. This means filter labels, response
# labels, and the DB `allowed_check_categories` column all share the same
# vocabulary — no translation layer needed.
CHECK_REGISTRY = [
    ("HTTPS",                                          lambda url, sitemap_urls: check_https(url)),
    ("robots.txt",                                     lambda url, sitemap_urls: check_robots_txt(url)),
    ("llms.txt",                                       lambda url, sitemap_urls: check_llms_txt(url)),
    (".well-known Discovery",                          lambda url, sitemap_urls: check_well_known(url)),
    ("sitemap.xml",                                    "__sitemap__"),  # sentinel: check_sitemap, captures return
    ("Search Engine & AI Platform Registration",       lambda url, sitemap_urls: check_search_engine_registration(url)),
    ("Structured Data",                                lambda url, sitemap_urls: check_structured_data(url)),
    ("Meta Tags",                                      lambda url, sitemap_urls: check_meta_tags(url)),
    ("Content Accessibility",                          lambda url, sitemap_urls: check_content_accessibility(url)),
    ("AI Crawl Readiness",                             lambda url, sitemap_urls: check_ai_crawl_readiness(url)),
    ("Content Quality for AI",                         lambda url, sitemap_urls: check_content_quality(url)),
    ("Technical Crawlability",                         lambda url, sitemap_urls: check_technical_crawlability(url)),
    ("Authority & Trust Signals",                      lambda url, sitemap_urls: check_authority_trust(url)),
    ("Brand Entity in Knowledge Graphs",               lambda url, sitemap_urls: check_brand_entity_kg(url)),
    ("Trust & Safety Signals",                         lambda url, sitemap_urls: check_trust_safety(url)),
    ("AI-Specific Optimization",                       lambda url, sitemap_urls: check_ai_optimization(url)),
    ("Social Signals",                                 lambda url, sitemap_urls: check_social_signals(url)),
    ("AI Answer Format Optimization",                  lambda url, sitemap_urls: check_ai_answer_formats(url)),
    ("Schema Breadcrumbs & Knowledge Panel",           lambda url, sitemap_urls: check_schema_knowledge(url)),
    ("Mobile-Friendliness & Page Weight",              lambda url, sitemap_urls: check_mobile_and_weight(url)),
    ("URL Normalization",                              lambda url, sitemap_urls: check_url_normalization(url)),
    ("Outbound Links & Media",                         lambda url, sitemap_urls: check_outbound_and_media(url)),
    ("Multilingual Content Depth",                     lambda url, sitemap_urls: check_multilingual_depth(url)),
    ("Cross-Platform Content Distribution",            lambda url, sitemap_urls: check_cross_platform(url)),
    ("Multi-Page Sampling",                            lambda url, sitemap_urls: check_multi_page(url, sitemap_urls)),
]

ALL_CATEGORIES = [label for label, _ in CHECK_REGISTRY]

# Free-tier categories (5 checks / 17 sub-items). The SaaS /api/check/anonymous
# endpoint passes this list. `--categories` on the CLI does the same thing.
FREE_CATEGORIES = [
    "HTTPS",
    "robots.txt",
    "sitemap.xml",
    "Meta Tags",
    "Mobile-Friendliness & Page Weight",
]


def _run_checks(base_url, allowed_categories=None):
    """Execute registered checks, optionally restricted by category whitelist."""
    sitemap_urls = []
    allowed_set = set(allowed_categories) if allowed_categories else None
    for label, check_fn in CHECK_REGISTRY:
        if allowed_set is not None and label not in allowed_set:
            continue
        if check_fn == "__sitemap__":
            sitemap_urls = check_sitemap(base_url)
        else:
            check_fn(base_url, sitemap_urls)
    return sitemap_urls


def generate_score(base_url, allowed_categories=None):
    reset_state()
    parsed = urlparse(base_url)
    if not parsed.scheme:
        base_url = "https://" + base_url
    if not base_url.endswith("/"):
        base_url += "/"

    mode = "Diagnose + Fix Recommendations" if _state.SHOW_FIX else "Diagnose Only"
    print(f"{'='*60}")
    print(f"  GEO Readiness Report for: {base_url}")
    print(f"  Mode: {mode}")
    if allowed_categories:
        print(f"  Restricted to {len(allowed_categories)} categories: {', '.join(allowed_categories)}")
    print(f"{'='*60}")

    _run_checks(base_url, allowed_categories=allowed_categories)

    # --- AI Visibility Score ---
    score = get_ai_visibility_score()
    grade = get_grade(score)

    print(f"\n{'='*60}")
    print(f"  AI VISIBILITY SCORE: {score}/100  (Grade: {grade})")
    print(f"{'='*60}")
    print(f"\n  Category Breakdown:")
    print(f"  {'Category':<25} {'Score':>7}  {'Bar'}")
    print(f"  {'-'*25} {'-'*7}  {'-'*20}")
    for cat, vals in sorted(_state._scores.items(), key=lambda x: x[0]):
        earned = vals["earned"]
        mx = vals["max"]
        pct = (earned / mx * 100) if mx > 0 else 0
        bar_len = int(pct / 5)
        bar = "\033[92m" + "█" * bar_len + "\033[0m" + "░" * (20 - bar_len)
        print(f"  {cat:<25} {earned:>4.1f}/{mx:<3.0f}  {bar}")

    total_earned = sum(v["earned"] for v in _state._scores.values())
    total_max = sum(v["max"] for v in _state._scores.values())
    print(f"  {'-'*25} {'-'*7}")
    print(f"  {'TOTAL':<25} {total_earned:>4.1f}/{total_max:<3.0f}")

    print(f"\n  Legend: PASS = good | WARN = could improve | FAIL = missing/bad")
    if _state.SHOW_FIX:
        print("          FIX = recommended action to resolve the issue")
    else:
        print("  Tip: Run with --fix to see recommended solutions")
    print(f"{'='*60}\n")

    return score


def run_silent(url):
    """Run all checks on a URL, suppress output, return (score, scores_dict, url).

    Used by compare_urls to quickly scorecard multiple URLs without cluttering
    the terminal with per-site output. SHOW_FIX is force-disabled since we're
    only collecting scores.
    """
    old_fix = _state.SHOW_FIX
    _state.SHOW_FIX = False
    reset_state()

    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"

    old_stdout = sys.stdout
    sys.stdout = _io.StringIO()

    try:
        # Calls match A's run_silent (root geo_checker.py:3308) — includes the
        # two checks C's version was missing (check_brand_entity_kg, check_trust_safety).
        check_https(url)
        check_robots_txt(url)
        check_llms_txt(url)
        check_well_known(url)
        sitemap_urls = check_sitemap(url)
        check_search_engine_registration(url)
        check_structured_data(url)
        check_meta_tags(url)
        check_content_accessibility(url)
        check_ai_crawl_readiness(url)
        check_content_quality(url)
        check_technical_crawlability(url)
        check_authority_trust(url)
        check_brand_entity_kg(url)
        check_trust_safety(url)
        check_ai_optimization(url)
        check_social_signals(url)
        check_ai_answer_formats(url)
        check_schema_knowledge(url)
        check_mobile_and_weight(url)
        check_url_normalization(url)
        check_outbound_and_media(url)
        check_multilingual_depth(url)
        check_cross_platform(url)
        check_multi_page(url, sitemap_urls)
    finally:
        sys.stdout = old_stdout
        _state.SHOW_FIX = old_fix

    score = get_ai_visibility_score()
    scores_copy = {k: dict(v) for k, v in _state._scores.items()}
    return score, scores_copy, url
