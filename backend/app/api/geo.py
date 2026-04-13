from fastapi import APIRouter, BackgroundTasks, Depends
from app.services.geo_checker import run_geo_check
from app.models.geo import GeoTestRequest, GeoTestResult
from app.utils.validator import validate_url, sanitize_url
from app.utils.error_handler import AppException
import asyncio
import json
import uuid
import time
from fastapi.responses import StreamingResponse
from typing import Dict, Any

router = APIRouter()

# Store active tasks for SSE
tasks: Dict[str, Dict[str, Any]] = {}

async def geo_check_task(task_id: str, url: str, include_fix: bool):
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
            result = run_geo_check(url, include_fix, progress_callback=progress_callback)
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

@router.post("/geo", response_model=GeoTestResult)
async def test_geo(request: GeoTestRequest):
    """Run GEO readiness test for a website"""
    # Validate and sanitize URL
    if not validate_url(request.url):
        raise AppException(status_code=400, message="Invalid URL format")
    
    sanitized_url = sanitize_url(request.url)
    
    result = run_geo_check(sanitized_url, request.include_fix)
    return result

@router.get("/geo/stream")
async def test_geo_stream(url: str, include_fix: bool = True, background_tasks: BackgroundTasks = BackgroundTasks()):
    """Run GEO readiness test and stream results via SSE"""
    print(f"Received SSE request for {url}")
    # Validate and sanitize URL
    if not validate_url(url):
        raise AppException(status_code=400, message="Invalid URL format")
    
    sanitized_url = sanitize_url(url)
    print(f"Sanitized URL: {sanitized_url}")
    
    # Create task ID
    task_id = str(uuid.uuid4())
    print(f"Created task ID: {task_id}")
    
    # Initialize task
    tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "url": sanitized_url,
        "include_fix": include_fix
    }
    print(f"Initialized task: {tasks[task_id]}")
    
    # Start background task
    import asyncio
    print(f"Starting background task for {sanitized_url}")
    asyncio.create_task(geo_check_task(task_id, sanitized_url, include_fix))
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
