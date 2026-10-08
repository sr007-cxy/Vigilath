"""2026-06-12 — Platform review rule library API (admin).

Each publishing platform has one "review red lines" text, injected into the prompt by the publishing row's platform when generating articles.
Two-step loop:
  1. Static rule library: admin edits manually / AI generates a first draft from public platform guidelines (seed)
  2. Learn from rejections: aggregate the platform's platform_reject_reason, the LLM distills rule increments into
     pending_rules_text, and after admin adoption (approve-pending) they merge into rules_text and take effect

2026-08-04 — added auto-adaptation (services/platform_rules_auto.py): platforms in use but missing from the rule library
automatically get a row + AI first draft (the first version takes effect directly, marked ai_draft pending proofreading); rejection increments for existing platforms still go to pending
for manual adoption. The list therefore also includes platforms "actually in use" (missing-rule ones first), no longer only the hard-coded
default platforms — otherwise new platforms added via multi-platform posting would have **zero guardrails** at generation time with nobody aware.

Endpoints (prefix /admin/platform-rules, require_maintainer = admin | maintainer):
- GET    /                      — platform rule list (default platforms ∪ existing rows ∪ actually in use)
- PUT    /{platform}            — upsert rules_text (can also clear pending; manually adding a platform also uses this)
- DELETE /{platform}            — delete a rule row (platforms in use will be rebuilt by auto-adaptation)
- POST   /{platform}/seed       — LLM generates a first-draft rule set for the platform (returns draft text only, not persisted)
- POST   /learn                 — all platforms: aggregate rejection reasons → LLM distills increments → pending
- POST   /sync                  — one round of auto-adaptation (discover new platforms + first draft + learn increments), same source as cron
- POST   /{platform}/approve-pending — merge pending into rules_text and clear it
"""
import logging
import os
from datetime import datetime
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.auth import require_maintainer
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import PlatformRuleORM, TopicGeneratedDocORM
from geo.services.llm_gateway import post_and_record

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/platform-rules")

# Consistent with admin_review.DEFAULT_PUBLISH_PLATFORMS; default platforms without rows are also returned as empty shells
DEFAULT_PLATFORMS = ["公众号", "小红书", "抖音", "视频号", "搜狐号", "知乎"]

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_TIMEOUT = 120


class PlatformRuleOut(BaseModel):
    platform: str
    rules_text: str = ""
    pending_rules_text: str = ""
    learned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Number of backfilled rejections for this platform (amount of learning material, shown in the frontend)
    rejected_count: int = 0
    # 2026-08-04 — auto-adaptation related (services/platform_rules_auto.py):
    # in_use   is this platform really in use (appeared in publishing plans / placement tasks / produced drafts)
    # usage    source labels of use, e.g. ["发文计划","投放任务"] ("publishing plan", "placement task")
    # ai_draft rules_text is an AI first draft not yet proofread by a human (updated_by_id empty)
    in_use: bool = False
    usage: list[str] = Field(default_factory=list)
    ai_draft: bool = False


class SyncResult(BaseModel):
    """Result of one auto-adaptation round — for the frontend toast."""
    in_use: int = 0
    created: list[str] = Field(default_factory=list)
    seeded: list[str] = Field(default_factory=list)
    learned: list[str] = Field(default_factory=list)
    seed_failed: list[str] = Field(default_factory=list)
    learn_failed: list[str] = Field(default_factory=list)
    skipped_seed: list[str] = Field(default_factory=list)
    rules: list[PlatformRuleOut] = Field(default_factory=list)


class RuleUpdatePayload(BaseModel):
    rules_text: str = Field("", max_length=20000)
    # When true, also clear pending (admin does not want old increments after editing rules)
    clear_pending: bool = False


def _llm_text(prompt: str) -> str:
    """Single-turn plain-text LLM call (deepseek first, fallback openrouter)."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        model = "deepseek-chat"
        headers = {"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"}
    elif or_key:
        url = OPENROUTER_URL
        model = "deepseek/deepseek-chat"
        headers = {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json",
                   "HTTP-Referer": "https://www.vigilath.com",
                   "X-Title": "GEO Platform Rules"}
    else:
        raise HTTPException(503, "DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置")
    # Note: the model here is **hard-coded** deepseek-chat and does not read the DEEPSEEK_MODEL env
    # (inconsistent with other outlets). This is existing behavior left untouched by the accounting refactor — change it in a separate PR.
    r = post_and_record(url, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }, headers, provider="deepseek" if ds_key else "openrouter",
        feature="platform_rules", timeout=LLM_TIMEOUT, model=model)
    if r.status_code >= 400:
        raise HTTPException(502, f"LLM HTTP {r.status_code}: {r.text[:200]}")
    try:
        return str(r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"unexpected LLM response: {e}")


def _rejected_counts(db: Session) -> dict[str, int]:
    from sqlalchemy import func
    rows = (
        db.query(TopicGeneratedDocORM.platform, func.count(TopicGeneratedDocORM.id))
          .filter(TopicGeneratedDocORM.platform_review_status == "rejected")
          .group_by(TopicGeneratedDocORM.platform)
          .all()
    )
    return {str(p or ""): int(n) for p, n in rows}


def _to_out(row: PlatformRuleORM | None, platform: str, rejected: int,
            usage: Optional[set[str]] = None) -> PlatformRuleOut:
    usage_list = sorted(usage or set())
    if row is None:
        return PlatformRuleOut(platform=platform, rejected_count=rejected,
                              in_use=bool(usage_list), usage=usage_list)
    return PlatformRuleOut(
        platform=row.platform,
        rules_text=row.rules_text or "",
        pending_rules_text=row.pending_rules_text or "",
        learned_at=row.learned_at,
        updated_at=row.updated_at,
        rejected_count=rejected,
        in_use=bool(usage_list),
        usage=usage_list,
        # Has rules but never edited by a human → system-generated first draft, not yet proofread
        ai_draft=bool((row.rules_text or "").strip()) and row.updated_by_id is None,
    )


def _list_rules(db: Session) -> list[PlatformRuleOut]:
    """Rule list = default platforms ∪ existing rows ∪ **platforms actually in use** (those missing rules must show too)."""
    rows = {r.platform: r for r in db.query(PlatformRuleORM).all()}
    rejected = _rejected_counts(db)
    try:
        from geo.services.platform_rules_auto import discover_platforms
        in_use = discover_platforms(db)
    except Exception:  # noqa: BLE001
        log.warning("discover platforms failed — list falls back to defaults + existing rows", exc_info=True)
        in_use = {}
    seen: list[str] = []
    for p in list(DEFAULT_PLATFORMS) + sorted(in_use) + sorted(rows):
        if p not in seen:
            seen.append(p)
    # In-use platforms first (especially "in use but no rules yet", the most urgent), then default platform order
    def sort_key(p: str) -> tuple:
        row = rows.get(p)
        has_rules = bool((row.rules_text or "").strip()) if row else False
        used = p in in_use
        return (0 if used and not has_rules else 1 if used else 2,
                DEFAULT_PLATFORMS.index(p) if p in DEFAULT_PLATFORMS else 99, p)
    return [_to_out(rows.get(p), p, rejected.get(p, 0), in_use.get(p))
            for p in sorted(seen, key=sort_key)]


@router.get("", response_model=list[PlatformRuleOut])
def list_platform_rules(
    _admin = Depends(require_maintainer),
    db: Session = Depends(get_db),
):
    return _list_rules(db)


@router.post("/sync", response_model=SyncResult)
def sync_platform_rules(
    _admin = Depends(require_maintainer),
    db: Session = Depends(get_db),
):
    """One auto-adaptation round (manual-click version, same service as cron):

    platforms in use but missing from the rule library → create row + AI first draft (first version takes effect directly, marked unproofread);
    platforms with rejection reasons → distill rule increments into pending for adoption.
    """
    from geo.services import platform_rules_auto
    stat = platform_rules_auto.sync_tick(db)
    return SyncResult(**{**stat, "rules": _list_rules(db)})


@router.delete("/{platform}", response_model=dict)
def delete_platform_rule(
    platform: str,
    _admin = Depends(require_maintainer),
    db: Session = Depends(get_db),
):
    """Delete a platform's rule row (cleanup for mistakenly created rows / platforms no longer used for placement).

    A platform still in use will be rediscovered by auto-adaptation and get a new first draft — which is correct: in use means it needs guardrails.
    """
    row = db.query(PlatformRuleORM).filter(PlatformRuleORM.platform == platform.strip()).first()
    if row is None:
        raise HTTPException(404, "该平台没有规则行")
    db.delete(row)
    db.commit()
    log.info("admin deleted platform rule: %s", platform)
    return {"deleted": platform}


def _get_or_create(db: Session, platform: str) -> PlatformRuleORM:
    platform = platform.strip()
    if not platform or len(platform) > 32:
        raise HTTPException(422, "invalid platform")
    row = db.query(PlatformRuleORM).filter(PlatformRuleORM.platform == platform).first()
    if row is None:
        row = PlatformRuleORM(platform=platform)
        db.add(row)
    return row


@router.put("/{platform}", response_model=PlatformRuleOut)
def update_platform_rule(
    platform: str,
    payload: RuleUpdatePayload,
    admin = Depends(require_maintainer),
    db: Session = Depends(get_db),
):
    row = _get_or_create(db, platform)
    row.rules_text = (payload.rules_text or "").strip()
    if payload.clear_pending:
        row.pending_rules_text = ""
    row.updated_at = datetime.utcnow()
    row.updated_by_id = admin.id
    db.commit()
    db.refresh(row)
    return _to_out(row, row.platform, _rejected_counts(db).get(row.platform, 0))


@router.post("/{platform}/seed", response_model=dict)
def seed_platform_rule(
    platform: str,
    _admin = Depends(require_maintainer),
):
    """LLM generates a first-draft rule set from public platform guidelines — returns the draft only; the frontend fills the editor for admin to proofread and save."""
    platform = platform.strip()
    if not platform:
        raise HTTPException(422, "invalid platform")
    draft = _llm_text(
        f"你是内容平台合规专家。请整理「{platform}」平台对图文内容的审核红线清单,"
        f"供 AI 写稿时遵守(违反任何一条稿件会被平台拒审)。\n"
        f"涵盖维度:广告法用语(绝对化用语/功效承诺)、外链与联系方式政策、"
        f"诱导关注与导流限制、标题规范、敏感领域(医疗/金融/教育)表述、"
        f"图片与素材要求、字数与排版习惯。\n"
        f"输出要求:每条一行,以「- 」开头,只写可执行的禁止/必须项,"
        f"不超过 20 条,不要解释,不要标题。"
    )
    return {"platform": platform, "draft": draft}


@router.post("/learn", response_model=list[PlatformRuleOut])
def learn_from_rejections(
    _admin = Depends(require_maintainer),
    db: Session = Depends(get_db),
):
    """All platforms: aggregate backfilled platform rejection reasons → LLM distills rule increments → write to pending for adoption.

    Only platforms with rejection reasons are processed; increments are given to the LLM together with the existing rules_text, asking it to output only
    new entries not covered by existing rules, to avoid duplicate buildup.
    """
    docs = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.platform_review_status == "rejected")
          .filter(TopicGeneratedDocORM.platform_reject_reason.isnot(None))
          .all()
    )
    by_platform: dict[str, list[str]] = {}
    for d in docs:
        p = (d.platform or "").strip()
        reason = (d.platform_reject_reason or "").strip()
        if p and reason:
            by_platform.setdefault(p, []).append(reason)

    out: list[PlatformRuleOut] = []
    rejected = {p: len(v) for p, v in by_platform.items()}
    for platform, reasons in by_platform.items():
        row = _get_or_create(db, platform)
        uniq = list(dict.fromkeys(reasons))[-50:]   # Dedupe, take at most the latest 50
        bullets = "\n".join(f"- {s[:300]}" for s in uniq)
        existing = (row.rules_text or "").strip() or "(暂无)"
        increment = _llm_text(
            f"你是内容平台合规专家。下面是「{platform}」平台真实的拒稿原因记录,"
            f"请提炼成可执行的审核规则条目,供 AI 写稿时遵守。\n\n"
            f"【现有规则(已生效,不要重复输出)】\n{existing}\n\n"
            f"【拒稿原因记录】\n{bullets}\n\n"
            f"输出要求:只输出现有规则没覆盖的新规则,每条一行以「- 」开头,"
            f"合并同类原因,不超过 10 条;没有新规则就输出「(无新增)」。"
        )
        if increment and "(无新增)" not in increment:
            row.pending_rules_text = increment.strip()
        row.learned_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        out.append(_to_out(row, platform, rejected.get(platform, 0)))
    if not out:
        raise HTTPException(404, "没有任何平台有已回填的拒稿原因,无可学习素材")
    return out


@router.post("/{platform}/approve-pending", response_model=PlatformRuleOut)
def approve_pending(
    platform: str,
    admin = Depends(require_maintainer),
    db: Session = Depends(get_db),
):
    row = db.query(PlatformRuleORM).filter(PlatformRuleORM.platform == platform.strip()).first()
    if not row or not (row.pending_rules_text or "").strip():
        raise HTTPException(404, "该平台没有待采纳的规则增量")
    base = (row.rules_text or "").strip()
    row.rules_text = (base + "\n" + row.pending_rules_text.strip()).strip()
    row.pending_rules_text = ""
    row.updated_at = datetime.utcnow()
    row.updated_by_id = admin.id
    db.commit()
    db.refresh(row)
    return _to_out(row, row.platform, _rejected_counts(db).get(row.platform, 0))
