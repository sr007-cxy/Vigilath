"""admin_crawl: AI/LLM crawler log analysis (admin only, read-only).

Only reads the snapshot in the shared DB (written once a day by the prod system crontab, reading the vigilath production
nginx logs). The endpoint **only reads, never computes**: prod / test query the same row, test sees production
data, and test will never overwrite production data with its own logs.

Snapshot generation: geo/services/crawl_snapshot.py (prod crontab calls __main__).
"""

from fastapi import APIRouter, Depends

from geo.api.auth import require_maintainer
from geo.services.crawl_snapshot import _EMPTY, _LOG_GLOB, load_snapshot

router = APIRouter(prefix="/admin/crawl-analysis")


@router.get("")
def get_crawl_analysis(_admin=Depends(require_maintainer)):
    """Return the AI crawler analysis snapshot from the shared DB (production data)."""
    snap = load_snapshot()
    if snap is not None:
        return snap
    # No snapshot yet (prod cron has never run): return an empty structure; the frontend shows "no data yet".
    return {**_EMPTY, "log_glob": _LOG_GLOB, "generated_at": None}
