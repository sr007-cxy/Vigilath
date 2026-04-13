from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class GeoTestRequest(BaseModel):
    """Request model for GEO test"""
    url: str = Field(..., description="Website URL to test")
    include_fix: bool = Field(default=True, description="Include fix recommendations")

class CheckResult(BaseModel):
    """Result of a single check"""
    category: str
    status: str  # PASS, WARN, FAIL, INFO
    message: str
    fix: Optional[str] = None

class GeoTestResult(BaseModel):
    """Response model for GEO test result"""
    url: str
    score: int
    grade: str
    checks: List[CheckResult]
    summary: Dict[str, Any]
