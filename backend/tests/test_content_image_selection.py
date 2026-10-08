from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.ai_telemetry import BrandProfile, TopicMediaORM
from geo.services import content_generator, image_gen


def _db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'media.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[TopicMediaORM.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(content_generator, "SessionLocal", factory)
    return factory()


def _media(*, topic_id=7, filename="a.png", uploaded_at=None, use_count=0,
           kind="image", ai=False, risk=None, description=""):
    return TopicMediaORM(
        topic_id=topic_id, user_id=9, filename=filename, kind=kind,
        description=description,
        mime="image/png", size=1, storage_path=f"data/topic_media/{topic_id}/{filename}",
        uploaded_at=uploaded_at or datetime(2026, 9, 18), use_count=use_count,
        is_ai_generated=ai, external_risk=risk,
    )


def test_new_media_defaults_use_count_to_zero(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    row = _media(filename="new.png")
    db.add(row)
    db.flush()

    assert row.use_count == 0
    assert row.image_edit_fail_count == 0


def test_claims_lowest_count_then_oldest_and_skips_ineligible(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    now = datetime(2026, 9, 18)
    older = _media(filename="a.png", uploaded_at=now, use_count=1)
    newer = _media(filename="b.png", uploaded_at=now + timedelta(seconds=1), use_count=1)
    cooling = _media(filename="cooling.png", use_count=0)
    cooling.image_edit_retry_at = now + timedelta(minutes=5)
    db.add_all([
        older, newer, cooling,
        _media(filename="ai.png", uploaded_at=now - timedelta(days=1), ai=True),
        _media(filename="video.mp4", uploaded_at=now - timedelta(days=1), kind="video"),
        _media(filename="risky.png", uploaded_at=now - timedelta(days=1), risk="high"),
        _media(topic_id=8, filename="other.png", uploaded_at=now - timedelta(days=1)),
    ])
    db.commit()

    first = content_generator._claim_article_image(7, now=now)
    second = content_generator._claim_article_image(7, now=now)

    assert first and first[0] == older.id
    assert second and second[0] == newer.id
    assert first[1] != second[1]


def test_claim_prefers_media_matching_article_then_falls_back_to_rotation(
        tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    now = datetime(2026, 9, 18)
    lobby = _media(filename="大堂.png", use_count=0, description="酒店大堂前台")
    pool = _media(filename="IMG_0001.png", use_count=3, description="室外无边泳池夜景")
    kids = _media(filename="亲子乐园.png", use_count=5)
    db.add_all([lobby, pool, kids])
    db.commit()

    # Filename hitting 2 terms (亲子/乐园, "family/park") beats a description hitting 1 term (泳池, "pool"), and also beats usage count
    by_name = content_generator._claim_article_image(
        7, article_text="周末亲子游攻略:乐园、泳池一次玩够", now=now)
    by_desc = content_generator._claim_article_image(
        7, article_text="夏天带娃住哪家？无边泳池才是重点", now=now)
    unrelated = content_generator._claim_article_image(
        7, article_text="Q3 财报解读", now=now)

    assert by_desc and by_desc[0] == pool.id
    assert by_name and by_name[0] == kids.id
    assert unrelated and unrelated[0] == lobby.id


def test_media_relevance_ignores_empty_inputs():
    grams = content_generator._match_grams("无边泳池")
    assert content_generator._media_relevance("", "IMG_0001.png", grams) == 0
    assert content_generator._media_relevance("泳池", "", set()) == 0
    assert content_generator._media_relevance("泳池夜景", "", grams) == 1


def test_active_claim_blocks_and_expired_claim_can_be_reclaimed(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    now = datetime(2026, 9, 18)
    source = _media()
    db.add(source)
    db.commit()

    first = content_generator._claim_article_image(7, now=now)
    assert first and first[0] == source.id
    assert content_generator._claim_article_image(7, now=now) is None

    db.refresh(source)
    source.claim_expires_at = now - timedelta(seconds=1)
    db.commit()
    second = content_generator._claim_article_image(7, now=now)
    assert second and second[0] == source.id
    assert second[1] != first[1]


def test_failure_backoff_grows_and_success_resets_it(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    now = datetime(2026, 9, 18)
    source = _media(use_count=1)
    source.claim_token = "claim-1"
    source.claim_expires_at = now + timedelta(hours=1)
    db.add(source)
    db.commit()

    content_generator._record_image_edit_failure(source.id, "claim-1", now=now)
    db.refresh(source)
    assert source.image_edit_fail_count == 1
    assert source.image_edit_retry_at == now + timedelta(minutes=5)
    assert source.claim_token is None

    source.claim_token = "claim-2"
    source.claim_expires_at = now + timedelta(hours=1)
    db.commit()
    content_generator._record_image_edit_failure(source.id, "claim-2", now=now)
    db.refresh(source)
    assert source.image_edit_fail_count == 2
    assert source.image_edit_retry_at == now + timedelta(minutes=10)

    source.claim_token = "claim-3"
    source.claim_expires_at = now + timedelta(hours=1)
    db.commit()
    content_generator._reset_image_edit_failures(source.id, "claim-3")
    db.refresh(source)
    assert source.image_edit_fail_count == 0
    assert source.image_edit_retry_at is None

    source.image_edit_fail_count = 20
    source.claim_token = "claim-4"
    source.claim_expires_at = now + timedelta(hours=1)
    db.commit()
    content_generator._record_image_edit_failure(source.id, "claim-4", now=now)
    db.refresh(source)
    assert source.image_edit_retry_at == now + timedelta(hours=24)


def test_first_use_is_original_and_success_increments_history(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    source = _media(use_count=0)
    db.add(source)
    db.commit()
    topic = SimpleNamespace(id=7, user_id=9)
    doc = SimpleNamespace(id=42)

    monkeypatch.setattr(content_generator, "_AUTO_IMAGE", True)
    selection, images = content_generator._prepare_article_image(
        db, topic, BrandProfile(), {})
    assert selection and selection.source_id == source.id
    assert [m.id for m in images] == [source.id]

    content_generator._finalize_article_image(db, topic, doc, selection)
    db.commit()
    db.refresh(source)
    assert source.use_count == 1
    assert source.claim_token is None


def test_rolled_back_article_does_not_increment_history(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    source = _media(use_count=0)
    db.add(source)
    db.commit()
    topic = SimpleNamespace(id=7, user_id=9)
    selection, _ = content_generator._prepare_article_image(
        db, topic, BrandProfile(), {})

    content_generator._finalize_article_image(
        db, topic, SimpleNamespace(id=44), selection)
    db.rollback()
    content_generator._abort_article_image(selection)
    db.refresh(source)

    assert source.use_count == 0
    assert source.claim_token is None


def test_reused_image_edit_is_pending_until_article_success(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    source = _media(use_count=2)
    db.add(source)
    db.commit()
    topic = SimpleNamespace(id=7, user_id=9)
    doc = SimpleNamespace(id=43)
    monkeypatch.setattr(content_generator, "_AUTO_IMAGE", True)
    monkeypatch.setattr(image_gen, "available", lambda: True)

    def fake_generate(image_db, **kwargs):
        assert kwargs["subject"] == "西湖游船——夜游路线"
        generated = _media(filename="generated.png", ai=True)
        generated.source_media_id = kwargs["source_media"].id
        generated.generation_state = "pending"
        image_db.add(generated)
        image_db.commit()
        image_db.refresh(generated)
        return generated

    monkeypatch.setattr(image_gen, "generate_article_image", fake_generate)
    monkeypatch.setattr(content_generator.topic_billing, "charge", lambda *_a, **_k: None)
    monkeypatch.setattr(content_generator.topic_billing, "price", lambda *_a, **_k: 0)

    selection, images = content_generator._prepare_article_image(
        db, topic, BrandProfile(ctrip_location={"name": "西湖"}), {},
        title="西湖游船", summary="夜游路线", body="正文", query="西湖怎么玩")
    assert selection and selection.generated
    assert len(images) == 1
    assert images[0].generation_state == "pending"

    content_generator._finalize_article_image(db, topic, doc, selection)
    db.commit()
    db.refresh(source)
    db.refresh(images[0])
    assert source.use_count == 3
    assert images[0].generation_state == "attached"
    assert images[0].generated_doc_id == 43


def test_blank_ctrip_location_reuses_original_without_edit(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    source = _media(use_count=2)
    db.add(source)
    db.commit()
    topic = SimpleNamespace(id=7, user_id=9)
    monkeypatch.setattr(content_generator, "_AUTO_IMAGE", True)
    monkeypatch.setattr(image_gen, "available", lambda: True)
    monkeypatch.setattr(
        image_gen, "generate_article_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应调用图生图")),
    )
    monkeypatch.setattr(content_generator, "_T2I_FALLBACK", False)

    selection, images = content_generator._prepare_article_image(
        db, topic, BrandProfile(ctrip_location={"name": "  "}), {}, title="标题")

    # No Ctrip location filled -> no longer skipped for lack of an image; reuse the original image directly, without triggering image-to-image.
    assert selection is not None and selection.generated is False
    assert selection.source_id == source.id
    assert [m.id for m in images] == [source.id]
    db.refresh(source)
    assert source.claim_token is not None
    assert source.image_edit_fail_count == 0


def test_failed_edit_cools_source_and_next_image_can_be_claimed(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    first = _media(filename="a.png", use_count=2)
    second = _media(filename="b.png", use_count=3)
    db.add_all([first, second])
    db.commit()
    topic = SimpleNamespace(id=7, user_id=9)
    profile = BrandProfile(ctrip_location={"name": "西湖"})
    monkeypatch.setattr(content_generator, "_AUTO_IMAGE", True)
    monkeypatch.setattr(image_gen, "available", lambda: True)
    monkeypatch.setattr(image_gen, "generate_article_image", lambda *_a, **_k: None)
    monkeypatch.setattr(content_generator, "_T2I_FALLBACK", False)

    selection, images = content_generator._prepare_article_image(db, topic, profile, {})
    assert selection is None and images == []
    db.refresh(first)
    assert first.image_edit_fail_count == 1
    assert first.image_edit_retry_at is not None
    assert content_generator._select_article_image(db, 7).id == second.id


def _fake_text_image(calls):
    def fake(image_db, **kwargs):
        calls.append(kwargs)
        generated = _media(filename="t2i.png", ai=True)
        generated.generation_state = "pending"
        image_db.add(generated)
        image_db.commit()
        image_db.refresh(generated)
        return generated
    return fake


def _t2i_env(monkeypatch, calls):
    monkeypatch.setattr(content_generator, "_AUTO_IMAGE", True)
    monkeypatch.setattr(content_generator, "_T2I_FALLBACK", True)
    monkeypatch.setattr(image_gen, "available", lambda: True)
    monkeypatch.setattr(image_gen, "generate_text_image", _fake_text_image(calls))
    monkeypatch.setattr(content_generator.topic_billing, "charge", lambda *_a, **_k: None)
    monkeypatch.setattr(content_generator.topic_billing, "price", lambda *_a, **_k: 0)


def test_empty_library_falls_back_to_text_image_and_attaches(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    calls = []
    _t2i_env(monkeypatch, calls)
    topic = SimpleNamespace(id=7, user_id=9)
    budget = {}

    selection, images = content_generator._prepare_article_image(
        db, topic, BrandProfile(industry="酒店"), budget,
        title="亲子游攻略", summary="带娃怎么玩", body="正文", query="亲子游")

    assert calls == [{"topic_id": 7, "user_id": 9, "title": "亲子游攻略",
                      "summary": "带娃怎么玩", "industry": "酒店"}]
    assert selection and selection.generated and selection.source_id is None
    assert budget["used"] == 1
    content_generator._finalize_article_image(db, topic, SimpleNamespace(id=50), selection)
    db.commit()
    db.refresh(images[0])
    assert images[0].generation_state == "attached"
    assert images[0].generated_doc_id == 50


def test_failed_edit_falls_back_and_both_count_against_budget(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    source = _media(use_count=2)
    db.add(source)
    db.commit()
    calls = []
    _t2i_env(monkeypatch, calls)
    monkeypatch.setattr(image_gen, "generate_article_image", lambda *_a, **_k: None)
    budget = {}

    selection, _ = content_generator._prepare_article_image(
        db, SimpleNamespace(id=7, user_id=9),
        BrandProfile(ctrip_location={"name": "西湖"}), budget, title="标题")

    assert selection and selection.source_id is None and len(calls) == 1
    assert budget["used"] == 2
    db.refresh(source)
    assert source.image_edit_fail_count == 1


def test_text_fallback_respects_budget_and_abort_cleans_up(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    calls = []
    _t2i_env(monkeypatch, calls)
    topic = SimpleNamespace(id=7, user_id=9)

    full = {"used": content_generator._IMAGE_GEN_MAX_PER_RUN}
    assert content_generator._prepare_article_image(
        db, topic, BrandProfile(), full, title="标题") == (None, [])
    assert calls == []

    deleted = []
    from geo.services import image_orphan_cleanup
    monkeypatch.setattr(image_orphan_cleanup, "mark_orphan_and_delete", deleted.append)
    selection, images = content_generator._prepare_article_image(
        db, topic, BrandProfile(), {}, title="标题")
    content_generator._abort_article_image(selection)
    assert deleted == [images[0].id]
