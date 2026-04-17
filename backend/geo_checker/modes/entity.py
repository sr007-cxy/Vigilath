"""entity_audit — 8-dimension entity GEO audit for brand / product / person.

Migrated from /geo_checker.py lines 6368-7174.
Depends on ai.py for engine queries + analysis helpers.
"""

import concurrent.futures
import json
import os
import re
import sys
import threading
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..constants import PASS, WARN, FAIL, INFO, FIX
from ..io import fetch, get_soup, get_text_content
from ..output import print, emit_check, emit_fix, fix, _pad
from ..state import (
    SHOW_FIX, _scores, _page_cache, reset_state, track_score,
    get_ai_visibility_score, get_grade,
)
from ..ai import (
    _query_perplexity, _query_openai, _query_anthropic,
    _query_deepseek, _query_doubao,
    _check_brand_in_result, _extract_competitors, _classify_framing,
)


def _check_knowledge_graph(entity_name, entity_type):
    """Check entity presence in Wikipedia, Wikidata, and Google Knowledge Panel signals.
    Returns (score 0-20, details dict)."""
    import urllib.parse
    score = 0
    details = {"wikipedia": False, "wikidata": False, "wikidata_id": None,
               "google_kg": False, "platforms_found": []}

    # 1. Wikipedia check — search API
    try:
        enc = urllib.parse.quote(entity_name)
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={enc}&format=json&srlimit=3"
        r = requests.get(url, timeout=10, headers={"User-Agent": "GEO-Checker/1.0"})
        if r.status_code == 200:
            data = r.json()
            results = data.get("query", {}).get("search", [])
            for result in results:
                if entity_name.lower() in result.get("title", "").lower():
                    details["wikipedia"] = True
                    details["platforms_found"].append("Wikipedia")
                    score += 7
                    break
    except Exception:
        pass

    # 2. Wikidata check — search API
    try:
        enc = urllib.parse.quote(entity_name)
        url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={enc}&language=en&format=json&limit=3"
        r = requests.get(url, timeout=10, headers={"User-Agent": "GEO-Checker/1.0"})
        if r.status_code == 200:
            data = r.json()
            results = data.get("search", [])
            for result in results:
                if entity_name.lower() in result.get("label", "").lower():
                    details["wikidata"] = True
                    details["wikidata_id"] = result.get("id")
                    details["platforms_found"].append("Wikidata")
                    score += 7
                    break
    except Exception:
        pass

    # 3. Google Knowledge Panel signal — check if structured org/person data exists
    # We probe Google via a simple search snippet (limited but free)
    try:
        enc = urllib.parse.quote(entity_name)
        url = f"https://kgsearch.googleapis.com/v1/entities:search?query={enc}&limit=3&indent=True&key=none"
        # Without a valid API key this returns 403, but we can try the free tier
        # Fallback: check if entity has a Wikipedia page (strong KG signal)
        if details["wikipedia"]:
            details["google_kg"] = True
            details["platforms_found"].append("Google KG (inferred)")
            score += 6
    except Exception:
        pass

    # If no Wikipedia but has Wikidata, partial KG credit
    if not details["google_kg"] and details["wikidata"]:
        score += 3

    return min(20, score), details


def _check_cross_platform_footprint(entity_name, entity_type):
    """Check entity presence across platforms AI models train on.
    Uses the same probe logic as check_cross_platform() for consistency.
    Returns (score 0-20, details dict)."""
    found = []
    not_found = []

    # Derive a URL-friendly brand slug from entity name
    brand = entity_name.lower().replace(" ", "").replace("-", "").replace("_", "")
    brand_slug = entity_name.lower().replace(" ", "-")

    # Same platform definitions as check_cross_platform() — probe real profile URLs
    platforms = {
        "X / Twitter": {
            "probe_urls": [f"https://x.com/{brand}", f"https://twitter.com/{brand}"],
        },
        "LinkedIn": {
            "probe_urls": [f"https://www.linkedin.com/company/{brand_slug}"]
                if entity_type != "person"
                else [f"https://www.linkedin.com/in/{brand_slug}"],
        },
        "YouTube": {
            "probe_urls": [f"https://www.youtube.com/@{brand}", f"https://www.youtube.com/c/{brand}"],
        },
        "GitHub": {
            "probe_urls": [f"https://github.com/{brand}"],
            "api_url": f"https://api.github.com/search/{'users' if entity_type == 'person' else 'repositories'}?q={entity_name}",
        },
        "Reddit": {
            "probe_urls": [f"https://www.reddit.com/r/{brand}", f"https://www.reddit.com/user/{brand}"],
        },
        "Facebook": {
            "probe_urls": [f"https://www.facebook.com/{brand}"],
        },
        "Medium": {
            "probe_urls": [f"https://medium.com/@{brand}", f"https://{brand}.medium.com"],
        },
        "TikTok": {
            "probe_urls": [f"https://www.tiktok.com/@{brand}"],
        },
    }

    # Same soft-404 detection as check_cross_platform()
    soft_404_phrases = [
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
    ]

    browser_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    for plat_name, plat_info in platforms.items():
        plat_found = False

        # Try GitHub API first if available (more reliable)
        api_url = plat_info.get("api_url")
        if api_url and not plat_found:
            try:
                r = requests.get(api_url, timeout=10, headers={"User-Agent": "GEO-Checker/1.0"})
                if r.status_code == 200:
                    data = r.json()
                    if data.get("total_count", 0) > 0:
                        plat_found = True
            except Exception:
                pass

        # Probe profile URLs (same logic as check_cross_platform)
        if not plat_found:
            for probe_url in plat_info["probe_urls"]:
                try:
                    r = requests.get(probe_url, timeout=8, allow_redirects=True,
                                     headers={"User-Agent": browser_ua})
                    if r.status_code == 200:
                        final_url = r.url.lower()
                        redirected_to_login = any(seg in final_url for seg in [
                            "/login", "/signin", "/sign_in", "/accounts/login",
                        ])
                        text_lower = r.text[:500000].lower() if len(r.text) < 500000 else r.text[:500000].lower()
                        is_404_page = redirected_to_login or any(p in text_lower for p in soft_404_phrases)
                        if not is_404_page:
                            plat_found = True
                            break
                except requests.RequestException:
                    pass

        if plat_found:
            found.append(plat_name)
        else:
            not_found.append(plat_name)

    total_platforms = len(platforms)
    if total_platforms == 0:
        return 0, {"found": found, "not_found": not_found}

    rate = len(found) / total_platforms
    score = round(rate * 20)
    return min(20, score), {"found": found, "not_found": not_found}


def entity_audit(entity_name, entity_type="brand", return_data=False):
    """Audit GEO readiness of a brand, product, or person via AI engine queries.

    When return_data=True, returns a dict with full scorecard, key findings,
    and content gaps. Raises RuntimeError on missing/invalid API key instead
    of sys.exit.
    """
    import os

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        if return_data:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        print(f"\n  [{FAIL}] OPENROUTER_API_KEY environment variable not set.")
        print(f"  This feature requires an OpenRouter API key.")
        print(f"  Set it with: export OPENROUTER_API_KEY='your-openrouter-api-key'")
        print(f"  Get an API key at: https://openrouter.ai/")
        sys.exit(1)

    entity_type = entity_type.lower()
    if entity_type not in ("brand", "product", "person"):
        if return_data:
            raise ValueError("entity_type must be one of: brand, product, person")
        print(f"\n  [{FAIL}] --entity-type must be one of: brand, product, person")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  ENTITY GEO AUDIT: \"{entity_name}\" ({entity_type})")
    print(f"{'='*60}\n")
    print(f"  Engine: OpenAI GPT-4o-mini (web search)")

    # ── Phase 1: Free checks (no API key needed) ──────────────
    print(f"\n  Phase 1: Knowledge Graph & Platform checks...\n")

    kg_score, kg_details = _check_knowledge_graph(entity_name, entity_type)
    for p in kg_details["platforms_found"]:
        print(f"    ✓ Knowledge Graph: Found on {p}")
    if not kg_details["platforms_found"]:
        print(f"    ✗ Knowledge Graph: Not found on Wikipedia or Wikidata")

    plat_score, plat_details = _check_cross_platform_footprint(entity_name, entity_type)
    for p in plat_details["found"]:
        print(f"    ✓ Platform: {p}")
    for p in plat_details["not_found"]:
        print(f"    ✗ Platform: {p}")

    # ── Phase 2: AI engine queries ────────────────────────────
    print(f"\n  Phase 2: AI engine queries...\n")

    # ── Build queries per entity type ──────────────────────────
    identity_queries = []
    category_queries = []
    competitive_queries = []
    gap_queries = []

    if entity_type == "brand":
        identity_queries = [
            f"What is {entity_name}?",
            f"What does {entity_name} do?",
            f"Is {entity_name} a reliable and trustworthy company?",
        ]
        competitive_queries = [
            f"Best alternatives to {entity_name}",
            f"{entity_name} review",
            f"How does {entity_name} compare to its competitors?",
        ]
        category_queries = [
            f"What industry is {entity_name} in?",
            f"What is {entity_name} known for?",
        ]
        gap_queries = [
            f"Top companies in the same space as {entity_name}",
            f"Best tools or services like {entity_name}",
        ]
    elif entity_type == "product":
        identity_queries = [
            f"What is {entity_name}?",
            f"What does {entity_name} do?",
            f"Is {entity_name} worth it?",
        ]
        competitive_queries = [
            f"Best alternatives to {entity_name}",
            f"{entity_name} pros and cons",
            f"How does {entity_name} compare to competitors?",
        ]
        category_queries = [
            f"What category of product is {entity_name}?",
            f"What problem does {entity_name} solve?",
        ]
        gap_queries = [
            f"Best products similar to {entity_name}",
            f"Top tools in the same category as {entity_name}",
        ]
    elif entity_type == "person":
        identity_queries = [
            f"Who is {entity_name}?",
            f"What is {entity_name} known for?",
            f"What are {entity_name}'s main contributions?",
        ]
        competitive_queries = [
            f"Top experts in the same field as {entity_name}",
            f"Leaders similar to {entity_name}",
        ]
        category_queries = [
            f"What field or industry is {entity_name} associated with?",
            f"What is {entity_name}'s area of expertise?",
        ]
        gap_queries = [
            f"Most influential people in {entity_name}'s field",
            f"Key thought leaders like {entity_name}",
        ]

    # ── Run queries ────────────────────────────────────────────
    STABILITY_RUNS = 3
    entity_lower = entity_name.lower()

    all_answers = []  # flat list of all answer strings for sentiment analysis

    def run_queries(queries, label):
        """Run a set of queries with stability runs, return list of (query, [answers])."""
        results = []
        for q in queries:
            answers = []
            for run in range(STABILITY_RUNS):
                answer, citations, error = _query_openai(q, api_key)
                if error == "invalid_key":
                    if return_data:
                        raise RuntimeError("Invalid OPENROUTER_API_KEY")
                    print(f"  [{FAIL}] Invalid OpenRouter API key. Check your OPENROUTER_API_KEY.")
                    sys.exit(1)
                if error:
                    answers.append("")
                else:
                    answers.append(answer)
                    all_answers.append(answer)
                time.sleep(1)
            results.append((q, answers))
            print(f"    ✓ {label}: \"{q[:50]}{'...' if len(q) > 50 else ''}\"")
        return results

    identity_results = run_queries(identity_queries, "Identity")
    category_results = run_queries(category_queries, "Category")
    competitive_results = run_queries(competitive_queries, "Competitive")
    gap_results = run_queries(gap_queries, "Gap")

    print(f"\n  Analysis complete. Scoring...\n")

    # ── 1. Entity Recognition (0-20) ──────────────────────────
    recognition_score = 0
    recognized_count = 0
    total_identity_runs = 0
    not_found_phrases = [
        "i don't have", "i'm not sure", "i couldn't find", "no information",
        "i don't know", "not familiar with", "i cannot find", "doesn't appear",
        "does not appear", "no results", "unknown", "not widely known",
    ]

    for q, answers in identity_results:
        for ans in answers:
            total_identity_runs += 1
            if not ans:
                continue
            ans_lower = ans.lower()
            is_not_found = any(p in ans_lower for p in not_found_phrases)
            has_entity = entity_lower in ans_lower
            if has_entity and not is_not_found:
                recognized_count += 1

    if total_identity_runs > 0:
        rec_rate = recognized_count / total_identity_runs
        recognition_score = round(rec_rate * 20)

    # ── 2. Entity Clarity (0-20) ──────────────────────────────
    clarity_score = 0
    confusion_detected = False
    first_run_answers = [answers[0] for _, answers in identity_results if answers[0]]

    if len(first_run_answers) >= 2:
        primary_answers = identity_results[0][1] if identity_results else []
        primary_answers = [a for a in primary_answers if a]

        def extract_key_phrases(text):
            sentences = re.split(r'[.!?]', text.lower())
            return [s.strip() for s in sentences if entity_lower in s and len(s.strip()) > 20]

        all_key_phrases = []
        for ans in primary_answers:
            all_key_phrases.extend(extract_key_phrases(ans))

        consistency_count = 0
        if len(primary_answers) >= 2:
            entity_mention_runs = sum(1 for a in primary_answers if entity_lower in a.lower())
            consistency_count = entity_mention_runs

        if entity_type in ("brand", "product"):
            for ans in primary_answers:
                ans_lower = ans.lower()
                if "not to be confused" in ans_lower or "should not be confused" in ans_lower:
                    confusion_detected = True
                if "different from" in ans_lower and entity_lower in ans_lower:
                    confusion_detected = True
        elif entity_type == "person":
            for ans in primary_answers:
                if "also known as" in ans.lower() or "not to be confused" in ans.lower():
                    confusion_detected = True

        if primary_answers:
            base = min(8, round((len(primary_answers) / STABILITY_RUNS) * 8))
            consistency_bonus = min(8, round((consistency_count / STABILITY_RUNS) * 8))
            confusion_penalty = 6 if confusion_detected else 0
            clarity_score = max(0, min(20, base + consistency_bonus - confusion_penalty + (4 if all_key_phrases else 0)))
    elif len(first_run_answers) == 1:
        clarity_score = 8

    # ── 3. Category Association (0-20) ────────────────────────
    category_score = 0
    category_associations = []

    for q, answers in category_results:
        for ans in answers:
            if not ans:
                continue
            if entity_lower in ans.lower():
                category_associations.append(ans)

    if category_associations:
        assoc_rate = len(category_associations) / (len(category_queries) * STABILITY_RUNS)
        category_score = round(assoc_rate * 20)

    # ── 4. Competitive Position (0-20) ────────────────────────
    comp_position_score = 0
    all_framings = []

    for q, answers in competitive_results:
        for ans in answers:
            if not ans:
                continue
            framing = _classify_framing(ans, entity_name)
            all_framings.append(framing)
            if entity_lower in ans.lower():
                all_framings.append("present")

    framing_scores = {
        "recommended": 20, "leader": 16, "option": 12,
        "mentioned": 8, "present": 8, "niche": 6, "not_mentioned": 0,
    }
    if all_framings:
        best_framing = max(all_framings, key=lambda f: framing_scores.get(f, 0))
        avg_score = sum(framing_scores.get(f, 0) for f in all_framings) / len(all_framings)
        comp_position_score = round(framing_scores.get(best_framing, 0) * 0.6 + avg_score * 0.4)
        comp_position_score = min(20, comp_position_score)

    # ── 5. Sentiment & Framing (0-20) ─────────────────────────
    sentiment_score = 0
    positive_signals = [
        "excellent", "outstanding", "highly recommended", "best", "top",
        "leading", "innovative", "trusted", "reliable", "widely used",
        "popular", "well-known", "well-regarded", "respected", "acclaimed",
        "pioneering", "influential", "groundbreaking", "notable", "renowned",
    ]
    negative_signals = [
        "controversial", "criticized", "problematic", "unreliable", "scam",
        "fraud", "poor", "worst", "avoid", "complaint", "lawsuit",
        "scandal", "failed", "bankrupt", "shut down", "discontinued",
    ]
    action_signals = [
        f"use {entity_lower}", f"try {entity_lower}", f"consider {entity_lower}",
        f"recommend {entity_lower}", f"choose {entity_lower}", f"go with {entity_lower}",
        f"check out {entity_lower}", f"look into {entity_lower}",
    ]

    pos_count = 0
    neg_count = 0
    action_count = 0
    total_analyzed = 0

    for ans in all_answers:
        if not ans:
            continue
        ans_lower = ans.lower()
        total_analyzed += 1
        for sig in positive_signals:
            if sig in ans_lower:
                pos_count += 1
                break
        for sig in negative_signals:
            if sig in ans_lower:
                neg_count += 1
                break
        for sig in action_signals:
            if sig in ans_lower:
                action_count += 1
                break

    if total_analyzed > 0:
        pos_rate = pos_count / total_analyzed
        neg_rate = neg_count / total_analyzed
        action_rate = action_count / total_analyzed
        sentiment_score = round(pos_rate * 12 + action_rate * 8 - neg_rate * 8)
        sentiment_score = max(0, min(20, sentiment_score))

    if total_analyzed == 0:
        sentiment_label = "unknown"
    elif neg_count > pos_count:
        sentiment_label = "negative" if neg_count > pos_count * 2 else "mixed"
    elif pos_count > 0 and action_count > 0:
        sentiment_label = "strongly positive"
    elif pos_count > 0:
        sentiment_label = "positive"
    else:
        sentiment_label = "neutral"

    # ── 6. Content Gap Analysis (0-20) ────────────────────────
    gap_score = 20
    content_gaps = []

    for q, answers in gap_results:
        mentioned_in_any = False
        for ans in answers:
            if ans and entity_lower in ans.lower():
                mentioned_in_any = True
                break
        if not mentioned_in_any:
            content_gaps.append(q)
            gap_score -= 5

    for q, answers in competitive_results:
        mentioned_in_any = False
        for ans in answers:
            if ans and entity_lower in ans.lower():
                mentioned_in_any = True
                break
        if not mentioned_in_any:
            content_gaps.append(q)
            gap_score -= 3

    gap_score = max(0, gap_score)

    # ── Composite Score & Report ──────────────────────────────
    scores = {
        "Entity Recognition": recognition_score,
        "Entity Clarity": clarity_score,
        "Category Association": category_score,
        "Competitive Position": comp_position_score,
        "Sentiment & Framing": sentiment_score,
        "Content Gap": gap_score,
        "Knowledge Graph": kg_score,
        "Platform Footprint": plat_score,
    }
    total_score = sum(scores.values())
    max_score = 160
    pct = round((total_score / max_score) * 100)

    if pct >= 90:
        grade = "A+"
    elif pct >= 80:
        grade = "A"
    elif pct >= 70:
        grade = "B"
    elif pct >= 60:
        grade = "C"
    elif pct >= 50:
        grade = "D"
    else:
        grade = "F"

    # Print scorecard
    print(f"  {'─'*50}")
    print(f"  ENTITY GEO SCORECARD")
    print(f"  {'─'*50}")

    for cat, sc in scores.items():
        bar_pct = sc / 20
        bar_len = round(bar_pct * 20)
        if bar_pct >= 0.7:
            bc = "\033[92m"
        elif bar_pct >= 0.4:
            bc = "\033[93m"
        else:
            bc = "\033[91m"
        bar = bc + "█" * bar_len + "\033[0m" + "░" * (20 - bar_len)
        print(f"    {_pad(cat, 24)} {sc:>2}/20  {bar}  {round(bar_pct*100):>3}%")

    print(f"    {'─'*44}")
    print(f"    {_pad('ENTITY GEO SCORE', 24)} {total_score:>3}/{max_score}")
    print(f"\n    Grade: {grade} ({pct}%)")

    # ── Key Findings ──────────────────────────────────────────
    print(f"\n  {'─'*50}")
    print(f"  KEY FINDINGS")
    print(f"  {'─'*50}")

    # Knowledge Graph finding
    if kg_score >= 14:
        print(f"    [{PASS}] Strong Knowledge Graph presence ({', '.join(kg_details['platforms_found'])})")
    elif kg_score >= 7:
        print(f"    [{WARN}] Partial Knowledge Graph presence ({', '.join(kg_details['platforms_found'])})")
        if not kg_details["wikipedia"]:
            fix(f"Create a Wikipedia article for \"{entity_name}\". Wikipedia is the #1 source\n"
                f"AI models use for entity recognition. Ensure it meets Wikipedia's notability\n"
                f"guidelines (third-party coverage in reliable sources).")
        if not kg_details["wikidata"]:
            fix(f"Create a Wikidata entry for \"{entity_name}\" at https://www.wikidata.org.\n"
                f"Wikidata is the structured knowledge base behind Google Knowledge Panel,\n"
                f"Wikipedia infoboxes, and many AI training pipelines.\n"
                f"Include: description, official website, social profiles, instance-of type.")
    else:
        print(f"    [{FAIL}] Not found in Knowledge Graph (Wikipedia, Wikidata)")
        fix(f"Establish \"{entity_name}\" as a recognized entity:\n"
            f"1. Create a Wikidata entry at https://www.wikidata.org with structured data\n"
            f"   (description, official website, social profiles, instance-of type)\n"
            f"2. If eligible, create a Wikipedia article with third-party reliable sources\n"
            f"3. Add JSON-LD Organization/Person schema to your website with sameAs links\n"
            f"   pointing to Wikidata, Wikipedia, and social profiles\n"
            f"4. Ensure consistent name/description across all platforms")

    # Platform Footprint finding
    if plat_score >= 14:
        print(f"    [{PASS}] Strong platform footprint ({len(plat_details['found'])}/{len(plat_details['found']) + len(plat_details['not_found'])} platforms)")
    elif plat_score >= 8:
        print(f"    [{WARN}] Moderate platform footprint ({len(plat_details['found'])}/{len(plat_details['found']) + len(plat_details['not_found'])} platforms)")
        missing = plat_details["not_found"]
        if missing:
            fix(f"Establish presence on missing platforms: {', '.join(missing)}\n"
                f"AI models train on data from these platforms. Each platform you're present on\n"
                f"increases the chance AI engines recognize and recommend \"{entity_name}\".\n"
                f"Priority: GitHub (for tech), LinkedIn (for professional), YouTube (for reach).")
    else:
        print(f"    [{FAIL}] Weak platform footprint ({len(plat_details['found'])}/{len(plat_details['found']) + len(plat_details['not_found'])} platforms)")
        missing = plat_details["not_found"]
        fix(f"Urgently establish presence on major platforms: {', '.join(missing)}\n"
            f"AI models learn about entities from cross-platform signals. Without presence\n"
            f"on platforms like GitHub, LinkedIn, YouTube, and Reddit, AI engines have\n"
            f"very little data to learn about \"{entity_name}\".\n"
            f"Steps:\n"
            f"1. Create profiles on each missing platform with consistent branding\n"
            f"2. Publish content regularly — AI training data favors active accounts\n"
            f"3. Cross-link all profiles (helps AI connect them as one entity)")

    # Recognition finding
    if recognition_score >= 16:
        print(f"    [{PASS}] AI consistently recognizes \"{entity_name}\"")
    elif recognition_score >= 8:
        print(f"    [{WARN}] AI partially recognizes \"{entity_name}\" — inconsistent across queries")
        if entity_type == "person":
            fix(f"Strengthen AI recognition of \"{entity_name}\":\n"
                f"1. Publish authored content on platforms AI trains on (Medium, LinkedIn, GitHub)\n"
                f"2. Get quoted or featured in industry publications and news articles\n"
                f"3. Ensure your personal website has clear JSON-LD Person schema\n"
                f"4. Maintain consistent bio/description across all platforms")
        else:
            fix(f"Strengthen AI recognition of \"{entity_name}\":\n"
                f"1. Publish a clear \"About\" page with JSON-LD Organization schema\n"
                f"2. Get listed in industry directories and comparison sites\n"
                f"3. Earn mentions in third-party articles and reviews\n"
                f"4. Create a llms.txt file at your site root explaining what {entity_name} is")
    else:
        print(f"    [{FAIL}] AI does not reliably recognize \"{entity_name}\"")
        if entity_type == "person":
            fix(f"AI engines do not recognize \"{entity_name}\". To fix this:\n"
                f"1. Build a personal website with comprehensive bio and JSON-LD Person schema\n"
                f"2. Publish regularly on Medium, LinkedIn, and/or a personal blog\n"
                f"3. Contribute to open-source projects on GitHub\n"
                f"4. Get featured in interviews, podcasts, or industry publications\n"
                f"5. Create a Wikipedia page if notability criteria are met\n"
                f"6. Create a Wikidata entry with structured facts about your career")
        else:
            fix(f"AI engines do not recognize \"{entity_name}\". To fix this:\n"
                f"1. Create a comprehensive website with clear About, Product, and FAQ pages\n"
                f"2. Add JSON-LD Organization schema with name, description, sameAs links\n"
                f"3. Create a llms.txt file explaining what {entity_name} is and does\n"
                f"4. Get listed on review platforms (G2, Capterra, Trustpilot, Product Hunt)\n"
                f"5. Publish content on Medium, LinkedIn, and your own blog\n"
                f"6. Create Wikipedia and Wikidata entries if notability criteria are met")

    # Clarity finding
    if clarity_score >= 16:
        print(f"    [{PASS}] Entity definition is clear and consistent across runs")
    elif clarity_score >= 8:
        print(f"    [{WARN}] Entity definition varies between queries — may indicate ambiguity")
        fix(f"AI gives inconsistent descriptions of \"{entity_name}\". To improve clarity:\n"
            f"1. Use the exact same tagline/description on every platform and page\n"
            f"2. Lead your homepage, About page, and llms.txt with a clear one-sentence definition\n"
            f"3. Ensure JSON-LD schema description matches your official tagline\n"
            f"4. If the name is ambiguous, add disambiguation content (e.g., \"{entity_name} (the company)\")")
    else:
        print(f"    [{FAIL}] Entity definition is unclear or confused with other entities")
        if confusion_detected:
            fix(f"AI actively confuses \"{entity_name}\" with other entities. Urgent fixes:\n"
                f"1. Add explicit disambiguation to your About page and JSON-LD description\n"
                f"2. Create a FAQ addressing \"What is {entity_name}?\" directly\n"
                f"3. Publish comparison/differentiation content vs commonly confused entities\n"
                f"4. Ensure your Wikidata entry has correct instance-of and distinct-from claims\n"
                f"5. Use consistent, unique branding language that sets you apart")
        else:
            fix(f"AI cannot clearly define \"{entity_name}\". To establish a clear identity:\n"
                f"1. Write a clear, concise definition and use it everywhere consistently\n"
                f"2. Structure your homepage to answer \"What is {entity_name}?\" in the first paragraph\n"
                f"3. Add FAQ schema with identity questions and clear answers\n"
                f"4. Create llms.txt with an unambiguous description at the top")

    # Category finding
    if category_score >= 14:
        print(f"    [{PASS}] Strong category/domain association detected")
    elif category_score >= 8:
        print(f"    [{WARN}] Weak category association — AI may not map entity to its core domain")
        fix(f"Strengthen category association for \"{entity_name}\":\n"
            f"1. Explicitly state your category/industry on your homepage and About page\n"
            f"   (e.g., \"{entity_name} is a [category] that...\")\n"
            f"2. Publish content using category keywords throughout (blog posts, guides)\n"
            f"3. Get listed in category-specific directories and comparison sites\n"
            f"4. Ensure JSON-LD schema uses the most specific @type for your category")
    else:
        print(f"    [{FAIL}] AI does not clearly associate entity with a specific category")
        fix(f"AI cannot determine what category \"{entity_name}\" belongs to. To fix:\n"
            f"1. Lead every platform bio with: \"{entity_name} is a [specific category]...\"\n"
            f"2. Create a \"What is {entity_name}\" page optimized for your core category terms\n"
            f"3. Publish comparison content: \"{entity_name} vs [category competitors]\"\n"
            f"4. Get featured in \"Best [category]\" listicles and roundup articles\n"
            f"5. Add industry/category keywords to your JSON-LD schema and meta descriptions\n"
            f"6. Create topical content clusters around your core category")

    # Competitive position finding
    best_framing_label = "unknown"
    if all_framings:
        framing_counts = {}
        for f in all_framings:
            if f != "present":
                framing_counts[f] = framing_counts.get(f, 0) + 1
        if framing_counts:
            best_framing_label = max(framing_counts, key=framing_counts.get)
    if entity_type == "person":
        if comp_position_score >= 14:
            print(f"    [{PASS}] Mentioned among top experts/leaders in field")
        elif comp_position_score >= 8:
            print(f"    [{WARN}] Mentioned but not prominently positioned among peers")
            fix(f"To elevate \"{entity_name}\" among field leaders:\n"
                f"1. Publish thought leadership content (articles, research, talks)\n"
                f"2. Speak at industry conferences and get talks published on YouTube\n"
                f"3. Contribute to high-profile open-source projects or publications\n"
                f"4. Get quoted as an expert in news articles and industry reports\n"
                f"5. Build a track record of public contributions AI can reference")
        else:
            print(f"    [{FAIL}] Not mentioned among leaders in field")
            fix(f"\"{entity_name}\" is not recognized as a leader in their field. To fix:\n"
                f"1. Publish original research, guides, or frameworks in your area of expertise\n"
                f"2. Create a personal website showcasing key contributions and publications\n"
                f"3. Get featured in \"Top [field] experts\" or \"People to follow\" lists\n"
                f"4. Build public presence: conference talks, podcast appearances, Twitter/X threads\n"
                f"5. Contribute to Wikipedia articles in your field (as a cited source, not self-editing)\n"
                f"6. Maintain active GitHub, LinkedIn, and Medium profiles with regular posts")
    else:
        print(f"    [{INFO}] Competitive framing: {best_framing_label}")
        if best_framing_label in ("not_mentioned", "niche", "unknown"):
            fix(f"AI does not position \"{entity_name}\" strongly against competitors. To improve:\n"
                f"1. Create comparison pages: \"{entity_name} vs [Competitor]\" for top 3-5 rivals\n"
                f"2. Get featured in third-party comparison articles and \"best of\" roundups\n"
                f"3. Earn reviews on G2, Capterra, Trustpilot — AI engines weight review platforms\n"
                f"4. Publish case studies showing concrete results vs alternatives\n"
                f"5. Target \"best [category]\" and \"[category] comparison\" search queries")
        elif best_framing_label == "option":
            fix(f"AI mentions \"{entity_name}\" as one option among many. To move to \"recommended\":\n"
                f"1. Earn more reviews and testimonials on authoritative platforms\n"
                f"2. Publish differentiation content highlighting unique strengths\n"
                f"3. Get featured as a top pick (not just listed) in comparison articles\n"
                f"4. Create \"Why {entity_name}\" content with specific advantages over alternatives")

    # Sentiment finding
    print(f"    [{INFO}] Overall AI sentiment: {sentiment_label}")
    if action_count > 0:
        print(f"    [{PASS}] AI uses recommendation language (\"use\", \"try\", \"consider\")")
    if sentiment_label in ("negative", "mixed"):
        fix(f"AI sentiment toward \"{entity_name}\" is {sentiment_label}. To improve:\n"
            f"1. Address negative signals: respond to criticism, fix reported issues\n"
            f"2. Earn positive reviews and testimonials on authoritative platforms\n"
            f"3. Publish case studies, success stories, and positive press coverage\n"
            f"4. Ensure third-party content about {entity_name} is accurate and up-to-date\n"
            f"5. If outdated negative content exists, publish fresh positive content to outweigh it")
    elif sentiment_label == "neutral" and total_analyzed > 0:
        fix(f"AI describes \"{entity_name}\" neutrally — no strong positive or recommendation signals.\n"
            f"To earn recommendation language (\"use X\", \"try X\", \"consider X\"):\n"
            f"1. Earn reviews that use recommendation language on G2, Capterra, Product Hunt\n"
            f"2. Get featured in \"best of\" and \"recommended\" lists in your category\n"
            f"3. Publish testimonials and endorsements from recognized authorities\n"
            f"4. Create content showing measurable results and clear advantages")

    # Content gaps
    if content_gaps:
        print(f"\n  {'─'*50}")
        print(f"  CONTENT GAPS (entity not mentioned for these queries)")
        print(f"  {'─'*50}")
        for gap in content_gaps:
            print(f"    [{WARN}] \"{gap}\"")
        if entity_type == "person":
            fix(f"Create content targeting these gap queries:\n" +
                "\n".join(f"  • Write an article, talk, or post about: \"{g}\"" for g in content_gaps) +
                f"\n\nPublish on platforms AI trains on: Medium, LinkedIn, personal blog, YouTube.\n"
                f"Each published piece increases the chance AI associates \"{entity_name}\" with these topics.")
        else:
            fix(f"Create content targeting these gap queries:\n" +
                "\n".join(f"  • Create a page or article about: \"{g}\"" for g in content_gaps) +
                f"\n\nTarget these as blog posts, landing pages, or FAQ entries.\n"
                f"Ensure each page mentions \"{entity_name}\" prominently and uses relevant category keywords.\n"
                f"Get these pages linked from authoritative sources for faster AI indexing.")
    else:
        print(f"    [{PASS}] No content gaps detected — entity appears across all tested queries")

    print(f"\n{'='*60}\n")

    if return_data:
        return {
            "entity": entity_name,
            "entity_type": entity_type,
            "scores": scores,
            "total_score": total_score,
            "max_score": max_score,
            "percent": pct,
            "grade": grade,
            "knowledge_graph": {
                "wikipedia": kg_details["wikipedia"],
                "wikidata": kg_details["wikidata"],
                "wikidata_id": kg_details["wikidata_id"],
                "google_kg": kg_details["google_kg"],
                "platforms_found": kg_details["platforms_found"],
            },
            "platforms": {
                "found": plat_details["found"],
                "not_found": plat_details["not_found"],
            },
            "sentiment": sentiment_label,
            "best_framing": best_framing_label,
            "content_gaps": content_gaps,
            "recognition_rate": round(recognition_score / 20 * 100, 1),
            "stability_runs": STABILITY_RUNS,
        }


