#!/usr/bin/env python3
"""
GEO Readiness Checker
Checks a website's readiness for Generative Engine Optimization (GEO).
Inspects robots.txt, llms.txt, sitemap.xml, structured data, meta tags, and more.

Usage:
    python geo_checker.py https://example.com          # Diagnose only
    python geo_checker.py https://example.com --fix     # Diagnose + show fix recommendations
"""

import argparse
import io
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------
SHOW_FIX = False  # Toggled by --fix flag

AI_BOTS = [
    "GPTBot", "ChatGPT-User", "Google-Extended", "GoogleOther",
    "Anthropic", "anthropic-ai", "ClaudeBot", "Claude-Web", "CCBot",
    "PerplexityBot", "Bytespider", "Diffbot", "Applebot-Extended",
    "Cohere-ai", "Meta-ExternalAgent",
]

# AI/LLM crawler user-agent patterns for log analysis
# importance: "critical" = core AI crawlers (WARN if missing),
#             "optional" = supplementary/preview bots (INFO if missing)
AI_CRAWLERS = {
    "GPTBot":              {"pattern": r"GPTBot",              "powers": "ChatGPT training data",          "importance": "critical"},
    "ChatGPT-User":        {"pattern": r"ChatGPT-User",       "powers": "ChatGPT live browsing",          "importance": "critical"},
    "ClaudeBot":           {"pattern": r"ClaudeBot",           "powers": "Claude training data",           "importance": "critical"},
    "Anthropic":           {"pattern": r"anthropic-ai|Anthropic", "powers": "Anthropic crawling",          "importance": "optional"},
    "PerplexityBot":       {"pattern": r"PerplexityBot",       "powers": "Perplexity AI answers",          "importance": "critical"},
    "Googlebot":           {"pattern": r"Googlebot",            "powers": "Google Search → AI Overviews / SGE", "importance": "critical"},
    "GoogleOther":         {"pattern": r"GoogleOther",         "powers": "Google AI training",             "importance": "optional"},
    "Bingbot":             {"pattern": r"bingbot|Bingbot",     "powers": "Bing index → Copilot / ChatGPT", "importance": "critical"},
    "BingPreview":         {"pattern": r"BingPreview",         "powers": "Bing link preview (Teams/Outlook)", "importance": "optional"},
    "Bytespider":          {"pattern": r"Bytespider",          "powers": "ByteDance / TikTok AI",          "importance": "optional"},
    "CCBot":               {"pattern": r"CCBot",               "powers": "Common Crawl (used by many LLMs)", "importance": "optional"},
    "Diffbot":             {"pattern": r"Diffbot",             "powers": "Knowledge graph extraction",     "importance": "optional"},
    "Applebot-Extended":   {"pattern": r"Applebot-Extended",   "powers": "Apple Intelligence / Siri",      "importance": "optional"},
    "Applebot":            {"pattern": r"Applebot(?!-Extended)", "powers": "Apple search / Siri",           "importance": "optional"},
    "Cohere-ai":           {"pattern": r"[Cc]ohere-ai",        "powers": "Cohere models",                  "importance": "optional"},
    "Meta-ExternalAgent":  {"pattern": r"Meta-ExternalAgent",  "powers": "Meta AI",                        "importance": "optional"},
    "YouBot":              {"pattern": r"YouBot",              "powers": "You.com AI search",              "importance": "optional"},
    "PetalBot":            {"pattern": r"PetalBot",            "powers": "Huawei / Petal Search AI",       "importance": "optional"},
    "SemrushBot":          {"pattern": r"SemrushBot",          "powers": "SEO analytics (AI-adjacent)",    "importance": "optional"},
    "AhrefsBot":           {"pattern": r"AhrefsBot",           "powers": "SEO analytics (AI-adjacent)",    "importance": "optional"},
}

PASS = "\033[92mPASS\033[0m"
WARN = "\033[93mWARN\033[0m"
FAIL = "\033[91mFAIL\033[0m"
INFO = "\033[94mINFO\033[0m"
FIX  = "\033[96m FIX\033[0m"

_page_cache = {}

# ---------------------------------------------------------------------------
# i18n-ready check emitter
# ---------------------------------------------------------------------------
# When the backend imports us, it sets GEO_EMIT_STRUCTURED=1 in the env
# BEFORE importing. Each emit_check() call then embeds a machine-parseable
# marker at the end of the printed line carrying the i18n key + params.
# The backend's parse_geo_output extracts the marker to attach message_key
# + message_params fields to CheckResult, enabling frontend t(key, params)
# rendering. Standalone CLI users don't set the env var and see clean
# English output, exactly as before.
#
# Backwards compatibility: legacy print(f"  [{PASS}] ...") calls that have
# NOT yet been migrated to emit_check() still work — the parser falls back
# to treating the raw English text as the display message (no message_key).
import os as _os

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


# ---------------------------------------------------------------------------
# Score tracking
# ---------------------------------------------------------------------------
_scores = {}  # category -> {"earned": float, "max": float}


def track_score(category, earned, max_points):
    """Record earned/max points for a check category."""
    if category not in _scores:
        _scores[category] = {"earned": 0.0, "max": 0.0}
    _scores[category]["earned"] += earned
    _scores[category]["max"] += max_points


def get_ai_visibility_score():
    """Calculate overall AI Visibility Score (0-100)."""
    total_earned = sum(v["earned"] for v in _scores.values())
    total_max = sum(v["max"] for v in _scores.values())
    if total_max == 0:
        return 0
    return round((total_earned / total_max) * 100)


def get_grade(score):
    """Convert 0-100 score to letter grade."""
    if score >= 90:
        return "A+"
    elif score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    else:
        return "F"


def reset_state():
    """Reset global state for a fresh run."""
    global _scores, _page_cache
    _scores = {}
    _page_cache = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fix(message):
    """Print a fix recommendation if --fix flag is enabled."""
    if SHOW_FIX:
        for line in message.strip().splitlines():
            print(f"  [{FIX}] {line}")


def fetch(url, timeout=15, allow_redirects=True):
    if url in _page_cache:
        return _page_cache[url]
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=allow_redirects, headers={
            "User-Agent": "GEO-Readiness-Checker/1.0"
        })
        _page_cache[url] = resp
        return resp
    except requests.RequestException:
        return None


def get_soup(base_url):
    resp = fetch(base_url)
    if not resp or resp.status_code != 200:
        return None, None
    return resp, BeautifulSoup(resp.text, "html.parser")


def get_text_content(soup):
    clone = BeautifulSoup(str(soup), "html.parser")
    for tag in clone(["script", "style", "noscript"]):
        tag.decompose()
    return clone.get_text(separator=" ", strip=True)


# ---------------------------------------------------------------------------
# 1. HTTPS
# ---------------------------------------------------------------------------
def check_https(url):
    print("\n--- HTTPS ---")
    parsed = urlparse(url)
    if parsed.scheme == "https":
        emit_check(PASS, "result.checks.https.uses_https", "Site uses HTTPS")
        track_score("HTTPS", 5, 5)
        return True
    else:
        emit_check(
            FAIL,
            "result.checks.https.not_https",
            "Site does not use HTTPS — AI engines prefer secure sites",
        )
        fix("Install an SSL/TLS certificate (free via Let's Encrypt) and redirect all HTTP traffic to HTTPS.\nExample nginx: return 301 https://$host$request_uri;")
        track_score("HTTPS", 0, 5)
        return False


# ---------------------------------------------------------------------------
# 2. robots.txt
# ---------------------------------------------------------------------------
def check_robots_txt(base_url):
    print("\n--- robots.txt ---")
    url = urljoin(base_url, "/robots.txt")
    resp = fetch(url)

    if resp is None or resp.status_code != 200:
        emit_check(FAIL, "result.checks.robots.not_found", f"robots.txt not found at {url}", {"url": url})
        fix("Create a robots.txt file at the root of your site.\nMinimal example:\n  User-agent: *\n  Allow: /\n  Sitemap: https://yoursite.com/sitemap.xml")
        track_score("robots.txt", 0, 8)
        return

    emit_check(PASS, "result.checks.robots.found", f"robots.txt found ({len(resp.text)} bytes)", {"bytes": len(resp.text)})
    track_score("robots.txt", 3, 3)
    lines = resp.text.splitlines()

    has_sitemap_ref = any(line.strip().lower().startswith("sitemap:") for line in lines)
    if has_sitemap_ref:
        emit_check(PASS, "result.checks.robots.sitemap_ref_present", "robots.txt references a sitemap")
        track_score("robots.txt", 2, 2)
    else:
        emit_check(WARN, "result.checks.robots.sitemap_ref_missing", "robots.txt does not reference a sitemap")
        fix("Add a Sitemap directive to your robots.txt:\n  Sitemap: https://yoursite.com/sitemap.xml")
        track_score("robots.txt", 0, 2)

    blocked, allowed, not_mentioned = [], [], []
    for bot in AI_BOTS:
        bot_lower = bot.lower()
        bot_mentioned = False
        bot_blocked = False
        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            if stripped.startswith("user-agent:") and bot_lower in stripped:
                bot_mentioned = True
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip().lower()
                    if next_line.startswith("user-agent:"):
                        break
                    if next_line.startswith("disallow: /"):
                        bot_blocked = True
                        break
        if bot_mentioned and bot_blocked:
            blocked.append(bot)
        elif bot_mentioned:
            allowed.append(bot)
        else:
            not_mentioned.append(bot)

    wildcard_blocks_all = False
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped == "user-agent: *":
            for j in range(i + 1, len(lines)):
                next_line = lines[j].strip().lower()
                if next_line.startswith("user-agent:"):
                    break
                if next_line == "disallow: /":
                    wildcard_blocks_all = True
                    break

    if wildcard_blocks_all:
        emit_check(WARN, "result.checks.robots.wildcard_blocks_all", "Wildcard user-agent blocks all crawlers (Disallow: /)")
        fix("Change 'Disallow: /' under 'User-agent: *' to 'Allow: /' if you want AI crawlers to index your site.\nYou can selectively block specific bots while allowing others.")
    if blocked:
        emit_check(WARN, "result.checks.robots.bots_blocked", f"AI bots explicitly BLOCKED: {', '.join(blocked)}", {"bots": ", ".join(blocked)})
        fix(f"To allow these AI bots, remove or modify their Disallow directives in robots.txt.\nExample to allow GPTBot:\n  User-agent: GPTBot\n  Allow: /")
    if allowed:
        emit_check(PASS, "result.checks.robots.bots_with_directives", f"AI bots with directives (not blocked): {', '.join(allowed)}", {"bots": ", ".join(allowed)})
    if not_mentioned:
        emit_check(INFO, "result.checks.robots.bots_inherit_wildcard", f"AI bots not mentioned (inherit wildcard rules): {', '.join(not_mentioned)}", {"bots": ", ".join(not_mentioned)})

    # Score: 3 pts for AI bot access
    total_bots = len(AI_BOTS)
    accessible = total_bots - len(blocked) - (total_bots if wildcard_blocks_all and not allowed else 0)
    bot_ratio = max(accessible, 0) / total_bots if total_bots > 0 else 1
    track_score("robots.txt", round(bot_ratio * 3, 1), 3)


# ---------------------------------------------------------------------------
# 3. llms.txt
# ---------------------------------------------------------------------------
def check_llms_txt(base_url):
    print("\n--- llms.txt ---")
    llms_score = 0

    for filename in ["llms.txt", "llms-full.txt", ".well-known/llms.txt"]:
        url = urljoin(base_url, f"/{filename}")
        resp = fetch(url)
        if resp and resp.status_code == 200 and len(resp.text.strip()) > 0:
            text = resp.text.strip()
            lines = text.splitlines()
            emit_check(PASS, "result.checks.llms.found", f"{filename} found ({len(lines)} lines, {len(text)} bytes)", {"filename": filename, "lines": len(lines), "bytes": len(text)})

            has_title = any(line.strip().startswith("# ") for line in lines)
            has_description = len([l for l in lines if l.strip() and not l.strip().startswith("#") and not l.strip().startswith(">") and not l.strip().startswith("-")]) > 0
            has_sections = any(line.strip().startswith("## ") for line in lines)
            has_links = any("](http" in line or "](/" in line for line in lines)
            has_blockquotes = any(line.strip().startswith("> ") for line in lines)

            llms_score += 2  # file found

            if has_title:
                title_line = next(l for l in lines if l.strip().startswith("# "))
                emit_check(PASS, "result.checks.llms.title_present", f"Title: {title_line.strip()}", {"title": title_line.strip()})
                llms_score += 0.5
            else:
                emit_check(WARN, "result.checks.llms.title_missing", "No markdown title (# heading) — recommended by llms.txt spec")
                fix(f"Add a title as the first line of {filename}:\n  # Your Site Name")

            if has_description:
                emit_check(PASS, "result.checks.llms.description_present", "Contains descriptive text")
            else:
                emit_check(WARN, "result.checks.llms.description_missing", "No descriptive text found — should explain what the site/org does")
                fix(f"Add a paragraph below the title explaining what your site/org does:\n  # Your Site\n  A brief description of your site and what it offers.")

            if has_sections:
                section_count = sum(1 for l in lines if l.strip().startswith("## "))
                emit_check(PASS, "result.checks.llms.sections_found", f"{section_count} section(s) found (## headings)", {"count": section_count})
                llms_score += 0.5
            else:
                emit_check(WARN, "result.checks.llms.sections_missing", "No sections (## headings) — consider organizing content into sections")
                fix("Organize your llms.txt with sections like:\n  ## Documentation\n  ## API Reference\n  ## Blog")

            if has_links:
                link_count = sum(1 for l in lines if "](http" in l or "](/" in l)
                emit_check(PASS, "result.checks.llms.links_found", f"{link_count} link(s) to resources found", {"count": link_count})
                llms_score += 0.5
            else:
                emit_check(WARN, "result.checks.llms.links_missing", "No links found — llms.txt should link to key resources")
                fix("Add markdown links to your key pages:\n  - [Documentation](https://yoursite.com/docs)\n  - [API Reference](https://yoursite.com/api)")

            if has_blockquotes:
                emit_check(PASS, "result.checks.llms.blockquotes_present", "Blockquote descriptions (>) present")

            if len(text) < 100:
                emit_check(WARN, "result.checks.llms.too_short", f"File is very short ({len(text)} bytes) — may be a placeholder", {"bytes": len(text)})
                fix("Expand the file with meaningful content about your site, its purpose, key pages, and resources.")
        else:
            emit_check(FAIL, "result.checks.llms.file_not_found", f"{filename} not found", {"filename": filename})
            if filename == "llms.txt":
                fix("Create an llms.txt file at your site root. Example structure:\n  # Your Site Name\n  A brief description of your site.\n  \n  ## Documentation\n  > Overview of your docs\n  - [Getting Started](https://yoursite.com/docs/start)\n  \n  ## API\n  > API reference\n  - [API Docs](https://yoursite.com/api)")
            elif filename == "llms-full.txt":
                fix("Create llms-full.txt with expanded content — a more detailed version of llms.txt\nwith full descriptions, complete resource listings, and deeper context for AI models.")

    track_score("llms.txt", min(llms_score, 5), 5)


# ---------------------------------------------------------------------------
# 4. .well-known Discovery
# ---------------------------------------------------------------------------
def check_well_known(base_url):
    print("\n--- .well-known Discovery ---")

    well_known_files = {
        ".well-known/ai-plugin.json": "OpenAI plugin manifest (ChatGPT plugins)",
        ".well-known/openai.yaml": "OpenAI API spec for plugins",
        ".well-known/security.txt": "Security contact info (trust signal for AI engines)",
        ".well-known/gpc.json": "Global Privacy Control signal",
    }

    found_any = False
    wk_found = 0
    for path, description in well_known_files.items():
        url = urljoin(base_url, f"/{path}")
        resp = fetch(url)
        if resp and resp.status_code == 200 and len(resp.text.strip()) > 0:
            found_any = True
            wk_found += 1
            emit_check(PASS, "result.checks.well_known.file_found", f"{path} found — {description}", {"path": path, "description": description})
            if path.endswith(".json"):
                try:
                    data = json.loads(resp.text)
                    if path.endswith("ai-plugin.json"):
                        name = data.get("name_for_human", data.get("name", "unknown"))
                        print(f"         Plugin name: {name}")
                except json.JSONDecodeError:
                    emit_check(WARN, "result.checks.well_known.invalid_json", f"{path} exists but contains invalid JSON", {"path": path})
                    fix(f"Validate and fix the JSON in {path} — use a JSON linter to check for syntax errors.")
        else:
            emit_check(INFO, "result.checks.well_known.file_not_found", f"{path} not found — {description}", {"path": path, "description": description})

    if not found_any:
        print(f"  [{INFO}] No .well-known AI discovery files found")
        fix("Consider adding .well-known/security.txt (RFC 9116) as a trust signal:\n  Contact: mailto:security@yoursite.com\n  Preferred-Languages: en\n  Canonical: https://yoursite.com/.well-known/security.txt")

    track_score(".well-known", min(wk_found, 3), 3)


# ---------------------------------------------------------------------------
# 5. sitemap.xml
# ---------------------------------------------------------------------------
def check_sitemap(base_url):
    print("\n--- sitemap.xml ---")

    sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/sitemap.xml"]
    found = False
    sitemap_urls = []

    for path in sitemap_paths:
        url = urljoin(base_url, path)
        resp = fetch(url)
        if resp and resp.status_code == 200 and ("<?xml" in resp.text or "<urlset" in resp.text or "<sitemapindex" in resp.text):
            found = True
            url_count = resp.text.count("<loc>")
            emit_check(PASS, "result.checks.sitemap.found", f"Sitemap found at {path} ({url_count} <loc> entries)", {"path": path, "count": url_count})
            track_score("Sitemap", 4, 4)

            has_lastmod = "<lastmod>" in resp.text
            if has_lastmod:
                emit_check(PASS, "result.checks.sitemap.lastmod_present", "Sitemap includes <lastmod> timestamps")
                track_score("Sitemap", 3, 3)
            else:
                emit_check(WARN, "result.checks.sitemap.lastmod_missing", "Sitemap missing <lastmod> timestamps — helps AI engines know content freshness")
                fix("Add <lastmod> to each <url> entry in your sitemap:\n  <url>\n    <loc>https://yoursite.com/page</loc>\n    <lastmod>2025-01-15</lastmod>\n  </url>")
                track_score("Sitemap", 1, 3)

            try:
                root = ElementTree.fromstring(resp.text)
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for loc in root.findall(".//sm:loc", ns):
                    if loc.text:
                        sitemap_urls.append(loc.text.strip())
                if not sitemap_urls:
                    for loc in root.iter():
                        if loc.tag.endswith("loc") and loc.text:
                            sitemap_urls.append(loc.text.strip())
            except ElementTree.ParseError:
                pass
            break

    if not found:
        emit_check(FAIL, "result.checks.sitemap.not_found", "No sitemap.xml found")
        fix("Create a sitemap.xml at your site root. Example:\n  <?xml version=\"1.0\" encoding=\"UTF-8\"?>\n  <urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n    <url>\n      <loc>https://yoursite.com/</loc>\n      <lastmod>2025-01-15</lastmod>\n    </url>\n  </urlset>\nMost CMS platforms (WordPress, Next.js, etc.) can auto-generate sitemaps.")
        track_score("Sitemap", 0, 7)

    return sitemap_urls


# ---------------------------------------------------------------------------
# 5b. Search Engine & AI Platform Registration
# ---------------------------------------------------------------------------
def check_search_engine_registration(base_url):
    """Check for signs that the site is registered with search engines and AI platforms."""
    print("\n--- Search Engine & AI Platform Registration ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("Platform Registration", 0, 7)
        return

    # Google Search Console verification
    google_verify = soup.find("meta", attrs={"name": "google-site-verification"})
    if google_verify and google_verify.get("content"):
        print(f"  [{PASS}] Google Search Console verification tag found")
    else:
        # Check for verification file
        gsc_resp = fetch(urljoin(base_url, "/google*.html"), timeout=5)
        # Can't glob on server, so just note the absence
        print(f"  [{WARN}] No Google Search Console verification tag found")
        fix("Register your site with Google Search Console (https://search.google.com/search-console):\n  1. Add your property (URL prefix or domain)\n  2. Verify ownership via meta tag, DNS, or HTML file\n  3. Submit your sitemap.xml under Sitemaps\n  4. Monitor indexing status and fix any crawl errors\nThis is critical — Google's AI Overviews and SGE pull from the Google index.")

    # Bing Webmaster Tools verification
    bing_verify = soup.find("meta", attrs={"name": "msvalidate.01"})
    if bing_verify and bing_verify.get("content"):
        print(f"  [{PASS}] Bing Webmaster Tools verification tag found")
    else:
        print(f"  [{WARN}] No Bing Webmaster Tools verification tag found")
        fix("Register your site with Bing Webmaster Tools (https://www.bing.com/webmasters):\n  1. Add your site and verify ownership\n  2. Submit your sitemap.xml\n  3. This is essential — Bing's index powers Microsoft Copilot, ChatGPT (via Bing search),\n     and other AI assistants that use Bing as their search backend.")

    # Yandex verification (feeds into some AI systems)
    yandex_verify = soup.find("meta", attrs={"name": "yandex-verification"})
    if yandex_verify and yandex_verify.get("content"):
        print(f"  [{PASS}] Yandex Webmaster verification tag found")
    else:
        print(f"  [{INFO}] No Yandex Webmaster verification tag — relevant if targeting international AI platforms")

    # IndexNow support — check for key file
    indexnow_found = False
    # Check for IndexNow key in common locations
    for key_path in ["/.well-known/indexnow", "/indexnow"]:
        inow_url = urljoin(base_url, key_path)
        inow_resp = fetch(inow_url, timeout=5)
        if inow_resp and inow_resp.status_code == 200 and len(inow_resp.text.strip()) > 0:
            indexnow_found = True
            print(f"  [{PASS}] IndexNow endpoint found at {key_path} — enables instant index notifications")
            break

    # Also check for IndexNow meta tag or key file pattern
    if not indexnow_found:
        # Some sites host the key as a text file at root
        indexnow_meta = soup.find("meta", attrs={"name": "indexnow"})
        if indexnow_meta:
            indexnow_found = True
            print(f"  [{PASS}] IndexNow meta tag found")

    if not indexnow_found:
        print(f"  [{INFO}] No IndexNow integration detected")
        fix("Set up IndexNow for instant indexing by Bing, Yandex, and others:\n  1. Generate an API key at https://www.indexnow.org/\n  2. Host the key file at your site root: https://yoursite.com/{key}.txt\n  3. Notify search engines when content changes:\n     POST https://api.indexnow.org/indexnow\n     {\"host\": \"yoursite.com\", \"key\": \"your-key\", \"urlList\": [\"https://yoursite.com/updated-page\"]}\n  4. Many CMS plugins (WordPress, etc.) support IndexNow automatically.")

    # Check for Pinterest verification (some AI visual search)
    pinterest_verify = soup.find("meta", attrs={"name": "p:domain_verify"})
    if pinterest_verify:
        print(f"  [{PASS}] Pinterest domain verification found")

    # Summary / platform checklist
    print()
    platforms = {
        "Google Search Console": bool(google_verify and google_verify.get("content")),
        "Bing Webmaster Tools": bool(bing_verify and bing_verify.get("content")),
        "IndexNow": indexnow_found,
    }
    registered = [k for k, v in platforms.items() if v]
    not_registered = [k for k, v in platforms.items() if not v]

    if registered:
        print(f"  [{PASS}] Registered: {', '.join(registered)}")
    if not_registered:
        print(f"  [{WARN}] Not detected: {', '.join(not_registered)}")

        fix("Having files like sitemap.xml and robots.txt is not enough on its own.\nYou must also register and submit them to each platform:\n  \n  Google Search Console → Submit sitemap → Powers Google AI Overviews / SGE\n  Bing Webmaster Tools  → Submit sitemap → Powers Copilot, ChatGPT (Bing backend)\n  IndexNow              → Auto-notify   → Instant indexing for Bing, Yandex, Naver\n  \nWithout registration, search engines may find your sitemap eventually via crawling,\nbut submission ensures faster, more reliable indexing.")

    reg_score = 0
    if platforms.get("Google Search Console"):
        reg_score += 3
    if platforms.get("Bing Webmaster Tools"):
        reg_score += 3
    if platforms.get("IndexNow"):
        reg_score += 1
    track_score("Platform Registration", reg_score, 7)


# ---------------------------------------------------------------------------
# 6. Structured Data
# ---------------------------------------------------------------------------
def check_structured_data(base_url):
    print("\n--- Structured Data ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.structured_data.fetch_failed", "Could not fetch homepage")
        return

    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    if json_ld_scripts:
        emit_check(PASS, "result.checks.structured_data.jsonld_found", f"Found {len(json_ld_scripts)} JSON-LD block(s)", {"count": len(json_ld_scripts)})
        track_score("Structured Data", 4, 4)
        parsed_types = 0
        for i, script in enumerate(json_ld_scripts):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    schema_type = data.get("@type", "unknown")
                    print(f"         Block {i+1}: @type = {schema_type}")
                    parsed_types += 1
                elif isinstance(data, list):
                    types = [item.get("@type", "unknown") for item in data if isinstance(item, dict)]
                    print(f"         Block {i+1}: @types = {', '.join(types)}")
                    parsed_types += len(types)
            except (json.JSONDecodeError, TypeError):
                print(f"         Block {i+1}: present but could not parse")
        track_score("Structured Data", min(parsed_types, 3), 3)
    else:
        emit_check(WARN, "result.checks.structured_data.jsonld_missing", "No JSON-LD structured data found — helps AI engines understand your content")
        track_score("Structured Data", 0, 7)
        fix("Add JSON-LD structured data to your <head>. Example for an Organization:\n  <script type=\"application/ld+json\">\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"Organization\",\n    \"name\": \"Your Company\",\n    \"url\": \"https://yoursite.com\",\n    \"description\": \"What your company does\"\n  }\n  </script>\nUse Google's Rich Results Test to validate: https://search.google.com/test/rich-results")

    has_schema_ref = 'schema.org' in resp.text
    if has_schema_ref and not json_ld_scripts:
        emit_check(INFO, "result.checks.structured_data.schema_ref_only", "schema.org references found (possibly microdata or RDFa)")


# ---------------------------------------------------------------------------
# 7. Meta Tags
# ---------------------------------------------------------------------------
def check_meta_tags(base_url):
    print("\n--- Meta Tags ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.meta.fetch_failed", "Could not fetch homepage")
        track_score("Meta Tags", 0, 7)
        return

    meta_score = 0
    title = soup.find("title")
    if title and title.string and title.string.strip():
        title_text = title.string.strip()[:80]
        emit_check(PASS, "result.checks.meta.title_found", f"<title> found: \"{title_text}\"", {"title": title_text})
        meta_score += 1.5
    else:
        emit_check(FAIL, "result.checks.meta.title_missing", "Missing <title> tag")
        fix("Add a <title> tag in your <head>:\n  <title>Your Page Title — Your Brand</title>\nKeep it under 60 characters and include your primary keyword.")

    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content", "").strip():
        content = desc["content"].strip()
        emit_check(PASS, "result.checks.meta.description_found", f"Meta description found ({len(content)} chars)", {"chars": len(content)})
        meta_score += 1.5
        if len(content) < 50:
            emit_check(WARN, "result.checks.meta.description_too_short", "Meta description is very short — aim for 120-160 characters")
            fix("Expand your meta description to 120-160 characters. Include a clear value proposition and primary keywords.")
    else:
        emit_check(FAIL, "result.checks.meta.description_missing", "Missing meta description")
        fix("Add a meta description in your <head>:\n  <meta name=\"description\" content=\"A 120-160 character summary of your page content, including key topics and value proposition.\">\nThis is often what AI engines use when summarizing your site.")

    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        emit_check(PASS, "result.checks.meta.canonical_found", f"Canonical URL set: {canonical['href']}", {"url": canonical['href']})
        meta_score += 1
    else:
        emit_check(WARN, "result.checks.meta.canonical_missing", "No canonical URL — can cause duplicate content issues for AI engines")
        fix("Add a canonical link in your <head>:\n  <link rel=\"canonical\" href=\"https://yoursite.com/current-page\" />\nThis tells AI engines which version of a page is the authoritative one.")

    og_tags = soup.find_all("meta", property=re.compile(r"^og:"))
    if og_tags:
        og_types = [tag.get("property") for tag in og_tags]
        emit_check(PASS, "result.checks.meta.og_tags_found", f"Open Graph tags found: {', '.join(og_types)}", {"tags": ", ".join(og_types)})
        meta_score += 1
    else:
        emit_check(WARN, "result.checks.meta.og_tags_missing", "No Open Graph tags — used by AI engines for content summarization")
        fix("Add Open Graph meta tags in your <head>:\n  <meta property=\"og:title\" content=\"Page Title\" />\n  <meta property=\"og:description\" content=\"Page description\" />\n  <meta property=\"og:type\" content=\"website\" />\n  <meta property=\"og:url\" content=\"https://yoursite.com/page\" />\n  <meta property=\"og:image\" content=\"https://yoursite.com/image.jpg\" />")

    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        emit_check(PASS, "result.checks.meta.lang_declared", f"Language declared: {html_tag['lang']}", {"lang": html_tag['lang']})
        meta_score += 1
    else:
        emit_check(WARN, "result.checks.meta.lang_missing", "No lang attribute on <html> — helps AI engines understand content language")
        fix("Add a lang attribute to your <html> tag:\n  <html lang=\"en\">")

    hreflangs = soup.find_all("link", rel="alternate", hreflang=True)
    if hreflangs:
        langs = [tag.get("hreflang") for tag in hreflangs]
        emit_check(PASS, "result.checks.meta.hreflang_found", f"Hreflang tags found for: {', '.join(langs)}", {"langs": ", ".join(langs)})
        meta_score += 1
    else:
        emit_check(INFO, "result.checks.meta.hreflang_missing", "No hreflang tags — add these if your site supports multiple languages")
        fix("If your site is multilingual, add hreflang tags:\n  <link rel=\"alternate\" hreflang=\"en\" href=\"https://yoursite.com/en/page\" />\n  <link rel=\"alternate\" hreflang=\"es\" href=\"https://yoursite.com/es/page\" />\n  <link rel=\"alternate\" hreflang=\"x-default\" href=\"https://yoursite.com/page\" />")

    track_score("Meta Tags", meta_score, 7)


# ---------------------------------------------------------------------------
# 8. Content Accessibility
# ---------------------------------------------------------------------------
def check_content_accessibility(base_url):
    print("\n--- Content Accessibility ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.content_access.fetch_failed", "Could not fetch homepage")
        track_score("Content Accessibility", 0, 6)
        return

    ca_score = 0
    text = get_text_content(soup)
    word_count = len(text.split())

    if word_count > 200:
        emit_check(PASS, "result.checks.content_access.words_ok", f"Homepage has {word_count} words in initial HTML", {"count": word_count})
        ca_score += 2
    elif word_count > 50:
        emit_check(WARN, "result.checks.content_access.words_low", f"Homepage has only {word_count} words in initial HTML — may rely too heavily on JavaScript rendering", {"count": word_count})
        fix("Ensure key content is rendered server-side (SSR/SSG) so AI crawlers can read it.\nIf using React/Vue/Angular, switch to Next.js/Nuxt.js/Angular Universal for server-side rendering.")
        ca_score += 1
    else:
        emit_check(FAIL, "result.checks.content_access.words_js_only", f"Homepage has only {word_count} words — likely JS-rendered, invisible to most AI crawlers", {"count": word_count})
        fix("Your page content is likely rendered client-side via JavaScript. AI crawlers cannot execute JS.\nSolutions:\n  1. Use server-side rendering (SSR) — Next.js, Nuxt.js, etc.\n  2. Use static site generation (SSG) — pre-render pages at build time.\n  3. Add a pre-rendering service (e.g., Prerender.io) to serve static HTML to bots.")

    html_size = len(resp.text)
    text_size = len(text.encode("utf-8"))
    if html_size > 0:
        ratio = (text_size / html_size) * 100
        if ratio >= 15:
            emit_check(PASS, "result.checks.content_access.ratio_good", f"Content-to-HTML ratio: {ratio:.1f}% (good)", {"ratio": f"{ratio:.1f}"})
            ca_score += 2
        elif ratio >= 5:
            emit_check(WARN, "result.checks.content_access.ratio_low", f"Content-to-HTML ratio: {ratio:.1f}% — low ratio means lots of boilerplate vs. real content", {"ratio": f"{ratio:.1f}"})
            ca_score += 1
            fix("Reduce HTML bloat: minimize inline CSS/JS, remove unused markup, and move scripts to external files.\nEnsure the page body contains substantive, unique content — not just navigation and footers.")
        else:
            emit_check(FAIL, "result.checks.content_access.ratio_very_low", f"Content-to-HTML ratio: {ratio:.1f}% — very low, mostly boilerplate/code", {"ratio": f"{ratio:.1f}"})
            fix("Extremely low content ratio. Likely causes:\n  1. Heavy inline CSS/JS frameworks — externalize them.\n  2. Client-side rendering — switch to SSR/SSG.\n  3. Content hidden in JavaScript state — ensure HTML contains readable text.")

    headings = soup.find_all(re.compile(r"^h[1-6]$"))
    if headings:
        h_tags = [h.name for h in headings]
        h_summary = {tag: h_tags.count(tag) for tag in sorted(set(h_tags))}
        summary_str = ", ".join(f"{k}: {v}" for k, v in h_summary.items())
        emit_check(PASS, "result.checks.content_access.headings_found", f"Heading structure found ({summary_str})", {"summary": summary_str})
        ca_score += 2
        if headings[0].name != "h1":
            emit_check(WARN, "result.checks.content_access.first_heading_not_h1", f"First heading is <{headings[0].name}>, not <h1> — clear hierarchy helps AI engines", {"tag": headings[0].name})
            fix("Ensure the first heading on the page is an <h1> tag containing the primary topic.\nUse a logical hierarchy: h1 > h2 > h3 (don't skip levels).")
    else:
        emit_check(WARN, "result.checks.content_access.headings_missing", "No heading tags found — structured headings help AI engines parse content")
        fix("Add heading tags to structure your content:\n  <h1>Main Page Topic</h1>\n  <h2>Subtopic</h2>\n  <h3>Detail</h3>\nHeadings help AI engines understand content hierarchy and extract key topics.")

    track_score("Content Accessibility", ca_score, 6)


# ---------------------------------------------------------------------------
# 9. AI Crawl Readiness
# ---------------------------------------------------------------------------
def check_ai_crawl_readiness(base_url):
    print("\n--- AI Crawl Readiness ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.crawl_ready.fetch_failed", "Could not fetch homepage")
        track_score("AI Crawl Readiness", 0, 8)
        return

    acr_score = 0
    body = soup.find("body")
    if body:
        body_text = body.get_text(separator=" ", strip=True)
        spa_indicators = ["__next", "__nuxt", "root", "app"]
        body_divs = body.find_all("div", id=True)
        div_ids = [d.get("id", "").lower() for d in body_divs]
        is_likely_spa = any(ind in div_ids for ind in spa_indicators)

        if is_likely_spa and len(body_text.split()) < 100:
            emit_check(FAIL, "result.checks.crawl_ready.spa_empty", "Likely a client-side rendered SPA with minimal server-side content")
            print(f"         AI crawlers cannot execute JavaScript — consider SSR/SSG")
            fix("Enable server-side rendering in your framework:\n  Next.js: use getServerSideProps() or generateStaticParams()\n  Nuxt.js: set ssr: true in nuxt.config\n  React: consider migrating to Next.js or Remix")
        elif is_likely_spa:
            emit_check(PASS, "result.checks.crawl_ready.spa_with_ssr", "SPA framework detected but server-side content is present (SSR/SSG)")
            acr_score += 2
        else:
            emit_check(PASS, "result.checks.crawl_ready.ssr_content", "Content is rendered server-side")
            acr_score += 2

    robots_meta = soup.find("meta", attrs={"name": "robots"})
    if robots_meta:
        robots_content = robots_meta.get("content", "").lower()
        if "noindex" in robots_content:
            emit_check(FAIL, "result.checks.crawl_ready.meta_noindex", "Meta robots contains 'noindex' — page will be excluded from AI training data")
            fix("Remove 'noindex' from the meta robots tag if you want AI engines to index this page:\n  <meta name=\"robots\" content=\"index, follow\" />")
        if "nofollow" in robots_content:
            emit_check(WARN, "result.checks.crawl_ready.meta_nofollow", "Meta robots contains 'nofollow' — AI crawlers won't follow links on this page")
            fix("Remove 'nofollow' if you want AI crawlers to discover linked pages:\n  <meta name=\"robots\" content=\"index, follow\" />")
        if "noai" in robots_content or "noimageai" in robots_content:
            emit_check(WARN, "result.checks.crawl_ready.meta_noai", f"Meta robots contains AI-specific opt-out directive: {robots_content}", {"content": robots_content})
            fix("The 'noai' / 'noimageai' directive opts your content out of AI training.\nRemove it if you want AI engines to include your content in their responses.")
        if "noindex" not in robots_content and "noai" not in robots_content:
            emit_check(PASS, "result.checks.crawl_ready.meta_allows_index", f"Meta robots allows indexing: {robots_content}", {"content": robots_content})
            acr_score += 1
    else:
        emit_check(PASS, "result.checks.crawl_ready.meta_no_restriction", "No restrictive meta robots tag found")
        acr_score += 1

    x_robots = resp.headers.get("X-Robots-Tag", "")
    if x_robots:
        if "noindex" in x_robots.lower() or "noai" in x_robots.lower():
            emit_check(FAIL, "result.checks.crawl_ready.xrobots_restrict", f"X-Robots-Tag header restricts AI: {x_robots}", {"header": x_robots})
            fix("Remove the restrictive X-Robots-Tag header from your server config.\nNginx: remove 'add_header X-Robots-Tag \"noindex\";'\nApache: remove 'Header set X-Robots-Tag \"noindex\"'")
        else:
            emit_check(INFO, "result.checks.crawl_ready.xrobots_present", f"X-Robots-Tag header present: {x_robots}", {"header": x_robots})
    else:
        emit_check(PASS, "result.checks.crawl_ready.xrobots_clean", "No restrictive X-Robots-Tag header")
        acr_score += 1

    paywall_indicators = [
        "paywall", "subscribe-wall", "login-wall", "premium-content",
        "gated-content", "registration-wall"
    ]
    paywall_classes = []
    for indicator in paywall_indicators:
        elements = soup.find_all(class_=re.compile(indicator, re.IGNORECASE))
        elements += soup.find_all(id=re.compile(indicator, re.IGNORECASE))
        if elements:
            paywall_classes.append(indicator)

    if paywall_classes:
        emit_check(WARN, "result.checks.crawl_ready.paywall_detected", f"Possible gated content detected (classes/ids: {', '.join(paywall_classes)})", {"classes": ", ".join(paywall_classes)})
        print(f"         Gated content is invisible to AI crawlers")
        fix("AI crawlers cannot see content behind paywalls/login walls.\nConsider:\n  1. Providing a generous free preview or summary above the gate.\n  2. Using 'metered' access so bots see full content on first visit.\n  3. Adding structured data (JSON-LD) with key facts outside the gate.")
    else:
        emit_check(PASS, "result.checks.crawl_ready.no_paywall", "No paywall/login-wall indicators detected")
        acr_score += 1

    semantic_tags = ["article", "main", "section", "nav", "aside", "header", "footer"]
    found_semantic = [tag for tag in semantic_tags if soup.find(tag)]
    if len(found_semantic) >= 3:
        emit_check(PASS, "result.checks.crawl_ready.semantic_good", f"Good semantic HTML structure ({', '.join(found_semantic)})", {"tags": ", ".join(found_semantic)})
        acr_score += 1
    elif found_semantic:
        emit_check(WARN, "result.checks.crawl_ready.semantic_limited", f"Limited semantic HTML ({', '.join(found_semantic)}) — more semantic tags help AI parse content", {"tags": ", ".join(found_semantic)})
        fix("Replace generic <div> containers with semantic HTML5 tags:\n  <header> for site header/nav\n  <main> for primary content\n  <article> for self-contained content\n  <section> for thematic groupings\n  <aside> for sidebar/related content\n  <footer> for footer")
    else:
        emit_check(FAIL, "result.checks.crawl_ready.semantic_missing", "No semantic HTML tags found — AI crawlers rely on semantic structure")
        fix("Your page uses only <div> tags. Replace them with semantic HTML5 elements:\n  <header>, <nav>, <main>, <article>, <section>, <aside>, <footer>\nThis helps AI engines understand the role of each content block.")

    images = soup.find_all("img")
    if images:
        with_alt = sum(1 for img in images if img.get("alt", "").strip())
        total = len(images)
        pct = (with_alt / total * 100) if total > 0 else 0
        if pct >= 80:
            emit_check(PASS, "result.checks.crawl_ready.alt_good", f"{with_alt}/{total} images have alt text ({pct:.0f}%)", {"with_alt": with_alt, "total": total, "pct": f"{pct:.0f}"})
            acr_score += 1
        elif pct >= 50:
            emit_check(WARN, "result.checks.crawl_ready.alt_medium", f"{with_alt}/{total} images have alt text ({pct:.0f}%) — aim for >80%", {"with_alt": with_alt, "total": total, "pct": f"{pct:.0f}"})
            fix("Add descriptive alt text to all <img> tags:\n  <img src=\"photo.jpg\" alt=\"Description of what the image shows\" />\nGood alt text is specific: 'Team meeting in conference room' not 'image1'.")
        else:
            emit_check(FAIL, "result.checks.crawl_ready.alt_poor", f"Only {with_alt}/{total} images have alt text ({pct:.0f}%) — AI crawlers need alt text", {"with_alt": with_alt, "total": total, "pct": f"{pct:.0f}"})
            fix("Most images are missing alt text. Add descriptive alt attributes to every <img>:\n  <img src=\"photo.jpg\" alt=\"Descriptive text about the image content\" />\nFor decorative images, use alt=\"\" (empty but present).")
    else:
        emit_check(INFO, "result.checks.crawl_ready.no_images", "No images found on homepage")

    links = soup.find_all("a", href=True)
    parsed_base = urlparse(base_url)
    internal_links = [
        l for l in links
        if urlparse(urljoin(base_url, l["href"])).netloc == parsed_base.netloc
    ]
    if len(internal_links) >= 10:
        emit_check(PASS, "result.checks.crawl_ready.internal_links_good", f"{len(internal_links)} internal links — good for AI crawl discovery", {"count": len(internal_links)})
    elif len(internal_links) >= 3:
        emit_check(WARN, "result.checks.crawl_ready.internal_links_few", f"Only {len(internal_links)} internal links — more internal links help AI engines discover content", {"count": len(internal_links)})
        fix("Add more internal links to help AI crawlers discover your content.\nInclude links to key pages in your navigation, footer, and within content body.\nUse descriptive anchor text: 'Read our pricing guide' not 'click here'.")
    else:
        emit_check(FAIL, "result.checks.crawl_ready.internal_links_none", f"Very few internal links ({len(internal_links)}) — AI crawlers rely on links to find content", {"count": len(internal_links)})
        fix("Your homepage has very few internal links. AI crawlers use links to discover pages.\nAdd:\n  1. A navigation menu linking to key sections\n  2. Featured content links in the body\n  3. A footer with links to important pages\n  4. Contextual links within content")

    start = time.time()
    fetch(urljoin(base_url, "/?_geo_timing_check"), timeout=10)
    elapsed = time.time() - start
    if elapsed < 1:
        emit_check(PASS, "result.checks.crawl_ready.response_fast", f"Response time: {elapsed:.2f}s", {"seconds": f"{elapsed:.2f}"})
        acr_score += 1
    elif elapsed < 3:
        emit_check(WARN, "result.checks.crawl_ready.response_slow", f"Response time: {elapsed:.2f}s — slow responses may cause AI crawlers to skip pages", {"seconds": f"{elapsed:.2f}"})
        fix("Improve response time:\n  1. Enable server-side caching (Redis, Varnish, CDN)\n  2. Optimize database queries\n  3. Use a CDN (Cloudflare, Fastly, CloudFront)\n  4. Enable gzip/brotli compression")
    else:
        emit_check(FAIL, "result.checks.crawl_ready.response_timeout", f"Response time: {elapsed:.2f}s — too slow for reliable AI crawling", {"seconds": f"{elapsed:.2f}"})
        fix("Response time is critically slow. AI crawlers may time out.\nImmediate actions:\n  1. Add a CDN in front of your origin server\n  2. Enable page caching at the server level\n  3. Profile your server-side code for bottlenecks\n  4. Consider static site generation for content pages")

    track_score("AI Crawl Readiness", acr_score, 8)


# ---------------------------------------------------------------------------
# 10. Content Quality for AI
# ---------------------------------------------------------------------------
def flesch_kincaid_grade(text):
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = text.split()
    if not sentences or not words:
        return None

    def count_syllables(word):
        word = word.lower().rstrip("e")
        count = 0
        prev_vowel = False
        for ch in word:
            is_vowel = ch in "aeiou"
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel
        return max(count, 1)

    num_syllables = sum(count_syllables(w) for w in words)
    return 0.39 * (len(words) / len(sentences)) + 11.8 * (num_syllables / len(words)) - 15.59


def check_content_quality(base_url):
    print("\n--- Content Quality for AI ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("Content Quality", 0, 7)
        return

    cq_score = 0
    text = get_text_content(soup)

    grade = flesch_kincaid_grade(text)
    if grade is not None:
        if 6 <= grade <= 12:
            print(f"  [{PASS}] Readability: Flesch-Kincaid grade {grade:.1f} (accessible)")
            cq_score += 2
        elif grade < 6:
            print(f"  [{INFO}] Readability: Flesch-Kincaid grade {grade:.1f} (very simple)")
            cq_score += 1.5
        else:
            print(f"  [{WARN}] Readability: Flesch-Kincaid grade {grade:.1f} (complex) — simpler text ranks better in AI answers")
            fix("Simplify your content for better AI readability:\n  1. Use shorter sentences (under 20 words)\n  2. Replace jargon with plain language\n  3. Break complex ideas into bullet points\n  4. Use active voice instead of passive\n  5. Target a grade 8-10 reading level")

    # FAQ detection
    faq_indicators = 0
    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    for script in json_ld_scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("@type") == "FAQPage":
                faq_indicators += 2
        except (json.JSONDecodeError, TypeError):
            pass

    headings = soup.find_all(re.compile(r"^h[1-6]$"))
    question_headings = [h for h in headings if h.get_text(strip=True).rstrip().endswith("?")]
    if question_headings:
        faq_indicators += 1

    faq_elements = soup.find_all(class_=re.compile(r"faq|frequently.asked", re.IGNORECASE))
    faq_elements += soup.find_all(id=re.compile(r"faq|frequently.asked", re.IGNORECASE))
    if faq_elements:
        faq_indicators += 1

    if soup.find_all("details"):
        faq_indicators += 1

    if faq_indicators >= 2:
        print(f"  [{PASS}] FAQ content detected — strong signal for AI-generated answers")
        cq_score += 2
    elif faq_indicators == 1:
        print(f"  [{INFO}] Possible FAQ-like content — consider adding FAQPage structured data")
        cq_score += 1
        fix("Add FAQPage schema to boost AI answer ranking:\n  <script type=\"application/ld+json\">\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"FAQPage\",\n    \"mainEntity\": [{\n      \"@type\": \"Question\",\n      \"name\": \"What is your product?\",\n      \"acceptedAnswer\": {\n        \"@type\": \"Answer\",\n        \"text\": \"Our product is...\"\n      }\n    }]\n  }\n  </script>")
    else:
        print(f"  [{INFO}] No FAQ content detected — FAQ pages rank well in AI-generated answers")
        fix("Consider adding an FAQ section to your page. Format questions as headings:\n  <h2>Frequently Asked Questions</h2>\n  <h3>What does your product do?</h3>\n  <p>Clear, concise answer...</p>\nThen add FAQPage structured data (JSON-LD) for each Q&A pair.")

    stat_patterns = re.findall(r'\d+(?:\.\d+)?%|\$\d+|\d+(?:,\d{3})+', text)
    if len(stat_patterns) >= 3:
        print(f"  [{PASS}] {len(stat_patterns)} quotable statistics found — good for AI citations")
        cq_score += 1
    elif stat_patterns:
        print(f"  [{INFO}] {len(stat_patterns)} statistic(s) found — more specific data improves AI citation likelihood")
    else:
        print(f"  [{WARN}] No quotable statistics found — specific numbers/data help AI engines cite your content")
        fix("Add concrete, quotable statistics to your content:\n  '95% of customers report improved performance'\n  'Over 10,000 companies use our platform'\n  'Reduces processing time by 3.5x'\nAI engines prefer citing specific data points over vague claims.")

    source_patterns = re.findall(
        r'(?:according to|source:|study by|research from|data from|report by|published in)\s',
        text, re.IGNORECASE
    )
    if source_patterns:
        print(f"  [{PASS}] {len(source_patterns)} source attribution(s) found — increases trust for AI engines")
        cq_score += 1
    else:
        print(f"  [{INFO}] No explicit source attributions — citing sources increases AI trust in your content")
        fix("Add source attributions to increase credibility:\n  'According to [Source Name], ...'\n  'Data from our 2025 industry report shows...'\n  'A study by [Institution] found...'\nAI engines weight attributed claims higher than unattributed ones.")

    lists = soup.find_all(["ul", "ol"])
    list_items = soup.find_all("li")
    if len(list_items) >= 5:
        print(f"  [{PASS}] Structured lists found ({len(lists)} lists, {len(list_items)} items)")
        cq_score += 1
    elif list_items:
        print(f"  [{INFO}] Some list content ({len(list_items)} items) — structured lists help AI extract key points")
    else:
        print(f"  [{WARN}] No list elements — structured lists help AI engines extract key points")
        fix("Add structured lists to make content easily extractable by AI:\n  <ul>\n    <li>Key feature or benefit</li>\n    <li>Another important point</li>\n  </ul>\nUse <ol> for steps/processes and <ul> for features/benefits.")

    track_score("Content Quality", min(cq_score, 7), 7)


# ---------------------------------------------------------------------------
# 11. Technical Crawlability
# ---------------------------------------------------------------------------
def check_technical_crawlability(base_url):
    print("\n--- Technical Crawlability ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("Technical Crawlability", 0, 5)
        return

    tc_score = 0
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        canonical_url = urljoin(base_url, canonical["href"])
        if canonical_url.rstrip("/") != base_url.rstrip("/"):
            canon_resp = fetch(canonical_url)
            if canon_resp and canon_resp.status_code == 200:
                canon_soup = BeautifulSoup(canon_resp.text, "html.parser")
                canon2 = canon_soup.find("link", rel="canonical")
                if canon2 and canon2.get("href"):
                    canon2_url = urljoin(canonical_url, canon2["href"])
                    if canon2_url.rstrip("/") != canonical_url.rstrip("/"):
                        print(f"  [{WARN}] Canonical chain detected: {base_url} -> {canonical_url} -> {canon2_url}")
                        fix("Fix the canonical chain — each page's canonical should point directly to the final URL, not through intermediaries.\nSet the canonical on each page to its own URL or the ultimate target.")
                    else:
                        print(f"  [{PASS}] Canonical URL resolves correctly")
                        tc_score += 1.5
                else:
                    print(f"  [{PASS}] Canonical URL resolves correctly")
                    tc_score += 1.5
            else:
                print(f"  [{FAIL}] Canonical URL {canonical_url} returns error")
                fix(f"The canonical URL {canonical_url} is broken. Either fix the target page or update the canonical to a working URL.")
        else:
            print(f"  [{PASS}] Canonical URL is self-referencing (correct)")
            tc_score += 1.5

    try:
        no_redir_resp = requests.get(base_url, allow_redirects=False, timeout=10, headers={
            "User-Agent": "GEO-Readiness-Checker/1.0"
        })
        if no_redir_resp.is_redirect:
            redir_resp = requests.get(base_url, allow_redirects=True, timeout=10, headers={
                "User-Agent": "GEO-Readiness-Checker/1.0"
            })
            num_redirects = len(redir_resp.history)
            if num_redirects > 2:
                chain = " -> ".join(r.url for r in redir_resp.history)
                print(f"  [{WARN}] Redirect chain with {num_redirects} hops: {chain} -> {redir_resp.url}")
                print(f"         Long redirect chains can cause AI crawlers to give up")
                fix("Reduce the redirect chain to a single hop (A -> B, not A -> B -> C -> D).\nUpdate your server config to redirect directly to the final destination URL.")
            elif num_redirects > 0:
                print(f"  [{PASS}] {num_redirects} redirect(s) — within acceptable range")
                tc_score += 1
        else:
            print(f"  [{PASS}] No redirects — direct access")
            tc_score += 1
    except requests.RequestException:
        print(f"  [{WARN}] Could not test redirect chain")

    try:
        import subprocess
        result = subprocess.run(
            ["curl", "-sI", "--http2", "-o", "/dev/null", "-w", "%{http_version}", base_url],
            capture_output=True, text=True, timeout=10
        )
        http_version = result.stdout.strip()
        if http_version in ("2", "3"):
            print(f"  [{PASS}] HTTP/{http_version} supported — faster crawling")
            tc_score += 1
        elif http_version:
            print(f"  [{INFO}] HTTP/{http_version} — consider upgrading to HTTP/2 or HTTP/3 for faster crawling")
            fix("Enable HTTP/2 on your server for faster crawling:\n  Nginx: listen 443 ssl http2;\n  Apache: Protocols h2 http/1.1\n  Or use a CDN like Cloudflare which enables HTTP/2 automatically.")
    except Exception:
        print(f"  [{INFO}] Could not determine HTTP version")

    feeds = soup.find_all("link", type=re.compile(r"(rss|atom)\+xml", re.IGNORECASE))
    if feeds:
        feed_urls = [f.get("href", "N/A") for f in feeds]
        print(f"  [{PASS}] RSS/Atom feed(s) found: {', '.join(feed_urls[:3])}")
        tc_score += 1.5
    else:
        feed_found = False
        for feed_path in ["/feed", "/feed.xml", "/rss.xml", "/atom.xml", "/rss", "/blog/feed"]:
            feed_url = urljoin(base_url, feed_path)
            feed_resp = fetch(feed_url, timeout=5)
            if feed_resp and feed_resp.status_code == 200 and ("<rss" in feed_resp.text or "<feed" in feed_resp.text):
                print(f"  [{PASS}] Feed found at {feed_path}")
                feed_found = True
                tc_score += 1.5
                break
        if not feed_found:
            print(f"  [{INFO}] No RSS/Atom feed found — feeds help AI engines monitor content freshness")
            fix("Add an RSS or Atom feed for your content and link to it in <head>:\n  <link rel=\"alternate\" type=\"application/rss+xml\" title=\"RSS\" href=\"/feed.xml\" />\nMost CMS platforms generate feeds automatically. For static sites, tools like eleventy-rss can help.")

    track_score("Technical Crawlability", min(tc_score, 5), 5)


# ---------------------------------------------------------------------------
# 12. Authority & Trust Signals
# ---------------------------------------------------------------------------
def check_authority_trust(base_url):
    print("\n--- Authority & Trust Signals ---")
    resp, soup = get_soup(base_url)
    if not resp:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("Authority & Trust", 0, 5)
        return

    at_score = 0

    security_headers = {
        "Strict-Transport-Security": "HSTS",
        "Content-Security-Policy": "CSP",
        "X-Content-Type-Options": "X-Content-Type-Options",
        "X-Frame-Options": "X-Frame-Options",
    }
    found_sec_headers = 0
    for header in security_headers:
        if resp.headers.get(header):
            found_sec_headers += 1

    if found_sec_headers >= 3:
        present = [h for h in security_headers if resp.headers.get(h)]
        print(f"  [{PASS}] Strong security headers ({found_sec_headers}/4): {', '.join(present)}")
        at_score += 2
    elif found_sec_headers >= 1:
        present = [h for h in security_headers if resp.headers.get(h)]
        missing = [h for h in security_headers if not resp.headers.get(h)]
        print(f"  [{WARN}] Some security headers present ({found_sec_headers}/4): {', '.join(present)}")
        at_score += 1
        print(f"         Missing: {', '.join(missing)}")
        fix("Add missing security headers to your server config:\n  Strict-Transport-Security: max-age=31536000; includeSubDomains\n  Content-Security-Policy: default-src 'self'\n  X-Content-Type-Options: nosniff\n  X-Frame-Options: DENY")
    else:
        print(f"  [{FAIL}] No security headers found — reduces trust signal for AI engines")
        fix("Add security headers to your server response. In nginx:\n  add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;\n  add_header Content-Security-Policy \"default-src 'self'\" always;\n  add_header X-Content-Type-Options \"nosniff\" always;\n  add_header X-Frame-Options \"DENY\" always;")

    humans_url = urljoin(base_url, "/humans.txt")
    humans_resp = fetch(humans_url)
    if humans_resp and humans_resp.status_code == 200 and len(humans_resp.text.strip()) > 0:
        print(f"  [{PASS}] humans.txt found — authorship transparency")
        at_score += 1
    else:
        print(f"  [{INFO}] No humans.txt — optional authorship transparency file")
        fix("Create a humans.txt at your site root to signal authorship:\n  /* TEAM */\n  Name: Your Name\n  Role: Lead Developer\n  Contact: email@example.com\n  \n  /* SITE */\n  Last update: 2025/01/15\n  Standards: HTML5, CSS3\nSee humanstxt.org for the full spec.")

    if soup:
        json_ld_scripts = soup.find_all("script", type="application/ld+json")
        has_author = False
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if data.get("author") or data.get("@type") == "Person":
                        has_author = True
                    graph = data.get("@graph", [])
                    for item in graph:
                        if isinstance(item, dict) and (item.get("author") or item.get("@type") == "Person"):
                            has_author = True
            except (json.JSONDecodeError, TypeError):
                pass

        author_meta = soup.find("meta", attrs={"name": "author"})
        author_link = soup.find("link", rel="author")
        author_tag = soup.find(class_=re.compile(r"author", re.IGNORECASE))

        if has_author:
            print(f"  [{PASS}] Author markup found in structured data (JSON-LD)")
            at_score += 2
        elif author_meta or author_link:
            print(f"  [{PASS}] Author information found (meta/link tag)")
            at_score += 1.5
        elif author_tag:
            print(f"  [{INFO}] Author class detected in HTML — consider adding schema.org Person markup")
            fix("Upgrade your author attribution with JSON-LD:\n  \"author\": {\n    \"@type\": \"Person\",\n    \"name\": \"Author Name\",\n    \"url\": \"https://authorsite.com\"\n  }")
        else:
            print(f"  [{WARN}] No author attribution found — authorship signals boost AI trust (E-E-A-T)")
            fix("Add author information to boost E-E-A-T signals:\n  1. Add <meta name=\"author\" content=\"Author Name\">\n  2. Or add author to your JSON-LD structured data:\n     \"author\": {\"@type\": \"Person\", \"name\": \"Author Name\"}\n  3. For blog posts, display author name, bio, and credentials visibly on the page.")

    track_score("Authority & Trust", min(at_score, 5), 5)


# ---------------------------------------------------------------------------
# 13. AI-Specific Optimization
# ---------------------------------------------------------------------------
def check_ai_optimization(base_url):
    print("\n--- AI-Specific Optimization ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("AI Optimization", 0, 5)
        return

    ao_score = 0

    freshness_signals = []
    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    for script in json_ld_scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                for key in ["dateModified", "datePublished", "dateCreated"]:
                    if data.get(key):
                        freshness_signals.append(f"JSON-LD {key}: {data[key]}")
                graph = data.get("@graph", [])
                for item in graph:
                    if isinstance(item, dict):
                        for key in ["dateModified", "datePublished"]:
                            if item.get(key):
                                freshness_signals.append(f"JSON-LD {key}: {item[key]}")
        except (json.JSONDecodeError, TypeError):
            pass

    time_tags = soup.find_all("time", datetime=True)
    if time_tags:
        freshness_signals.append(f"{len(time_tags)} <time> tag(s)")

    last_modified = resp.headers.get("Last-Modified")
    if last_modified:
        freshness_signals.append(f"Last-Modified header: {last_modified}")

    if freshness_signals:
        print(f"  [{PASS}] Content freshness signals found:")
        ao_score += 2
        for sig in freshness_signals[:5]:
            print(f"         {sig}")
    else:
        print(f"  [{WARN}] No content freshness signals — add dateModified to JSON-LD or <time> elements")
        fix("Add freshness signals so AI engines know your content is current:\n  1. Add dateModified to your JSON-LD: \"dateModified\": \"2025-01-15\"\n  2. Use <time> tags: <time datetime=\"2025-01-15\">January 15, 2025</time>\n  3. Set Last-Modified HTTP header on your server")

    title = soup.find("title")
    og_site = soup.find("meta", property="og:site_name")
    site_names = set()
    if title and title.string:
        parts = re.split(r'[|\-\u2013\u2014]', title.string)
        if len(parts) > 1:
            site_names.add(parts[-1].strip())
    if og_site and og_site.get("content"):
        site_names.add(og_site["content"].strip())

    if len(site_names) > 1:
        print(f"  [{WARN}] Inconsistent site name across tags: {', '.join(site_names)}")
        fix(f"Use the same brand name everywhere. Ensure og:site_name, the title tag suffix,\nand JSON-LD Organization name all use the exact same string.\nPick one: {' or '.join(repr(n) for n in site_names)}")
    elif site_names:
        name = list(site_names)[0]
        text = get_text_content(soup)
        occurrences = text.lower().count(name.lower())
        if occurrences >= 2:
            print(f"  [{PASS}] Brand entity \"{name}\" used consistently ({occurrences} occurrences)")
            ao_score += 1.5
        else:
            print(f"  [{INFO}] Brand entity \"{name}\" found but used sparingly — consistent naming helps AI entity recognition")
            fix(f"Use your brand name \"{name}\" more consistently throughout the page content.\nMention it in headings, intro paragraphs, and structured data to strengthen entity recognition.")
    else:
        print(f"  [{INFO}] Could not determine primary brand/entity name")
        fix("Make your brand name discoverable by adding:\n  <meta property=\"og:site_name\" content=\"Your Brand\" />\nAnd use a consistent 'Brand — Page Title' format in your <title> tags.")

    api_paths = [
        "/openapi.json", "/openapi.yaml", "/swagger.json",
        "/api-docs", "/api/v1", "/graphql",
    ]
    api_found = False
    for path in api_paths:
        api_url = urljoin(base_url, path)
        api_resp = fetch(api_url, timeout=5)
        if api_resp and api_resp.status_code == 200:
            print(f"  [{PASS}] Machine-readable endpoint found: {path}")
            api_found = True
            ao_score += 1.5
            break
    if not api_found:
        print(f"  [{INFO}] No public API endpoints found — optional, but helps AI systems access structured data")

    track_score("AI Optimization", min(ao_score, 5), 5)


# ---------------------------------------------------------------------------
# 14. Social Signals
# ---------------------------------------------------------------------------
def check_social_signals(base_url):
    """Check for social media presence signals that help AI entity recognition."""
    print("\n--- Social Signals ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("Social Signals", 0, 3)
        return

    ss_score = 0

    # Twitter/X card meta tags
    twitter_tags = soup.find_all("meta", attrs={"name": re.compile(r"^twitter:", re.IGNORECASE)})
    if not twitter_tags:
        twitter_tags = soup.find_all("meta", property=re.compile(r"^twitter:", re.IGNORECASE))
    if twitter_tags:
        tw_types = [t.get("name") or t.get("property") for t in twitter_tags]
        print(f"  [{PASS}] Twitter/X card tags found: {', '.join(tw_types)}")
        ss_score += 1
    else:
        print(f"  [{WARN}] No Twitter/X card meta tags found")
        fix("Add Twitter card tags to your <head>:\n  <meta name=\"twitter:card\" content=\"summary_large_image\" />\n  <meta name=\"twitter:site\" content=\"@yourhandle\" />\n  <meta name=\"twitter:title\" content=\"Page Title\" />\n  <meta name=\"twitter:description\" content=\"Page description\" />\n  <meta name=\"twitter:image\" content=\"https://yoursite.com/image.jpg\" />")

    # sameAs links in JSON-LD (social profiles for entity disambiguation)
    same_as_links = []
    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    for script in json_ld_scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                sa = data.get("sameAs", [])
                if isinstance(sa, str):
                    same_as_links.append(sa)
                elif isinstance(sa, list):
                    same_as_links.extend(sa)
                for item in data.get("@graph", []):
                    if isinstance(item, dict):
                        sa = item.get("sameAs", [])
                        if isinstance(sa, str):
                            same_as_links.append(sa)
                        elif isinstance(sa, list):
                            same_as_links.extend(sa)
        except (json.JSONDecodeError, TypeError):
            pass

    if same_as_links:
        print(f"  [{PASS}] sameAs social links in JSON-LD ({len(same_as_links)}):")
        ss_score += 2
        for link in same_as_links[:5]:
            print(f"         {link}")
    else:
        print(f"  [{WARN}] No sameAs social profile links in structured data")
        fix("Add sameAs to your Organization JSON-LD to connect your social profiles:\n  \"sameAs\": [\n    \"https://twitter.com/yourbrand\",\n    \"https://linkedin.com/company/yourbrand\",\n    \"https://github.com/yourbrand\",\n    \"https://facebook.com/yourbrand\"\n  ]\nThis helps AI engines confirm your entity identity across platforms.")

    # Check for social profile links in HTML (fallback)
    if not same_as_links:
        social_domains = ["twitter.com", "x.com", "linkedin.com", "facebook.com",
                          "instagram.com", "youtube.com", "github.com", "tiktok.com"]
        social_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            for domain in social_domains:
                if domain in href:
                    social_links.append(href)
                    break
        if social_links:
            print(f"  [{INFO}] {len(social_links)} social profile link(s) found in HTML — consider adding them as sameAs in JSON-LD too")
        else:
            print(f"  [{INFO}] No social profile links detected on the page")

    track_score("Social Signals", min(ss_score, 3), 3)


# ---------------------------------------------------------------------------
# 15. AI Answer Format Optimization
# ---------------------------------------------------------------------------
def check_ai_answer_formats(base_url):
    """Check for content patterns that AI engines prefer to cite."""
    print("\n--- AI Answer Format Optimization ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("AI Answer Formats", 0, 5)
        return

    text = get_text_content(soup)
    score = 0
    total_checks = 5

    # 1. Definition sentences ("X is...", "X refers to...")
    definition_patterns = re.findall(
        r'(?:^|\.\s+)([A-Z][^.]{5,60}?\s+(?:is|are|refers to|means|describes)\s+[^.]{10,}\.)',
        text
    )
    if definition_patterns:
        score += 1
        print(f"  [{PASS}] {len(definition_patterns)} definition-style sentence(s) found — highly citable by AI")
    else:
        print(f"  [{WARN}] No definition-style sentences detected")
        fix("Add clear definition sentences that AI engines can directly quote:\n  'Generative Engine Optimization (GEO) is the practice of optimizing web content...'\n  'A sitemap refers to a file that lists all pages on a website...'\nPattern: '[Term] is/are [clear definition].'")

    # 2. Comparison tables
    tables = soup.find_all("table")
    has_comparison_table = False
    for table in tables:
        headers = table.find_all("th")
        if len(headers) >= 2:
            has_comparison_table = True
            break
    if has_comparison_table:
        score += 1
        print(f"  [{PASS}] Comparison table(s) with headers found — AI engines extract tabular data")
    else:
        if tables:
            print(f"  [{WARN}] Tables found but missing <th> headers — add headers for AI extraction")
            fix("Add proper headers to your tables:\n  <table>\n    <thead><tr><th>Feature</th><th>Plan A</th><th>Plan B</th></tr></thead>\n    <tbody><tr><td>Price</td><td>$10</td><td>$20</td></tr></tbody>\n  </table>\nAI engines extract well-structured tables for comparison answers.")
        else:
            print(f"  [{INFO}] No comparison tables — consider adding tables for feature comparisons, pricing, etc.")
            fix("Add comparison tables where applicable (pricing, features, vs. competitors):\n  <table>\n    <thead><tr><th>Feature</th><th>Basic</th><th>Pro</th></tr></thead>\n    <tbody>...</tbody>\n  </table>\nAI engines frequently cite tabular data in comparison answers.")

    # 3. Numbered step-by-step instructions
    ordered_lists = soup.find_all("ol")
    has_steps = False
    for ol in ordered_lists:
        items = ol.find_all("li")
        if len(items) >= 3:
            has_steps = True
            break
    # Also check for "Step 1", "Step 2" patterns in headings
    step_headings = [h for h in soup.find_all(re.compile(r"^h[1-6]$"))
                     if re.search(r'step\s+\d|^\d+[\.\)]\s', h.get_text(strip=True), re.IGNORECASE)]
    if has_steps or step_headings:
        score += 1
        print(f"  [{PASS}] Step-by-step instructional content detected — great for 'how to' AI answers")
    else:
        print(f"  [{INFO}] No step-by-step instructions found")
        fix("Add numbered how-to instructions where relevant:\n  <h2>How to Set Up Your Account</h2>\n  <ol>\n    <li>Go to the signup page</li>\n    <li>Enter your email address</li>\n    <li>Verify your account</li>\n  </ol>\nAI engines surface step-by-step content for 'how to' queries.")

    # 4. Pros and cons / advantages and disadvantages
    pros_cons_patterns = re.findall(
        r'(?:pros?\s+(?:and|&)\s+cons?|advantages?\s+(?:and|&)\s+disadvantages?|benefits?\s+(?:and|&)\s+drawbacks?)',
        text, re.IGNORECASE
    )
    pros_cons_elements = soup.find_all(class_=re.compile(r"pros?|cons?|advantage|disadvantage", re.IGNORECASE))
    if pros_cons_patterns or pros_cons_elements:
        score += 1
        print(f"  [{PASS}] Pros/cons or advantages/disadvantages content detected")
    else:
        print(f"  [{INFO}] No pros/cons pattern detected")
        fix("Add pros and cons sections for products, services, or comparisons:\n  <h3>Pros</h3>\n  <ul><li>Fast performance</li><li>Easy to use</li></ul>\n  <h3>Cons</h3>\n  <ul><li>Limited free tier</li><li>No mobile app</li></ul>\nAI engines frequently cite balanced pros/cons in recommendation answers.")

    # 5. Key takeaways / TL;DR / summary sections
    summary_indicators = soup.find_all(
        re.compile(r"^h[1-6]$"),
        string=re.compile(r"key\s+takeaway|tl;?\s*dr|summary|in\s+(?:a\s+)?nutshell|bottom\s+line|conclusion", re.IGNORECASE)
    )
    summary_classes = soup.find_all(class_=re.compile(r"takeaway|tldr|summary|highlight", re.IGNORECASE))
    if summary_indicators or summary_classes:
        score += 1
        print(f"  [{PASS}] Summary/key takeaways section found — AI engines prefer concise summaries")
    else:
        print(f"  [{INFO}] No key takeaways or TL;DR section found")
        fix("Add a 'Key Takeaways' or 'TL;DR' section near the top or bottom:\n  <h2>Key Takeaways</h2>\n  <ul>\n    <li>Main point 1</li>\n    <li>Main point 2</li>\n  </ul>\nAI engines often pull from summary sections for quick answers.")

    print(f"\n  AI answer format score: {score}/{total_checks}")
    track_score("AI Answer Formats", score, total_checks)


# ---------------------------------------------------------------------------
# 16. Schema Breadcrumbs & Knowledge Panel Readiness
# ---------------------------------------------------------------------------
def check_schema_knowledge(base_url):
    """Check for BreadcrumbList schema and knowledge panel readiness."""
    print("\n--- Schema Breadcrumbs & Knowledge Panel ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("Schema & Knowledge", 0, 4)
        return

    sk_score = 0

    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    all_types = []
    org_data = None

    for script in json_ld_scripts:
        try:
            data = json.loads(script.string)
            items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
            # Also check @graph
            if isinstance(data, dict) and "@graph" in data:
                items.extend(data["@graph"])
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type", "")
                if isinstance(t, list):
                    all_types.extend(t)
                else:
                    all_types.append(t)
                if t in ("Organization", "LocalBusiness", "Corporation"):
                    org_data = item
        except (json.JSONDecodeError, TypeError):
            pass

    # Breadcrumbs
    if "BreadcrumbList" in all_types:
        print(f"  [{PASS}] BreadcrumbList schema found — helps AI engines understand site hierarchy")
        sk_score += 1.5
    else:
        # Check for HTML breadcrumb nav
        breadcrumb_nav = soup.find(attrs={"aria-label": re.compile(r"breadcrumb", re.IGNORECASE)})
        breadcrumb_class = soup.find(class_=re.compile(r"breadcrumb", re.IGNORECASE))
        if breadcrumb_nav or breadcrumb_class:
            print(f"  [{WARN}] HTML breadcrumb navigation found but no BreadcrumbList schema")
            fix("Add BreadcrumbList structured data to match your HTML breadcrumbs:\n  <script type=\"application/ld+json\">\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"BreadcrumbList\",\n    \"itemListElement\": [\n      {\"@type\": \"ListItem\", \"position\": 1, \"name\": \"Home\", \"item\": \"https://yoursite.com\"},\n      {\"@type\": \"ListItem\", \"position\": 2, \"name\": \"Products\", \"item\": \"https://yoursite.com/products\"}\n    ]\n  }\n  </script>")
        else:
            print(f"  [{INFO}] No breadcrumb navigation or schema found")
            fix("Add breadcrumb navigation to help AI engines understand your site structure:\n  1. Add visible breadcrumbs: Home > Category > Page\n  2. Add BreadcrumbList JSON-LD schema to match")

    # Knowledge panel readiness — check Organization/LocalBusiness completeness
    if org_data:
        print(f"  [{PASS}] Organization/Business schema found: @type = {org_data.get('@type')}")
        sk_score += 1
        required_fields = {
            "name": "Organization name",
            "url": "Website URL",
            "logo": "Logo image",
            "description": "Description",
        }
        optional_fields = {
            "address": "Physical address",
            "telephone": "Phone number",
            "email": "Email contact",
            "foundingDate": "Founding date",
            "sameAs": "Social profiles",
            "contactPoint": "Contact point",
        }

        for field, label in required_fields.items():
            if org_data.get(field):
                print(f"  [{PASS}] {label}: present")
                sk_score += 0.375  # 4 fields * 0.375 = 1.5 pts max
            else:
                print(f"  [{WARN}] {label}: missing")
                fix(f"Add \"{field}\" to your Organization JSON-LD to improve knowledge panel eligibility.")

        present_optional = [label for field, label in optional_fields.items() if org_data.get(field)]
        missing_optional = [label for field, label in optional_fields.items() if not org_data.get(field)]
        if present_optional:
            print(f"  [{PASS}] Optional fields present: {', '.join(present_optional)}")
        if missing_optional:
            print(f"  [{INFO}] Optional fields missing: {', '.join(missing_optional)}")
            fix("Add more fields to strengthen knowledge panel eligibility:\n  \"address\": {\"@type\": \"PostalAddress\", \"streetAddress\": \"...\", \"addressLocality\": \"...\"},\n  \"telephone\": \"+1-xxx-xxx-xxxx\",\n  \"foundingDate\": \"2020\",\n  \"sameAs\": [\"https://twitter.com/...\", \"https://linkedin.com/...\"]")
    else:
        print(f"  [{WARN}] No Organization/LocalBusiness schema found — needed for knowledge panels")
        fix("Add Organization structured data for knowledge panel eligibility:\n  <script type=\"application/ld+json\">\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"Organization\",\n    \"name\": \"Your Company\",\n    \"url\": \"https://yoursite.com\",\n    \"logo\": \"https://yoursite.com/logo.png\",\n    \"description\": \"What your company does\",\n    \"sameAs\": [\"https://twitter.com/you\", \"https://linkedin.com/company/you\"]\n  }\n  </script>")

    track_score("Schema & Knowledge", min(sk_score, 4), 4)


# ---------------------------------------------------------------------------
# 17. Mobile-Friendliness & Page Weight
# ---------------------------------------------------------------------------
def check_mobile_and_weight(base_url):
    """Check mobile-friendliness signals and page weight."""
    print("\n--- Mobile-Friendliness & Page Weight ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.mobile.fetch_failed", "Could not fetch homepage")
        track_score("Mobile & Weight", 0, 4)
        return

    mw_score = 0

    # Viewport meta tag
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if viewport and viewport.get("content"):
        content = viewport["content"]
        preview = content[:80]
        emit_check(PASS, "result.checks.mobile.viewport_found", f"Viewport meta tag found: {preview}", {"viewport": preview})
        if "width=device-width" in content:
            emit_check(PASS, "result.checks.mobile.viewport_responsive", "Uses width=device-width (responsive)")
            mw_score += 1
        else:
            emit_check(WARN, "result.checks.mobile.viewport_not_responsive", "Viewport doesn't use width=device-width")
            fix("Set viewport to responsive:\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />")
    else:
        emit_check(FAIL, "result.checks.mobile.viewport_missing", "No viewport meta tag — page won't render properly on mobile")
        fix("Add a viewport meta tag to your <head>:\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\nMobile-first indexing means AI crawlers expect mobile-friendly pages.")

    # Page weight
    html_size = len(resp.text.encode("utf-8"))
    html_kb = html_size / 1024
    if html_kb < 100:
        emit_check(PASS, "result.checks.mobile.weight_light", f"HTML page weight: {html_kb:.0f} KB (lightweight)", {"kb": int(html_kb)})
        mw_score += 1
    elif html_kb < 500:
        emit_check(WARN, "result.checks.mobile.weight_medium", f"HTML page weight: {html_kb:.0f} KB — consider reducing inline CSS/JS", {"kb": int(html_kb)})
        fix("Reduce page weight:\n  1. Move inline CSS to external stylesheets\n  2. Move inline JS to external scripts with defer/async\n  3. Remove unused HTML/comments\n  4. Enable server-side compression (gzip/brotli)")
    else:
        emit_check(FAIL, "result.checks.mobile.weight_heavy", f"HTML page weight: {html_kb:.0f} KB — very heavy, may slow AI crawlers", {"kb": int(html_kb)})
        fix("Page is too heavy for efficient crawling. Actions:\n  1. Externalize all inline CSS and JavaScript\n  2. Remove inline SVGs and base64 images — use external files\n  3. Enable gzip/brotli compression on your server\n  4. Consider code-splitting for JavaScript-heavy pages")

    # Count inline resources
    inline_styles = soup.find_all("style")
    inline_scripts = soup.find_all("script", src=False)
    inline_scripts = [s for s in inline_scripts if s.string and len(s.string.strip()) > 100 and s.get("type") != "application/ld+json"]

    if len(inline_styles) > 3 or len(inline_scripts) > 5:
        emit_check(WARN, "result.checks.mobile.inline_heavy", f"Heavy inline resources: {len(inline_styles)} <style> blocks, {len(inline_scripts)} large <script> blocks", {"styles": len(inline_styles), "scripts": len(inline_scripts)})
        fix("Move inline styles and scripts to external files to reduce HTML weight\nand improve caching for repeat crawls.")
    else:
        emit_check(PASS, "result.checks.mobile.inline_ok", "Inline resources within acceptable range")
        mw_score += 1

    # Cache headers
    cache_control = resp.headers.get("Cache-Control", "")
    etag = resp.headers.get("ETag", "")
    last_modified = resp.headers.get("Last-Modified", "")

    cache_signals = []
    if cache_control:
        cache_signals.append(f"Cache-Control: {cache_control}")
    if etag:
        cache_signals.append(f"ETag: {etag[:40]}")
    if last_modified:
        cache_signals.append(f"Last-Modified: {last_modified}")

    if cache_signals:
        signals_text = "; ".join(cache_signals[:2])
        emit_check(PASS, "result.checks.mobile.cache_headers_found", f"Cache headers found: {signals_text}", {"signals": signals_text})
        mw_score += 1
    else:
        emit_check(WARN, "result.checks.mobile.cache_headers_missing", "No cache headers (Cache-Control, ETag, Last-Modified)")
        fix("Add cache headers for efficient re-crawling:\n  Cache-Control: public, max-age=3600\n  ETag: (auto-generated by most servers)\nThis allows AI crawlers to use conditional requests (If-None-Match)\nand avoid re-downloading unchanged pages.")

    track_score("Mobile & Weight", mw_score, 4)


# ---------------------------------------------------------------------------
# 18. URL Normalization
# ---------------------------------------------------------------------------
def check_url_normalization(base_url):
    """Check for URL consistency (www vs non-www, trailing slashes, etc.)."""
    print("\n--- URL Normalization ---")
    un_score = 0

    parsed = urlparse(base_url)
    hostname = parsed.netloc

    # www vs non-www
    if hostname.startswith("www."):
        alt_host = hostname[4:]
        alt_url = base_url.replace(f"www.{alt_host}", alt_host, 1)
    else:
        alt_host = f"www.{hostname}"
        alt_url = base_url.replace(hostname, alt_host, 1)

    try:
        alt_resp = requests.get(alt_url, allow_redirects=True, timeout=10, headers={
            "User-Agent": "GEO-Readiness-Checker/1.0"
        })
        final_url = alt_resp.url.rstrip("/")
        base_stripped = base_url.rstrip("/")

        if final_url == base_stripped or final_url == base_stripped + "/":
            print(f"  [{PASS}] {alt_host} redirects to {hostname} (consistent)")
            un_score += 1
        elif alt_resp.status_code == 200:
            print(f"  [{WARN}] Both {hostname} and {alt_host} serve content — duplicate content risk")
            fix(f"Set up a 301 redirect so one version redirects to the other:\n  # Nginx: redirect www to non-www\n  server {{ server_name www.{parsed.netloc.replace('www.', '')}; return 301 https://{parsed.netloc.replace('www.', '')}$request_uri; }}\nThen set the canonical URL to match the preferred version.")
        else:
            print(f"  [{PASS}] Alternate hostname ({alt_host}) is not accessible")
            un_score += 1
    except requests.RequestException:
        print(f"  [{PASS}] Alternate hostname ({alt_host}) is not accessible")
        un_score += 1

    # Trailing slash consistency
    test_path = base_url.rstrip("/")
    test_path_slash = test_path + "/"
    try:
        resp_no_slash = requests.get(test_path, allow_redirects=False, timeout=5, headers={
            "User-Agent": "GEO-Readiness-Checker/1.0"
        })
        resp_slash = requests.get(test_path_slash, allow_redirects=False, timeout=5, headers={
            "User-Agent": "GEO-Readiness-Checker/1.0"
        })

        if resp_no_slash.status_code == resp_slash.status_code == 200:
            print(f"  [{INFO}] Both trailing slash and non-trailing slash return 200 — ensure canonical is set")
        elif resp_no_slash.is_redirect or resp_slash.is_redirect:
            print(f"  [{PASS}] Trailing slash consistency handled via redirect")
            un_score += 0.5
        else:
            print(f"  [{PASS}] URL paths are consistent")
            un_score += 0.5
    except requests.RequestException:
        pass

    # Mixed case check
    upper_url = base_url.upper()
    if upper_url != base_url:
        try:
            upper_resp = requests.get(upper_url, allow_redirects=True, timeout=5, headers={
                "User-Agent": "GEO-Readiness-Checker/1.0"
            })
            if upper_resp.status_code == 200 and upper_resp.url.rstrip("/") != base_url.rstrip("/"):
                print(f"  [{WARN}] Mixed case URLs resolve to different pages — can cause duplicate content")
                fix("Ensure your server normalizes URL case (lowercase). In nginx:\n  location ~ [A-Z] { rewrite ^(.*)$ $scheme://$host$uri_lowercase permanent; }")
            else:
                print(f"  [{PASS}] URL case handling is consistent")
                un_score += 0.5
        except requests.RequestException:
            pass

    track_score("URL Normalization", min(un_score, 2), 2)


# ---------------------------------------------------------------------------
# 19. Outbound Link Quality & Media Schema
# ---------------------------------------------------------------------------
def check_outbound_and_media(base_url):
    """Check outbound link quality and video/media structured data."""
    print("\n--- Outbound Links & Media ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        track_score("Outbound & Media", 0, 3)
        return

    om_score = 0

    # Outbound link analysis
    parsed_base = urlparse(base_url)
    links = soup.find_all("a", href=True)
    outbound_links = []
    for link in links:
        href = link["href"]
        parsed_href = urlparse(urljoin(base_url, href))
        if parsed_href.netloc and parsed_href.netloc != parsed_base.netloc:
            outbound_links.append(parsed_href.netloc)

    if outbound_links:
        unique_domains = list(dict.fromkeys(outbound_links))
        authoritative_domains = [d for d in unique_domains if any(
            auth in d for auth in [".gov", ".edu", ".org", "wikipedia", "scholar.google",
                                    "nature.com", "ieee.org", "arxiv.org", "ncbi.nlm.nih"]
        )]
        print(f"  [{PASS}] {len(outbound_links)} outbound link(s) to {len(unique_domains)} unique domain(s)")
        om_score += 0.5
        if authoritative_domains:
            print(f"  [{PASS}] Links to authoritative sources: {', '.join(authoritative_domains[:5])}")
            om_score += 0.5
        else:
            print(f"  [{INFO}] No links to .gov/.edu/.org authoritative sources detected")
            fix("Link to authoritative external sources where relevant (research papers, .gov/.edu sites,\nindustry standards). Outbound links to reputable sources signal well-researched content to AI engines.")
    else:
        print(f"  [{INFO}] No outbound links found — linking to authoritative sources increases content trust")
        fix("Add outbound links to reputable, authoritative sources that support your claims.\nAI engines see this as a signal of well-researched, trustworthy content.")

    # Video / media schema
    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    has_video_schema = False
    for script in json_ld_scripts:
        try:
            data = json.loads(script.string)
            items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
            if isinstance(data, dict) and "@graph" in data:
                items.extend(data["@graph"])
            for item in items:
                if isinstance(item, dict) and item.get("@type") in ("VideoObject", "AudioObject", "MediaObject"):
                    has_video_schema = True
        except (json.JSONDecodeError, TypeError):
            pass

    # Check for video embeds
    videos = soup.find_all(["video", "iframe"])
    video_embeds = [v for v in videos if v.name == "video" or
                    (v.get("src") and any(p in v.get("src", "") for p in ["youtube", "vimeo", "wistia"]))]

    if has_video_schema:
        print(f"  [{PASS}] VideoObject structured data found")
        om_score += 0.5
    elif video_embeds:
        print(f"  [{WARN}] Video content found ({len(video_embeds)} embed(s)) but no VideoObject schema")
        fix("Add VideoObject structured data for your video content:\n  <script type=\"application/ld+json\">\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"VideoObject\",\n    \"name\": \"Video Title\",\n    \"description\": \"Video description\",\n    \"thumbnailUrl\": \"https://yoursite.com/thumb.jpg\",\n    \"uploadDate\": \"2025-01-15\",\n    \"contentUrl\": \"https://yoursite.com/video.mp4\"\n  }\n  </script>")
    else:
        print(f"  [{INFO}] No video content detected")

    # Check for video transcripts
    if video_embeds:
        transcript_indicators = soup.find_all(class_=re.compile(r"transcript", re.IGNORECASE))
        transcript_indicators += soup.find_all(id=re.compile(r"transcript", re.IGNORECASE))
        if transcript_indicators:
            print(f"  [{PASS}] Video transcript section found — AI engines can index transcript text")
        else:
            print(f"  [{WARN}] Videos found but no transcript detected")
            fix("Add text transcripts for video content so AI crawlers can index the spoken content.\nPlace the transcript in a visible section below the video.")

    # Table markup quality
    tables = soup.find_all("table")
    if tables:
        well_formed = 0
        for table in tables:
            has_thead = bool(table.find("thead"))
            has_th = bool(table.find("th"))
            if has_thead and has_th:
                well_formed += 1
        if well_formed == len(tables):
            print(f"  [{PASS}] {len(tables)} table(s) with proper <thead>/<th> markup")
            om_score += 0.5
        elif well_formed > 0:
            print(f"  [{WARN}] {well_formed}/{len(tables)} tables have proper headers — fix the rest")
            fix("Add <thead> and <th> to all data tables:\n  <table>\n    <thead><tr><th>Column 1</th><th>Column 2</th></tr></thead>\n    <tbody><tr><td>Data</td><td>Data</td></tr></tbody>\n  </table>")
        else:
            print(f"  [{WARN}] {len(tables)} table(s) but none have proper <thead>/<th> headers")
            fix("Add semantic headers to your tables for AI extraction:\n  <table>\n    <thead><tr><th>Header 1</th><th>Header 2</th></tr></thead>\n    <tbody>...</tbody>\n  </table>")
    else:
        print(f"  [{INFO}] No tables found on homepage")

    # Definition elements
    dfn_tags = soup.find_all("dfn")
    abbr_tags = soup.find_all("abbr")
    if dfn_tags or abbr_tags:
        print(f"  [{PASS}] Definition markup found: {len(dfn_tags)} <dfn>, {len(abbr_tags)} <abbr> tags")
        om_score += 0.5
    else:
        print(f"  [{INFO}] No <dfn> or <abbr> tags — use these to mark up technical terms and abbreviations")
        fix("Mark up key terms and abbreviations:\n  <dfn>Generative Engine Optimization</dfn> (GEO) is...\n  <abbr title=\"Generative Engine Optimization\">GEO</abbr>\nThis helps AI engines understand and define terms in your content.")

    track_score("Outbound & Media", min(om_score, 3), 3)


# ---------------------------------------------------------------------------
# 20. Multilingual Content Depth
# ---------------------------------------------------------------------------
def check_multilingual_depth(base_url):
    """Check if alternate language pages actually exist and have content."""
    print("\n--- Multilingual Content Depth ---")
    resp, soup = get_soup(base_url)
    if not soup:
        print(f"  [{FAIL}] Could not fetch homepage")
        return

    hreflangs = soup.find_all("link", rel="alternate", hreflang=True)
    if not hreflangs:
        print(f"  [{INFO}] No hreflang tags — skipping multilingual check")
        return

    print(f"  Found {len(hreflangs)} hreflang tag(s), checking content depth...\n")

    thin_pages = []
    broken_pages = []
    good_pages = []

    for tag in hreflangs[:8]:  # Limit to 8 to avoid too many requests
        lang = tag.get("hreflang", "?")
        href = urljoin(base_url, tag.get("href", ""))

        if href.rstrip("/") == base_url.rstrip("/"):
            continue  # Skip self-reference

        alt_resp = fetch(href, timeout=10)
        if not alt_resp or alt_resp.status_code != 200:
            broken_pages.append((lang, href))
            continue

        alt_soup = BeautifulSoup(alt_resp.text, "html.parser")
        alt_text = get_text_content(alt_soup)
        word_count = len(alt_text.split())

        if word_count < 50:
            thin_pages.append((lang, href, word_count))
        else:
            good_pages.append((lang, word_count))

    if good_pages:
        for lang, wc in good_pages:
            print(f"  [{PASS}] [{lang}] has substantive content ({wc} words)")

    if thin_pages:
        for lang, href, wc in thin_pages:
            print(f"  [{WARN}] [{lang}] has very thin content ({wc} words): {href}")
        fix("Alternate language pages have too little content. Ensure translations are complete\nand not just stubs or machine-translated snippets. AI engines may skip thin multilingual pages.")

    if broken_pages:
        for lang, href in broken_pages:
            print(f"  [{FAIL}] [{lang}] page is broken or inaccessible: {href}")
        fix("Fix broken hreflang URLs — they return errors. Either create the page\nor remove the hreflang tag to avoid confusing AI crawlers.")

    if not thin_pages and not broken_pages and good_pages:
        print(f"  [{PASS}] All alternate language pages have substantive content")
        track_score("Multilingual", 2, 2)
    elif good_pages and not broken_pages:
        track_score("Multilingual", 1, 2)
    elif good_pages:
        track_score("Multilingual", 0.5, 2)
    else:
        track_score("Multilingual", 0, 2)


# ---------------------------------------------------------------------------
# 21. Cross-Platform Content Distribution
# ---------------------------------------------------------------------------
def check_cross_platform(base_url):
    """Check if the brand has presence on major social/content platforms that AI models train on."""
    print("\n--- Cross-Platform Content Distribution ---")
    resp, soup = get_soup(base_url)

    parsed = urlparse(base_url)
    domain = parsed.netloc.replace("www.", "")
    brand = domain.split(".")[0]

    # Platform definitions: name -> (url_patterns_to_check, on_page_signal_domains)
    platforms = {
        "X / Twitter":  {
            "probe_urls": [f"https://x.com/{brand}", f"https://twitter.com/{brand}"],
            "link_domains": ["x.com", "twitter.com"],
            "meta_signal": "twitter:site",
        },
        "LinkedIn":     {
            "probe_urls": [f"https://www.linkedin.com/company/{brand}"],
            "link_domains": ["linkedin.com"],
        },
        "YouTube":      {
            "probe_urls": [f"https://www.youtube.com/@{brand}", f"https://www.youtube.com/c/{brand}"],
            "link_domains": ["youtube.com"],
        },
        "GitHub":       {
            "probe_urls": [f"https://github.com/{brand}"],
            "link_domains": ["github.com"],
        },
        "Reddit":       {
            "probe_urls": [f"https://www.reddit.com/r/{brand}", f"https://www.reddit.com/user/{brand}"],
            "link_domains": ["reddit.com"],
        },
        "Facebook":     {
            "probe_urls": [f"https://www.facebook.com/{brand}"],
            "link_domains": ["facebook.com"],
        },
        "Instagram":    {
            "probe_urls": [f"https://www.instagram.com/{brand}"],
            "link_domains": ["instagram.com"],
        },
        "Medium":       {
            "probe_urls": [f"https://medium.com/@{brand}", f"https://{brand}.medium.com"],
            "link_domains": ["medium.com"],
        },
        "TikTok":       {
            "probe_urls": [f"https://www.tiktok.com/@{brand}"],
            "link_domains": ["tiktok.com"],
        },
        "Quora":        {
            "probe_urls": [f"https://www.quora.com/profile/{brand}"],
            "link_domains": ["quora.com"],
        },
    }

    # --- Phase 1: Gather on-page signals ---
    on_page_links = {}   # platform_name -> url found on page
    same_as_links = []

    if soup:
        # Collect all sameAs from JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
                if isinstance(data, dict) and "@graph" in data:
                    items.extend(data["@graph"])
                for item in items:
                    if isinstance(item, dict):
                        sa = item.get("sameAs", [])
                        if isinstance(sa, str):
                            same_as_links.append(sa)
                        elif isinstance(sa, list):
                            same_as_links.extend(sa)
            except (json.JSONDecodeError, TypeError):
                pass

        # Collect all outbound links
        all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
        all_hrefs.extend(same_as_links)

        # Match links to platforms
        for plat_name, plat_info in platforms.items():
            for href in all_hrefs:
                href_lower = href.lower()
                for link_domain in plat_info["link_domains"]:
                    if link_domain in href_lower:
                        on_page_links[plat_name] = href
                        break
                if plat_name in on_page_links:
                    break

        # Check meta signals (e.g. twitter:site)
        for plat_name, plat_info in platforms.items():
            if plat_name in on_page_links:
                continue
            meta_key = plat_info.get("meta_signal")
            if meta_key:
                tag = soup.find("meta", attrs={"name": meta_key}) or soup.find("meta", attrs={"property": meta_key})
                if tag and tag.get("content"):
                    on_page_links[plat_name] = tag["content"]

    # --- Phase 2: Probe platforms not found on-page ---
    probed = {}  # platform_name -> (found: bool, url)
    platforms_to_probe = {k: v for k, v in platforms.items() if k not in on_page_links}

    browser_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    for plat_name, plat_info in platforms_to_probe.items():
        found = False
        for probe_url in plat_info["probe_urls"]:
            try:
                r = requests.get(probe_url, timeout=8, allow_redirects=True, headers={
                    "User-Agent": browser_ua
                })
                # Platform-specific detection logic
                if r.status_code == 200:
                    # Detect login redirects (e.g. Facebook redirects to /login/ for non-existent pages)
                    final_url = r.url.lower()
                    redirected_to_login = any(seg in final_url for seg in [
                        "/login", "/signin", "/sign_in", "/accounts/login",
                    ])

                    # Some platforms return 200 for "not found" pages, check content
                    text_lower = r.text.lower() if len(r.text) < 500000 else r.text[:500000].lower()
                    is_404_page = redirected_to_login or any(phrase in text_lower for phrase in [
                        "page not found", "this account doesn", "this page isn",
                        "nothing here", "sorry, nobody", "user not found",
                        "hmm...this page doesn", "account suspended",
                        "content isn't available", "content not available",
                        "this content is not available", "page isn't available",
                        "this page is not available", "link may be broken",
                        "profile not found", "channel not found",
                        "this user is not available", "suspended account",
                        "no results found", "couldn't find this account",
                        "we couldn't find", "does not exist",
                    ])
                    if not is_404_page:
                        probed[plat_name] = (True, probe_url)
                        found = True
                        break
            except requests.RequestException:
                pass
        if not found:
            probed[plat_name] = (False, plat_info["probe_urls"][0])

    # --- Phase 3: Report results ---
    found_platforms = []
    not_found_platforms = []

    # On-page links (highest confidence)
    for plat_name, link in sorted(on_page_links.items()):
        found_platforms.append(plat_name)
        print(f"  [{PASS}] {plat_name:<16} linked on site: {link}")

    # Probed and found
    for plat_name, (found, url) in sorted(probed.items()):
        if found:
            found_platforms.append(plat_name)
            print(f"  [{PASS}] {plat_name:<16} profile found: {url}")

    # Not found
    for plat_name, (found, url) in sorted(probed.items()):
        if not found:
            not_found_platforms.append(plat_name)
            print(f"  [{INFO}] {plat_name:<16} not detected")

    # --- Score & summary ---
    total_platforms = len(platforms)
    found_count = len(found_platforms)

    print()
    if found_count >= 6:
        print(f"  [{PASS}] Strong cross-platform presence: {found_count}/{total_platforms} platforms")
    elif found_count >= 3:
        print(f"  [{WARN}] Moderate cross-platform presence: {found_count}/{total_platforms} platforms")
    elif found_count >= 1:
        print(f"  [{WARN}] Limited cross-platform presence: {found_count}/{total_platforms} platforms")
    else:
        print(f"  [{FAIL}] No cross-platform presence detected")

    if not_found_platforms:
        fix(
            "Expand your brand presence on platforms that AI models train on:\n"
            + "".join(f"  - {p}\n" for p in not_found_platforms)
            + "AI engines (ChatGPT, Perplexity, Claude, Gemini) train on data from these platforms.\n"
            "Being present increases the probability of your brand being cited in AI answers,\n"
            "regardless of which source the AI pulls from."
        )

    # Score: 0-5 based on coverage
    if found_count >= 7:
        cp_score = 5
    elif found_count >= 5:
        cp_score = 4
    elif found_count >= 3:
        cp_score = 3
    elif found_count >= 1:
        cp_score = 1.5
    else:
        cp_score = 0
    track_score("Cross-Platform", cp_score, 5)

    # Provide the AI training context
    if found_platforms:
        print(f"\n  Platforms where your brand is visible to AI training:")
        ai_training_map = {
            "X / Twitter": "ChatGPT, Grok, Perplexity",
            "Reddit":      "ChatGPT, Claude, Gemini, Perplexity",
            "YouTube":     "Gemini, Perplexity (transcripts)",
            "LinkedIn":    "Bing Copilot, Perplexity",
            "GitHub":      "Copilot, ChatGPT, Claude",
            "Medium":      "ChatGPT, Claude, Perplexity",
            "Facebook":    "Meta AI",
            "Instagram":   "Meta AI",
            "TikTok":      "ByteDance AI",
            "Quora":       "ChatGPT, Perplexity",
        }
        for plat in found_platforms:
            ai_models = ai_training_map.get(plat, "various AI models")
            print(f"    {plat:<16} → trains: {ai_models}")


# ---------------------------------------------------------------------------
# 22. Multi-Page Sampling
# ---------------------------------------------------------------------------
def check_multi_page(base_url, sitemap_urls, max_pages=5):
    print("\n--- Multi-Page Sampling ---")

    candidates = []
    if sitemap_urls:
        candidates = sitemap_urls
    else:
        resp, soup = get_soup(base_url)
        if soup:
            parsed_base = urlparse(base_url)
            for a in soup.find_all("a", href=True):
                href = urljoin(base_url, a["href"])
                parsed_href = urlparse(href)
                if parsed_href.netloc == parsed_base.netloc and href.rstrip("/") != base_url.rstrip("/"):
                    candidates.append(href)

    if not candidates:
        print(f"  [{WARN}] No internal pages to sample")
        track_score("Multi-Page", 0, 5)
        return

    candidates = list(dict.fromkeys(candidates))
    content_candidates = [
        u for u in candidates
        if not re.search(r'\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|pdf|zip)$', u, re.IGNORECASE)
        and "#" not in u
    ]
    sample = content_candidates[:max_pages]

    if not sample:
        print(f"  [{WARN}] No content pages found to sample")
        track_score("Multi-Page", 0, 5)
        return

    print(f"  Sampling {len(sample)} internal page(s)...\n")

    issues = {
        "missing_title": [],
        "missing_description": [],
        "missing_canonical": [],
        "missing_structured_data": [],
        "missing_h1": [],
        "low_word_count": [],
        "missing_og": [],
        "missing_alt_text": [],
    }
    descriptions_seen = {}

    for page_url in sample:
        page_resp = fetch(page_url, timeout=10)
        if not page_resp or page_resp.status_code != 200:
            continue

        page_soup = BeautifulSoup(page_resp.text, "html.parser")
        short_url = urlparse(page_url).path or page_url

        title = page_soup.find("title")
        if not title or not (title.string and title.string.strip()):
            issues["missing_title"].append(short_url)

        desc = page_soup.find("meta", attrs={"name": "description"})
        desc_content = ""
        if desc and desc.get("content", "").strip():
            desc_content = desc["content"].strip()
        else:
            issues["missing_description"].append(short_url)

        if desc_content:
            descriptions_seen.setdefault(desc_content, []).append(short_url)

        canonical = page_soup.find("link", rel="canonical")
        if not canonical or not canonical.get("href"):
            issues["missing_canonical"].append(short_url)

        if not page_soup.find_all("script", type="application/ld+json"):
            issues["missing_structured_data"].append(short_url)

        if not page_soup.find("h1"):
            issues["missing_h1"].append(short_url)

        page_text = get_text_content(page_soup)
        if len(page_text.split()) < 100:
            issues["low_word_count"].append(short_url)

        if not page_soup.find("meta", property="og:title"):
            issues["missing_og"].append(short_url)

        imgs = page_soup.find_all("img")
        if imgs:
            missing_alt = sum(1 for img in imgs if not img.get("alt", "").strip())
            if missing_alt > len(imgs) * 0.5:
                issues["missing_alt_text"].append(short_url)

    duplicate_descs = {desc: pages for desc, pages in descriptions_seen.items() if len(pages) > 1}

    check_labels = {
        "missing_title": ("Missing <title>", FAIL),
        "missing_description": ("Missing meta description", FAIL),
        "missing_canonical": ("Missing canonical URL", WARN),
        "missing_structured_data": ("No structured data (JSON-LD)", WARN),
        "missing_h1": ("Missing <h1>", WARN),
        "low_word_count": ("Low word count (<100 words)", WARN),
        "missing_og": ("Missing Open Graph tags", WARN),
        "missing_alt_text": ("Most images missing alt text", WARN),
    }

    fix_suggestions = {
        "missing_title": "Add a unique, descriptive <title> tag to each page (under 60 chars).",
        "missing_description": "Add a unique meta description to each page (120-160 chars) summarizing the page content.",
        "missing_canonical": "Add <link rel=\"canonical\" href=\"...\"> to each page pointing to its preferred URL.",
        "missing_structured_data": "Add JSON-LD structured data to content pages (Article, Product, FAQPage, etc.).",
        "missing_h1": "Add a single <h1> tag to each page describing its primary topic.",
        "low_word_count": "Pages with <100 words have too little content for AI engines. Add substantive, unique text.",
        "missing_og": "Add Open Graph tags (og:title, og:description, og:image) to each page.",
        "missing_alt_text": "Add descriptive alt text to all images on these pages.",
    }

    all_good = True
    for key, (label, severity) in check_labels.items():
        pages = issues[key]
        if pages:
            all_good = False
            print(f"  [{severity}] {label} on {len(pages)} page(s):")
            for p in pages[:3]:
                print(f"         {p}")
            if len(pages) > 3:
                print(f"         ...and {len(pages) - 3} more")
            fix(fix_suggestions[key])

    if duplicate_descs:
        all_good = False
        print(f"  [{WARN}] Duplicate meta descriptions found across pages:")
        for desc_text, pages in list(duplicate_descs.items())[:3]:
            print(f"         \"{desc_text[:60]}...\" on {len(pages)} pages")
        fix("Write unique meta descriptions for each page. Duplicate descriptions\nconfuse AI engines about which page to cite for a given topic.")

    if all_good:
        print(f"  [{PASS}] All sampled pages maintain consistent GEO standards")
        track_score("Multi-Page", 5, 5)
    else:
        total_issues = sum(len(v) for v in issues.values()) + len(duplicate_descs)
        mp_score = max(5 - total_issues, 0)
        track_score("Multi-Page", mp_score, 5)


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

    mode = "Diagnose + Fix Recommendations" if SHOW_FIX else "Diagnose Only"
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
    for cat, vals in sorted(_scores.items(), key=lambda x: x[0]):
        earned = vals["earned"]
        mx = vals["max"]
        pct = (earned / mx * 100) if mx > 0 else 0
        bar_len = int(pct / 5)
        bar = "\033[92m" + "█" * bar_len + "\033[0m" + "░" * (20 - bar_len)
        print(f"  {cat:<25} {earned:>4.1f}/{mx:<3.0f}  {bar}")

    total_earned = sum(v["earned"] for v in _scores.values())
    total_max = sum(v["max"] for v in _scores.values())
    print(f"  {'-'*25} {'-'*7}")
    print(f"  {'TOTAL':<25} {total_earned:>4.1f}/{total_max:<3.0f}")

    print(f"\n  Legend: PASS = good | WARN = could improve | FAIL = missing/bad")
    if SHOW_FIX:
        print("          FIX = recommended action to resolve the issue")
    else:
        print("  Tip: Run with --fix to see recommended solutions")
    print(f"{'='*60}\n")

    return score


def run_silent(url):
    """Run all checks on a URL, suppress output, return (score, scores_dict)."""
    global SHOW_FIX
    old_fix = SHOW_FIX
    SHOW_FIX = False
    reset_state()

    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    try:
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
        SHOW_FIX = old_fix

    score = get_ai_visibility_score()
    scores_copy = {k: dict(v) for k, v in _scores.items()}
    return score, scores_copy, url


def compare_urls(urls):
    """Compare GEO readiness across multiple URLs side-by-side."""
    results = []

    for url in urls:
        print(f"  Analyzing {url} ...", flush=True)
        score, scores_dict, normalized_url = run_silent(url)
        results.append({
            "url": normalized_url,
            "domain": urlparse(normalized_url).netloc,
            "score": score,
            "grade": get_grade(score),
            "categories": scores_dict,
        })

    # Collect all category names
    all_categories = sorted(set(
        cat for r in results for cat in r["categories"]
    ))

    # Display comparison
    domains = [r["domain"] for r in results]
    col_width = max(max(len(d) for d in domains) + 2, 14)

    print(f"\n{'='*60}")
    print(f"  GEO COMPETITIVE COMPARISON")
    print(f"{'='*60}\n")

    # Header row
    header = f"  {'Category':<25}"
    for r in results:
        header += f" {r['domain']:>{col_width}}"
    print(header)
    print(f"  {'-'*25}" + f" {'-'*col_width}" * len(results))

    # Category rows
    for cat in all_categories:
        row = f"  {cat:<25}"
        cat_scores = []
        for r in results:
            vals = r["categories"].get(cat, {"earned": 0, "max": 0})
            earned = vals["earned"]
            mx = vals["max"]
            pct = round(earned / mx * 100) if mx > 0 else 0
            cat_scores.append(pct)
            row += f" {earned:>4.1f}/{mx:<3.0f} ({pct:>2}%)"

        # Mark the winner with color
        if len(cat_scores) > 1:
            max_pct = max(cat_scores)
            parts = row.split()
            # Just print the row — winner highlighting via score comparison table below
        print(row)

    # Totals
    print(f"  {'-'*25}" + f" {'-'*col_width}" * len(results))
    total_row = f"  {'AI VISIBILITY SCORE':<25}"
    for r in results:
        total_row += f" {'':>{col_width - 10}}{r['score']:>3}/100 ({r['grade']})"
    print(total_row)

    # Winner
    if len(results) > 1:
        sorted_results = sorted(results, key=lambda x: x["score"], reverse=True)
        winner = sorted_results[0]
        runner_up = sorted_results[1]
        lead = winner["score"] - runner_up["score"]

        print(f"\n  Winner: {winner['domain']} ({winner['score']}/100, Grade {winner['grade']})")
        if lead > 0:
            print(f"  Lead: +{lead} points over {runner_up['domain']}")

        # Show where each site wins
        print(f"\n  Category Advantages:")
        for cat in all_categories:
            scores_per_url = []
            for r in results:
                vals = r["categories"].get(cat, {"earned": 0, "max": 0})
                mx = vals["max"]
                pct = (vals["earned"] / mx * 100) if mx > 0 else 0
                scores_per_url.append((r["domain"], pct))
            scores_per_url.sort(key=lambda x: x[1], reverse=True)
            if len(scores_per_url) > 1 and scores_per_url[0][1] > scores_per_url[1][1]:
                print(f"    {cat:<25} → {scores_per_url[0][0]} ({scores_per_url[0][1]:.0f}% vs {scores_per_url[1][1]:.0f}%)")

    print(f"\n{'='*60}\n")


def parse_log_line(line):
    """Parse a single access log line (Common Log Format or Combined)."""
    # Combined/Common log format:
    # 66.249.66.1 - - [10/Apr/2026:12:34:56 +0000] "GET /page HTTP/1.1" 200 1234 "-" "Mozilla/5.0 ..."
    pattern = (
        r'^(\S+)\s+'           # IP
        r'\S+\s+\S+\s+'       # ident, authuser
        r'\[([^\]]+)\]\s+'    # timestamp
        r'"(\S+)\s+(\S+)\s+[^"]*"\s+'  # method, path
        r'(\d{3})\s+'         # status
        r'(\S+)'              # size
        r'(?:\s+"([^"]*)"\s+"([^"]*)")?'  # referer, user-agent (optional)
    )
    m = re.match(pattern, line)
    if not m:
        return None
    return {
        "ip": m.group(1),
        "timestamp": m.group(2),
        "method": m.group(3),
        "path": m.group(4),
        "status": int(m.group(5)),
        "size": m.group(6),
        "referer": m.group(7) or "-",
        "user_agent": m.group(8) or "",
    }


def parse_timestamp(ts_str):
    """Parse log timestamp like '10/Apr/2026:12:34:56 +0000' into datetime."""
    from datetime import datetime
    try:
        return datetime.strptime(ts_str.split()[0], "%d/%b/%Y:%H:%M:%S")
    except (ValueError, IndexError):
        return None


def resolve_log_paths(log_pattern):
    """Resolve a log path or glob pattern into a sorted list of files.
    Supports glob wildcards (*, ?, []) and also handles .gz compressed logs.
    """
    import glob as glob_mod
    import os

    paths = sorted(glob_mod.glob(log_pattern))
    if not paths:
        # Maybe it's a literal path without wildcards
        if os.path.isfile(log_pattern):
            return [log_pattern]
        return []
    return [p for p in paths if os.path.isfile(p)]


def open_log_file(path):
    """Open a log file, handling .gz compression transparently."""
    import gzip
    if path.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return open(path, "r", errors="replace")


def crawl_check(log_pattern):
    """Convenience wrapper: resolve a single glob pattern and analyze."""
    import os
    log_files = resolve_log_paths(log_pattern)
    if not log_files:
        print(f"  [{FAIL}] No log files matched: {log_pattern}")
        return
    crawl_check_files(log_files, log_pattern)


def crawl_check_files(log_files, display_pattern=""):
    """Analyze server access logs for AI/LLM crawler activity.
    Accepts a list of file paths. Handles .gz compressed rotated logs.
    """
    import os
    from collections import defaultdict

    total_size = sum(os.path.getsize(f) for f in log_files)
    print(f"\n{'='*60}")
    print(f"  AI/LLM Crawl Activity Report")
    print(f"  Pattern: {display_pattern}")
    print(f"  Files matched: {len(log_files)} ({total_size / 1024 / 1024:.1f} MB total)")
    for lf in log_files:
        sz = os.path.getsize(lf)
        gz = " (gzipped)" if lf.endswith(".gz") else ""
        print(f"    - {lf} ({sz / 1024 / 1024:.1f} MB{gz})")
    print(f"{'='*60}")

    # Track per-bot stats
    bot_hits = defaultdict(list)      # bot_name -> list of {path, timestamp, status, ip}
    bot_pages = defaultdict(set)      # bot_name -> set of paths
    bot_ips = defaultdict(set)        # bot_name -> set of IPs
    total_lines = 0
    parsed_lines = 0
    first_ts = None
    last_ts = None

    for log_file in log_files:
        with open_log_file(log_file) as f:
            for line in f:
                total_lines += 1
                entry = parse_log_line(line.strip())
                if not entry:
                    continue
                parsed_lines += 1
                ua = entry["user_agent"]
                if not ua:
                    continue

                ts = parse_timestamp(entry["timestamp"])
                if ts:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts

                for bot_name, bot_info in AI_CRAWLERS.items():
                    if re.search(bot_info["pattern"], ua):
                        bot_hits[bot_name].append({
                            "path": entry["path"],
                            "timestamp": entry["timestamp"],
                            "status": entry["status"],
                            "ip": entry["ip"],
                        })
                        bot_pages[bot_name].add(entry["path"])
                        bot_ips[bot_name].add(entry["ip"])
                        break  # one bot per line

    # Summary
    print(f"\n  Log period: {first_ts or 'unknown'} → {last_ts or 'unknown'}")
    print(f"  Total lines: {total_lines:,} | Parsed: {parsed_lines:,}")
    print(f"  AI/LLM bot requests: {sum(len(h) for h in bot_hits.values()):,}")

    if not bot_hits:
        print(f"\n  [{WARN}] No AI/LLM crawler activity detected in this log file.")
        print(f"  [{INFO}] This could mean:")
        print(f"         - AI bots haven't discovered your site yet")
        print(f"         - The log file doesn't cover enough time")
        print(f"         - Bots are blocked by robots.txt or firewall")
        print(f"         - Your CDN/proxy strips bot user agents")
        fix("Register with Google Search Console and Bing Webmaster Tools to get indexed.\n"
            "Submit your sitemap.xml to each platform.\n"
            "Ensure robots.txt does not block AI crawlers.\n"
            "Check your CDN/WAF settings — some block bot traffic by default.")
        return

    # Per-bot breakdown
    print(f"\n--- Bot Activity Breakdown ---")
    sorted_bots = sorted(bot_hits.items(), key=lambda x: len(x[1]), reverse=True)
    for bot_name, hits in sorted_bots:
        info = AI_CRAWLERS[bot_name]
        pages = bot_pages[bot_name]
        ips = bot_ips[bot_name]
        status_counts = defaultdict(int)
        for h in hits:
            status_counts[h["status"]] += 1

        first_hit = hits[0]["timestamp"]
        last_hit = hits[-1]["timestamp"]

        print(f"\n  [{PASS}] {bot_name}")
        print(f"         Powers: {info['powers']}")
        print(f"         Requests: {len(hits):,} | Unique pages: {len(pages)} | IPs: {len(ips)}")
        print(f"         Status codes: {dict(status_counts)}")
        print(f"         First seen: {first_hit}")
        print(f"         Last seen:  {last_hit}")

        # Top pages
        page_counts = defaultdict(int)
        for h in hits:
            page_counts[h["path"]] += 1
        top_pages = sorted(page_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"         Top pages:")
        for path, count in top_pages:
            print(f"           {count:>5}x  {path}")

    # Bots NOT seen — split by importance
    missing_critical = [n for n in AI_CRAWLERS if n not in bot_hits and AI_CRAWLERS[n]["importance"] == "critical"]
    missing_optional = [n for n in AI_CRAWLERS if n not in bot_hits and AI_CRAWLERS[n]["importance"] == "optional"]

    if missing_critical:
        print(f"\n--- Critical Bots NOT Detected ---")
        for bot_name in missing_critical:
            info = AI_CRAWLERS[bot_name]
            print(f"  [{WARN}] {bot_name} — {info['powers']}")
        fix("These are core AI crawlers. If missing, your content may not appear in their AI products.\n"
            "Ensure you are registered with the corresponding search platforms.\n"
            "Check that robots.txt does not block these user agents.\n"
            "Submit your sitemap to Google Search Console and Bing Webmaster Tools.")

    if missing_optional:
        print(f"\n--- Optional Bots NOT Detected ---")
        for bot_name in missing_optional:
            info = AI_CRAWLERS[bot_name]
            print(f"  [{INFO}] {bot_name} — {info['powers']}")

    missing_bots = missing_critical + missing_optional

    # Summary table
    print(f"\n--- Summary ---")
    print(f"  {'Bot':<25} {'Requests':>10} {'Pages':>8} {'Last Seen':>25}")
    print(f"  {'-'*25} {'-'*10} {'-'*8} {'-'*25}")
    for bot_name, hits in sorted_bots:
        last_hit = hits[-1]["timestamp"]
        print(f"  {bot_name:<25} {len(hits):>10,} {len(bot_pages[bot_name]):>8} {last_hit:>25}")

    print(f"\n{'='*60}")
    print(f"  Detected: {len(bot_hits)} bot(s) | Not seen: {len(missing_bots)} bot(s)")
    print(f"{'='*60}\n")


def crawl_test(url):
    """Test if a site is accessible to AI crawlers by simulating requests with their user agents.
    Also checks robots.txt rules and external indexes (Common Crawl).
    Useful when you don't have access to server logs.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc

    print(f"\n{'='*60}")
    print(f"  AI Crawl Accessibility Test")
    print(f"  Target: {base_url}")
    print(f"{'='*60}")

    # ── Step 1: Parse robots.txt for bot-specific rules ──
    print(f"\n--- robots.txt Rules for AI Bots ---")
    robots_url = base_url + "/robots.txt"
    robots_resp = fetch(robots_url)
    robots_text = ""
    if robots_resp and robots_resp.status_code == 200:
        robots_text = robots_resp.text
        print(f"  [{PASS}] robots.txt found")
    else:
        print(f"  [{WARN}] robots.txt not found — all bots allowed by default")

    # Simple robots.txt parser: check per-bot rules
    def check_robots_for_bot(robots_text, bot_name):
        """Returns 'allowed', 'blocked', or 'no rule' based on robots.txt."""
        if not robots_text:
            return "allowed"
        lines = robots_text.lower().splitlines()
        in_block = False
        in_wildcard = False
        bot_lower = bot_name.lower()
        bot_blocked = None
        wildcard_blocked = None

        for line in lines:
            line = line.split("#")[0].strip()
            if line.startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip()
                in_block = agent == bot_lower
                in_wildcard = agent == "*"
            elif in_block and line.startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path == "/" or path == "/*":
                    bot_blocked = True
                elif path == "":
                    bot_blocked = False
            elif in_block and line.startswith("allow:"):
                path = line.split(":", 1)[1].strip()
                if path == "/":
                    bot_blocked = False
            elif in_wildcard and line.startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path == "/" or path == "/*":
                    wildcard_blocked = True
                elif path == "":
                    wildcard_blocked = False
            elif in_wildcard and line.startswith("allow:"):
                path = line.split(":", 1)[1].strip()
                if path == "/":
                    wildcard_blocked = False

        if bot_blocked is True:
            return "blocked"
        if bot_blocked is False:
            return "allowed"
        if wildcard_blocked is True:
            return "blocked"
        if wildcard_blocked is False:
            return "allowed"
        return "allowed"

    # Check robots.txt rules for each critical bot
    critical_bots_for_robots = {
        "GPTBot": "GPTBot",
        "ChatGPT-User": "ChatGPT-User",
        "ClaudeBot": "ClaudeBot",
        "PerplexityBot": "PerplexityBot",
        "Google-Extended": "Google-Extended",
        "Googlebot": "Googlebot",
        "Bingbot": "bingbot",
        "CCBot": "CCBot",
        "Anthropic": "anthropic-ai",
        "Bytespider": "Bytespider",
        "Meta-ExternalAgent": "Meta-ExternalAgent",
    }

    blocked_bots = []
    for display_name, ua_name in critical_bots_for_robots.items():
        status = check_robots_for_bot(robots_text, ua_name)
        if status == "blocked":
            print(f"  [{FAIL}] {display_name} — BLOCKED by robots.txt")
            blocked_bots.append(display_name)
        else:
            print(f"  [{PASS}] {display_name} — allowed")

    if blocked_bots:
        fix("The following bots are blocked by robots.txt: " + ", ".join(blocked_bots) + "\n"
            "If you want AI engines to index your content, remove or modify these rules:\n"
            "  User-agent: BotName\n"
            "  Disallow: /\n"
            "Change 'Disallow: /' to 'Allow: /' or remove the block entirely.")

    # ── Step 2: Simulate requests with AI bot user agents ──
    print(f"\n--- Simulated Bot Access Test ---")
    print(f"  Sending requests to {base_url}/ with AI bot user agents...")
    print(f"  (Checks if your server/CDN/WAF blocks bot traffic)\n")

    test_bots = {
        "GPTBot":        "Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)",
        "ChatGPT-User":  "Mozilla/5.0 ChatGPT-User/1.0",
        "ClaudeBot":     "ClaudeBot/1.0",
        "Googlebot":     "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Bingbot":       "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "PerplexityBot": "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
        "CCBot":         "CCBot/2.0 (https://commoncrawl.org/faq/)",
        "Bytespider":    "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)",
    }

    # First get a baseline with a normal browser user agent
    try:
        baseline = requests.get(base_url + "/", timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        baseline_status = baseline.status_code
        baseline_len = len(baseline.content)
    except requests.RequestException:
        baseline_status = None
        baseline_len = 0

    print(f"  Baseline (browser): status={baseline_status}, size={baseline_len:,} bytes\n")

    waf_blocked = []
    for bot_name, ua_string in test_bots.items():
        try:
            resp = requests.get(base_url + "/", timeout=15, headers={
                "User-Agent": ua_string
            })
            status = resp.status_code
            size = len(resp.content)

            # Detect blocking: 403/406/429, or dramatically smaller response (likely a block page)
            if status in (403, 406, 429, 451):
                print(f"  [{FAIL}] {bot_name:<18} status={status} — BLOCKED by server/WAF")
                waf_blocked.append(bot_name)
            elif baseline_len > 0 and size < baseline_len * 0.3:
                print(f"  [{WARN}] {bot_name:<18} status={status}, size={size:,} bytes — "
                      f"suspiciously small vs baseline ({baseline_len:,})")
                waf_blocked.append(bot_name)
            else:
                print(f"  [{PASS}] {bot_name:<18} status={status}, size={size:,} bytes")
        except requests.RequestException as e:
            print(f"  [{FAIL}] {bot_name:<18} request failed: {e}")
            waf_blocked.append(bot_name)

    if waf_blocked:
        fix("Some AI bots are being blocked by your server, CDN, or WAF.\n"
            "Check your Cloudflare/AWS WAF/Nginx rules and whitelist these user agents.\n"
            "Common causes:\n"
            "  - Cloudflare Bot Fight Mode blocking non-browser user agents\n"
            "  - Rate limiting rules that are too aggressive\n"
            "  - Security plugins (Wordfence, Sucuri) with strict bot blocking")
    else:
        print(f"\n  All tested bots can access your site successfully.")

    # ── Step 3: Check Common Crawl index ──
    print(f"\n--- Common Crawl Index Check ---")
    cc_url = f"https://index.commoncrawl.org/CC-MAIN-2025-51-index?url={domain}&output=json"
    try:
        cc_resp = requests.get(cc_url, timeout=30)
        if cc_resp.status_code == 200 and cc_resp.text.strip():
            cc_lines = [l for l in cc_resp.text.strip().splitlines() if l.strip()]
            print(f"  [{PASS}] Found {len(cc_lines)} page(s) in Common Crawl index")
            for line in cc_lines[:5]:
                try:
                    data = json.loads(line)
                    print(f"         {data.get('url', 'unknown')}")
                except json.JSONDecodeError:
                    pass
            if len(cc_lines) > 5:
                print(f"         ...and {len(cc_lines) - 5} more")
        elif cc_resp.status_code == 404:
            print(f"  [{WARN}] Domain not found in Common Crawl index")
            fix("Your site hasn't been crawled by Common Crawl yet.\n"
                "This is normal for newer/smaller sites. Build inbound links to increase discovery.")
        else:
            print(f"  [{INFO}] Common Crawl index returned status {cc_resp.status_code} — try again later")
    except requests.RequestException:
        print(f"  [{INFO}] Could not reach Common Crawl index — their servers may be slow, try again later")

    # ── Summary ──
    total_issues = len(blocked_bots) + len(waf_blocked)
    print(f"\n{'='*60}")
    if total_issues == 0:
        print(f"  Your site appears accessible to all major AI crawlers.")
        print(f"  robots.txt: all bots allowed | WAF/CDN: no blocks detected")
    else:
        print(f"  Issues found: {len(blocked_bots)} bot(s) blocked by robots.txt, "
              f"{len(waf_blocked)} bot(s) blocked by server/WAF")
    print(f"{'='*60}\n")


def authority_audit(url):
    """Audit off-page authority signals: reviews, awards, Google authority, authoritative mentions."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc.replace("www.", "")
    brand = domain.split(".")[0]

    print(f"\n{'='*60}")
    print(f"  Authority & Reputation Audit")
    print(f"  Target: {base_url}")
    print(f"  Domain: {domain} | Brand: {brand}")
    print(f"{'='*60}")

    # Fetch homepage for on-page analysis
    resp, soup = get_soup(base_url)
    homepage_text = ""
    json_ld_blocks = []
    if soup:
        homepage_text = get_text_content(soup).lower()
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    json_ld_blocks.extend(data)
                else:
                    json_ld_blocks.append(data)
            except (json.JSONDecodeError, TypeError):
                pass

    authority_score = 0
    max_score = 0

    # ── 1. Online Reviews ──
    print(f"\n--- 1. Online Reviews ---")
    max_score += 5

    # Check review platforms
    review_platforms = {
        "Trustpilot":    f"https://www.trustpilot.com/review/{domain}",
        "G2":            f"https://www.g2.com/products/{brand}/reviews",
        "Capterra":      f"https://www.capterra.com/p/{brand}/reviews/",
        "Product Hunt":  f"https://www.producthunt.com/products/{brand}",
        "Google Business": None,  # checked via schema
    }

    found_platforms = []
    for platform, check_url in review_platforms.items():
        if platform == "Google Business":
            continue
        try:
            r = requests.get(check_url, timeout=10, allow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            # Most platforms return 200 even for non-existent pages, so check content
            if r.status_code == 200 and domain in r.text.lower():
                found_platforms.append(platform)
                print(f"  [{PASS}] {platform} — profile found")
                print(f"         {check_url}")
            elif r.status_code == 200 and brand in r.text.lower():
                found_platforms.append(platform)
                print(f"  [{PASS}] {platform} — possible profile found")
                print(f"         {check_url}")
            else:
                print(f"  [{INFO}] {platform} — no profile detected")
        except requests.RequestException:
            print(f"  [{INFO}] {platform} — could not check (timeout/blocked)")

    # Check for review schema on site
    has_review_schema = False
    for block in json_ld_blocks:
        block_str = json.dumps(block).lower()
        if any(t in block_str for t in ['"review"', '"aggregaterating"', '"rating"']):
            has_review_schema = True
            break
    if soup:
        # Also check for embedded review markup
        review_attrs = soup.find_all(attrs={"itemtype": re.compile(r"schema.org/(Review|AggregateRating)", re.I)})
        if review_attrs:
            has_review_schema = True

    if has_review_schema:
        print(f"  [{PASS}] Review/Rating structured data found on site")
        found_platforms.append("On-site schema")

    # Check for links to review platforms in page
    if soup:
        review_domains = ["trustpilot.com", "g2.com", "capterra.com", "producthunt.com",
                         "yelp.com", "bbb.org", "glassdoor.com"]
        linked_platforms = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            for rd in review_domains:
                if rd in href:
                    linked_platforms.add(rd.split(".")[0].title())
        if linked_platforms:
            for lp in linked_platforms:
                print(f"  [{PASS}] Links to {lp} review page from site")
            found_platforms.extend(linked_platforms)

    if len(found_platforms) >= 3:
        authority_score += 5
        print(f"\n  Review presence: STRONG ({len(found_platforms)} platforms)")
    elif len(found_platforms) >= 1:
        authority_score += 3
        print(f"\n  Review presence: MODERATE ({len(found_platforms)} platform(s))")
    else:
        print(f"\n  [{WARN}] No review presence detected on major platforms")
        fix("List your product on review platforms to build trust signals:\n"
            "  - Trustpilot (https://business.trustpilot.com) — general reviews\n"
            "  - G2 (https://sell.g2.com) — B2B/SaaS reviews\n"
            "  - Product Hunt (https://producthunt.com) — launch & discovery\n"
            "  - Capterra (https://capterra.com) — software reviews\n"
            "Add AggregateRating schema to your site to display star ratings in search.")

    # ── 2. Awards, Accreditations & Affiliations ──
    print(f"\n--- 2. Awards, Accreditations & Affiliations ---")
    max_score += 5

    # Check page content for award/accreditation signals
    award_keywords = [
        "award", "awarded", "winner", "finalist", "recognized", "named",
        "best of", "top rated", "leader in", "badge", "certified",
        "accredited", "accreditation", "certification", "iso ", "soc 2",
        "soc2", "gdpr", "hipaa", "pci dss", "pci-dss", "compliant",
        "compliance", "member of", "affiliated", "partnership", "partner",
        "backed by", "funded by", "yc ", "y combinator", "techstars",
        "forbes", "gartner", "forrester", "inc 5000", "deloitte",
    ]
    found_awards = []
    for kw in award_keywords:
        if kw in homepage_text:
            found_awards.append(kw)

    # Check for award-related schema
    award_schema_types = ["Award", "Certification", "EducationalOccupationalCredential"]
    found_award_schema = False
    for block in json_ld_blocks:
        block_str = json.dumps(block)
        for ast in award_schema_types:
            if ast in block_str:
                found_award_schema = True
                break

    # Check for trust badges / certification images
    badge_keywords = ["badge", "certified", "award", "seal", "trust", "secure",
                     "accredited", "verified", "partner"]
    badge_images = []
    if soup:
        for img in soup.find_all("img"):
            alt = (img.get("alt") or "").lower()
            src = (img.get("src") or "").lower()
            for bk in badge_keywords:
                if bk in alt or bk in src:
                    badge_images.append(img.get("alt") or img.get("src", ""))
                    break

    if found_awards:
        unique_awards = list(set(found_awards))[:10]
        print(f"  [{PASS}] Award/accreditation signals found in content:")
        for kw in unique_awards:
            print(f"         • '{kw}'")
    else:
        print(f"  [{WARN}] No award/accreditation keywords detected in homepage content")

    if found_award_schema:
        print(f"  [{PASS}] Award/certification structured data found")
    else:
        print(f"  [{INFO}] No award-specific schema markup")

    if badge_images:
        print(f"  [{PASS}] Trust/certification badge images found ({len(badge_images)}):")
        for b in badge_images[:5]:
            print(f"         • {b}")
    else:
        print(f"  [{INFO}] No trust badge images detected")

    award_signals = len(found_awards) + (2 if found_award_schema else 0) + len(badge_images)
    if award_signals >= 5:
        authority_score += 5
        print(f"\n  Awards/accreditations: STRONG")
    elif award_signals >= 2:
        authority_score += 3
        print(f"\n  Awards/accreditations: MODERATE")
    else:
        print(f"\n  Awards/accreditations: WEAK")
        fix("Strengthen trust signals:\n"
            "  - Display certifications prominently (SOC2, GDPR, ISO, PCI-DSS)\n"
            "  - Add award badges with alt text: <img alt='2025 Best Fintech Award' ...>\n"
            "  - Add partner/affiliation logos (Y Combinator, accelerators, industry groups)\n"
            "  - Use structured data for awards:\n"
            '    {"@type": "Organization", "award": ["Best Fintech 2025", ...]}')

    # ── 3. Google Website Authority ──
    print(f"\n--- 3. Google Website Authority ---")
    max_score += 5

    # 3a. Check Google indexed page count via site: search
    print(f"  Checking Google index presence...")
    google_indexed = None
    try:
        google_url = f"https://www.google.com/search?q=site:{domain}"
        gr = requests.get(google_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if gr.status_code == 200:
            # Try to extract result count from "About X results"
            count_match = re.search(r'About ([\d,]+) results', gr.text)
            if count_match:
                google_indexed = int(count_match.group(1).replace(",", ""))
                print(f"  [{PASS}] Google indexed pages: ~{google_indexed:,}")
            else:
                # Check if any results exist
                if "did not match any documents" in gr.text:
                    print(f"  [{FAIL}] Domain not indexed by Google")
                    google_indexed = 0
                else:
                    print(f"  [{INFO}] Google returned results but count not parseable (CAPTCHA or JS-rendered)")
        elif gr.status_code == 429:
            print(f"  [{INFO}] Google rate-limited the request — try 'site:{domain}' in your browser")
        else:
            print(f"  [{INFO}] Could not query Google (status {gr.status_code})")
    except requests.RequestException:
        print(f"  [{INFO}] Could not reach Google — check manually: site:{domain}")

    # 3b. Check for Google Knowledge Panel signals
    print(f"  Checking Knowledge Panel readiness...")
    kg_signals = 0
    # Check Organization schema completeness
    for block in json_ld_blocks:
        block_type = block.get("@type", "")
        if block_type in ("Organization", "Corporation", "LocalBusiness"):
            fields = ["name", "url", "logo", "description", "sameAs",
                      "contactPoint", "founder", "foundingDate", "address"]
            present = [f for f in fields if block.get(f)]
            kg_signals = len(present)
            print(f"  [{PASS}] Organization schema: {len(present)}/{len(fields)} fields")
            missing = [f for f in fields if not block.get(f)]
            if missing:
                print(f"         Missing: {', '.join(missing)}")
            break

    # Check Wikipedia/Wikidata presence (strong Knowledge Panel signal)
    wiki_found = False
    if soup:
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "wikipedia.org" in href or "wikidata.org" in href:
                wiki_found = True
                print(f"  [{PASS}] Links to Wikipedia/Wikidata — strong entity signal")
                break
    for block in json_ld_blocks:
        same_as = block.get("sameAs", [])
        if isinstance(same_as, str):
            same_as = [same_as]
        for link in same_as:
            if "wikipedia.org" in link or "wikidata.org" in link:
                wiki_found = True
                print(f"  [{PASS}] Wikipedia/Wikidata in sameAs — strong entity signal")
                break

    if not wiki_found:
        print(f"  [{INFO}] No Wikipedia/Wikidata links — consider creating entries for brand recognition")

    google_score = 0
    if google_indexed and google_indexed >= 50:
        google_score += 2
    elif google_indexed and google_indexed >= 10:
        google_score += 1
    google_score += min(kg_signals, 3)  # up to 3 points for schema
    if wiki_found:
        google_score += 1

    authority_score += min(google_score, 5)
    if google_score >= 4:
        print(f"\n  Google authority signals: STRONG")
    elif google_score >= 2:
        print(f"\n  Google authority signals: MODERATE")
    else:
        print(f"\n  Google authority signals: WEAK")
        fix("Boost Google authority:\n"
            "  - Complete your Organization schema (all 9 fields)\n"
            "  - Create a Wikipedia article for your brand/product\n"
            "  - Create a Wikidata entity and link it in sameAs\n"
            "  - Claim your Google Business Profile\n"
            "  - Build high-quality backlinks from authoritative domains")

    # ── 4. Authoritative List Mentions ──
    print(f"\n--- 4. Authoritative List Mentions ---")
    max_score += 5

    # Check if the domain appears on authoritative platforms
    authority_sources = {
        "GitHub":         f"https://api.github.com/search/repositories?q={brand}&per_page=3",
        "npm":            f"https://registry.npmjs.org/{brand}",
        "PyPI":           f"https://pypi.org/pypi/{brand}/json",
        "Crunchbase":     f"https://www.crunchbase.com/organization/{brand}",
        "LinkedIn":       f"https://www.linkedin.com/company/{brand}",
        "AngelList":      f"https://wellfound.com/company/{brand}",
        "HackerNews":     f"https://hn.algolia.com/api/v1/search?query={domain}&tags=story",
    }

    found_mentions = []
    for source, check_url in authority_sources.items():
        try:
            r = requests.get(check_url, timeout=10, allow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            if source == "HackerNews":
                # JSON API — check for actual hits
                try:
                    hn_data = r.json()
                    hits = hn_data.get("nbHits", 0)
                    if hits > 0:
                        found_mentions.append(source)
                        print(f"  [{PASS}] {source} — {hits} mention(s)")
                    else:
                        print(f"  [{INFO}] {source} — no mentions")
                except (json.JSONDecodeError, ValueError):
                    print(f"  [{INFO}] {source} — could not parse response")
            elif source == "npm":
                # npm registry API returns JSON for existing packages
                if r.status_code == 200:
                    try:
                        pkg = r.json()
                        if pkg.get("name"):
                            found_mentions.append(source)
                            desc = pkg.get("description", "")[:60]
                            print(f"  [{PASS}] {source} — package found: {pkg['name']}")
                            if desc:
                                print(f"         {desc}")
                        else:
                            print(f"  [{INFO}] {source} — not found")
                    except (json.JSONDecodeError, ValueError):
                        print(f"  [{INFO}] {source} — not found")
                else:
                    print(f"  [{INFO}] {source} — not found")
            elif source == "PyPI":
                # PyPI JSON API returns package metadata
                if r.status_code == 200:
                    try:
                        pkg = r.json()
                        info = pkg.get("info", {})
                        if info.get("name"):
                            found_mentions.append(source)
                            desc = info.get("summary", "")[:60]
                            print(f"  [{PASS}] {source} — package found: {info['name']}")
                            if desc:
                                print(f"         {desc}")
                        else:
                            print(f"  [{INFO}] {source} — not found")
                    except (json.JSONDecodeError, ValueError):
                        print(f"  [{INFO}] {source} — not found")
                else:
                    print(f"  [{INFO}] {source} — not found")
            elif source == "Crunchbase":
                if r.status_code == 200 and brand in r.text.lower():
                    found_mentions.append(source)
                    print(f"  [{PASS}] {source} — profile found")
                else:
                    print(f"  [{INFO}] {source} — no profile found")
            elif source == "LinkedIn":
                # LinkedIn often blocks/redirects
                if r.status_code == 200:
                    found_mentions.append(source)
                    print(f"  [{PASS}] {source} — company page found")
                else:
                    print(f"  [{INFO}] {source} — not detected (may require login)")
            elif source == "GitHub":
                # GitHub search API returns JSON with matching repos
                if r.status_code == 200:
                    try:
                        data = r.json()
                        total = data.get("total_count", 0)
                        if total > 0:
                            found_mentions.append(source)
                            top = data["items"][0]
                            print(f"  [{PASS}] {source} — {total} repo(s) found, top: {top['full_name']}")
                        else:
                            print(f"  [{INFO}] {source} — not found")
                    except (json.JSONDecodeError, ValueError, KeyError):
                        print(f"  [{INFO}] {source} — could not check")
                else:
                    print(f"  [{INFO}] {source} — not found")
            else:
                if r.status_code == 200 and (brand in r.text.lower() or domain in r.text.lower()):
                    found_mentions.append(source)
                    print(f"  [{PASS}] {source} — found")
                else:
                    print(f"  [{INFO}] {source} — not found")
        except requests.RequestException:
            print(f"  [{INFO}] {source} — could not check")

    # Check for .gov / .edu / .org backlink signals on the page
    if soup:
        authority_outlinks = []
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            for ext in [".gov", ".edu", ".org"]:
                if ext in href and domain not in href:
                    authority_outlinks.append(href)
                    break
        if authority_outlinks:
            unique_auth = list(set(authority_outlinks))[:5]
            print(f"  [{PASS}] Links to {len(set(authority_outlinks))} authoritative domain(s) (.gov/.edu/.org)")

    if len(found_mentions) >= 4:
        authority_score += 5
        print(f"\n  Authoritative mentions: STRONG ({len(found_mentions)} platforms)")
    elif len(found_mentions) >= 2:
        authority_score += 3
        print(f"\n  Authoritative mentions: MODERATE ({len(found_mentions)} platform(s))")
    else:
        authority_score += 1
        print(f"\n  Authoritative mentions: WEAK ({len(found_mentions)} platform(s))")
        fix("Increase your presence on authoritative platforms:\n"
            "  - Create a Crunchbase profile for your company\n"
            "  - Maintain an active GitHub organization\n"
            "  - Publish packages on npm/PyPI if applicable\n"
            "  - Get mentioned on Hacker News (Show HN posts)\n"
            "  - Submit to startup directories (Product Hunt, AngelList/Wellfound)\n"
            "  - Seek mentions in industry publications and comparison lists")

    # ── Final Score ──
    print(f"\n{'='*60}")
    print(f"  Authority Score: {authority_score}/{max_score}")
    pct = (authority_score / max_score * 100) if max_score > 0 else 0
    if pct >= 80:
        grade = "A — Excellent"
    elif pct >= 60:
        grade = "B — Good"
    elif pct >= 40:
        grade = "C — Needs improvement"
    elif pct >= 20:
        grade = "D — Weak"
    else:
        grade = "F — Critical gaps"
    print(f"  Grade: {grade}")
    print(f"\n  Breakdown:")
    print(f"    Online Reviews:         {'checked' :>10}")
    print(f"    Awards/Accreditations:  {'checked' :>10}")
    print(f"    Google Authority:       {'checked' :>10}")
    print(f"    Authoritative Mentions: {'checked' :>10}")
    print(f"{'='*60}\n")


def main():
    global SHOW_FIX
    parser = argparse.ArgumentParser(
        description="Check a website's GEO (Generative Engine Optimization) readiness."
    )
    parser.add_argument("url", nargs="?", help="The website URL to check (e.g. https://example.com)")
    parser.add_argument("--fix", action="store_true",
                        help="Show fix recommendations for each issue found")
    parser.add_argument("--compare", metavar="URL", nargs="+",
                        help="Compare GEO readiness across multiple URLs side-by-side. "
                             "Example: --compare https://site1.com https://site2.com")
    parser.add_argument("--crawl-check", metavar="LOG_PATTERN", nargs="+",
                        help="Analyze server access logs for AI/LLM crawler activity. "
                             "Accepts glob patterns (e.g. '/var/log/nginx/access*.log') "
                             "and .gz compressed files. Multiple patterns can be specified.")
    parser.add_argument("--crawl-test", metavar="URL",
                        help="Test if a site is accessible to AI crawlers without needing log files. "
                             "Checks robots.txt rules, simulates bot requests, and queries external indexes.")
    parser.add_argument("--authority-audit", metavar="URL",
                        help="Audit off-page authority signals: online reviews, awards/accreditations, "
                             "Google authority, and authoritative list mentions.")
    parser.add_argument("--categories", metavar="LIST",
                        help="Comma-separated category whitelist (e.g. "
                             "'HTTPS,robots.txt,Sitemap,Meta Tags,Mobile & Weight'). "
                             "When set, only the listed categories are run. Used by the SaaS backend "
                             "to implement the free-tier 5-check subset.")
    args = parser.parse_args()

    SHOW_FIX = args.fix

    if args.compare:
        compare_urls(args.compare)
    elif args.authority_audit:
        authority_audit(args.authority_audit)
    elif args.crawl_test:
        crawl_test(args.crawl_test)
    elif args.crawl_check:
        # Merge all patterns into one combined list of files
        import glob as glob_mod
        import os
        all_files = []
        for pattern in args.crawl_check:
            resolved = resolve_log_paths(pattern)
            all_files.extend(resolved)
        # Deduplicate while preserving order
        seen = set()
        unique_files = []
        for f in all_files:
            real = os.path.realpath(f)
            if real not in seen:
                seen.add(real)
                unique_files.append(f)
        if not unique_files:
            print(f"  [{FAIL}] No log files matched the given pattern(s)")
        else:
            # Pass a synthetic pattern string for display, then feed files directly
            display = " ".join(args.crawl_check)
            crawl_check_files(unique_files, display)
    elif args.url:
        allowed_categories = None
        if args.categories:
            allowed_categories = [c.strip() for c in args.categories.split(",") if c.strip()]
        generate_score(args.url, allowed_categories=allowed_categories)
    else:
        parser.error("Either provide a URL, --compare URL1 URL2, --crawl-check LOG_PATTERN, or --crawl-test URL")


if __name__ == "__main__":
    main()
