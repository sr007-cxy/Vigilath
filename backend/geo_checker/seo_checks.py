"""The 12 `check_seo_*` functions — classic search-engine optimization audit.

Companion to checks.py (the 25 GEO checks). Kept in a separate module and a
separate score namespace (`state._seo_scores` via `track_seo_score`) for two
reasons:

  1. The GEO AI Visibility Score must stay identical to the frozen root
     geo_checker.py baseline. Adding categories to the shared `_scores` dict
     would move every site's GEO score silently.
  2. SEO and GEO answer different questions. A site can be perfectly indexable
     by Google and still be invisible to AI engines (no llms.txt, JS-rendered
     body, no entity signals), and the reverse also happens. Two scores keep
     the diagnosis honest instead of averaging the two into one vague number.

Conventions are identical to checks.py so the same parser handles both:
  - Every function prints a `--- SEO: X ---` section header first. The `SEO: `
    prefix is what keeps the two category vocabularies from colliding in
    `CheckResult.category`.
  - Findings go through emit_check(); fixes through emit_fix().
  - Scores go through track_seo_score(category_short_name, earned, max).

HTTP note: redirect-sensitive probes use `_raw_get()` (a direct requests call)
instead of io.fetch(). io.fetch caches by URL only, ignoring allow_redirects —
caching a 301 under the same key a follow-redirects caller uses would poison
the shared page cache. Everything that just needs the rendered homepage still
goes through get_soup()/fetch() so we don't re-download it 12 times.
"""

import json
import re
import time
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests

from .constants import PASS, WARN, FAIL, INFO
from .io import fetch, get_soup, get_text_content
from .output import emit_check, emit_fix, print
from .state import track_seo_score

_UA = {"User-Agent": "GEO-Readiness-Checker/1.0 (SEO audit)"}

# Anchor text that carries no ranking signal, in both languages we serve.
_GENERIC_ANCHORS = {
    "click here", "read more", "more", "here", "link", "learn more",
    "see more", "details", "this", "go", "download", "continue",
    "点击这里", "点击", "更多", "了解更多", "查看更多", "详情", "阅读全文", "这里",
}

_GENERIC_TITLES = {
    "home", "homepage", "index", "untitled", "welcome", "new page",
    "首页", "主页", "未命名", "网站首页",
}


def _raw_get(url, *, allow_redirects=False, timeout=8):
    """Uncached GET. Returns the Response or None. See module docstring."""
    try:
        return requests.get(url, allow_redirects=allow_redirects,
                            timeout=timeout, headers=_UA)
    except requests.RequestException:
        return None


def _text_of(tag):
    return (tag.get_text() or "").strip() if tag is not None else ""


def _tokens(text):
    """Lowercase word/CJK-character tokens, for cheap overlap comparisons."""
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9]+", text)
    cjk = re.findall(r"[一-鿿]", text)
    return set(words) | set(cjk)


# ---------------------------------------------------------------------------
# 1. Title Tag
# ---------------------------------------------------------------------------
def check_seo_title(base_url):
    """<title> presence, length, brand pattern, and topical alignment with h1."""
    print("\n--- SEO: Title Tag ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.title.fetch_failed", "Could not fetch homepage")
        track_seo_score("Title Tag", 0, 8)
        return

    score = 0
    title_tag = soup.find("title")
    title = _text_of(title_tag)
    if not title:
        emit_check(FAIL, "result.checks.seo.title.missing", "Missing <title> tag — this is the single strongest on-page ranking element")
        emit_fix("result.fixes.seo.title.add",
                 "Add a <title> in <head>:\n  <title>Primary Keyword — Brand</title>\nAim for 30-60 characters, put the primary keyword first, keep the brand as a suffix.")
        track_seo_score("Title Tag", 0, 8)
        return

    n = len(title)
    if 30 <= n <= 60:
        emit_check(PASS, "result.checks.seo.title.length_ok", f"Title length is {n} characters (ideal 30-60)", {"chars": n})
        score += 3
    elif 15 <= n <= 70:
        emit_check(WARN, "result.checks.seo.title.length_fair", f"Title length is {n} characters — search results usually truncate outside 30-60", {"chars": n})
        emit_fix("result.fixes.seo.title.retune_length",
                 "Rewrite the title to 30-60 characters so it renders in full on the results page. Front-load the primary keyword; drop filler words.")
        score += 2
    else:
        emit_check(FAIL, "result.checks.seo.title.length_bad", f"Title length is {n} characters — far outside the 30-60 range", {"chars": n})
        emit_fix("result.fixes.seo.title.rewrite",
                 "Rewrite the title to 30-60 characters. Too short wastes ranking real estate; too long gets truncated and dilutes relevance.")
        score += 1

    if re.search(r"[|\-–—:·]", title):
        emit_check(PASS, "result.checks.seo.title.brand_pattern", "Title uses a keyword/brand separator pattern")
        score += 1
    else:
        emit_check(INFO, "result.checks.seo.title.no_brand_pattern", "Title has no brand suffix — \"Keyword — Brand\" improves recognition in results")

    if title.strip().lower() in _GENERIC_TITLES:
        emit_check(FAIL, "result.checks.seo.title.generic", f"Title is generic: \"{title}\"", {"title": title})
        emit_fix("result.fixes.seo.title.make_specific",
                 "Replace the generic title with a descriptive one naming what the page offers and for whom.")
    else:
        emit_check(PASS, "result.checks.seo.title.specific", f"Title is descriptive: \"{title[:70]}\"", {"title": title[:70]})
        score += 1

    h1 = soup.find("h1")
    h1_text = _text_of(h1)
    if h1_text:
        t_tok, h_tok = _tokens(title), _tokens(h1_text)
        overlap = len(t_tok & h_tok) / max(1, min(len(t_tok), len(h_tok)))
        if overlap >= 0.3:
            emit_check(PASS, "result.checks.seo.title.h1_aligned", f"Title and <h1> share the same topic ({overlap * 100:.0f}% token overlap)", {"pct": f"{overlap * 100:.0f}"})
            score += 2
        else:
            emit_check(WARN, "result.checks.seo.title.h1_diverged", "Title and <h1> describe different topics — search engines discount the mismatch")
            emit_fix("result.fixes.seo.title.align_with_h1",
                     "Keep the title and the <h1> on the same topic. They may be worded differently, but the primary keyword should appear in both.")
            score += 1
    else:
        emit_check(WARN, "result.checks.seo.title.no_h1_to_compare", "No <h1> on the page, so title/heading alignment cannot be verified")

    words = re.findall(r"[a-z0-9一-鿿]+", title.lower())
    repeated = [w for w in set(words) if len(w) > 2 and words.count(w) >= 3]
    if repeated:
        emit_check(WARN, "result.checks.seo.title.stuffing", f"Repeated term in title: {', '.join(repeated)} — reads as keyword stuffing", {"terms": ", ".join(repeated)})
        emit_fix("result.fixes.seo.title.remove_stuffing",
                 "Remove the repeated keyword. Modern ranking models penalize repetition; one clear mention is enough.")
    else:
        emit_check(PASS, "result.checks.seo.title.no_stuffing", "No keyword stuffing detected in title")
        score += 1

    track_seo_score("Title Tag", min(score, 8), 8)


# ---------------------------------------------------------------------------
# 2. Meta Description
# ---------------------------------------------------------------------------
def check_seo_meta_description(base_url):
    """Meta description presence, length, and distinctness from the title."""
    print("\n--- SEO: Meta Description ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.desc.fetch_failed", "Could not fetch homepage")
        track_seo_score("Meta Description", 0, 6)
        return

    score = 0
    tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    desc = (tag.get("content") or "").strip() if tag else ""
    if not desc:
        emit_check(FAIL, "result.checks.seo.desc.missing", "Missing meta description — the search snippet is then auto-generated and unpredictable")
        emit_fix("result.fixes.seo.desc.add",
                 "Add a meta description in <head>:\n  <meta name=\"description\" content=\"120-160 characters describing the page value, with the primary keyword and a call to action.\">")
        track_seo_score("Meta Description", 0, 6)
        return

    emit_check(PASS, "result.checks.seo.desc.found", f"Meta description found ({len(desc)} characters)", {"chars": len(desc)})
    score += 2

    n = len(desc)
    if 120 <= n <= 160:
        emit_check(PASS, "result.checks.seo.desc.length_ok", f"Description length is {n} characters (ideal 120-160)", {"chars": n})
        score += 2
    elif 70 <= n <= 200:
        emit_check(WARN, "result.checks.seo.desc.length_fair", f"Description length is {n} characters — 120-160 fills the snippet without truncation", {"chars": n})
        emit_fix("result.fixes.seo.desc.retune_length", "Adjust the meta description to 120-160 characters so the snippet renders in full.")
        score += 1
    else:
        emit_check(FAIL, "result.checks.seo.desc.length_bad", f"Description length is {n} characters — well outside 120-160", {"chars": n})
        emit_fix("result.fixes.seo.desc.rewrite", "Rewrite the meta description to 120-160 characters. Under 70 wastes the snippet; over 200 gets cut off mid-sentence.")

    title = _text_of(soup.find("title"))
    if title and desc.strip().lower() == title.strip().lower():
        emit_check(WARN, "result.checks.seo.desc.same_as_title", "Meta description duplicates the title — the snippet then adds no new information")
        emit_fix("result.fixes.seo.desc.differentiate", "Make the description complement the title instead of repeating it: state the benefit, the scope, and one reason to click.")
    else:
        emit_check(PASS, "result.checks.seo.desc.distinct", "Meta description differs from the title")
        score += 1

    if re.search(r"[.!?。！？]", desc) and len(desc.split()) >= 3:
        emit_check(PASS, "result.checks.seo.desc.readable", "Meta description reads as a complete sentence")
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.desc.keyword_list", "Meta description looks like a keyword list rather than a sentence")
        emit_fix("result.fixes.seo.desc.write_sentence", "Write the description as one or two complete sentences aimed at a human reader, not a comma-separated keyword list.")

    track_seo_score("Meta Description", min(score, 6), 6)


# ---------------------------------------------------------------------------
# 3. Heading Structure
# ---------------------------------------------------------------------------
def check_seo_headings(base_url):
    """Single-h1 rule, subheading coverage, and level continuity."""
    print("\n--- SEO: Heading Structure ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.headings.fetch_failed", "Could not fetch homepage")
        track_seo_score("Heading Structure", 0, 8)
        return

    score = 0
    headings = soup.find_all(re.compile(r"^h[1-6]$"))
    h1s = [h for h in headings if h.name == "h1"]

    if len(h1s) == 1:
        emit_check(PASS, "result.checks.seo.headings.single_h1", f"Exactly one <h1>: \"{_text_of(h1s[0])[:70]}\"", {"text": _text_of(h1s[0])[:70]})
        score += 3
    elif len(h1s) == 0:
        emit_check(FAIL, "result.checks.seo.headings.no_h1", "No <h1> on the page — the main topic is undeclared")
        emit_fix("result.fixes.seo.headings.add_h1", "Add one <h1> containing the page's primary topic. Use it once per page; all other headings should be <h2> and below.")
    else:
        emit_check(WARN, "result.checks.seo.headings.multi_h1", f"{len(h1s)} <h1> tags found — the primary topic becomes ambiguous", {"count": len(h1s)})
        emit_fix("result.fixes.seo.headings.dedupe_h1", "Keep a single <h1> and demote the rest to <h2>. Multiple <h1> tags split the topical signal.")
        score += 1

    h1_text = _text_of(h1s[0]) if h1s else ""
    if h1_text and len(h1_text) <= 70:
        emit_check(PASS, "result.checks.seo.headings.h1_concise", f"<h1> is {len(h1_text)} characters", {"chars": len(h1_text)})
        score += 1
    elif h1_text:
        emit_check(WARN, "result.checks.seo.headings.h1_long", f"<h1> is {len(h1_text)} characters — long headings dilute the topic", {"chars": len(h1_text)})
        emit_fix("result.fixes.seo.headings.shorten_h1", "Shorten the <h1> to under 70 characters and move the supporting detail into the first paragraph.")

    h2s = [h for h in headings if h.name == "h2"]
    if len(h2s) >= 2:
        emit_check(PASS, "result.checks.seo.headings.h2_ok", f"{len(h2s)} <h2> subheadings structure the page", {"count": len(h2s)})
        score += 2
    elif len(h2s) == 1:
        emit_check(WARN, "result.checks.seo.headings.h2_thin", "Only one <h2> — subheadings help both ranking and answer extraction")
        emit_fix("result.fixes.seo.headings.add_h2", "Break the body into sections and give each an <h2> naming the question or subtopic it answers.")
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.headings.h2_missing", "No <h2> subheadings — the page reads as one undifferentiated block")
        emit_fix("result.fixes.seo.headings.add_h2", "Break the body into sections and give each an <h2> naming the question or subtopic it answers.")

    levels = [int(h.name[1]) for h in headings]
    skipped = [(levels[i], levels[i + 1]) for i in range(len(levels) - 1) if levels[i + 1] - levels[i] > 1]
    if not levels:
        emit_check(FAIL, "result.checks.seo.headings.none", "No heading tags at all")
    elif skipped:
        a, b = skipped[0]
        emit_check(WARN, "result.checks.seo.headings.level_skipped", f"Heading levels skip (h{a} → h{b}) — the outline is broken", {"from": a, "to": b})
        emit_fix("result.fixes.seo.headings.fix_hierarchy", "Do not skip heading levels. Follow h1 → h2 → h3 in order; use CSS for size, not heading level.")
    else:
        emit_check(PASS, "result.checks.seo.headings.hierarchy_ok", "Heading levels follow a continuous hierarchy")
        score += 1

    empties = [h.name for h in headings if not _text_of(h)]
    if empties:
        emit_check(WARN, "result.checks.seo.headings.empty", f"{len(empties)} empty heading tags — usually layout markup abusing heading semantics", {"count": len(empties)})
        emit_fix("result.fixes.seo.headings.remove_empty", "Remove empty heading tags or replace them with <div>. Headings used purely for styling mislead crawlers.")
    elif headings:
        emit_check(PASS, "result.checks.seo.headings.all_filled", "All heading tags carry text")
        score += 1

    track_seo_score("Heading Structure", min(score, 8), 8)


# ---------------------------------------------------------------------------
# 4. Indexability
# ---------------------------------------------------------------------------
def check_seo_indexability(base_url):
    """The four ways a page silently drops out of the index."""
    print("\n--- SEO: Indexability ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.index.fetch_failed", "Could not fetch homepage")
        track_seo_score("Indexability", 0, 10)
        return

    score = 0

    robots_meta = ""
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or "").lower()
        if name in ("robots", "googlebot", "bingbot"):
            robots_meta += " " + (tag.get("content") or "").lower()
    if "noindex" in robots_meta:
        emit_check(FAIL, "result.checks.seo.index.meta_noindex", "<meta name=\"robots\"> contains noindex — this page cannot rank at all")
        emit_fix("result.fixes.seo.index.remove_noindex",
                 "Remove `noindex` from the robots meta tag on pages that should rank. Staging directives left in production are the most common cause of a site vanishing from search.")
    else:
        emit_check(PASS, "result.checks.seo.index.meta_indexable", "No noindex directive in the robots meta tag")
        score += 3

    if "nofollow" in robots_meta:
        emit_check(WARN, "result.checks.seo.index.meta_nofollow", "Robots meta contains nofollow — link equity stops here and internal discovery suffers")
        emit_fix("result.fixes.seo.index.remove_nofollow", "Remove the page-level `nofollow`. Use link-level rel attributes if you need to control specific links.")
    else:
        emit_check(PASS, "result.checks.seo.index.links_followable", "Page-level links are followable")
        score += 2

    x_robots = (resp.headers.get("X-Robots-Tag") or "").lower() if resp is not None else ""
    if "noindex" in x_robots:
        emit_check(FAIL, "result.checks.seo.index.header_noindex", f"X-Robots-Tag header blocks indexing: {x_robots}", {"value": x_robots})
        emit_fix("result.fixes.seo.index.remove_header_noindex",
                 "Drop the `X-Robots-Tag: noindex` response header for public pages. Header-level directives are easy to miss because the HTML looks fine.")
    else:
        emit_check(PASS, "result.checks.seo.index.header_clean", "No blocking X-Robots-Tag header")
        score += 2

    parsed = urlparse(base_url)
    robots_txt = _raw_get(f"{parsed.scheme}://{parsed.netloc}/robots.txt", allow_redirects=True, timeout=6)
    if robots_txt is None or robots_txt.status_code != 200:
        emit_check(INFO, "result.checks.seo.index.robots_absent", "No robots.txt — everything is crawlable by default, but crawl budget is unmanaged")
        score += 1.5
    else:
        blocked = False
        applies = False
        for line in robots_txt.text.splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                applies = value in ("*", "googlebot", "Googlebot")
            elif key == "disallow" and applies and value in ("/", "/*"):
                blocked = True
        if blocked:
            emit_check(FAIL, "result.checks.seo.index.robots_blocks_all", "robots.txt disallows the whole site for search crawlers")
            emit_fix("result.fixes.seo.index.open_robots",
                     "Remove the site-wide `Disallow: /`. Block only the paths that must stay private (admin, cart, internal search).")
        else:
            emit_check(PASS, "result.checks.seo.index.robots_allows", "robots.txt does not block search crawlers from the site root")
            score += 3

    track_seo_score("Indexability", min(score, 10), 10)


# ---------------------------------------------------------------------------
# 5. URL, Canonical & Redirects
# ---------------------------------------------------------------------------
def check_seo_canonical_redirects(base_url):
    """Canonical correctness plus the two consolidation redirects that matter."""
    print("\n--- SEO: URL, Canonical & Redirects ---")
    resp, soup = get_soup(base_url)
    score = 0
    parsed = urlparse(base_url)

    if soup:
        canonical = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, str) else " ".join(v)).lower())
        href = (canonical.get("href") or "").strip() if canonical else ""
        if href:
            emit_check(PASS, "result.checks.seo.canonical.present", f"Canonical declared: {href}", {"url": href})
            score += 2
            absolute = urljoin(base_url, href)
            if absolute.rstrip("/") == base_url.rstrip("/"):
                emit_check(PASS, "result.checks.seo.canonical.self_referencing", "Canonical is self-referencing (correct for the homepage)")
                score += 1
            else:
                emit_check(WARN, "result.checks.seo.canonical.points_elsewhere", f"Homepage canonical points to a different URL: {absolute}", {"url": absolute})
                emit_fix("result.fixes.seo.canonical.self_reference",
                         "Unless this page is a deliberate duplicate, the canonical should point at itself. A wrong canonical de-indexes the page in favour of another.")
        else:
            emit_check(FAIL, "result.checks.seo.canonical.missing", "No canonical link — duplicate URLs will compete with each other")
            emit_fix("result.fixes.seo.canonical.add",
                     "Add a self-referencing canonical to every indexable page:\n  <link rel=\"canonical\" href=\"https://yoursite.com/page\" />\nUse absolute URLs and one canonical per page.")
    else:
        emit_check(FAIL, "result.checks.seo.canonical.fetch_failed", "Could not fetch homepage")

    if parsed.scheme == "https":
        http_resp = _raw_get(f"http://{parsed.netloc}/", allow_redirects=False, timeout=6)
        if http_resp is None:
            emit_check(INFO, "result.checks.seo.redirect.http_unreachable", "The HTTP version of the site is unreachable (acceptable — nothing to consolidate)")
            score += 2
        elif http_resp.status_code in (301, 308):
            emit_check(PASS, "result.checks.seo.redirect.http_permanent", f"HTTP redirects to HTTPS with a permanent {http_resp.status_code}", {"code": http_resp.status_code})
            score += 2
        elif http_resp.is_redirect:
            emit_check(WARN, "result.checks.seo.redirect.http_temporary", f"HTTP redirects to HTTPS with a temporary {http_resp.status_code} — link equity is not consolidated", {"code": http_resp.status_code})
            emit_fix("result.fixes.seo.redirect.make_permanent", "Change the HTTP → HTTPS redirect from 302/307 to 301. Temporary redirects do not pass ranking signals durably.")
            score += 1
        else:
            emit_check(FAIL, "result.checks.seo.redirect.http_serves_200", "The HTTP version serves content directly — the site is indexable under two protocols")
            emit_fix("result.fixes.seo.redirect.force_https", "Force HTTPS with a site-wide 301:\n  server { listen 80; server_name example.com; return 301 https://$host$request_uri; }")
    else:
        emit_check(FAIL, "result.checks.seo.redirect.not_https", "The site is being served over HTTP — HTTPS is a baseline ranking requirement")
        emit_fix("result.fixes.seo.redirect.enable_https", "Install a TLS certificate and 301-redirect all HTTP traffic to HTTPS.")

    host = parsed.netloc
    alt_host = host[4:] if host.startswith("www.") else f"www.{host}"
    alt_resp = _raw_get(f"{parsed.scheme}://{alt_host}/", allow_redirects=True, timeout=8)
    if alt_resp is None:
        emit_check(PASS, "result.checks.seo.redirect.alt_host_absent", f"{alt_host} does not resolve — no duplicate host to consolidate", {"host": alt_host})
        score += 2
    elif urlparse(alt_resp.url).netloc == host:
        emit_check(PASS, "result.checks.seo.redirect.alt_host_consolidated", f"{alt_host} redirects to {host}", {"alt": alt_host, "main": host})
        score += 2
    elif alt_resp.status_code == 200:
        emit_check(FAIL, "result.checks.seo.redirect.alt_host_duplicate", f"Both {host} and {alt_host} serve content — classic duplicate-content split", {"main": host, "alt": alt_host})
        # The params dict is required, not optional: the frontend renders the
        # localized fix from fix_key + fix_params, so hostnames baked only into
        # the English f-string would have nothing to interpolate from.
        emit_fix("result.fixes.seo.redirect.consolidate_host",
                 f"Pick one hostname and 301 the other to it:\n  server {{ server_name {alt_host}; return 301 {parsed.scheme}://{host}$request_uri; }}\nThen make the canonical match the chosen host.",
                 {"alt": alt_host, "scheme": parsed.scheme, "main": host})
    else:
        # Distinct from alt_host_absent (which is "does not resolve at all"):
        # the host answers but not with a page. Same score, different key —
        # one i18n key cannot carry two different sentences.
        emit_check(PASS, "result.checks.seo.redirect.alt_host_no_content", f"{alt_host} does not serve content (HTTP {alt_resp.status_code})", {"host": alt_host, "status": alt_resp.status_code})
        score += 2

    chain = _raw_get(base_url, allow_redirects=True, timeout=8)
    if chain is not None:
        hops = len(chain.history)
        if hops == 0:
            emit_check(PASS, "result.checks.seo.redirect.no_chain", "The URL resolves directly with no redirect hop")
            score += 1
        elif hops == 1:
            emit_check(PASS, "result.checks.seo.redirect.single_hop", "One redirect hop before the final URL — acceptable")
            score += 1
        else:
            emit_check(WARN, "result.checks.seo.redirect.long_chain", f"{hops} redirect hops before the final URL — each hop leaks crawl budget and signal", {"hops": hops})
            emit_fix("result.fixes.seo.redirect.flatten_chain", "Flatten redirect chains so every source URL points at the final destination in a single 301.")

    path = parsed.path or "/"
    issues = []
    if len(path.strip("/").split("/")) > 4 and path != "/":
        issues.append("depth")
    if any(c.isupper() for c in path):
        issues.append("uppercase")
    if parsed.query:
        issues.append("query string")
    if "_" in path:
        issues.append("underscores")
    if issues:
        emit_check(WARN, "result.checks.seo.url.unclean", f"URL hygiene issues: {', '.join(issues)}", {"issues": ", ".join(issues)})
        emit_fix("result.fixes.seo.url.clean",
                 "Use lowercase, hyphen-separated, parameter-free paths no more than three levels deep. Redirect the old form with a 301 when you change it.")
    else:
        emit_check(PASS, "result.checks.seo.url.clean", "URL is lowercase, shallow and free of parameters")
        score += 2

    track_seo_score("URL & Canonical", min(score, 10), 10)


# ---------------------------------------------------------------------------
# 6. Internal Linking
# ---------------------------------------------------------------------------
def check_seo_internal_links(base_url):
    """Link volume, anchor descriptiveness, and breadcrumb presence."""
    print("\n--- SEO: Internal Linking ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.links.fetch_failed", "Could not fetch homepage")
        track_seo_score("Internal Linking", 0, 8)
        return

    score = 0
    host = urlparse(base_url).netloc
    internal, external = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        target_host = urlparse(urljoin(base_url, href)).netloc
        (internal if target_host == host else external).append(a)

    if len(internal) >= 10:
        emit_check(PASS, "result.checks.seo.links.volume_ok", f"{len(internal)} internal links give crawlers a path into the site", {"count": len(internal)})
        score += 2
    elif len(internal) >= 3:
        emit_check(WARN, "result.checks.seo.links.volume_thin", f"Only {len(internal)} internal links — deep pages will be slow to discover", {"count": len(internal)})
        emit_fix("result.fixes.seo.links.add_internal", "Link the homepage to your main category and money pages. Ten to thirty internal links on a homepage is a healthy range.")
        score += 1
    else:
        emit_check(FAIL, "result.checks.seo.links.volume_none", f"Almost no internal links ({len(internal)}) — the site has no crawlable structure from the homepage", {"count": len(internal)})
        emit_fix("result.fixes.seo.links.add_internal", "Link the homepage to your main category and money pages. Ten to thirty internal links on a homepage is a healthy range.")

    if internal:
        generic, empty = 0, 0
        for a in internal:
            text = _text_of(a).lower()
            if not text:
                if not a.find("img", alt=True):
                    empty += 1
            elif text in _GENERIC_ANCHORS:
                generic += 1
        good_ratio = 1 - (generic + empty) / len(internal)
        if good_ratio >= 0.8:
            emit_check(PASS, "result.checks.seo.links.anchors_descriptive", f"{good_ratio * 100:.0f}% of internal anchors are descriptive", {"pct": f"{good_ratio * 100:.0f}"})
            score += 2
        else:
            emit_check(WARN, "result.checks.seo.links.anchors_generic", f"Only {good_ratio * 100:.0f}% of internal anchors are descriptive ({generic} generic, {empty} empty)", {"pct": f"{good_ratio * 100:.0f}", "generic": generic, "empty": empty})
            emit_fix("result.fixes.seo.links.rewrite_anchors",
                     "Replace \"click here\" / \"read more\" with the target topic, e.g. \"commercial cleaning robot pricing\". Anchor text is one of the few relevance signals you fully control.")
            score += 1

        if empty == 0:
            emit_check(PASS, "result.checks.seo.links.no_empty_anchors", "No empty anchors (image links carry alt text)")
            score += 1
        else:
            emit_check(WARN, "result.checks.seo.links.empty_anchors", f"{empty} links have neither text nor image alt text", {"count": empty})
            emit_fix("result.fixes.seo.links.fill_anchors", "Give every link either visible text or an image with descriptive alt text.")

        nofollowed = [a for a in internal if "nofollow" in (a.get("rel") or [])]
        if nofollowed:
            emit_check(WARN, "result.checks.seo.links.internal_nofollow", f"{len(nofollowed)} internal links carry rel=nofollow — internal link equity is being discarded", {"count": len(nofollowed)})
            emit_fix("result.fixes.seo.links.drop_internal_nofollow", "Remove rel=nofollow from internal links. Sculpting internal equity with nofollow has not worked for over a decade.")
        else:
            emit_check(PASS, "result.checks.seo.links.internal_followed", "All internal links are followable")
            score += 1

    has_breadcrumb = bool(
        soup.find(attrs={"class": re.compile(r"breadcrumb", re.I)})
        or soup.find("nav", attrs={"aria-label": re.compile(r"breadcrumb", re.I)})
        or "BreadcrumbList" in (resp.text if resp is not None else "")
    )
    if has_breadcrumb:
        emit_check(PASS, "result.checks.seo.links.breadcrumb_found", "Breadcrumb navigation present")
        score += 2
    else:
        emit_check(WARN, "result.checks.seo.links.breadcrumb_missing", "No breadcrumb navigation — hierarchy is harder to infer and rich breadcrumbs cannot render")
        emit_fix("result.fixes.seo.links.add_breadcrumb",
                 "Add breadcrumb navigation plus BreadcrumbList JSON-LD so the result page shows the path instead of a raw URL.")

    if external:
        emit_check(INFO, "result.checks.seo.links.external_count", f"{len(external)} outbound links found", {"count": len(external)})

    track_seo_score("Internal Linking", min(score, 8), 8)


# ---------------------------------------------------------------------------
# 7. Image SEO
# ---------------------------------------------------------------------------
def check_seo_images(base_url):
    """Alt coverage, layout-stability attributes, lazy loading, modern formats."""
    print("\n--- SEO: Image SEO ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.images.fetch_failed", "Could not fetch homepage")
        track_seo_score("Image SEO", 0, 7)
        return

    imgs = soup.find_all("img")
    if not imgs:
        emit_check(INFO, "result.checks.seo.images.none", "No <img> tags on the homepage — image checks do not apply")
        track_seo_score("Image SEO", 0, 0)
        return

    score = 0
    total = len(imgs)
    with_alt = [i for i in imgs if (i.get("alt") or "").strip()]
    coverage = len(with_alt) / total
    if coverage >= 0.9:
        emit_check(PASS, "result.checks.seo.images.alt_ok", f"{len(with_alt)}/{total} images have alt text", {"with": len(with_alt), "total": total})
        score += 3
    elif coverage >= 0.6:
        emit_check(WARN, "result.checks.seo.images.alt_partial", f"Only {len(with_alt)}/{total} images have alt text", {"with": len(with_alt), "total": total})
        emit_fix("result.fixes.seo.images.add_alt", "Give every content image descriptive alt text. Decorative images should carry alt=\"\" so screen readers and crawlers skip them.")
        score += 2
    elif coverage >= 0.3:
        emit_check(WARN, "result.checks.seo.images.alt_low", f"Only {len(with_alt)}/{total} images have alt text", {"with": len(with_alt), "total": total})
        emit_fix("result.fixes.seo.images.add_alt", "Give every content image descriptive alt text. Decorative images should carry alt=\"\" so screen readers and crawlers skip them.")
        score += 1
    else:
        emit_check(FAIL, "result.checks.seo.images.alt_missing", f"Only {len(with_alt)}/{total} images have alt text — image search traffic is forfeited", {"with": len(with_alt), "total": total})
        emit_fix("result.fixes.seo.images.add_alt", "Give every content image descriptive alt text. Decorative images should carry alt=\"\" so screen readers and crawlers skip them.")

    sized = [i for i in imgs if i.get("width") and i.get("height")]
    if len(sized) / total >= 0.7:
        emit_check(PASS, "result.checks.seo.images.dimensions_ok", f"{len(sized)}/{total} images declare width and height", {"with": len(sized), "total": total})
        score += 2
    else:
        emit_check(WARN, "result.checks.seo.images.dimensions_missing", f"Only {len(sized)}/{total} images declare width and height — layout shift hurts the page-experience signal", {"with": len(sized), "total": total})
        emit_fix("result.fixes.seo.images.add_dimensions", "Add width and height attributes (or a CSS aspect-ratio) to every image so the browser reserves space before the image loads.")

    lazy = [i for i in imgs if (i.get("loading") or "").lower() == "lazy"]
    if lazy:
        emit_check(PASS, "result.checks.seo.images.lazy_ok", f"{len(lazy)} images use native lazy loading", {"count": len(lazy)})
        score += 1
    else:
        emit_check(INFO, "result.checks.seo.images.lazy_missing", "No native lazy loading — below-the-fold images still block bandwidth")
        emit_fix("result.fixes.seo.images.add_lazy", "Add loading=\"lazy\" to below-the-fold images. Keep the hero image eager so the largest paint is not delayed.")

    modern = [i for i in imgs if re.search(r"\.(webp|avif)(\?|$)", (i.get("src") or ""), re.I)]
    responsive = [i for i in imgs if i.get("srcset")]
    if modern or responsive or soup.find("picture"):
        emit_check(PASS, "result.checks.seo.images.modern_ok", f"Modern delivery in use ({len(modern)} next-gen formats, {len(responsive)} responsive sources)", {"modern": len(modern), "responsive": len(responsive)})
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.images.legacy_only", "All images are legacy formats with no responsive sources")
        emit_fix("result.fixes.seo.images.modernize", "Serve WebP or AVIF with a <picture> fallback and provide srcset variants so mobile devices do not download desktop-sized files.")

    track_seo_score("Image SEO", min(score, 7), 7)


# ---------------------------------------------------------------------------
# 8. Rich Result Schema
# ---------------------------------------------------------------------------
def check_seo_rich_results(base_url):
    """Structured data types that unlock rich results on the search page."""
    print("\n--- SEO: Rich Result Schema ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.schema.fetch_failed", "Could not fetch homepage")
        track_seo_score("Rich Result Schema", 0, 6)
        return

    score = 0
    types = set()
    blocks = soup.find_all("script", type="application/ld+json")
    invalid = 0
    for block in blocks:
        raw = block.string or block.get_text() or ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            invalid += 1
            continue

        def walk(node):
            if isinstance(node, dict):
                t = node.get("@type")
                if isinstance(t, str):
                    types.add(t)
                elif isinstance(t, list):
                    types.update(x for x in t if isinstance(x, str))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)

    if not blocks:
        emit_check(FAIL, "result.checks.seo.schema.none", "No JSON-LD structured data — no rich result can render")
        emit_fix("result.fixes.seo.schema.add_base",
                 "Add Organization and WebSite JSON-LD to the homepage, then BreadcrumbList site-wide. Validate the markup with schema.org's official validator.")
        track_seo_score("Rich Result Schema", 0, 6)
        return

    if invalid:
        emit_check(FAIL, "result.checks.seo.schema.invalid_json", f"{invalid} JSON-LD block(s) failed to parse — invalid markup is ignored entirely", {"count": invalid})
        emit_fix("result.fixes.seo.schema.fix_json", "Fix the malformed JSON-LD. A single syntax error voids the whole block; validate with the Rich Results Test after every change.")

    if types & {"Organization", "LocalBusiness", "Corporation"}:
        emit_check(PASS, "result.checks.seo.schema.org_found", "Organization / LocalBusiness schema present — feeds the knowledge panel")
        score += 1.5
    else:
        emit_check(WARN, "result.checks.seo.schema.org_missing", "No Organization schema — brand entity data is left to inference")
        emit_fix("result.fixes.seo.schema.add_org",
                 "Add Organization JSON-LD with name, url, logo, sameAs (social and knowledge-base profiles) and contactPoint.")

    if "WebSite" in types:
        has_search = "SearchAction" in types or "potentialAction" in (resp.text if resp is not None else "")
        if has_search:
            emit_check(PASS, "result.checks.seo.schema.sitelinks_search", "WebSite schema with SearchAction — enables the sitelinks search box")
            score += 1.5
        else:
            emit_check(WARN, "result.checks.seo.schema.search_missing", "WebSite schema present but no SearchAction")
            emit_fix("result.fixes.seo.schema.add_searchaction", "Add potentialAction/SearchAction to the WebSite schema so the sitelinks search box can appear.")
            score += 0.75
    else:
        emit_check(WARN, "result.checks.seo.schema.website_missing", "No WebSite schema")
        emit_fix("result.fixes.seo.schema.add_website", "Add WebSite JSON-LD with url, name and potentialAction/SearchAction.")

    if "BreadcrumbList" in types:
        emit_check(PASS, "result.checks.seo.schema.breadcrumb_found", "BreadcrumbList schema present — the result shows the path instead of the raw URL")
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.schema.breadcrumb_missing", "No BreadcrumbList schema")
        emit_fix("result.fixes.seo.schema.add_breadcrumb", "Add BreadcrumbList JSON-LD matching the visible breadcrumb trail on every non-homepage template.")

    content_types = types & {"Article", "BlogPosting", "NewsArticle", "Product",
                             "FAQPage", "HowTo", "Event", "Recipe", "VideoObject",
                             "Service", "SoftwareApplication"}
    if content_types:
        emit_check(PASS, "result.checks.seo.schema.content_found", f"Content-level schema present: {', '.join(sorted(content_types))}", {"types": ", ".join(sorted(content_types))})
        score += 2
    else:
        emit_check(WARN, "result.checks.seo.schema.content_missing", "No content-level schema (Article / Product / FAQPage / HowTo …)")
        emit_fix("result.fixes.seo.schema.add_content_type",
                 "Mark up the page's actual content type — Article for posts, Product with offers for commerce, FAQPage for question blocks. This is what drives rich results.")

    track_seo_score("Rich Result Schema", min(score, 6), 6)


# ---------------------------------------------------------------------------
# 9. Page Speed Signals
# ---------------------------------------------------------------------------
def check_seo_speed_signals(base_url):
    """Server-side speed proxies. Not a Core Web Vitals measurement — these are
    the HTML-observable causes behind bad vitals."""
    print("\n--- SEO: Page Speed Signals ---")
    started = time.time()
    resp = _raw_get(base_url, allow_redirects=True, timeout=15)
    elapsed = time.time() - started
    if resp is None:
        emit_check(FAIL, "result.checks.seo.speed.fetch_failed", "Could not fetch homepage")
        track_seo_score("Page Speed Signals", 0, 8)
        return

    score = 0
    if elapsed < 0.6:
        emit_check(PASS, "result.checks.seo.speed.ttfb_fast", f"Server responded in {elapsed:.2f}s", {"seconds": f"{elapsed:.2f}"})
        score += 2
    elif elapsed < 1.5:
        emit_check(WARN, "result.checks.seo.speed.ttfb_fair", f"Server responded in {elapsed:.2f}s — aim for under 0.6s", {"seconds": f"{elapsed:.2f}"})
        emit_fix("result.fixes.seo.speed.improve_ttfb", "Reduce server response time with page caching, a CDN in front of the origin, and database query tuning.")
        score += 1
    else:
        emit_check(FAIL, "result.checks.seo.speed.ttfb_slow", f"Server responded in {elapsed:.2f}s — slow enough to depress both crawl rate and rankings", {"seconds": f"{elapsed:.2f}"})
        emit_fix("result.fixes.seo.speed.improve_ttfb", "Reduce server response time with page caching, a CDN in front of the origin, and database query tuning.")

    html_kb = len(resp.content) / 1024
    if html_kb < 150:
        emit_check(PASS, "result.checks.seo.speed.html_small", f"HTML document is {html_kb:.0f} KB", {"kb": f"{html_kb:.0f}"})
        score += 1.5
    elif html_kb < 300:
        emit_check(WARN, "result.checks.seo.speed.html_medium", f"HTML document is {html_kb:.0f} KB — trim toward 150 KB", {"kb": f"{html_kb:.0f}"})
        emit_fix("result.fixes.seo.speed.trim_html", "Shrink the HTML payload: move inline styles and scripts to cached external files, and drop server-rendered state blobs that the page does not need on first paint.")
        score += 0.75
    else:
        emit_check(FAIL, "result.checks.seo.speed.html_large", f"HTML document is {html_kb:.0f} KB — very heavy for a single document", {"kb": f"{html_kb:.0f}"})
        emit_fix("result.fixes.seo.speed.trim_html", "Shrink the HTML payload: move inline styles and scripts to cached external files, and drop server-rendered state blobs that the page does not need on first paint.")

    encoding = (resp.headers.get("Content-Encoding") or "").lower()
    if any(x in encoding for x in ("gzip", "br", "zstd", "deflate")):
        emit_check(PASS, "result.checks.seo.speed.compressed", f"Response is compressed ({encoding})", {"encoding": encoding})
        score += 1.5
    else:
        emit_check(FAIL, "result.checks.seo.speed.uncompressed", "Response is not compressed — the cheapest speed win is unclaimed")
        emit_fix("result.fixes.seo.speed.enable_compression", "Enable Brotli (or at least gzip) for HTML, CSS, JS and SVG at the web server or CDN edge.")

    cache_control = resp.headers.get("Cache-Control") or ""
    if cache_control:
        emit_check(PASS, "result.checks.seo.speed.cache_header", f"Cache-Control header set: {cache_control}", {"value": cache_control})
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.speed.cache_header_missing", "No Cache-Control header — every visit and every crawl re-downloads everything")
        emit_fix("result.fixes.seo.speed.add_cache_headers", "Set Cache-Control for HTML (short, revalidated) and static assets (long, immutable with a content hash in the filename).")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.content, "html.parser")
    head = soup.find("head")
    blocking = 0
    if head:
        for s in head.find_all("script", src=True):
            if not s.get("async") and not s.get("defer") and (s.get("type") or "") != "module":
                blocking += 1
    if blocking <= 2:
        emit_check(PASS, "result.checks.seo.speed.blocking_ok", f"{blocking} render-blocking script(s) in <head>", {"count": blocking})
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.speed.blocking_many", f"{blocking} render-blocking scripts in <head> — first paint waits for all of them", {"count": blocking})
        emit_fix("result.fixes.seo.speed.defer_scripts", "Add defer (or async where order does not matter) to head scripts, or move them to the end of <body>.")

    inline_bytes = sum(len(s.get_text() or "") for s in soup.find_all(["script", "style"]) if not s.get("src"))
    if inline_bytes < 50 * 1024:
        emit_check(PASS, "result.checks.seo.speed.inline_ok", f"{inline_bytes / 1024:.0f} KB of inline script/style", {"kb": f"{inline_bytes / 1024:.0f}"})
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.speed.inline_heavy", f"{inline_bytes / 1024:.0f} KB of inline script/style is re-downloaded on every page view", {"kb": f"{inline_bytes / 1024:.0f}"})
        emit_fix("result.fixes.seo.speed.externalize_inline", "Move large inline CSS/JS into cacheable external files; keep only the critical above-the-fold CSS inline.")

    track_seo_score("Page Speed Signals", min(score, 8), 8)


# ---------------------------------------------------------------------------
# 10. Mobile Usability
# ---------------------------------------------------------------------------
def check_seo_mobile(base_url):
    """Mobile-first indexing prerequisites that are visible in the HTML."""
    print("\n--- SEO: Mobile Usability ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.mobile.fetch_failed", "Could not fetch homepage")
        track_seo_score("Mobile Usability", 0, 5)
        return

    score = 0
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    content = (viewport.get("content") or "").lower() if viewport else ""
    if "width=device-width" in content:
        emit_check(PASS, "result.checks.seo.mobile.viewport_ok", "Viewport meta declares width=device-width")
        score += 2
    elif viewport:
        emit_check(WARN, "result.checks.seo.mobile.viewport_partial", f"Viewport meta present but unusual: {content}", {"value": content})
        emit_fix("result.fixes.seo.mobile.fix_viewport", "Use the standard viewport:\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
        score += 1
    else:
        emit_check(FAIL, "result.checks.seo.mobile.viewport_missing", "No viewport meta — the page renders zoomed-out on phones and fails mobile-first indexing")
        emit_fix("result.fixes.seo.mobile.fix_viewport", "Use the standard viewport:\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")

    if "user-scalable=no" in content or re.search(r"maximum-scale=1(\.0)?\b", content):
        emit_check(WARN, "result.checks.seo.mobile.zoom_blocked", "The viewport blocks zooming — an accessibility failure that also shows up in page-experience audits")
        emit_fix("result.fixes.seo.mobile.allow_zoom", "Remove user-scalable=no and maximum-scale from the viewport tag so users can zoom.")
    else:
        emit_check(PASS, "result.checks.seo.mobile.zoom_allowed", "Zooming is not blocked")
        score += 1

    if soup.find("picture") or soup.find("img", srcset=True) or soup.find("source", srcset=True):
        emit_check(PASS, "result.checks.seo.mobile.responsive_images", "Responsive image sources are in use")
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.mobile.no_responsive_images", "No responsive image sources — phones download desktop-sized images")
        emit_fix("result.fixes.seo.mobile.add_srcset", "Provide srcset/sizes or a <picture> element so each device downloads an appropriately sized image.")

    wide = 0
    for tag in soup.find_all(style=True):
        m = re.search(r"width\s*:\s*(\d{3,})px", tag["style"])
        if m and int(m.group(1)) > 480:
            wide += 1
    if wide == 0:
        emit_check(PASS, "result.checks.seo.mobile.no_fixed_width", "No fixed-width elements wider than a phone viewport")
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.mobile.fixed_width", f"{wide} element(s) use a fixed width above 480px — horizontal scrolling on phones", {"count": wide})
        emit_fix("result.fixes.seo.mobile.use_relative_width", "Replace fixed pixel widths with max-width:100%, percentages, or grid/flex layout so content reflows on narrow screens.")

    track_seo_score("Mobile Usability", min(score, 5), 5)


# ---------------------------------------------------------------------------
# 11. Crawl Directives & Sitemap
# ---------------------------------------------------------------------------
def check_seo_crawl_sitemap(base_url):
    """robots.txt hygiene, sitemap health, and honest 404s."""
    print("\n--- SEO: Crawl Directives & Sitemap ---")
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    score = 0

    robots = _raw_get(f"{root}/robots.txt", allow_redirects=True, timeout=6)
    sitemap_urls_declared = []
    if robots is not None and robots.status_code == 200:
        emit_check(PASS, "result.checks.seo.crawl.robots_found", "robots.txt is reachable")
        score += 1.5
        sitemap_urls_declared = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots.text)
        if sitemap_urls_declared:
            emit_check(PASS, "result.checks.seo.crawl.sitemap_declared", f"robots.txt declares {len(sitemap_urls_declared)} sitemap(s)", {"count": len(sitemap_urls_declared)})
            score += 1.5
        else:
            emit_check(WARN, "result.checks.seo.crawl.sitemap_not_declared", "robots.txt does not declare a Sitemap line")
            emit_fix("result.fixes.seo.crawl.declare_sitemap", "Add a Sitemap line to robots.txt:\n  Sitemap: https://yoursite.com/sitemap.xml")
    else:
        emit_check(WARN, "result.checks.seo.crawl.robots_missing", "No robots.txt — crawl budget and private paths are unmanaged")
        emit_fix("result.fixes.seo.crawl.add_robots", "Publish a robots.txt at the site root declaring your sitemap and disallowing only genuinely private paths.")

    sitemap_url = sitemap_urls_declared[0] if sitemap_urls_declared else f"{root}/sitemap.xml"
    sm = _raw_get(sitemap_url, allow_redirects=True, timeout=10)
    if sm is not None and sm.status_code == 200:
        try:
            tree = ElementTree.fromstring(sm.content)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            entries = tree.findall(".//sm:url", ns) or tree.findall(".//sm:sitemap", ns)
            emit_check(PASS, "result.checks.seo.crawl.sitemap_valid", f"Sitemap parses with {len(entries)} entries", {"count": len(entries)})
            score += 2
            if entries:
                with_lastmod = [e for e in entries if e.find("sm:lastmod", ns) is not None]
                if len(with_lastmod) / len(entries) >= 0.5:
                    emit_check(PASS, "result.checks.seo.crawl.lastmod_ok", f"{len(with_lastmod)}/{len(entries)} entries carry lastmod", {"with": len(with_lastmod), "total": len(entries)})
                    score += 1
                else:
                    emit_check(WARN, "result.checks.seo.crawl.lastmod_missing", f"Only {len(with_lastmod)}/{len(entries)} entries carry lastmod — crawlers cannot prioritize what changed", {"with": len(with_lastmod), "total": len(entries)})
                    emit_fix("result.fixes.seo.crawl.add_lastmod", "Emit an accurate <lastmod> per URL and update it only when the content genuinely changes.")
        except ElementTree.ParseError:
            emit_check(FAIL, "result.checks.seo.crawl.sitemap_invalid", "Sitemap is reachable but not valid XML")
            emit_fix("result.fixes.seo.crawl.fix_sitemap", "Regenerate the sitemap as valid XML against the sitemaps.org schema; validate it in Search Console after publishing.")
    else:
        emit_check(FAIL, "result.checks.seo.crawl.sitemap_missing", f"No sitemap at {sitemap_url}", {"url": sitemap_url})
        emit_fix("result.fixes.seo.crawl.add_sitemap", "Publish an XML sitemap listing every indexable URL, declare it in robots.txt, and submit it in Search Console.")

    probe = _raw_get(f"{root}/geo-seo-probe-404-{int(time.time())}", allow_redirects=False, timeout=6)
    if probe is None:
        emit_check(INFO, "result.checks.seo.crawl.404_unknown", "Could not probe 404 handling")
    elif probe.status_code == 404:
        emit_check(PASS, "result.checks.seo.crawl.404_correct", "Missing pages return a real 404 status")
        score += 2
    elif probe.status_code == 410:
        emit_check(PASS, "result.checks.seo.crawl.410_correct", "Missing pages return 410 Gone")
        score += 2
    elif probe.status_code == 200:
        emit_check(FAIL, "result.checks.seo.crawl.soft_404", "A non-existent URL returns HTTP 200 — soft 404s let junk URLs into the index")
        emit_fix("result.fixes.seo.crawl.fix_soft_404", "Return a real 404 (or 410) status for missing pages. A styled error page is fine, but the status code must not be 200.")
    else:
        emit_check(WARN, "result.checks.seo.crawl.404_unexpected", f"A non-existent URL returned HTTP {probe.status_code}", {"code": probe.status_code})
        emit_fix("result.fixes.seo.crawl.fix_soft_404", "Return a real 404 (or 410) status for missing pages. A styled error page is fine, but the status code must not be 200.")

    track_seo_score("Crawl & Sitemap", min(score, 8), 8)


# ---------------------------------------------------------------------------
# 12. International Targeting
# ---------------------------------------------------------------------------
def check_seo_international(base_url):
    """Language declaration and hreflang correctness.

    Conditional denominator: a single-language site is not penalized for having
    no hreflang, so only the `lang` check counts toward max in that case. Same
    precedent as `check_multilingual_depth` on the GEO side.
    """
    print("\n--- SEO: International Targeting ---")
    resp, soup = get_soup(base_url)
    if not soup:
        emit_check(FAIL, "result.checks.seo.intl.fetch_failed", "Could not fetch homepage")
        track_seo_score("International", 0, 4)
        return

    score = 0
    html_tag = soup.find("html")
    lang = (html_tag.get("lang") or "").strip() if html_tag else ""
    if lang and re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$", lang):
        emit_check(PASS, "result.checks.seo.intl.lang_ok", f"Document language declared: {lang}", {"lang": lang})
        score += 1
    elif lang:
        emit_check(WARN, "result.checks.seo.intl.lang_malformed", f"lang=\"{lang}\" is not a well-formed language tag", {"lang": lang})
        emit_fix("result.fixes.seo.intl.fix_lang", "Use a valid BCP 47 tag on <html>, e.g. lang=\"zh-CN\" or lang=\"en-US\".")
    else:
        emit_check(FAIL, "result.checks.seo.intl.lang_missing", "No lang attribute on <html>")
        emit_fix("result.fixes.seo.intl.fix_lang", "Use a valid BCP 47 tag on <html>, e.g. lang=\"zh-CN\" or lang=\"en-US\".")

    alternates = soup.find_all("link", rel="alternate", hreflang=True)
    if not alternates:
        emit_check(INFO, "result.checks.seo.intl.single_language", "No hreflang tags — treated as a single-language site, so hreflang rules do not apply")
        track_seo_score("International", score, 1)
        return

    codes = [(a.get("hreflang") or "").strip() for a in alternates]
    emit_check(PASS, "result.checks.seo.intl.hreflang_found", f"hreflang declared for: {', '.join(codes)}", {"langs": ", ".join(codes)})

    if any(c.lower() == "x-default" for c in codes):
        emit_check(PASS, "result.checks.seo.intl.xdefault_ok", "x-default fallback is declared")
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.intl.xdefault_missing", "No x-default — users outside the declared locales get no defined fallback")
        emit_fix("result.fixes.seo.intl.add_xdefault", "Add an x-default alternate pointing at the language selector or the global default page.")

    self_ref = any(urljoin(base_url, a.get("href") or "").rstrip("/") == base_url.rstrip("/") for a in alternates)
    if self_ref:
        emit_check(PASS, "result.checks.seo.intl.self_referencing", "The hreflang set includes this page itself")
        score += 1
    else:
        emit_check(WARN, "result.checks.seo.intl.no_self_reference", "The hreflang set does not reference this page — incomplete sets are ignored wholesale")
        emit_fix("result.fixes.seo.intl.add_self_reference", "Every page in an hreflang cluster must list all alternates including itself, and each alternate must link back.")

    bad = [c for c in codes if c.lower() != "x-default" and not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$", c)]
    if bad:
        emit_check(FAIL, "result.checks.seo.intl.codes_invalid", f"Malformed hreflang values: {', '.join(bad)}", {"codes": ", ".join(bad)})
        emit_fix("result.fixes.seo.intl.fix_codes", "hreflang takes a language code, optionally with a region: zh-CN, en-GB, es. Country-only values such as \"cn\" are invalid.")
    else:
        emit_check(PASS, "result.checks.seo.intl.codes_valid", "All hreflang values are well-formed")
        score += 1

    track_seo_score("International", min(score, 4), 4)
