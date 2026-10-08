"""AI automatic media selection -- after a draft is approved, the system automatically picks placement media/platforms from attribution recommendations.

Decision inputs (highest priority first):
  1. Historically effective: media this topic placed on that were cited by AI (publish_attribution) -> reuse directly, no LLM call
  2. LLM selection: source strategy recommendations (source_strategy) + candidate pool (price <= cap, publish rate descending,
     industry dimension match) -> DeepSeek picks media + <= 3 free channels, with reasons
  3. Rule fallback: when the LLM is unavailable, greedy bin packing by cost-effectiveness

**Pick a set within budget, not just one** (2026-08-26): previously one draft picked only 1 media outlet, but the
effect of a placement depends on coverage -- if one outlet publishes and the other does not, the whole article is wasted. Now 1-3 are picked within budget,
**at most 1 per platform** (putting everything on Sohu at once carries a high indexing risk on that platform).

Budget is the minimum of three: `RWJ_AUTO_BUDGET_CENTS`, the topic owner's wallet balance, unit price cap x article count.
**Taking the min with the balance is required** -- picking a set the user cannot afford is pointless; tasks created would never go out.

**Free first, then paid**: the own site and manual platforms cost nothing and are scheduled first; remaining budget then picks paid media.

The red line is unchanged: automatic actions stop at "push drafts (free) + create planned tasks", **never charging automatically**.
Switch RWJ_AUTO_SELECT_ENABLED; unit price cap RWJ_AUTO_PRICE_CAP_YUAN.
Entry schedule_auto_select(doc_id): runs in a daemon thread without blocking the review request.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional

import requests

from geo.database import SessionLocal, settings
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, AiTelemetryTopicSourceStrategyORM,
    TopicDocPublishTaskORM, TopicGeneratedDocORM,
)
from geo.services.llm_gateway import post_and_record

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT = 60

# Allowlist of manual platforms the AI may pick (aligned with frontend platformCatalog)
MANUAL_PLATFORMS = ("公众号", "知乎", "小红书", "微博", "抖音", "视频号",
                    "搜狐号", "百家号", "头条号")
# Max free channels the LLM picks at once. Raised from 2 to 3: free channels cost nothing, so one more is pure gain.
MAX_MANUAL = 3
# Max paid media picked at once (any more should be planned manually)
MAX_PAID = 3


# Own site (website blog): zero cost, no external account dependency, scheduled by default in automatic media selection.
# To turn off: GEO_AUTO_OWN_BLOG=0 (e.g. customers whose site is not built yet).
OWN_BLOG_PLATFORM = "官网博客"


def _own_blog_enabled() -> bool:
    return (os.environ.get("GEO_AUTO_OWN_BLOG", "1") or "").strip().lower() \
        not in ("0", "false", "no")


def _auto_budget_cents() -> int:
    """Budget cap for automatic media selection (cents). Default = unit price cap x MAX_PAID."""
    raw = (os.environ.get("RWJ_AUTO_BUDGET_CENTS") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return int(float(settings.RWJ_AUTO_PRICE_CAP_YUAN) * 100) * MAX_PAID


def schedule_auto_select(doc_id: int, *, actor_id: Optional[int] = None) -> None:
    """Called by the review hook: runs automatic media selection in a background thread; any exception is only logged."""
    if not settings.RWJ_AUTO_SELECT_ENABLED:
        return
    threading.Thread(target=_worker, args=(doc_id, actor_id), daemon=True).start()


def _worker(doc_id: int, actor_id: Optional[int]) -> None:
    db = SessionLocal()
    try:
        doc = db.get(TopicGeneratedDocORM, doc_id)
        if doc is None or doc.status not in ("approved", "published"):
            return
        topic = db.get(AiTelemetryTopicORM, doc.topic_id)
        if topic is None:
            return
        from geo.services import ruanwenjiang_publisher as rp
        # Idempotent: if the draft already has a Ruanwenjiang task, do not auto-select again
        existing = (db.query(TopicDocPublishTaskORM)
                      .filter(TopicDocPublishTaskORM.doc_id == doc.id,
                              TopicDocPublishTaskORM.platform.in_(rp.PLATFORM_NAMES),
                              TopicDocPublishTaskORM.status != "cancelled").first())
        if existing is not None:
            return
        choice = _decide(db, doc, topic)
        if choice is None:
            log.info("[media-select] doc=%s no selectable media (channel not configured or candidates empty)", doc.id)
            return
        _apply_choice(db, doc, topic, choice, actor_id=actor_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("[media-select] doc=%s automatic media selection failed", doc_id)
    finally:
        db.close()


# -- Decision -------------------------------------------------------------------

def _decide(db, doc, topic) -> Optional[dict]:
    """Returns {provider, resource: {resource_id,title,price}, manual_platforms, reason, source}.

    provider must travel along -- resource ids are not portable across sites; without it, provider A's id would be used to push drafts on provider B.
    """
    from geo.services.rwj_client import SITES, get_client

    # Use the first site with configured credentials (Ruanwenjiang by default); if Ruanwenjiang is not configured fall back to Meijieku, do not give up outright
    c = next((x for x in (get_client(p) for p in SITES) if x.configured), None)
    if c is None:
        return None
    choice = _decide_on(db, topic, c)
    return {**choice, "provider": c.provider} if choice else None


def _decide_on(db, topic, c) -> Optional[dict]:
    from geo.services.publish_attribution import compute_publish_attribution

    budget = _budget_for(db, topic)

    # 1) Reuse historically effective media directly (no LLM call). By citation effectiveness descending, as many as fit.
    attribution = compute_publish_attribution(db, topic.id)
    proven_rows = sorted(
        (r for r in attribution["rows"] if r["media"] and r["cited_docs"] > 0),
        key=lambda r: (-r["cited_rate"], -r["cited_docs"]))
    proven: list[dict] = []
    spent = 0
    seen_platforms: set[str] = set()
    for row in proven_rows:
        if len(proven) >= MAX_PAID:
            break
        res = _find_proven_resource(db, topic, row["media"], c)
        if res is None:
            continue
        cents = int(round(float(res["price"]) * 100))
        if spent + cents > budget:
            continue
        plat = res.get("platform") or res["title"]
        if plat in seen_platforms:
            continue
        seen_platforms.add(plat)
        spent += cents
        res["_why"] = (f"历史投放被 AI 引用 {row['cited_docs']}/{row['published_docs']} 篇")
        proven.append(res)
    if proven:
        return {"resources": proven, "manual_platforms": [],
                "reason": f"复用 {len(proven)} 家历史上被 AI 引用过的媒体",
                "source": "attribution"}

    # 2) Candidate pool: industry dimension + generic, price <= cap, publish rate descending
    candidates = _build_candidates(c, topic)
    if not candidates:
        return None

    # 3) LLM selection (falls back to rules on failure)
    recs = _strategy_recs(db, topic.id)
    try:
        pick = _llm_pick(topic, recs, candidates, budget)
        if pick:
            return pick
    except Exception as e:  # noqa: BLE001
        log.warning("[media-select] LLM failed, falling back to rules: %s", e)
    chosen = _pick_set(candidates, budget)
    if not chosen:
        return None
    return {"resources": chosen, "manual_platforms": [],
            "reason": (f"规则兜底:按性价比(出稿率/价格)在预算 ¥{budget / 100:.0f} 内"
                       f"挑了 {len(chosen)} 家"),
            "source": "rules"}


def _budget_for(db, topic) -> int:
    """How much this article can spend (cents).

    Takes **the min with the topic owner's wallet balance** -- picking a set the user cannot afford is pointless,
    tasks created would never go out and would need manual cleanup. Operator topics are not constrained by balance.
    """
    cap = _auto_budget_cents()
    try:
        from geo.services import topic_billing

        uid = topic_billing.payer_id(db, topic)
        if uid is None:
            return cap                       # Operator topic / owner deleted, only constrained by env
        from geo.services import balance_service

        return max(0, min(cap, balance_service.get_balance(db, uid)))
    except Exception:  # noqa: BLE001 -- if the balance cannot be read, only apply the env constraint; do not block media selection
        return cap


def _pick_set(candidates: list[dict], budget_cents: int,
              max_count: int = MAX_PAID) -> list[dict]:
    """Greedy bin packing: by cost-effectiveness descending, fit as many as the budget allows.

    **At most 1 per platform** -- putting all three articles on Sohu at once carries a high indexing risk on that platform,
    and three same-source links are worth far less to the AI citation pool than three different sites.
    """
    def _rate(x) -> float:
        try:
            return float(str(x.get("publish_rate") or 0).rstrip("%"))
        except ValueError:
            return 0.0

    def _cents(x) -> int:
        try:
            return int(round(float(x["price"]) * 100))
        except (TypeError, ValueError, KeyError):
            return 0

    ranked = sorted(candidates,
                    key=lambda x: (-(_rate(x) / max(_cents(x), 1)), _cents(x)))
    out: list[dict] = []
    spent = 0
    used_platforms: set[str] = set()
    for x in ranked:
        if len(out) >= max_count:
            break
        cents = _cents(x)
        if cents <= 0 or spent + cents > budget_cents:
            continue
        plat = (x.get("platform") or x.get("channel") or x.get("title") or "").strip()
        if plat and plat in used_platforms:
            continue
        used_platforms.add(plat)
        spent += cents
        out.append(x)
    return out


def _find_proven_resource(db, topic, media_name: str, c) -> Optional[dict]:
    """Historically effective media -> orderable (provider, resource_id, price).

    **Look up our own placement tasks first**, not upstream: an attribution row's `media` comes from the task's `media_name` anyway,
    so mapping it back to its own task is an **identity mapping**, with no same-name ambiguity and zero API calls.

    The old approach took the first same-named result from `c.list_resources("Website", title=..., pageSize=10)` --
    two pitfalls: same-named media get picked wrong (same-named sites with different channels/prices are common), and Website was hardcoded,
    so historical media of the self-media type could not be found at all.

    Only when that lookup fails fall back to the mirror table (scoped by res_type + provider, not a bare name search).
    """
    from geo.models.ai_telemetry import TopicDocPublishTaskORM
    from geo.services import ruanwenjiang_publisher as rp

    row = (db.query(TopicDocPublishTaskORM)
             .filter(TopicDocPublishTaskORM.topic_id == topic.id,
                     TopicDocPublishTaskORM.media_name == media_name,
                     TopicDocPublishTaskORM.platform.in_(rp.PLATFORM_NAMES),
                     TopicDocPublishTaskORM.status == "published")
             .order_by(TopicDocPublishTaskORM.id.desc()).first())
    if row is not None:
        try:
            meta = json.loads(row.channel_meta_json or "{}")
        except (TypeError, ValueError):
            meta = {}
        rid, price = meta.get("resource_id"), meta.get("quoted_price")
        if rid and price is not None:
            return {"resource_id": int(rid), "title": media_name,
                    "price": str(price), "res_type": str(meta.get("type") or "Website"),
                    # provider must follow history -- wherever it was published before is where it stays,
                    # ids are not portable across sites
                    "provider": meta.get("provider") or rp.provider_of(row.platform),
                    "platform": media_name}

    # Fallback: mirror table. **Fetch with res_type and provider together**, no bare name matching.
    try:
        from geo.models.rwj_media import RwjMediaResourceORM as R
        from geo.services.media_catalog import _title_key

        hit = (db.query(R)
                 .filter(R.title_key == _title_key(media_name), R.is_active.is_(True),
                         R.ref_price_cents.isnot(None))
                 .order_by(R.ref_price_cents.asc()).first())
        if hit is not None:
            return {"resource_id": int(hit.resource_id), "title": hit.title,
                    "price": f"{(hit.ref_price_cents or 0) / 100:.2f}",
                    "res_type": hit.res_type, "provider": hit.provider or "rwj",
                    "platform": hit.platform or hit.title}
    except Exception:  # noqa: BLE001
        log.warning("[media-select] mirror table lookup failed: %s", media_name, exc_info=True)
    return None


def _build_candidates(c, topic) -> list[dict]:
    """Ruanwenjiang candidate pool: one batch matched by industry dimension + one generic batch, price <= cap, deduped by publish rate descending."""
    from geo.services.rwj_client import pick_delivery, resource_price, sort_expr, RwjApiError

    cap = float(settings.RWJ_AUTO_PRICE_CAP_YUAN)
    info = c.ensure_session()
    level = int(info.get("level") or 1)

    batches: list[dict] = []
    field_1 = _match_industry_field(c, topic)
    queries: list[dict] = []
    if field_1:
        queries.append({"field_1": field_1})
    queries.append({})   # Generic batch
    seen: set[int] = set()
    for extra in queries:
        try:
            page = c.list_resources(
                "Website", pageSize=30, price_max=str(int(cap)),
                sort=sort_expr("rate_desc", level), **extra)
        except RwjApiError:
            continue
        for r in page.get("data") or []:
            rid = r.get("resource_id")
            if rid in seen:
                continue
            try:
                d = pick_delivery(r)
                price = float(resource_price(r, level, delivery=d))
            except RwjApiError:
                continue
            if price > cap:
                continue
            seen.add(rid)
            batches.append({
                "resource_id": int(rid), "title": str(r.get("title") or ""),
                "price": f"{price:.2f}",
                "channel": r.get("field_1_name"), "region": r.get("field_3_name"),
                "publish_rate": r.get("publish_rate"), "pc_weigh": r.get("pc_weigh"),
                "remarks": str(r.get("remarks") or "")[:40],
            })
    # Publish rate descending, price ascending on ties
    def _rate(x):
        try:
            return float(str(x.get("publish_rate") or 0).rstrip("%"))
        except ValueError:
            return 0.0
    batches.sort(key=lambda x: (-_rate(x), float(x["price"])))
    return batches[:30]


def _match_industry_field(c, topic) -> Optional[int]:
    """topic.industry text -> Ruanwenjiang "industry field" (field_1) option id; returns None if no match."""
    try:
        init = c.base_init()
        opts = ((init.get("field") or {}).get("website") or {}).get("field_1") or []
        hay = f"{topic.industry or ''}{topic.industry_class or ''}{topic.name or ''}"
        for o in opts:
            t = str(o.get("field_title") or "")
            if t and len(t) >= 2 and any(ch in hay for ch in (t, t[:2])):
                if t[:2] in hay or t in hay:
                    return int(o["field_id"])
    except Exception:  # noqa: BLE001
        pass
    return None


def _strategy_recs(db, topic_id: int) -> list[dict]:
    row = (db.query(AiTelemetryTopicSourceStrategyORM)
             .filter(AiTelemetryTopicSourceStrategyORM.topic_id == topic_id).one_or_none())
    if row is None or row.status != "ready":
        return []
    try:
        recs = json.loads(row.recommendations_json or "[]")
        return recs[:6] if isinstance(recs, list) else []
    except ValueError:
        return []


def _llm_pick(topic, recs: list[dict], candidates: list[dict],
              budget_cents: int) -> Optional[dict]:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    cand_lines = "\n".join(
        f"{x['resource_id']} | {x['title']} | ¥{x['price']} | {x.get('channel') or '-'} | "
        f"出稿率{x.get('publish_rate') or '-'} | 权重{x.get('pc_weigh') or '-'} | {x.get('remarks') or ''}"
        for x in candidates)
    rec_lines = "\n".join(
        f"[{r.get('priority')}] {r.get('platform')} — {str(r.get('reason') or '')[:60]}"
        for r in recs) or "(暂无,按行业与候选质量判断)"
    system = (
        f"你是 GEO 投放策划。任务:为一篇已过审的品牌内容,在预算 ¥{budget_cents / 100:.0f} 内"
        f"选定 1-{MAX_PAID} 个付费发稿媒体,"
        "目标是让内容尽快进入 AI 搜索引擎(DeepSeek/豆包/通义等)的引用池。"
        "优先考虑:与品牌行业相关、备注含收录/新闻源、出稿率高、权重高;同等条件选低价。"
        "**同一个平台/频道最多选 1 个** —— 三条同源链接对引用池的价值远不如三个不同站点。"
        "合计价格不得超过预算。"
        f"另可从这份平台列表中挑最多 {MAX_MANUAL} 个适合该内容的免费渠道:"
        f"{'、'.join(MANUAL_PLATFORMS)}(免费渠道不占预算,能挑就挑)。"
        '只输出 JSON:{"resource_ids": [<候选表中的编号>, ...], "reason": "<50字内选择理由>", '
        '"manual_platforms": ["平台名", ...]}'
    )
    user = (
        f"品牌/主题:{topic.target or topic.name}(行业:{topic.industry or '未知'})\n\n"
        f"历史归因投放建议:\n{rec_lines}\n\n"
        f"软文匠候选媒体(编号|名称|价格|频道|出稿率|权重|备注):\n{cand_lines}"
    )
    r = post_and_record(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        {"model": DEEPSEEK_MODEL, "temperature": 0.2,
         "response_format": {"type": "json_object"},
         "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": user}]},
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        provider="deepseek", feature="media_select", timeout=LLM_TIMEOUT)
    r.raise_for_status()
    out = json.loads(r.json()["choices"][0]["message"]["content"])
    manual = [p for p in (out.get("manual_platforms") or []) if p in MANUAL_PLATFORMS][:MAX_MANUAL]
    reason = str(out.get("reason") or "")[:100]

    # Handle the model still emitting a singular resource_id (changing the prompt does not mean the model follows immediately)
    raw_ids = out.get("resource_ids")
    if raw_ids is None and out.get("resource_id") is not None:
        raw_ids = [out["resource_id"]]
    ids: list[int] = []
    for x in (raw_ids or []):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue

    by_id = {x["resource_id"]: x for x in candidates}
    picked = [by_id[i] for i in ids if i in by_id]
    # **Budget and same-platform dedup are enforced by code**, never trusting the model's arithmetic -- it goes over budget and picks two from the same channel
    picked = _pick_set(picked, budget_cents) if picked else []
    if not picked:
        if manual:
            return {"resources": [], "manual_platforms": manual,
                    "reason": reason or "AI 判断当前候选无合适付费媒体,只走免费渠道",
                    "source": "llm"}
        return None
    return {"resources": picked, "manual_platforms": manual,
            "reason": reason or "AI 按归因推荐选定", "source": "llm"}


# -- Landing --------------------------------------------------------------------

def _apply_choice(db, doc, topic, choice: dict, *, actor_id: Optional[int]) -> None:
    """Turn the decision into tasks. **Free first, then paid** -- free channels cost nothing and are scheduled first without using budget.

    The red line is unchanged: stop at "push drafts + create planned tasks", **never charging automatically**.
    """
    from geo.services import compliance_audit, publish_task_service
    from geo.services import ruanwenjiang_publisher as rp
    from geo.services.compliance_scanner import publish_gate

    resources = choice.get("resources") or []

    # The compliance gate runs only once: the verdict is the same for one draft across all media; scanning per outlet is slow and
    # would flood compliance_audit with duplicate rows
    if resources:
        ok, reason = publish_gate(db, doc, topic, hook="publish_paid_media",
                                  actor_id=actor_id, actor_name="AI自动选媒")
        if not ok:
            db.commit()
            log.warning("[media-select] doc=%s blocked by compliance, no paid media placed: %s", doc.id, reason)
            resources = []

    # -- Free first: manual platforms + own site ------------------------------
    manual = list(choice.get("manual_platforms") or [])
    if _own_blog_enabled() and OWN_BLOG_PLATFORM not in manual:
        # Own site costs nothing and has no external account dependency, no reason not to schedule it
        manual.append(OWN_BLOG_PLATFORM)
    if manual:
        publish_task_service.create_tasks(
            db, doc, topic, [{"platform": p} for p in manual],
            assigned_to_user_id=actor_id)
        log.info("[media-select] doc=%s -> free channel tasks: %s", doc.id, "、".join(manual))

    # -- Then paid ------------------------------------------------------------
    for res in resources:
        provider = res.get("provider") or choice.get("provider") or rp.DEFAULT_PROVIDER
        platform_label = rp.platform_name(provider)
        res_type = str(res.get("res_type") or "Website")
        meta: dict[str, Any] = {
            "channel": provider, "provider": provider, "type": res_type,
            "resource_id": res["resource_id"], "resource_title": res["title"],
            "quoted_price": res["price"],
            "auto_selected": True, "select_source": choice["source"],
            "select_reason": res.get("_why") or choice["reason"],
            "quoted_at": datetime.utcnow().isoformat(),
        }
        last_error = None
        try:
            result = rp.push_draft(doc, type_=res_type, provider=provider,
                                   resource_id=res["resource_id"], resource_title=res["title"])
            meta.update({"draft_id": result.draft_id, "delivery": result.delivery,
                         "quoted_price": result.price, "quoted_level": result.level,
                         "dropped_images": result.dropped_images})
        except Exception as e:  # noqa: BLE001 -- one outlet failing to push should not drag down the others
            last_error = f"自动推草稿失败:{e}"
        tasks = publish_task_service.create_tasks(
            db, doc, topic,
            [{"platform": platform_label, "media_name": res["title"],
              "channel_type": "external_media", "publish_mode": "paid_media"}],
            assigned_to_user_id=actor_id, initial_status="planned", channel_meta=meta)
        if last_error and tasks:
            tasks[0].last_error = last_error
        if tasks:
            compliance_audit.record(
                db, topic_id=topic.id, event_type="paid_publish",
                object_type="publish_task", object_id=tasks[0].id,
                actor_type="system", actor_name="AI自动选媒",
                detail={"action": "auto_select", "source": choice["source"],
                        "resource_id": res["resource_id"], "resource_title": res["title"],
                        "price": res["price"], "reason": choice["reason"]})
        log.info("[media-select] doc=%s → %s %s(¥%s)%s | %s",
                 doc.id, platform_label, res["title"], res["price"],
                 " [草稿失败]" if last_error else "", choice["reason"])

    db.commit()
