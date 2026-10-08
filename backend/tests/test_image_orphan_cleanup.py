from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.ai_telemetry import TopicMediaORM
from geo.services import image_orphan_cleanup


def _factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'orphans.db'}")
    Base.metadata.create_all(engine, tables=[TopicMediaORM.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(image_orphan_cleanup, "SessionLocal", factory)
    return factory


def _media(name: str, *, state=None, uploaded_at=None, ai=True):
    return TopicMediaORM(
        topic_id=7, user_id=9, filename=name, kind="image", mime="image/png",
        size=1, storage_path=f"data/topic_media/7/{name}",
        uploaded_at=uploaded_at or datetime.utcnow(), is_ai_generated=ai,
        generation_state=state,
    )


def test_cleanup_deletes_orphaned_and_expired_pending_only(tmp_path, monkeypatch):
    factory = _factory(tmp_path, monkeypatch)
    now = datetime(2026, 9, 20, 12)
    db = factory()
    old_pending = _media("old.png", state="pending", uploaded_at=now - timedelta(hours=25))
    fresh_pending = _media("fresh.png", state="pending", uploaded_at=now - timedelta(hours=23))
    orphaned = _media("orphan.png", state="orphaned", uploaded_at=now)
    attached = _media("attached.png", state="attached", uploaded_at=now - timedelta(days=2))
    legacy = _media("legacy.png", state=None, uploaded_at=now - timedelta(days=2))
    uploaded = _media("user.png", state="orphaned", uploaded_at=now - timedelta(days=2), ai=False)
    db.add_all([old_pending, fresh_pending, orphaned, attached, legacy, uploaded])
    db.commit()
    kept_ids = {fresh_pending.id, attached.id, legacy.id, uploaded.id}
    deleted_files = []
    monkeypatch.setattr(
        image_orphan_cleanup.media_storage, "delete_file",
        lambda topic_id, path: deleted_files.append((topic_id, path)),
    )

    result = image_orphan_cleanup.cleanup_orphans(now=now)

    assert result == {"matched": 2, "deleted": 2}
    assert len(deleted_files) == 2
    assert {row.id for row in db.query(TopicMediaORM).all()} == kept_ids


def test_known_failure_is_marked_and_deleted_immediately(tmp_path, monkeypatch):
    factory = _factory(tmp_path, monkeypatch)
    db = factory()
    pending = _media("pending.png", state="pending")
    db.add(pending)
    db.commit()
    media_id = pending.id
    monkeypatch.setattr(image_orphan_cleanup.media_storage, "delete_file", lambda *_a: None)

    assert image_orphan_cleanup.mark_orphan_and_delete(media_id) == 1
    db.expire_all()
    assert db.get(TopicMediaORM, media_id) is None
