"""Shared foundation of the ops console (admin) line — identity check / project lock / ownership check / direct route function calls.

Both tools.py and admin_tools.py import this module (a separate file avoids them importing each other in a cycle).

Three iron rules:
  1. **The project is locked by the backend**: admin tool signatures never contain topic_id; the project always comes from deps.locked_topic_id.
     If the model can't fill it, it can't switch projects — this is where "scope = within the currently selected project" is enforced,
     asserted by eval/test_admin_scope.py via inspect.signature.
  2. **No ownership check; check role + project instead**: ops act on customers' projects (topic.user_id ≠ ops account_id),
     so the assert_owns approach would wrongly reject. The escalation surface here isn't "cross-account" but "**cross-project**" —
     admin endpoints (approve_doc / select_docs_for_review etc.) look up only by doc_id and never check topic,
     so a model passing through an id from a previous project in history means a cross-customer mis-write. Every tool accepting model-supplied ids
     must first pass own_doc / own_task / own_plan_item / own_doc_ids.
  3. **Writes call route functions directly via call_route**, without copying business logic — for what copying leads to, see
     the metric divergence between tools.py's _review_doc and admin_content_review.approve_doc
     (the former doesn't trigger publish_preset or send notifications: the draft passed but not a single placement task was created).
"""
from __future__ import annotations

import inspect
import logging
import os
import re
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from agents.geo.deps import AgentDeps, OwnershipError, assert_staff

log = logging.getLogger(__name__)

# Ops console project page routes: /ops/topics/:topicId/... (fallback criterion when the frontend omits scope).
# The 2026-09-08 navigation refactor moved the ops console from /workbench to /ops, but this regex wasn't updated —
# the fallback never matched, and it's the only rescue path when "the frontend omits scope"; it failed without any error.
# Both prefixes are accepted: the point of this fallback is tolerating staggered frontend/backend releases; old frontends still send /workbench.
_ADMIN_ROUTE_RE = re.compile(r"^/(?:ops|workbench)/topics/(\d+)(?:/|$)")


# ── Roles ───────────────────────────────────────────────────────────────
def _role_of(user) -> str:
    """role is the single source of truth for permissions; legacy data without role falls back to is_admin.

    Copies the logic of _user_role in geo/api/auth.py:66. **Can't import it** — geo.api.auth
    depends on the main backend's old-FastAPI AppException, and the agent runs in a separate venv.
    """
    role = getattr(user, "role", None)
    if role:
        return str(role)
    return "admin" if getattr(user, "is_admin", False) else "user"


def is_staff_role(role: str) -> bool:
    """Workspace staff (admin | operator). Same definition as is_staff in geo/api/auth.py."""
    return role in ("admin", "operator")


def admin_enabled() -> bool:
    """Master switch for the admin line. When off, resolve_scope always returns self and the whole line disappears (for canary / emergency shutdown)."""
    return (os.environ.get("AGENT_ADMIN_ENABLED", "0") or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_scope(user, *, requested_scope: str | None,
                  topic_id: int | None, page_context: dict | None) -> str:
    """Decide whether this turn uses the self line or the admin line.

    **Keywords never take part in the decision**: ops and customers say exactly the same things ("check the plan for me", "approve this one"),
    indistinguishable at the text level; and route_line's keyword table already had incidents like "voice hijacked by the sentiment line".
    The cost of misjudging the admin line is "executing writes on a customer project", not "an incomplete answer"; we can't gamble on keywords.
    The only reliable signal is "which project's ops page you're standing on right now".
    """
    # ① Role hard gate: non-staff are always self, no matter what the frontend says
    if not is_staff_role(_role_of(user)):
        return "self"
    # ② Master switch
    if not admin_enabled():
        return "self"
    # ③ No explicit project means no admin line (the admin line may not fall back to guessing a project)
    if not topic_id:
        return "self"
    # ④ Primary criterion: explicit declaration from the frontend
    if (requested_scope or "").strip() == "admin":
        return "admin"
    # ⑤ Fallback: when the frontend omits scope, rescue once by route prefix (tolerating staggered frontend/backend releases)
    m = _ADMIN_ROUTE_RE.match(str((page_context or {}).get("route") or ""))
    if m:
        try:
            if int(m.group(1)) == int(topic_id):
                return "admin"
        except (TypeError, ValueError):
            pass
    return "self"


# ── Project anchor and actor ────────────────────────────────────────────
def admin_topic(ctx: RunContext[AgentDeps]):
    """Get the "currently locked project" on the admin line. Three fundamental differences from tools._account_topic:

    ① checks role, not ownership (the project belongs to the customer, not to ops);
    ② **no fallback** without a topic; raise ModelRetry directly so the model asks the user;
    ③ the project id always comes from deps.locked_topic_id — the model can neither supply nor change it.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    assert_staff(ctx.deps)
    tid = ctx.deps.locked_topic_id
    if not tid:
        raise ModelRetry(
            "当前没有选中的项目。请如实告诉用户:先在工作台打开某个项目的编辑页,"
            "我才能操作那个项目。**不要**自己猜项目、也不要拿别的项目顶替。"
        )
    t = ctx.deps.db.get(AiTelemetryTopicORM, tid)
    if t is None:
        raise ModelRetry(f"项目 {tid} 不存在(可能已删除),请让用户刷新页面重新选择项目。")
    return t


def admin_actor(ctx: RunContext[AgentDeps]):
    """The actor to pass to route functions for pattern ③ — use a **real UserORM**.

    No more fake objects like type("Actor", (), {"id": ...})() in tools.py:1547: route functions read
    .id / .email / .name (e.g. admin_content_review's email notifications), and a fake object only has .id,
    so touching anything else yields a silent empty string or AttributeError.

    **Re-query the DB to check the role**, don't trust the deps.role snapshot — a demotion mid-session takes effect immediately,
    at the cost of just one primary-key lookup.
    """
    from geo.models.user import UserORM

    assert_staff(ctx.deps)
    u = ctx.deps.db.get(UserORM, ctx.deps.user_id)
    if u is None or not is_staff_role(_role_of(u)):
        raise OwnershipError("运营身份校验失败(账号不存在或已被降权)")
    return u


# ── Ownership check set of four (every admin tool accepting model-supplied ids must pass first) ──────────
def own_doc(ctx: RunContext[AgentDeps], doc_id: int):
    """Check draft ownership against the locked project."""
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    topic = admin_topic(ctx)
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        raise ModelRetry("doc_id 必须是整数。先用 admin_list_docs 查到真实 id,不要臆造。")
    d = ctx.deps.db.get(TopicGeneratedDocORM, did)
    if d is None:
        raise ModelRetry(f"稿件 {did} 不存在。先用 admin_list_docs 查当前项目的稿件 id。")
    if d.topic_id != topic.id:
        raise ModelRetry(
            f"稿件 {did} 不属于当前项目「{topic.name}」,不能操作。"
            "(多半是拿了上一轮别的项目的 id)请先用 admin_list_docs 查本项目的稿件 id。"
        )
    # Soft-deleted drafts accept no operations (restore goes through admin_restore_doc, which checks ownership itself and doesn't come here)
    if getattr(d, "deleted_at", None) is not None:
        raise ModelRetry(
            f"稿件 {did} 已被删除,不能再操作它。"
            "要找回请用 admin_restore_doc;想看删了哪些用 admin_list_deleted_docs。"
        )
    return d


def own_task(ctx: RunContext[AgentDeps], task_id: int):
    """Check placement task ownership against the locked project."""
    from geo.models.ai_telemetry import TopicDocPublishTaskORM

    topic = admin_topic(ctx)
    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        raise ModelRetry("task_id 必须是整数。先用 admin_list_publish_tasks 查真实 id。")
    t = ctx.deps.db.get(TopicDocPublishTaskORM, tid)
    if t is None:
        raise ModelRetry(f"投放任务 {tid} 不存在。先用 admin_list_publish_tasks 查 id。")
    if t.topic_id != topic.id:
        raise ModelRetry(
            f"投放任务 {tid} 不属于当前项目「{topic.name}」,不能操作。"
            "请先用 admin_list_publish_tasks 查本项目的任务 id。"
        )
    return t


def own_plan(ctx: RunContext[AgentDeps]):
    """Get the latest execution plan of the locked project. Equivalent to admin_review._latest_plan_or_404, but doesn't raise 404."""
    from geo.models.ai_telemetry import AiTelemetryTopicExecutionPlanORM as PlanORM

    topic = admin_topic(ctx)
    plan = (
        ctx.deps.db.query(PlanORM)
        .filter(PlanORM.topic_id == topic.id)
        .order_by(PlanORM.id.desc())
        .first()
    )
    if plan is None:
        raise ModelRetry(
            f"项目「{topic.name}」还没有执行计划书。如实告诉用户:需要先在计划书页「启动项目」"
            "生成计划,我才能查看或调整发文表。"
        )
    return plan


def own_plan_item(plan, item_id: str) -> dict:
    """Get one publishing plan row from publishing_plan_json by id."""
    import json

    try:
        rows = json.loads(plan.publishing_plan_json or "[]")
    except Exception:  # noqa: BLE001
        rows = []
    key = str(item_id or "").strip()
    if not key:
        raise ModelRetry("item_id 不能为空。先用 admin_list_plan_items 查到计划行的 item_id。")
    for r in rows:
        if isinstance(r, dict) and str(r.get("id") or "") == key:
            return r
    raise ModelRetry(
        f"计划行 {key} 不在当前项目的发文表里。请先用 admin_list_plan_items 查真实 item_id,不要臆造。"
    )


def own_doc_ids(ctx: RunContext[AgentDeps], doc_ids: list[int]) -> tuple[list[int], list[int]]:
    """Batch filter: returns (ids belonging to this project, filtered-out ids).

    Filtered-out ones **must be reported truthfully by the caller in the return body** — silently dropping them makes the model think everything succeeded.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    topic = admin_topic(ctx)
    want: list[int] = []
    for x in doc_ids or []:
        try:
            want.append(int(x))
        except (TypeError, ValueError):
            continue
    if not want:
        raise ModelRetry("doc_ids 为空或都不是整数。先用 admin_list_docs 查真实 id。")
    ok = {
        r.id for r in ctx.deps.db.query(TopicGeneratedDocORM.id, TopicGeneratedDocORM.topic_id)
        .filter(TopicGeneratedDocORM.id.in_(want))
        .filter(TopicGeneratedDocORM.topic_id == topic.id)
        .filter(TopicGeneratedDocORM.deleted_at.is_(None))   # Deleted ones are treated as nonexistent
        .all()
    }
    kept = [i for i in want if i in ok]
    dropped = [i for i in want if i not in ok]
    if not kept:
        raise ModelRetry(
            f"这些稿件都不属于当前项目「{topic.name}」:{dropped}。"
            "请先用 admin_list_docs 查本项目的稿件 id。"
        )
    return kept, dropped


# ── Pattern ③: in-process direct calls to FastAPI route functions ─────────────────
def _detail(e) -> str:
    """HTTPException.detail is often a dict ({"code","message"}); stuffing it directly into error text yields a blob of
    JSON repr, and the model reading it often retries erratically. Prefer message."""
    d = getattr(e, "detail", "")
    if isinstance(d, dict):
        return str(d.get("message") or d.get("code") or d)
    return str(d)


def _code(e) -> str:
    d = getattr(e, "detail", "")
    if isinstance(d, dict) and d.get("code"):
        return str(d["code"])
    return f"HTTP_{getattr(e, 'status_code', 0)}"


async def call_route(fn, /, **kwargs) -> Any:
    """Call a FastAPI route function directly in-process. Three things that must be done uniformly here:

    ① For route functions declaring `background: BackgroundTasks`, create one and pass it in,
       **and await it after the call**. Starlette won't run it for you on in-process calls; missing it causes
       silent failures of the **successful return + zero effect** kind, like "plan confirmed but not a single article generated"
       or "approved but the customer never got the email" — the frontend looks fine and the missing drafts surface days later.
       confirm_execution_plan / add_plan_item / approve_doc / reject_doc all depend on it.
    ② Tiered translation of HTTPException:
         422 -> ModelRetry (bad params, worth letting the model fix them and retry)
         others -> structured failure {"ok": False, "code", "message"}
       **Not always ModelRetry**: a 409 on a write endpoint triggering a retry may cause duplicate writes.
    ③ Await sync / async route functions uniformly.
    """
    from fastapi import BackgroundTasks, HTTPException

    bg: BackgroundTasks | None = None
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # noqa: BLE001 — if the signature is unavailable, treat it as having no background
        params = {}
    for name, p in params.items():
        ann = p.annotation
        if ann is BackgroundTasks or getattr(ann, "__name__", "") == "BackgroundTasks":
            bg = BackgroundTasks()
            kwargs.setdefault(name, bg)
            break

    try:
        out = fn(**kwargs)
        if inspect.isawaitable(out):
            out = await out
    except HTTPException as e:
        if getattr(e, "status_code", 0) in (400, 422):
            raise ModelRetry(f"参数不合法:{_detail(e)}") from None
        return {"ok": False, "modified": False, "code": _code(e), "message": _detail(e)}

    # ★ Background tasks must really run, otherwise it's "successful return + zero effect"
    if bg is not None and getattr(bg, "tasks", None):
        try:
            await bg()
        except Exception:  # noqa: BLE001 — a failed background task shouldn't turn an already-successful main write into an error
            log.exception("[agent-admin] background tasks failed after %s", getattr(fn, "__name__", fn))
    return out


def route_failed(out: Any) -> bool:
    """Whether call_route's return is a structured failure (rather than a business result)."""
    return isinstance(out, dict) and out.get("ok") is False
