"""Brand profile = the single source of truth for the user profile.

The `ai_telemetry_topics.profile_json` customers fill in at `/workbench/topics/:id/edit`
is now the profile itself, and `sales_agent_seller_profiles` is demoted to a read-only mirror. This file pins four things:

1. **Sync goes one way only** -- when the profile changes the mirror follows; conversely, writing the body from /admin must return 400,
   not silently discard (on discard the admin page shows "saved successfully", the next sync overwrites it, and nobody notices).
2. **Report capability is derived from service keywords** -- brands selling audits get the report entry, brands selling restaurant franchising don't.
   Frontend visibility (`brand_report_capable`) and backend permission (`report_capability_denial`)
   must share a source, otherwise "the button is there, clicking it is rejected".
3. **What's added in chat flows back to the profile itself** -- add only, never remove; team size follows "profile first",
   and once filled in the profile it must not be changed by chat.
4. **Orphan brands aren't crippled** -- existing brands without a claimed profile still work, just flagged + no report entry.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_brand_profile_source.py -q
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentSellerProfileORM
from agents.sales_agent.service import (
    BRAND_NO_PROFILE_HEADLINE,
    SalesAgentError,
    _latest_seller_profile,
    _new_brand_key,
    _next_brand_sort_order,
    account_report_capable,
    archive_brand_for_topic,
    brand_profile_payload,
    brand_report_capable,
    build_seller_profile_summary_card,
    create_or_update_seller_profile,
    derive_report_capability,
    list_brands,
    report_capability_denial,
    seller_profile_for_lead_entry,
    sync_brand_from_topic,
    topic_profile,
    write_agent_updates_to_topic,
)
from geo.database import Base
from geo.models.ai_telemetry import AiTelemetryTopicORM
from geo.models.user import UserORM

USER = 4100001

# A brand selling GEO audits -- service capabilities contain "检测" / "诊断" ("audit" / "diagnosis"), so the report entry should appear.
DETECTION_PROFILE = {
    "company_short_name": "洞见科技",
    "company_full_name": "杭州洞见科技有限公司",
    "core_message": "让品牌在 AI 回答里被优先提及",
    "core_service_overview": "从可见性诊断到内容改造的一站式 GEO 服务。",
    "core_business_lines": ["AI 可见性检测", "GEO 内容改造"],
    "service_features": ["主流大模型品牌认知诊断", "官网结构化数据改造"],
    "target_scenarios": ["有官网但在 AI 搜索里搜不到的企业"],
    "core_credentials": ["服务过 30+ 制造业品牌"],
    "team_size": "11-100 名员工",
    "contact_email": "hi@dongjian.example.com",
}

# A brand selling restaurant franchising -- not a single audit word, so the report entry shouldn't appear.
FRANCHISE_PROFILE = {
    "company_short_name": "百味餐饮",
    "core_message": "十年只做一件事：把小面馆开成连锁",
    "core_service_overview": "选址、供应链、门店培训一条龙。",
    "core_business_lines": ["餐饮加盟咨询", "门店选址"],
    "service_features": ["加盟商招募与培训", "中央厨房供应链搭建"],
    "target_scenarios": ["想开第一家面馆的创业者"],
    "team_size": "1-10 名员工",
    "contact_phone": "0571-66660000",
}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(UserORM(id=USER, email="profile@test.local", password_hash="x", is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _topic(db, profile: dict, name: str = "话题") -> AiTelemetryTopicORM:
    row = AiTelemetryTopicORM(
        user_id=USER,
        name=name,
        target=profile.get("company_short_name") or name,
        profile_json=json.dumps(profile, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return row


def _brand(db, topic: AiTelemetryTopicORM) -> SalesAgentSellerProfileORM:
    row = sync_brand_from_topic(db, topic)
    db.commit()
    return row


def make_brand(db, name: str, user_id: int = USER):
    """Create an orphan brand row (no topic_id).

    Replaces `create_brand`, deleted on 2026-08-27 -- that was the create-brand button on /sales-agent/admin;
    after the page was removed there's no manual brand creation path in production. This is just a fixture for fabricating existing data.
    """
    row = SalesAgentSellerProfileORM(
        user_id=user_id,
        name=name,
        brand_key=_new_brand_key(db, user_id),
        sort_order=_next_brand_sort_order(db, user_id),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def chat_fills_in(db, brand_id: int, **fields):
    """Filling in the profile via chat -- now the only write entry for orphan brands (`_persist_lead_seller_profile_snapshot`
    goes this way). With the admin page gone, this is its only way to regain report capability."""
    return create_or_update_seller_profile(db, USER, seller_profile_id=brand_id, **fields)



# --- 1. Sync direction ---------------------------------------------------------

def test_sync_copies_the_profile_into_the_mirror(db):
    brand = _brand(db, _topic(db, DETECTION_PROFILE))
    assert brand.name == "洞见科技"
    assert "主流大模型品牌认知诊断" in json.loads(brand.service_capabilities_json)
    assert "AI 可见性检测" in json.loads(brand.service_scope_json)
    assert json.loads(brand.typical_customers_json) == ["有官网但在 AI 搜索里搜不到的企业"]
    assert brand.company_size == "11-100 名员工"


def test_sync_is_idempotent_and_never_forks_a_second_brand(db):
    """One sync per profile save. If each created a new row, the brand selection card would grow a string of same-named brands."""
    topic = _topic(db, DETECTION_PROFILE)
    first = _brand(db, topic)
    second = _brand(db, topic)
    assert first.id == second.id
    assert db.query(SalesAgentSellerProfileORM).filter_by(user_id=USER).count() == 1


def test_resaving_the_profile_refreshes_the_mirror(db):
    topic = _topic(db, DETECTION_PROFILE)
    _brand(db, topic)
    profile = topic_profile(topic)
    profile["service_features"] = ["只做内容改造，不做检测"]
    profile["core_business_lines"] = ["GEO 内容改造"]
    topic.profile_json = json.dumps(profile, ensure_ascii=False)
    db.commit()
    brand = _brand(db, topic)
    assert json.loads(brand.service_capabilities_json) == ["只做内容改造，不做检测"]
def test_the_recommendation_snapshot_cannot_overwrite_the_body(db):
    """`create_or_update_seller_profile` overwrites the whole row, and the recommendation flow calls it every round.

    Without blocking, one find-customers run would change the synced service scope / team size to whatever was casually said in chat,
    and /admin is read-only now -- users can see it but can't change it back.
    """
    brand = _brand(db, _topic(db, DETECTION_PROFILE))
    create_or_update_seller_profile(
        db, USER, seller_profile_id=brand.id,
        service_scope=["聊天里随口说的服务范围"],
        service_capabilities=[],                 # old full-overwrite semantics: not passed means written empty
        company_size="1001+ 名员工",
        typical_customers=[],
        proof_points=[],
    )
    db.refresh(brand)
    scope = json.loads(brand.service_scope_json)
    assert "聊天里随口说的服务范围" not in scope
    assert "AI 可见性检测" in scope
    assert brand.company_size == "11-100 名员工"
    assert json.loads(brand.service_capabilities_json) != []
    assert brand.name == "洞见科技"


def test_the_recommendation_snapshot_still_writes_its_own_columns(db):
    """What's blocked is the body, not the whole function -- sales-side columns like buyer roles and report snapshots are still written."""
    brand = _brand(db, _topic(db, DETECTION_PROFILE))
    create_or_update_seller_profile(
        db, USER, seller_profile_id=brand.id,
        default_buyer_roles=["市场负责人"],
        source_report_table="full_diagnosis_reports",
        source_report_id="42",
    )
    db.refresh(brand)
    assert json.loads(brand.default_buyer_roles_json) == ["市场负责人"]
    assert brand.source_report_id == "42"
    assert "市场负责人" in brand.summary          # summary recomputed with roles, no stale value left


def test_orphan_brand_snapshot_still_writes_the_body(db):
    """For brands without a claimed profile, this path is still their only foothold -- blocking it too would cripple them."""
    make_brand(db, "老品牌")
    row = _latest_seller_profile(db, USER)
    create_or_update_seller_profile(
        db, USER, seller_profile_id=row.id, service_capabilities=["官网建设"], company_size="1-10 名员工",
    )
    db.refresh(row)
    assert json.loads(row.service_capabilities_json) == ["官网建设"]
    assert row.company_size == "1-10 名员工"


def test_archiving_the_topic_archives_the_mirror(db):
    topic = _topic(db, DETECTION_PROFILE)
    _brand(db, topic)
    archive_brand_for_topic(db, topic.id, USER, commit=True)
    assert list_brands(db, USER) == []


# --- 2. Report capability ---------------------------------------------------------

def test_report_capability_is_derived_from_the_service_words(db):
    assert derive_report_capability(DETECTION_PROFILE) is True
    assert derive_report_capability(FRANCHISE_PROFILE) is False


def test_geo_tag_metering_without_website_detection_does_not_get_report_entry(db):
    geo_tag_profile = {
        "company_short_name": "GEO-TAG",
        "core_message": "GEO Tag 是GEO领域首个中立计量工具",
        "core_service_overview": "面向品牌方提供全链路 GEO 效果计量和 ROI 对账。",
        "core_business_lines": ["AI 引擎可见性基线测量", "效果计量与 ROI 对账"],
        "service_features": ["第三方中立计量", "跨渠道提及与转化归因"],
        "target_scenarios": ["想看清 AI 搜索转化来源的品牌方"],
    }
    brand = _brand(db, _topic(db, geo_tag_profile))
    assert derive_report_capability(geo_tag_profile) is False
    assert brand.report_capable is False
    assert brand_report_capable(db, USER, brand.id) is False


def test_detection_brand_gets_the_report_entry(db):
    brand = _brand(db, _topic(db, DETECTION_PROFILE))
    assert brand.report_capable is True
    assert report_capability_denial(db, USER, brand.id) == ""
    assert brand_report_capable(db, USER, brand.id) is True


def test_franchise_brand_is_denied_with_a_reason_the_user_can_act_on(db):
    brand = _brand(db, _topic(db, FRANCHISE_PROFILE))
    denial = report_capability_denial(db, USER, brand.id)
    assert "百味餐饮" in denial
    assert "服务核心特点" in denial          # tell them where to fill it in, not just "no"
    assert brand_report_capable(db, USER, brand.id) is False


def test_visibility_and_permission_never_disagree(db):
    """Button visible <=> action allowed. Deciding separately on each side drifts, so `brand_report_capable` is implemented as the negation."""
    detection = _brand(db, _topic(db, DETECTION_PROFILE, name="检测"))
    franchise = _brand(db, _topic(db, FRANCHISE_PROFILE, name="加盟"))
    for brand in (detection, franchise):
        assert brand_report_capable(db, USER, brand.id) == (not report_capability_denial(db, USER, brand.id))


def test_unselected_brand_falls_back_to_the_account_level(db):
    """Being strict for multi-brand accounts before a brand is chosen would block even starting a report -- and that's the most common entry order."""
    _brand(db, _topic(db, DETECTION_PROFILE, name="检测"))
    _brand(db, _topic(db, FRANCHISE_PROFILE, name="加盟"))
    assert account_report_capable(db, USER) is True
    assert brand_report_capable(db, USER, None) is True


def test_account_with_only_a_franchise_brand_is_denied_even_without_a_selection(db):
    _brand(db, _topic(db, FRANCHISE_PROFILE))
    assert account_report_capable(db, USER) is False
    assert brand_report_capable(db, USER, None) is False


# --- 3. Content added in chat flows back to the profile itself -------------------------------------

def test_agent_updates_append_to_the_profile_body(db):
    topic = _topic(db, DETECTION_PROFILE)
    assert write_agent_updates_to_topic(db, topic, capabilities=["竞品对比内容代写"]) is True
    db.commit()
    features = topic_profile(topic)["service_features"]
    assert features[-1] == "竞品对比内容代写"
    assert "主流大模型品牌认知诊断" in features       # add only, never remove


def test_agent_updates_append_typical_customers_to_target_scenarios(db):
    topic = _topic(db, DETECTION_PROFILE)
    before = topic_profile(topic)["target_scenarios"]

    assert write_agent_updates_to_topic(db, topic, typical_customers=["连锁美容机构"]) is True
    db.commit()

    scenarios = topic_profile(topic)["target_scenarios"]
    assert scenarios == [*before, "连锁美容机构"]


def test_agent_updates_never_wipe_the_admin_segment(db):
    """The `_admin` section is ops config, invisible and unchangeable to customers. Writing straight with json.dumps would wipe it entirely."""
    profile = {**DETECTION_PROFILE, "_admin": {"tier": "vip"}}
    topic = _topic(db, profile)
    write_agent_updates_to_topic(db, topic, capabilities=["新能力"])
    db.commit()
    assert json.loads(topic.profile_json)["_admin"] == {"tier": "vip"}


def test_agent_updates_leave_a_changelog_entry(db):
    topic = _topic(db, DETECTION_PROFILE)
    write_agent_updates_to_topic(db, topic, capabilities=["新能力"], actor_id=USER)
    db.commit()
    entry = json.loads(topic.topic_changelog_json)[-1]
    assert entry["actor_role"] == "agent"


def test_team_size_only_fills_a_blank_never_overwrites(db):
    """One chat question changing the option the customer carefully picked in the form gets the direction backwards."""
    topic = _topic(db, DETECTION_PROFILE)          # profile already has 11-100
    assert write_agent_updates_to_topic(db, topic, company_size="1001+ 名员工") is False
    assert topic_profile(topic)["team_size"] == "11-100 名员工"

    blank = _topic(db, {k: v for k, v in DETECTION_PROFILE.items() if k != "team_size"}, name="没填规模")
    assert write_agent_updates_to_topic(db, blank, company_size="1001+ 名员工") is True
    assert topic_profile(blank)["team_size"] == "1001+ 名员工"


def test_write_back_is_a_noop_when_nothing_is_new(db):
    topic = _topic(db, DETECTION_PROFILE)
    assert write_agent_updates_to_topic(db, topic, capabilities=["主流大模型品牌认知诊断"]) is False


# --- 4. Orphan brands ---------------------------------------------------------

def test_orphan_brand_stays_usable_and_is_marked(db):
    """Existing brands that can't be matched to a topic are kept + flagged, not auto-deleted."""
    make_brand(db, "老品牌")
    brand = list_brands(db, USER)[0]
    assert brand["has_profile"] is False
    assert brand["topic_id"] is None
    assert brand["report_capable"] is False


def test_orphan_brand_keeps_its_hand_filled_headline(db):
    """`headline` only joins content. Putting 「未关联品牌资料」 ("no linked brand profile") in it would push out the items hand-filled in /admin."""
    make_brand(db, "老品牌")
    row = _latest_seller_profile(db, USER)
    chat_fills_in(db, row.id, service_capabilities=["官网建设", "老站改版"], company_size="11-100 名员工")
    brand = list_brands(db, USER)[0]
    assert BRAND_NO_PROFILE_HEADLINE not in brand["headline"]
    assert "官网建设" in brand["headline"]


def test_orphan_brand_card_says_where_the_content_lives(db):
    make_brand(db, "老品牌")
    payload = seller_profile_for_lead_entry(db, USER)
    card = build_seller_profile_summary_card(payload)
    assert BRAND_NO_PROFILE_HEADLINE in card["data"]["title"]
    assert card["data"]["has_profile"] is False


def test_profile_backed_card_points_missing_fields_at_the_upload_form(db):
    """When content is missing, steer people to chat; what they add lands on the mirror while the profile form stays empty."""
    sparse = {"company_short_name": "半份资料", "service_features": ["只有能力这一项"]}
    _brand(db, _topic(db, sparse))
    payload = seller_profile_for_lead_entry(db, USER)
    card = build_seller_profile_summary_card(payload)
    assert payload["has_profile"] is True
    assert BRAND_NO_PROFILE_HEADLINE not in card["data"]["title"]
    assert "客户工作台" in card["data"]["summary_text"]


# --- 5. Email material ---------------------------------------------------------

def test_email_material_comes_from_the_uploaded_profile(db):
    brand = _brand(db, _topic(db, FRANCHISE_PROFILE))
    material = brand_profile_payload(db, USER, brand.id)["email_material"]
    assert material["brand_name"] == "百味餐饮"
    assert material["core_message"] == "十年只做一件事：把小面馆开成连锁"
    # A brand selling franchising shouldn't have anything about GEO/audits in its material
    assert "检测" not in json.dumps(material, ensure_ascii=False)


def test_contacts_are_a_whitelist_not_a_suggestion(db):
    """Contact info not filled in the profile must not appear in the material -- a phone number the model improvised is fabricated."""
    franchise = _brand(db, _topic(db, FRANCHISE_PROFILE))
    material = brand_profile_payload(db, USER, franchise.id)["email_material"]
    assert material["contacts"] == {"phone": "0571-66660000"}   # only the phone was filled in, so only the phone
    assert material["has_contacts"] is True

    bare = _brand(db, _topic(db, {
        "company_short_name": "光杆品牌", "service_features": ["有能力但没留联系方式"],
    }, name="没联系方式"))
    bare_material = brand_profile_payload(db, USER, bare.id)["email_material"]
    assert bare_material["contacts"] == {}
    assert bare_material["has_contacts"] is False


# --- 6. Half-empty profiles shouldn't become brands -------------------------------------------

def test_an_empty_topic_does_not_become_a_brand(db):
    """If every topic in the workbench became an empty brand, the brand selection card would be flooded with 「默认品牌」 ("default brand")."""
    assert sync_brand_from_topic(db, _topic(db, {}, name="刚建的空话题")) is None
    assert sync_brand_from_topic(db, _topic(db, {"company_short_name": "只填了名字"})) is None
    assert list_brands(db, USER) == []


def test_a_topic_becomes_a_brand_as_soon_as_it_has_a_name_and_a_service(db):
    """The bar is low: a name plus one service is enough, otherwise users would think the profile wasn't saved."""
    topic = _topic(db, {"company_short_name": "刚够格", "service_features": ["就一项能力"]})
    assert sync_brand_from_topic(db, topic) is not None


def test_a_half_emptied_profile_does_not_stop_syncing_an_existing_brand(db):
    """Only new creation is blocked. A customer editing the profile back to half-empty is "editing", not "withdrawing" --
    a brand in use suddenly stopping syncing is much harder to debug than an extra brand."""
    topic = _topic(db, DETECTION_PROFILE)
    brand = _brand(db, topic)
    topic.profile_json = json.dumps({"company_short_name": "改到一半"}, ensure_ascii=False)
    db.commit()
    assert sync_brand_from_topic(db, topic) is not None
    db.refresh(brand)
    assert brand.name == "改到一半"


# --- 7. Orphan brands still work -------------------------------------------------

def test_an_orphan_brand_can_still_write_an_email(db):
    """The decision is "keep + flag, don't auto-delete", which implies they must still work.

    Without this fallback, orphan brands' email material is all empty, and `build_no_report_default_email_copy`
    raises "no brand profile" -- manually added brands couldn't send a single email.
    """
    make_brand(db, "手动加的品牌")
    row = _latest_seller_profile(db, USER)
    chat_fills_in(db, row.id, service_capabilities=["官网建设", "老站改版"])
    payload = brand_profile_payload(db, USER, row.id)
    assert payload["has_profile"] is False              # still flagged as having no profile
    assert payload["email_material"]["brand_name"] == "手动加的品牌"
    assert "官网建设" in payload["email_material"]["service_features"]


def test_an_orphan_brand_can_earn_the_report_entry_from_chat(db):
    """`report_capable` is written only by the syncer across the repo, and the syncer only touches rows with a claimed profile --
    orphan brands are always False, the report button never appears again, and the UI has no remedy entry.

    With /sales-agent/admin removed, adding capabilities in chat is its only way out."""
    make_brand(db, "手动加的品牌")
    row = _latest_seller_profile(db, USER)
    assert brand_report_capable(db, USER, row.id) is False

    chat_fills_in(db, row.id, service_capabilities=["AI 可见性检测"])
    db.refresh(row)
    assert row.report_capable is True
    assert brand_report_capable(db, USER, row.id) is True


def test_an_orphan_brand_loses_the_report_entry_when_the_capability_goes_away(db):
    """Recomputation goes both ways -- if it could only add, one mistake could never be undone."""
    make_brand(db, "手动加的品牌")
    row = _latest_seller_profile(db, USER)
    chat_fills_in(db, row.id, service_capabilities=["AI 可见性检测"])
    chat_fills_in(db, row.id, service_capabilities=["餐饮加盟咨询"])
    db.refresh(row)
    assert row.report_capable is False


def test_chat_never_touches_a_profile_backed_brands_capability(db):
    """Claimed rows are computed by the syncer. Recomputing here would let chat bypass the profile to change report capability --
    the customer profile says "content rework only, no audits", and a casual mention of 「可见性检测」 ("visibility audit") in chat
    would light up the report button."""
    brand = _brand(db, _topic(db, DETECTION_PROFILE))
    chat_fills_in(db, brand.id, service_capabilities=["餐饮加盟咨询"])
    db.refresh(brand)
    assert brand.report_capable is True
