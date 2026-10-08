"""Translation layer from brand material -> sales profile. **The source of truth for profiles is here, not in sales_agent's own tables.**

The 6-module brand material the customer fills at `/workbench/topics/:id/edit`
(`ai_telemetry_topics.profile_json`, model in `geo.models.ai_telemetry.BrandProfile`)
is the single source of truth. One `sales_agent_seller_profiles` row = one brand = one piece of material,
and the profile content in the table is demoted to a read-only mirror, refreshed one-way by `sync_brand_from_topic()`.

Why have this layer instead of reading `profile_json` everywhere: there can be only one mapping. Previously
"capabilities/service scope/size/ideal customers" were scattered across the profile card, capability card, ideal-customer card and email context,
four or five places each reading its own way, so adding a field meant chasing every call site. Defined once here; everything else imports it.

Layering: this module sits above `profiles` and below `guidance` (see tests/sales_agent/test_service_layering.py).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from geo.models.ai_telemetry import (
    PROFILE_ADMIN_KEY,
    AiTelemetryTopicORM,
    merge_profile_json,
)

from agents.sales_agent.models import SalesAgentSellerProfileORM
from agents.sales_agent.service.common import (
    REPORT_CAPABILITY_KEYWORDS,
    _clean_list,
    _clean_profile_option_list,
    _dumps,
    _loads,
    texts_are_report_capable,
)
from agents.sales_agent.service.profiles import (
    _active_brand_query,
    _latest_seller_profile,
    _new_brand_key,
    _next_brand_sort_order,
    _profile_summary_text,
)


# --------------------------------------------------------------------------- Reading material


def topic_profile(topic: AiTelemetryTopicORM | None) -> dict[str, Any]:
    """Get the topic's material form. The `_admin` section is back-office config, not part of the profile, so it's stripped here."""
    if topic is None:
        return {}
    data = _loads(topic.profile_json, {})
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k != PROFILE_ADMIN_KEY}


def _field_text(profile: dict[str, Any], key: str) -> str:
    return str(profile.get(key) or "").strip()


def _field_list(profile: dict[str, Any], key: str) -> list[str]:
    value = profile.get(key)
    return _clean_list(value if isinstance(value, list) else [value], 60)


# --------------------------------------------------------------------------- Mapping


def brand_name_from_profile(profile: dict[str, Any], fallback: str = "") -> str:
    """Brand name. Short name first — the full name ends in 「有限公司」 ("Co., Ltd."), long and hard to recognize on the selection card."""
    for key in ("company_short_name", "company_full_name", "profile_name"):
        text = _field_text(profile, key)
        if text:
            return text[:128]
    return fallback


def brand_content_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Material -> the five profile content items of the mirror row.

    Places that merge rather than pick one (capabilities, service scope, credentials) do so because the material form splits one thing into
    two fields: 「服务核心特点」 ("core service features") is what we can do, 「品牌差异化标签」 ("brand differentiation tags") is why we're better than others,
    and to the sales side both are "our capabilities". The order is fixed: specific before general, more informative when the card shows only the first few.
    """
    return {
        # Core service features + brand differentiation tags
        "service_capabilities": _clean_profile_option_list(
            [*_field_list(profile, "service_features"), *_field_list(profile, "brand_diff_tags")], 30
        ),
        # Core business lines + field (industry) + core service overview.
        # industry is nominally "industry classification", but in the material form many customers use it to write what they do
        # (e.g. "GEO performance measurement"), which to the sales side is part of the service content, so it's merged into the service scope display.
        # The order is still specific before general: business lines -> field -> overview (the whole paragraph goes last as a supplement).
        # Note that industry is also the default source of "target customer industry" in icp_from_profile —
        # that's the other side ("who to find"), while here it's "what I sell"; each takes what it needs without interfering.
        "service_scope": _clean_profile_option_list(
            [
                *_field_list(profile, "core_business_lines"),
                _field_text(profile, "industry"),
                _field_text(profile, "core_service_overview"),
            ],
            20,
        ),
        # Both sides' option word lists are identical (1-10 / 11-100 / 101-500 / 501-1000 / 1001+ 名员工, i.e. employees), so use it directly
        "company_size": _field_text(profile, "team_size")[:128],
        # This field's label in the form is exactly 「服务覆盖场景 / 客户类型」 ("service scenarios / customer types")
        "typical_customers": _field_list(profile, "target_scenarios")[:20],
        # Core honors / endorsing credentials + typical cases
        "proof_points": _clean_list(
            [*_field_list(profile, "core_credentials"), *_field_list(profile, "case_stories")], 30
        ),
    }


def icp_from_profile(profile: dict[str, Any]) -> dict[str, list[str]]:
    """Material -> industry / region of the target company ICP.

    Only **defaults**: each customer search still re-selects them. Industry and region are "who to find this time",
    while those two items in the material are "which industry I'm in, which area I serve" — similar in direction but not the same thing.
    """
    industry = _field_text(profile, "industry")
    geo = _field_text(profile, "service_geo")
    return {
        "target_industries": _clean_list([industry], 30),
        # Service region is a free-text field; enumeration-comma/comma separation is the most common way it's written in the material form
        "target_regions": _clean_list(
            [part for chunk in (geo,) for part in chunk.replace(",", "、").replace(",", "、").split("、")], 30
        ),
    }


def email_material_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Material -> material usable for writing emails + contact info allowlist.

    Contact info is an **allowlist**, not suggested values: if none is filled in the material, the body may contain no
    phone/email/URL at all. This is the same rule as `_build_contact_block` on the content generation side —
    contact info the model improvises is fabricated.
    """
    contacts = {
        "phone": _field_text(profile, "contact_phone"),
        "wechat": _field_text(profile, "contact_wechat"),
        "email": _field_text(profile, "contact_email"),
        "website": _field_text(profile, "website"),
    }
    return {
        "brand_name": brand_name_from_profile(profile),
        "company_full_name": _field_text(profile, "company_full_name"),
        "industry": _field_text(profile, "industry"),
        "service_geo": _field_text(profile, "service_geo"),
        "founded_year": _field_text(profile, "founded_year"),
        "entity_type": _field_text(profile, "entity_type"),
        "core_message": _field_text(profile, "core_message"),
        "brand_slogan": _field_text(profile, "brand_slogan"),
        "brand_values": _field_text(profile, "brand_values"),
        "brand_story": _field_text(profile, "brand_story"),
        "key_person_story": _field_text(profile, "key_person_story"),
        "core_service_overview": _field_text(profile, "core_service_overview"),
        "service_features": _field_list(profile, "service_features")[:20],
        "service_process": _field_list(profile, "service_process")[:20],
        "service_guarantees": _field_list(profile, "service_guarantees")[:20],
        "target_scenarios": _field_list(profile, "target_scenarios")[:20],
        "core_credentials": _field_list(profile, "core_credentials")[:20],
        "case_stories": _field_list(profile, "case_stories")[:10],
        "brand_diff_tags": _field_list(profile, "brand_diff_tags")[:10],
        "content_redlines": _field_list(profile, "content_redlines")[:20],
        "contacts": {k: v for k, v in contacts.items() if v},
        "has_contacts": any(contacts.values()),
    }


# --------------------------------------------------------------------------- Report capability

# Whether the current user's own brand offers website audit/diagnosis report features.
# "Generate report" means the platform's GEO audit tool producing a diagnosis report for the target company's website, not asking whether the recommended company
# itself has report generation features. Deliberately keyword-derived rather than a toggle: a toggle needs users to tick it,
# and all existing data is unticked, meaning everyone's report button would vanish overnight.
# Keywords only match **the services the current user provides**, not target customer industry names — 「医疗」 ("medical") doesn't count,
# 「口腔诊断」 ("dental diagnosis") does; but something like GEO-TAG that only does performance measurement/ROI reconciliation can't count just because the tag contains GEO.
# The word list itself is in `common.py` (the orphan brand path needs the same copy, and profiles sits below this module).
# A same-named re-export is kept here: the facade and callers have always referenced `brandprofile.REPORT_CAPABILITY_KEYWORDS`.

# These fields are scanned during derivation. Only the "what services I provide" fields — rhetoric like 「我们诊断了
# 行业痛点」 ("we diagnosed industry pain points") in the brand story shouldn't light up the report button.
_REPORT_CAPABILITY_FIELDS: tuple[str, ...] = (
    "service_features",
    "core_business_lines",
    "core_service_overview",
    "service_process",
    "brand_diff_tags",
    "profile_name",
    "company_short_name",
    "industry",
)


def derive_report_capability(profile: dict[str, Any]) -> bool:
    """Whether the current user's service profile includes website audit/diagnosis report features. Empty material is always False."""
    haystack: list[str] = []
    for key in _REPORT_CAPABILITY_FIELDS:
        value = profile.get(key)
        if isinstance(value, list):
            haystack.extend(str(item) for item in value)
        elif value:
            haystack.append(str(value))
    return texts_are_report_capable(haystack)


def _runtime_report_capable_for_row(db: Session, row: SalesAgentSellerProfileORM | None) -> bool:
    """Judge the report entry live from the current material, avoiding stale true values in old row.report_capable from the old word list."""
    if row is None:
        return False
    topic = topic_for_brand(db, row)
    if topic is not None:
        return derive_report_capability(topic_profile(topic))
    return texts_are_report_capable([
        row.name or "",
        *_loads(row.service_capabilities_json, []),
        *_loads(row.service_scope_json, []),
    ])


# --------------------------------------------------------------------------- Sync


def topic_for_brand(db: Session, row: SalesAgentSellerProfileORM | None) -> AiTelemetryTopicORM | None:
    """Brand -> its material. Orphan brands without claimed material return None; callers treat it as "material empty"."""
    if row is None or not row.topic_id:
        return None
    return (
        db.query(AiTelemetryTopicORM)
        .filter(
            AiTelemetryTopicORM.id == int(row.topic_id),
            AiTelemetryTopicORM.user_id == row.user_id,
        )
        .first()
    )


def _profile_worth_a_brand(profile: dict[str, Any], content: dict[str, Any]) -> bool:
    """Is this material filled enough to support a brand.

    The bar is deliberately very low — as long as it **can state a name** and has **at least one** of service capabilities/business lines,
    it counts. Too strict would make people think the material wasn't saved; too loose (e.g. just a non-empty profile) is no check at all,
    and every topic in the workspace would become an empty brand in the sales assistant.
    """
    named = bool(brand_name_from_profile(profile, fallback=""))
    # "Has substantive service content" is judged by capabilities / business lines / overview — deliberately ignoring the industry now
    # merged into service_scope: a draft topic with only an industry classification like "food service" filled in and no service described
    # shouldn't spawn an empty brand (see the spam note above).
    has_body = bool(
        content.get("service_capabilities")
        or _field_list(profile, "core_business_lines")
        or _field_text(profile, "core_service_overview")
    )
    return named and has_body


def profile_from_brand_row(row: SalesAgentSellerProfileORM | None) -> dict[str, Any]:
    """Mirror row -> material shape. The inverse mapping of `brand_content_from_profile`, only for orphan brands.

    Brands that claimed material **must never come here**: that would bypass the source of truth, passing off possibly stale mirror content
    as material. This serves only one case — legacy brands with no material form to read at all.

    Only the items that map back are filled. Material-only fields like `core_message` / contact info are left empty:
    the email side has allowlist semantics; better one sentence fewer than a contact made up out of thin air.
    """
    if row is None:
        return {}
    return {
        "company_short_name": (row.name or "").strip(),
        "service_features": _loads(row.service_capabilities_json, []),
        "core_business_lines": _loads(row.service_scope_json, []),
        "team_size": (row.company_size or "").strip(),
        "target_scenarios": _loads(row.typical_customers_json, []),
        "core_credentials": _loads(row.proof_points_json, []),
    }


def brand_profile_payload(db: Session, user_id: int, seller_profile_id: int | None = None) -> dict[str, Any]:
    """Get "who this brand is" all at once: brand row + material + derived profile + email material.

    Every echo takes it from here; don't assemble separately anymore — cards, email context and the admin page
    used to read independently, so adding a field meant chasing four or five call sites, and the content would disagree.
    """
    row = _latest_seller_profile(db, user_id, seller_profile_id)
    topic = topic_for_brand(db, row)
    profile = topic_profile(topic)
    if not profile and row is not None:
        # Orphan brands fall back to their own row. **They must not leave with an empty profile** — an empty profile means
        # all email material is empty, and `build_no_report_default_email_copy` raises 「没有品牌资料」 ("no brand material"),
        # so manually added brands couldn't send a single letter. And the original decision was "keep + mark, never auto-delete",
        # implying they must keep working: their mirror row holds the content hand-filled in /admin + collected in chat,
        # which **is** their profile, just without a material form behind it (`has_profile` stays False).
        profile = profile_from_brand_row(row)
    content = brand_content_from_profile(profile)
    return {
        "brand_id": row.id if row is not None else 0,
        "brand_name": (row.name or "").strip() if row is not None else "",
        "topic_id": topic.id if topic is not None else None,
        # Only material-backed ones count as has_profile: the fallback copy is "usable", not "has material".
        # Conflating them would make the admin page mark hand-filled content as read-only and send people to the workspace to edit it — where it's empty.
        "has_profile": topic is not None,
        "report_capable": _runtime_report_capable_for_row(db, row),
        "profile": profile,
        **content,
        "icp": icp_from_profile(profile),
        "email_material": email_material_from_profile(profile),
    }


def sync_brand_from_topic(
    db: Session,
    topic: AiTelemetryTopicORM | None,
    commit: bool = False,
) -> SalesAgentSellerProfileORM | None:
    """Refresh a piece of material into its brand mirror row; create one if missing. **The only place allowed to write profile content.**

    `commit=False` is the default: callers are basically all in their own transactions (the material save endpoint),
    and committing here would commit their unfinished work along with it.

    The email-style set of four is untouched — that's the sales side's own config, with no counterpart in the material form.
    """
    if topic is None or not topic.user_id:
        return None
    row = (
        db.query(SalesAgentSellerProfileORM)
        .filter(
            SalesAgentSellerProfileORM.user_id == topic.user_id,
            SalesAgentSellerProfileORM.topic_id == topic.id,
        )
        .first()
    )
    profile = topic_profile(topic)
    content = brand_content_from_profile(profile)
    name = brand_name_from_profile(profile, fallback=(topic.name or topic.target or "").strip() or "默认品牌")

    if row is None and not _profile_worth_a_brand(profile, content):
        # Material without substantive content yet -> don't create a brand. Whenever a customer starts a topic in the workspace a material row lands
        # (the `create_draft_topic` path syncs with an empty profile), giving the sales assistant one more
        # brand — and the brand selection card pops up whenever `len(brands) >= 2`, so with many half-empty topics, users would have to
        # pick from a pile of 「默认品牌」 ("default brand") every conversation turn, and still end up with an empty profile.
        #
        # The criterion is "has content" rather than `submission_status == "draft"`: the path where customers create a topic with material
        # (ai_telemetry.py:686) doesn't set a status at all and defaults to draft — blocking by status
        # would shut the main path entirely.
        #
        # Only creation is blocked; existing rows still refresh: a customer reverting material to half-empty is "editing", not "withdrawing",
        # and an in-use brand suddenly stopping syncing is much harder to debug than an extra brand.
        return None

    if row is None:
        row = SalesAgentSellerProfileORM(
            user_id=topic.user_id,
            topic_id=topic.id,
            brand_key=_new_brand_key(db, topic.user_id),
            sort_order=_next_brand_sort_order(db, topic.user_id),
        )
        db.add(row)
    row.name = name[:128]
    row.service_capabilities_json = _dumps(content["service_capabilities"])
    row.service_scope_json = _dumps(content["service_scope"])
    row.company_size = content["company_size"]
    row.typical_customers_json = _dumps(content["typical_customers"])
    row.proof_points_json = _dumps(content["proof_points"])
    row.report_capable = derive_report_capability(profile)
    row.summary = _profile_summary_text(
        content["service_scope"],
        content["company_size"],
        content["typical_customers"],
        _loads(row.default_buyer_roles_json, []),
        content["service_capabilities"],
    )
    # Unarchive when the material comes back: if a topic was deleted and then restored, the brand should come back with it
    row.archived_at = None
    db.flush()
    _archive_brands_without_material(db, topic.user_id)
    if commit:
        db.commit()
        db.refresh(row)
    return row


def _archive_brands_without_material(db: Session, user_id: int) -> int:
    """Once an account has a brand with material, active brands **without material** in the same account are archived.

    Production on 2026-08-27: account 1000056 got a brand selection card before every conversation turn because it had two active
    brands — GEO-TAG (synced from material, non-empty `topic_id`) and Vigilath (empty `topic_id`,
    a legacy orphan accumulated from chat content by old code before the material system; the multi-brand migration's
    "keep the latest row active per user" kept it).

    The card's cost isn't just an extra click: until the card is answered the brand is undecided, and `_brand_is_ambiguous` makes
    the 「适合客户」 ("ideal customers") card **skip reading the DB entirely** (`_append_lead_typical_customers_guidance` in api.py),
    falling back to guessing a generic option set by capability keywords — the uploaded profile clearly states which customers it serves,
    yet not a word of it gets used. So an orphan isn't "one harmless extra row"; it crowds out the real data.

    **Only archives when a brand with a profile already exists**, so it never empties the account: this function is only
    called from `sync_brand_from_topic`, which has always just written a brand row with a `topic_id`,
    so at least that one remains. An account with no profile-backed brand at all (production 1000023 is like this: 15 brands,
    all orphans) never reaches this point and is left as-is.

    Archiving is not deleting: historical recommendation tasks and sent emails reference `seller_profile_id`, and `_latest_seller_profile`
    ignores `archived_at` when fetching a row by id, so the "our profile" section in old reports never goes blank.
    But orphans **do not come back on their own**: they have no `topic_id`, and `sync_brand_from_topic` looks rows up
    by `topic_id`, so it will never find them. To restore one, manually clear `archived_at`,
    or link a profile to it.
    """
    from datetime import datetime

    return (
        db.query(SalesAgentSellerProfileORM)
        .filter(
            SalesAgentSellerProfileORM.user_id == user_id,
            SalesAgentSellerProfileORM.topic_id.is_(None),
            SalesAgentSellerProfileORM.archived_at.is_(None),
        )
        .update({"archived_at": datetime.utcnow()}, synchronize_session=False)
    )


def archive_brand_for_topic(db: Session, topic_id: int, user_id: int, commit: bool = False) -> None:
    """Profile deleted / archived -> brand is soft-deleted with it. Historical recommendation tasks and sent emails are all kept."""
    from datetime import datetime

    row = (
        db.query(SalesAgentSellerProfileORM)
        .filter(
            SalesAgentSellerProfileORM.user_id == user_id,
            SalesAgentSellerProfileORM.topic_id == int(topic_id),
            SalesAgentSellerProfileORM.archived_at.is_(None),
        )
        .first()
    )
    if row is None:
        return
    row.archived_at = datetime.utcnow()
    db.flush()
    if commit:
        db.commit()


def sync_brands_for_user(db: Session, user_id: int, commit: bool = True) -> list[SalesAgentSellerProfileORM]:
    """Refresh every profile of the account. Used by the backfill script and "bulk profile changes"."""
    topics = (
        db.query(AiTelemetryTopicORM)
        .filter(AiTelemetryTopicORM.user_id == user_id)
        .order_by(AiTelemetryTopicORM.id.asc())
        .all()
    )
    rows = [row for row in (sync_brand_from_topic(db, topic, commit=False) for topic in topics) if row is not None]
    if commit:
        db.commit()
        for row in rows:
            db.refresh(row)
    return rows


# --------------------------------------------------------------------------- reverse write-back


def remove_agent_profile_option(
    db: Session,
    row: SalesAgentSellerProfileORM | None,
    kind: str,
    value: str,
    actor_id: int | None = None,
    commit: bool = False,
) -> bool:
    """Remove one item from the sales profile and sync it according to the source of truth.

    Rows that claimed a brand profile must update topic.profile_json; orphan brands have no topic and can only
    update their sales_agent_seller_profiles row. Both paths finally refresh the summary and the derived report capability.
    """
    text = str(value or "").strip()
    if row is None or not text:
        return False
    kind_key = str(kind or "").strip()
    if kind_key == "capability":
        topic_keys = ("service_features", "brand_diff_tags")
        row_attr = "service_capabilities_json"
        note = "服务核心特点"
    elif kind_key == "typical_customer":
        topic_keys = ("target_scenarios",)
        row_attr = "typical_customers_json"
        note = "适合客户类型"
    else:
        return False

    target = text.casefold()
    topic = topic_for_brand(db, row)
    if topic is not None:
        profile = topic_profile(topic)
        changed = False
        for key in topic_keys:
            current = _field_list(profile, key)
            kept = [item for item in current if item.casefold() != target]
            if kept != current:
                profile[key] = kept
                changed = True
        if not changed:
            return False
        topic.profile_json = merge_profile_json(topic, profile)
        _append_topic_remove_changelog(topic, actor_id, text, note)
        sync_brand_from_topic(db, topic)
    else:
        current = _loads(getattr(row, row_attr, "[]"), [])
        cleaned = _clean_list(current, 30)
        kept = [item for item in cleaned if item.casefold() != target]
        if kept == cleaned:
            return False
        setattr(row, row_attr, _dumps(kept))
        capabilities = kept if kind_key == "capability" else _loads(row.service_capabilities_json, [])
        typical = kept if kind_key == "typical_customer" else _loads(row.typical_customers_json, [])
        row.report_capable = texts_are_report_capable([
            row.name or "",
            *_clean_list(capabilities, 30),
            *_loads(row.service_scope_json, []),
        ])
        row.summary = _profile_summary_text(
            _loads(row.service_scope_json, []),
            row.company_size or "",
            _clean_list(typical, 20),
            _loads(row.default_buyer_roles_json, []),
            _clean_list(capabilities, 30),
        )
        db.flush()
    if commit:
        db.commit()
    return True


def remove_agent_capability_from_profile(
    db: Session,
    row: SalesAgentSellerProfileORM | None,
    value: str,
    actor_id: int | None = None,
    commit: bool = False,
) -> bool:
    """Backward-compatible legacy call: delete a service capability from the sales profile."""
    return remove_agent_profile_option(db, row, "capability", value, actor_id=actor_id, commit=commit)


def write_agent_updates_to_topic(
    db: Session,
    topic: AiTelemetryTopicORM | None = None,
    capabilities: list[str] | None = None,
    typical_customers: list[str] | None = None,
    company_size: str = "",
    actor_id: int | None = None,
    commit: bool = False,
) -> bool:
    """Write what the user added via "update profile" in chat back to the profile itself.

    Capabilities go into "service core features"; target customers go into "service scenarios / customer types". Both lists are
    **append-only**: a card's selected_values only reflects what was ticked in this session, and when a new session opens the card
    nothing is ticked; treating it as the full set would wash the user's dozens of existing entries down to one.

    Team size is a scalar and cannot be "appended", so the **profile wins**: if the profile has a value, leave it,
    and only fill it in when empty. Overwriting what the customer carefully picked in the form because of one chat question is backwards.

    Uses `merge_profile_json` rather than `json.dumps`: the latter would wipe the entire `_admin` section.
    """
    if topic is None:
        return False
    profile = topic_profile(topic)
    # This function writes **the user's brand profile itself**, so nothing here is truncated by length: whether existing
    # entries or newly added ones, whatever goes into the profile form must be the user's original wording. Once a truncated version is written back,
    # long copy the user wrote by hand in the workspace is cut in half irrecoverably (that is where half-sentences in email bodies came from).
    # The display side controls length by truncating on read (brand_content_from_profile / the profile cards), not here.
    current_raw = _field_list(profile, "service_features")
    current = _clean_profile_option_list(current_raw, 20)
    seen = {item.casefold() for item in current}
    added = [item for item in _clean_profile_option_list(capabilities, 30) if item.casefold() not in seen]
    current_typical_raw = _field_list(profile, "target_scenarios")
    current_typical = _clean_profile_option_list(current_typical_raw, 20)
    typical_seen = {item.casefold() for item in current_typical}
    added_typical = [item for item in _clean_profile_option_list(typical_customers, 30) if item.casefold() not in typical_seen]
    size = str(company_size or "").strip()[:64]
    fill_size = bool(size) and not _field_text(profile, "team_size")
    if not added and not added_typical and not fill_size:
        return False
    if added:
        # Keep the profile form's own limit (BrandProfile.service_features max_length=20)
        profile["service_features"] = (current + added)[:20]
    if added_typical:
        # Keep the profile form's own limit (BrandProfile.target_scenarios max_length=20)
        profile["target_scenarios"] = (current_typical + added_typical)[:20]
    if fill_size:
        profile["team_size"] = size
    topic.profile_json = merge_profile_json(topic, profile)
    _append_topic_changelog(topic, actor_id, added, size if fill_size else "", added_typical)
    db.flush()
    if commit:
        db.commit()
    return True


def _append_topic_remove_changelog(
    topic: AiTelemetryTopicORM,
    actor_id: int | None,
    removed: str,
    label: str = "服务核心特点",
) -> None:
    """Record that the sales assistant removed a profile item from the brand profile."""
    from datetime import datetime

    text = str(removed or "").strip()
    if not text:
        return
    try:
        entries = _loads(topic.topic_changelog_json, [])
        if not isinstance(entries, list):
            entries = []
        new_version = int(getattr(topic, "version", 1) or 1) + 1
        topic.version = new_version
        entries.append({
            "at": datetime.utcnow().isoformat(),
            "actor_id": actor_id,
            "actor_role": "agent",
            "field": "profile",
            "after": text[:500],
            "note": f"销售助理删除{str(label or '画像项').strip()}",
            "version": new_version,
        })
        topic.topic_changelog_json = _dumps(entries)
    except Exception:  # noqa: BLE001
        pass



def _append_topic_changelog(
    topic: AiTelemetryTopicORM,
    actor_id: int | None,
    added: list[str],
    team_size: str = "",
    added_typical: list[str] | None = None,
) -> None:
    """Same changelog metric definition as the profile form; `actor_role` is set to agent to distinguish the source.

    `geo.api.ai_telemetry._append_changelog` lives in the api layer and the service layer must not reference it,
    so we build an entry ourselves with the same schema; fields align with `TopicChangelogEntry`, and
    **`version` must also be incremented**: each changelog entry there maps to a version number; without it here
    the profile page's revision number would skip the writes made by the sales assistant.
    """
    from datetime import datetime

    try:
        entries = _loads(topic.topic_changelog_json, [])
        if not isinstance(entries, list):
            entries = []
        new_version = int(getattr(topic, "version", 1) or 1) + 1
        topic.version = new_version
        after_parts = [*added, *(added_typical or [])]
        after = "、".join(after_parts)
        notes = []
        if added:
            notes.append("补充服务核心特点")
        if added_typical:
            notes.append("补充适合客户类型")
        if team_size:
            notes.append(f"补填团队规模 {team_size}")
            after = f"{after};{team_size}" if after else team_size
        entries.append({
            "at": datetime.utcnow().isoformat(),
            "actor_id": actor_id,
            "actor_role": "agent",
            "field": "profile",
            "after": after[:500],
            "note": "销售助理" + "、".join(notes),
            "version": new_version,
        })
        topic.topic_changelog_json = _dumps(entries)
    except Exception:  # noqa: BLE001
        # The changelog is an audit aid; failing to write it must not fail the profile update itself
        pass


# --------------------------------------------------------------------------- echo back


def account_report_capable(db: Session, user_id: int) -> bool:
    """Whether **any** active brand of the account offers website audit/diagnostic reports. Used to decide whether to hide the button before a brand is selected."""
    return any(_runtime_report_capable_for_row(db, row) for row in _active_brand_query(db, user_id).all())


def report_capability_denial(db: Session, user_id: int, seller_profile_id: int | None = None) -> str:
    """Return a user-facing reason when the audit should not run; return an empty string when it may run.

    Hiding the button in the frontend only blocks clicks; the model cannot see the UI, and a user saying "generate an audit report for me" still
    reaches the tool layer. So the gate must exist in the backend too.

    When no brand is selected (`seller_profile_id` empty and the account has multiple brands), relax to **account level**:
    allow it as long as any brand sells audits, and let the brand selection card narrow it down. Being strict here would mean multi-brand
    accounts could not even start a report before picking a brand, which is exactly the most common entry order.
    """
    row = _latest_seller_profile(db, user_id, seller_profile_id)
    if row is None:
        # No brand at all: the profile is not set up yet. Do not block here; let the existing "set up profile first" prompt handle it.
        return ""
    if seller_profile_id and row.id == seller_profile_id:
        if _runtime_report_capable_for_row(db, row):
            return ""
        name = (row.name or "").strip() or "当前品牌"
        if not row.topic_id:
            return "你当前的品牌资料里还没有体现检测/诊断类服务能力,所以暂时不能发起检测报告。请先到客户工作台补充品牌资料,完善服务能力或专业能力后再试。"
        return (
            f"品牌「{name}」的服务能力里没有检测、诊断、报告这类业务,不提供检测报告功能。"
            "可以直接介绍服务、生成外联邮件;如果确实做这类服务,请到品牌资料的"
            "「服务核心特点」里补上再试。"
        )
    if account_report_capable(db, user_id):
        return ""
    return (
        "你的品牌资料里没有检测、诊断、报告这类服务,不提供检测报告功能。"
        "可以直接介绍服务、生成外联邮件;如果确实做这类服务,请到品牌资料的"
        "「服务核心特点」里补上再试。"
    )


def brand_report_capable(db: Session, user_id: int, seller_profile_id: int | None = None) -> bool:
    """Whether the frontend should show the report button.

    Deliberately written as the negation of `report_capability_denial` instead of re-checking `row.report_capable`:
    button visible <=> action allowed; with a single source there is never "button shown but rejected on click" or the reverse,
    "can run but no entry point". The relaxation rule (account level when no brand is selected) also lives in one place only.
    """
    return not report_capability_denial(db, user_id, seller_profile_id)
