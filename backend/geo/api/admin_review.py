"""Admin 审核 — Phase B 仅做 require_admin 守门 + 空数据脚手架.

Phase C 接入 Topic.seed_prompts_json / queries_json 的 status 字段后,
这里的 GET /pending 会真返回待审核项;现在返回空。
"""
from fastapi import APIRouter, Depends

from geo.api.auth import require_admin

router = APIRouter(prefix="/admin/review")


@router.get("/pending")
async def list_pending(_admin = Depends(require_admin)):
    """Return all pending seed prompts + queries across all topics.

    Phase B stub:返回空数组。Phase C 接入真实数据。
    """
    return {
        "seed_prompts": [],
        "queries": [],
    }
