"""Option catalog for profile/filter cards persisted to DB -- covers the two TODOs S-33 / S-34 in docs.

The two original holes:
1. The 7 capability items were literals in `service/guidance.py`, and **copied twice**
   (one each in the `{"专业能力","我方画像"}` and `"公司/团队规模或专业能力"` branches),
   so changing one and missing the other made behavior diverge;
2. Custom items the user wrote in the card only landed in that profile row's `service_capabilities_json` --
   just a value on that profile, **never becoming a selectable option next time**; the next session's card showed the same few.

Now all five option kinds read `sales_agent_option_catalog`: rows with both ownership columns NULL are system presets
(shared by all accounts, seeded by migration c2e8a5d31f94); rows with both set are user-written, private per brand.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_option_catalog.py -q
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.constants import TARGET_INDUSTRY_OPTIONS
from agents.sales_agent.models import SalesAgentOptionCatalogORM, SalesAgentSellerProfileORM
from agents.sales_agent.service import (
    CAPABILITY,
    COMPANY_SIZE,
    CUSTOM_OPTION_LIMIT,
    INDUSTRY,
    OPTION_KINDS,
    SalesAgentError,
    TYPICAL_CUSTOMER,
    _new_brand_key,
    add_custom_option,
    add_custom_options,
    archive_custom_option,
    build_missing_info_guidance_card,
    build_slot_catalog,
    create_custom_option,
    is_system_option,
    list_option_catalog,
    load_card_options,
    load_options,
    option_values,
    reset_option_cache,
    update_custom_option,
)
from geo.database import Base
from geo.models.user import UserORM

USER = 2000001
OTHER_USER = 2000002

# System preset capability items seeded by the migration, same source as c2e8a5d31f94._CAPABILITIES
SYSTEM_CAPABILITIES = (
    "AI-GEO优化", "SEO技术团队", "官网建设/优化", "内容营销能力",
    "行业解决方案经验", "数据分析能力", "销售/交付团队",
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """The catalog cache is process-level; not clearing it leaks between tests."""
    reset_option_cache()
    yield
    reset_option_cache()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(UserORM(id=USER, email="catalog@test.local", password_hash="x", is_active=True))
    session.add(UserORM(id=OTHER_USER, email="catalog2@test.local", password_hash="x", is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _brand(db, user_id: int, name: str) -> int:
    # brand_key must be set explicitly: the column default is "" and the unique index is (user_id, brand_key),
    # so creating a second brand for the same account gives two "" rows -> immediate index collision. The production path
    # (brand creation in service/profiles.py) also uses _new_brand_key.
    row = SalesAgentSellerProfileORM(user_id=user_id, name=name,
                                     brand_key=_new_brand_key(db, user_id))
    db.add(row)
    db.commit()
    return int(row.id)


def _seed_system(db) -> None:
    """`Base.metadata.create_all` only creates tables, the seed is in the migration -- DB-path tests must load it themselves."""
    for idx, value in enumerate(SYSTEM_CAPABILITIES):
        db.add(SalesAgentOptionCatalogORM(
            kind=CAPABILITY, value=value, label=value, source="system", sort_order=(idx + 1) * 10,
        ))
    db.commit()
    reset_option_cache()


# ─────────────── Fallback: behavior unchanged when the migration hasn't run ───────────────


def test_empty_table_falls_back_to_code_constants(db):
    """Migration not run / table empty -- must be exactly as before the change: no blank screen, no missing options."""
    assert [o["value"] for o in load_options(db, CAPABILITY)] == list(SYSTEM_CAPABILITIES)
    assert option_values(db, INDUSTRY) == tuple(v for v in TARGET_INDUSTRY_OPTIONS if v != "自定义行业")


def test_no_db_falls_back_too(db):
    assert [o["value"] for o in load_options(None, CAPABILITY)] == list(SYSTEM_CAPABILITIES)


def test_code_fallback_adds_missing_builtin_industry_without_migration(db):
    """Without changing the production DB: when the old option_catalog lacks 「法律服务」 ("legal services"), the code fallback must keep cards and slot extraction working."""
    old_industries = [value for value in TARGET_INDUSTRY_OPTIONS if value != "法律服务"]
    for idx, value in enumerate(old_industries):
        db.add(SalesAgentOptionCatalogORM(
            kind=INDUSTRY, value=value, label=value, source="system", sort_order=(idx + 1) * 10,
        ))
    db.commit()
    reset_option_cache()

    values = option_values(db, INDUSTRY)
    assert "法律服务" in values
    assert values.index("法律服务") < len(TARGET_INDUSTRY_OPTIONS)


def test_company_size_keeps_numeric_ranges(db):
    """Size buckets aren't a plain label list -- `_bucket_for_employee_count` relies on these two numbers."""
    sizes = load_options(db, COMPANY_SIZE)
    assert sizes[0]["min_employees"] == 1 and sizes[0]["max_employees"] == 10
    assert sizes[-1]["max_employees"] is None


def test_unknown_kind_returns_nothing(db):
    assert load_options(db, "not_a_kind") == []
    assert set(load_card_options(db)) == set(OPTION_KINDS)


# ─────────────── Read: system presets + brand-private ───────────────


def test_system_rows_win_the_fallback(db):
    _seed_system(db)
    assert [o["value"] for o in load_options(db, CAPABILITY)] == list(SYSTEM_CAPABILITIES)


def test_custom_option_shows_up_next_time(db):
    """The core of S-33 / S-34: capability items the user wrote are in the options next time the card opens."""
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")

    values = option_values(db, CAPABILITY, USER, brand)
    assert "跨境独立站代运营" in values
    # Appended after system presets, not inserted ahead
    assert values[: len(SYSTEM_CAPABILITIES)] == SYSTEM_CAPABILITIES


def test_custom_option_is_private_to_the_brand(db):
    """Capabilities written by brand A aren't visible to brand B -- that's the definition of "brand-level private"."""
    _seed_system(db)
    brand_a, brand_b = _brand(db, USER, "A 品牌"), _brand(db, USER, "B 品牌")
    add_custom_option(db, CAPABILITY, USER, brand_a, "口腔行业内容合规")

    assert "口腔行业内容合规" in option_values(db, CAPABILITY, USER, brand_a)
    assert "口腔行业内容合规" not in option_values(db, CAPABILITY, USER, brand_b)


def test_custom_option_is_invisible_to_other_accounts(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    other_brand = _brand(db, OTHER_USER, "别人的品牌")
    add_custom_option(db, CAPABILITY, USER, brand, "只有我能看到")

    assert "只有我能看到" not in option_values(db, CAPABILITY, OTHER_USER, other_brand)
    # Without ownership, only system presets are visible
    assert "只有我能看到" not in option_values(db, CAPABILITY)


# ─────────────── Write: idempotent, cap, action words ───────────────


def test_adding_the_same_value_twice_does_not_duplicate(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    assert add_custom_options(db, CAPABILITY, USER, brand, ["跨境独立站代运营"]) == 1
    # The second time isn't a new addition -- an inflated count would hit the cap early
    assert add_custom_options(db, CAPABILITY, USER, brand, ["跨境独立站代运营"]) == 0
    assert option_values(db, CAPABILITY, USER, brand).count("跨境独立站代运营") == 1


def test_archived_value_revives_instead_of_inserting_again(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    row = add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")
    archive_custom_option(db, USER, int(row.id))
    assert "跨境独立站代运营" not in option_values(db, CAPABILITY, USER, brand)

    again = add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")
    assert int(again.id) == int(row.id)
    assert "跨境独立站代运营" in option_values(db, CAPABILITY, USER, brand)


def test_system_preset_is_not_stored_as_custom(db):
    """Selecting a preset must not grow a duplicate private row in the catalog."""
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    assert add_custom_options(db, CAPABILITY, USER, brand, list(SYSTEM_CAPABILITIES)) == 0
    assert option_values(db, CAPABILITY, USER, brand) == SYSTEM_CAPABILITIES


@pytest.mark.parametrize("token", ["自定义能力", "自定义行业", "自定义", "其他能力"])
def test_action_tokens_never_enter_the_catalog(db, token):
    """「自定义能力」 ("custom capability") is the "I'll write my own" action entry, not a capability.

    Stored, it leaves 「您是一家提供自定义能力的公司」 ("you are a company providing custom capability") in the profile (verbatim from production on 2026-08-23).
    """
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    assert add_custom_options(db, CAPABILITY, USER, brand, [token]) == 0
    assert token not in option_values(db, CAPABILITY, USER, brand)


def test_without_a_brand_nothing_is_written(db):
    """Without ownership it can't be stored -- storing as a system preset would pollute every account."""
    _seed_system(db)
    assert add_custom_options(db, CAPABILITY, USER, None, ["无主能力"]) == 0
    assert "无主能力" not in option_values(db, CAPABILITY, USER, None)


def test_custom_options_are_capped(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    added = add_custom_options(db, CAPABILITY, USER, brand, [f"能力{i}" for i in range(CUSTOM_OPTION_LIMIT + 5)])
    assert added == CUSTOM_OPTION_LIMIT
    custom = [v for v in option_values(db, CAPABILITY, USER, brand) if v not in SYSTEM_CAPABILITIES]
    assert len(custom) == CUSTOM_OPTION_LIMIT


def test_cache_is_invalidated_on_write(db):
    """Read once to warm the cache, then write -- the write must be readable immediately, not after a process restart."""
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    assert "新能力" not in option_values(db, CAPABILITY, USER, brand)
    add_custom_option(db, CAPABILITY, USER, brand, "新能力")
    assert "新能力" in option_values(db, CAPABILITY, USER, brand)


def test_is_system_option_ignores_brand_rows(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")
    assert is_system_option(db, CAPABILITY, "AI-GEO优化") is True
    assert is_system_option(db, CAPABILITY, "跨境独立站代运营") is False


# ─────────────── Extraction word list shares its source with card options ───────────────


def test_custom_industry_becomes_recognizable(db):
    """Today, entering 「口腔正畸」 ("orthodontics") via "custom industry" goes into the campaign, but since it's not in
    `TARGET_INDUSTRY_OPTIONS`, later mentions of 「口腔正畸」 are never recognized. Only after persisting to DB does this path work.
    """
    brand = _brand(db, USER, "A 品牌")
    assert "口腔正畸" not in build_slot_catalog(db, USER, brand).industries
    add_custom_option(db, INDUSTRY, USER, brand, "口腔正畸")
    assert "口腔正畸" in build_slot_catalog(db, USER, brand).industries


def test_slot_catalog_still_has_the_builtin_words(db):
    catalog = build_slot_catalog(db, USER, None)
    assert "工业制造" in catalog.industries
    assert "全国" not in catalog.regions
    assert "北京" in catalog.regions
    assert "11-100 名员工" in catalog.company_sizes


# ─────────────── Card injection ───────────────


def _capability_card(catalog_options=None, missing: str = "我方画像") -> dict:
    card = build_missing_info_guidance_card(
        [missing], question="我方画像", catalog_options=catalog_options,
    )
    return card["data"]["question_card"]


def test_card_without_injection_is_unchanged(db):
    """Without injection, every item matches pre-change -- zero regression in environments without the migration."""
    card = _capability_card()
    assert [o["value"] for o in card["options"]] == list(SYSTEM_CAPABILITIES)
    # 「自定义能力」 ("custom capability") no longer takes an option slot: the add entry moved into the card's built-in input box
    assert card["allow_custom_option"] is True


def test_both_profile_gaps_render_the_same_card():
    """The two gaps used to each copy the capability table; change one and miss the other and they diverge.

    Compare only question_card: the outer payload's `missing_items` is supposed to differ by gap.
    """
    assert _capability_card(missing="我方画像") == _capability_card(missing="公司/团队规模或专业能力")


def test_card_shows_the_brands_custom_options(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")

    card = _capability_card(load_card_options(db, USER, brand))
    values = [o["value"] for o in card["options"]]
    assert "跨境独立站代运营" in values
    # The add entry is now the card's built-in input box; it no longer takes an option slot, so it can't be cut off by max_items
    assert card["allow_custom_option"] is True
    assert values[-1] == "跨境独立站代运营"


def test_custom_options_sit_after_the_system_presets(db):
    """Newly added custom items rank after system presets.

    The position isn't arbitrary: `sort_order` for system items is 10..180, custom items start at 1000. Changing the order would
    turn into "just-added items inserted among presets" or "adding 20 pushes presets out". The add entry itself has moved from
    an option slot into the card's `allow_custom_option` input box, and the count cap is enforced by `custom_option_max`.
    """
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")
    add_custom_option(db, CAPABILITY, USER, brand, "口腔诊所获客代运营")

    card = _capability_card(load_card_options(db, USER, brand))
    values = [o["value"] for o in card["options"]]

    assert card["allow_custom_option"] is True
    assert card["custom_option_max"] == 20
    # The two custom items are last, in insertion order
    assert values[-2:] == ["跨境独立站代运营", "口腔诊所获客代运营"]
    # Not a single system preset was pushed out, and all rank ahead of custom items
    assert values.index("AI-GEO优化") < values.index("跨境独立站代运营")
    assert len([v for v in values if v in ("AI-GEO优化", "SEO技术团队", "官网建设/优化")]) == 3


def test_custom_options_keep_their_source_marker(db):
    """The frontend uses source to distinguish user-added items from presets."""
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")
    options = _capability_card(load_card_options(db, USER, brand))["options"]
    assert next(o for o in options if o["value"] == "跨境独立站代运营")["source"] == "custom"


# ─────────────── REST: hidden admin page /sales-agent/admin ───────────────


def test_list_groups_by_kind(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    payload = list_option_catalog(db, USER, seller_profile_id=brand)
    assert set(payload["items"]) == set(OPTION_KINDS)
    assert payload["custom_option_limit"] == CUSTOM_OPTION_LIMIT


def test_list_rejects_unknown_kind(db):
    with pytest.raises(SalesAgentError) as e:
        list_option_catalog(db, USER, kind="not_a_kind")
    assert e.value.status_code == 400


def test_create_requires_a_brand(db):
    with pytest.raises(SalesAgentError) as e:
        create_custom_option(db, USER, CAPABILITY, None, "无主能力")
    assert e.value.status_code == 400


def test_create_rejects_duplicates_and_presets(db):
    _seed_system(db)
    brand = _brand(db, USER, "A 品牌")
    create_custom_option(db, USER, CAPABILITY, brand, "跨境独立站代运营")
    with pytest.raises(SalesAgentError) as dup:
        create_custom_option(db, USER, CAPABILITY, brand, "跨境独立站代运营")
    assert dup.value.status_code == 409
    with pytest.raises(SalesAgentError) as preset:
        create_custom_option(db, USER, CAPABILITY, brand, "AI-GEO优化")
    assert preset.value.status_code == 409


def test_create_rejects_action_tokens(db):
    brand = _brand(db, USER, "A 品牌")
    with pytest.raises(SalesAgentError) as e:
        create_custom_option(db, USER, CAPABILITY, brand, "自定义能力")
    assert e.value.status_code == 400


def test_create_returns_a_serialized_row(db):
    brand = _brand(db, USER, "A 品牌")
    payload = create_custom_option(db, USER, TYPICAL_CUSTOMER, brand, "连锁口腔机构", description="多门店")
    assert payload["source"] == "custom"
    assert payload["editable"] is True
    assert payload["seller_profile_id"] == brand


def test_archive_refuses_other_peoples_rows(db):
    """Ownership mismatch returns 404, not 403 -- a 403 admits the id exists."""
    brand = _brand(db, USER, "A 品牌")
    row = add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")
    with pytest.raises(SalesAgentError) as e:
        archive_custom_option(db, OTHER_USER, int(row.id))
    assert e.value.status_code == 404


def test_archive_refuses_system_rows(db):
    """System preset rows may never be modified through the API under any circumstances."""
    _seed_system(db)
    system_row = db.query(SalesAgentOptionCatalogORM).filter_by(source="system").first()
    with pytest.raises(SalesAgentError) as e:
        archive_custom_option(db, USER, int(system_row.id))
    assert e.value.status_code == 404


def test_archive_is_idempotent(db):
    brand = _brand(db, USER, "A 品牌")
    row = add_custom_option(db, CAPABILITY, USER, brand, "跨境独立站代运营")
    first = archive_custom_option(db, USER, int(row.id))
    assert archive_custom_option(db, USER, int(row.id))["archived_at"] == first["archived_at"]


# ─────────────── Description (desc) -- the small line under a card option's title ───────────────


def _typical_customer_card(catalog_options=None) -> dict:
    card = build_missing_info_guidance_card(
        ["典型客户或适合服务的客户类型"], question="典型客户", catalog_options=catalog_options,
    )
    return card["data"]["question_card"]


def test_custom_option_description_reaches_the_card(db):
    """Custom item descriptions must render as desc just like system presets.

    The whole chain was already there; what was always missing is "nobody writes anything into description" --
    items added on the spot in the chat card carry only a value, so the admin page must be able to fill it in.
    """
    brand = _brand(db, USER, "A 品牌")
    create_custom_option(
        db, USER, TYPICAL_CUSTOMER, brand, "连锁口腔机构",
        description="3 家以上门店,总部统一投放但各院区自己接诊。",
    )
    option = next(
        o for o in _typical_customer_card(load_card_options(db, USER, brand))["options"]
        if o["value"] == "连锁口腔机构"
    )
    assert option["desc"] == "3 家以上门店,总部统一投放但各院区自己接诊。"


def test_option_without_description_has_no_desc_key(db):
    """Without a description, don't stuff an empty string -- the frontend decides whether to render that line by whether desc exists."""
    brand = _brand(db, USER, "A 品牌")
    add_custom_option(db, TYPICAL_CUSTOMER, USER, brand, "连锁口腔机构")
    option = next(
        o for o in _typical_customer_card(load_card_options(db, USER, brand))["options"]
        if o["value"] == "连锁口腔机构"
    )
    assert "desc" not in option


def test_update_writes_the_description(db):
    brand = _brand(db, USER, "A 品牌")
    row = add_custom_option(db, TYPICAL_CUSTOMER, USER, brand, "连锁口腔机构")
    payload = update_custom_option(db, USER, int(row.id), description="3 家以上门店")
    assert payload["description"] == "3 家以上门店"
    option = next(
        o for o in _typical_customer_card(load_card_options(db, USER, brand))["options"]
        if o["value"] == "连锁口腔机构"
    )
    # If the cache isn't invalidated, the card still shows the old copy
    assert option["desc"] == "3 家以上门店"


def test_update_leaves_absent_fields_alone(db):
    """Passing only the description must not wipe the display name -- each field is updated independently."""
    brand = _brand(db, USER, "A 品牌")
    row = create_custom_option(
        db, USER, TYPICAL_CUSTOMER, brand, "连锁口腔机构", label="连锁口腔", description="旧说明",
    )
    after = update_custom_option(db, USER, int(row["id"]), description="新说明")
    assert (after["label"], after["description"]) == ("连锁口腔", "新说明")
    assert update_custom_option(db, USER, int(row["id"]), label="连锁口腔机构")["description"] == "新说明"


def test_update_can_clear_the_description(db):
    brand = _brand(db, USER, "A 品牌")
    row = create_custom_option(db, USER, TYPICAL_CUSTOMER, brand, "连锁口腔机构", description="旧说明")
    assert update_custom_option(db, USER, int(row["id"]), description="")["description"] == ""


def test_update_never_leaves_an_empty_label(db):
    """Clearing the display name falls back to value; no option may be left without a title."""
    brand = _brand(db, USER, "A 品牌")
    row = add_custom_option(db, TYPICAL_CUSTOMER, USER, brand, "连锁口腔机构")
    assert update_custom_option(db, USER, int(row.id), label="   ")["label"] == "连锁口腔机构"


def test_update_refuses_other_peoples_rows(db):
    brand = _brand(db, USER, "A 品牌")
    row = add_custom_option(db, TYPICAL_CUSTOMER, USER, brand, "连锁口腔机构")
    with pytest.raises(SalesAgentError) as e:
        update_custom_option(db, OTHER_USER, int(row.id), description="改别人的")
    assert e.value.status_code == 404


def test_update_refuses_system_rows(db):
    """System presets are shared by all accounts; one person's change would change it for everyone -- read-only."""
    _seed_system(db)
    system_row = db.query(SalesAgentOptionCatalogORM).filter_by(source="system").first()
    with pytest.raises(SalesAgentError) as e:
        update_custom_option(db, USER, int(system_row.id), description="改预置项")
    assert e.value.status_code == 404


def test_readding_a_removed_option_refreshes_its_description(db):
    """"Delete and re-add" is the most intuitive way to change a description; the revived row must use the new description."""
    brand = _brand(db, USER, "A 品牌")
    row = create_custom_option(db, USER, TYPICAL_CUSTOMER, brand, "连锁口腔机构", description="旧说明")
    archive_custom_option(db, USER, int(row["id"]))
    again = create_custom_option(db, USER, TYPICAL_CUSTOMER, brand, "连锁口腔机构", description="新说明")
    assert (again["id"], again["description"]) == (row["id"], "新说明")


def test_chat_flow_readd_does_not_wipe_the_description(db):
    """The chat flow collects custom items in batch without descriptions; that must not wash out descriptions filled in on the admin page.

    `add_custom_options` re-collects the whole slot every turn, and existing rows take exactly the revive/hit path.
    """
    brand = _brand(db, USER, "A 品牌")
    row = create_custom_option(db, USER, TYPICAL_CUSTOMER, brand, "连锁口腔机构", description="管理页写的说明")
    archive_custom_option(db, USER, int(row["id"]))
    add_custom_options(db, TYPICAL_CUSTOMER, USER, brand, ["连锁口腔机构"])
    option = next(
        o for o in _typical_customer_card(load_card_options(db, USER, brand))["options"]
        if o["value"] == "连锁口腔机构"
    )
    assert option["desc"] == "管理页写的说明"
