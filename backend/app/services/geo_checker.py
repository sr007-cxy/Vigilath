import io
import re
import threading
import time
from contextlib import redirect_stdout
from typing import Dict, List, Any, Optional

from geo_checker import __main__ as _gc_module
from app.models.geo import GeoTestResult, CheckResult

# Simple in-memory cache for test results
# In a production environment, you might want to use Redis or another caching solution
_cache: Dict[str, tuple[GeoTestResult, float]] = {}
_CACHE_TTL = 3600  # 1 hour cache time

# geo_checker.__main__ uses module-level globals (_scores, _page_cache, SHOW_FIX),
# so concurrent in-process calls would race. Serialize with a lock; concurrency
# across requests is still achieved by offloading via asyncio.to_thread in the
# API layer — the event loop stays unblocked, even while checks queue here.
_geo_checker_lock = threading.Lock()

class GeoChecker:
    def run_geo_check(
        self,
        url: str,
        include_fix: bool,
        allowed_categories: Optional[List[str]] = None,
    ) -> GeoTestResult:
        return run_geo_check(url, include_fix, allowed_categories=allowed_categories)

geo_checker = GeoChecker()

def run_geo_check(
    url: str,
    include_fix: bool,
    progress_callback=None,
    allowed_categories: Optional[List[str]] = None,
) -> GeoTestResult:
    """Run GEO readiness check in-process and return structured result.

    allowed_categories: optional whitelist of category labels (e.g. the 5 free-tier
    checks). When None, all 23 categories are run.
    """
    print(f"Starting run_geo_check for {url}")
    # Check cache first — cache key must include the category filter so that a
    # free-tier 5-check run doesn't serve a cached 23-check result to a pro user
    # or vice versa.
    categories_key = ",".join(sorted(allowed_categories)) if allowed_categories else "all"
    cache_key = f"{url}_{include_fix}_{categories_key}"
    cached_result = get_cached_result(cache_key)
    if cached_result:
        print(f"Cache hit for {url}")
        if progress_callback:
            progress_callback(100)
        return cached_result

    # Background ticker thread: emits a time-based progress estimate while the
    # checker is running (same semantics as the old subprocess-based reader).
    stop_event = threading.Event()
    progress_thread: Optional[threading.Thread] = None
    if progress_callback:
        start_time = time.time()

        def _tick_progress():
            while not stop_event.wait(2):
                elapsed = time.time() - start_time
                progress_callback(min(int((elapsed / 300) * 90), 90))

        progress_thread = threading.Thread(target=_tick_progress, daemon=True)
        progress_thread.start()

    try:
        buf = io.StringIO()
        # The lock protects geo_checker's module-level globals against concurrent
        # invocations in the same process.
        with _geo_checker_lock:
            old_show_fix = _gc_module.SHOW_FIX
            _gc_module.SHOW_FIX = include_fix
            try:
                with redirect_stdout(buf):
                    _gc_module.generate_score(
                        url, allowed_categories=allowed_categories
                    )
            finally:
                _gc_module.SHOW_FIX = old_show_fix

        output = buf.getvalue()
        print(f"Parsing GEO output...")
        geo_result = parse_geo_output(url, output, include_fix)
        print(f"GEO check completed successfully")

        cache_result(cache_key, geo_result)
        return geo_result
    except Exception as e:
        print(f"Error in run_geo_check: {str(e)}")
        raise Exception(f"Failed to run GEO check: {str(e)}")
    finally:
        stop_event.set()
        if progress_thread is not None:
            progress_thread.join(timeout=1)
        if progress_callback:
            progress_callback(100)

def get_cached_result(key: str) -> Optional[GeoTestResult]:
    """Get cached result if it's still valid"""
    if key in _cache:
        result, timestamp = _cache[key]
        if time.time() - timestamp < _CACHE_TTL:
            return result
        else:
            # Remove expired cache
            del _cache[key]
    return None

def cache_result(key: str, result: GeoTestResult) -> None:
    """Cache the result with timestamp"""
    _cache[key] = (result, time.time())

def parse_geo_output(url: str, output: str, include_fix: bool) -> GeoTestResult:
    """Parse the output from geo_checker script into structured format"""
    checks = []
    current_category = None
    
    # Remove ANSI color codes from output
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    output = ansi_escape.sub('', output)
    
    # Regex patterns to extract information
    category_pattern = re.compile(r"^--- (.*) ---")
    status_pattern = re.compile(r"^\s*\[(PASS|WARN|FAIL|INFO| FIX)\] (.*)")
    score_pattern = re.compile(r"^AI VISIBILITY SCORE:\s*(\d+)/100\s*\(Grade: ([A-F+]+)\)")
    
    score = 0
    grade = "F"
    
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        
        # Check for category
        category_match = category_pattern.match(line)
        if category_match:
            current_category = category_match.group(1)
            continue
        
        # Check for status line
        status_match = status_pattern.match(line)
        if status_match and current_category:
            status = status_match.group(1)
            message = status_match.group(2)
            
            # For FIX lines, we need to handle differently
            if status == " FIX":
                # This is a fix recommendation for the previous check
                if checks:
                    checks[-1].fix = message
            else:
                # This is a new check result
                check = CheckResult(
                    category=current_category,
                    status=status,
                    message=message,
                    fix=None
                )
                checks.append(check)
        
        # Check for score
        score_match = score_pattern.match(line)
        if score_match:
            score = int(score_match.group(1))
            grade = score_match.group(2)
    
    # Calculate summary
    summary = {
        "pass_count": sum(1 for c in checks if c.status == "PASS"),
        "warn_count": sum(1 for c in checks if c.status == "WARN"),
        "fail_count": sum(1 for c in checks if c.status == "FAIL"),
        "info_count": sum(1 for c in checks if c.status == "INFO"),
        "total_checks": len(checks)
    }
    
    return GeoTestResult(
        url=url,
        score=score,
        grade=grade,
        checks=checks,
        summary=summary
    )
