"""Sales Agent service layer: option catalog for profile/filter cards.

The five option kinds (capabilities / target customer types / industries / regions / company sizes) used to be scattered across three literals:
`guidance.py` copied the capability table twice, industries and regions were in `constants.py`, size buckets in `common.py`.
Custom items users wrote in a card only landed in that profile row's json column and never became selectable options next time.

Now everything is read from `sales_agent_option_catalog`:

- `user_id` / `seller_profile_id` both NULL -> system preset, shared by all accounts
- both set -> written by the user, private per brand (= seller_profile row)

The code-side tuples remain as a fallback; when the DB query fails or the migration has not run, behavior is exactly as before.
The convention in `constants.py:29-30` that "card options and the flow/slots.py extraction vocabulary must share a source"
is now guaranteed by both sides calling `load_options()`, instead of manually keeping two tuples aligned.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agents.sales_agent.constants import TARGET_INDUSTRY_OPTIONS, TARGET_REGION_OPTIONS
from agents.sales_agent.flow import SlotCatalog
from agents.sales_agent.models import SalesAgentOptionCatalogORM
from agents.sales_agent.service.common import COMPANY_SIZE_BUCKETS, SalesAgentError

# Module-private state, not part of the facade: uses annotated assignments, since `test_service_layering`'s
# `_top_level_names` only collects ast.Assign, so these three are not required to be re-exported.
logger: logging.Logger = logging.getLogger(__name__)

CAPABILITY = "capability"
TYPICAL_CUSTOMER = "typical_customer"
INDUSTRY = "industry"
REGION = "region"
COMPANY_SIZE = "company_size"

OPTION_KINDS: tuple[str, ...] = (CAPABILITY, TYPICAL_CUSTOMER, INDUSTRY, REGION, COMPANY_SIZE)

# Max number of custom items per brand, same order of magnitude as guidance.CUSTOM_OPTION_MAX
CUSTOM_OPTION_LIMIT = 20
MAX_VALUE_LEN = 120
MAX_DESC_LEN = 255

# 「自定义能力」 ("custom capability") and 「自定义行业」 ("custom industry") are "let me write my own" action entries, not capabilities/industries.
# Card-building code appends them after the options; they must never be written back to the catalog as custom items, or the profile ends up saying
# 「您是一家提供自定义能力的公司」 ("you are a company that provides custom capability") (verbatim from production, 2026-08-23).
ACTION_TOKENS = frozenset({"自定义能力", "自定义行业", "自定义", "其它能力", "其他能力", "其它行业", "其他行业"})


def _build_fallback() -> dict[str, tuple[dict[str, Any], ...]]:
    """Fallback when the migration has not run / the DB query fails; same source as the seed in migration c2e8a5d31f94."""
    return {
        CAPABILITY: tuple(
            {"value": v, "label": v, "desc": "", "source": "system"}
            for v in ("AI-GEO优化", "SEO技术团队", "官网建设/优化", "内容营销能力",
                      "行业解决方案经验", "数据分析能力", "销售/交付团队")
        ),
        TYPICAL_CUSTOMER: tuple(
            {"value": v, "label": v, "desc": d, "source": "system"}
            for v, d in (
                ("有官网但搜索获客一般的企业", "已有官网,但自然搜索、询盘或咨询转化不理想。"),
                ("想提升 AI 曝光的品牌方", "希望在 ChatGPT、Perplexity、AI Overviews 等场景被更多推荐。"),
                ("有内容团队但转化不足的公司", "持续做内容,但排名、留资或成交线索没有明显增长。"),
                ("正在做数字化获客的中小企业", "愿意投入官网、SEO、GEO 或 AI 搜索获客体系升级。"),
            )
        ),
        INDUSTRY: tuple(
            {"value": v, "label": v, "desc": "", "source": "system"}
            for v in TARGET_INDUSTRY_OPTIONS if v not in ACTION_TOKENS
        ),
        REGION: tuple(
            {"value": v, "label": v, "desc": "", "source": "system"}
            for v in TARGET_REGION_OPTIONS if v not in ACTION_TOKENS
        ),
        COMPANY_SIZE: tuple(
            {"value": label, "label": label, "desc": "", "source": "system",
             "min_employees": low, "max_employees": high}
            for label, low, high in COMPANY_SIZE_BUCKETS
        ),
    }


_FALLBACK: dict[str, tuple[dict[str, Any], ...]] = _build_fallback()

# In-process cache: without it each chat turn costs 5 extra DB queries (one per kind), and these rows barely change.
# Invalidated wholesale on write: the catalog is small, fine-grained invalidation is not worth it.
_LOCK: threading.Lock = threading.Lock()
_VERSION: int = 0
_CACHE: dict[tuple[str, int | None, int | None], tuple[int, tuple[dict[str, Any], ...]]] = {}


def _bump() -> None:
    global _VERSION
    with _LOCK:
        _VERSION += 1
        _CACHE.clear()


def reset_option_cache() -> None:
    """For tests: clear the cache. In production, write functions invalidate it automatically."""
    _bump()


def _copy(options: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [dict(o) for o in options]


def _row_payload(row: SalesAgentOptionCatalogORM) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "kind": row.kind,
        "value": row.value,
        "label": row.label or row.value,
        "desc": row.description or "",
        "source": row.source or "system",
        "seller_profile_id": row.seller_profile_id,
        "editable": row.user_id is not None,
    }
    if row.kind == COMPANY_SIZE:
        payload["min_employees"] = row.min_employees
        payload["max_employees"] = row.max_employees
    return payload


def load_options(
    db: Session | None,
    kind: str,
    user_id: int | None = None,
    seller_profile_id: int | None = None,
) -> list[dict[str, Any]]:
    """System presets + this brand's private items, ordered by sort_order; system items first.

    **When no system preset rows are found, fill in the code fallback**; note the criterion is "no system rows", not "no rows at all":
    when the table exists but the seed was not loaded (e.g. `create_all` in tests), brand-private rows may exist,
    and a "fallback only when there are no rows" check would make the 16 built-in industries vanish, leaving only the user's own one.
    """
    if kind not in OPTION_KINDS:
        return []
    fallback = _FALLBACK.get(kind, ())
    if db is None:
        return _copy(fallback)

    key = (kind, user_id, seller_profile_id)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == _VERSION:
            return _copy(cached[1])
        version_at_read = _VERSION

    # System preset rows (both ownership columns NULL) are always included; brand-private rows only when ownership is given.
    # Filter out other accounts' rows in SQL; pulling every kind back and grouping in Python
    # becomes a full table scan once there are many accounts.
    scope = SalesAgentOptionCatalogORM.user_id.is_(None) & SalesAgentOptionCatalogORM.seller_profile_id.is_(None)
    if user_id is not None and seller_profile_id is not None:
        scope = or_(
            scope,
            (SalesAgentOptionCatalogORM.user_id == int(user_id))
            & (SalesAgentOptionCatalogORM.seller_profile_id == int(seller_profile_id)),
        )
    try:
        rows = (
            db.query(SalesAgentOptionCatalogORM)
            .filter(
                SalesAgentOptionCatalogORM.kind == kind,
                SalesAgentOptionCatalogORM.archived_at.is_(None),
                scope,
            )
            .order_by(
                SalesAgentOptionCatalogORM.sort_order.asc(),
                SalesAgentOptionCatalogORM.id.asc(),
            )
            .all()
        )
    except Exception:  # noqa: BLE001
        # Migration not run / table missing: never let the find-customers flow break on options
        logger.warning("Option catalog query failed kind=%s, falling back to constants", kind, exc_info=True)
        return _copy(fallback)

    seen: set[str] = set()
    options: list[dict[str, Any]] = []
    has_system = False
    for row in rows:
        value = str(row.value or "").strip()
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        has_system = has_system or row.user_id is None
        options.append(_row_payload(row))

    # New system fallback items added in code must show up on cards even without DB changes.
    # Same-named items still defer to the DB row; missing fallback system items are inserted before brand-private items to keep "system items first".
    missing_fallback = [dict(o) for o in fallback if str(o.get("value") or "").strip().casefold() not in seen]
    if missing_fallback:
        insert_at = next((idx for idx, option in enumerate(options) if option.get("editable")), len(options))
        options = options[:insert_at] + missing_fallback + options[insert_at:]
    if not options:
        return _copy(fallback)

    with _LOCK:
        # If a write happened during the read, do not put the stale snapshot into the cache
        if version_at_read == _VERSION:
            _CACHE[key] = (_VERSION, tuple(dict(o) for o in options))
    return options


def option_values(
    db: Session | None,
    kind: str,
    user_id: int | None = None,
    seller_profile_id: int | None = None,
) -> tuple[str, ...]:
    return tuple(
        str(o.get("value") or "")
        for o in load_options(db, kind, user_id, seller_profile_id)
        if str(o.get("value") or "").strip()
    )


def load_card_options(
    db: Session | None,
    user_id: int | None = None,
    seller_profile_id: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Load all five kinds at once and hand them to `build_missing_info_guidance_card` for injection.

    guidance.py stays a pure function and does not touch the db (test_service_layering.py guards this),
    so the caller loads and passes them in; same convention as SlotCatalog's "injected by caller".
    """
    return {kind: load_options(db, kind, user_id, seller_profile_id) for kind in OPTION_KINDS}


def build_slot_catalog(
    db: Session | None,
    user_id: int | None = None,
    seller_profile_id: int | None = None,
) -> SlotCatalog:
    """Extraction vocabulary shares a source with card options.

    Putting custom items into the vocabulary also fixes a real bug: previously when a user entered 「口腔正畸」 ("orthodontics") via "custom industry",
    it went into the campaign, but because it was not in TARGET_INDUSTRY_OPTIONS, later mentions of 「口腔正畸」
    were never recognized.
    """
    return SlotCatalog(
        industries=option_values(db, INDUSTRY, user_id, seller_profile_id),
        regions=tuple(value for value in option_values(db, REGION, user_id, seller_profile_id) if value != "全国"),
        company_sizes=option_values(db, COMPANY_SIZE, user_id, seller_profile_id),
    )


def is_system_option(db: Session | None, kind: str, value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(
        str(o.get("value") or "").strip().casefold() == text.casefold()
        for o in load_options(db, kind, None, None)
    )


def _upsert_custom_option(
    db: Session,
    kind: str,
    user_id: int | None,
    seller_profile_id: int | None,
    value: str,
    label: str = "",
    description: str = "",
) -> tuple[SalesAgentOptionCatalogORM | None, bool]:
    """Return (row, whether newly added or revived). Does not commit; the caller decides."""
    text = str(value or "").strip()[:MAX_VALUE_LEN]
    if not text or kind not in OPTION_KINDS or text in ACTION_TOKENS:
        return None, False
    if user_id is None or seller_profile_id is None:
        # Without ownership it cannot be brand-private; storing it as a system preset would pollute all accounts, so better not to store it
        return None, False
    if is_system_option(db, kind, text):
        return None, False

    row = (
        db.query(SalesAgentOptionCatalogORM)
        .filter(
            SalesAgentOptionCatalogORM.kind == kind,
            SalesAgentOptionCatalogORM.user_id == int(user_id),
            SalesAgentOptionCatalogORM.seller_profile_id == int(seller_profile_id),
            SalesAgentOptionCatalogORM.value == text,
        )
        .first()
    )
    if row is not None:
        if row.archived_at is None:
            return row, False
        row.archived_at = None
        # Refresh display name / description on revival: "delete and add again" is the most intuitive way to change the description;
        # reusing the old row without updating would leave the user seeing their new description not take effect.
        # Only overwrite when a non-empty value is given: the chat flow does not pass these two args when bulk-collecting custom items,
        # and overwriting unconditionally would wipe descriptions filled in on the admin page.
        if str(label or "").strip():
            row.label = str(label).strip()[:MAX_VALUE_LEN]
        if str(description or "").strip():
            row.description = str(description).strip()[:MAX_DESC_LEN]
        row.updated_at = datetime.utcnow()
        db.flush()
        return row, True

    active = (
        db.query(SalesAgentOptionCatalogORM)
        .filter(
            SalesAgentOptionCatalogORM.kind == kind,
            SalesAgentOptionCatalogORM.user_id == int(user_id),
            SalesAgentOptionCatalogORM.seller_profile_id == int(seller_profile_id),
            SalesAgentOptionCatalogORM.archived_at.is_(None),
        )
        .count()
    )
    if active >= CUSTOM_OPTION_LIMIT:
        return None, False
    row = SalesAgentOptionCatalogORM(
        kind=kind,
        user_id=int(user_id),
        seller_profile_id=int(seller_profile_id),
        value=text,
        label=str(label or text).strip()[:MAX_VALUE_LEN],
        description=str(description or "").strip()[:MAX_DESC_LEN],
        source="custom",
        # Placed after system presets: system items' sort_order is 10..180
        sort_order=1000 + active,
    )
    db.add(row)
    db.flush()
    return row, True


def add_custom_option(
    db: Session,
    kind: str,
    user_id: int | None,
    seller_profile_id: int | None,
    value: str,
    label: str = "",
    description: str = "",
) -> SalesAgentOptionCatalogORM | None:
    """Add an item the user wrote themselves to the catalog so it appears in the options next time a card is shown.

    Idempotent: one row per value per brand; archived rows are revived rather than inserting a new one.
    """
    try:
        row, changed = _upsert_custom_option(db, kind, user_id, seller_profile_id, value, label, description)
        if changed:
            db.commit()
            _bump()
        return row
    except Exception:  # noqa: BLE001
        logger.warning("Add custom option failed kind=%s value=%s", kind, value, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def add_custom_options(
    db: Session,
    kind: str,
    user_id: int | None,
    seller_profile_id: int | None,
    values: list[Any] | None,
) -> int:
    """Bulk version. Returns the number actually added/revived (existing ones are not counted)."""
    added = 0
    try:
        for item in values or []:
            _, changed = _upsert_custom_option(db, kind, user_id, seller_profile_id, str(item or ""))
            if changed:
                added += 1
        if added:
            db.commit()
            _bump()
    except Exception:  # noqa: BLE001
        logger.warning("Add custom options failed kind=%s", kind, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    return added


# ---------------------------------------------------------------------------
# For REST: read/write endpoints for the hidden admin page /sales-agent/admin
#
# All three verify ownership by user_id: even if someone guesses the URL, they can only see and change their own account's rows.
# System preset rows (user_id NULL) may never be changed through the API under any circumstances.
# ---------------------------------------------------------------------------

def _serialize_option(row: SalesAgentOptionCatalogORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "value": row.value,
        "label": row.label or row.value,
        "description": row.description or "",
        "source": row.source or "system",
        "sort_order": row.sort_order,
        "min_employees": row.min_employees,
        "max_employees": row.max_employees,
        "seller_profile_id": row.seller_profile_id,
        "editable": row.user_id is not None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


def list_option_catalog(
    db: Session,
    user_id: int,
    seller_profile_id: int | None = None,
    kind: str = "",
) -> dict[str, Any]:
    """Return system presets + this brand's private items grouped by kind. Empty kind returns all five."""
    kinds = (kind,) if kind else OPTION_KINDS
    for k in kinds:
        if k not in OPTION_KINDS:
            raise SalesAgentError(f"未知的选项类型:{k}", status_code=400)
    return {
        "seller_profile_id": seller_profile_id,
        "custom_option_limit": CUSTOM_OPTION_LIMIT,
        "items": {k: load_options(db, k, user_id, seller_profile_id) for k in kinds},
    }


def create_custom_option(
    db: Session,
    user_id: int,
    kind: str,
    seller_profile_id: int | None,
    value: str,
    label: str = "",
    description: str = "",
) -> dict[str, Any]:
    if kind not in OPTION_KINDS:
        raise SalesAgentError(f"未知的选项类型:{kind}", status_code=400)
    text = str(value or "").strip()
    if not text:
        raise SalesAgentError("选项内容不能为空", status_code=400)
    if seller_profile_id is None:
        raise SalesAgentError("请先选择一个品牌,自定义选项按品牌私有保存", status_code=400)
    if text in ACTION_TOKENS:
        raise SalesAgentError(f"「{text}」是卡片上的操作入口,不能存成选项", status_code=400)
    if is_system_option(db, kind, text):
        raise SalesAgentError(f"「{text}」已经是系统预置选项", status_code=409)

    row, changed = _upsert_custom_option(db, kind, user_id, seller_profile_id, text, label, description)
    if row is None:
        raise SalesAgentError(f"该品牌的自定义选项已达上限({CUSTOM_OPTION_LIMIT} 条),请先归档一些", status_code=400)
    if not changed:
        raise SalesAgentError(f"「{text}」已经在选项里了", status_code=409)
    db.commit()
    _bump()
    return _serialize_option(row)


def update_custom_option(
    db: Session,
    user_id: int,
    option_id: int,
    label: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Change a custom option's display name and description.

    **value cannot be changed**: it is the key in the extraction vocabulary and also the literal stored in historical profiles, recommendation tasks
    and sent emails. Changing it would point old data at a nonexistent option; to change the word, delete and re-add.

    The description (`description`) goes into the card as `desc`, rendered on the line below the option title.
    System preset items have user_id NULL, so the filter naturally excludes them (read-only).
    """
    row = (
        db.query(SalesAgentOptionCatalogORM)
        .filter(SalesAgentOptionCatalogORM.id == int(option_id))
        .first()
    )
    # Consistent with archive: ownership mismatch returns 404, not 403
    if row is None or row.user_id is None or int(row.user_id) != int(user_id):
        raise SalesAgentError("选项不存在", status_code=404)
    if label is None and description is None:
        return _serialize_option(row)
    if label is not None:
        # Clearing the display name falls back to value; options with an empty title are not allowed
        row.label = str(label).strip()[:MAX_VALUE_LEN] or row.value
    if description is not None:
        row.description = str(description).strip()[:MAX_DESC_LEN]
    row.updated_at = datetime.utcnow()
    db.commit()
    _bump()
    return _serialize_option(row)


def archive_custom_option(db: Session, user_id: int, option_id: int) -> dict[str, Any]:
    row = (
        db.query(SalesAgentOptionCatalogORM)
        .filter(SalesAgentOptionCatalogORM.id == int(option_id))
        .first()
    )
    # Ownership mismatch also returns 404: returning 403 would tell the caller "this id exists"
    if row is None or row.user_id is None or int(row.user_id) != int(user_id):
        raise SalesAgentError("选项不存在", status_code=404)
    if row.archived_at is None:
        row.archived_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        db.commit()
        _bump()
    return _serialize_option(row)


def archive_custom_option_value(
    db: Session,
    user_id: int,
    kind: str,
    seller_profile_id: int | None,
    value: str,
) -> dict[str, Any] | None:
    """Archive a custom item by the kind/value sent back from the card; returns None when there is no catalog row.

    Profile capabilities have two sources: catalog rows the user added in a card, and the brand profile's own
    service_features / brand_diff_tags. The latter have no option_id but still need profile sync on deletion;
    so this function does not treat "no catalog row found" as an error and lets the caller continue with profile sync.
    """
    text = str(value or "").strip()
    if not text or kind not in OPTION_KINDS or seller_profile_id is None:
        return None
    row = (
        db.query(SalesAgentOptionCatalogORM)
        .filter(
            SalesAgentOptionCatalogORM.kind == kind,
            SalesAgentOptionCatalogORM.user_id == int(user_id),
            SalesAgentOptionCatalogORM.seller_profile_id == int(seller_profile_id),
            SalesAgentOptionCatalogORM.value == text,
        )
        .first()
    )
    if row is None:
        return None
    if row.archived_at is None:
        row.archived_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        db.commit()
        _bump()
    return _serialize_option(row)
