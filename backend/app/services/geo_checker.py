import sys
import subprocess
import json
import re
import time
import os
from typing import Dict, List, Any, Optional
from app.models.geo import GeoTestResult, CheckResult

# Simple in-memory cache for test results
# In a production environment, you might want to use Redis or another caching solution
_cache: Dict[str, tuple[GeoTestResult, float]] = {}
_CACHE_TTL = 3600  # 1 hour cache time

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
    """Run GEO readiness check and return structured result.

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

    # backend/ is the CWD used for subprocess (the geo_checker package lives there)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(os.path.dirname(current_dir))
    project_root = backend_dir
    print(f"Project root: {project_root}")

    # Run the geo_checker script
    cmd = [
        sys.executable,
        "-m",
        "geo_checker",
        url
    ]

    if include_fix:
        cmd.append("--fix")
    if allowed_categories:
        cmd.extend(["--categories", ",".join(allowed_categories)])
    print(f"Running command: {cmd}")
    
    try:
        # Use Popen to run the script
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=project_root
        )
        print(f"Process started with PID: {process.pid}")
        
        # Update progress manually
        import time
        start_time = time.time()
        last_update_time = start_time
        
        # Read output line by line
        output = []
        while True:
            line = process.stdout.readline()
            if not line:
                break
            output.append(line)
            print(f"Output line: {line.strip()}")
            
            # Update progress every 5 lines or every 2 seconds
            current_time = time.time()
            if (len(output) % 5 == 0 or current_time - last_update_time > 2) and progress_callback:
                elapsed_time = current_time - start_time
                # Estimate progress based on elapsed time
                # We'll assume a maximum of 300 seconds for the task
                progress = min(int((elapsed_time / 300) * 90), 90)
                print(f"Updating progress to {progress}%")
                progress_callback(progress)
                last_update_time = current_time
        
        # Wait for process to complete
        print(f"Waiting for process to complete...")
        try:
            process.wait(timeout=1800)  # 1800 seconds (30 minutes) timeout
            print(f"Process completed with return code: {process.returncode}")
        except subprocess.TimeoutExpired:
            print(f"Process timed out after 1800 seconds")
            process.kill()
            raise Exception("GEO check timed out after 1800 seconds")
        
        if process.returncode != 0:
            stderr = process.stderr.read()
            print(f"Process failed with stderr: {stderr}")
            raise Exception(f"Error running GEO check: {stderr}")
        
        output = ''.join(output)
        print(f"Parsing GEO output...")
        geo_result = parse_geo_output(url, output, include_fix)
        print(f"GEO check completed successfully")
        
        # Cache the result
        cache_result(cache_key, geo_result)
        
        # Set progress to 100%
        if progress_callback:
            print(f"Setting progress to 100%")
            progress_callback(100)
        
        return geo_result
        
    except subprocess.TimeoutExpired:
        raise Exception("GEO check timed out after 1800 seconds")
    except KeyboardInterrupt:
        print(f"GEO check was interrupted by user")
        if process:
            process.kill()
        raise Exception("GEO check was interrupted by user")
    except Exception as e:
        print(f"Error in run_geo_check: {str(e)}")
        if process:
            process.kill()
        raise Exception(f"Failed to run GEO check: {str(e)}")

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
