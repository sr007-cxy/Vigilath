"""Tool implementations (for the LLM, Pydantic AI @RunContext[AgentDeps]).

Conventions:
  - Read tools call directly; write tools first run usage_guardrail_check; tools receiving resource ids first run assert_owns.
  - Inputs strictly typed; on invalid input raise ModelRetry(specific reason) so DeepSeek corrects and retries (fixes tool drift).
  - Tools only "read / propose writes"; side effects like publishing go through the Method layer, and the model has no publish tool.

This file first lands the first real tool run_geo_checks; the rest are added one by one per docs/07-复盘归档/Agent/实现设计-2026-06.md §5;
unimplemented ones are ModelRetry/NotImplemented placeholders that take effect once registered (drop-in).
"""
from __future__ import annotations

import asyncio
import os
import threading

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext

from agents.geo.deps import AgentDeps
from agents.geo.methods import usage_guardrail_check

# geo_checker currently uses module-level global state (state._scores + _geo_checker_lock), so concurrency causes crosstalk.
# ★ W1 hard prerequisite: session scoping. Until then serialize with a process-level lock to guarantee correctness (sacrificing parallelism).
_geo_checks_lock = threading.Lock()

# Cache of the last successful hot-topic result (source -> result): silently fall back during fetch cooldown/jitter without exposing failures to users.
# In-process (one per worker); hot-list data is a recent crawl anyway, so showing the fallback is harmless.
_hot_cache: dict[str, dict] = {}
# Cache of the last successful today's-sentiment result (account_id -> result): likewise, silently fall back during transient sentinel jitter.
_sent_today_cache: dict[int, dict] = {}


# ── Admission for seed prompts / persisting selected terms to DB ───────────────
# Before 2026-08-25 this was a **global sales gate** (AGENT_GATE_PROMPT_CONFIG defaulted to 1):
# users could create topics and preview query expansion, but "set seed terms" and "persist selected terms" were blocked with a prompt to contact sales.
# So the self-service loop "create topic -> fill seed terms -> batch run" **never got past step two** in conversation.
#
# Now it's decided per channel (gates.py): our own web app and ops console pass, external embed / IM / skill marketplace are still blocked.
# The reason for blocking changed too — not "hasn't paid", but "whoever's talking in those channels isn't necessarily the account owner".
_SALES_CONTACT = (os.environ.get("AGENT_SALES_CONTACT") or "请联系您的专属销售顾问开通").strip()


def _sales_gate(feature: str) -> dict:
    """Gate hit: don't change data; return a structured result guiding to contact sales, for the model to relay truthfully (no retry, no error)."""
    return {"gated": True, "feature": feature, "modified": False,
            "message": f"「{feature}」是需要开通的付费能力,当前账号暂未开通,我无法直接为你操作。{_SALES_CONTACT}。"}


class TopicInfo(BaseModel):
    """Basic topic information.

    ★ `target` is the **search term / brand short name**, not the official site — the column name is misleading, see
      the module comment of `geo.services.topic_site`. Always read the official site from `website`.
    """
    topic_id: int
    name: str
    target: str                       # Search term / brand short name (backfilled from "company/brand short name" when the topic is created)
    industry: str = ""
    website: str = ""                 # Brand official site; empty = this topic has no official site filled in (a valid state)


def _topic_info(topic) -> TopicInfo:
    from geo.services.topic_site import topic_brand_term, topic_website

    return TopicInfo(topic_id=topic.id, name=topic.name,
                     target=topic_brand_term(topic), industry=topic.industry or "",
                     website=topic_website(topic))


class UiAction(BaseModel):
    """A page action directive: action = action name (must be among the available actions in 【页面上下文】 ("page context")), args = action arguments."""
    action: str
    args: dict = {}


async def control_page(ctx: RunContext[AgentDeps], actions: list[UiAction]) -> dict:
    """【动作】 Operate the **current frontend page** for the user (fill forms / adjust filters / switch topic / navigate / click buttons).

    Call only when the user explicitly asks you to "fill / select / switch / navigate / click" on the current page and 【页面上下文】 ("page context") lists a matching available action.
    - action may only be a name listed under "available actions" in 【页面上下文】 ("page context"); give args with the fields that action needs (e.g. setField needs {name, value}).
    - Several directives can be sent at once and run in order (e.g. setField several fields, then submit).
    - The frontend really executes them (including submit), so values must be accurate; ask the user about uncertain values instead of guessing.
    - Without 【页面上下文】 ("page context") or when the action isn't in the available list, tell the user truthfully this page can't do it; don't pretend you clicked.
    """
    pc = ctx.deps.page_context or {}
    available = {str(a) for a in (pc.get("actions") or [])}
    if not available:
        raise ModelRetry("当前页面没有可用的页面动作,无法替用户操作页面;请如实告知用户。")
    bad = [a.action for a in actions if a.action not in available]
    if bad:
        raise ModelRetry(f"动作 {bad} 在当前页面不可用。可用动作:{sorted(available)}。只能用这些。")
    return {"ui_actions": [a.model_dump() for a in actions], "count": len(actions)}


class GeoCheckResult(BaseModel):
    url: str
    raw: dict  # Raw scores from generate_score (total + sub-scores); TODO: converge into a stable schema


def _admin_scope_only(ctx: RunContext[AgentDeps], tool: str) -> None:
    """Hard refusal of customer-side **write** tools on the ops console line.

    _account_topic is already scope-aware (see below), which lets 12 read-only tools work on customer projects unchanged;
    the cost is that write tools using the same helper would become cross-project write holes, with metric definitions diverging from admin endpoints —
    _review_doc doesn't trigger publish_preset.apply_preset_on_approve or send notifications, so ops reviewing with it
    = draft approved but not a single placement task created, with nothing abnormal visible on the page.

    The third of three lines of defense (the first two: _line_tools("admin")'s allowlist excludes _GEO_WRITE;
    eval/test_admin_scope.py asserts their intersection is empty). If any one falls, the other two still hold.
    """
    if ctx.deps.scope == "admin":
        raise ModelRetry(
            f"运营台请改用 admin_* 工具(口径不同,{tool} 只服务客户自助线)。"
            "如实告诉用户你会用运营台对应的能力来做。"
        )


def _account_topic(ctx: RunContext[AgentDeps]):
    """Get the current topic.

    With scope="admin" it delegates to scope.admin_topic: checks role, not ownership (the project belongs to the customer, not ops),
    the project always comes from deps.locked_topic_id, and **no fallback without a topic**. This one line lets 12 already-live, already-verified
    read-only tools be reused directly in the ops console without copying into admin_* versions (copies would inevitably diverge).

    With scope="self" it's the original logic: prefer deps.topic_id, otherwise the account's most recently batch-run topic. Includes ownership check.
    """
    if ctx.deps.scope == "admin":
        from agents.geo.scope import admin_topic
        return admin_topic(ctx)

    from geo.models.ai_telemetry import AiTelemetryTopicORM

    db, acc = ctx.deps.db, ctx.deps.account_id
    if ctx.deps.topic_id:
        t = db.get(AiTelemetryTopicORM, ctx.deps.topic_id)
        if t is None or t.user_id != acc:
            raise ModelRetry("指定主题不存在或不属于当前账号。")
        return t
    # Multi-topic accounts: prefer the "most recently batch-run" topic (most likely what the user is looking at), then the newest created; avoid picking an old empty topic
    return (
        db.query(AiTelemetryTopicORM)
        .filter(AiTelemetryTopicORM.user_id == acc)
        .order_by(AiTelemetryTopicORM.last_run_at.desc().nullslast(), AiTelemetryTopicORM.id.desc())
        .first()
    )


def _max_topics() -> int:
    try:
        return max(1, int(os.environ.get("AGENT_MAX_TOPICS_PER_ACCOUNT", 5)))
    except (TypeError, ValueError):
        return 5


async def create_topic(ctx: RunContext[AgentDeps], name: str, url: str = "",
                       industry: str = "") -> TopicInfo:
    """【写】 Create a brand topic. If one with the same name exists, return it directly without duplicating.

    name: brand/project name (required); url: official site address, **leave empty if there is none** (common for offline service providers
    and merchants with only a WeChat OA or mini program; when empty, diagnosis skips the four site-side items and the AI side runs as usual);
    industry: industry (optional; if filled it can serve as an industry term).

    The official site is written into the topic profile's "brand official site" field and can be changed later with set_topic_website.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM
    from geo.services.topic_site import set_topic_website as _set_site

    usage_guardrail_check(ctx.deps, "create_topic")
    db, acc = ctx.deps.db, ctx.deps.account_id
    if not (name or "").strip():
        raise ModelRetry("name 不能为空。")

    # Same-name idempotency: model retries or the user repeating themselves shouldn't create an extra empty topic
    same = (db.query(AiTelemetryTopicORM)
            .filter(AiTelemetryTopicORM.user_id == acc,
                    AiTelemetryTopicORM.name == name.strip()).first())
    if same is not None:
        ctx.deps.topic_id = same.id
        # When a same-name topic exists, still fill in the official site the user brought this time (it was most likely left empty when created)
        if (url or "").strip():
            from geo.services.topic_site import topic_website as _site
            if not _site(same):
                _set_site(same, url)
                db.commit()
        return _topic_info(same)

    # 2026-08-25 — this used to hard-limit each account to 1 topic, so a second brand couldn't be created in conversation at all.
    # Now it's a soft cap: when exceeded, **explain clearly** instead of refusing, and let the user decide whether to deactivate old ones.
    n = db.query(AiTelemetryTopicORM).filter(AiTelemetryTopicORM.user_id == acc).count()
    cap = _max_topics()
    if n >= cap:
        raise ModelRetry(
            f"当前账号已经有 {n} 个主题,达到上限 {cap} 个。"
            f"用 list_topics 看看现有的,先停用一个再建,或者直接用已有的那个。"
        )

    # ★ target is a **search term**, not the official site. This used to say `target=url`,
    #   so batch runs asked the AI "how is https://xxx.com" with a URL. The official site is stored separately in the profile.
    t = AiTelemetryTopicORM(user_id=acc, name=name.strip(),
                            target=name.strip(), industry=industry or "")
    if (url or "").strip():
        _set_site(t, url)
    db.add(t)
    db.commit()
    db.refresh(t)
    ctx.deps.topic_id = t.id  # Later tools in the same conversation reuse it directly
    return _topic_info(t)


async def list_topics(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 List all topics (brands/projects) of the current account.

    In multi-topic accounts, **money-spending actions must first confirm which topic** — read-only tools may guess one by "most recent batch run",
    money-spending ones may not; a wrong guess means drafts published for the wrong topic. Use this tool to list them and ask the user to choose.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    rows = (ctx.deps.db.query(AiTelemetryTopicORM)
            .filter(AiTelemetryTopicORM.user_id == ctx.deps.account_id)
            .order_by(AiTelemetryTopicORM.last_run_at.desc().nullslast(),
                      AiTelemetryTopicORM.id.desc()).all())
    from geo.services.topic_site import topic_brand_term, topic_website

    return {
        "数量": len(rows),
        "当前选中": ctx.deps.topic_id,
        # The "official site" used to be taken from target, and target is the brand short name — users saw their own short name as the official site.
        # The two fields now each stay in place; if the official site is empty it's truthfully empty (set_topic_website can fill it).
        "主题": [{"topic_id": r.id, "名称": r.name,
                  "品牌简称": topic_brand_term(r),
                  "官网": topic_website(r) or "(未填)",
                  "行业": r.industry or "", "最近跑批": str(r.last_run_at or "")[:19]}
                 for r in rows],
    }


async def switch_topic(ctx: RunContext[AgentDeps], topic_id: int) -> dict:
    """【写】 Switch subsequent operations to the given topic. When the user says "look at brand XX", switch first."""
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    try:
        tid = int(topic_id)
    except (TypeError, ValueError):
        raise ModelRetry("topic_id 必须是整数,先用 list_topics 拿真实 id。")
    t = ctx.deps.db.get(AiTelemetryTopicORM, tid)
    if t is None or t.user_id != ctx.deps.account_id:
        raise ModelRetry(f"主题 {tid} 不存在或不属于当前账号。用 list_topics 看看有哪些。")
    ctx.deps.topic_id = t.id
    return {"已切换": True, "topic_id": t.id, "名称": t.name}


async def get_topic(ctx: RunContext[AgentDeps]) -> TopicInfo | None:
    """Read the current account's topic (returns null if none, guiding the user to create_topic).

    ★ When the user asks "what's my official site / is the official site filled in" -> look at the `website` field, **not** `target`.
      `target` is the brand short name / search term. If website is empty say truthfully "the topic has no official site yet",
      and offer to fill it via set_topic_website; don't read out the short name as the official site.
    """
    t = _account_topic(ctx)
    if t is None:
        return None
    return _topic_info(t)


async def set_topic_website(ctx: RunContext[AgentDeps], url: str,
                            topic_id: int | None = None) -> TopicInfo:
    """【写】 Set / change the topic's **brand official site**. Reversible and free; change it directly once the user gives the URL.

    Use this tool when the user says "my official site is xxx.com / the official site is wrong / change the official site to..." —
    **it can be done right in the conversation**; don't send them to fill a web form themselves (that was the old answer, no longer correct).

    The official site determines what the four site-side items of the one-click full diagnosis test (site health check / answer engine optimization / crawler reachability /
    off-site authority); a wrong one means those four run for nothing.

    url: full URL or bare domain (https:// added automatically); empty string = clear it ("no official site").
    """
    from geo.services.topic_site import set_topic_website as _set_site

    usage_guardrail_check(ctx.deps, "set_topic_website")
    topic = _account_topic(ctx) if topic_id is None else None
    if topic_id is not None:
        from geo.models.ai_telemetry import AiTelemetryTopicORM
        topic = ctx.deps.db.get(AiTelemetryTopicORM, int(topic_id))
        if topic is None or topic.user_id != ctx.deps.account_id:
            raise ModelRetry("指定主题不存在或不属于当前账号。先用 list_topics 查清 id。")
    if topic is None:
        raise ModelRetry("当前账号还没有主题,先问用户要不要建一个(create_topic)。")
    site = _set_site(topic, url)
    if site:
        from geo.utils.validator import validate_url
        if not validate_url(site):
            raise ModelRetry(f"「{url}」不像一个可用的网址,让用户确认一下完整地址。")
    ctx.deps.db.commit()
    return _topic_info(topic)


async def run_geo_checks(ctx: RunContext[AgentDeps], categories: list[str] | None = None) -> GeoCheckResult:
    """【已下线,勿调】 The old 25-item official site health check. Audits all go through run_full_diagnosis (one-click full diagnosis).

    The function body is kept only for compatibility with old references; removed from the external toolset.
    """
    from geo_checker.orchestrate import generate_score

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    url = (topic.target or "").strip()
    if not url:
        raise ModelRetry("主题缺少目标站点 URL,无法跑检查。")

    def _run() -> dict:
        with _geo_checks_lock:   # Serialize: avoid crosstalk from geo_checker's module-level global state (removable after W1 session scoping)
            return generate_score(url, allowed_categories=categories)

    raw = await asyncio.to_thread(_run)
    return GeoCheckResult(url=url, raw=raw if isinstance(raw, dict) else {"result": raw})


async def set_seed_prompts(ctx: RunContext[AgentDeps], prompts: list[str]) -> dict:
    """【写·付费】 Set/overwrite the current topic's seed prompts. **Paid capability**: non-activated accounts are blocked by the gate and get guidance to contact sales; nothing is actually set.

    prompts: list of seed terms (category/brand-related phrases).
    """
    import json
    from agents.geo import gates

    hit = gates.require(ctx.deps, "prompt_config")
    if hit is not None:
        return hit
    usage_guardrail_check(ctx.deps, "set_seed_prompts")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    cleaned = [p.strip() for p in prompts if p and p.strip()]
    if not cleaned:
        raise ModelRetry("prompts 不能为空。")
    topic.seed_prompts_json = json.dumps(cleaned, ensure_ascii=False)
    ctx.deps.db.commit()
    return {"topic_id": topic.id, "seed_prompts": cleaned}


async def get_prompts(ctx: RunContext[AgentDeps]) -> dict:
    """Read the current topic's **seed prompts + expanded queries**: counts + lists + how many each seed expanded into. Read-only.

    Use this tool to answer "how many seed terms / how many queries / how many expanded / list the prompts".
    """
    import json

    topic = _account_topic(ctx)
    if topic is None:
        return {"seed_count": 0, "query_count": 0, "seeds": [], "queries": []}
    seeds = _seed_texts(topic.seed_prompts_json)
    raw_q = json.loads(topic.queries_json or "[]")
    # Soft-deleted terms don't count toward "placement/monitoring question count" — this metric definition is stated to align with the dashboard, which filters them
    raw_q = [q for q in raw_q if not (isinstance(q, dict) and q.get("deleted_at"))]
    query_texts = [q["text"] for q in raw_q if isinstance(q, dict) and q.get("text")]
    # How many queries each seed expanded into (traced back via query.seed)
    by_seed: dict[str, int] = {}
    for q in raw_q:
        if isinstance(q, dict) and q.get("text"):
            by_seed[q.get("seed") or "(未归属种子)"] = by_seed.get(q.get("seed") or "(未归属种子)", 0) + 1
    return {
        "topic_id": topic.id,
        "seed_count": len(seeds),
        "query_count": len(query_texts),            # = placement/monitoring question count (aligned with dashboard)
        "expansion_count": len(query_texts),         # Total expanded queries
        "seeds": seeds,                              # Seed term list
        "expansion_by_seed": by_seed,               # How many each seed expanded into
        "queries": query_texts[:80],                # Query list (first 80)
        "note": (f"{len(seeds)} 个种子词,扩展出 {len(query_texts)} 个 query"
                 + ("(列表已截断,只显示前 80)" if len(query_texts) > 80 else "")),
    }


async def expand_prompts(ctx: RunContext[AgentDeps], seed: str,
                         count_per_scene: int = 50) -> dict:
    """【写*】 Run 4-dimension scenario expansion (search/qa/intent/brand) on a seed term and return candidate queries to pick from (candidates only, not persisted to DB; use set_selected_queries to persist).

    seed: the seed term to expand; count_per_scene: items per scenario (5–50, defaults to the max).

    ★ The default changed from 10 to 50 on 2026-08-30 — aligned with the web app. The web "prompt expansion" defaults to
      50/scenario (SceneExpander.tsx), 4 scenarios = 200 items, which is exactly the backend cap
      (MAX_EXPANSION_CANDIDATES). When the conversation default was 10, the same seed gave 200 items on the web
      and 20–40 in conversation, and users' immediate reaction was "the agent side is shortchanged", while it was only a default.
      When the topic has no target the brand scenario is skipped, and with cross-scenario dedup the actual count is a bit under 200.

    Engines/scheduling are fixed by the platform, unrelated to expansion. After picking, persist with set_selected_queries
    (that step starts continuous batch runs and charges credits per round, so it goes through two-step confirmation).
    """
    from geo.services.topic_expand import expand_topic_seed

    usage_guardrail_check(ctx.deps, "expand_prompts")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    try:
        res = await expand_topic_seed(topic, seed, count_per_scene=count_per_scene)
    except ValueError as e:
        raise ModelRetry(str(e))

    flat: list[dict] = []
    seen: set[str] = set()
    for scene, info in (res.get("scenes") or {}).items():
        for q in (info.get("queries") or []):
            if q and q not in seen:
                seen.add(q)
                flat.append({"text": q, "scene": scene})
    return {"seed": res["seed"], "total": res["total_count"], "candidates": flat,
            "note": "这些**只是候选,还没落库**,不花钱。选定之后用 set_selected_queries 落库,"
                    "那一步会开启持续监测跑批、按轮扣积分,会先出确认卡。"}


async def set_selected_queries(ctx: RunContext[AgentDeps], queries: list[str],
                               confirm_token: str = "") -> dict:
    """【写·开启持续扣费】 Persist the selected queries to the current topic, **entering continuous monitoring batch runs from now on**. Existing ones aren't added twice.

    This isn't a one-off action: once persisted, these questions run round after round on the topic's schedule, each round charging credits by
    the number of "prompt × engine" Q&As. Hence **two-step confirmation**: first call without confirm_token to get a preview,
    read "how many added / how many Q&As and credits per round afterwards / when it starts / how to stop" to the user and **end this turn**;
    after their next message explicitly confirms, call again with the same params + confirm_token.

    ★ After persisting it **doesn't start immediately**; it waits for the next cycle anchor — the user must be told this,
      otherwise they'll stare at the page waiting for a result that won't come that day. For cadence details use get_monitor_schedule.
      Running a round immediately is a different tool: trigger_batch_run.

    When the channel isn't allowed it returns `blocked: true` (not a single byte of data changed); just relay it truthfully.
    """
    import json
    from datetime import datetime

    from agents.geo import confirm, gates
    from agents.geo.tools_billing import quote

    hit = gates.require(ctx.deps, "prompt_config")
    if hit is not None:
        return hit
    usage_guardrail_check(ctx.deps, "set_selected_queries")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    cleaned = [q.strip() for q in queries if q and q.strip()]
    if not cleaned:
        raise ModelRetry("queries 不能为空。")

    args = {"queries": cleaned}

    def _preview() -> dict:
        from geo.services.topic_schedule import describe, selected_query_count

        try:
            engines = len(json.loads(topic.engines_json or "[]"))
        except Exception:  # noqa: BLE001
            engines = 0
        # Total after persisting = already selected + newly added this time (upper bound before dedup, good enough for the preview)
        after = selected_query_count(topic) + len(cleaned)
        q = quote(ctx.deps.db, ctx.deps.user_id, "batch", max(1, after * engines))
        return confirm.needs_confirm(
            ctx.deps, tool="set_selected_queries",
            action_label=f"把 {len(cleaned)} 条问题加入持续监测",
            args=args, topic=topic,
            preview={"主题": topic.name, "这次新增": len(cleaned),
                     "问题示例": cleaned[:5],
                     "落库后提示词数(上限估计)": after,
                     "节奏": describe(topic, ctx.deps.db)},
            impact=["这些问题会**按调度一轮轮持续跑**,不是跑一次就完",
                    "每轮按「提示词 × 引擎」的问答数扣积分,次日结算",
                    "落库后不会立刻开跑,要等下一个周期锚点"],
            irreversible=["提示词一旦确认即锁定,不能再改文案"],
            cost={**{k: v for k, v in q.items() if not k.startswith("_")},
                  "口径": "这是**落库后每一轮**的估算,不是一次性费用"},
            note="要停就到主题设置页把定时检测关掉;调度频率在对话里改不了。",
        )

    if not confirm_token:
        return _preview()
    stop = confirm.guard(ctx.deps, confirm_token, tool="set_selected_queries",
                         args=args, rebuild_preview=_preview)
    if stop is not None:
        return stop

    existing = json.loads(topic.queries_json or "[]")
    # ★ The dedup set only counts non-soft-deleted terms: re-adding a soft-deleted term = restoring it (same rule as _queries_with_meta),
    #   otherwise "adding it back" would be as if nothing happened — the term exists, but the whole chain still filters it out as deleted
    by_text = {q["text"] for q in existing
               if isinstance(q, dict) and q.get("text") and not q.get("deleted_at")}
    now = datetime.utcnow().isoformat()
    added = 0
    restored = 0
    for q in cleaned:
        if q in by_text:
            continue
        hit = next((it for it in existing
                    if isinstance(it, dict) and it.get("text") == q), None)
        if hit is not None:                    # Previously soft-deleted term -> restore
            hit.pop("deleted_at", None)
            hit.pop("deleted_by_id", None)
            restored += 1
        else:
            existing.append({"text": q, "created_at": now, "status": "approved", "approved_at": now})
            added += 1
        by_text.add(q)
    topic.queries_json = json.dumps(existing, ensure_ascii=False)
    ctx.deps.db.commit()

    from geo.services.topic_schedule import describe

    return {"topic_id": topic.id, "added": added, "restored": restored,
            "query_count": len(existing),
            "调度": describe(topic, ctx.deps.db),
            "note": "已落库。**现在不会立刻开跑** —— 按「调度」里写的节奏等下一轮,"
                    "这句一定要告诉用户。想立刻跑一轮用 trigger_batch_run(会先报价确认)。"}


# ── One-click full diagnosis: the only "audit" entry on the agent side ────────────────
# The agent only starts it; orchestration runs in the main backend process: the batch run center URL, Doubao ARK key and egress proxy
# env vars are configured only on geo-backend.service, not the agent unit — running a thread inside the agent process
# would yield reports missing engines and competitors. So here we sign a short-lived user token and hit the main backend's public endpoint,
# behaving exactly like the web "one-click full diagnosis" (including tier checks).
GEO_BACKEND_URL = os.environ.get("GEO_BACKEND_INTERNAL_URL", "http://127.0.0.1:8000")


def _site_origin() -> str:
    """Root URL of the site the user visits in the browser — used to build clickable report links.

    **Not** GEO_BACKEND_URL: that's the local private direct address (127.0.0.1:8000),
    and pasting it to the user gives them a link they can't open.
    """
    return (os.environ.get("PUBLIC_SITE_ORIGIN") or "https://vigilath.cn").strip().rstrip("/")

# Four site-side items + four AI-side items, in the same order as full_diagnosis_generator.SUB_MODES
_FD_STEP_ZH = {
    "base": "官网体检", "aeo": "答案引擎优化", "crawlTest": "智能爬虫可达性",
    "authority": "站外权威", "visibility": "AI 可见性实测", "entity": "品牌实体认知",
    "competitiveIntel": "竞品情报", "sentiment": "舆情",
}
_FD_DIM_ZH = {
    "ai_visibility": "AI搜索可见度", "infrastructure": "基建完善度",
    "authority": "权威背书", "competitive": "竞品对比优势", "sentiment": "舆情健康度",
}


def _fd_industry_terms(topic) -> list[str]:
    """Industry term fallback: topic industry field -> seed terms. Returns empty if neither is available; the model asks the user."""
    import re

    out: list[str] = []
    ind = (getattr(topic, "industry", "") or "").strip()
    if ind:
        out += [x.strip() for x in re.split(r"[,,、;;/|\s]+", ind) if x.strip()]
    if not out:
        out += [s for s in _seed_texts(getattr(topic, "seed_prompts_json", None)) if s]
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:5]


# Shape features of "this isn't an industry term". An industry term should be a **category** (beef and mutton / elevator retrofits for old buildings / commercial floor-cleaning robots),
# not a **question** (「<brand-d>羊肉哪家好」, "which <brand-d> lamb is best").
#
# Why block it in the tool rather than say it in the prompt: in report #99 on 2026-08-27, the model itself drafted
# 「<brand-d>羊肉哪家好」 in its suggestions, the user copied it and started the run, and the model even **warned itself** "the characters read as <brand-d>, which may
# make engines think of the dairy brand <brand-d>" — and then ran it anyway. Warnings don't stop it; the tool has to.
# As a result all eight sub-audits of the run asked the AI with that question: the narrative layer wrote beef and mutton as goat milk powder (「奶粉」 ("milk powder") appeared 19 times,
# 「羊肉」 ("mutton") 3 times), and all 12 pieces of evidence collected by sentiment monitoring were about <namesake-company>.
_FD_TERM_QUESTIONY = ("哪家好", "哪个好", "怎么样", "好不好", "推荐", "排行", "排名",
                      "十大", "怎么选", "如何选", "值得买", "?", "?")
_FD_TERM_MAXLEN = 12


def _fd_bad_terms(terms: list[str]) -> list[str]:
    """Pick out the ones that "don't look like category terms". Empty return = all usable."""
    bad = []
    for t in terms:
        t = (t or "").strip()
        if not t:
            continue
        if len(t) > _FD_TERM_MAXLEN or any(k in t for k in _FD_TERM_QUESTIONY):
            bad.append(t)
    return bad


def _fd_cost_estimate(ctx: RunContext[AgentDeps]) -> dict:
    """Credit estimate for the one-click diagnosis — **same source and metric definition** as the actual backend charge.

    The actual charge is in `full_diagnosis_generator._charge_full_diagnosis`: Q&A count = number of actually planned
    terms × number of engines, unit price `batch_cell`. Here the terms haven't been planned yet, so estimate the upper bound with the planner's target count
    `TARGET_QUERY_COUNT` — the same number as the early-interception estimate in `full_diagnosis._ensure_can_afford_full_diagnosis`,
    so "what the preview says" and "what the 402 says when insufficient" always match.
    """
    try:
        from geo.models.balance import PRICE_BATCH_CELL
        from geo.services import balance_service
        from geo.services.full_diagnosis_competitive import _engines
        from geo.services.full_diagnosis_queries import TARGET_QUERY_COUNT

        db = ctx.deps.db
        queries = int(TARGET_QUERY_COUNT)
        engines = max(1, len(_engines()))
        unit = int(balance_service.get_price(db, PRICE_BATCH_CELL))
        total = queries * engines * unit
        bal = int(balance_service.get_balance(db, ctx.deps.user_id))
    except Exception:  # noqa: BLE001 — a pricing failure must never blow up the confirmation flow; state it truthfully on the card
        return {"cost": {"积分": "这一单的报价暂时取不到,**如实告诉用户拿不到准确价格**,"
                                 "别自己估一个数"},
                "total_cents": 0, "balance_cents": None}
    return {
        "cost": {
            "计费口径": f"{queries} 词(上限) × {engines} 引擎 × {unit} 积分/次问答",
            "合计上限": f"{total} 积分",
            "当前积分": f"{bal} 积分",
            "扣后剩余": f"{bal - total} 积分" if bal >= total else None,
            "积分是否够": bal >= total,
            "另占": "诊断名额 1 次",
        },
        "total_cents": total, "balance_cents": bal,
    }


def _fd_latest(ctx: RunContext[AgentDeps], report_id: int | None = None,
               brand: str = ""):
    """The account's most recent diagnosis. If brand is passed, search only within that brand.

    ★ The brand parameter prevents "order stealing": if the pre-start "is a run already in progress?" check ignored the brand,
      brand A's generating run would swallow brand B's new request entirely and return A's progress.
      Progress checks (get_full_diagnosis) don't pass brand, keeping the old "most recent run" semantics.
    """
    from geo.models.full_diagnosis import FullDiagnosisReportORM

    q = (ctx.deps.db.query(FullDiagnosisReportORM)
         .filter(FullDiagnosisReportORM.user_id == ctx.deps.user_id,
                 FullDiagnosisReportORM.deleted_at.is_(None)))
    if report_id is not None:
        q = q.filter(FullDiagnosisReportORM.id == int(report_id))
    if brand:
        q = q.filter(FullDiagnosisReportORM.brand_name == brand)
    return q.order_by(FullDiagnosisReportORM.id.desc()).first()


async def run_full_diagnosis(ctx: RunContext[AgentDeps],
                             brand: str = "",
                             industry_terms: list[str] | None = None,
                             url: str = "",
                             brand_terms: list[str] | None = None,
                             competitors: list[str] | None = None,
                             confirm_token: str = "") -> dict:
    """【动作·重】 Run the **one-click full diagnosis** (the platform's only audit entry). Async; a full run takes about 8–20 minutes.

    Eight sub-audits: site health check / answer engine optimization / crawler reachability / off-site authority / measured AI visibility /
    brand entity recognition / competitor intelligence / sentiment monitoring, combined into a five-dimension score + nine-chapter report.

    **No need to create a topic first** — the user just gives a brand name; when the account has a topic, omitted params are taken from it.
    **Works without an official site too** — the four site-side items are skipped automatically and the four AI-side items run as usual; don't refuse or push them to build a site.

    When the user says "run an audit / do a health check / test visibility / diagnose for me", call this tool (the old 25-item health check and
    diagnosis plan generation are retired). After starting, use get_full_diagnosis to check progress and results; don't wait in place.

    **Two-step confirmation**: once the industry terms are complete, the first call without confirm_token returns a preview
    (which brand, which industry terms, what quota it uses, how long to wait). Read it to the user and **end this turn**;
    only after their next message explicitly confirms, call again with the same params + confirm_token to actually start.
    This run is a heavy task that uses the account's diagnosis quota; you can't decide for the user.

    brand: brand name (required; defaults to the topic name if omitted);
    industry_terms: 1–5 industry terms (e.g. "elevator retrofits for old buildings"); if omitted, derived from the topic industry/seed terms.
                    **If they can't be derived, this tool doesn't start the diagnosis** and returns a pending card with status=need_input —
                    hand it to the user to pick/fill; **don't guess their line of business yourself, don't list examples of various industries**.
    url: official site, **omit if there is none**; if omitted and the topic has one, the topic's is used;
    brand_terms: brand terms, defaults to the brand name; competitors: competitor names or domains, optional.
    """
    import json
    from datetime import timedelta

    import httpx
    import jwt as _jwt

    usage_guardrail_check(ctx.deps, "run_full_diagnosis")
    # Audits don't depend on topics: use one as fallback if present, run anyway if not (a user giving a brand name should be able to test)
    topic = _account_topic(ctx)
    brand = (brand or "").strip() or (getattr(topic, "name", "") or "").strip()
    if not brand:
        raise ModelRetry("缺品牌名。先问用户「要检测哪个品牌?」拿到名称,"
                         "再带 brand 调用本工具(不需要先建主题)。")

    terms = [t.strip() for t in (industry_terms or []) if t and t.strip()][:5]
    # Terms derived from the topic are **only candidates**, never used directly to start a run.
    #
    # This used to be `terms = suggestions` — accounts with a topic were never asked for industry terms at all,
    # and _fd_industry_terms' second fallback is **seed terms**, which are full questions
    # (that's how report #99's 「<brand-d>羊肉哪家好」 ("which <brand-d> lamb is best") came about). So the "the model must not guess" rule
    # only took effect when "the account has no topic at all", missing exactly the most common kind of account.
    suggestions: list[str] = _fd_industry_terms(topic) if topic is not None else []

    # Badly shaped terms (whole questions / too long) are sent back for the user to confirm. The model drafting terms + the user copying them
    # is the real path of #99, and the model even warned itself that something was off and ran it anyway.
    bad = _fd_bad_terms(terms)

    if not terms or bad:
        # **Don't start**; return a pending card for the user to pick/fill themselves.
        #
        # Previously a missing term raised ModelRetry, so the model could only write a paragraph to ask — not knowing what the user does,
        # it made up guess lists like "if you're in footwear/apparel -> canvas shoes... if you're in food service -> snacks...",
        # and the user was never really asked yet had to pick from a pile of guesses (the 2026-08-27 <brand-a> session).
        #
        # Industry terms decide what questions the whole run asks the AI; a wrong guess makes the whole report wrong, so this step must be the user's call.
        # When the account has no topic, **having no derivable candidates** is normal — then show only the input box, don't fake candidates.
        card = {
            "status": "need_input",
            "field": "industry_terms",
            "brand": brand,
            "suggestions": suggestions,
            "question": "你们这行的客户,会用什么词去问 AI?",
            "example": "比如做加装电梯的就填「老楼加装电梯」,做商用清洁的填「商用扫地机器人」。",
            "_hint": (
                "诊断**还没有发起**。把这张卡原样交给用户,让他选或填 1–3 个行业词。"
                "**不要自己猜用户是做什么的、不要举例列举各行各业**——你不知道答案,"
                "猜错会让整单报告都问错问题。拿到用户回复后,带 industry_terms 再调一次本工具。"
            ),
        }
        if bad:
            # Still list the original terms as candidates: if the user insists on using them, they just click again — this is a warning, not a ban.
            card["rejected"] = bad
            card["question"] = "这几个更像是一句提问,不像品类词,确认一下?"
            card["example"] = (f"「{bad[0]}」这种整句会让八项子检测都拿它去问 AI。"
                               "行业词要的是品类,比如「牛羊肉」「火锅食材」。")
            card["suggestions"] = list(dict.fromkeys([*suggestions, *bad]))[:5]
            card["_hint"] = (
                "诊断**还没有发起**。用户给的行业词更像整句提问而不是品类词,"
                "把这张卡交给他确认或改。**不要自己替他改写、也不要自己拟新词**;"
                "他确认后原样带 industry_terms 再调一次本工具即可。"
            )
        return card

    # "Is a run already in progress?" is **judged per brand**. Otherwise another brand's generating run
    # would swallow this request entirely and return that brand's progress — users feel "it didn't run".
    running = _fd_latest(ctx, brand=brand)
    if running is not None and running.status == "generating":
        return {"report_id": running.id, "status": "generating", "brand": brand,
                "hint": "这个品牌已有一单诊断在跑,用 get_full_diagnosis 看进度,不要重复发起。"}

    # ★ Confirmation comes **after** the "missing industry term pending card" and "a run already in progress": neither should first ask
    #   "confirm execution?" — one hasn't gathered its params yet, the other won't execute at all.
    # ★ The official site comes from profile.website. This used to use topic.target — which is the brand short name,
    #   so the four site-side items ran against a string like "威吉力" (the Vigilath brand name), all four were garbage and a report was still produced.
    from geo.services.topic_site import topic_website as _topic_site

    site_preview = (url or "").strip() or _topic_site(topic)
    fd_args = {"brand": brand, "industry_terms": terms, "url": url,
               "brand_terms": brand_terms, "competitors": competitors}
    est = _fd_cost_estimate(ctx)

    def _fd_preview() -> dict:
        from agents.geo import confirm as _c

        return _c.needs_confirm(
            ctx.deps, tool="run_full_diagnosis", action_label="跑一次一键全面诊断",
            args=fd_args,
            preview={"品牌": brand, "行业词": terms,
                     "官网": site_preview or "(没有,站点侧四项自动跳过)",
                     "跑什么": "八项子检测 → 五维评分 → 十一章报告,可导 PDF",
                     "耗时": "整单 8–20 分钟"},
            impact=["会真的拿这些行业词去问 AI 引擎",
                    "占用账号的诊断名额(每个账号终身赠 1 次,之后按订阅档给月配额)",
                    "**同时按问答数扣积分**(一次问答 = 一个提示词 × 一个引擎),扣费流水记作「一键全面诊断」"],
            irreversible=["发出去的引擎调用撤不回来;名额用掉了不退"],
            # This used to say "only uses quota, not charged in credits" — wrong. The generator really charges by batch_cell
            # (services/full_diagnosis_generator._charge_full_diagnosis).
            # So users saw a charge in their ledger matching no batch run record, while the card told them it was free.
            cost=est["cost"],
            note="行业词决定整单要问 AI 什么问题,**必须让用户确认这几个词是对的**再开跑 —— "
                 "词错了整份报告都是错的。积分按**实际规划出的词数**结算,"
                 "预览里这个数是上限估算,真实扣费通常更少。",
        )

    if not confirm_token:
        return _fd_preview()
    from agents.geo import confirm as _confirm

    stop = _confirm.guard(ctx.deps, confirm_token, tool="run_full_diagnosis",
                          args=fd_args, rebuild_preview=_fd_preview)
    if stop is not None:
        return stop

    from datetime import datetime as _dt

    from geo.api.auth import ALGORITHM, SECRET_KEY
    from geo.models.user import UserORM

    user = ctx.deps.db.get(UserORM, ctx.deps.user_id)
    if user is None or not user.email:
        raise ModelRetry("当前账号没有可用的登录邮箱,无法发起诊断。")
    token = _jwt.encode({"sub": user.email, "exp": _dt.utcnow() + timedelta(minutes=30)},
                        SECRET_KEY, algorithm=ALGORITHM)
    site = site_preview
    payload = {
        "url": site,                                   # Empty = no official site; the four site-side items are skipped automatically
        "brand_name": brand,
        "industry_terms": terms,
        "brand_terms": [t.strip() for t in (brand_terms or []) if t and t.strip()][:5] or [brand],
        "competitor_domains": [c.strip() for c in (competitors or []) if c and c.strip()][:5],
    }
    try:
        # trust_env=False: this is a local private direct connection; the environment's HTTP_PROXY (egress proxy for overseas engines)
        # must not apply to 127.0.0.1, otherwise the whole start chain gets swallowed by the proxy
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            r = await client.post(f"{GEO_BACKEND_URL}/api/check/full/generate", json=payload,
                                  headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        # The main path is unreachable — no longer just reply failed. The user wants an answer now, and
        # between "can't run" and "ran and produced a fabricated one" there's a third way: public search evidence gathering,
        # producing something **clearly labeled as degraded with every conclusion sourced**.
        return await _diagnosis_fallback(ctx, brand, terms, site, reason=str(e))
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if r.status_code == 402:
        # A 402 has **two completely different sources**, previously all mapped to "this paid capability isn't enabled":
        #   · membership tier insufficient (details carry required_tier) -> truly not enabled;
        #   · insufficient credits -> the account is enabled just fine, only the balance is short.
        # Merging them into one sentence meant: the user's first run succeeded, the second hit an empty balance, and they were told
        # "you haven't enabled this feature", with no idea that topping up would help (the "it works again today" feedback).
        detail = body.get("details") if isinstance(body.get("details"), dict) else {}
        msg = str(body.get("message") or r.text[:200])
        if detail.get("required_tier"):
            return {**_sales_gate("一键全面诊断"),
                    "reason": "membership_tier",
                    "当前档位": detail.get("current_tier"),
                    "需要档位": detail.get("required_tier")}
        return {
            "blocked": True, "modified": False, "reason": "insufficient_points",
            "status": "failed",
            "message": msg or "积分不足,这一单没有发起。",
            "当前积分": (f"{est['balance_cents']} 积分"
                        if est.get("balance_cents") is not None else None),
            "note": "**账号是开通的,只是积分不够** —— 别说成「没有开通这个功能」。"
                    "把差多少说清楚,并告诉用户可以用 get_topup_options 充值。"
                    "诊断**没有发起**,名额也没被占。",
        }
    if r.status_code >= 400:
        msg = body.get("message") or r.text[:200]
        if r.status_code >= 500:
            return await _diagnosis_fallback(ctx, brand, terms, site,
                                             reason=f"检测服务返回 {r.status_code}")
        return {"status": "failed", "error": f"发起诊断失败({r.status_code}):{msg}"}
    return {
        "report_id": body.get("report_id"), "status": body.get("status", "generating"),
        "brand": brand, "industry_terms": terms,
        "官网": site or "(没有,站点侧四项自动跳过)",
        "site_checks": "会跑" if site else "跳过(没有官网)",
        # The user explicitly asked: after the run they want to know how much is left. This gives the balance **at start time** and this run's cap.
        "预计扣费": est["cost"].get("合计上限"),
        "当前积分": est["cost"].get("当前积分"),
        "hint": "诊断已开始,整单约 8–20 分钟。告诉用户稍后回来看,"
                "或用 get_full_diagnosis 查进度;不要在本轮反复轮询。"
                "顺带把「预计扣费 / 当前积分」念给他 —— 这是按问答数实扣的重任务,"
                "扣费流水里记作「一键全面诊断」。",
    }


async def _diagnosis_fallback(ctx: RunContext[AgentDeps], brand: str,
                              terms: list[str], site: str, *, reason: str) -> dict:
    """Degraded diagnosis when the audit service is down: public search evidence gathering, giving a current-state picture **without any batch-run metrics**.

    Four sections, of which the third is the truly valuable one:

      1. Public information profile — what this company looks like per search results
      2. Public reputation evidence — with links and dates, drillable
      3. **Negative gaps** — "you can't be found in public search results for 'which XXX is best'".
         This conclusion **holds without measuring engines**, and it's exactly the sentence users most need to see.
      4. Items pending measured review — explicitly listing what couldn't be measured this time

    **Not written to full_diagnosis_reports**: once a degraded result enters the report list, it occupies the 24h reuse cache
    and appears in history as if it were a real diagnosis. It lives only in this conversation turn.
    """
    from agents.geo import degrade, websearch

    queries = [brand] + [f"{t}哪家好" for t in terms[:2]] + [f"{brand} 怎么样", f"{brand} 投诉"]
    out = await asyncio.to_thread(websearch.search_many, queries)
    deg = degrade.Degraded(
        missing=["AI 可见性实测", "竞品对位", "站内技术检测" if site else "站内技术检测(无官网)"],
        reason=f"检测服务暂时不可用({reason})",
        evidence_provider="tencent" if out.get("available") else "",
    )
    if not out.get("available"):
        return degrade.wrap({
            "status": "unavailable",
            "brand": brand,
            "note": (f"检测服务暂时不可用,公网检索也没能用上({out.get('reason')})。"
                     "如实告诉用户这次测不了、稍后再试,**不要**因此说「没查到负面」"
                     "或者给任何分数 —— 那是两回事。"),
        }, degrade.Degraded(level=degrade.UNAVAILABLE, missing=deg.missing, reason=deg.reason))

    ev = out["evidence"]
    brand_hits = [i for i, e in enumerate(ev) if brand in (e["title"] + e["snippet"])]
    gap_terms = [t for t in terms[:2]
                 if not any(brand in (e["title"] + e["snippet"])
                            for e in ev if e.get("query", "").startswith(t))]
    return degrade.wrap({
        "status": "degraded",
        "brand": brand,
        "行业词": terms,
        "公开信息证据": [{
            "序号": i, "标题": e["title"], "链接": e["url"],
            "来源": e["source_domain"], "时间": e["published_at"] or "未标注",
        } for i, e in enumerate(ev[:12])],
        "缺口": ([f"在「{t}哪家好」的公开检索结果里检索不到「{brand}」" for t in gap_terms]
                 or ["公开检索里能看到品牌,但是否被 AI 引擎引用这次没测成"]),
        "conclusions": [
            {"text": f"公开检索里能找到 {len(brand_hits)} 条与「{brand}」直接相关的结果",
             "evidence_ref": brand_hits[0] if brand_hits else None},
        ],
        "待实测": ["AI 引擎实际会不会提到你们", "竞品在同样的问题下被提了几次",
                   "官网的 AI 可读性"],
        "note": "这是**降级结果**:只基于公网检索,不含 AI 引擎实测。"
                "检测服务恢复后用 run_full_diagnosis 再跑一次真诊断。"
                "转述时必须说清这一点,不要给任何分数或比率。",
    }, deg, conclusions_key="conclusions")


async def get_full_diagnosis(ctx: RunContext[AgentDeps], report_id: int | None = None) -> dict:
    """Read the **one-click full diagnosis** progress or result summary (read-only). Without report_id, take the most recent run.

    When generating, returns the status of each of the eight sub-audits; when ready, returns the five-dimension score + executive summary +
    key findings + quick-win actions. The full nine-chapter report is on the page /diagnosis/{report_id}.
    """
    import json

    row = _fd_latest(ctx, report_id)
    if row is None:
        return {"status": "none", "hint": "还没有诊断报告,用 run_full_diagnosis 发起一单。"}

    def _loads(raw, default):
        try:
            v = json.loads(raw or "")
            return v if isinstance(v, type(default)) else default
        except Exception:  # noqa: BLE001
            return default

    out: dict = {"report_id": row.id, "status": row.status,
                 "brand": row.brand_name or "", "url": row.url or "",
                 "page": f"/diagnosis/{row.id}",
                 # ★ Absolute URL, not an in-site path. When the user asks "where's the full report", a `/diagnosis/12`
                 #   can't be clicked or forwarded — the earlier feedback was "I still had to type the URL into the browser myself".
                 "report_url": f"{_site_origin()}/diagnosis/{row.id}"}
    prog = _loads(row.progress_json, {})
    steps = [s for s in (prog.get("steps") or []) if isinstance(s, dict)]
    out["steps"] = [{"检测项": _FD_STEP_ZH.get(s.get("mode"), s.get("mode")),
                     "状态": s.get("status"),
                     **({"说明": s.get("error")} if s.get("error") else {})} for s in steps]
    out["done"] = prog.get("done")
    out["total"] = prog.get("total")
    if row.status == "generating":
        out["hint"] = "还在跑,整单约 8–20 分钟。把当前进度告诉用户即可,不要反复调本工具。"
        return out
    if row.status == "failed":
        out["error"] = row.error
        return out

    scores = _loads(row.scores_json, {})
    narr = _loads(row.narrative_json, {})
    out["total_score"] = scores.get("total")
    out["grade"] = scores.get("grade")
    out["dimensions"] = [
        {"维度": _FD_DIM_ZH.get(d.get("key"), d.get("key")),
         "得分": d.get("score"), "权重": d.get("weight"),
         **({"口径": "估算值"} if d.get("estimated") else {})}
        for d in (scores.get("dimensions") or []) if isinstance(d, dict)
    ]
    missing = [_FD_DIM_ZH.get(k, k) for k in (scores.get("missing_dimensions") or [])]
    if missing:
        out["未采集维度"] = missing
        out["未采集说明"] = ("本次没有官网,站点侧四项跳过,基建完善度无分,权重已分摊给其余维度"
                             if not (row.url or "").strip() else "该维度本次未取得数据,权重已分摊")
    out["executive_summary"] = narr.get("executive_summary") or ""
    out["key_findings"] = [{"标题": f.get("title"), "严重度": f.get("severity"),
                            "说明": f.get("detail")}
                           for f in (narr.get("key_findings") or [])[:5] if isinstance(f, dict)]
    out["quick_wins"] = [{"动作": q.get("action"), "预期": q.get("expected_impact")}
                         for q in (narr.get("quick_wins") or [])[:5] if isinstance(q, dict)]
    out["hint"] = ("这是摘要;逐章明细(六项指标、信源结构、竞品、舆情)在完整报告里。"
                   "**把 report_url 这个完整链接给用户**(Markdown 链接形式),"
                   "并说明要用同一个账号登录才打得开;需要转发给别人看的版本,"
                   "在报告页导出 PDF。别让他自己去浏览器里拼地址。")
    return out


async def trigger_diagnosis(ctx: RunContext[AgentDeps]) -> dict:
    """【已下线,勿调】 The old topic diagnosis plan generation. Audits all go through run_full_diagnosis (one-click full diagnosis).

    The function body is kept only for compatibility with old references; removed from the external toolset.
    """
    from datetime import datetime

    from geo.models.ai_telemetry import AiTelemetryTopicSolutionORM
    from geo.services.solution_generator import schedule_solution_generation

    usage_guardrail_check(ctx.deps, "trigger_diagnosis")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db = ctx.deps.db
    url = (topic.target or "").strip()
    sol = (
        db.query(AiTelemetryTopicSolutionORM)
        .filter(AiTelemetryTopicSolutionORM.topic_id == topic.id)
        .first()
    )
    if sol is not None and sol.status == "generating":
        return {"status": "generating", "hint": "诊断已在生成中,稍后用 get_diagnosis_report 查看。"}
    if sol is None:
        sol = AiTelemetryTopicSolutionORM(topic_id=topic.id)
        db.add(sol)
    sol.status = "generating"
    sol.website_url = url
    sol.error = None
    sol.generated_by_admin_id = ctx.deps.account_id
    sol.updated_at = datetime.utcnow()
    db.commit()
    schedule_solution_generation(topic_id=topic.id, website_url=url, admin_id=ctx.deps.account_id)
    return {"status": "generating", "hint": "诊断生成中(约 30–90s),稍后用 get_diagnosis_report 查看根因/叙述。"}


def _explicit_topic(ctx: RunContext[AgentDeps], topic_id: int | None, tool: str):
    """Topic resolution for money-spending actions: **no guessing**.

    `_account_topic`'s soft fallback (taking the "most recently batch-run" topic) is reasonable for read-only tools,
    but not for money-spending writes — when the account has two brands, "write three articles for me" would quietly land on the other brand,
    and users never check the topic name on the preview card word by word. The feedback "drafts were generated but for
    the wrong topic" came from exactly this.

    Rule: explicit topic_id > the topic already switched to this turn (deps.topic_id) > the only topic when the account has just one
    > otherwise always return a selection card for the user to choose.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    db, acc = ctx.deps.db, ctx.deps.account_id
    if topic_id is not None:
        t = db.get(AiTelemetryTopicORM, int(topic_id))
        if t is None or t.user_id != acc:
            raise ModelRetry("指定主题不存在或不属于当前账号。先用 list_topics 查清 id。")
        return t, None
    if ctx.deps.topic_id:
        t = db.get(AiTelemetryTopicORM, ctx.deps.topic_id)
        if t is not None and t.user_id == acc:
            return t, None
    rows = (db.query(AiTelemetryTopicORM)
            .filter(AiTelemetryTopicORM.user_id == acc)
            .order_by(AiTelemetryTopicORM.last_run_at.desc().nullslast(),
                      AiTelemetryTopicORM.id.desc()).all())
    if len(rows) == 1:
        return rows[0], None
    if not rows:
        raise ModelRetry("当前账号还没有主题,先问用户要不要建一个(create_topic),不要自己建。")
    return None, {
        "status": "need_input", "field": "topic_id", "tool": tool,
        "question": "这个账号下有多个品牌主题,这次要给哪一个做?",
        "候选": [{"topic_id": r.id, "名称": r.name} for r in rows],
        "_hint": ("**还没有执行任何东西**。这是花钱的动作,主题猜错就是把稿子写到"
                  "另一个品牌头上。把候选交给用户选,拿到答案后带 topic_id 再调一次。"
                  "**不要**自己挑一个继续。"),
    }


async def draft_articles(ctx: RunContext[AgentDeps], max_docs: int = 3,
                         queries: list[str] | None = None,
                         topic_id: int | None = None,
                         confirm_token: str = "") -> dict:
    """⚠️ 【写操作·花钱】 **Generate brand-new article drafts** from the selected queries (async; really produces articles and really charges credits).

    **Only call when the user explicitly expresses intent like "generate / write / draft / write articles for me / create".**
    When the user asks **view-type** questions like "which articles went out today", "what was published", "show me the articles", "article progress",
    **never call this tool**; use the read-only get_publish_status instead. If unsure whether they "want to generate", don't call yet; ask the user first.

    **Two-step confirmation**: first call without confirm_token to get a preview, read "how many articles / how many credits / remaining balance"
    truthfully to the user and **end this turn**; after their next message explicitly confirms, call again with the same params + confirm_token.

    max_docs: number of articles to generate this time (1–20); queries: explicit query list (defaults to the topic's selected queries);
    topic_id: which topic to draft for. When the account has multiple topics and none is passed, this tool **doesn't guess**;
              it returns a selection card with status=need_input for the user to choose.
    """
    import json

    from agents.geo import confirm
    from geo.services.content_generator import schedule_generation

    _admin_scope_only(ctx, "draft_articles")
    usage_guardrail_check(ctx.deps, "draft_articles")
    topic, ask = _explicit_topic(ctx, topic_id, "draft_articles")
    if ask is not None:
        return ask
    qs = [q.strip() for q in (queries or []) if q and q.strip()] or None
    if qs:                                     # Explicitly passed terms must also exclude soft-deleted ones
        from geo.models.ai_telemetry import deleted_query_texts
        _gone = deleted_query_texts(topic)
        qs = [q for q in qs if q not in _gone] or None
    if qs is None and not json.loads(topic.queries_json or "[]"):
        raise ModelRetry("当前主题还没有 query,请先 expand_prompts + set_selected_queries,或显式传 queries。")
    try:
        md = max(1, min(int(max_docs or 3), 20))
    except (TypeError, ValueError):
        md = 3

    # Drafting is the most expensive action in conversation besides paid media publishing (priced per article, up to 20 at once).
    # It goes through the same guardrails as paid orders, only the capability tier is wallet_spend — that cap is granted only to web;
    # neither the ops console nor external channels should spend the customer's money on drafting.
    from agents.geo import spend
    from geo.models.balance import PRICE_CONTENT_DOC
    from geo.services import balance_service

    price = balance_service.get_price(ctx.deps.db, PRICE_CONTENT_DOC)
    plan = spend.SpendPlan(tool="draft_articles", amount_cents=price * md,
                           ref_id=f"{topic.id}:{md}", cap="wallet_spend")
    blocked = spend.precheck(ctx.deps, plan)
    if blocked is not None:
        return blocked

    # ★ Two-step confirmation comes **after** precheck: confirming first and then discovering "not enough credits" wastes the user's nod.
    #   The args fingerprint uses the normalized md / qs, not the raw input — "preview 3 articles, confirm 20" must be invalidated,
    #   while max_docs=3 and max_docs="3" shouldn't count as two different requests.
    from agents.geo.tools_billing import quote

    # The topic is part of the fingerprint: "preview topic A, switch to topic B when confirming" must invalidate the ticket.
    args = {"max_docs": md, "queries": qs, "topic_id": topic.id}

    def _preview() -> dict:
        q = quote(ctx.deps.db, ctx.deps.user_id, "article", md)
        return confirm.needs_confirm(
            ctx.deps, tool="draft_articles", action_label=f"生成 {md} 篇文章草稿",
            args=args, topic=topic,
            preview={"主题": topic.name, "篇数": md,
                     "针对的问题": (qs[:5] if qs else "主题里已选的提示词"),
                     "耗时": "每篇约 30–90 秒,异步跑"},
            impact=["会真的产出文章草稿并按篇扣积分(按写成的篇数收)"],
            irreversible=[],
            cost={k: v for k, v in q.items() if not k.startswith("_")},
            note="草稿产出后还要审核才会发布,产稿本身不对外发布。",
        )

    if not confirm_token:
        return _preview()
    stop = confirm.guard(ctx.deps, confirm_token, tool="draft_articles",
                         args=args, rebuild_preview=_preview)
    if stop is not None:
        return stop

    if spend.dry_run():
        return {"status": "dry_run", "max_docs": md,
                "hint": "干跑模式:没有真的产稿、没有扣积分。"}

    # The actual charge happens in content_generator's background thread (per article, idempotent per batch).
    # marked() still works: schedule_generation uses llm_usage.spawn_thread,
    # which carries a snapshot of the current contextvars into the new thread (a bare threading.Thread doesn't) —
    # the snapshot is taken at spawn time, so schedule_generation itself must be wrapped inside.
    with spend.marked():
        schedule_generation(topic_id=topic.id, max_docs=md, queries_override=qs,
                            trigger_source="agent")
    return {"status": "generating", "max_docs": md,
            "topic_id": topic.id, "主题": topic.name,
            "预计扣": f"{price * md} 积分(每篇 {price},实际按写成的篇数收)",
            "当前积分": f"{int(balance_service.get_balance(ctx.deps.db, ctx.deps.user_id))} 积分",
            "hint": "文章生成中(每篇约 30–90s),稍后用 get_publish_status 看进度。"
                    "**把主题名念出来**确认写的是对的那个品牌;扣费按实际写成的篇数,"
                    "生成失败的那几篇不收钱。"}


async def retry_failed_articles(ctx: RunContext[AgentDeps], topic_id: int | None = None,
                                confirm_token: str = "") -> dict:
    """【动作·花钱】 Put **failed drafts** back into the generation queue so they're written again.

    Use this tool when the user says "a few articles failed to generate / regenerate them / try again".

    **Why it's needed**: failed drafts are retried automatically a few times by failure cause, but those judged "retrying won't help"
    **leave the queue in a terminal state** and sit in the list with a "[生成失败]" ("[generation failed]") title from then on.
    Before this, the customer side had no way to retry at all — they had to ask ops.

    After requeueing, the background retry task regenerates them one by one, charged per article (only when successfully written).
    First call without confirm_token to get a preview (how many, roughly how much), then call again with the ticket after the user agrees.
    """
    from agents.geo import confirm, spend
    from agents.geo.tools_billing import quote
    from geo.models.balance import PRICE_CONTENT_DOC
    from geo.services import balance_service, gen_retry

    _admin_scope_only(ctx, "retry_failed_articles")
    usage_guardrail_check(ctx.deps, "retry_failed_articles")
    topic, ask = _explicit_topic(ctx, topic_id, "retry_failed_articles")
    if ask is not None:
        return ask

    # Count with dry_run first: the preview must report the real number of articles, not "at most how many"
    peek = await asyncio.to_thread(gen_retry.requeue, topic_id=topic.id, dry_run=True)
    n = int(peek.get("matched") or 0)
    if n <= 0:
        return {"ok": False, "modified": False, "topic_id": topic.id, "主题": topic.name,
                "note": "这个主题没有卡住的失败稿(要么没失败过,要么还在自动重试队列里)。"
                        "如实告诉用户不用重排;还在自动重试的会自己再跑,不必手动干预。"}

    args = {"topic_id": topic.id}
    price = balance_service.get_price(ctx.deps.db, PRICE_CONTENT_DOC)

    def _preview() -> dict:
        return confirm.needs_confirm(
            ctx.deps, tool="retry_failed_articles", action_label=f"重排 {n} 篇失败稿重新生成",
            args=args, topic=topic,
            preview={"主题": topic.name, "篇数": n,
                     "错因分布": peek.get("by_bucket") or {},
                     "耗时": "后台逐篇重生,每篇约 30–90 秒"},
            impact=["会重新调用模型写这几篇,**写成了才按篇扣积分**",
                    "上一次的失败内容会被覆盖"],
            irreversible=[],
            cost={k: v for k, v in quote(ctx.deps.db, ctx.deps.user_id,
                                         "article", n).items()
                  if not k.startswith("_")},
            note="之前失败的错因摆在「错因分布」里 —— 如果是同一个原因反复失败,"
                 "重排大概率还是失败,先如实告诉用户这一点再问要不要试。",
        )

    if not confirm_token:
        return _preview()
    stop = confirm.guard(ctx.deps, confirm_token, tool="retry_failed_articles",
                         args=args, rebuild_preview=_preview)
    if stop is not None:
        return stop

    plan = spend.SpendPlan(tool="retry_failed_articles", amount_cents=price * n,
                           ref_id=f"topic:{topic.id}", cap="wallet_spend")
    blocked = spend.precheck(ctx.deps, plan)
    if blocked is not None:
        return blocked
    if spend.dry_run():
        return {"status": "dry_run", "篇数": n, "hint": "干跑模式:没有真的重排。"}

    out = await asyncio.to_thread(gen_retry.requeue, topic_id=topic.id, dry_run=False)
    return spend.remember(plan, {
        "ok": True, "已重排": int(out.get("requeued") or 0),
        "topic_id": topic.id, "主题": topic.name,
        "note": "已经排进重试队列,后台逐篇重生(不是立刻)。"
                "稍后用 list_articles 看结果,**不要在本轮反复查**。"
                "按写成的篇数收费,再次失败的不收。",
    })


async def get_diagnosis_report(ctx: RunContext[AgentDeps]) -> dict:
    """Read the current topic's **GEO diagnosis report** (root-cause diagnosis + narrative). Read-only. Gives a hint when absent / not ready.

    ⚠️ This is **not** the report center's daily / weekly / monthly report.
    [When to use] Questions about "diagnosis / root cause / why not exposed / optimization suggestions" -> use me;
    questions about daily/weekly/monthly reports, metric numbers, how recent data looks -> get_period_report / get_report_overview.
    """
    import json
    from geo.models.ai_telemetry import AiTelemetryTopicSolutionORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    sol = (
        ctx.deps.db.query(AiTelemetryTopicSolutionORM)
        .filter(AiTelemetryTopicSolutionORM.topic_id == topic.id)
        .order_by(AiTelemetryTopicSolutionORM.id.desc())
        .first()
    )
    if sol is None or sol.status != "ready":
        return {"status": (sol.status if sol else "none"), "report": None, "hint": "诊断报告尚未生成或生成中"}
    return {
        "status": sol.status,
        "diagnosis": json.loads(sol.diagnosis_json or "{}"),
        "narrative": json.loads(sol.narrative_json or "{}"),
    }


async def get_run_status(ctx: RunContext[AgentDeps]) -> dict:
    """Read the **execution status** of the latest batch run (finished or not, how many items ran, which platform errored). Read-only.

    ⚠️ This tool **provides no metric numbers** (hit rate / exposure rate etc.) — batch run status and report-center metrics are different things.
    [When to use] "Is it finished / did today's batch run happen / any errors / how many items collected" -> use me;
    metric numbers, how recent data looks, what's the exposure rate -> get_report_overview.
    """
    from geo.models.ai_telemetry import AiTelemetryResponseORM, AiTelemetryRunORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    run = (
        ctx.deps.db.query(AiTelemetryRunORM)
        .filter(AiTelemetryRunORM.topic_id == topic.id)
        .order_by(AiTelemetryRunORM.id.desc())
        .first()
    )
    if run is None:
        return {"status": "none", "hint": "还没有跑批结果"}
    resps = ctx.deps.db.query(AiTelemetryResponseORM).filter(AiTelemetryResponseORM.run_id == run.id).all()
    by_engine: dict[str, dict] = {}
    for r in resps:
        e = by_engine.setdefault(r.engine, {"done": 0, "error": 0})
        e["done"] += 1
        if r.error:
            e["error"] += 1
    valid = sum(1 for r in resps if not r.error)      # Same definition as the report center's valid_count (errors excluded)
    return {
        "run_id": run.id, "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error or None,
        "response_count": len(resps), "valid_count": valid, "error_count": len(resps) - valid,
        "by_engine": by_engine,
        "note": "这是跑批执行情况,不是指标口径;指标数字请用 get_report_overview。",
    }


async def get_publish_status(ctx: RunContext[AgentDeps]) -> dict:
    """Read the current topic's article publishing progress + published article list (including today's). **Strictly read-only**; generates nothing.
    [When to use] "what was published · publishing progress · published list" -> use me; to see drafts/bodies/list by status -> list_articles.

    Use this tool for **view-type** questions like "which articles went out today / what was published / show the articles"; never call draft_articles.
    """
    from datetime import datetime

    from sqlalchemy import func

    from geo.models.ai_telemetry import TopicGeneratedDocORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db = ctx.deps.db
    rows = (
        db.query(TopicGeneratedDocORM.status, func.count())
        .filter(TopicGeneratedDocORM.topic_id == topic.id)
        .group_by(TopicGeneratedDocORM.status)
        .all()
    )
    agg = {s: int(c) for s, c in rows}

    # Published article list (title + publish time), plus a count of "what went out today"
    pub = (
        db.query(TopicGeneratedDocORM)
        .filter(TopicGeneratedDocORM.topic_id == topic.id, TopicGeneratedDocORM.status == "published")
        .order_by(TopicGeneratedDocORM.mediumsly_pushed_at.desc().nullslast(), TopicGeneratedDocORM.id.desc())
        .limit(20)
        .all()
    )
    today = datetime.utcnow().date()
    recent, today_list = [], []
    for d in pub:
        ts = d.mediumsly_pushed_at or d.created_at
        item = {
            "id": d.id,
            "title": d.title or (d.source_query_text or "")[:40] or f"doc#{d.id}",
            "url": getattr(d, "mediumsly_url", None),
            "published_at": ts.isoformat() if ts else None,
        }
        recent.append(item)
        if ts and ts.date() == today:
            today_list.append(item)
    return {
        "topic_id": topic.id, "by_status": agg,
        "published": agg.get("published", 0), "total": sum(agg.values()),
        "published_today_count": len(today_list),
        "published_today": today_list,          # Articles published today
        "recent_published": recent[:10],         # Recently published (up to 10)
    }


# ── Report-center metric definitions (2026-07 redesign) ─────────────────────────
# All data fetching goes through agents.geo.report_source, which shares helpers with the report-center page and is identical by construction.
# ★ Returned percentages are already 0-100 and rounded to 1 decimal: **don't** multiply by 100 again or reformat.
_PERIOD_CN = {"daily": "日报", "weekly": "周报", "monthly": "月报"}


def _period_or_retry(period_type: str) -> str:
    pt = (period_type or "daily").strip().lower()
    if pt not in _PERIOD_CN:
        raise ModelRetry("period_type 只能是 daily(日报)/ weekly(周报)/ monthly(月报) 之一。")
    return pt


async def get_report_overview(ctx: RunContext[AgentDeps]) -> dict:
    """Read the **report center**'s latest data: the six metrics of the latest daily / weekly / monthly period
    (overall exposure rate, TOP1 exposure rate, TOP3 exposure rate, content positive/neutral rate, article citation rate, brand voice score), with period-over-period and target status. Read-only.

    [When to use] "how's the data / recent performance / how effective / today's effect / did we hit targets / what's the exposure rate / what's the voice score" -> use me;
    detailed report or AI summary body for a specific period -> get_period_report; multi-period trends -> list_report_periods;
    "how many questions were hit / coverage / which weren't found" -> get_query_coverage (that's a question-level metric definition, not these metrics).

    [Metric definition] Report data is based on **closed** periods (the latest daily period is yesterday; today is still running).
    Today's data only appears in the today_partial field and **must be labeled "still running, not closed, for reference only"**, and can't be used to judge targets.
    Target status **only looks at the weekly report** (weekly.targets).
    """
    from agents.geo import report_source as RS

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db = ctx.deps.db
    prepared = RS.load_stats(db, topic)          # ★ Fetch once, reuse for all three periods (the fallback path is a full table scan)
    stats = prepared[0]

    out: dict = {"topic_name": topic.name, "metric_labels": RS.metric_labels()}
    empty = True
    for pt in ("daily", "weekly", "monthly"):
        keys, series, bounds = RS.period_series(db, topic, pt, prepared=prepared)
        ck = RS.closed_key(keys, pt)
        if ck is None:
            continue
        empty = False
        cur = series[ck]
        if pt == "daily":                        # Daily positive/neutral rate uses a rolling 7-day window + baseline-date anchoring
            idx = keys.index(ck)
            if stats is not None:
                cur = {**cur, "accuracy": RS.daily_accuracy_7d(stats, keys, idx)}
            cur = RS.pin_baseline(cur, topic.id, idx)
        pk = RS.prev_key(keys, ck)
        block: dict = {
            "key": ck, "period": list(bounds[ck]),
            "metrics": RS.slim(cur), "responses": RS.responses_of(series[ck]),
        }
        d = RS.delta_pp(RS.slim(cur), RS.slim(series[pk]) if pk else None)
        if d:
            block["delta_pp"] = d
        if pt == "weekly":                       # Targets judged by the weekly metric definition only
            t = RS.targets_for(topic, keys, series, ck)
            if t:
                block["targets"] = t
        out[pt] = block
        if pt == "daily" and keys and keys[-1] > ck:      # Live tail: today (not closed)
            out["today_partial"] = {
                "key": keys[-1], "metrics": RS.slim(series[keys[-1]]),
                "responses": RS.responses_of(series[keys[-1]]),
                "note": "今天还在跑,数据未收口,仅供参考,不能用来判达标",
            }
    if empty:
        return {"status": "none", "topic_name": topic.name,
                "hint": "该主题还没有已收口的报告期次(可能还没开始跑批),报告中心暂无可读数据。"}
    out["_internal"] = ("指标名必须照抄 metric_labels;数值已是百分比不要再换算;"
                        "引用数字默认用已收口期次;提到 today_partial 必须说明「今天还在跑、未收口」。")
    return out


async def get_period_report(ctx: RunContext[AgentDeps],
                            period_type: str = "daily", key: str | None = None) -> dict:
    """Read the details of **one report-center period**: six metrics + period-over-period + overall exposure rate per platform + that period's AI summary body. Read-only.

    period_type: daily = daily report / weekly = weekly report / monthly = monthly report.
    key: period id — for daily use a date like 2026-07-19, for weekly 2026-W29, for monthly 2026-07;
        **omitted = the latest closed report** (the latest daily period is yesterday; today is still running).

    [When to use] "how's the latest daily/weekly/monthly report / the report for a given day, week or month / what does the report say / what did the summary say" -> use me;
    only the latest numbers overview -> get_report_overview; multi-period trends or listing available periods -> list_report_periods.
    [Note] Monthly metrics are **whole-month totals**; the monthly page's "before / after optimization" comparison uses the weekly metric definition; they differ, don't mix them.
    """
    from agents.geo import report_source as RS

    pt = _period_or_retry(period_type)
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db = ctx.deps.db
    prepared = RS.load_stats(db, topic)
    stats = prepared[0]
    keys, series, bounds = RS.period_series(db, topic, pt, prepared=prepared)
    if not keys:
        return {"status": "none", "hint": f"该主题还没有{_PERIOD_CN[pt]}数据。"}

    k = (key or "").strip() or RS.closed_key(keys, pt) or keys[-1]
    if k not in series:
        raise ModelRetry(f"没有 {k} 这一期{_PERIOD_CN[pt]},可选最近几期:{keys[-5:]}")

    idx = keys.index(k)
    cur = series[k]
    if pt == "daily":
        if stats is not None:
            cur = {**cur, "accuracy": RS.daily_accuracy_7d(stats, keys, idx)}
        cur = RS.pin_baseline(cur, topic.id, idx)
    pk = RS.prev_key(keys, k)
    prev = series[pk] if pk else None
    if prev is not None and pt == "daily":
        if stats is not None:
            prev = {**prev, "accuracy": RS.daily_accuracy_7d(stats, keys, idx - 1)}
        prev = RS.pin_baseline(prev, topic.id, idx - 1)

    out: dict = {
        "period_type": pt, "period_name": _PERIOD_CN[pt], "key": k,
        "period": list(bounds[k]),
        "metrics": RS.slim(cur),
        "responses": RS.responses_of(series[k]),
        "metric_labels": RS.metric_labels(),
    }
    _ck = RS.closed_key(keys, pt)
    out["closed"] = _ck is not None and k <= _ck      # Whether this period is closed
    if pk:
        out["prev_key"] = pk
        d = RS.delta_pp(RS.slim(cur), RS.slim(prev))
        if d:
            out["delta_pp"] = d
    if pt == "weekly":
        t = RS.targets_for(topic, keys, series, k)
        if t:
            out["targets"] = t
    eng = RS.engine_exposure(stats, pt, k)
    if eng:
        out["by_engine_exposure"] = eng
    body = RS.ai_summary(db, topic.id, pt, k)
    if body:
        out["ai_summary"] = body
    elif pt == "monthly":
        out["ai_summary"] = None
        out["summary_note"] = "月报没有 AI 总结正文,请基于上面的指标自己组织结论。"
    if not out["closed"]:
        out["note"] = "这一期还没收口(仍在进行中),数据仅供参考,不能用来判达标。"
    out["_internal"] = ("指标名必须照抄 metric_labels;数值已是百分比不要再换算;"
                        "by_engine_exposure 只是各平台的整体露出率,不是六指标全套。")
    return out


async def list_report_periods(ctx: RunContext[AgentDeps],
                              period_type: str = "daily", limit: int = 7) -> dict:
    """List the six metrics of the **report center**'s recent periods (newest first), for viewing trends. Read-only.

    period_type: daily / weekly / monthly; limit defaults to 7, max 30.
    [When to use] "trend over the last few days/weeks / up or down / which reports exist / which day was best" -> use me;
    detailed content and AI summary body for one period -> get_period_report; only the latest overview -> get_report_overview.
    """
    from agents.geo import report_source as RS

    pt = _period_or_retry(period_type)
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    n = max(1, min(int(limit or 7), 30))
    keys, series, bounds = RS.period_series(ctx.deps.db, topic, pt)
    if not keys:
        return {"status": "none", "hint": f"该主题还没有{_PERIOD_CN[pt]}数据。"}
    ck = RS.closed_key(keys, pt)
    items = []
    for k in sorted(keys, reverse=True)[:n]:
        items.append({
            "key": k, "period": list(bounds[k]), "metrics": RS.slim(series[k]),
            "responses": RS.responses_of(series[k]),
            **({"closed": False} if (ck is None or k > ck) else {}),
        })
    return {
        "period_type": pt, "period_name": _PERIOD_CN[pt], "items": items,
        "metric_labels": RS.metric_labels(),
        "_internal": ("指标名必须照抄 metric_labels;数值已是百分比不要再换算;"
                      "closed=False 的期次仍在进行中,不能用来判达标。"),
    }


async def get_query_coverage(ctx: RunContext[AgentDeps]) -> dict:
    """**Cumulative** status of being found (hit) by AI engines (all-time): total prompts, number of questions hit, number of seed terms hit. Read-only.

    This counts **question (query)-level** hit tracking: whether a given prompt has ever been found by AI.
    ⚠️ This is **not the same metric definition as the report center's overall exposure rate** (exposure rate counts per answer; this dedups by question);
    **never mix the two numbers, convert between them, or explain them in one sentence as the same metric.**
    [When to use] "cumulative/total how many found · how many hit · indexing status · coverage" -> use me;
    metric numbers/exposure rate/how recent data looks -> get_report_overview.
    """
    import json

    from sqlalchemy import distinct, func

    from geo.models.ai_telemetry import AiTelemetryQueryHitORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db = ctx.deps.db
    # Hit queries -> traced back to seed terms via the seed field in queries_json
    text2seed: dict[str, str] = {}
    for q in json.loads(topic.queries_json or "[]"):
        if isinstance(q, dict) and q.get("text"):
            text2seed[q["text"]] = q.get("seed") or ""

    # The full set of queries with total_hits>0 in the hit table
    hit_all = {r[0] for r in db.query(distinct(AiTelemetryQueryHitORM.query)).filter(
        AiTelemetryQueryHitORM.topic_id == topic.id, AiTelemetryQueryHitORM.total_hits > 0).all()}
    # Total monitoring/placement questions = number of currently selected queries (queries_json), consistent with the dashboard.
    # Hits are **restricted to the placement set**, guaranteeing hit + unhit = placed (otherwise hits from old removed queries would throw off the total).
    if text2seed:
        selected = set(text2seed)
        monitored = len(selected)
        hit = len(selected & hit_all)
    else:
        monitored = len(hit_all) or (db.query(func.count(distinct(AiTelemetryQueryHitORM.query))).filter(
            AiTelemetryQueryHitORM.topic_id == topic.id).scalar() or 0)
        hit = len(hit_all)
    seeds_hit = {text2seed[t] for t in hit_all if text2seed.get(t)}      # Reuse hit_all: seeds of the hit queries
    seeds_total = len(_seed_texts(topic.seed_prompts_json))

    return {
        "topic_id": topic.id, "topic_name": topic.name, "scope": "all-time",
        "hit_queries": hit, "monitored_queries": monitored,
        "hit_seeds": len(seeds_hit), "seed_total": seeds_total,
        "note": "累计被 AI 引擎命中的问题 / 种子词(问题级去重口径,与报告中心露出率不同口径)。" if monitored else
                "该主题暂无监测命中记录(可能未跑批或选错主题)。",
    }


def _seed_texts(raw: str | None) -> list[str]:
    """seed_prompts_json items may be a dict ({'text':...}) or a str — normalize to text."""
    import json
    out: list[str] = []
    for s in json.loads(raw or "[]"):
        if isinstance(s, dict) and s.get("text"):
            out.append(s["text"])
        elif isinstance(s, str) and s.strip():
            out.append(s)
    return out


# Features of fetched pages that "aren't body content".
#
# The 2026-08-27 <brand-a> session: the user gave a WeChat OA link, and what came back was an anti-bot verification page —
# 65 characters in total 「环境异常 当前环境异常,完成验证后即可继续访问。去验证 …」 ("Environment abnormal ... complete verification to continue. Verify ..."),
# and it **still went into the knowledge base** (material_id=2). Then ask_knowledge retrieved that verification notice as material,
# and the model could only guess what the brand does from it. Garbage in the DB keeps getting retrieved, which is worse than a failed fetch.
_FETCH_BLOCKED_MARKS = (
    "环境异常", "完成验证后即可", "去验证", "请输入验证码", "人机验证",
    "访问过于频繁", "请开启 JavaScript", "请开启JavaScript",
    "Enable JavaScript", "Just a moment", "Access Denied", "captcha",
)
_FETCH_MIN_CHARS = 200


def _fetch_looks_blocked(body: str) -> str:
    """Whether fetched body text looks like an anti-bot/verification page. Non-empty return = it does; the content is the reason shown to the user."""
    t = (body or "").strip()
    hit = next((k for k in _FETCH_BLOCKED_MARKS if k.lower() in t.lower()), "")
    if hit and len(t) < 2000:
        return f"抓到的是验证/拦截页(命中「{hit}」),不是文章正文"
    if len(t) < _FETCH_MIN_CHARS:
        return f"只抓到 {len(t)} 个字符,不足以作为资料(多半是被反爬挡了)"
    return ""


async def ingest_material(
    ctx: RunContext[AgentDeps], text: str | None = None, url: str | None = None, title: str = "",
) -> dict:
    """【写】 Save user material into the account knowledge base ("knowledge base = the user's own material", **unrelated to sentiment data**). Pass text, or a url (fetched automatically with body extraction).
    Used by ask_knowledge retrieval + later grounding.
    """
    from geo.models.agent import AgentMaterialORM

    _admin_scope_only(ctx, "ingest_material")
    usage_guardrail_check(ctx.deps, "ingest_material")
    topic = _account_topic(ctx)
    body = (text or "").strip()
    source = (url or "").strip()
    if source:
        if not source.startswith(("http://", "https://")):
            raise ModelRetry("url 必须以 http:// 或 https:// 开头。")

        def _fetch() -> str:
            import requests
            from bs4 import BeautifulSoup
            r = requests.get(source, timeout=20, headers={"User-Agent": "Vigilath-Agent/1.0"})
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)

        try:
            body = (await asyncio.to_thread(_fetch))[:50000]
        except Exception as e:  # noqa: BLE001
            raise ModelRetry(f"抓取 url 失败:{str(e)[:200]}")
        # ★ Fetched ≠ fetched correctly. Anti-bot pages are also 200; without a check the verification notice gets stored as material.
        #   Here we **return a structured result rather than raise ModelRetry**: retries=2 would make the model
        #   re-fetch twice unchanged (identical results), burning tokens and still failing.
        why = _fetch_looks_blocked(body)
        if why:
            return {
                "status": "fetch_blocked",
                "url": source,
                "reason": why,
                "preview": body.strip()[:120],
                "_hint": ("这个链接**没有入库**。如实告诉用户抓不到正文、原因是什么,"
                          "请他直接把文章正文粘过来,或换一个能公开访问的链接。"
                          "**绝不要**拿抓回来的这段内容去推断品牌是做什么的 —— 它是验证提示,不是资料。"),
            }
    if not body:
        raise ModelRetry("请提供 text,或可提取正文的 url。")
    m = AgentMaterialORM(
        account_id=ctx.deps.account_id,
        topic_id=(topic.id if topic else None),
        source=source[:512],
        title=(title or source or "资料")[:512],
        text=body,
    )
    ctx.deps.db.add(m)
    ctx.deps.db.commit()
    ctx.deps.db.refresh(m)
    # Compute embeddings (DashScope) for semantic retrieval; failure/missing key doesn't block (ask_knowledge falls back to keywords)
    embedded = False
    try:
        import json as _json

        from geo.services.embedding import embed_texts
        vecs = await embed_texts([body])
        if vecs:
            m.embedding_json = _json.dumps(vecs[0])
            ctx.deps.db.commit()
            embedded = True
    except Exception:  # noqa: BLE001
        pass
    return {"material_id": m.id, "chars": len(body), "title": m.title, "embedded": embedded}


async def ask_knowledge(ctx: RunContext[AgentDeps], query: str) -> dict:
    """Retrieve passages relevant to the query from the account's **own material** (read-only). Returns hit passages; answer the user based on them.
    MVP keyword retrieval (can be upgraded to semantic/vector later).
    """
    import re

    from geo.models.agent import AgentMaterialORM

    q = (query or "").strip()
    if not q:
        raise ModelRetry("query 不能为空。")
    rows = (
        ctx.deps.db.query(AgentMaterialORM)
        .filter(AgentMaterialORM.account_id == ctx.deps.account_id)
        .order_by(AgentMaterialORM.id.desc())
        .limit(200)
        .all()
    )
    if not rows:
        return {"hits": [], "hint": "账号还没有上传资料,可先用 ingest_material 添加。"}

    # 1) Semantic retrieval (DashScope embeddings) first — rank material with embeddings by cosine
    try:
        import json as _json

        from geo.services.embedding import cosine, embed_texts
        qv = await embed_texts([q])
        if qv:
            qvec = qv[0]
            sem: list[tuple[float, dict]] = []
            for m in rows:
                if not m.embedding_json:
                    continue
                try:
                    mv = _json.loads(m.embedding_json)
                except Exception:  # noqa: BLE001
                    continue
                s = cosine(qvec, mv)
                if s > 0.2:   # Threshold filters out clearly irrelevant items
                    sem.append((s, {"title": m.title, "source": m.source, "snippet": (m.text or "")[:400], "score": round(s, 3)}))
            if sem:
                sem.sort(key=lambda x: -x[0])
                return {"hits": [h for _, h in sem[:5]], "count": min(len(sem), 5), "mode": "semantic"}
    except Exception:  # noqa: BLE001 — semantic failure falls back to keywords
        pass

    # 2) Fallback: Chinese bigram keyword retrieval
    def _tokens(s: str) -> list[str]:
        """Chinese bigrams + English/numeric words — Chinese has no spaces, so whitespace tokenization doesn't work; hence 2-grams."""
        s = s.lower()
        toks = re.findall(r"[a-z0-9]{2,}", s)            # ASCII words
        for run in re.findall(r"[一-鿿]+", s):    # Chinese runs -> bigrams
            toks += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
        return toks

    qtoks = list(dict.fromkeys(_tokens(q)))[:40]
    scored: list[tuple[float, int, dict]] = []
    for m in rows:
        low = (m.text or "").lower()
        if not qtoks:
            continue
        hit_toks = sum(1 for t in qtoks if t in low)
        if hit_toks == 0:
            continue
        occ = sum(low.count(t) for t in qtoks)
        score = hit_toks * 2 + min(occ, 20) + (8 if q.lower() in low else 0)   # Mainly coverage + frequency/whole-phrase weighting
        idx = next((low.find(t) for t in qtoks if low.find(t) >= 0), 0)
        start = max(0, idx - 120)
        scored.append((score, m.id, {"title": m.title, "source": m.source, "snippet": (m.text or "")[start:start + 400]}))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    hits = [h for _, _, h in scored[:5]]
    if not hits:  # No hits -> fall back to summaries of recent material
        hits = [{"title": m.title, "source": m.source, "snippet": (m.text or "")[:300]} for m in rows[:3]]
    return {"hits": hits, "count": len(hits), "mode": "keyword"}


async def confirm_template(ctx: RunContext[AgentDeps]) -> dict:
    """【写】 Confirm the publishing plan (template) -> trigger article generation (drafts, async). Plan status draft -> confirmed.
    Without a plan, guide to draft_articles to draft directly. Replicates admin confirm_execution_plan.
    """
    from datetime import datetime

    from geo.models.ai_telemetry import AiTelemetryTopicExecutionPlanORM
    from geo.services.content_generator import schedule_generation

    _admin_scope_only(ctx, "confirm_template")
    usage_guardrail_check(ctx.deps, "confirm_template")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    plan = (
        ctx.deps.db.query(AiTelemetryTopicExecutionPlanORM)
        .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic.id)
        .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
        .first()
    )
    if plan is None:
        raise ModelRetry("当前主题还没有发文计划;可用 draft_articles 直接基于选定 query 产稿。")
    if plan.status != "confirmed":
        plan.status = "confirmed"
        plan.confirmed_at = datetime.utcnow()
        plan.confirmed_by_id = ctx.deps.account_id
        ctx.deps.db.commit()
    schedule_generation(topic_id=topic.id, plan_id=plan.id, trigger_source="agent")
    return {"plan_id": plan.id, "status": "confirmed", "hint": "已确认模板,文章生成中(草稿)。用 get_publish_status 看进度、publish_drafts 发布。"}


async def publish_drafts(ctx: RunContext[AgentDeps], draft_ids: list[int] | None = None) -> dict:
    """【写·对外】 **Really publish** generated articles to external platforms (outward-facing, real external publishing).

    **Channel guardrail**: only available in our own web app and ops console (gates.publish_own); external embed / IM /
    skill marketplace can't even see this tool's schema. For emergency shutdown set AGENT_ALLOW_EXTERNAL_PUBLISH to 0.
    **Compliance gate**: each article passes publish_gate (term list rescan + qualification check) before publishing; blocked ones aren't published,
    the reason is reported truthfully, and the remaining drafts aren't interrupted.
    draft_ids: ids of the articles to publish (omitted = all drafts with a body under this topic).
    """
    from datetime import datetime

    from agents.geo import gates
    from geo.models.ai_telemetry import TopicGeneratedDocORM
    from geo.models.user import UserORM
    from geo.services import mediumsly_publisher

    usage_guardrail_check(ctx.deps, "publish_drafts")
    hit = gates.require(ctx.deps, "publish_own")
    if hit is not None:
        return {"published": 0, **hit}
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db = ctx.deps.db
    user = db.get(UserORM, ctx.deps.account_id)
    if user is None:
        raise ModelRetry("找不到账号用户。")
    q = db.query(TopicGeneratedDocORM).filter(TopicGeneratedDocORM.topic_id == topic.id)
    if draft_ids:
        q = q.filter(TopicGeneratedDocORM.id.in_(draft_ids))
    docs = [d for d in q.all() if (d.body_markdown or "").strip()]
    if not docs:
        raise ModelRetry("没有可发布的文章,请先 draft_articles / confirm_template 产稿。")

    from geo.services.compliance_scanner import publish_gate

    published = 0
    compliance_blocked = 0
    results: list[dict] = []
    for d in docs:
        # Compliance gate: this own-site exit **wasn't wired up** before; it was the only real outbound point on the whole site
        # skipping term list/qualification checks (paid media, own accounts and content review were wired up long ago).
        # Blocked drafts aren't published and don't interrupt the rest; report truthfully so the model tells the user which sentence triggered what.
        ok, reason = publish_gate(
            db, d, topic, hook="publish_drafts_agent",
            actor_id=ctx.deps.account_id, actor_name="agent",
        )
        if not ok:
            compliance_blocked += 1
            d.mediumsly_last_error = f"COMPLIANCE_BLOCKED: {reason}"
            results.append({"doc_id": d.id, "blocked": True, "reason": reason})
            continue
        try:
            res = await mediumsly_publisher.push(d, user, topic)
            d.mediumsly_post_id = res.post_id
            d.mediumsly_url = res.url
            d.mediumsly_pushed_at = datetime.utcnow()
            d.mediumsly_last_error = None
            d.status = "published"
            published += 1
            results.append({"doc_id": d.id, "url": res.url})
        except mediumsly_publisher.MediumslyError as e:  # Don't interrupt the remaining docs
            d.mediumsly_last_error = f"{getattr(e, 'code', None) or 'ERROR'}: {e}"
            results.append({"doc_id": d.id, "error": d.mediumsly_last_error})
    db.commit()
    return {"published": published, "compliance_blocked": compliance_blocked,
            "total": len(docs), "results": results}


async def list_pending_articles(ctx: RunContext[AgentDeps]) -> dict:
    """List the current topic's articles **pending review** (draft / pending review, with id + title + summary), for the user's own review. **Strictly read-only**.

    Use this tool to answer "which articles are pending review / articles to review". Approve/reject via approve_article / reject_article.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    rows = (
        ctx.deps.db.query(TopicGeneratedDocORM)
        .filter(TopicGeneratedDocORM.topic_id == topic.id,
                TopicGeneratedDocORM.status.in_(["draft", "pending_review"]))
        .order_by(TopicGeneratedDocORM.id.desc()).limit(30).all()
    )
    return {
        "count": len(rows),
        "pending": [{"id": d.id, "title": d.title or (d.source_query_text or "")[:40] or f"doc#{d.id}",
                     "status": d.status, "summary": (d.summary or "")[:120]} for d in rows],
    }


async def list_articles(ctx: RunContext[AgentDeps], status: str | None = None, limit: int = 5) -> dict:
    """List articles **with title + summary + body excerpt** (filterable by status: draft/pending_review/approved/published/rejected). Read-only.
    [When to use] "list several · browse by status · show a few" -> use me; full text of one -> get_article; only pending review -> list_pending_articles; only publishing progress -> get_publish_status.

    Use this tool when the user wants to "show/see these articles, article content, bodies of pending articles, send the articles here".
    For the **full body** of one article use get_article(doc_id).
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    q = ctx.deps.db.query(TopicGeneratedDocORM).filter(TopicGeneratedDocORM.topic_id == topic.id)
    if status in ("draft", "pending_review", "approved", "published", "rejected"):
        q = q.filter(TopicGeneratedDocORM.status == status)
    try:
        lim = max(1, min(int(limit or 5), 10))
    except (TypeError, ValueError):
        lim = 5
    rows = q.order_by(TopicGeneratedDocORM.id.desc()).limit(lim).all()
    return {
        "count": len(rows),
        "articles": [{
            "id": d.id, "title": d.title or (d.source_query_text or "")[:40] or f"doc#{d.id}",
            "status": d.status, "summary": (d.summary or "")[:200],
            "body_excerpt": (d.body_markdown or "")[:800],
            "truncated": len(d.body_markdown or "") > 800,
        } for d in rows],
        "note": "正文已截断到 800 字;要某篇完整正文用 get_article(doc_id)。" if rows else "没有符合条件的文章。",
    }


async def get_article(ctx: RunContext[AgentDeps], doc_id: int) -> dict:
    """View the **full content of one article** (title/summary/body/status). Read-only. Use when the user wants to "see/show article N, the full text of one"."""
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    topic = _account_topic(ctx)
    d = ctx.deps.db.get(TopicGeneratedDocORM, doc_id)
    if d is None or topic is None or d.topic_id != topic.id:
        raise ModelRetry("找不到这篇文章,或不属于当前账号主题;先用 list_articles 看 id。")
    body = d.body_markdown or ""
    return {
        "id": d.id, "title": d.title or "(无标题)", "status": d.status,
        "summary": (d.summary or "")[:400], "body": body[:4000], "truncated": len(body) > 4000,
        "source_query": d.source_query_text or "",
    }


def _review_doc(ctx: RunContext[AgentDeps], doc_id: int, *, approve: bool, reason: str = ""):
    from datetime import datetime

    from geo.models.ai_telemetry import TopicGeneratedDocORM

    _admin_scope_only(ctx, "approve_article / reject_article")
    usage_guardrail_check(ctx.deps, "review_article")
    topic = _account_topic(ctx)
    d = ctx.deps.db.get(TopicGeneratedDocORM, doc_id)
    if d is None or topic is None or d.topic_id != topic.id:
        raise ModelRetry("找不到这篇文章,或它不属于当前账号主题;先用 list_pending_articles 看 id。")
    if d.status not in ("draft", "pending_review"):
        raise ModelRetry(f"文章当前 status={d.status},不可审核(只能审 草稿/待审)。")
    d.status = "approved" if approve else "rejected"
    d.review_decision_at = datetime.utcnow()
    d.reviewer_id = ctx.deps.account_id
    if approve:
        d.selected_for_review = True
        d.reject_reason = None
    else:
        d.reject_reason = (reason or "").strip()[:500]
    ctx.deps.db.commit()
    return {"id": d.id, "title": d.title, "status": d.status}


async def approve_article(ctx: RunContext[AgentDeps], doc_id: int) -> dict:
    """【写】 **Approve** an article (draft/pending_review -> approved). Only call when the user explicitly wants to "approve/accept" it."""
    return _review_doc(ctx, doc_id, approve=True)


async def reject_article(ctx: RunContext[AgentDeps], doc_id: int, reason: str = "") -> dict:
    """【写】 **Reject** an article (-> rejected, with a reason). Only call when the user explicitly wants to "reject/decline" it."""
    return _review_doc(ctx, doc_id, approve=False, reason=reason)


async def list_unhit_queries(ctx: RunContext[AgentDeps]) -> dict:
    """List the current topic's questions (queries) **placed but not yet hit (found) by AI engines**. Read-only.

    Use this tool to answer "which weren't hit / weren't found / which aren't covered / what are those 5".
    Metric definition: questions in the placement set (queries_json) with total_hits=0 in the hit tracking table.
    ⚠️ This is a **question-level** metric definition, **not the same thing** as the report center's overall exposure rate (counted per answer);
    never mix the two numbers or convert between them.
    """
    import json

    from sqlalchemy import distinct

    from geo.models.ai_telemetry import AiTelemetryQueryHitORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db, tid = ctx.deps.db, topic.id
    selected = [q["text"] for q in json.loads(topic.queries_json or "[]")
                if isinstance(q, dict) and q.get("text") and not q.get("deleted_at")]
    hit_set = {r[0] for r in db.query(distinct(AiTelemetryQueryHitORM.query)).filter(
        AiTelemetryQueryHitORM.topic_id == tid, AiTelemetryQueryHitORM.total_hits > 0).all()}
    unhit = [q for q in selected if q not in hit_set]
    return {
        "monitored": len(selected), "hit": len(selected) - len(unhit), "unhit_count": len(unhit),
        "unhit_queries": unhit[:60],
        "note": ("全部已命中。" if not unhit else
                 f"投放 {len(selected)} 个,{len(unhit)} 个未命中" + ("(列表已截断,只显示前 60)" if len(unhit) > 60 else "")),
    }


async def list_hit_queries(ctx: RunContext[AgentDeps]) -> dict:
    """List the current topic's queries **placed and already hit (found) by AI engines**, with the hitting engines and cumulative counts. Read-only.

    Use this tool to answer "which were hit / what was hit / what did those 140 hit / hit details / list hits one by one";
    complements list_unhit_queries (hit + unhit = placed). Metric definition: questions in the placement set (queries_json) with
    total_hits>0 in the hit tracking table, sorted by cumulative hits descending; each item lists the engines that hit it.
    ⚠️ This is a **question-level** metric definition, **not the same thing** as the report center's overall exposure rate (counted per answer);
    never mix the two numbers or convert between them.
    """
    import json
    from collections import defaultdict

    from geo.models.ai_telemetry import AiTelemetryQueryHitORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    db, tid = ctx.deps.db, topic.id
    selected = [q["text"] for q in json.loads(topic.queries_json or "[]")
                if isinstance(q, dict) and q.get("text") and not q.get("deleted_at")]
    selected_set = set(selected)
    rows = db.query(AiTelemetryQueryHitORM.query, AiTelemetryQueryHitORM.engine,
                    AiTelemetryQueryHitORM.total_hits).filter(
        AiTelemetryQueryHitORM.topic_id == tid, AiTelemetryQueryHitORM.total_hits > 0).all()
    agg: dict = defaultdict(lambda: {"engines": set(), "total_hits": 0})
    for q, engine, th in rows:
        if selected_set and q not in selected_set:    # Restricted to the placement set, consistent with list_unhit_queries
            continue
        agg[q]["engines"].add(engine)
        agg[q]["total_hits"] += (th or 0)
    items = sorted(agg.items(), key=lambda kv: kv[1]["total_hits"], reverse=True)
    hit_list = [{"query": q, "engines": sorted(v["engines"]), "total_hits": v["total_hits"]} for q, v in items]
    CAP = 200
    return {
        "monitored": len(selected), "hit_count": len(hit_list),
        "unhit_count": max(0, len(selected) - len(hit_list)),
        "hit_queries": hit_list[:CAP],
        "note": ("暂无命中。" if not hit_list else
                 f"投放 {len(selected)} 个,{len(hit_list)} 个已命中" +
                 (f"(列表已截断,只显示命中次数最高的前 {CAP})" if len(hit_list) > CAP else "")),
    }


def _sentiment_account(ctx: RunContext[AgentDeps], active_only: bool = False):
    from geo.models.sentiment import SentimentAccountORM

    q = ctx.deps.db.query(SentimentAccountORM).filter(SentimentAccountORM.user_id == ctx.deps.account_id)
    if active_only:
        q = q.filter(SentimentAccountORM.active.is_(True))
    return q.order_by(SentimentAccountORM.last_run_at.desc().nullslast(), SentimentAccountORM.id.desc()).first()


def _shallow_trim(d, cap: int = 6):
    """Prevent token bloat: truncate lists in the dict to the first cap items (shallow)."""
    if isinstance(d, dict):
        return {k: (v[:cap] if isinstance(v, list) else v) for k, v in d.items()}
    return d


async def get_sentiment_today(ctx: RunContext[AgentDeps]) -> dict:
    """Query today's overview of the current account's **daily health monitoring** (KPIs / 7-day trend / risk distribution / latest brief / top high-risk). Read-only.

    This is the **subscription-based continuous monitoring** line (daily batch runs, history, alerts). When the user asks "how's sentiment today /
    any negatives / risks" and the account has daily monitoring enabled, use this tool; **don't get stuck if daily monitoring isn't enabled** —
    use run_sentiment_once for a one-off quick check instead (it requires creating nothing).

    The sentiment score scale is **−1 to +1** (−1 very negative / 0 neutral / +1 very positive, ±0.2 is the positive/negative boundary),
    **not a percentage** — read it as-is; never say −0.31 as "−31%".
    Express enums like stance/intent/factuality in Chinese.
    """
    from datetime import datetime

    from geo.services import sentinel_client

    acc = _sentiment_account(ctx, active_only=True)
    if acc is None:
        return {"configured": False,
                "note": "这个账号没有开日常健康监测(持续监测:每天自动跑一轮,含历史与预警,按天扣积分)。",
                "next": "想现在就看一眼这个品牌的舆情,用 run_sentiment_once 跑单次快检 —— "
                        "不用创建账户、不用订阅,报品牌名就能跑(会先出确认卡报价)。"
                        "用户要「每天帮我盯着」才提日常监控,那个用 start_sentiment_monitor "
                        "**在对话里就能开**。先问用户要看哪个品牌。"}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        data = await asyncio.to_thread(sentinel_client.get_today, acc.id, acc.ticker, 7)
        result = {"configured": True, "brand": acc.target, "status": acc.last_run_status,
                  "today": _shallow_trim(data), "fetched_at": now}
        _sent_today_cache[int(acc.id)] = result        # Record the last success for silent fallback during transient jitter
        return result
    except Exception:  # noqa: BLE001 — transient sentinel jitter: silently fall back to the last result; never expose "endpoint unavailable" to users
        cached = _sent_today_cache.get(int(acc.id))
        if cached:
            return {**cached, "brand": acc.target}
        # Not even a cache: still don't report "endpoint unavailable"; give an internal hint so the model can query history instead (get_sentiment_history reads the DB directly)
        return {"configured": True, "brand": acc.target, "status": acc.last_run_status, "fetched_at": now,
                "_internal": "今日聚合本次没取到;可改用 get_sentiment_history 直连库查近况。"
                             "**绝不要**对用户说舆情接口不可用/无法拉取/取数失败。"}


# ── One-off sentiment quick check ───────────────────────────────────────────────
# A separate line from the "daily health monitoring" above: one-off checks create no account, no subscription, no batch runs; just give a brand name.
# Implemented in geo/services/sentiment_once.py (start + poll, because a quick check round may take up to 360s,
# while one agent conversation turn only has 240s).

def _once_terms(ctx: RunContext[AgentDeps], brand: str, keywords: list[str] | None) -> list[str]:
    kws = [k.strip() for k in (keywords or []) if k and k.strip()][:8]
    return kws or [brand]


def _once_industry(ctx: RunContext[AgentDeps], industry: list[str] | None) -> list[str]:
    """Category terms — used for **brand disambiguation**, not as a filter.

    Searching by brand name alone pulls in same-named entities from other industries; brands whose names contain an industry word
    ("GEO Analytics") are worse — what comes back is negativity about the whole industry, attributed to this one brand.
    With the category added, the search terms go from 「GEO Analytics 舆情 负面」 ("GEO Analytics sentiment negative") to 「GEO Analytics AI搜索优化
    舆情 负面」 ("GEO Analytics AI search optimization sentiment negative"). The same fix was already verified in report #99 (<brand-a> vs <namesake-company>).

    Explicitly passed values take priority; otherwise derived from the topic's industry / seed terms (reusing the diagnosis `_fd_industry_terms`).
    """
    got = [t.strip() for t in (industry or []) if t and t.strip()][:3]
    if got:
        return got
    # Quick checks **don't need a topic** (just give a brand name), so not finding a topic is a normal path, not an error:
    # the anonymous side has no db at all, and accounts that never created a topic are the same. If nothing can be derived, run with it empty.
    try:
        topic = _account_topic(ctx)
    except Exception:  # noqa: BLE001
        return []
    return _fd_industry_terms(topic)[:3] if topic is not None else []


def _once_brand(ctx: RunContext[AgentDeps], brand: str) -> str:
    """Brand name: explicitly passed value takes priority, otherwise the account's topic name. If neither, let the model ask the user."""
    name = (brand or "").strip()
    if name:
        return name
    topic = _account_topic(ctx)
    name = (getattr(topic, "name", "") or "").strip()
    if not name:
        raise ModelRetry("缺品牌名。先问用户「要查哪个品牌的舆情?」拿到名称再调本工具"
                         "(单次快检不需要建主题、也不需要开日常监测)。")
    return name


def _once_digest(result: dict, brand: str) -> dict:
    """Compress quick-check results into a shape that can be read to the user — every negative item carries the original post link and publish time; items without a link are omitted."""
    kpi = result.get("kpi") or {}
    posts = [p for p in (result.get("posts") or []) if isinstance(p, dict)]
    neg = [p for p in (result.get("negative_posts") or []) if isinstance(p, dict)] or [
        p for p in posts if str(p.get("sentiment_label") or "").lower() in
        ("负面", "negative", "bearish", "看跌", "利空")
    ]

    def _row(p: dict) -> dict:
        return {"标题": p.get("title") or "", "来源": p.get("source") or "",
                "原帖链接": p.get("url") or "", "发布时间": p.get("publish_time") or "",
                "风险": p.get("risk_level") or "", "摘要": (p.get("summary") or "")[:160]}

    return {
        "mode": "单次快检", "brand": brand,
        "条目数": kpi.get("total_today"),
        "中高风险条数": kpi.get("high_risk"),
        "平均情感分": kpi.get("avg_sentiment"),
        # Give the scale along with the number. Previously only a bare number like -0.31 was given, while the prompt said 「用百分比表述」 ("express as a percentage"),
        # so it was read as "-31%" — users understood "31% of content is negative", which isn't what it means at all.
        "平均情感分_口径": "−1 到 +1(−1 极负面 / 0 中性 / +1 极正面),±0.2 为正负分界;"
                           "这是本轮报告级的综合判定,**不是逐帖分数的算术平均**,"
                           "**不是百分比**,原样念、别换算",
        "信源数": kpi.get("active_sources"),
        "风险分布": result.get("risk_dist"),
        "负面明细": [_row(p) for p in neg[:8]],
        "证据时间范围": result.get("evidence_range"),
        "近期条数": result.get("recent_count"),
        "更早条数": result.get("historical_count"),
        "无日期条数": result.get("undated_count"),
        "generated_at": result.get("generated_at"),
        "note": "单次快检:一次性抓取,不含历史与预警。证据横跨的时间范围见「证据时间范围」,"
                "**不要**说成「最近 30 天」。要每天自动盯 + 负面预警 + 历史回溯,那是日常健康监测,"
                "用 start_sentiment_monitor **在对话里就能开通**(会先出确认卡说清每天扣多少)。",
    }


async def run_sentiment_once(ctx: RunContext[AgentDeps], brand: str = "",
                             keywords: list[str] | None = None,
                             industry_terms: list[str] | None = None,
                             confirm_token: str = "") -> dict:
    """【动作·花钱】 Run a **one-off sentiment quick check** — no account or subscription needed, but credits are charged per run.

    Use this tool when the user asks "any negatives / how's sentiment / is anyone bashing us" and the account **doesn't have daily health monitoring enabled**;
    also when the user explicitly says "one-off / just once / check sentiment now".

    **Two-step confirmation**: first call without confirm_token to get a preview, read "what runs / how many credits / remaining balance"
    to the user and **end this turn**; after their next message explicitly confirms, call again with the same params + confirm_token.
    If it already ran within 24h, or the full diagnosis just ran the same content for free, **no repeat charge**; the preview says so.

    brand: brand name, defaults to the account's topic name; keywords: words to search together (aliases, product names), optional;
    industry_terms: **1–3 category terms** (e.g. "AI search optimization", "commercial floor-cleaning robots") —
                    used to separate same-named other companies and whole-industry negativity from the results.
                    If omitted they're derived from the topic's industry info; if they can't be derived **you may ask the user once**,
                    but **never guess their line of business yourself**.

    If it hasn't run before, this tool **only starts it**; a round takes about 1–5 minutes: tell the user to wait a moment,
    and fetch the result with get_sentiment_once on the next message; **don't poll repeatedly in this turn**.
    """
    from agents.geo import confirm
    from agents.geo.tools_billing import quote
    from geo.services import sentiment_once

    usage_guardrail_check(ctx.deps, "run_sentiment_once")
    name = _once_brand(ctx, brand)
    terms = _once_terms(ctx, name, keywords)
    cat = _once_industry(ctx, industry_terms)

    # The args fingerprint uses the **normalized** brand and terms: the model filling brand from an empty string to the topic name between turns is normal,
    # and that shouldn't invalidate the ticket (it's asking the same thing).
    args = {"brand": name, "keywords": terms, "industry": cat}

    def _preview() -> dict:
        cached = sentiment_once.peek(ctx.deps.user_id, name, terms, cat) is not None
        q = quote(ctx.deps.db, ctx.deps.user_id, "sentiment", 1)
        return confirm.needs_confirm(
            ctx.deps, tool="run_sentiment_once", action_label="跑一次单次舆情快检",
            args=args,
            preview={"品牌": name, "一起搜的词": terms,
                     "品类(用来和同名的别家区分开)": cat or "(没有,结果可能混进同名主体或整个行业的负面)",
                     "耗时": "约 1–5 分钟",
                     "给什么": "当下这一轮抓到的条目、风险分级、负面明细(带原帖链接和时间)",
                     "24h内已有结果": cached},
            impact=(["24 小时内已经跑过同一个品牌,这次直接返回上次结果,**不扣积分**"]
                    if cached else
                    ["会真的去抓一轮,按单扣积分", "单次快检没有历史、也没有预警"]),
            irreversible=[],
            cost=({"合计": "0 积分(复用 24h 内的结果)",
                   "当前积分": q.get("当前积分")} if cached else
                  {k: v for k, v in q.items() if not k.startswith("_")}),
            note="全面诊断里已经包含舆情章节且不额外收费 —— 用户刚跑过诊断的话,"
                 "先问他是不是要看诊断里那一份,别让他为同一批内容付两次。",
        )

    if not confirm_token:
        return _preview()
    stop = confirm.guard(ctx.deps, confirm_token, tool="run_sentiment_once",
                         args=args, rebuild_preview=_preview)
    if stop is not None:
        return stop

    st = await asyncio.to_thread(sentiment_once.start, ctx.deps.user_id, name, terms, cat)
    state = st.get("state")
    if state == "ready":
        return {"status": "ready", **_once_digest(st.get("result") or {}, name)}
    if state == "degraded":
        # Cache unavailable -> queuing would drop the result into a black hole. Run synchronously on the spot; if it can't finish, say so truthfully.
        sync = await asyncio.to_thread(sentiment_once.run_sync, ctx.deps.user_id,
                                       name, terms, cat)
        if sync.get("state") == "ready":
            return {"status": "ready", **_once_digest(sync.get("result") or {}, name)}
        if sync.get("state") == "timeout":
            return {"status": "timeout", "brand": name,
                    "note": "这次没在限时内跑完。告诉用户过几分钟用同一个品牌名再问一次,不要连着重发。"}
        # The whole fetch pipeline is unavailable — use public search as fallback.
        # ⚠️ Yuanbao WebSearch **can't** be the fallback here: it goes through telemetry's /run-now itself,
        #    so it goes down with the batch run center — a circular dependency. Only the in-process direct public path works.
        return await _sentiment_fallback(ctx, name, terms,
                                         reason=str(sync.get("error") or "抓取链路不可用"))
    return {"status": "running", "brand": name, "keywords": terms,
            "note": "已经开始跑了,约 1–5 分钟。请把这句告诉用户并**结束本轮**;"
                    "他下次问的时候用 get_sentiment_once 取结果。"}


async def _sentiment_fallback(ctx: RunContext[AgentDeps], brand: str,
                              terms: list[str], *, reason: str) -> dict:
    """Degradation when sentiment fetching is down: public search evidence gathering, normalized into a dict of **the same shape** as `_once_digest`.

    Keys must match the normal result exactly (values may be null) — the frontend and prompt are hard-coded to that shape;
    a different key set when degraded means another branch in the display layer, and that branch will sooner or later go unmaintained.
    """
    from agents.geo import degrade, websearch

    queries = [f"{brand} 投诉", f"{brand} 差评", f"{brand} 负面", brand] + terms[:2]
    out = await asyncio.to_thread(websearch.search_many, queries)
    deg = degrade.Degraded(
        missing=["全平台社媒抓取", "情感打分", "风险分级"],
        reason=f"舆情抓取暂时不可用({reason})",
        evidence_provider="tencent" if out.get("available") else "",
    )
    if not out.get("available"):
        return degrade.wrap({
            "status": "failed", "mode": "单次快检", "brand": brand,
            "note": "这次没取到结果,公网检索也没能用上。如实告诉用户过几分钟再试。"
                    "**不要**因为没查到就说「没有负面消息」—— 那是两回事。",
        }, degrade.Degraded(level=degrade.UNAVAILABLE, missing=deg.missing, reason=deg.reason))

    ev = out["evidence"]
    return degrade.wrap({
        "status": "ready",
        "mode": "公网检索(降级)", "brand": brand,
        # No numeric metrics at all — search results can't yield sentiment scores or risk grading,
        # and degrade.wrap adds another safety net
        "条目数": len(ev), "中高风险条数": None, "平均情感分": None,
        "信源数": len({e["source_domain"] for e in ev if e["source_domain"]}),
        "风险分布": None,
        "负面明细": [{
            "标题": e["title"], "来源": e["source_domain"], "原帖链接": e["url"],
            "发布时间": e["published_at"] or "未标注", "风险": "", "摘要": e["snippet"][:160],
        } for e in ev[:8]],
        "证据时间范围": None,
        "note": "这是**降级结果**:走的是公网检索,不是全平台社媒抓取,"
                "没有情感打分也没有风险分级。转述时必须说清这一点,"
                "**不要**说成「监测到 N 条负面」。抓取恢复后再跑一次真快检。",
    }, deg)


async def get_sentiment_once(ctx: RunContext[AgentDeps], brand: str = "",
                             keywords: list[str] | None = None) -> dict:
    """Read the result or progress of a **one-off sentiment quick check**. Read-only; doesn't trigger a new fetch.

    When the user asks about sentiment again after starting a quick check, use this tool first to see if the result is out; don't start again.
    brand / keywords must match those used at start (defaults to the topic name).
    """
    from geo.services import sentiment_once

    name = _once_brand(ctx, brand)
    terms = _once_terms(ctx, name, keywords)
    cat = _once_industry(ctx, None)
    # user_id must be passed — the quick-check cache is partitioned by account; omitting it would read someone else's result.
    # The category must also match the start, otherwise a different key is computed and your own run is never found.
    st = await asyncio.to_thread(sentiment_once.status, ctx.deps.user_id, name, terms, cat)
    state = st.get("state")
    if state == "ready":
        return {"status": "ready", **_once_digest(st.get("result") or {}, name)}
    if state == "running":
        return {"status": "running", "brand": name, "已跑秒数": st.get("elapsed_s"),
                "note": "还在跑,约 1–5 分钟。把进度告诉用户即可,不要反复调本工具、也不要重新发起。"}
    if state in ("failed", "stale"):
        return {"status": "failed", "brand": name,
                "note": "上一单没跑成。可以用 run_sentiment_once 重新发起一次。"
                        "对用户如实说「上次没取到结果,再跑一次」,不要展示技术错误。"}
    return {"status": "none", "brand": name,
            "note": "这个品牌还没有跑过单次快检,用 run_sentiment_once 发起一次。"}


# Negative criteria: Chinese and English negative labels + bearish (stock metric definition) + low sentiment score. Consistent with the historical search metric definition.
_NEG_SQL = "(lower(coalesce(a.sentiment_label,'')) IN ('负面','negative','bearish','看跌','利空') OR a.sentiment_score < -0.2)"


async def get_sentiment_history(
    ctx: RunContext[AgentDeps],
    before: str | None = None,
    after: str | None = None,
    only_negative: bool = True,
    limit: int = 10,
) -> dict:
    """Query **historical sentiment** (persisted posts + sentiment analysis, traceable far back). Read-only.

    You **must** use this tool to answer "were there negatives before day X / during some period", "what negatives/bearish news historically", "past sentiment" —
    get_sentiment_today only covers today; **don't** claim out of thin air "no earlier data / not retained".
    Fill before/after as 'YYYY-MM-DD' (filters by post publish time; before = before that day, after = that day and later).
    only_negative=True returns only negatives (default). Returns the total hit count + up to limit samples (title/source/link/time/sentiment).
    Note: in the stock metric definition bearish ≈ negative; the lower sentiment_score, the more negative.
    """
    from datetime import datetime as _dt
    from sqlalchemy import text

    acc = _sentiment_account(ctx, active_only=True)
    if acc is None:
        return {"configured": False, "note": "当前账号还没有配置舆情监控,无法查历史。可去舆情页创建监控账户。"}

    def _vd(s):  # Validate YYYY-MM-DD: prevents injection + dirty input
        if not s:
            return None
        try:
            return _dt.strptime(s.strip(), "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None

    before_d, after_d = _vd(before), _vd(after)
    schema = f"tenant_{int(acc.id)}"          # acc.id is an integer, cast before inserting into the schema name (bind params can't be used)
    limit = max(1, min(int(limit or 10), 25))

    where = ["a.symbol = :sym", "p.publish_time IS NOT NULL"]
    params: dict = {"sym": acc.ticker}
    if before_d:
        where.append("p.publish_time < :before"); params["before"] = before_d
    if after_d:
        where.append("p.publish_time >= :after"); params["after"] = after_d
    if only_negative:
        where.append(_NEG_SQL)
    wsql = " AND ".join(where)
    base = f"FROM {schema}.analyses a JOIN {schema}.posts p USING (source, post_id, symbol) WHERE {wsql}"

    try:
        total = ctx.deps.db.execute(text(f"SELECT count(*) {base}"), params).scalar() or 0
        rows = ctx.deps.db.execute(text(
            f"SELECT p.title, a.source, p.url, p.publish_time, a.sentiment_label, a.sentiment_score, a.summary "
            f"{base} ORDER BY p.publish_time DESC LIMIT :lim"), {**params, "lim": limit}).fetchall()
    except Exception:  # noqa: BLE001 — schema missing / fetch failed: give an internal hint; never expose "fetch failed / endpoint unavailable" to users
        return {"configured": True, "brand": acc.target, "matched_total": 0, "returned": 0, "items": [],
                "_internal": "本次历史检索没取到(可能该账号还没跑过批或数据未入库)。"
                             "**绝不要**对用户说取数失败/接口不可用;如确实无数据,据实说这段时间暂无入库的相关负面即可。"}

    items = [{
        "title": (r[0] or "")[:120], "source": r[1], "url": r[2],
        "publish_time": str(r[3])[:10] if r[3] else None,
        "sentiment_label": r[4], "sentiment_score": r[5],
        "summary": (r[6] or "")[:160],
    } for r in rows]
    return {
        "configured": True, "brand": acc.target, "ticker": acc.ticker,
        "window": {"before": before_d, "after": after_d}, "only_negative": only_negative,
        "matched_total": int(total), "returned": len(items), "items": items,
        "note": "matched_total 是命中总数,items 仅样本(最多 limit 条,按时间倒序)。无 publish_time 的帖子未计入。",
    }


async def get_hot_topics(ctx: RunContext[AgentDeps], source: str | None = None, limit: int = 15) -> dict:
    """Query **NewsNow real-time hot lists/trending topics** (same source as the "Trending" tab on the sentiment page). Read-only.

    Use this tool to answer "what's trending today / hot lists / Weibo trending / any trends to ride / recent popular topics".
    If source is omitted, the first of the account's subscribed hot-list sources (newsnow_sources) is used; you can also specify e.g. weibo/zhihu/toutiao/v2ex/baidu.
    Returns the top N trending items of that source (title/link/heat), for topic selection and trend-riding reference.
    Note: these are **market-wide real-time trends** (not tied to a brand); for **brand-related negatives/sentiment** use get_sentiment_today / get_sentiment_history.
    """
    import json

    from geo.services import sentinel_client

    acc = _sentiment_account(ctx, active_only=True)
    srcs: list = []
    if acc is not None:
        try:
            srcs = json.loads(acc.newsnow_sources_json or "[]")
        except (ValueError, TypeError):
            srcs = []
    from datetime import datetime

    src = (source or "").strip() or (str(srcs[0]) if srcs else "weibo")
    limit = max(1, min(int(limit or 15), 30))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        data = await asyncio.to_thread(sentinel_client.get_newsnow_hot, src, limit)
        result = {"source": src, "subscribed_sources": srcs,
                  "hot": _shallow_trim(data, limit), "fetched_at": now}
        _hot_cache[src] = result                       # Record the last successful result for silent fallback during cooldown
        return result
    except Exception:  # noqa: BLE001 — fetch cooldown/jitter: silently fall back to the last result; never expose "failed/unavailable" to users
        cached = _hot_cache.get(src)
        if cached:
            # Recent cache hit: return trends as usual + the last fetch time (the data is recent anyway; no need to announce it's cached)
            return {**cached, "subscribed_sources": srcs}
        # Not even a cache (rare): still don't report "failed"; give an internal hint so the model retries with another source, don't tell users it's unavailable
        return {"source": src, "subscribed_sources": srcs, "hot": [], "fetched_at": now,
                "_internal": "本次该源没取到条目;请换个 source 再试。**不要**对用户说接口失败/不可用/无法获取。"}


async def configure_sentiment(
    ctx: RunContext[AgentDeps],
    keywords: list[str] | None = None,
    aliases: list[str] | None = None,
    brand: str | None = None,
) -> dict:
    """【写】 Configure the current account's sentiment monitoring: update monitored **keywords / aliases / brand name**. Only call when the user explicitly wants to configure it.

    If there's no daily monitoring yet, enable it first (start_sentiment_monitor, can be done in conversation). Changes take effect on the next batch run.
    """
    import json

    usage_guardrail_check(ctx.deps, "configure_sentiment")
    acc = _sentiment_account(ctx)
    if acc is None:
        raise ModelRetry(
            "这个账号还没开日常健康监测,所以没有提示词可配 —— 提示词是持续跑批用的。"
            "要开的话用 start_sentiment_monitor(**对话里就能开**,它会先出确认卡说清每天扣多少),"
            "开完再回来配词。"
            "如果用户只是想现在看一眼舆情,别卡在这里:用 run_sentiment_once 跑单次快检,"
            "想搜的词直接当 keywords 传进去就行。")
    changed: list[str] = []
    if brand and brand.strip():
        acc.target = brand.strip()
        changed.append("品牌名")
    if keywords is not None:
        kw = [k.strip() for k in keywords if k and k.strip()]
        acc.keywords_json = json.dumps(kw, ensure_ascii=False)
        changed.append(f"关键词({len(kw)}个)")
    if aliases is not None:
        al = [a.strip() for a in aliases if a and a.strip()]
        acc.aliases_json = json.dumps(al, ensure_ascii=False)
        changed.append(f"别名({len(al)}个)")
    if not changed:
        return {"updated": False, "note": "没有要更新的内容,请提供 keywords / aliases / brand 之一。"}
    ctx.deps.db.commit()
    return {"updated": True, "brand": acc.target, "changed": changed, "note": "已更新,下次舆情跑批生效。"}


async def list_action_items(ctx: RunContext[AgentDeps], status: str = "open") -> dict:
    """List the to-dos in the current topic's optimization task center (flywheel: an action list combining daily report suggestions/retest regressions/insight suggestions/placement media suggestions). Read-only.

    Use this tool to answer "any to-dos / optimization tasks / what should I do / action list".
    status is optional: open (default) / converted / dismissed / done.
    """
    import json as _json

    from geo.models.ai_telemetry import GeoActionItemORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    q = ctx.deps.db.query(GeoActionItemORM).filter(GeoActionItemORM.topic_id == topic.id)
    if status in ("open", "converted", "dismissed", "done"):
        q = q.filter(GeoActionItemORM.status == status)
    rows = (q.order_by(GeoActionItemORM.priority.asc(),
                       GeoActionItemORM.created_at.desc())
             .limit(30).all())
    src_zh = {"daily_brief": "日报建议", "retest": "复测退步", "cell_insight": "洞察建议",
              "source_strategy": "投放媒体建议", "manual": "手录"}
    items = [{
        "id": r.id, "priority": r.priority, "title": r.title,
        "source": src_zh.get(r.source, r.source), "status": r.status,
        "detail": (r.body_md or "")[:300],
        "可转化": ("转发文计划" if r.suggested_action == "add_plan_item"
                 else "转复测" if r.suggested_action == "retest" else "改稿/其它"),
    } for r in rows]
    return {"count": len(items), "items": items,
            "note": ("任务中心为空。" if not items else
                     "用 convert_action_item 可把任务转成发文计划行或定向复测;dismiss 忽略。")}


async def convert_action_item(ctx: RunContext[AgentDeps], item_id: int,
                              action: str, seed: str = "") -> dict:
    """【写】 Handle one task in the optimization task center. Only call when the user explicitly wants to "convert/execute/ignore" a task.

    action is one of three:
    - add_plan_item: convert into a publishing plan row (topic flows back, then automatic generation/publishing)
    - retest: register a targeted retest for the linked draft
    - dismiss: ignore the task

    seed: the topic to write for the add_plan_item row. If omitted, the task title is used as the topic; but titles of 「投放媒体建议」 ("placement media suggestion")
    tasks are just platform names (「投放媒体建议:博客园」 ("placement media suggestion: Cnblogs")), and using that as the topic would produce an article about Cnblogs,
    so for such tasks the user must say what to write, which then goes into seed.
    """
    from fastapi import HTTPException

    from geo.api.action_items import ConvertPayload
    from geo.api.action_items import convert_action_item as _convert_ep
    from geo.api.action_items import dismiss_action_item as _dismiss_ep
    from geo.models.ai_telemetry import GeoActionItemORM

    _admin_scope_only(ctx, "convert_action_item")
    usage_guardrail_check(ctx.deps, "convert_action_item")
    topic = _account_topic(ctx)
    r = ctx.deps.db.get(GeoActionItemORM, item_id)
    if r is None or topic is None or r.topic_id != topic.id:
        raise ModelRetry("找不到这条任务,或它不属于当前账号主题;先用 list_action_items 看 id。")
    # ★ Must be a real UserORM. A fake object type("Actor", (), {"id": ...})() only has .id,
    #   while the endpoint reads .email / .name for the audit trail; touching them yields a silent empty string or AttributeError
    #   — scope.admin_actor's docstring already explicitly forbids this shape; this was a spot that slipped through.
    from agents.geo.tools_publish import _self_actor

    actor = _self_actor(ctx)
    try:
        if action == "dismiss":
            out = _dismiss_ep(item_id, _admin=actor, db=ctx.deps.db)
        elif action in ("add_plan_item", "retest"):
            out = _convert_ep(item_id, ConvertPayload(action=action, seed=seed.strip()),
                              admin=actor, db=ctx.deps.db)
        else:
            raise ModelRetry("action 必须是 add_plan_item / retest / dismiss 之一。")
    except HTTPException as e:
        raise ModelRetry(f"任务转化失败:{e.detail}")
    return {"id": out.id, "status": out.status, "title": out.title,
            "note": {"converted": "已转化,后续走自动生成/发布或复测链路。",
                     "dismissed": "已忽略。"}.get(out.status, out.status)}


# ── Capabilities not yet wired up (the matching ai_telemetry endpoint logic must first be extracted into reusable services) ──────
#   trace_sources (source tracing) / analyze_competitor (competitor deep dive) / edit_article (article editing)
# Note: report-center metric data fetching is already extracted into agents/geo/report_source.py; new report tools should reuse it directly,
#     don't write separate aggregations in this file (they'd diverge from the report-center page's metric definition).


# Tool list registered with the Agent (referenced by agent.py). Adding a tool here makes it drop-in.
# ⚠️ **This is a registry, not a toolset.** What's actually loaded for the model is agent._line_tools (business line × channel).
#
# Before 2026-08-25 build_agent("both") used it directly, and it **missed the two one-off quick-check tools** —
# so mixed intents like "check sentiment and also run a GEO diagnosis" routed to both, and the internal web agent
# couldn't reach quick check while external embeds could. Now both takes the same path as the other lines; this is only a registry,
# and tests/geo_agent/test_toolset_consistency.py asserts it no longer misses anything.
TOOLS = [
    create_topic,
    get_topic,
    set_seed_prompts,
    get_prompts,
    expand_prompts,
    set_selected_queries,
    # Unified audit entry: the old run_geo_checks / trigger_diagnosis / get_diagnosis_report are retired
    run_full_diagnosis,
    get_full_diagnosis,
    draft_articles,
    get_run_status,
    # Report-center metric definitions (2026-07 redesign, replacing the old brand-growth get_growth_summary / get_today_effect)
    get_report_overview,
    get_period_report,
    list_report_periods,
    get_query_coverage,
    get_publish_status,
    list_unhit_queries,
    list_hit_queries,
    list_pending_articles,
    list_articles,
    get_article,
    approve_article,
    reject_article,
    get_sentiment_today,
    get_sentiment_history,
    get_hot_topics,
    configure_sentiment,
    ingest_material,
    ask_knowledge,
    confirm_template,
    publish_drafts,
    list_action_items,
    convert_action_item,
    run_sentiment_once,
    get_sentiment_once,
    list_topics,
    switch_topic,
    set_topic_website,
    retry_failed_articles,
]

# Tools defined elsewhere but also loaded on the customer line must be in the registry too (see the reason for the assertion above).
# Imported separately at the end: tools_industry / tools_monitor both import helpers from this module,
# so a top-level import would create a cycle.
def _register_external() -> None:
    from agents.geo import tools_industry as _ti
    from agents.geo import tools_monitor as _tm

    for fn in (_ti.get_industry_board, _tm.get_monitor_schedule, _tm.trigger_batch_run,
               _tm.set_monitor_schedule):
        if fn not in TOOLS:
            TOOLS.append(fn)


_register_external()
