import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.api import aitoearn_channel
from geo.models.aitoearn import AitoearnAccountBindingORM, AitoearnAuthAttemptORM
from geo.models.user import UserORM
from geo.services import aitoearn_publisher


def _image_bytes(format_: str, size=(500, 800), mode="RGB",
                 color="navy") -> bytes:
    image = Image.new(mode, size, color)
    out = io.BytesIO()
    image.save(out, format=format_)
    return out.getvalue()


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    UserORM.__table__.create(engine)
    AitoearnAccountBindingORM.__table__.create(engine)
    AitoearnAuthAttemptORM.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_regular_user_only_sees_and_resolves_own_binding(db, monkeypatch):
    alice = UserORM(email="alice@example.com", password_hash="x", role="user")
    bob = UserORM(email="bob@example.com", password_hash="x", role="user")
    db.add_all([alice, bob])
    db.flush()
    alice_binding = AitoearnAccountBindingORM(
        user_id=alice.id, upstream_account_id="up-alice", account_type="twitter", display_name="Alice")
    bob_binding = AitoearnAccountBindingORM(
        user_id=bob.id, upstream_account_id="up-bob", account_type="twitter", display_name="Bob")
    db.add_all([alice_binding, bob_binding])
    db.commit()

    async def upstream(*_args, **_kwargs):
        return [
            {"account_id": "up-alice", "account_type": "twitter", "platform": "X", "nickname": "Alice"},
            {"account_id": "up-bob", "account_type": "twitter", "platform": "X", "nickname": "Bob"},
        ]

    monkeypatch.setattr(aitoearn_channel.ae_pub, "list_article_accounts", upstream)
    visible = asyncio.run(aitoearn_channel._visible_accounts(db, alice))
    assert [row["account_id"] for row in visible] == [f"binding:{alice_binding.id}"]
    assert "up-alice" not in str(visible)

    picked = asyncio.run(aitoearn_channel._pick_accounts(
        db, alice, [f"binding:{alice_binding.id}"]))
    assert picked[0]["account_id"] == "up-alice"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(aitoearn_channel._pick_accounts(
            db, alice, [f"binding:{bob_binding.id}"]))
    assert exc.value.status_code == 422


def test_staff_company_list_excludes_every_personal_binding(db, monkeypatch):
    owner = UserORM(email="owner@example.com", password_hash="x", role="user")
    db.add(owner)
    db.flush()
    db.add(AitoearnAccountBindingORM(
        user_id=owner.id, upstream_account_id="personal", account_type="twitter", display_name="Personal"))
    db.commit()

    async def upstream(*_args, **_kwargs):
        return [
            {"account_id": "personal", "account_type": "twitter", "platform": "X", "nickname": "Personal"},
            {"account_id": "company", "account_type": "twitter", "platform": "X", "nickname": "Company"},
        ]

    monkeypatch.setattr(aitoearn_channel.ae_pub, "list_article_accounts", upstream)
    staff = SimpleNamespace(id=999, role="admin")
    visible = asyncio.run(aitoearn_channel._visible_accounts(db, staff))
    assert [row["account_id"] for row in visible] == ["company:company"]


def test_domestic_binding_is_manageable_but_not_publishable(db, monkeypatch):
    user = UserORM(email="domestic@example.com", password_hash="x", role="user")
    db.add(user)
    db.flush()
    binding = AitoearnAccountBindingORM(
        user_id=user.id, upstream_account_id="domestic", account_type="xhs", display_name="旧小红书")
    db.add(binding)
    db.commit()

    async def upstream(*_args, **_kwargs):
        return [{"account_id": "domestic", "account_type": "xhs", "platform": "小红书", "nickname": "旧小红书"}]

    monkeypatch.setattr(aitoearn_channel.ae_pub, "list_article_accounts", upstream)
    assert asyncio.run(aitoearn_channel._visible_accounts(db, user)) == []
    with pytest.raises(HTTPException) as exc:
        asyncio.run(aitoearn_channel._pick_accounts(db, user, [f"binding:{binding.id}"]))
    assert exc.value.status_code == 422

    managed = aitoearn_channel.own_account_bindings(user, db)
    assert managed["accounts"][0]["publish_enabled"] is False


def test_auth_only_accepts_enabled_overseas_platforms(db, monkeypatch):
    user = UserORM(email="auth@example.com", password_hash="x", role="user")
    db.add(user)
    db.commit()
    calls = []

    class Client:
        async def start_account_auth(self, platform):
            calls.append(platform)
            return {"sessionId": f"session-{platform}", "url": "https://example.test/auth",
                    "expiresAt": "2030-01-01T00:00:00Z"}

    async def metas(*_args, **_kwargs):
        return {platform: {
            "platform": platform, "status": "available",
            "contentLimits": {"modes": ["article"]},
            "capabilities": {
                "auth": {"supported": True}, "publish": {"supported": True},
            },
        } for platform in aitoearn_channel.ae_pub.ENABLED_ACCOUNT_TYPES}

    monkeypatch.setattr(aitoearn_channel.ae_pub, "platform_meta", metas)
    monkeypatch.setattr(aitoearn_channel, "get_client", lambda: Client())
    for platform in sorted(aitoearn_channel.ae_pub.ENABLED_ACCOUNT_TYPES):
        result = asyncio.run(aitoearn_channel.start_account_auth(platform, user, db))
        assert result["url"] == "https://example.test/auth"
    assert set(calls) == set(aitoearn_channel.ae_pub.ENABLED_ACCOUNT_TYPES)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(aitoearn_channel.start_account_auth("xhs", user, db))
    assert exc.value.status_code == 422
    assert "xhs" not in calls


def test_auth_rejects_upstream_unavailable_platform(db, monkeypatch):
    user = UserORM(email="unavailable@example.com", password_hash="x", role="user")
    db.add(user)
    db.commit()

    async def metas(*_args, **_kwargs):
        return {"tiktok": {
            "platform": "tiktok", "status": "unavailable",
            "contentLimits": {"modes": ["article"]},
            "capabilities": {
                "auth": {"supported": False}, "publish": {"supported": False},
            },
        }}

    monkeypatch.setattr(aitoearn_channel.ae_pub, "platform_meta", metas)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(aitoearn_channel.start_account_auth("tiktok", user, db))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "AITOEARN_PLATFORM_UNAVAILABLE"


def test_article_account_list_filters_out_domestic_platforms(monkeypatch):
    async def metas(*_args, **_kwargs):
        return {
            platform: {
                "platform": platform, "status": "available",
                "contentLimits": {"modes": ["article"]},
                "capabilities": {
                    "auth": {"supported": True}, "publish": {"supported": True},
                },
            } for platform in ("twitter", "xhs")
        }

    class Client:
        async def list_accounts(self):
            return [
                {"id": "overseas", "type": "twitter", "nickname": "X account"},
                {"id": "domestic", "type": "xhs", "nickname": "XHS account"},
            ]

    monkeypatch.setattr(aitoearn_publisher, "platform_meta", metas)
    monkeypatch.setattr(aitoearn_publisher, "get_client", lambda: Client())
    rows = asyncio.run(aitoearn_publisher.list_article_accounts())
    assert [row["account_id"] for row in rows] == ["overseas"]


def test_tiktok_single_image_is_uploaded_twice(monkeypatch):
    rendered = aitoearn_publisher.RenderedContent(
        title="title", body="body", image_urls=["https://example.test/one.jpg"])
    captured = {}

    async def metas(*_args, **_kwargs):
        return {"tiktok": {"platform": "tiktok"}}

    async def fetch(_url):
        return _image_bytes("PNG"), "one.png"

    class Client:
        async def upload_asset(self, data, name):
            with Image.open(io.BytesIO(data)) as uploaded:
                assert uploaded.format == "JPEG"
            return f"https://assets.test/{name}"

        async def create_flow(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    monkeypatch.setattr(aitoearn_publisher, "platform_meta", metas)
    monkeypatch.setattr(aitoearn_publisher, "render_for_platform", lambda *_a, **_k: rendered)
    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", fetch)
    monkeypatch.setattr(aitoearn_publisher, "get_client", lambda: Client())

    task = SimpleNamespace(channel_meta_json=json.dumps({
        "account_id": "upstream", "account_type": "tiktok", "flow_id": "flow-1",
    }))
    result = asyncio.run(aitoearn_publisher.publish_task(task, SimpleNamespace()))

    assert captured["content"]["media"] == [
        {"url": "https://assets.test/one.jpg"},
        {"url": "https://assets.test/copy-one.jpg"},
    ]
    assert result["asset_map"][aitoearn_publisher._TIKTOK_DUPLICATE_ASSET_KEY] == \
        "https://assets.test/copy-one.jpg"


@pytest.mark.parametrize("format_,filename", [
    ("PNG", "source.png"),
    ("WEBP", "source.webp"),
    ("JPEG", "source.jpeg"),
])
def test_tiktok_image_is_real_jpeg_and_keeps_compliant_size(format_, filename):
    source = _image_bytes(format_)
    meta = {"platform": "tiktok", "mediaRules": {
        "imageFormats": ["jpg", "jpeg", "webp"], "maxImageSize": 20 * 1024 * 1024,
        "maxImageShortSide": 1080,
    }}
    image = aitoearn_publisher._inspect_image("test", source, filename)
    aitoearn_publisher._evaluate_image(image, meta, 1)
    data, name = aitoearn_publisher._prepare_image(image, meta)

    assert name == "source.jpg"
    assert len(data) <= 20 * 1024 * 1024
    with Image.open(io.BytesIO(data)) as converted:
        assert converted.format == "JPEG"
        assert converted.size == (500, 800)


def test_tiktok_image_normalizes_short_edge_and_transparency():
    transparent = _image_bytes("PNG", size=(100, 200), mode="RGBA",
                               color=(255, 0, 0, 0))
    meta = {"platform": "tiktok", "mediaRules": {
        "imageFormats": ["jpg", "jpeg", "webp"], "maxImageShortSide": 1080,
    }}
    image = aitoearn_publisher._inspect_image("test", transparent, "alpha.png")
    aitoearn_publisher._evaluate_image(image, meta, 1)
    data, _ = aitoearn_publisher._prepare_image(image, meta)
    with Image.open(io.BytesIO(data)) as converted:
        assert converted.size == (360, 720)
        assert all(channel >= 250 for channel in converted.getpixel((10, 10)))

    large = _image_bytes("PNG", size=(1200, 2400))
    image = aitoearn_publisher._inspect_image("test", large, "large.png")
    aitoearn_publisher._evaluate_image(image, meta, 1)
    data, _ = aitoearn_publisher._prepare_image(image, meta)
    with Image.open(io.BytesIO(data)) as converted:
        assert converted.size == (1080, 2160)


def test_tiktok_image_compresses_without_crossing_min_short_edge():
    noisy = Image.effect_noise((1600, 1600), 100).convert("RGB")
    source = io.BytesIO()
    noisy.save(source, format="PNG")

    meta = {"platform": "tiktok", "mediaRules": {
        "imageFormats": ["jpg", "jpeg", "webp"], "maxImageSize": 100_000,
        "maxImageShortSide": 1080,
    }}
    image = aitoearn_publisher._inspect_image("test", source.getvalue(), "noise.png")
    aitoearn_publisher._evaluate_image(image, meta, 1)
    data, _ = aitoearn_publisher._prepare_image(image, meta)

    assert len(data) <= 100_000
    with Image.open(io.BytesIO(data)) as converted:
        assert min(converted.size) >= 360

    with pytest.raises(aitoearn_publisher.AitoearnError, match="无法.*压缩"):
        tiny_meta = {"platform": "tiktok", "mediaRules": {
            "imageFormats": ["jpg"], "maxImageSize": 10,
            "maxImageShortSide": 1080,
        }}
        impossible = aitoearn_publisher._inspect_image(
            "test", source.getvalue(), "noise.png")
        aitoearn_publisher._evaluate_image(impossible, tiny_meta, 1)
        aitoearn_publisher._prepare_image(impossible, tiny_meta)


def test_tiktok_publish_uploads_jpeg_media_and_cover(monkeypatch):
    urls = ["https://example.test/one.png", "https://example.test/two.webp"]
    rendered = aitoearn_publisher.RenderedContent(
        title="title", body="body", image_urls=urls, cover_url=urls[0])
    uploads = []
    captured = {}

    async def metas(*_args, **_kwargs):
        return {"tiktok": {"platform": "tiktok"}}

    async def fetch(url):
        return _image_bytes("PNG"), url.rsplit("/", 1)[-1]

    class Client:
        async def upload_asset(self, data, name):
            with Image.open(io.BytesIO(data)) as uploaded:
                assert uploaded.format == "JPEG"
            uploads.append(name)
            return f"https://assets.test/{name}"

        async def create_flow(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    client = Client()
    monkeypatch.setattr(aitoearn_publisher, "platform_meta", metas)
    monkeypatch.setattr(aitoearn_publisher, "render_for_platform", lambda *_a, **_k: rendered)
    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", fetch)
    monkeypatch.setattr(aitoearn_publisher, "get_client", lambda: client)

    task = SimpleNamespace(channel_meta_json=json.dumps({
        "account_id": "upstream", "account_type": "tiktok", "flow_id": "flow-2",
        "asset_map": {urls[0]: "https://assets.test/legacy.png"},
    }))
    asyncio.run(aitoearn_publisher.publish_task(task, SimpleNamespace()))

    assert uploads == ["one.jpg", "two.jpg"]
    assert captured["content"]["media"] == [
        {"url": "https://assets.test/one.jpg"},
        {"url": "https://assets.test/two.jpg"},
    ]
    assert captured["content"]["cover"] == {
        "url": "https://assets.test/one.jpg"}


def test_non_tiktok_asset_upload_keeps_original_bytes(monkeypatch):
    uploaded = {}
    original = _image_bytes("PNG")

    async def fetch(_url):
        return original, "source.png"

    class Client:
        async def upload_asset(self, data, name):
            uploaded.update(data=data, name=name)
            return "https://assets.test/source.png"

    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", fetch)
    monkeypatch.setattr(aitoearn_publisher, "get_client", lambda: Client())

    asyncio.run(aitoearn_publisher.ensure_assets(["https://example.test/source.png"]))
    assert uploaded == {"data": original, "name": "source.png"}


def test_facebook_publish_reuploads_webp_as_jpg(monkeypatch):
    uploaded = {}
    captured = {}
    url = "https://example.test/source.webp"
    rendered = aitoearn_publisher.RenderedContent(
        title="title", body="body", image_urls=[url], cover_url=url)

    async def metas(*_args, **_kwargs):
        return {"facebook": {"platform": "facebook", "mediaRules": {
            "imageFormats": ["jpg", "jpeg", "png", "gif", "bmp", "tiff"],
            "maxImageSize": 10 * 1024 * 1024,
        }}}

    async def fetch(_url):
        return _image_bytes("WEBP", mode="RGBA", color=(255, 0, 0, 0)), "source.webp"

    class Client:
        async def upload_asset(self, data, name):
            uploaded.update(data=data, name=name)
            return "https://assets.test/source.jpg"

        async def create_flow(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    monkeypatch.setattr(aitoearn_publisher, "platform_meta", metas)
    monkeypatch.setattr(aitoearn_publisher, "render_for_platform", lambda *_a, **_k: rendered)
    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", fetch)
    monkeypatch.setattr(aitoearn_publisher, "get_client", lambda: Client())

    task = SimpleNamespace(channel_meta_json=json.dumps({
        "account_id": "upstream", "account_type": "facebook", "flow_id": "flow-fb",
        "asset_map": {url: "https://assets.test/legacy.webp"},
    }))
    result = asyncio.run(aitoearn_publisher.publish_task(task, SimpleNamespace()))

    assert result["asset_map"][url] == "https://assets.test/source.jpg"
    assert captured["content"]["media"] == [{"url": "https://assets.test/source.jpg"}]
    assert uploaded["name"] == "source.jpg"
    with Image.open(io.BytesIO(uploaded["data"])) as converted:
        assert converted.format == "JPEG"
        assert converted.getpixel((10, 10))[0] >= 250


def test_preflight_uses_real_format_not_extension(monkeypatch):
    data = _image_bytes("WEBP")

    async def fetch(_url):
        return data, "looks-like.jpg"

    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", fetch)
    rendered = aitoearn_publisher.RenderedContent(
        title="title", body="body", image_urls=["test"], original_image_count=1)
    meta = {"platform": "facebook", "mediaRules": {
        "imageFormats": ["jpg", "jpeg", "png"], "maxImageSize": 10 * 1024 * 1024,
    }}

    check = asyncio.run(aitoearn_publisher.preflight_media(rendered, meta))

    assert check.status == "repairable"
    assert check.images[0].format == "webp"
    assert check.images[0].sha256
    assert any(issue.action == "convert_jpg" for issue in check.issues)


def test_instagram_ratio_is_padded_without_cropping(monkeypatch):
    data = _image_bytes("JPEG", size=(500, 1000))

    async def fetch(_url):
        return data, "portrait.jpg"

    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", fetch)
    rendered = aitoearn_publisher.RenderedContent(
        title="title", body="body", image_urls=["test"], original_image_count=1)
    meta = {"platform": "instagram", "mediaRules": {
        "imageFormats": ["jpg", "jpeg"], "maxImageSize": 8 * 1024 * 1024,
        "aspectRatio": {"min": 0.8, "max": 1.91},
    }}

    check = asyncio.run(aitoearn_publisher.preflight_media(rendered, meta))
    converted, name = aitoearn_publisher._prepare_image(check.images[0], meta)

    assert check.status == "repairable"
    assert any(issue.action == "pad_jpg" for issue in check.issues)
    assert name == "portrait.jpg"
    with Image.open(io.BytesIO(converted)) as image:
        assert image.size == (800, 1000)


def test_corrupt_and_unsafe_pixel_images_are_blocked(monkeypatch):
    rendered = aitoearn_publisher.RenderedContent(
        title="title", body="body", image_urls=["test"], original_image_count=1)

    async def corrupt(_url):
        return b"not-an-image", "broken.jpg"

    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", corrupt)
    assert asyncio.run(aitoearn_publisher.preflight_media(
        rendered, {"platform": "facebook"})).status == "blocked"

    data = _image_bytes("PNG", size=(20, 20))

    async def large_pixels(_url):
        return data, "large.png"

    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", large_pixels)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    assert asyncio.run(aitoearn_publisher.preflight_media(
        rendered, {"platform": "facebook"})).status == "blocked"


def test_signed_snapshot_reuses_hash_and_changed_image_rechecks(monkeypatch):
    current = {"data": _image_bytes("WEBP", size=(500, 800))}

    async def fetch(_url):
        return current["data"], "source.webp"

    monkeypatch.setattr(aitoearn_publisher, "_fetch_image", fetch)
    rendered = aitoearn_publisher.RenderedContent(
        title="title", body="body", image_urls=["test"], original_image_count=1)
    meta = {"platform": "facebook", "mediaRules": {
        "imageFormats": ["jpg", "jpeg", "png"], "maxImageSize": 10 * 1024 * 1024,
    }}
    original = asyncio.run(aitoearn_publisher.preflight_media(rendered, meta))
    snapshot = aitoearn_publisher.preflight_snapshot(original, meta)

    inspect_image = aitoearn_publisher._inspect_image
    monkeypatch.setattr(
        aitoearn_publisher, "_inspect_image",
        lambda *_args, **_kwargs: pytest.fail("unchanged hash must not decode with Pillow"),
    )
    reused, was_reused = asyncio.run(
        aitoearn_publisher.revalidate_preflight(rendered, meta, snapshot))
    assert was_reused is True
    assert reused.images[0].sha256 == original.images[0].sha256

    monkeypatch.setattr(aitoearn_publisher, "_inspect_image", inspect_image)
    current["data"] = _image_bytes("PNG", size=(640, 640))
    refreshed, was_reused = asyncio.run(
        aitoearn_publisher.revalidate_preflight(rendered, meta, snapshot))
    assert was_reused is False
    assert refreshed.images[0].format == "png"
    assert refreshed.images[0].width == 640

    changed_rules = {**meta, "mediaRules": {
        **meta["mediaRules"], "maxImageSize": 5 * 1024 * 1024,
    }}
    _, was_reused = asyncio.run(
        aitoearn_publisher.revalidate_preflight(rendered, changed_rules, snapshot))
    assert was_reused is False


def test_preflight_token_is_bound_to_user_doc_accounts_and_media():
    payload = aitoearn_channel.AccountsPayload(
        account_ids=["binding:2", "binding:1"], media_ids=[7])
    token, _ = aitoearn_channel._preflight_token(
        9, 3, payload,
        {"binding:1": "facebook", "binding:2": "instagram"},
        {"facebook": {"status": "ready"}},
    )

    subset = aitoearn_channel.AccountsPayload(
        account_ids=["binding:1"], media_ids=[7], preflight_token=token)
    assert aitoearn_channel._verified_preflight_token(9, 3, subset)["doc_id"] == 9
    assert aitoearn_channel._verified_preflight_token(9, 4, subset) == {}
    changed_media = aitoearn_channel.AccountsPayload(
        account_ids=["binding:1"], media_ids=[8], preflight_token=token)
    assert aitoearn_channel._verified_preflight_token(9, 3, changed_media) == {}
