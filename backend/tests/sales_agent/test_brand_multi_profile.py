"""Multiple brands per account -- "A sells, B sells too, with different profiles".

Before the change `sales_agent_seller_profiles` was an account-level singleton: every read path was
`order_by(id.desc()).first()`, **newest row wins**. After filling in B's capabilities, A's were buried underneath
and could never be read again; the find-customers flow's first step asked about capabilities directly, never "which one are you selling this time".

Now one row = one brand. With >= 2 active brands the flow's first step shows a brand selection card, and the choice is written into
`context_json` -> not asked again later in the same session. **With only one brand the whole branch is skipped**, zero change for existing accounts --
the most important assertion in this file (`test_single_brand_*`); if it breaks, it's a full regression.

Also pins the root cause of brands growing on their own: `create_or_update_seller_profile` used to
`db.add` a row whenever `seller_profile_id is None`, and the three callers couldn't pass an id anyway,
so every profile edit added a row. With multiple brands those rows would be listed as real brands.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_brand_multi_profile.py -q
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.api import (
    _append_lead_seller_capabilities_guidance,
    _append_lead_typical_customers_guidance,
    _append_seller_profile_summary_card,
    _brand_slot,
    _is_new_brand_action,
    _persist_lead_seller_profile_snapshot,
    _run_deterministic_flow,
    _slot_from_step,
)
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.flow.router import BRAND, READY, next_step
from agents.sales_agent.flow.state import LeadState
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentSellerProfileORM,
)
from agents.sales_agent.service import (
    CAPABILITY,
    SalesAgentError,
    _guidance_selected_values_context,
    _latest_seller_profile,
    _new_brand_key,
    _next_brand_sort_order,
    _profile_for_recommendation,
    active_brand_count,
    add_custom_options,
    append_account_icp_preferences,
    build_brand_picker_card,
    create_or_update_seller_profile,
    get_current_customer_profile,
    list_brands,
    outreach_brand_settings,
    report_email_fingerprint,
    resolve_active_brand_id,
    serialize_brand,
)
from agents.sales_agent.tools import _active_brand_id
from geo.database import Base
from geo.models.user import UserORM

USER = 3000001
OTHER_USER = 3000002

# `create_brand` / `update_brand` / `archive_brand` were deleted on 2026-08-27 along with
# /sales-agent/admin -- brands are no longer added or edited by hand; they're synced from brand profiles.
# The three below are **test fixtures**, not code under test: this file tests "how multiple brands are read and
# isolated", and none of that behavior changed; only the way data is fabricated changed to writing rows directly.

def make_brand(db, user_id: int, name: str) -> dict:
    """Create an orphan brand row (no topic_id) and return the serialized result; used like the original `create_brand`."""
    row = SalesAgentSellerProfileORM(
        user_id=user_id,
        name=name,
        brand_key=_new_brand_key(db, user_id),
        sort_order=_next_brand_sort_order(db, user_id),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_brand(row) or {}


def set_body(db, brand_id: int, **fields) -> None:
    """Write the profile body directly into the mirror row. This used to be done by the admin page's `update_brand`."""
    row = db.get(SalesAgentSellerProfileORM, brand_id)
    mapping = {
        "service_capabilities": "service_capabilities_json",
        "service_scope": "service_scope_json",
        "typical_customers": "typical_customers_json",
        "proof_points": "proof_points_json",
    }
    for key, column in mapping.items():
        if key in fields:
            setattr(row, column, json.dumps(fields[key], ensure_ascii=False))
    if "company_size" in fields:
        row.company_size = fields["company_size"]
    row.summary = ""  # make serialization recompute from the new body
    db.commit()


def archive_row(db, brand_id: int) -> None:
    """Soft delete. Used to go through `archive_brand`; now only `archive_brand_for_topic` writes it in production."""
    from datetime import datetime as _dt
    row = db.get(SalesAgentSellerProfileORM, brand_id)
    row.archived_at = _dt.utcnow()
    db.commit()



@pytest.fixture()
def deps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="brand@test.local", password_hash="x", is_active=True))
    db.add(UserORM(id=OTHER_USER, email="brand2@test.local", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id="brand-1", title="Sales Agent 会话")
    db.add(session)
    db.commit()
    d = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id, session_public_id="brand-1")
    try:
        yield d
    finally:
        db.close()


def _run(deps, message: str, intent: str = "lead") -> bool:
    return asyncio.run(_run_deterministic_flow(deps, message, intent))


def _cards(deps) -> list[dict]:
    """tool_cards carried by all assistant messages in the session, in chronological order."""
    out: list[dict] = []
    rows = (
        deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.role == "assistant")
        .order_by(SalesAgentChatMessageORM.id.asc()).all()
    )
    for row in rows:
        try:
            meta = json.loads(row.metadata_json or "{}")
        except ValueError:
            continue
        out.extend(meta.get("tool_cards") or [])
    return out


def _card_ids(deps) -> list[str]:
    return [
        str((card.get("data") or {}).get("question_card", {}).get("id") or "")
        for card in _cards(deps)
    ]


def _flow(deps) -> dict:
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    try:
        ctx = json.loads(row.context_json or "{}") or {}
        return ((ctx.get("tool_state") or {}).get("lead_flow")) or {}
    except ValueError:
        return {}


def _capabilities(deps, brand_id: int) -> list[str]:
    row = deps.db.get(SalesAgentSellerProfileORM, brand_id)
    return json.loads(row.service_capabilities_json or "[]") if row else []


# --- Zero regression: single-brand accounts unchanged -------------------------------------------

def test_single_brand_never_shows_the_picker(deps):
    """Only one brand -> the first step is still the profile/capability card; not a single word should change."""
    make_brand(deps.db, USER, "唯一品牌")

    assert _run(deps, "找客户") is True

    ids = _card_ids(deps)
    assert "sales_agent_brand_picker" not in ids, f"单品牌账号不该出品牌卡:{ids}"
    assert "brand_id" not in _flow(deps)


def test_zero_brand_account_still_works(deps):
    """No brand at all (brand-new account) must not get stuck either -- go straight to the capability card; the flow creates the first row along the way."""
    assert _run(deps, "找客户") is True
    assert "sales_agent_brand_picker" not in _card_ids(deps)


def test_router_skips_brand_unless_required():
    """`brand_required` is injected by the caller. Not injected -> the router never returns BRAND."""
    state = LeadState(brand_required=False)
    assert next_step(state) == "seller_capabilities"

    state.brand_required = True
    assert next_step(state) == BRAND

    state.brand_id = 7
    assert next_step(state) == "seller_capabilities"


def test_router_brand_precedes_every_slot():
    """Even with all slots filled the brand comes first -- the brand is a precondition, not a slot."""
    full = LeadState(
        seller_capabilities=["AI-GEO优化"],
        service_scope=["AI 可见性检测"],
        typical_customers=["B2B 企业"],
        target_company_size="11-100 名员工",
        target_industries=["制造业"],
        target_regions=["华东"],
    )
    assert next_step(full) == READY

    full.brand_required = True
    assert next_step(full) == BRAND


# --- Multiple brands: show the card, select, remember --------------------------------------------

def test_two_brands_ask_first(deps):
    make_brand(deps.db, USER, "A 品牌")
    make_brand(deps.db, USER, "B 品牌")

    assert _run(deps, "找客户") is True

    assert _card_ids(deps) == ["sales_agent_brand_picker"]
    assert _flow(deps).get("step") == BRAND


def test_picker_card_lists_only_existing_brands(deps):
    """The card has only existing brands, **no** 「新增品牌」 ("add brand") -- creating brands is a configuration action, only done in /sales-agent/admin."""
    make_brand(deps.db, USER, "A 品牌")
    make_brand(deps.db, USER, "B 品牌")

    card = build_brand_picker_card(list_brands(deps.db, USER))
    options = [o["value"] for o in card["data"]["question_card"]["options"]]

    assert options == ["A 品牌", "B 品牌"]
    assert "新增品牌" not in options
    # Uses the existing guidance channel; the frontend renderer needs no change
    assert card["tool"] == "recommend_companies"


def test_pick_a_brand_is_remembered_for_the_session(deps):
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    make_brand(deps.db, USER, "B 品牌")

    _run(deps, "找客户")
    assert _run(deps, "A 品牌") is True

    flow = _flow(deps)
    assert flow.get("brand_id") == a, "选中的品牌要落进 context_json"
    assert resolve_active_brand_id(deps.db, USER, flow) == a

    # The second turn shouldn't ask again
    before = _card_ids(deps).count("sales_agent_brand_picker")
    _run(deps, "找客户")
    assert _card_ids(deps).count("sales_agent_brand_picker") == before


def test_chat_never_creates_a_brand(deps):
    """Typing 「新增品牌」 ("add brand") in chat neither creates a brand nor asks for a name -- the selection card is shown again as-is.

    Creating brands happens only in /sales-agent/admin. In chat, one slip would add an empty business line,
    and when the sentence is handed back to the model the brand isn't decided yet, so the profile in the model's hands is from an arbitrary brand.
    """
    make_brand(deps.db, USER, "A 品牌")
    make_brand(deps.db, USER, "B 品牌")

    _run(deps, "找客户")
    assert _run(deps, "新增品牌") is True
    assert _card_ids(deps)[-1] == "sales_agent_brand_picker", "认不出品牌就再问一次,不追问名字"
    assert _flow(deps).get("step") == BRAND

    # Following up with a name is still just "an unrecognized brand name" and must not become a new brand
    _run(deps, "口腔医疗")
    assert active_brand_count(deps.db, USER) == 2, "聊天流程不许建品牌"
    assert [b["name"] for b in list_brands(deps.db, USER)] == ["A 品牌", "B 品牌"]


# --- Brand name matching: better ask again than guess wrong ----------------------------------

def test_brand_slot_matches_exact_name():
    brands = [{"id": 1, "name": "A 品牌"}, {"id": 2, "name": "B 品牌"}]
    assert _brand_slot("A 品牌", brands) == {"brand_id": 1}
    assert _slot_from_step(BRAND, "B 品牌", brands) == {"brand_id": 2}


def test_brand_slot_accepts_a_single_loose_hit():
    brands = [{"id": 1, "name": "口腔医疗"}, {"id": 2, "name": "跨境 SaaS"}]
    assert _brand_slot("这次推口腔医疗那条线", brands) == {"brand_id": 1}


def test_brand_slot_refuses_when_ambiguous():
    """If both match, treat it as unrecognized -- picking the wrong brand makes the whole round of recommendations and the emails sent later all wrong."""
    brands = [{"id": 1, "name": "医疗"}, {"id": 2, "name": "医疗器械"}]
    assert _brand_slot("医疗器械", brands) == {"brand_id": 2}, "精确匹配优先"
    assert _brand_slot("我们做医疗和医疗器械都行", brands) == {}


def test_brand_slot_ignores_the_new_brand_action():
    brands = [{"id": 1, "name": "A 品牌"}]
    assert _brand_slot("新增品牌", brands) == {}
    assert _is_new_brand_action("+ 新增品牌") is True
    assert _is_new_brand_action("A 品牌") is False


# --- A's and B's profiles don't overwrite each other -----------------------------------------------

def test_two_brands_keep_separate_profiles(deps):
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    b = make_brand(deps.db, USER, "B 品牌")["id"]

    create_or_update_seller_profile(deps.db, USER, seller_profile_id=a, service_capabilities=["AI-GEO优化"])
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=b, service_capabilities=["官网建设/优化"])

    assert _capabilities(deps, a) == ["AI-GEO优化"]
    assert _capabilities(deps, b) == ["官网建设/优化"], "写 B 不该动 A —— 改造前这里两行会互相压掉"


def _pick(deps, brand_id: int) -> None:
    """Write "which brand to use this time" into the session. Passing 0 = not selected, used to test the multi-brand unselected branch."""
    flow = {"brand_id": brand_id} if brand_id else {}
    deps.tool_state["lead_flow"] = flow
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    row.context_json = json.dumps({"tool_state": {"lead_flow": flow}}, ensure_ascii=False)
    deps.db.commit()


def _last_question_card(deps) -> dict:
    return (_cards(deps)[-1].get("data") or {}).get("question_card") or {}


def test_update_profile_card_shows_this_brands_own_content(deps):
    """When clicking 「更新画像」 ("update profile"), the capability options on the card must come from the row **this brand** saved in /admin.

    This card used to read only the option catalog, not a single field of sales_agent_seller_profiles.
    The catalog is mostly system preset rows (both owner columns NULL), and brand-private rows had only one source,
    "the user typed a custom value in chat" -- content filled in /admin never entered the catalog at all.
    So for multi-brand accounts clicking "update profile", A and B got the same preset list, word for word, and the items the brand itself
    filled in weren't on the card at all; they had to be typed again by hand (production 2026-08-27: the update profile cards of Vigilath and
    GEO-TAG were identical).
    """
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    b = make_brand(deps.db, USER, "B 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=["AI引擎可见性基线测量", "效果计量与ROI对账"], company_size="101-500 名员工",
        # All four filled -- this is "a brand configured in /admin". Missing one makes it an incomplete orphan profile,
        # which `_needs_business_hint_first` intercepts to ask for business direction first, never reaching the update profile card.
        service_scope=["AI 可见性检测"], typical_customers=["有官网但搜索获客一般的企业"],
    )
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=b,
        # Deliberately pick a value **not in the system preset table**: preset items are on both brands' cards anyway,
        # and using one as the probe would make this assertion always false, unable to detect "B's content leaking onto A's card".
        service_capabilities=["跨境电商代运营"], company_size="1001+ 名员工",
    )
    _pick(deps, a)

    _append_lead_seller_capabilities_guidance(deps, "lead")

    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert values[:2] == ["AI引擎可见性基线测量", "效果计量与ROI对账"], "库里那份要排在系统预置前面"
    assert "跨境电商代运营" not in values, "B 的能力不该出现在 A 的卡上"


def test_update_profile_card_hides_system_presets_when_the_brand_has_its_own(deps):
    """When the brand's own set exists, system presets are hidden.

    The 7 presets are generic words shared platform-wide and are noise for well-positioned brands: GEO-TAG only does performance measurement,
    yet the card showed 「SEO技术团队」 ("SEO technical team"); ticking it writes into service_capabilities_json -- the profile body,
    recommendation definitions and email definitions all change with it (user 2026-08-27: "all of them still show up").
    To add generic capabilities use the card's own `allow_custom_option` input box; it no longer takes an option slot.
    """
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=["AI引擎可见性基线测量"], company_size="101-500 名员工",
        service_scope=["AI 可见性检测"], typical_customers=["有官网但搜索获客一般的企业"],
    )
    _pick(deps, a)

    _append_lead_seller_capabilities_guidance(deps, "lead")

    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert values == ["AI引擎可见性基线测量"]
    assert card["allow_custom_option"] is True


def test_update_profile_card_keeps_this_brands_custom_options(deps):
    """Only system presets are hidden. Custom items the brand typed in chat (source=custom) must stay."""
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=["AI引擎可见性基线测量"], company_size="101-500 名员工",
        service_scope=["AI 可见性检测"], typical_customers=["有官网但搜索获客一般的企业"],
    )
    add_custom_options(deps.db, CAPABILITY, USER, a, ["AI答案纠偏"])
    _pick(deps, a)

    _append_lead_seller_capabilities_guidance(deps, "lead")

    values = [str(o.get("value") or "") for o in _last_question_card(deps).get("options") or []]
    assert values == ["AI引擎可见性基线测量", "AI答案纠偏"]


def test_update_profile_card_defaults_to_the_saved_company_size(deps):
    """The size default must come from the DB too.

    Otherwise the card always shows the literal 「11-100 名员工」 ("11-100 employees"), and when the user clicks continue the 101-500
    saved in /admin is gone -- in this flow the card default equals "the value the user chose".
    """
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=["AI-GEO优化"], company_size="101-500 名员工",
        service_scope=["AI 可见性检测"], typical_customers=["有官网但搜索获客一般的企业"],
    )
    _pick(deps, a)

    _append_lead_seller_capabilities_guidance(deps, "lead")

    assert _last_question_card(deps)["secondary_default_value"] == "101-500 名员工"


def test_update_profile_card_does_not_fall_back_to_service_scope(deps):
    """When professional capabilities are empty, keep asking about them; don't pass off service directions as capability options."""
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=[], service_scope=["结构化数据部署", "月度报告与优化迭代"],
        company_size="101-500 名员工",
    )
    _pick(deps, a)

    _append_lead_seller_capabilities_guidance(deps, "lead")

    values = [str(o.get("value") or "") for o in _last_question_card(deps).get("options") or []]
    assert "结构化数据部署" not in values
    assert "月度报告与优化迭代" not in values


def test_unselected_brand_leaks_nothing_into_the_card(deps):
    """Multiple brands + not yet selected = nothing on the card may come from any specific brand.

    `_active_seller_profile_id` returns None here, and every downstream fallback is
    `_latest_seller_profile(db, user_id)` = the row with the largest id. Consequence before the fix: whichever brand the user wanted,
    the card showed the capabilities of the most recently created one (production 2026-08-27: the update profile cards of both brands
    were identical).
    """
    make_brand(deps.db, USER, "A 品牌")
    b = make_brand(deps.db, USER, "B 品牌")["id"]  # larger id -- always won before the fix
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=b,
        service_capabilities=["跨境电商代运营"], company_size="1001+ 名员工",
    )
    _pick(deps, 0)  # two active brands, no brand_id in the flow

    _append_lead_seller_capabilities_guidance(deps, "lead")

    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert "跨境电商代运营" not in values, "没选品牌时不该泄露 id 最大那个品牌的能力"
    assert card["secondary_default_value"] == "11-100 名员工", "规模同理,不能拿 B 的 1001+ 当默认值"


def test_typical_customers_card_is_per_brand(deps):
    """The suitable-customers card also goes by brand: each brand's own set ranks first."""
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    b = make_brand(deps.db, USER, "B 品牌")["id"]
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=a, typical_customers=["口腔连锁"])
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=b, typical_customers=["跨境卖家"])

    _pick(deps, a)
    _append_lead_typical_customers_guidance(deps, "lead")
    values_a = [str(o.get("value") or "") for o in _last_question_card(deps).get("options") or []]

    _pick(deps, b)
    _append_lead_typical_customers_guidance(deps, "lead")
    values_b = [str(o.get("value") or "") for o in _last_question_card(deps).get("options") or []]

    assert values_a[0] == "口腔连锁" and "跨境卖家" not in values_a
    assert values_b[0] == "跨境卖家" and "口腔连锁" not in values_b


def test_unselected_brand_leaks_nothing_into_the_typical_customers_card(deps):
    """da38e54e changed three places -- capability card / snapshot / profile summary card -- and missed this one.

    Measured before the fix: with neither brand selected, this card always showed the customer types of the brand with the largest id.
    """
    make_brand(deps.db, USER, "A 品牌")
    b = make_brand(deps.db, USER, "B 品牌")["id"]  # larger id -- always won before the fix
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=b, typical_customers=["跨境卖家"])
    _pick(deps, 0)

    _append_lead_typical_customers_guidance(deps, "lead")

    values = [str(o.get("value") or "") for o in _last_question_card(deps).get("options") or []]
    assert "跨境卖家" not in values, "没选品牌时不该泄露 id 最大那个品牌的客户类型"


def test_unselected_brand_does_not_get_a_summary_card(deps):
    """The profile confirm card must not appear while the brand is undecided -- it would prefill the flow from a guessed brand.

    After prefilling, the router sees filled slots and skips ahead, and the user never gets a chance to correct it; the card title shows a brand name,
    while the body is another business line's content. Return False to hand back to the router; the next step is the brand selection card.
    """
    make_brand(deps.db, USER, "A 品牌")
    b = make_brand(deps.db, USER, "B 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=b,
        service_capabilities=["跨境电商代运营"], company_size="1001+ 名员工",
    )
    _pick(deps, 0)

    assert _append_seller_profile_summary_card(deps, "lead") is False
    assert _flow(deps).get("seller_capabilities") is None, "更不该把 B 的能力预填进 flow"


def test_unselected_brand_snapshot_does_not_overwrite_another_brand(deps):
    """Don't persist while the brand is undecided -- `create_or_update_seller_profile` overwrites the whole row.

    Before the fix: brand_id is None -> the writer falls back to the largest-id row -> the capabilities collected this round wipe out
    the whole row B filled in /admin. Better not persist this round; once the brand is chosen the router comes back here.
    """
    make_brand(deps.db, USER, "A 品牌")
    b = make_brand(deps.db, USER, "B 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=b,
        service_capabilities=["跨境电商代运营"], company_size="1001+ 名员工",
    )
    _pick(deps, 0)

    _persist_lead_seller_profile_snapshot(deps, ["AI-GEO优化"], "11-100 名员工")

    assert _capabilities(deps, b) == ["跨境电商代运营"], "B 那一行必须原封不动"


def test_single_brand_snapshot_still_persists(deps):
    """Zero regression: with only one brand there's no ambiguity, and the snapshot persists as before."""
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    _pick(deps, 0)  # single brand needs no selection; the flow never has a brand_id

    _persist_lead_seller_profile_snapshot(deps, ["AI-GEO优化"], "11-100 名员工")

    assert _capabilities(deps, a) == ["AI-GEO优化"]


def test_chat_added_capability_appends_to_the_brand_row(deps):
    """Capabilities added in chat should go into the /admin row, not replace what's there.

    Before the fix it was `capabilities or existing` -- replacing the whole column. So adding one item in chat
    wiped out the existing items in /admin (user 2026-08-27: "things added in chat should show up here too").
    """
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=["AI引擎可见性基线测量"], company_size="1-10 名员工",
    )
    _pick(deps, a)

    _persist_lead_seller_profile_snapshot(deps, ["跨境电商代运营"], "1-10 名员工")

    assert _capabilities(deps, a) == ["AI引擎可见性基线测量", "跨境电商代运营"]


def test_chat_answer_does_not_drop_unmentioned_capabilities(deps):
    """Not ticked this round doesn't mean delete.

    When a new session opens the card, `selected_values` comes only from this lead_flow, so none of the DB items are ticked.
    The user sees 3 items, casually ticks 1, and before the fix /admin was left with just that 1. Deleting capabilities only happens in /admin.
    """
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=["AI-GEO优化", "SEO技术团队", "内容营销能力"],
        company_size="11-100 名员工",
    )
    _pick(deps, a)

    _persist_lead_seller_profile_snapshot(deps, ["SEO技术团队"], "11-100 名员工")

    assert _capabilities(deps, a) == ["AI-GEO优化", "SEO技术团队", "内容营销能力"]


def test_empty_profile_first_asks_business_hint_without_fixed_presets(deps):
    """Empty brand profiles no longer show hardcoded fallback capabilities; ask the user to confirm the business direction first."""
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    _pick(deps, a)

    _append_lead_seller_capabilities_guidance(deps, "lead")

    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert card["id"] == "seller_business_hint"
    assert card["field"] == "business_hint"
    assert card.get("allow_custom_option") is True
    assert "法律服务" in values
    assert "AI-GEO优化" not in values


def test_model_context_follows_the_selected_brand(deps):
    """The profile fed to the model must be **the brand selected in the session**, not the one with the largest id.

    This context is exactly what the model uses to invent options, and `catalog_or` lets >= 3 model-invented options
    override the catalog outright. The consequence of the wrong brand isn't "a few fewer options"; the whole card's options and copy switch to
    another business line's definitions -- the user chose A and the card shows all of B's service capabilities
    (production 2026-08-26: chose Vigilath, the profile card had GEO-TAG's capabilities).

    What's pinned is the **brand resolution** step; which slot is used as the probe doesn't matter. Size is used rather than capabilities/suitable customers:
    those two have echo turned off via `_ECHO_DISABLED_SLOTS` and no longer appear in the return value (see
    `test_capabilities_and_typical_customers_are_not_echoed`).
    """
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    b = make_brand(deps.db, USER, "B 品牌")["id"]  # larger id -- always won before the change
    assert b > a
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=a,
        service_capabilities=["AI-GEO优化"], typical_customers=["口腔诊所"], company_size="11-100 名员工",
    )
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=b,
        service_capabilities=["官网建设/优化"], typical_customers=["跨境电商"], company_size="1001+ 名员工",
    )
    session = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    session.context_json = json.dumps({"tool_state": {"lead_flow": {"brand_id": a}}})
    deps.db.commit()

    selected = _guidance_selected_values_context(deps.db, USER, session)
    assert selected["seller_team"] == "11-100 名员工", "取成 id 最大的 B 品牌了"


def test_model_context_rejects_a_foreign_brand_id(deps):
    """context_json can be tampered with; the brand_id in it must not be used directly to query the DB."""
    mine = make_brand(deps.db, USER, "我的品牌")["id"]
    theirs = make_brand(deps.db, OTHER_USER, "别人的品牌")["id"]
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=mine, company_size="11-100 名员工")
    create_or_update_seller_profile(deps.db, OTHER_USER, seller_profile_id=theirs, company_size="1001+ 名员工")
    session = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    session.context_json = json.dumps({"tool_state": {"lead_flow": {"brand_id": theirs}}})
    deps.db.commit()

    selected = _guidance_selected_values_context(deps.db, USER, session)
    assert selected.get("seller_team") == "11-100 名员工", "越权的 id 要被丢掉,落回自己唯一的品牌"


def test_capabilities_and_typical_customers_are_not_echoed(deps):
    """The two cards 「我方能力」 ("our capabilities") and 「适合服务的客户类型」 ("suitable customer types") don't echo for now -- values follow the /admin table.

    Why off: these two are now prefilled into lead_flow by the profile card and the router skips them, so on the normal path the card
    never appears; if it does appear, /admin is empty, and with nothing there pre-ticking has nothing to tick. With echo on,
    the card pre-ticks the DB values, and a casual change of ticks by the user writes back and overwrites what the admin page filled in.

    Both layers must be blocked: the direct DB read layer, and the lead_flow layer on top (the profile card just wrote values into the flow).
    """
    brand_id = make_brand(deps.db, USER, "唯一品牌")["id"]
    create_or_update_seller_profile(
        deps.db, USER, seller_profile_id=brand_id,
        service_capabilities=["AI-GEO优化"], typical_customers=["口腔诊所"], company_size="11-100 名员工",
    )
    session = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    session.context_json = json.dumps({"tool_state": {"lead_flow": {
        # The set prefilled by the profile card -- echoing it back through this path isn't allowed either
        "seller_capabilities": ["AI-GEO优化"],
        "typical_customers": ["口腔诊所"],
    }}}, ensure_ascii=False)
    deps.db.commit()

    selected = _guidance_selected_values_context(deps.db, USER, session)
    assert "seller_capabilities" not in selected, "能力还在回显 —— 库里直读那层没挡住"
    assert "typical_customers" not in selected, "适合客户还在回显 —— lead_flow 那层没挡住"
    assert selected["seller_team"] == "11-100 名员工", "只关这两项,别把规模一起关了"


def test_profile_write_does_not_grow_phantom_brands(deps):
    """Without a named id, write to the current active brand's row, **not a new row**.

    This is the root cause of one user having several profile rows in existing DBs: the three callers (per-round profile snapshots in the find-customers flow)
    couldn't pass an id, and the original implementation just `db.add`ed them all. Without fixing this, the migration's archival backfill is undone within days.
    """
    make_brand(deps.db, USER, "唯一品牌")
    for _ in range(3):
        create_or_update_seller_profile(deps.db, USER, service_capabilities=["AI-GEO优化"])

    assert active_brand_count(deps.db, USER) == 1
    assert deps.db.query(SalesAgentSellerProfileORM).filter_by(user_id=USER).count() == 1


def test_profile_write_does_not_rename_the_brand(deps):
    """Profile snapshots don't pass name; a hardcoded default would overwrite the user's chosen name back to 「默认品牌」 ("default brand") every round."""
    brand_id = make_brand(deps.db, USER, "口腔医疗")["id"]
    create_or_update_seller_profile(deps.db, USER, service_capabilities=["AI-GEO优化"])

    assert deps.db.get(SalesAgentSellerProfileORM, brand_id).name == "口腔医疗"


def test_explicit_create_new_still_adds_a_row(deps):
    make_brand(deps.db, USER, "A 品牌")
    create_or_update_seller_profile(deps.db, USER, create_new=True, service_capabilities=["官网建设/优化"])

    assert active_brand_count(deps.db, USER) == 2


# --- "What are my suitable customer types" must also go by brand ---------------------------------
#
# What's pinned above is the **card** path (`_guidance_selected_values_context`). A user asking directly
# "what are my suitable customer types" takes a different path: prompts.py forces the model to call
# `get_current_customer_profile`. That tool originally accepted no brand parameter at all,
# so the service layer kept "taking the newest row" -- user test 2026-08-26: two brands shared one brand's customer types.

def test_customer_profile_read_follows_the_selected_brand(deps):
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    b = make_brand(deps.db, USER, "B 品牌")["id"]  # larger id -- always wins without naming
    assert b > a
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=a, typical_customers=["口腔诊所"])
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=b, typical_customers=["跨境电商"])

    payload = get_current_customer_profile(deps.db, USER, seller_profile_id=a)

    assert payload["seller_profile"]["id"] == a
    assert payload["seller_profile"]["typical_customers"] == ["口腔诊所"]


def test_customer_profile_read_without_a_brand_is_unchanged(deps):
    """Single-brand accounts not passing an id -> still read that one row; existing behavior unchanged to the letter."""
    brand_id = make_brand(deps.db, USER, "唯一品牌")["id"]
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=brand_id, typical_customers=["口腔诊所"])

    payload = get_current_customer_profile(deps.db, USER)

    assert payload["seller_profile"]["typical_customers"] == ["口腔诊所"]


def test_tool_layer_resolves_the_brand_from_the_session(deps):
    """The tool layer resolves the active brand itself -- the model won't pass it and shouldn't be relied on to."""
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    make_brand(deps.db, USER, "B 品牌")

    assert _active_brand_id(deps) is None, "两个品牌又没选过 → 解析不出来,由品牌卡去问"

    deps.tool_state["lead_flow"] = {"brand_id": a}
    assert _active_brand_id(deps) == a


def test_preferences_land_on_the_brand_that_said_them(deps):
    """"Not the education industry" said in A's session should be recorded on A.

    The read side already queries the ICP by brand, but the write side wrote to "the account's newest one" --
    so what was said in A landed on B's ICP, and A itself couldn't read it back.
    """
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    b = make_brand(deps.db, USER, "B 品牌")["id"]

    append_account_icp_preferences(deps.db, USER, exclude=["教育行业"], seller_profile_id=a)

    assert get_current_customer_profile(deps.db, USER, seller_profile_id=a)["account_icp"]["negative_icp"] == ["教育行业"]
    assert not (get_current_customer_profile(deps.db, USER, seller_profile_id=b)["account_icp"] or {}).get("negative_icp")


# --- CRUD boundaries ----------------------------------------------------------

def test_archive_hides_from_the_picker_but_keeps_history(deps):
    a = make_brand(deps.db, USER, "A 品牌")["id"]
    make_brand(deps.db, USER, "B 品牌")
    create_or_update_seller_profile(deps.db, USER, seller_profile_id=a, service_capabilities=["AI-GEO优化"])

    archive_row(deps.db, a)

    assert [b["name"] for b in list_brands(deps.db, USER)] == ["B 品牌"]
    # A named id can still be read: historical recommendation tasks and sent emails carry campaign.seller_profile_id,
    # and if unreadable the "our profile" block in old reports would go blank. Archived rows are excluded only when **not named**.
    assert _latest_seller_profile(deps.db, USER, a) is not None
    assert _capabilities(deps, a) == ["AI-GEO优化"]
    assert any(b["id"] == a for b in list_brands(deps.db, USER, include_archived=True))


def test_brands_are_scoped_to_the_owner(deps):
    make_brand(deps.db, USER, "A 品牌")
    make_brand(deps.db, OTHER_USER, "别人的品牌")

    assert [b["name"] for b in list_brands(deps.db, USER)] == ["A 品牌"]
    assert active_brand_count(deps.db, OTHER_USER) == 1


def test_session_brand_id_from_another_account_is_rejected(deps):
    """`context_json` can be tampered with; using it directly to query profiles lets people read other accounts' brands."""
    mine = make_brand(deps.db, USER, "A 品牌")["id"]
    theirs = make_brand(deps.db, OTHER_USER, "别人的品牌")["id"]

    assert resolve_active_brand_id(deps.db, USER, {"brand_id": theirs}) == mine, "越权 id 要被丢掉,落回自己的唯一品牌"


def test_running_a_recommendation_keeps_the_profile_body(deps):
    """Running find-customers recommendations once must not wash out the capabilities and evidence filled in on the admin page.

    `_profile_for_recommendation` writes the profile back to the DB along the way, and the underlying
    `create_or_update_seller_profile` **overwrites the whole row**: unpassed columns are all written as empty arrays.
    The recommendation path only collects scope/size/typical, so every recommendation wiped capabilities and evidence once --
    right after `_persist_lead_seller_profile_snapshot` had just written them in the previous step.
    """
    brand_id = make_brand(deps.db, USER, "官网业务线")["id"]
    set_body(
        deps.db, brand_id,
        service_capabilities=["官网建设", "SEO技术优化"],
        service_scope=["企业官网从 0 到 1 搭建"],
        typical_customers=["没有官网的企业"],
        company_size="11-100 名员工",
        proof_points=["服务过 60+ 制造业官网"],
    )

    # The find-customers flow calls recommend without seller_profile_id, and scope takes the capability slot -- copying that call in api.py
    _profile_for_recommendation(
        deps.db, USER,
        profile_id=None,
        service_scope=["AI-GEO优化"],
        industries=["制造业"],
        regions=["深圳"],
    )

    row = deps.db.get(SalesAgentSellerProfileORM, brand_id)
    assert json.loads(row.service_capabilities_json) == ["官网建设", "SEO技术优化"], "能力被推荐流程洗掉了"
    assert json.loads(row.proof_points_json) == ["服务过 60+ 制造业官网"], "佐证被推荐流程洗掉了"
    # Service scope being overwritten by this round's conditions is existing behavior (it's "what's being sold this time"); pinned here too to prevent regressions
    assert json.loads(row.service_scope_json) == ["AI-GEO优化"]
    assert json.loads(row.typical_customers_json) == ["没有官网的企业"]
    assert row.company_size == "11-100 名员工"


# --- Each brand's own email definitions ----------------------------------------------

def test_brand_email_settings_are_just_the_identity_now():
    """Brand-level email definitions (style / tone / extra requirements / signature) were deleted along with /sales-agent/admin.

    What's left here is only "which brand this email is sent as" -- it must stay, because the draft cache key uses the id to distinguish brands
    (see `test_fingerprint_tracks_the_brand`). The wording now comes entirely from the brand profile.
    """
    context = {"seller_profile": {"id": 7, "name": "口腔医疗"}}
    settings = outreach_brand_settings(context)

    assert settings == {"id": 7, "name": "口腔医疗"}


def test_brand_email_settings_default_to_empty():
    assert outreach_brand_settings({}) == {"id": 0, "name": ""}
    assert outreach_brand_settings({"seller_profile": None}) == {"id": 0, "name": ""}


def test_fingerprint_separates_two_brands_on_the_same_lead():
    """Without the brand, B would directly hit A's already-generated draft -- a pitfall hit immediately once brand prompts are added."""
    report_context = {"has_real_detection": True, "public_url": "https://x/y"}
    a = report_email_fingerprint(1, "email", "leijun_pitch", report_context, "https://x/y",
                                 brand={"id": 1, "prompt_extra": "必须提资质"})
    b = report_email_fingerprint(1, "email", "leijun_pitch", report_context, "https://x/y",
                                 brand={"id": 2, "prompt_extra": "不要出现价格"})

    assert a != b


def test_fingerprint_tracks_the_brand():
    """When business lines A and B email the same lead, they must not hit the same draft.

    Lead, report and perspective are all the same; the only difference is the brand -- and the two brands' profiles differ,
    so the email material is entirely different. Without it in the fingerprint B would directly get the one A already generated,
    sending someone else's self-introduction.
    """
    ctx = {"has_real_detection": False}
    a = report_email_fingerprint(1, "email", "leijun_pitch", ctx, brand={"id": 1, "name": "A"})
    b = report_email_fingerprint(1, "email", "leijun_pitch", ctx, brand={"id": 2, "name": "B"})

    assert a != b


def test_fingerprint_is_stable_without_a_brand():
    """Accounts without a configured brand: two calls must land on the same key, otherwise the cache is useless."""
    ctx = {"has_real_detection": False}
    assert report_email_fingerprint(1, "email", "leijun_pitch", ctx) == \
        report_email_fingerprint(1, "email", "leijun_pitch", ctx)
