"""Ops workspace (admin line) tools — plan management / article management / placement strategy / source analysis / article placement.

Parallel to tools.py (customer self-service line), **never loaded together**: agent.py's _line_tools("admin") is an allowlist
without _GEO_WRITE; conversely the geo/sentiment/both lines include no tool from this file. Isolation is a mechanism, not a prompt.

Three implementation conventions (new tools must follow them; eval/test_admin_scope.py asserts them):
  1. The first parameter is always ctx, and the signature **never contains topic_id / account_id / user_id** —
     the project is locked by the backend (scope.admin_topic takes deps.locked_topic_id); if the model can't fill it, it can't switch projects.
  2. **Hand-trim return bodies to around 2KB**; never model_dump() a TopicExecutionPlanOut directly
     (150+ rows of publishing_plan + cell-level progress + three snapshots) or the full build_placement_strategy.
     The admin line mounts ~30 schemas per turn; add untrimmed return bodies and context blows up by turn 3-4,
     showing up as "the agent suddenly can't do anything" + a markedly higher wrong-tool rate.
  3. Every tool that accepts a model-supplied id must have scope.own_* as the first line of its body.

Metric definitions: all six metrics go through the three reused report-center tools in tools.py (get_report_overview /
get_period_report / list_report_periods); **this file computes no metrics**.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from pydantic_ai import RunContext

from pydantic import BaseModel
from pydantic_ai import ModelRetry

from agents.geo.audit import record_agent_action
from agents.geo.confirm import guard, needs_confirm
from agents.geo.deps import AgentDeps
from agents.geo.scope import (
    admin_actor, admin_topic, call_route, own_doc, own_doc_ids, own_plan,
    own_plan_item, own_task, route_failed,
)

# Defaults and hard caps for list returns (server-side cap: no larger limit from the model gets through)
_MAX_ROWS = 40
_MAX_TEXT = 300


def _max_batch() -> int:
    """Per-call batch cap. Exceeding it is rejected outright, not even a preview — guards against mistakes like "generate the whole year's plan at once"."""
    try:
        return max(1, int(os.environ.get("AGENT_ADMIN_MAX_BATCH", "30")))
    except (TypeError, ValueError):
        return 30


def _clip(s, n: int = _MAX_TEXT) -> str:
    t = str(s or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _cap(n, default: int, hi: int = _MAX_ROWS) -> int:
    try:
        v = int(n)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, hi))


def _jload(s, dflt):
    try:
        v = json.loads(s or "")
    except Exception:  # noqa: BLE001
        return dflt
    return v if isinstance(v, type(dflt)) else dflt


def _plan_rows(plan) -> list[dict]:
    rows = _jload(plan.publishing_plan_json, [])
    return [r for r in rows if isinstance(r, dict)]


def _row_platform(r: dict) -> str:
    return str(r.get("platform") or (r.get("channel") or "") or "")


def _row_extra_platforms(r: dict) -> list[str]:
    """The "also publish to" platforms of a plan row.

    A row can publish to a primary platform + several extra platforms (8 rows of live topic 20 are WeChat OA + official blog).
    _row_platform returns only the primary platform, so these extra destinations are neither shown nor filterable — for an agent whose
    job is answering "where does this row publish", missing the second destination means a confidently wrong answer.

    ★ Do not merge this into _row_platform: that function is also used by the by_platform stats; merging would make
      the per-platform counts sum to more than the total plan rows, and report metric definitions would diverge on the spot.
    """
    v = r.get("extra_platforms")
    if not v:
        return []
    return [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]


# ── Project anchor ─────────────────────────────────────────────────────────
async def admin_topic_overview(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 Full picture of the project currently being operated: customer / stage / batch run status / plan status / article funnel / placement funnel.

    This is the ops console's "where are you" anchor. **Call it once to confirm the project before any write action**;
    don't answer with an old project name remembered from conversation history (the project may have been renamed).
    """
    from geo.models.ai_telemetry import (
        AiTelemetryTopicExecutionPlanORM as PlanORM,
        TopicDocPublishTaskORM,
        TopicGeneratedDocORM,
    )
    from sqlalchemy import func

    t = admin_topic(ctx)
    db = ctx.deps.db

    doc_funnel = dict(
        db.query(TopicGeneratedDocORM.status, func.count(TopicGeneratedDocORM.id))
        .filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))   # Soft-deleted ones are not counted
        .group_by(TopicGeneratedDocORM.status).all()
    )
    task_funnel = dict(
        db.query(TopicDocPublishTaskORM.status, func.count(TopicDocPublishTaskORM.id))
        .filter(TopicDocPublishTaskORM.topic_id == t.id)
        .group_by(TopicDocPublishTaskORM.status).all()
    )
    plan = (
        db.query(PlanORM).filter(PlanORM.topic_id == t.id)
        .order_by(PlanORM.id.desc()).first()
    )
    rows = _plan_rows(plan) if plan is not None else []

    return {
        "topic_id": t.id,
        "topic_name": t.name,
        "客户user_id": t.user_id,
        "官网": t.target or "",
        "行业": t.industry or "",
        "审核状态": t.submission_status or "",
        "最近跑批时间": t.last_run_at,
        "自动生成": bool(getattr(t, "auto_generate_enabled", False)),
        "自动发布": bool(getattr(t, "auto_publish_enabled", False)),
        "计划状态": (plan.status if plan is not None else "无计划"),
        "计划行总数": len(rows),
        "文章漏斗": {str(k): int(v) for k, v in doc_funnel.items()},
        "投放漏斗": {str(k): int(v) for k, v in task_funnel.items()},
    }


# ── Plan management ────────────────────────────────────────────────────────
async def admin_get_plan(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 Status and overview of this project's execution plan (draft/confirmed, scheduling window, total rows, drafted/undrafted).

    Summary only, no per-row details — use admin_list_plan_items to see rows.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    t = admin_topic(ctx)
    plan = own_plan(ctx)
    rows = _plan_rows(plan)
    dates = sorted(str(r.get("publish_date") or "") for r in rows if r.get("publish_date"))

    linked = {
        str(x[0]) for x in ctx.deps.db.query(TopicGeneratedDocORM.plan_item_id)
        .filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))   # Soft-deleted ones don't count as drafted
        .filter(TopicGeneratedDocORM.plan_item_id.isnot(None)).all()
    }
    done = sum(1 for r in rows if str(r.get("id") or "") in linked)
    by_platform: dict[str, int] = {}
    for r in rows:
        by_platform[_row_platform(r) or "未指定"] = by_platform.get(_row_platform(r) or "未指定", 0) + 1

    overview = _jload(plan.overview_json, {})
    return {
        "topic_name": t.name,
        "plan_id": plan.id,
        "计划状态": plan.status or "",
        "排期起": dates[0] if dates else "",
        "排期止": dates[-1] if dates else "",
        "计划行总数": len(rows),
        "已出稿行数": done,
        "未出稿行数": max(0, len(rows) - done),
        "按平台分布": dict(sorted(by_platform.items(), key=lambda kv: -kv[1])[:12]),
        "确认时间": plan.confirmed_at,
        "最近编辑": plan.last_edited_at,
        "计划生成时间": overview.get("generated_at") or "",
        "有排期依据": bool((overview.get("plan_basis") or {})),
    }


async def admin_list_plan_items(ctx: RunContext[AgentDeps], days: int = 7,
                                only_pending: bool = False, limit: int = 20,
                                date_from: str = "", date_to: str = "",
                                platform: str = "", seed_contains: str = "",
                                offset: int = 0) -> dict:
    """【读】 List publishing plan rows (item_id / date / seed / platform / media / template / draft status).

    item_id is a uuid, **quote it verbatim**; it is the only addressing key for all later plan writes (edit / delete / reorder rows).

    By default only shows the next `days` days **from today** (1–90). To see **past-due rows** you must pass date_from explicitly —
    that is the only way to find past-due undrafted rows in a large plan.
      days:          days ahead from today (ignored if date_from/date_to is given)
      date_from/to:  YYYY-MM-DD, inclusive; if only one is given the other end is open
      platform:      filter by platform (substring match, e.g. 「知乎」 ("Zhihu"), 「软文匠」 ("Ruanwenjiang"));
                     both the primary platform and the "also publish to" extra platforms count as a match
      seed_contains: filter by seed term (substring match)
      only_pending:  only rows not yet drafted
      limit/offset:  at most 40 rows per call; page with offset when the total exceeds it (the body includes 「还有更多」 ("more available"))

    In the return body 「匹配总数」 ("total matched") is the true filtered count and 「返回行数」 ("rows returned") is this page's count — if they differ
    you haven't seen everything yet; don't draw conclusions from this page as if it were the whole set.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    t = admin_topic(ctx)
    plan = own_plan(ctx)
    rows = _plan_rows(plan)
    win = _cap(days, 7, 90)
    lim = _cap(limit, 20)
    off = max(0, int(offset or 0))

    today = date.today()
    # Explicit date range takes priority. This is the only entry that reaches **past-due rows** — the default window starts today,
    # so past-due rows always fall outside it, and since all write tools address by item_id, rows without a visible id effectively don't exist.
    explicit = bool(str(date_from).strip() or str(date_to).strip())
    if explicit:
        start = _parse_ymd(date_from, date.min, "date_from")
        end = _parse_ymd(date_to, date.max, "date_to")
        if start > end:
            raise ModelRetry("date_from 比 date_to 还晚,请对调。")
    else:
        start, end = today, today + timedelta(days=win)
    doc_by_item = {
        str(d.plan_item_id): (d.id, d.status)
        for d in ctx.deps.db.query(
            TopicGeneratedDocORM.id, TopicGeneratedDocORM.status, TopicGeneratedDocORM.plan_item_id
        ).filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))   # Soft-deleted ones count as "undrafted"
        .filter(TopicGeneratedDocORM.plan_item_id.isnot(None)).all()
    }

    plat_kw = str(platform or "").strip().lower()
    seed_kw = str(seed_contains or "").strip().lower()
    picked: list[dict] = []
    matched = 0
    unparsable = 0
    for r in sorted(rows, key=lambda x: (str(x.get("publish_date") or ""), int(x.get("seq") or 0))):
        ds = str(r.get("publish_date") or "")
        d0 = _row_date(ds)
        if d0 is None:
            unparsable += 1      # Don't drop silently — count them and report truthfully in the return body
            continue
        if not (start <= d0 <= end):
            continue
        item_id = str(r.get("id") or "")
        doc = doc_by_item.get(item_id)
        if only_pending and doc is not None:
            continue
        if plat_kw and not any(plat_kw in x.lower()
                               for x in [_row_platform(r), *_row_extra_platforms(r)]):
            continue
        if seed_kw and seed_kw not in str(r.get("seed") or "").lower():
            continue
        matched += 1
        if matched <= off:          # Paging: count through offset before collecting
            continue
        if len(picked) >= lim:      # Keep counting after the page is full so 「匹配总数」 ("total matched") is accurate
            continue
        extras = _row_extra_platforms(r)
        picked.append({
            "item_id": item_id,
            # Normalized date. The raw value may be a timestamped string like '2026-08-10T11:00'; echoing it would make
            # the model think "time-of-day scheduling" exists; but don't flatten it silently either — attach the raw value when different.
            "日期": d0.isoformat(),
            **({"日期原文": ds} if ds != d0.isoformat() else {}),
            "种子": _clip(r.get("seed"), 60),
            "提示词": [_clip(q, 40) for q in (r.get("queries") or [])][:3],
            "平台": _row_platform(r),
            **({"另发": extras} if extras else {}),
            "媒体": _clip(r.get("media"), 40),
            "模板id": r.get("template_id"),
            "出稿": ("未出稿" if doc is None else f"doc#{doc[0]} · {doc[1]}"),
        })

    out = {
        "topic_name": t.name,
        "排期起": start.isoformat() if explicit else today.isoformat(),
        "排期止": end.isoformat() if explicit else (today + timedelta(days=win)).isoformat(),
        "只看未出稿": bool(only_pending),
        "匹配总数": matched,
        "返回行数": len(picked),
        "items": picked,
    }
    if not explicit:
        out["窗口天数"] = win          # Compatibility with the old return body
        out["窗口内总行数"] = matched if not (plat_kw or seed_kw or only_pending) else sum(
            1 for r in rows
            if (_d := _row_date(r.get("publish_date"))) is not None and start <= _d <= end
        )
    if plat_kw:
        out["按平台筛"] = platform
    if seed_kw:
        out["按种子筛"] = seed_contains
    if off:
        out["已跳过"] = off
    if unparsable:
        # Really happens in production: 5 rows have publish_date '2026-08-10T11:00' (ISO with time).
        # The old implementation just `continue`d on parse failure, so these rows were **permanently invisible** to the agent with no hint —
        # lenient parsing now rescues most of them; any that can't be rescued must be reported, not treated as nonexistent.
        out["日期无法解析的行"] = unparsable
        out["_warn"] = (f"有 {unparsable} 行的日期格式不对、没法归入任何区间,"
                        f"它们不在上面的结果里。如实告诉用户这些行需要人工去页面上修日期。")
    if off + len(picked) < matched:
        out["还有更多"] = True
        out["下一页offset"] = off + len(picked)
        out["_note"] = (f"只显示了第 {off + 1}–{off + len(picked)} 行,共 {matched} 行匹配。"
                        f"要看后面的,用 offset={off + len(picked)} 再调一次;"
                        f"别拿这一页当全部下结论。")
    return out


def _parse_ymd(raw: str, dflt: date, field: str) -> date:
    """Parse YYYY-MM-DD. Empty string uses the default; a bad format **raises ModelRetry so the model fixes it** instead of silently falling back —
    a silent fallback yields a result that "looks normal but has a totally wrong range", which is much harder to debug than an error."""
    txt = str(raw or "").strip()
    if not txt:
        return dflt
    try:
        return datetime.strptime(txt, "%Y-%m-%d").date()
    except ValueError:
        raise ModelRetry(f"{field} 要 YYYY-MM-DD 格式(比如 2026-07-01),收到的是 {txt!r}。")


def _row_date(value) -> date | None:
    """Plan row publish_date -> date; returns None if unparsable.

    ★ Lenient: real production data mixes in timestamped ISO strings like '2026-08-10T11:00'; a strict
      strptime("%Y-%m-%d") raises ValueError, and the old implementation just `continue`d — so those 5 rows
      were permanently invisible to the agent. Taking the first 10 characters rescues them.
    """
    txt = str(value or "").strip()
    if not txt:
        return None
    for fmt_src in (txt, txt[:10]):
        try:
            return datetime.strptime(fmt_src, "%Y-%m-%d").date()
        except ValueError:
            continue
    return None


def _safe_date(s, dflt: date) -> date:
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return dflt


async def admin_plan_basis(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 **Why this plan was laid out the way it was** (scheduling-basis snapshot: quota metric definition / strategy / template / seed sources).

    Note this is a snapshot **from when the plan was made**, and often disagrees with the **live** results of
    admin_placement_strategy / admin_source_insights — not a bug: account authorization, the Ruanwenjiang resource library and batch run results keep drifting.
    When answering, present the two separately, each labeled with its time basis.
    """
    from geo.services import plan_basis as pb

    t = admin_topic(ctx)
    plan = own_plan(ctx)
    basis = pb.load(plan)
    if not basis:
        return {"topic_name": t.name, "有依据": False,
                "说明": "这是一份老计划,没有存排期依据快照。可以讲现算的投放策略,但要说明不是当初的依据。"}
    out = {k: v for k, v in basis.items() if k not in ("rows", "items")}
    return {"topic_name": t.name, "有依据": True, "依据快照": _shrink(out)}


def _shrink(o, depth: int = 0):
    """Blunt trimming: keep only the first 8 items + a count for long lists, truncate long strings. Keeps the basis snapshot from blowing up the context."""
    if depth > 3:
        return "…"
    if isinstance(o, dict):
        return {k: _shrink(v, depth + 1) for k, v in list(o.items())[:20]}
    if isinstance(o, list):
        head = [_shrink(x, depth + 1) for x in o[:8]]
        return head if len(o) <= 8 else head + [f"…共 {len(o)} 项"]
    if isinstance(o, str):
        return _clip(o, 200)
    return o


async def admin_plan_candidates(ctx: RunContext[AgentDeps], limit: int = 20) -> dict:
    """【读】 Candidate topics: prompts in the placement set that were **not hit** + optimization suggestions from cell insights; can be turned into plan rows."""
    from geo.api import admin_review

    t = admin_topic(ctx)
    lim = _cap(limit, 20)
    out = await call_route(admin_review.plan_candidates,
                           topic_id=t.id, _admin=admin_actor(ctx), db=ctx.deps.db)
    if isinstance(out, dict) and out.get("ok") is False:
        return out
    data = out.model_dump() if hasattr(out, "model_dump") else dict(out or {})
    unhit = [
        {"提示词": _clip(q.get("text"), 60), "所属种子": _clip(q.get("seed"), 40)}
        for q in (data.get("unhit_queries") or [])[:lim]
    ]
    ins = [
        {"建议": _clip(i.get("title") or i.get("text"), 90), "来源": _clip(i.get("source"), 30)}
        for i in (data.get("insights") or [])[:lim]
    ]
    return {"topic_name": t.name,
            "未命中问题数": len(data.get("unhit_queries") or []),
            "洞察建议数": len(data.get("insights") or []),
            "未命中问题": unhit, "洞察建议": ins}


# ── Article management ─────────────────────────────────────────────────────
async def admin_list_docs(ctx: RunContext[AgentDeps], status: str | None = None,
                          limit: int = 10) -> dict:
    """【读】 List this project's drafts (id / title / status / quality score / plan row / platform).

    status is optional: to_review (draft + pending review, ops default view) / draft / pending_review /
    approved / rejected / published / generating; omitted = all. limit at most 40.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM
    from sqlalchemy import func

    t = admin_topic(ctx)
    lim = _cap(limit, 10)
    db = ctx.deps.db

    q = (db.query(TopicGeneratedDocORM)
           .filter(TopicGeneratedDocORM.topic_id == t.id)
           .filter(TopicGeneratedDocORM.deleted_at.is_(None)))   # Soft-deleted ones are not shown
    st = (status or "").strip()
    if st == "to_review":
        q = q.filter(TopicGeneratedDocORM.status.in_(["draft", "pending_review"]))
    elif st:
        q = q.filter(TopicGeneratedDocORM.status == st)
    rows = q.order_by(TopicGeneratedDocORM.id.desc()).limit(lim).all()

    funnel = dict(
        db.query(TopicGeneratedDocORM.status, func.count(TopicGeneratedDocORM.id))
        .filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))
        .group_by(TopicGeneratedDocORM.status).all()
    )
    return {
        "topic_name": t.name, "筛选": st or "全部", "返回篇数": len(rows),
        "各状态篇数": {str(k): int(v) for k, v in funnel.items()},
        "docs": [{
            "doc_id": d.id,
            "标题": _clip(d.title, 60),
            "状态": d.status or "",
            "质量分": getattr(d, "quality_score", None),
            "平台": d.platform or "",
            "来源": getattr(d, "source", "") or "",
            "计划行": d.plan_item_id or "",
            "更新时间": d.updated_at,
        } for d in rows],
    }


async def admin_get_doc(ctx: RunContext[AgentDeps], doc_id: int,
                        with_rationale: bool = False, with_retest: bool = False) -> dict:
    """【读】 View a draft's full content + status + placement targets; optionally with generation rationale (with_rationale) and post-placement retest (with_retest).

    Overly long bodies are truncated to 4000 characters. Look up doc_id with admin_list_docs first; don't make it up.
    """
    from geo.api import admin_content_review as acr

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    out: dict = {
        "topic_name": t.name, "doc_id": d.id,
        "标题": d.title or "", "状态": d.status or "",
        "质量分": getattr(d, "quality_score", None),
        "平台": d.platform or "", "计划行": d.plan_item_id or "",
        "摘要": _clip(d.summary, 400),
        "正文": _clip(d.body_markdown, 4000),
        "投放目标": _jload(d.publish_targets_json, []),
        "驳回原因": getattr(d, "reject_reason", "") or "",
        "平台审核": getattr(d, "platform_review_status", "") or "",
        "更新时间": d.updated_at,
    }
    if with_rationale:
        r = await call_route(acr.get_doc_rationale, doc_id=d.id, admin=admin_actor(ctx), db=ctx.deps.db)
        out["生成依据"] = _shrink(r if isinstance(r, dict) else {"结果": str(r)[:500]})
    if with_retest:
        probes = await call_route(acr.get_doc_retest, doc_id=d.id, admin=admin_actor(ctx), db=ctx.deps.db)
        if isinstance(probes, list):
            out["投放后复测"] = [{
                "probe_id": getattr(p, "id", None), "状态": getattr(p, "status", ""),
                "对比": _shrink(getattr(p, "result", {}) or {}),
            } for p in probes[:3]]
        else:
            out["投放后复测"] = probes
    return out


# ── Placement strategy ─────────────────────────────────────────────────────
async def admin_placement_strategy(ctx: RunContext[AgentDeps], per_day: int = 5,
                                   months: int = 1, with_quota: bool = False) -> dict:
    """【读】 This project's placement strategy (engine priority / per-engine placement list / content format / placement cadence).

    **Engine priority is not ranked by hit rate**: rank = answer share × miss ratio × industry weight,
    i.e. engines that "get asked a lot yet don't name our brand" rank first — high-hit-rate engines rank lower (already stable, moved to maintenance).
    When relaying, always include each row's "reason"; **never** describe it as "sorted by hit rate", or ops will see a self-contradictory table.

    **Computed live from current data** (is_live), not the one used when this plan was made — disagreeing with admin_plan_basis
    is normal; present them separately with their time bases, and never say "the plan was laid out from this".
    per_day / months only affect the cadence conversion and are not persisted to DB; with_quota=True also returns strategy-weighted platform quotas.
    """
    from geo.services.placement_strategy import build_placement_strategy, build_strategy_quota

    t = admin_topic(ctx)
    pd, mo = _cap(per_day, 5, 30), _cap(months, 1, 12)
    if with_quota:
        strategy, quota = build_strategy_quota(ctx.deps.db, t.id, pd, months=mo)
    else:
        strategy, quota = build_placement_strategy(ctx.deps.db, t.id, per_day=pd, months=mo), None

    # ★ Rank and hit rate are **inversely related**, and that's not a bug: score = answer share × miss ratio × industry weight
    #   — the worse the answers and the larger the impact, the higher the engine's priority. So "55% hit rate ranked 4th" is correct.
    #   But if only these two numbers are shown side by side, any reader (human or model) reads it as "sorted by hit rate",
    #   and renders a self-contradictory table. The reason must be included and not truncated too short.
    #   The key used to be named reason while the service layer emits rationale — a mismatch, so the reason was always an empty string.
    prio = [{
        "引擎": p.get("engine"), "排名": p.get("rank"),
        "命中率": p.get("hit_rate_pct"),
        "应答占比": p.get("share_pct"),
        "理由": _clip(p.get("rationale"), 140),
    } for p in (strategy.get("engine_priority") or [])[:8]]
    plans = [{
        "引擎": p.get("engine"),
        "优先投": [_clip(x.get("platform") or x.get("name"), 24)
                   for x in (p.get("publishable") or [])[:4]],
        "基建待办数": len(p.get("infra") or []),
    } for p in (strategy.get("platform_plan") or [])[:8]]

    out = {
        "topic_name": t.name, "is_live": True, "computed_at": datetime.utcnow(),
        "_note": "这是按此刻数据现算的一份;排这份计划当初用的依据在 admin_plan_basis。",
        "行业": strategy.get("industry") or "",
        "引擎优先级": prio,
        "各引擎投放清单": plans,
        "内容形态": [_clip(x, 60) for x in (strategy.get("content_forms") or [])[:8]],
        "投放节奏": _shrink(strategy.get("cadence") or {}),
        "短板": [_clip(x, 60) for x in (strategy.get("gaps") or [])[:8]],
    }
    if quota is not None:
        out["平台配额"] = [{"平台": k, "每天篇数": v} for k, v in list((quota or {}).items())[:12]]
    return out


# ── Source analysis ────────────────────────────────────────────────────────
async def admin_source_insights(ctx: RunContext[AgentDeps], per_day: int = 5,
                                include_per_query: bool = False, top_n: int = 15) -> dict:
    """【读】 Source analysis: cited-domain leaderboard (including whether/how to place) + source type shares + per-engine distribution + platform quota preview.

    **Computed live from current data** (is_live), same metric definition as admin_placement_strategy.
    include_per_query=True also returns "who cited each prompt" (misses first).
    """
    from geo.api import admin_review

    t = admin_topic(ctx)
    pd, top = _cap(per_day, 5, 30), _cap(top_n, 15, 30)
    out = await call_route(admin_review.get_source_insights,
                           topic_id=t.id, per_day=pd,
                           current_user=admin_actor(ctx), db=ctx.deps.db)
    if isinstance(out, dict) and out.get("ok") is False:
        return out
    data = out.model_dump() if hasattr(out, "model_dump") else dict(out or {})

    # ★ Always drop sample_titles — it's the biggest token sink in this return
    domains = [{
        "域名": s.get("domain"), "被引次数": s.get("count"),
        "信源类型": s.get("source_type_label") or s.get("source_type"),
        "可投": s.get("actionable_label") or s.get("kind") or "",
        "投放平台": s.get("platform") or "",
    } for s in (data.get("top_sources") or [])[:top]]

    res: dict = {
        "topic_name": t.name, "is_live": True, "computed_at": data.get("computed_at"),
        "_note": "这是按此刻数据现算的一份;排这份计划当初用的依据在 admin_plan_basis。",
        "应答总数": data.get("total_responses"),
        "自有文章被引应答数": data.get("own_cited_responses"),
        "被引域名榜": domains,
        "信源类型占比": _shrink(data.get("source_type_share") or {}),
        "分引擎": [{
            "引擎": e.get("engine"), "应答数": e.get("responses"),
            "命中率": e.get("hit_rate_pct"),
            "常引域名": [d.get("domain") for d in (e.get("top_domains") or [])[:5]],
        } for e in (data.get("per_engine") or [])[:8]],
        "平台配额预览": [{"平台": q.get("platform"), "每天篇数": q.get("per_day")}
                       for q in (data.get("platform_quota") or [])[:12]],
        "未命中问题数": len(data.get("unhit_queries") or []),
    }
    if include_per_query:
        res["各提示词信源"] = [{
            "提示词": _clip(q.get("query"), 50),
            "被引域名": [d.get("domain") for d in (q.get("domains") or [])[:5]],
            "答案形态": q.get("answer_format") or "",
        } for q in (data.get("per_query") or [])[:12]]
    return res


async def admin_source_strategy(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 The "citation-friendly writing requirements" from the source strategy + per-prompt gap notes (latest LLM output)."""
    from geo.services.source_strategy import latest_content_guidelines, latest_query_gaps

    t = admin_topic(ctx)
    guides = latest_content_guidelines(ctx.deps.db, t.id) or []
    gaps = latest_query_gaps(ctx.deps.db, t.id) or {}
    return {
        "topic_name": t.name,
        "有策略": bool(guides or gaps),
        "写作要求": [_clip(g, 160) for g in guides[:10]],
        "问题差距": [{"提示词": _clip(k, 50), "补法": _clip(v, 160)}
                    for k, v in list(gaps.items())[:12]],
        "_note": ("还没有生成过信源策略,可以在计划书「信源分析」页触发。" if not (guides or gaps) else ""),
    }


async def admin_media_candidates(ctx: RunContext[AgentDeps], platform: str,
                                 limit: int = 5) -> dict:
    """【读】 Paid media candidates available on a platform (resource id / name / reference price / publish rate), for picking when creating placement tasks."""
    from geo.services.source_plan import pick_rwj_resources

    t = admin_topic(ctx)
    p = (platform or "").strip()
    if not p:
        from pydantic_ai import ModelRetry
        raise ModelRetry("platform 不能为空。先用 admin_source_insights 看有哪些可投平台。")
    rows = pick_rwj_resources(ctx.deps.db, p, limit=_cap(limit, 5, 20)) or []
    return {
        "topic_name": t.name, "平台": p, "候选数": len(rows),
        "候选": [{
            "resource_id": r.get("resource_id"), "媒体": _clip(r.get("title"), 40),
            "参考价": r.get("ref_price_yuan") or r.get("ref_price_cents"),
            "出稿率": r.get("publish_rate"), "类型": r.get("res_type"),
        } for r in rows],
    }


# ── Article placement ──────────────────────────────────────────────────────
async def admin_list_publish_tasks(ctx: RunContext[AgentDeps], status: str | None = None,
                                   doc_id: int | None = None, limit: int = 20) -> dict:
    """【读】 List this project's placement tasks (platform / media / channel / status / cost / published URL).

    status is optional: planned / pending_publish / publishing / pending_url / url_submitted /
    published / failed / cancelled; doc_id is optional, to see only one article's tasks.
    """
    from geo.services.publish_task_service import list_tasks

    t = admin_topic(ctx)
    lim = _cap(limit, 20)
    did = None
    if doc_id is not None:
        did = own_doc(ctx, doc_id).id
    sts = [s.strip() for s in str(status or "").split(",") if s.strip()] or None

    rows = list_tasks(ctx.deps.db, t.id, doc_id=did, statuses=sts)
    funnel: dict[str, int] = {}
    for r in rows:
        funnel[str(r.status or "")] = funnel.get(str(r.status or ""), 0) + 1
    return {
        "topic_name": t.name, "筛选": status or "全部", "任务总数": len(rows),
        "各状态任务数": funnel,
        "tasks": [{
            "task_id": r.id, "doc_id": r.doc_id,
            "平台": r.platform or "", "媒体": _clip(r.media_name, 40),
            "通道": r.channel_type or "", "方式": r.publish_mode or "",
            "状态": r.status or "",
            "已发链接": r.published_url or "",
            "成本(分)": r.cost_cents,
        } for r in rows[:lim]],
    }


async def admin_publish_channel_status(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 Current availability of each placement channel (paid media balance and charge gate, number of authorized accounts on own-account channels)."""
    import os

    admin_topic(ctx)   # Only checks role + project lock
    out: dict = {"付费媒体": [], "自有账号通道": {}}
    try:
        from geo.services.ruanwenjiang_publisher import channel_status
        for provider in ("rwj", "mjk"):
            try:
                out["付费媒体"].append(channel_status(provider))
            except Exception as e:  # noqa: BLE001
                out["付费媒体"].append({"provider": provider, "configured": False,
                                       "error": _clip(str(e), 120)})
    except Exception as e:  # noqa: BLE001
        out["付费媒体"] = [{"error": _clip(str(e), 120)}]
    try:
        from geo.services.aitoearn_publisher import list_article_accounts
        accs = await list_article_accounts()
        out["自有账号通道"] = {"已授权可发文账号数": len(accs or []),
                              "平台": sorted({str(a.get("type") or "") for a in (accs or [])})[:12]}
    except Exception as e:  # noqa: BLE001
        out["自有账号通道"] = {"error": _clip(str(e), 120)}

    from geo.database import settings
    out["扣费闸门"] = {
        "平台总闸(RWJ_PUBLISH_ENABLED)": bool(getattr(settings, "RWJ_PUBLISH_ENABLED", False)),
        "运营台付费护栏(AGENT_ALLOW_PAID_PUBLISH)":
            (os.environ.get("AGENT_ALLOW_PAID_PUBLISH", "0") == "1"),
    }
    return out


# ══════════════════════════════════════════════════════════════════════
# Write tools: plan management (edit / delete / add / reorder / confirm / batch drafting)
# ══════════════════════════════════════════════════════════════════════

# Allowlist of publishing-row fields the model may edit. **Anything not on the allowlist is never changed** —
# machine fields like origin / why are carried through by _validate_plan_items' existing; don't touch them.
_ROW_EDITABLE = ("publish_date", "platform", "media", "note", "template_id", "seed", "queries")


def _rows_to_drafts(db, rows: list[dict]) -> tuple[list, int]:
    """Existing publishing rows -> list of PlanItemDraft, for full-table write-back.

    update_execution_plan **replaces the whole table**; even editing one row requires writing back the full table. Two pitfalls:
      ① PlanItemDraft.template_id is **required and non-empty**, while candidate-topic insertion (insert_plan_items)
         writes template_id=None, so those rows 422 on full write-back — here they are filled with the default system template,
         and the caller reports truthfully to the user how many rows were filled;
      ② machine fields like origin / why are not in the Draft; they are carried through via _validate_plan_items' existing param.
         **Going through update_execution_plan makes this automatically safe**; never take the shortcut of editing
         publishing_plan_json directly — that is exactly the "ops saves the publishing table once, renewal reverts to seed rotation" incident.
    """
    from geo.api.admin_review import _pick_default_template_id
    from geo.models.ai_telemetry import PlanItemDraft

    fallback = _pick_default_template_id(db)
    drafts, patched = [], 0
    for r in rows:
        tid = r.get("template_id")
        if not tid:
            tid = fallback
            patched += 1
        if not tid:
            raise ModelRetry("系统里没有可用的内容模板,无法保存发文表。请先在模板库建一个。")
        drafts.append(PlanItemDraft(
            id=str(r.get("id") or "") or None,
            seq=int(r.get("seq") or 0),
            publish_date=str(r.get("publish_date") or ""),
            seed=r.get("seed"),
            query=str(r.get("query") or ""),
            queries=[str(q) for q in (r.get("queries") or [])],
            template_id=int(tid),
            platform=str(r.get("platform") or ""),
            channel=r.get("channel"),
            extra_platforms=[str(x) for x in (r.get("extra_platforms") or [])],
            media=r.get("media"),
            rwj_resource_id=r.get("rwj_resource_id"),
            note=r.get("note"),
            creation_directions=[str(x) for x in (r.get("creation_directions") or [])],
            copywriting_types=[str(x) for x in (r.get("copywriting_types") or [])],
        ))
    return drafts, patched


async def _put_plan(ctx, rows: list[dict], patched: int, *, tool: str, extra: dict) -> dict:
    """Full-table write-back + audit. All plan row edits / deletes converge here so nobody writes their own path."""
    from geo.api import admin_review
    from geo.models.ai_telemetry import ExecutionPlanUpdatePayload

    t = admin_topic(ctx)
    drafts, patched2 = _rows_to_drafts(ctx.deps.db, rows)
    out = await call_route(admin_review.update_execution_plan,
                           topic_id=t.id, payload=ExecutionPlanUpdatePayload(items=drafts),
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool=tool, topic_id=t.id, risk="write",
                        args=extra, object_type="execution_plan", object_id=getattr(out, "id", None))
    ctx.deps.db.commit()
    res = {"ok": True, "topic_name": t.name, "计划行总数": len(rows), **extra}
    if patched + patched2:
        res["_note"] = f"有 {patched + patched2} 行原本没有模板,已按默认模板补齐(请如实告知用户)。"
    return res


async def admin_update_plan_item(
    ctx: RunContext[AgentDeps], item_id: str,
    publish_date: str = "", platform: str = "", media: str = "",
    note: str = "", template_id: int | None = None,
    seed: str = "", queries: list[str] | None = None,
) -> dict:
    """【写】 Edit one publishing plan row: date / platform / media / note / template / seed / prompts.

    Look up item_id with admin_list_plan_items first; don't make it up. Pass only the fields to change; empty ones are untouched.
    This changes scheduling, not the body of already-generated articles (use admin_update_doc for that).
    """
    plan = own_plan(ctx)
    rows = _plan_rows(plan)
    row = own_plan_item(plan, item_id)

    patch: dict = {}
    if publish_date.strip():
        d = publish_date.strip()
        if not d[:10].count("-") == 2:
            raise ModelRetry("publish_date 要写成 2026-08-20 这种格式。")
        patch["publish_date"] = d
    if platform.strip():
        patch["platform"] = platform.strip()
    if media.strip():
        patch["media"] = media.strip()
    if note.strip():
        patch["note"] = note.strip()
    if template_id is not None:
        patch["template_id"] = int(template_id)
    if seed.strip():
        patch["seed"] = seed.strip()
    if queries is not None:
        monitored = set(_jload(plan.monitored_queries_snapshot_json, []))
        bad = [q for q in queries if q not in monitored]
        if bad:
            raise ModelRetry(f"这些不在本项目的提示词里:{bad[:5]}。先用 get_prompts 看有哪些。")
        patch["queries"] = list(queries)
    if not patch:
        raise ModelRetry("没有给出任何要改的字段。至少传一个:日期 / 平台 / 媒体 / 备注 / 模板 / 种子 / 提示词。")

    new_rows = [{**r, **patch} if str(r.get("id") or "") == str(item_id) else r for r in rows]
    return await _put_plan(ctx, new_rows, 0, tool="admin_update_plan_item",
                           extra={"改了行": item_id, "改动": patch,
                                  "原日期": row.get("publish_date")})


async def admin_remove_plan_items(ctx: RunContext[AgentDeps], item_ids: list[str],
                                  confirm_token: str = "") -> dict:
    """【动作·不可逆】 Delete several publishing plan rows.

    Deleted rows are gone; **when an already-drafted row is deleted, its articles become detached from the plan** (the articles themselves stay,
    but the detail page's plan backlink and the calendar's historical attribution break). Hence two-step confirmation.
    """
    plan = own_plan(ctx)
    rows = _plan_rows(plan)
    want = {str(x).strip() for x in (item_ids or []) if str(x).strip()}
    if not want:
        raise ModelRetry("item_ids 为空。先用 admin_list_plan_items 查要删哪几行。")
    if len(want) > _max_batch():
        raise ModelRetry(f"一次最多删 {_max_batch()} 行,请分批,或让用户在页面上批量操作。")

    hit = [r for r in rows if str(r.get("id") or "") in want]
    missing = sorted(want - {str(r.get("id") or "") for r in hit})
    if not hit:
        raise ModelRetry(f"这些行不在当前项目的发文表里:{sorted(want)[:5]}。先查一次 item_id。")

    from geo.models.ai_telemetry import TopicGeneratedDocORM
    t = admin_topic(ctx)
    with_docs = {
        str(x[0]) for x in ctx.deps.db.query(TopicGeneratedDocORM.plan_item_id)
        .filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))
        .filter(TopicGeneratedDocORM.plan_item_id.in_(list(want))).all()
    }

    args = {"item_ids": sorted(want)}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_remove_plan_items", action_label=f"删除 {len(hit)} 行发文计划",
            args=args, topic=t,
            preview={"要删的行数": len(hit),
                     "其中已出稿": len(with_docs),
                     "前几行": [{"日期": r.get("publish_date"), "种子": _clip(r.get("seed"), 30),
                                "平台": _row_platform(r)} for r in hit[:5]],
                     "找不到的 id": missing[:5]},
            impact=[f"发文表从 {len(rows)} 行变成 {len(rows) - len(hit)} 行"],
            irreversible=(["删除的计划行无法恢复"] + (
                [f"{len(with_docs)} 篇已出稿的文章会与计划脱钩(文章不删,但计划回链会断)"]
                if with_docs else [])),
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_remove_plan_items", args=args)
    if blocked is not None:
        return blocked

    kept = [r for r in rows if str(r.get("id") or "") not in want]
    out = await _put_plan(ctx, kept, 0, tool="admin_remove_plan_items",
                          extra={"已删行数": len(hit), "已出稿被脱钩": len(with_docs)})
    if isinstance(out, dict) and missing:
        out["_note"] = f"有 {len(missing)} 个 id 在表里找不到,已跳过:{missing[:5]}"
    return out


async def admin_add_plan_item(
    ctx: RunContext[AgentDeps], publish_date: str, seed: str = "",
    queries: list[str] | None = None, platform: str = "", template_id: int | None = None,
    note: str = "", generate_now: bool = False, confirm_token: str = "",
) -> dict:
    """【写】 Add a publishing plan row on a given date.

    generate_now=True **dispatches drafting immediately** (really spends LLM quota), in which case it goes through two-step confirmation;
    scheduling only without drafting (default) executes directly. Provide at least one of seed and queries.
    """
    from geo.api import admin_review
    from geo.api.admin_review import _pick_default_template_id
    from geo.models.ai_telemetry import PlanAddItemPayload, PlanItemDraft

    t = admin_topic(ctx)
    plan = own_plan(ctx)
    d = (publish_date or "").strip()
    if not d:
        raise ModelRetry("publish_date 必填,写成 2026-08-20 这种格式。")
    if not (seed or "").strip() and not queries:
        raise ModelRetry("seed 和 queries 至少给一个 —— 不然不知道这篇写什么。")
    monitored = set(_jload(plan.monitored_queries_snapshot_json, []))
    bad = [q for q in (queries or []) if q not in monitored]
    if bad:
        raise ModelRetry(f"这些不在本项目的提示词里:{bad[:5]}。先用 get_prompts 看有哪些。")
    tid = template_id or _pick_default_template_id(ctx.deps.db)
    if not tid:
        raise ModelRetry("系统里没有可用的内容模板,无法新增计划行。")

    args = {"publish_date": d, "seed": seed, "queries": queries or [],
            "platform": platform, "template_id": int(tid), "note": note,
            "generate_now": bool(generate_now)}
    if generate_now and not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_add_plan_item", action_label="新增一行发文计划并立即产稿",
            args=args, topic=t,
            preview={"日期": d, "种子": _clip(seed, 40), "平台": platform or "(按模板默认)",
                     "提示词": (queries or [])[:3]},
            impact=["会立即派发 1 篇文章生成任务(约 30–90 秒)", "消耗 1 次产稿额度"],
        )
    if generate_now:
        blocked = guard(ctx.deps, confirm_token, tool="admin_add_plan_item", args=args)
        if blocked is not None:
            return blocked

    payload = PlanAddItemPayload(
        item=PlanItemDraft(publish_date=d, seed=(seed or None), queries=list(queries or []),
                           template_id=int(tid), platform=platform or "", note=note or None),
        generate_now=bool(generate_now),
    )
    out = await call_route(admin_review.add_plan_item, topic_id=t.id, payload=payload,
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_add_plan_item", topic_id=t.id,
                        risk=("act" if generate_now else "write"), args=args,
                        confirmed=bool(confirm_token), object_type="execution_plan",
                        object_id=getattr(out, "id", None))
    ctx.deps.db.commit()
    return {"ok": True, "topic_name": t.name, "已新增": {"日期": d, "种子": _clip(seed, 40)},
            "是否已派发产稿": bool(generate_now),
            "计划行总数": len(getattr(out, "publishing_plan", []) or [])}


async def admin_insert_plan_candidates(ctx: RunContext[AgentDeps], queries: list[str],
                                       note: str = "") -> dict:
    """【写】 Insert candidate topics (unhit prompts) into the plan, scheduled one per day starting tomorrow.

    Look up candidates with admin_plan_candidates first. Scheduling only, no drafting, reversible (remove mistakes with admin_remove_plan_items).
    """
    from geo.api import admin_review
    from geo.api.admin_review import InsertPlanItemPayload, InsertPlanItemsPayload

    t = admin_topic(ctx)
    qs = [str(q).strip() for q in (queries or []) if str(q).strip()]
    if not qs:
        raise ModelRetry("queries 为空。先用 admin_plan_candidates 查候选选题。")
    if len(qs) > _max_batch():
        raise ModelRetry(f"一次最多插 {_max_batch()} 条,请分批。")

    payload = InsertPlanItemsPayload(items=[
        # Mark origin as unhit_query: plan row provenance, shown in the frontend's "why it was scheduled this way"
        InsertPlanItemPayload(query=q, note=note or None, origin={"type": "unhit_query", "ref": q})
        for q in qs
    ])
    out = await call_route(admin_review.insert_plan_items, topic_id=t.id, payload=payload,
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_insert_plan_candidates", topic_id=t.id,
                        risk="write", args={"queries": qs}, object_type="execution_plan")
    ctx.deps.db.commit()
    return {"ok": True, "topic_name": t.name, "已插入": len(qs), "选题": qs[:10],
            "说明": "从明天起逐日排期;还没产稿,确认计划或批量产稿时才会出文章。"}


async def admin_reorder_plan(ctx: RunContext[AgentDeps], per_day: int = 5,
                             source: str = "seeds", platform_mode: str = "sources",
                             months: int = 1, confirm_token: str = "") -> dict:
    """【动作】 Re-schedule the publishing table **from today onward** at a new cadence (historical rows before today are kept as-is).

    platform_mode: sources = split platforms by measured citation share / strategy = weighted by placement strategy / template = keep template.
    **Already-drafted rows from today onward get detached from their articles** (articles are not deleted) — when such rows exist, a second confirmation is required.
    """
    from geo.api import admin_review
    from geo.models.ai_telemetry import PlanGeneratePayload, TopicGeneratedDocORM

    t = admin_topic(ctx)
    plan = own_plan(ctx)
    rows = _plan_rows(plan)
    pd, mo = _cap(per_day, 5, 10), _cap(months, 1, 3)
    if source not in ("seeds", "strategy"):
        raise ModelRetry("source 只能是 seeds 或 strategy。")
    if platform_mode not in ("sources", "strategy", "template"):
        raise ModelRetry("platform_mode 只能是 sources / strategy / template。")

    today = date.today().isoformat()
    linked = {
        str(x[0]) for x in ctx.deps.db.query(TopicGeneratedDocORM.plan_item_id)
        .filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))
        .filter(TopicGeneratedDocORM.plan_item_id.isnot(None)).all()
    }
    future_with_docs = [
        r for r in rows
        if str(r.get("publish_date") or "")[:10] >= today and str(r.get("id") or "") in linked
    ]

    args = {"per_day": pd, "source": source, "platform_mode": platform_mode, "months": mo}
    # Re-scheduling future empty rows only is low risk and runs directly; escalates to two-step confirmation when it would detach drafted articles.
    if future_with_docs and not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_reorder_plan", action_label="按新节奏重排今天及以后的发文表",
            args=args, topic=t,
            preview={"每天篇数": pd, "铺几个月": mo, "排期来源": source,
                     "平台分配": platform_mode,
                     "今天及以后已出稿的行": len(future_with_docs),
                     "发文行总数": len(rows)},
            impact=["今天之前的历史行原样保留", "重排后需要再次确认计划,新行才会产稿"],
            irreversible=[f"{len(future_with_docs)} 行已出稿的文章会与计划脱钩(文章不删,计划回链会断)"],
        )
    if future_with_docs:
        blocked = guard(ctx.deps, confirm_token, tool="admin_reorder_plan", args=args)
        if blocked is not None:
            return blocked

    out = await call_route(
        admin_review.generate_plan_items, topic_id=t.id,
        payload=PlanGeneratePayload(source=source, per_day=pd, template_ids=[],
                                    platform_mode=platform_mode, months=mo),
        admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_reorder_plan", topic_id=t.id, risk="act",
                        args=args, confirmed=bool(confirm_token), object_type="execution_plan")
    ctx.deps.db.commit()
    return {"ok": True, "topic_name": t.name,
            "计划行总数": len(getattr(out, "publishing_plan", []) or []),
            "已脱钩的已出稿行": len(future_with_docs),
            "说明": "只重排了今天及以后;要产稿还需确认计划或批量产稿。"}


async def admin_confirm_plan(ctx: RunContext[AgentDeps], force: bool = False,
                             confirm_token: str = "") -> dict:
    """【动作·不可逆】 Confirm the publishing table -> trigger batch drafting (async, really spends LLM quota producing articles).

    force=True **regenerates already-drafted rows as well**, overwriting existing bodies.
    """
    from geo.api import admin_review
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    t = admin_topic(ctx)
    plan = own_plan(ctx)
    rows = _plan_rows(plan)
    if not rows:
        raise ModelRetry("发文表是空的,先加计划行(admin_add_plan_item / admin_reorder_plan)再确认。")

    linked = {
        str(x[0]) for x in ctx.deps.db.query(TopicGeneratedDocORM.plan_item_id)
        .filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))
        .filter(TopicGeneratedDocORM.plan_item_id.isnot(None)).all()
    }
    undone = [r for r in rows if str(r.get("id") or "") not in linked]
    will = len(rows) if force else len(undone)

    args = {"force": bool(force)}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_confirm_plan", action_label="确认发文表并触发批量产稿",
            args=args, topic=t,
            preview={"计划状态": plan.status, "发文行总数": len(rows),
                     "还没出稿的行": len(undone), "本次会派发": will,
                     "最近几行": [{"日期": r.get("publish_date"), "种子": _clip(r.get("seed"), 30)}
                                for r in undone[:3]]},
            impact=[f"会为 {will} 个计划行派发文章生成任务",
                    f"消耗约 {will} 次产稿额度", "生成是异步的,每篇约 30–90 秒"],
            irreversible=(["force=True 会把已出稿的行全部重生,覆盖现有正文"] if force else []),
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_confirm_plan", args=args)
    if blocked is not None:
        return blocked

    out = await call_route(admin_review.confirm_execution_plan, topic_id=t.id, force=force,
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_confirm_plan", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="execution_plan",
                        object_id=getattr(out, "id", None))
    ctx.deps.db.commit()
    return {"ok": True, "topic_name": t.name, "计划状态": getattr(out, "status", ""),
            "已派发": will,
            "说明": "文章生成已派发,用 admin_list_docs(status='generating') 看进度。"}


async def admin_batch_generate_docs(
    ctx: RunContext[AgentDeps], days: int | None = None, limit: int | None = None,
    item_ids: list[str] | None = None, include_existing: bool = False,
    confirm_token: str = "",
) -> dict:
    """【动作·不可逆】 Batch drafting by range (possibly dozens of articles at once, really spends LLM quota).

    days = plan rows in the next N days; limit = at most this many rows; item_ids = specific rows (if given, filter by it only).
    include_existing=True also regenerates drafted rows (published drafts are never regenerated).
    """
    from geo.api import admin_review
    from geo.models.ai_telemetry import PlanBatchGenPayload

    t = admin_topic(ctx)
    own_plan(ctx)
    if limit is not None and int(limit) > _max_batch():
        raise ModelRetry(f"一次最多产 {_max_batch()} 篇,请把 limit 调小或分批。")
    payload = PlanBatchGenPayload(
        item_ids=[str(x) for x in item_ids] if item_ids else None,
        days=int(days) if days else None,
        limit=int(limit) if limit else None,
        include_existing=bool(include_existing),
    )
    args = {"days": days, "limit": limit, "item_ids": item_ids,
            "include_existing": bool(include_existing)}

    if not confirm_token:
        # ★ The preview must use the endpoint's dry_run — "how many will be dispatched" is computed by the endpoint's internal filter
        #   (skip drafted, block published); recomputing it here would inevitably diverge from the real dispatch.
        prev = await call_route(admin_review.batch_generate_docs, topic_id=t.id, payload=payload,
                                dry_run=True, admin=admin_actor(ctx), db=ctx.deps.db)
        if route_failed(prev):
            return prev
        queued = int(getattr(prev, "queued", 0))
        if queued == 0:
            return {"ok": True, "modified": False, "已派发": 0,
                    "说明": "这个范围里没有需要产稿的计划行(要么都出过稿了,要么范围里没有行)。"}
        if queued > _max_batch():
            raise ModelRetry(
                f"这个范围会派 {queued} 篇,超过单次上限 {_max_batch()}。"
                "请把 days / limit 调小分批,或让用户在页面上操作。")
        return needs_confirm(
            ctx.deps, tool="admin_batch_generate_docs", action_label="批量产稿",
            args=args, topic=t,
            preview={"将派发": queued, "命中范围的行": int(getattr(prev, "matched", 0)),
                     "已出稿被跳过": int(getattr(prev, "skipped_existing", 0)),
                     "已发布被挡下": int(getattr(prev, "skipped_published", 0))},
            impact=[f"会派发 {queued} 篇文章生成任务", f"消耗约 {queued} 次产稿额度",
                    "生成是异步的,每篇约 30–90 秒"],
            irreversible=(["已出稿的行会被重新生成,覆盖现有正文"] if include_existing else []),
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_batch_generate_docs", args=args)
    if blocked is not None:
        return blocked

    out = await call_route(admin_review.batch_generate_docs, topic_id=t.id, payload=payload,
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_batch_generate_docs", topic_id=t.id, risk="act",
                        args=args, confirmed=True, result={"queued": getattr(out, "queued", 0)})
    ctx.deps.db.commit()
    return {"ok": True, "topic_name": t.name, "已派发": int(getattr(out, "queued", 0)),
            "已出稿被跳过": int(getattr(out, "skipped_existing", 0)),
            "已发布被挡下": int(getattr(out, "skipped_published", 0)),
            "说明": "用 admin_list_docs(status='generating') 看进度。"}


async def admin_push_infra_actions(ctx: RunContext[AgentDeps]) -> dict:
    """【写】 Push infrastructure actions from the placement strategy that don't fit the publishing table (encyclopedia entries / account verification / webmaster verification) into the optimization task center.

    Idempotent: each item is created only once; repeated calls won't flood.
    """
    from geo.services.placement_strategy import build_placement_strategy, push_infra_actions

    t = admin_topic(ctx)
    strategy = build_placement_strategy(ctx.deps.db, t.id)
    n = push_infra_actions(ctx.deps.db, t.id, strategy)
    record_agent_action(ctx.deps, tool="admin_push_infra_actions", topic_id=t.id,
                        risk="write", args={}, result={"created": n})
    ctx.deps.db.commit()
    return {"ok": True, "topic_name": t.name, "新建任务数": n,
            "说明": "已推进优化任务中心;之前推过的不会重复建。"}

# ══════════════════════════════════════════════════════════════════════
# Write tools: article management (edit / submit for review / approve / reject / rewrite / delete)
# ══════════════════════════════════════════════════════════════════════

async def admin_update_doc(ctx: RunContext[AgentDeps], doc_id: int, title: str = "",
                           summary: str = "", body_markdown: str = "") -> dict:
    """【写】 Edit a draft's title / summary / body. Pass only what changes; empty ones are untouched.

    Editing the body is a **full overwrite** (the old body is not kept); pass the complete new body, not a fragment.
    Published drafts cannot be edited — the live article won't change, so the DB would diverge from what's online.
    """
    from geo.api import admin_content_review as acr
    from geo.models.ai_telemetry import AdminDocUpdatePayload

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    if d.status == "published":
        raise ModelRetry(
            f"稿件 {d.id} 已发布,不能改。线上那篇不会跟着变,改了库里就和线上对不上、"
            "基于 URL 的引用归因也会失真。要重写请先和用户确认走重新生成。")
    patch = {k: v for k, v in (("title", title), ("summary", summary),
                               ("body_markdown", body_markdown)) if (v or "").strip()}
    if not patch:
        raise ModelRetry("没给要改的内容。至少传 title / summary / body_markdown 之一。")

    out = await call_route(acr.admin_update_doc, doc_id=d.id,
                           payload=AdminDocUpdatePayload(**patch),
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_update_doc", topic_id=t.id, risk="write",
                        args={"doc_id": d.id, "改了": sorted(patch)},
                        object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    return {"ok": True, "doc_id": d.id, "改了": sorted(patch),
            "标题": _clip(getattr(out, "title", d.title), 60), "状态": d.status}


async def admin_select_docs_for_review(ctx: RunContext[AgentDeps], doc_ids: list[int]) -> dict:
    """【写】 Move a draft to "pending review", entering the manual review queue. Only affects drafts; reversible."""
    from geo.api import admin_content_review as acr
    from geo.models.ai_telemetry import SelectDocsPayload, TopicGeneratedDocORM

    t = admin_topic(ctx)
    kept, dropped = own_doc_ids(ctx, doc_ids)
    if len(kept) > _max_batch():
        raise ModelRetry(f"一次最多送审 {_max_batch()} 篇,请分批。")
    not_draft = [
        r.id for r in ctx.deps.db.query(TopicGeneratedDocORM.id, TopicGeneratedDocORM.status)
        .filter(TopicGeneratedDocORM.id.in_(kept))
        .filter(TopicGeneratedDocORM.deleted_at.is_(None)).all() if r.status != "draft"
    ]
    todo = [i for i in kept if i not in not_draft]
    if not todo:
        return {"ok": False, "modified": False,
                "message": f"这些稿件都不是草稿(不能送审):{kept[:5]}。"}

    out = await call_route(acr.select_docs_for_review, payload=SelectDocsPayload(doc_ids=todo),
                           _admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_select_docs_for_review", topic_id=t.id,
                        risk="write", args={"doc_ids": todo}, object_type="doc")
    ctx.deps.db.commit()
    res: dict = {"ok": True, "已送审": len(todo), "doc_ids": todo}
    skip = []
    if dropped:
        skip.append(f"{len(dropped)} 篇不属于当前项目({dropped[:5]})")
    if not_draft:
        skip.append(f"{len(not_draft)} 篇不是草稿({not_draft[:5]})")
    if skip:
        res["_note"] = "已跳过:" + ";".join(skip) + "。请如实告知用户。"
    return res


async def admin_approve_doc(ctx: RunContext[AgentDeps], doc_id: int,
                            confirm_token: str = "") -> dict:
    """【动作·不可逆】 Approve a draft.

    After approval, placement tasks are **created automatically** per the plan row's preset (paid media auto-push drafts), and the **customer is notified by email**.
    """
    from geo.api import admin_content_review as acr

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    if d.status not in ("draft", "pending_review"):
        raise ModelRetry(f"稿件 {d.id} 当前是「{d.status}」,不能通过审核(只有草稿 / 待审核可以)。")

    args = {"doc_id": d.id}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_approve_doc", action_label="审核通过一篇稿", args=args, topic=t,
            preview={"doc_id": d.id, "标题": _clip(d.title, 60), "当前状态": d.status,
                     "质量分": getattr(d, "quality_score", None),
                     "摘要": _clip(d.summary, 150)},
            impact=["会按计划行预设自动建投放任务(付费媒体会自动推草稿)",
                    "会邮件通知客户审核结果"],
            irreversible=["审核结果与通知邮件发出后无法撤回"],
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_approve_doc", args=args)
    if blocked is not None:
        return blocked

    out = await call_route(acr.approve_doc, doc_id=d.id, admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_approve_doc", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    return {"ok": True, "doc_id": d.id, "状态": getattr(out, "status", "approved"),
            "说明": "已通过;投放任务按计划行预设自动建,用 admin_list_publish_tasks 看。"}


async def admin_reject_doc(ctx: RunContext[AgentDeps], doc_id: int, reason: str,
                           confirm_token: str = "") -> dict:
    """【动作】 Reject a draft (a reason is required). **Notifies the customer by email**."""
    from geo.api import admin_content_review as acr
    from geo.models.ai_telemetry import RejectDocPayload

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    why = (reason or "").strip()
    if not why:
        raise ModelRetry("驳回必须给原因 —— 客户会收到这条原因的邮件。先问用户为什么驳回。")

    args = {"doc_id": d.id, "reason": why}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_reject_doc", action_label="驳回一篇稿", args=args, topic=t,
            preview={"doc_id": d.id, "标题": _clip(d.title, 60), "当前状态": d.status,
                     "驳回原因": _clip(why, 200)},
            impact=["稿件状态变为已驳回", "会把这条原因邮件通知客户"],
            irreversible=["通知邮件发出后无法撤回"],
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_reject_doc", args=args)
    if blocked is not None:
        return blocked

    out = await call_route(acr.reject_doc, doc_id=d.id, payload=RejectDocPayload(reason=why),
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_reject_doc", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    return {"ok": True, "doc_id": d.id, "状态": "rejected", "原因": _clip(why, 120)}


async def admin_regenerate_doc(ctx: RunContext[AgentDeps], doc_id: int,
                               confirm_token: str = "") -> dict:
    """【动作·不可逆】 Rewrite a draft: **overwrites the body in place; the original body is unrecoverable**.

    Published drafts cannot be rewritten (the live article won't change).
    """
    from geo.api import admin_content_review as acr
    from geo.api import admin_review

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    if d.status == "published":
        raise ModelRetry(f"稿件 {d.id} 已发布,不能重写 —— 线上那篇不会跟着变,库里和线上会对不上。")

    args = {"doc_id": d.id}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_regenerate_doc", action_label="重写一篇稿", args=args, topic=t,
            preview={"doc_id": d.id, "标题": _clip(d.title, 60), "当前状态": d.status,
                     "计划行": d.plan_item_id or "(无,是孤立稿)"},
            impact=(["会异步重新生成,约 30–90 秒"] if d.plan_item_id
                    else ["这篇没挂计划行,会**同步**重写,要等 30–60 秒"]),
            irreversible=["现有正文会被覆盖,原文不可恢复"],
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_regenerate_doc", args=args)
    if blocked is not None:
        return blocked

    # With a plan row, use async single regeneration; orphan drafts can only use the sync path (blocks 30–60 seconds)
    if d.plan_item_id:
        out = await call_route(admin_review.regenerate_plan_item, topic_id=t.id,
                               item_id=d.plan_item_id, admin=admin_actor(ctx), db=ctx.deps.db)
        mode = "异步派发"
    else:
        out = await call_route(acr.regenerate_doc, doc_id=d.id,
                               admin=admin_actor(ctx), db=ctx.deps.db)
        mode = "同步重写"
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_regenerate_doc", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    return {"ok": True, "doc_id": d.id, "方式": mode,
            "说明": "用 admin_get_doc 看新正文;异步的要等生成完。"}


async def admin_delete_doc(ctx: RunContext[AgentDeps], doc_id: int,
                           confirm_token: str = "") -> dict:
    """【动作】 Delete a draft (**soft delete**: sets a deleted flag, never shown on the frontend again, recoverable when needed).

    Not a physical delete — drafts carry placement tasks, post-placement retests and URL-level citation attribution; a physical delete would erase the evidence chain.
    After deletion: the matching publishing plan row goes back to "undrafted" and can be drafted again; its placement tasks are all set to cancelled.
    **Drafts that are published or already have a real placement link cannot be deleted** (the live content still exists); use reject / take down instead.
    Restore mistaken deletions with admin_restore_doc.
    """
    from datetime import datetime as _dt

    from geo.models.ai_telemetry import TopicDocPublishTaskORM

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    db = ctx.deps.db

    tasks = db.query(TopicDocPublishTaskORM).filter(TopicDocPublishTaskORM.doc_id == d.id).all()
    live = [x for x in tasks if (x.published_url or "").strip()]
    if d.status == "published" or live:
        raise ModelRetry(
            f"稿件 {d.id}「{_clip(d.title, 30)}」已经投放出去了(状态 {d.status},"
            f"{len(live)} 条投放任务带真实链接),**不能删**:线上内容还在,把它从列表里抹掉会让"
            "引用归因和投放后复测对不上账。如果是想下架,请改用 admin_reject_doc 驳回,"
            "并如实把这个限制告诉用户。")

    args = {"doc_id": d.id}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_delete_doc", action_label="删除一篇稿(软删除,可恢复)",
            args=args, topic=t,
            preview={"doc_id": d.id, "标题": _clip(d.title, 60), "状态": d.status,
                     "计划行": d.plan_item_id or "(无)",
                     "会一并取消的投放任务": len(tasks)},
            impact=["前端(运营台与客户侧)都不再展示这篇稿",
                    f"它的 {len(tasks)} 条投放任务置为已取消",
                    "对应的发文计划行回到「未出稿」,可以重新产稿",
                    "正文与历史都还在库里,用 admin_restore_doc 可以恢复"],
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_delete_doc", args=args)
    if blocked is not None:
        return blocked

    d.deleted_at = _dt.utcnow()
    d.deleted_by_id = ctx.deps.user_id
    cancelled = 0
    for x in tasks:
        if x.status not in ("published", "cancelled"):
            x.status = "cancelled"
            cancelled += 1
    record_agent_action(ctx.deps, tool="admin_delete_doc", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="doc", object_id=d.id,
                        result={"soft": True, "title": d.title,
                                "plan_item_id": d.plan_item_id, "cancelled_tasks": cancelled})
    db.commit()
    return {"ok": True, "已删除": d.id, "标题": _clip(d.title, 60), "方式": "软删除(可恢复)",
            "已取消的投放任务": cancelled,
            "说明": ("对应计划行已回到「未出稿」,需要的话可以重新产稿;" if d.plan_item_id else "")
                    + "删错了用 admin_restore_doc 恢复。"}


async def admin_restore_doc(ctx: RunContext[AgentDeps], doc_id: int) -> dict:
    """【写】 Restore a deleted draft (undo soft delete; it reappears in lists).

    Cancelled placement tasks are **not** restored automatically — recreate them based on the current situation.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    t = admin_topic(ctx)
    # own_doc blocks deleted drafts (correctly: deleted ones shouldn't be edited), so restore bypasses that gate and checks ownership itself
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        raise ModelRetry("doc_id 必须是整数。") from None
    d = ctx.deps.db.get(TopicGeneratedDocORM, did)
    if d is None or d.topic_id != t.id:
        raise ModelRetry(f"稿件 {did} 不存在或不属于当前项目「{t.name}」。")
    if d.deleted_at is None:
        return {"ok": True, "modified": False, "doc_id": d.id,
                "message": "这篇稿没有被删除,不需要恢复。"}

    d.deleted_at = None
    d.deleted_by_id = None
    record_agent_action(ctx.deps, tool="admin_restore_doc", topic_id=t.id, risk="write",
                        args={"doc_id": d.id}, object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    return {"ok": True, "doc_id": d.id, "标题": _clip(d.title, 60), "状态": d.status,
            "说明": "已恢复,重新出现在列表里;被取消的投放任务不会自动恢复,需要的话重新建。"}


async def admin_list_deleted_docs(ctx: RunContext[AgentDeps], limit: int = 10) -> dict:
    """【读】 List this project's **deleted** drafts (soft-deleted, invisible on the frontend), to confirm what was deleted / recover it."""
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    t = admin_topic(ctx)
    rows = (
        ctx.deps.db.query(TopicGeneratedDocORM)
        .filter(TopicGeneratedDocORM.topic_id == t.id)
        .filter(TopicGeneratedDocORM.deleted_at.isnot(None))
        .order_by(TopicGeneratedDocORM.deleted_at.desc())
        .limit(_cap(limit, 10)).all()
    )
    return {"topic_name": t.name, "已删除篇数": len(rows),
            "docs": [{"doc_id": d.id, "标题": _clip(d.title, 60), "删除时的状态": d.status,
                      "删除时间": d.deleted_at} for d in rows],
            "说明": "用 admin_restore_doc 可以恢复。"}


# ══════════════════════════════════════════════════════════════════════
# Write tools: article placement (create task / backfill link / cancel / publish / paid media publishing)
# ══════════════════════════════════════════════════════════════════════

class PublishTargetIn(BaseModel):
    """A placement target: platform + media name (media name needed only for paid media)."""
    platform: str
    media_name: str = ""


async def admin_submit_publish_url(ctx: RunContext[AgentDeps], task_id: int, url: str) -> dict:
    """【写】 Backfill the real article link for a manual placement task. A post-placement retest is registered automatically afterwards."""
    from geo.services.publish_task_service import submit_url

    t = admin_topic(ctx)
    task = own_task(ctx, task_id)
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        raise ModelRetry("url 要是完整链接(http:// 或 https:// 开头)。")
    try:
        submit_url(ctx.deps.db, task, u, actor_id=ctx.deps.user_id)
    except ValueError as e:
        raise ModelRetry(f"回填失败:{e}") from None
    record_agent_action(ctx.deps, tool="admin_submit_publish_url", topic_id=t.id, risk="write",
                        args={"task_id": task.id, "url": u},
                        object_type="publish_task", object_id=task.id)
    ctx.deps.db.commit()
    return {"ok": True, "task_id": task.id, "平台": task.platform,
            "状态": task.status, "链接": task.published_url or u}


async def admin_auto_select_media(ctx: RunContext[AgentDeps], doc_id: int) -> dict:
    """【动作】 Auto-select media for an approved draft: pick paid media resources, push the draft, create pending-confirmation tasks. **No charge**.

    The actual charged order is a separate step (admin_publish_preview -> admin_publish_paid).
    """
    from geo.services.media_select import schedule_auto_select

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    if d.status != "approved":
        raise ModelRetry(f"稿件 {d.id} 当前是「{d.status}」,要先通过审核才能选媒。")
    schedule_auto_select(d.id, actor_id=ctx.deps.user_id)
    record_agent_action(ctx.deps, tool="admin_auto_select_media", topic_id=t.id, risk="act",
                        args={"doc_id": d.id}, object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    return {"ok": True, "doc_id": d.id,
            "说明": "自动选媒在后台跑(约 10–45 秒),完了用 admin_list_publish_tasks 看建了哪些任务。这一步不扣费。"}


async def admin_create_publish_tasks(ctx: RunContext[AgentDeps], doc_id: int,
                                     targets: list[PublishTargetIn],
                                     confirm_token: str = "") -> dict:
    """【动作】 Create placement tasks for a draft (platform + media); once created they enter the pending-publish queue.

    Idempotent: if the same draft already has a non-cancelled task for the same (platform, media), it is reused rather than duplicated.
    """
    from geo.services.publish_task_service import create_tasks

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    tgs = [x for x in (targets or []) if (x.platform or "").strip()]
    if not tgs:
        raise ModelRetry("targets 为空。至少给一个 {platform, media_name?};可用平台看 admin_source_insights。")
    if len(tgs) > _max_batch():
        raise ModelRetry(f"一次最多建 {_max_batch()} 条投放任务,请分批。")

    args = {"doc_id": d.id, "targets": [x.model_dump() for x in tgs]}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_create_publish_tasks",
            action_label=f"为《{_clip(d.title, 24)}》建 {len(tgs)} 条投放任务",
            args=args, topic=t,
            preview={"doc_id": d.id, "稿件状态": d.status,
                     "投放目标": [f"{x.platform}{' · ' + x.media_name if x.media_name else ''}" for x in tgs]},
            impact=["任务进待发布队列;付费媒体仍需另行核价下单才会真扣费"],
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_create_publish_tasks", args=args)
    if blocked is not None:
        return blocked

    rows = create_tasks(ctx.deps.db, d, t,
                        [{"platform": x.platform, "media_name": x.media_name} for x in tgs])
    record_agent_action(ctx.deps, tool="admin_create_publish_tasks", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    return {"ok": True, "doc_id": d.id, "任务数": len(rows),
            "tasks": [{"task_id": r.id, "平台": r.platform, "媒体": r.media_name or "",
                       "状态": r.status} for r in rows[:20]]}


async def admin_cancel_publish_task(ctx: RunContext[AgentDeps], task_id: int,
                                    confirm_token: str = "") -> dict:
    """【动作】 Cancel a placement task. Tasks already sent out (with a real link) cannot be cancelled."""
    t = admin_topic(ctx)
    task = own_task(ctx, task_id)
    if (task.published_url or "").strip() or task.status == "published":
        raise ModelRetry(
            f"投放任务 {task.id} 已经发出去了(有真实链接),不能取消 —— 线上内容还在。"
            "如实告诉用户:要下架得去对应平台操作。")

    args = {"task_id": task.id}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_cancel_publish_task", action_label="取消一条投放任务",
            args=args, topic=t,
            preview={"task_id": task.id, "平台": task.platform,
                     "媒体": task.media_name or "", "当前状态": task.status},
            impact=["任务标记为已取消,不会再被发布流程拾起"],
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_cancel_publish_task", args=args)
    if blocked is not None:
        return blocked

    task.status = "cancelled"
    record_agent_action(ctx.deps, tool="admin_cancel_publish_task", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="publish_task", object_id=task.id)
    ctx.deps.db.commit()
    return {"ok": True, "task_id": task.id, "状态": "cancelled"}


async def admin_publish_doc(ctx: RunContext[AgentDeps], doc_id: int,
                            targets: list[PublishTargetIn] | None = None,
                            urls: list[str] | None = None,
                            push_to_mediumsly: bool = False,
                            confirm_token: str = "") -> dict:
    """【动作·不可逆·对外】 Publish an approved draft: mark placement targets (optionally with real links);
    with push_to_mediumsly=True it is **really pushed to the own content site**.

    Governed by the env guardrail AGENT_ALLOW_EXTERNAL_PUBLISH: when off (default in test environments) it only marks, no real push.
    urls and targets correspond one-to-one in order; post-placement retest is registered only when there is a real link.
    """
    from geo.api import admin_content_review as acr
    from geo.models.ai_telemetry import PublishPayload, PublishTargetItem

    t = admin_topic(ctx)
    d = own_doc(ctx, doc_id)
    if d.status != "approved":
        raise ModelRetry(f"稿件 {d.id} 当前是「{d.status}」,要先通过审核才能发布。")
    tgs = [x for x in (targets or []) if (x.platform or "").strip()]
    us = [str(u).strip() for u in (urls or [])]
    if not tgs and not push_to_mediumsly:
        raise ModelRetry("要么给 targets(发到哪些平台),要么 push_to_mediumsly=True,不能都不给。")

    allow = os.environ.get("AGENT_ALLOW_EXTERNAL_PUBLISH", "0") == "1"
    real_push = bool(push_to_mediumsly) and allow

    args = {"doc_id": d.id, "targets": [x.model_dump() for x in tgs],
            "urls": us, "push_to_mediumsly": bool(push_to_mediumsly)}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_publish_doc",
            action_label=f"发布《{_clip(d.title, 24)}》", args=args, topic=t,
            preview={"doc_id": d.id, "标题": _clip(d.title, 60),
                     "投放目标": [f"{x.platform}{' · ' + x.media_name if x.media_name else ''}" for x in tgs],
                     "已有链接": us[:5] or "(无,发布后再回填)",
                     "推送自有内容站": bool(push_to_mediumsly),
                     "外发护栏": ("已开启,会真实推送" if allow else "未开启,只标记不真推")},
            impact=["稿件状态变为已发布", "带真实链接时会登记投放后复测"]
                   + (["会把正文真实推送到自有内容站"] if real_push else []),
            irreversible=(["推送出去的内容需要去对应站点手工删除"] if real_push else []),
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_publish_doc", args=args)
    if blocked is not None:
        return blocked

    payload = PublishPayload(
        publish_targets=[
            PublishTargetItem(platform=x.platform, media=x.media_name,
                              url=(us[i] if i < len(us) else ""))
            for i, x in enumerate(tgs)
        ],
        push_to_mediumsly=real_push,
    )
    out = await call_route(acr.publish_doc, doc_id=d.id, payload=payload,
                           admin=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    record_agent_action(ctx.deps, tool="admin_publish_doc", topic_id=t.id, risk="act",
                        args=args, confirmed=True, object_type="doc", object_id=d.id)
    ctx.deps.db.commit()
    res = {"ok": True, "doc_id": d.id, "状态": getattr(out, "status", "published"),
           "投放目标数": len(tgs)}
    if push_to_mediumsly and not allow:
        res["blocked_external"] = True
        res["_note"] = ("外发护栏未开启(测试环境默认),**没有**真实推送到自有内容站,"
                        "只标记了发布状态。请如实告诉用户。")
    return res


async def admin_publish_preview(ctx: RunContext[AgentDeps], task_id: int) -> dict:
    """【读·核价】 Price check before paid media publishing: re-fetch resources, get live prices, check balance and gate, obtain a price-bound pricing token.

    The pricing token is valid for 10 minutes; if it expires or the price rises, re-check before confirming.
    """
    from geo.api import rwj_channel

    t = admin_topic(ctx)
    task = own_task(ctx, task_id)
    out = await call_route(rwj_channel.publish_preview, task_id=task.id,
                           current_user=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        return out
    data = out.model_dump() if hasattr(out, "model_dump") else dict(out or {})
    return {
        "topic_name": t.name, "task_id": task.id,
        "平台": task.platform, "媒体": task.media_name or "",
        "单价": data.get("price_yuan") or data.get("price"),
        "价格变了": data.get("price_changed"),
        "平台余额": data.get("balance"),
        "扣费闸门": data.get("publish_enabled"),
        "rwj_confirm_token": data.get("confirm_token"),
        "_note": "把单价、余额、是否涨价如实转述给用户,他确认后再调 admin_publish_paid。",
    }


async def admin_publish_paid(ctx: RunContext[AgentDeps], task_id: int,
                             rwj_confirm_token: str, confirm_token: str = "") -> dict:
    """【动作·付费外发·不可逆】 Place a real media order per the pricing result; **really deducts the platform balance**, and can't be recalled once sent.

    You must first call admin_publish_preview to get rwj_confirm_token (it is bound to the price, valid for 10 minutes).
    """
    from geo.api import rwj_channel

    t = admin_topic(ctx)
    task = own_task(ctx, task_id)
    if os.environ.get("AGENT_ALLOW_PAID_PUBLISH", "0") != "1":
        return {"ok": False, "modified": False, "blocked": True, "code": "AGENT_PAID_OFF",
                "message": "运营台的付费下单护栏未开启,这一单**没有**真的下。"
                           "如实告诉用户,请他在页面上操作或联系管理员开启护栏。"}
    if not (rwj_confirm_token or "").strip():
        raise ModelRetry("缺少核价令牌。先调 admin_publish_preview 核价,拿到 rwj_confirm_token 再来。")

    args = {"task_id": task.id}
    if not confirm_token:
        return needs_confirm(
            ctx.deps, tool="admin_publish_paid",
            action_label=f"付费发媒:{task.platform} · {task.media_name or ''}",
            args=args, topic=t,
            preview={"task_id": task.id, "平台": task.platform,
                     "媒体": task.media_name or "", "当前状态": task.status},
            impact=["向付费媒体真实下单发稿"],
            irreversible=["下单后无法撤回,发出去的内容要去媒体方处理"],
            cost={"currency": "CNY", "note": "按 admin_publish_preview 核到的单价从平台余额扣"},
        )
    blocked = guard(ctx.deps, confirm_token, tool="admin_publish_paid", args=args)
    if blocked is not None:
        return blocked

    from geo.api.rwj_channel import PublishPayload as RwjPublishPayload

    out = await call_route(rwj_channel.publish_task, task_id=task.id,
                           payload=RwjPublishPayload(confirm_token=rwj_confirm_token),
                           current_user=admin_actor(ctx), db=ctx.deps.db)
    if route_failed(out):
        # Price changed / pricing expired -> let the model re-check the price instead of throwing the error code at the user
        code = out.get("code")
        if code in ("PRICE_CHANGED", "TOKEN_EXPIRED"):
            out["_note"] = "请重新调 admin_publish_preview 核价,把**新价格**告诉用户再确认,绝不能拿旧价下单。"
        return out
    record_agent_action(ctx.deps, tool="admin_publish_paid", topic_id=t.id, risk="paid",
                        args=args, confirmed=True, object_type="publish_task", object_id=task.id)
    ctx.deps.db.commit()
    return {"ok": True, "task_id": task.id, "状态": task.status,
            "说明": "已真实下单;出稿后回执会回填链接,用 admin_list_publish_tasks 看。"}

# ── Tool list (referenced by agent.py). New tools must be put in the matching risk group. ─────────
# Groups are the guardrail: _line_tools("admin") decides whether to grant the write group based on can_write,
# and the paid group is additionally loaded based on AGENT_ALLOW_PAID_PUBLISH (when off, the model can't even see the schema).

ADMIN_READ_TOOLS = [
    admin_topic_overview,
    admin_get_plan,
    admin_list_plan_items,
    admin_plan_basis,
    admin_plan_candidates,
    admin_list_docs,
    admin_get_doc,
    admin_placement_strategy,
    admin_source_insights,
    admin_source_strategy,
    admin_media_candidates,
    admin_list_publish_tasks,
    admin_publish_channel_status,
    admin_list_deleted_docs,
]

# Reversible writes: mistakes can be undone, execute directly without second confirmation (frequent small ops; asking every time is annoying)
ADMIN_WRITE_TOOLS = [
    admin_update_plan_item,
    admin_add_plan_item,          # Escalates to two-step confirmation itself when generate_now=True
    admin_insert_plan_candidates,
    admin_push_infra_actions,
    admin_update_doc,
    admin_select_docs_for_review,
    admin_submit_publish_url,
    admin_restore_doc,
]

# Irreversible / external / spends quota: all go through two-step confirmation (confirm.py)
ADMIN_ACT_TOOLS = [
    admin_remove_plan_items,
    admin_reorder_plan,           # Escalates to confirmation only when it would detach drafted articles
    admin_confirm_plan,
    admin_batch_generate_docs,
    admin_approve_doc,
    admin_reject_doc,
    admin_regenerate_doc,
    admin_delete_doc,
    admin_auto_select_media,
    admin_create_publish_tasks,
    admin_cancel_publish_task,
    admin_publish_doc,
]

# Paid external publishing: really deducts the platform balance. **Listed separately**, loaded per env.
ADMIN_PAID_TOOLS = [
    admin_publish_preview,
    admin_publish_paid,
]

# Compatibility with old references / unit tests: all non-paid tools
ADMIN_TOOLS = ADMIN_READ_TOOLS + ADMIN_WRITE_TOOLS + ADMIN_ACT_TOOLS
