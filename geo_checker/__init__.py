"""
GEO Readiness Checker — Check a website's Generative Engine Optimization readiness.
"""

__version__ = "1.0.0"

from typing import Dict, List, Any, Optional, Callable
from .__main__ import (
    generate_score,
    reset_state,
    _scores,
    get_ai_visibility_score,
    get_grade,
    SHOW_FIX,
    check_https,
    check_robots_txt,
    check_llms_txt,
    check_well_known,
    check_sitemap,
    check_search_engine_registration,
    check_structured_data,
    check_meta_tags,
    check_content_accessibility,
    check_ai_crawl_readiness,
    check_content_quality,
    check_technical_crawlability,
    check_authority_trust,
    check_ai_optimization,
    check_social_signals,
    check_ai_answer_formats,
    check_schema_knowledge,
    check_mobile_and_weight,
    check_url_normalization,
    check_outbound_and_media,
    check_multilingual_depth,
    check_cross_platform,
    check_multi_page,
    PASS,
    WARN,
    FAIL,
    INFO,
    FIX,
)

ALL_CATEGORIES = [
    "HTTPS",
    "robots.txt",
    "llms.txt",
    ".well-known",
    "Sitemap",
    "Platform Registration",
    "Structured Data",
    "Meta Tags",
    "Content Accessibility",
    "AI Crawl Readiness",
    "Content Quality",
    "Technical Crawlability",
    "Authority & Trust",
    "AI Optimization",
    "Social Signals",
    "AI Answer Formats",
    "Schema & Knowledge",
    "Mobile & Weight",
    "URL Normalization",
    "Outbound & Media",
    "Multilingual",
    "Cross-Platform",
    "Multi-Page",
]


def run_check(
    url: str,
    include_fix: bool = False,
    allowed_categories: Optional[List[str]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Dict[str, Any]:
    """Run GEO readiness check and return structured result.
    
    Args:
        url: The website URL to check
        include_fix: Whether to include fix recommendations
        allowed_categories: Optional list of category names to run (None = all)
        progress_callback: Optional callback function for progress updates (0-100)
    
    Returns:
        Dictionary containing:
        - url: The checked URL
        - score: AI Visibility Score (0-100)
        - grade: Letter grade (A+ to F)
        - checks: List of check results
        - summary: Summary statistics
        - category_scores: Per-category score breakdown
    """
    import sys
    import io
    from urllib.parse import urlparse
    
    global SHOW_FIX
    old_fix = SHOW_FIX
    SHOW_FIX = include_fix
    
    reset_state()
    
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    
    checks = []
    current_category = None
    category_checks = {}
    
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    
    try:
        total_categories = len(allowed_categories) if allowed_categories else len(ALL_CATEGORIES)
        completed_categories = 0
        
        def update_progress():
            nonlocal completed_categories
            completed_categories += 1
            if progress_callback:
                progress = int((completed_categories / total_categories) * 100)
                progress_callback(min(progress, 99))
        
        def run_check_if_allowed(category_name: str, check_func, *args, **kwargs):
            nonlocal current_category
            if allowed_categories is None or category_name in allowed_categories:
                current_category = category_name
                check_func(*args, **kwargs)
                update_progress()
        
        sitemap_urls = []
        
        run_check_if_allowed("HTTPS", check_https, url)
        run_check_if_allowed("robots.txt", check_robots_txt, url)
        run_check_if_allowed("llms.txt", check_llms_txt, url)
        run_check_if_allowed(".well-known", check_well_known, url)
        
        if allowed_categories is None or "Sitemap" in allowed_categories:
            current_category = "Sitemap"
            sitemap_urls = check_sitemap(url)
            update_progress()
        
        run_check_if_allowed("Platform Registration", check_search_engine_registration, url)
        run_check_if_allowed("Structured Data", check_structured_data, url)
        run_check_if_allowed("Meta Tags", check_meta_tags, url)
        run_check_if_allowed("Content Accessibility", check_content_accessibility, url)
        run_check_if_allowed("AI Crawl Readiness", check_ai_crawl_readiness, url)
        run_check_if_allowed("Content Quality", check_content_quality, url)
        run_check_if_allowed("Technical Crawlability", check_technical_crawlability, url)
        run_check_if_allowed("Authority & Trust", check_authority_trust, url)
        run_check_if_allowed("AI Optimization", check_ai_optimization, url)
        run_check_if_allowed("Social Signals", check_social_signals, url)
        run_check_if_allowed("AI Answer Formats", check_ai_answer_formats, url)
        run_check_if_allowed("Schema & Knowledge", check_schema_knowledge, url)
        run_check_if_allowed("Mobile & Weight", check_mobile_and_weight, url)
        run_check_if_allowed("URL Normalization", check_url_normalization, url)
        run_check_if_allowed("Outbound & Media", check_outbound_and_media, url)
        run_check_if_allowed("Multilingual", check_multilingual_depth, url)
        run_check_if_allowed("Cross-Platform", check_cross_platform, url)
        run_check_if_allowed("Multi-Page", check_multi_page, url, sitemap_urls)
        
    finally:
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        SHOW_FIX = old_fix
    
    score = get_ai_visibility_score()
    grade = get_grade(score)
    
    checks = _parse_output_to_checks(output)
    
    summary = {
        "pass_count": sum(1 for c in checks if c["status"] == "PASS"),
        "warn_count": sum(1 for c in checks if c["status"] == "WARN"),
        "fail_count": sum(1 for c in checks if c["status"] == "FAIL"),
        "info_count": sum(1 for c in checks if c["status"] == "INFO"),
        "total_checks": len(checks),
    }
    
    category_scores = {}
    for cat, vals in _scores.items():
        category_scores[cat] = {
            "earned": vals["earned"],
            "max": vals["max"],
            "percentage": round((vals["earned"] / vals["max"] * 100) if vals["max"] > 0 else 0, 1),
        }
    
    if progress_callback:
        progress_callback(100)
    
    return {
        "url": url,
        "score": score,
        "grade": grade,
        "checks": checks,
        "summary": summary,
        "category_scores": category_scores,
    }


def _parse_output_to_checks(output: str) -> List[Dict[str, Any]]:
    """Parse the output string into structured check results."""
    import re
    
    checks = []
    current_category = None
    current_check = None
    
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    output = ansi_escape.sub('', output)
    
    category_pattern = re.compile(r"^--- (.*) ---")
    status_pattern = re.compile(r"^\s*\[(PASS|WARN|FAIL|INFO|FIX)\] (.*)")
    
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        
        category_match = category_pattern.match(line)
        if category_match:
            current_category = category_match.group(1)
            current_check = None
            continue
        
        status_match = status_pattern.match(line)
        if status_match and current_category:
            status = status_match.group(1).strip()
            message = status_match.group(2)
            
            if status == "FIX":
                if checks:
                    checks[-1]["fix"] = message
            else:
                current_check = {
                    "category": current_category,
                    "status": status,
                    "message": message,
                    "fix": None,
                }
                checks.append(current_check)
        
        # 处理多行消息
        elif current_check and line and not line.startswith("---") and not line.startswith("  ["):
            current_check["message"] += " " + line
    
    return checks
