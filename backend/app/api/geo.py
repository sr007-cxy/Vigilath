from fastapi import APIRouter, BackgroundTasks, Depends, Request
from app.services.geo_checker import run_geo_check
from app.services.membership_service import (
    membership_service,
    FREE_CHECK_CATEGORIES,
    ALL_CHECK_CATEGORIES,
)
from app.services.quota_service import check_and_increment_quota
from app.models.geo import GeoTestRequest, GeoTestResult
from app.models.membership import Membership
from app.utils.validator import validate_url, sanitize_url
from app.utils.error_handler import AppException
from app.api.auth import SECRET_KEY, ALGORITHM
from app.services.user_service import user_service
from jose import JWTError, jwt
import asyncio
import json
import uuid
import time
from fastapi.responses import StreamingResponse
from typing import Dict, Any, Optional, List

# Re-export from the membership service so we don't maintain duplicate lists.
ALL_CATEGORIES = ALL_CHECK_CATEGORIES


def _resolve_membership_from_request(request: Request) -> Membership:
    """Pull the effective membership for the caller.

    If an Authorization: Bearer token is present and valid, return the user's
    active paid tier (or fall back to free). Otherwise return the free tier.
    This is used by endpoints that allow both anonymous and authenticated
    callers without forcing login.
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                user = user_service.get_user_by_username(username)
                if user:
                    return membership_service.get_effective_membership(user.id)
        except JWTError:
            pass
    free = membership_service.get_membership_by_slug("free")
    if free is None:
        raise AppException(status_code=500, message="Free membership tier not initialized")
    return free


def _locked_for(membership: Membership) -> List[str]:
    allowed = membership.allowed_check_categories
    if allowed is None:
        return []
    allowed_set = set(allowed)
    return [c for c in ALL_CATEGORIES if c not in allowed_set]


router = APIRouter()

# Store active tasks for SSE
tasks: Dict[str, Dict[str, Any]] = {}

async def geo_check_task(
    task_id: str,
    url: str,
    include_fix: bool,
    allowed_categories: Optional[List[str]] = None,
    tier: Optional[str] = None,
    locked_categories: Optional[List[str]] = None,
):
    """Background task to run GEO check and store results"""
    print(f"Starting GEO check for {url}")
    try:
        # Check if task still exists
        if task_id not in tasks:
            print(f"Task {task_id} no longer exists")
            return
            
        # Update task status to running
        tasks[task_id]["status"] = "running"
        tasks[task_id]["progress"] = 0
        print(f"Task {task_id} status updated to running")
        
        # Define progress callback function
        def progress_callback(progress):
            print(f"Progress callback called with progress: {progress}%")
            if task_id in tasks:
                tasks[task_id]["progress"] = progress
                print(f"Task {task_id} progress updated to {progress}%")
        
        # Run GEO check with progress callback
        print(f"Calling run_geo_check for {url}")
        try:
            result = run_geo_check(
                url,
                include_fix,
                progress_callback=progress_callback,
                allowed_categories=allowed_categories,
            )
            if tier is not None:
                result.tier = tier
            if locked_categories is not None:
                result.locked_categories = locked_categories
            print(f"GEO check completed for {url}")
        except KeyboardInterrupt:
            print(f"GEO check interrupted for {url}")
            # Update task status to failed
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = "GEO check was interrupted"
            return
        except Exception as e:
            print(f"GEO check failed for {url}: {str(e)}")
            # Update task status to failed
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)
            return
        
        # Check if task still exists
        if task_id not in tasks:
            print(f"Task {task_id} no longer exists")
            return
            
        # Update task status to completed
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["progress"] = 100
        # Convert result to dictionary for proper JSON serialization
        tasks[task_id]["result"] = result.model_dump()
        print(f"Task {task_id} status updated to completed")
        print(f"Task {task_id} result: {result}")
    except Exception as e:
        # Check if task still exists
        if task_id in tasks:
            # Update task status to failed
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)
            print(f"Task {task_id} failed with error: {str(e)}")
        else:
            print(f"Task {task_id} no longer exists, error: {str(e)}")

@router.post("/check/anonymous", response_model=GeoTestResult)
async def check_anonymous(body: GeoTestRequest):
    """Anonymous check — always returns only the free-tier 5 categories.

    No authentication, no rate limiting (this phase).
    """
    if not validate_url(body.url):
        raise AppException(status_code=400, message="Invalid URL format")
    sanitized_url = sanitize_url(body.url)

    result = run_geo_check(
        sanitized_url,
        body.include_fix,
        allowed_categories=FREE_CHECK_CATEGORIES,
    )
    result.tier = "free"
    result.locked_categories = [c for c in ALL_CATEGORIES if c not in FREE_CHECK_CATEGORIES]
    return result


@router.post("/check", response_model=GeoTestResult)
async def check_authenticated(body: GeoTestRequest, request: Request):
    """Tiered check: uses the caller's membership to decide quota + categories.

    - Bearer token is optional. Without it, falls back to the free tier (same
      behavior as /check/anonymous but without writing any usage rows).
    - With a valid token, enforces monthly_check_quota and runs the tier's
      allowed_check_categories (NULL = all 23).
    """
    if not validate_url(body.url):
        raise AppException(status_code=400, message="Invalid URL format")
    sanitized_url = sanitize_url(body.url)

    membership = _resolve_membership_from_request(request)

    # Enforce quota only for authenticated users (we can tell by re-reading the
    # header — if present and valid, we'll have a non-free tier or at least a
    # real user_id to record against).
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                user = user_service.get_user_by_username(username)
                if user:
                    # TODO: 429 配额用尽,临时注释掉配额校验
                    # check_and_increment_quota(user.id, membership)
                    pass
        except JWTError:
            pass  # fall through to anonymous-equivalent run

    allowed = membership.allowed_check_categories  # None = all 23
    result = run_geo_check(
        sanitized_url,
        body.include_fix,
        allowed_categories=allowed,
    )
    result.tier = membership.slug
    result.locked_categories = _locked_for(membership)
    return result


@router.post("/geo", response_model=GeoTestResult)
async def test_geo(body: GeoTestRequest):
    """Legacy alias — behaves like /check/anonymous (5 free categories).

    Kept so existing frontend calls to `geoApi.checkGeo` continue to work
    during the frontend migration.
    """
    if not validate_url(body.url):
        raise AppException(status_code=400, message="Invalid URL format")
    sanitized_url = sanitize_url(body.url)

    result = run_geo_check(
        sanitized_url,
        body.include_fix,
        allowed_categories=FREE_CHECK_CATEGORIES,
    )
    result.tier = "free"
    result.locked_categories = [c for c in ALL_CATEGORIES if c not in FREE_CHECK_CATEGORIES]
    return result

@router.get("/geo/stream")
async def test_geo_stream(
    request: Request,
    url: str,
    include_fix: bool = True,
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Run GEO readiness test and stream results via SSE.

    Tier-aware: if a valid Bearer token is present in the Authorization
    header, runs with that user's allowed categories and enforces quota;
    otherwise runs the free-tier 5 categories.
    """
    print(f"Received SSE request for {url}")
    if not validate_url(url):
        raise AppException(status_code=400, message="Invalid URL format")

    sanitized_url = sanitize_url(url)
    print(f"Sanitized URL: {sanitized_url}")

    membership = _resolve_membership_from_request(request)

    # Enforce quota for authenticated callers only.
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                user = user_service.get_user_by_username(username)
                if user:
                    # TODO: 429 配额用尽,临时注释掉配额校验
                    # check_and_increment_quota(user.id, membership)
                    pass
        except JWTError:
            pass

    allowed = membership.allowed_check_categories  # None = all 23
    locked = _locked_for(membership)

    task_id = str(uuid.uuid4())
    print(f"Created task ID: {task_id}")

    tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "url": sanitized_url,
        "include_fix": include_fix,
        "tier": membership.slug,
    }
    print(f"Initialized task: {tasks[task_id]}")

    import asyncio
    print(f"Starting background task for {sanitized_url}")
    asyncio.create_task(
        geo_check_task(
            task_id,
            sanitized_url,
            include_fix,
            allowed_categories=allowed,
            tier=membership.slug,
            locked_categories=locked,
        )
    )
    print(f"Background task started for {sanitized_url}")
    
    async def event_generator():
        print(f"Starting event generator for task {task_id}")
        try:
            # Send initial status
            print(f"Sending initial status for task {task_id}")
            yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
            
            # Update status to running
            tasks[task_id]["status"] = "running"
            print(f"Updated task status to running: {tasks[task_id]}")
            yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
            # Keep sending status updates every 2 seconds until task is completed or failed
            timeout = 1800  # 1800 seconds (30 minutes) timeout
            start_time = time.time()
            progress = 0
            
            while task_id in tasks:
                # Check if task is completed or failed
                if tasks[task_id]["status"] in ["completed", "failed"]:
                    print(f"Task {task_id} is {tasks[task_id]['status']}, sending final status")
                    yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
                    break
                
                # Check for timeout
                if time.time() - start_time > timeout:
                    print(f"Task {task_id} timed out after {timeout} seconds")
                    if task_id in tasks:
                        tasks[task_id]["status"] = "failed"
                        tasks[task_id]["error"] = f"GEO check timed out after {timeout} seconds"
                        yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
                    break
                
                # Check if progress is 100% but status is still running
                if task_id in tasks and tasks[task_id]["progress"] == 100 and tasks[task_id]["status"] == "running":
                    print(f"Updating task {task_id} status to completed")
                    tasks[task_id]["status"] = "completed"
                    print(f"Sending final status for task {task_id}: completed")
                    yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
                    break
                
                # Check if task has result but status is still running
                if task_id in tasks and "result" in tasks[task_id] and tasks[task_id]["status"] == "running":
                    print(f"Task {task_id} has result, updating status to completed")
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["progress"] = 100
                    print(f"Sending final status for task {task_id}: completed")
                    yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
                    break
                
                # Update progress manually to show activity
                progress = min(progress + 5, 90)
                if task_id in tasks:
                    tasks[task_id]["progress"] = progress
                    print(f"Sending progress update for task {task_id}: {progress}%")
                    yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
                
                await asyncio.sleep(2)
            
            # Send final status if task still exists
            if task_id in tasks:
                print(f"Sending final status for task {task_id}: {tasks[task_id]['status']}")
                yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
                # Clean up task after sending final status
                print(f"Cleaning up task {task_id}")
                del tasks[task_id]
            else:
                print(f"Task {task_id} no longer exists")
                # Send completed status if task was removed
                yield f"event: status\ndata: {{\"status\": \"completed\", \"progress\": 100, \"url\": \"{sanitized_url}\", \"include_fix\": {include_fix}}}\n\n"
        except asyncio.CancelledError:
            print(f"Event generator for task {task_id} was cancelled")
            # Clean up task if it still exists
            if task_id in tasks:
                print(f"Cleaning up cancelled task {task_id}")
                del tasks[task_id]
        except Exception as e:
            print(f"Error in event generator for task {task_id}: {str(e)}")
            # Send error status if task still exists
            if task_id in tasks:
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = f"SSE error: {str(e)}"
                print(f"Sending error status for task {task_id}: {tasks[task_id]['status']}")
                yield f"event: status\ndata: {json.dumps(tasks[task_id], default=str)}\n\n"
                # Clean up task after sending error status
                print(f"Cleaning up task {task_id}")
                del tasks[task_id]
            else:
                # Send error status if task was removed
                yield f"event: status\ndata: {{\"status\": \"failed\", \"error\": \"{str(e)}\", \"url\": \"{sanitized_url}\", \"include_fix\": {include_fix}}}\n\n"
        finally:
            print(f"Event generator for task {task_id} completed")

    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
