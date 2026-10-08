"""Clean up AI images left behind by failed article generation or interrupted processes."""
from __future__ import annotations

if __name__ == "__main__":  # Must load the cron environment before importing database
    from geo.cron_env import load_env_for_cron
    load_env_for_cron()

import logging
from datetime import datetime, timedelta

from sqlalchemy import or_

from geo.database import SessionLocal
from geo.models.ai_telemetry import TopicMediaORM
from geo.services import media_storage

log = logging.getLogger(__name__)
_PENDING_MAX_AGE = timedelta(hours=24)


def _delete_rows(rows: list[TopicMediaORM]) -> int:
    deleted = 0
    for candidate in rows:
        db = SessionLocal()
        try:
            row = db.get(TopicMediaORM, candidate.id)
            if row is None or not row.is_ai_generated:
                continue
            if row.generation_state not in {"pending", "orphaned"}:
                continue
            media_storage.delete_file(row.topic_id, row.storage_path or "")
            db.delete(row)
            db.commit()
            deleted += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            log.warning("delete orphan image failed media=%s", candidate.id, exc_info=True)
        finally:
            db.close()
    return deleted


def mark_orphan_and_delete(media_id: int | None) -> int:
    """When an article is known to have failed, mark and delete immediately; on failure leave it for the scheduled job to retry."""
    if not media_id:
        return 0
    db = SessionLocal()
    try:
        row = db.get(TopicMediaORM, media_id)
        if row is None or not row.is_ai_generated or row.generation_state != "pending":
            return 0
        row.generation_state = "orphaned"
        db.commit()
        db.refresh(row)
        candidate = row
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("mark orphan image failed media=%s", media_id, exc_info=True)
        return 0
    finally:
        db.close()
    return _delete_rows([candidate])


def cleanup_orphan_media_ids(media_ids: list[int]) -> int:
    """Clean up old AI images replaced by successful articles and already committed as orphaned."""
    ids = sorted({int(x) for x in media_ids if x})
    if not ids:
        return 0
    db = SessionLocal()
    try:
        rows = (
            db.query(TopicMediaORM)
              .filter(TopicMediaORM.id.in_(ids),
                      TopicMediaORM.is_ai_generated.is_(True),
                      TopicMediaORM.generation_state == "orphaned")
              .all()
        )
    finally:
        db.close()
    return _delete_rows(rows)


def cleanup_orphans(*, now: datetime | None = None, limit: int = 1000) -> dict[str, int]:
    """Delete orphaned images, and pending images still not bound to an article after 24 hours."""
    cutoff = (now or datetime.utcnow()) - _PENDING_MAX_AGE
    db = SessionLocal()
    try:
        rows = (
            db.query(TopicMediaORM)
              .filter(TopicMediaORM.is_ai_generated.is_(True))
              .filter(or_(
                  TopicMediaORM.generation_state == "orphaned",
                  (TopicMediaORM.generation_state == "pending")
                  & (TopicMediaORM.uploaded_at < cutoff),
              ))
              .order_by(TopicMediaORM.uploaded_at.asc(), TopicMediaORM.id.asc())
              .limit(max(1, limit))
              .all()
        )
        matched = len(rows)
    finally:
        db.close()
    deleted = _delete_rows(rows)
    result = {"matched": matched, "deleted": deleted}
    log.info("image orphan cleanup: %s", result)
    return result


if __name__ == "__main__":  # pragma: no cover - crontab entry point
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(cleanup_orphans())
