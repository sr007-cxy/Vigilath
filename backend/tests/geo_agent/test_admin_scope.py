"""Mechanism-level regression gate for the ops console (admin line) -- no LLM, no DB, milliseconds, runs in CI.

Everything asserted here is **mechanism**, not behavior: even if the model wants to cross the line, switch projects or self-confirm,
the toolset simply doesn't have that capability / the signature simply doesn't have that parameter -> it can't reach.
Prompts get pinned by history and ignored by models; these assertions don't.

Run (with the agent venv; the import chain pulls in pydantic-ai):
    venv/bin/python -m pytest tests/geo_agent/test_admin_scope.py -q   # cwd = backend/
"""
from __future__ import annotations

import inspect
import re

import pytest

from agents.geo.admin_tools import ADMIN_PAID_TOOLS, ADMIN_TOOLS
from agents.geo.agent import (
    _GEO_WRITE, _SENTIMENT_READ, _SENTIMENT_WRITE, _line_tools,
)
from agents.geo.scope import resolve_scope


def _names(tools) -> set[str]:
    return {getattr(t, "__name__", str(t)) for t in tools}


# Paid tools must pass the same signature / docstring / confirm-parameter assertions too; they just aren't loaded by default
ALL_ADMIN = ADMIN_TOOLS + ADMIN_PAID_TOOLS
_ADMIN_NAMES = _names(ALL_ADMIN)


class _U:
    """Minimal user stand-in: resolve_scope only reads role / is_admin."""

    def __init__(self, role: str):
        self.role = role
        self.is_admin = role == "admin"


@pytest.fixture()
def admin_on(monkeypatch):
    monkeypatch.setenv("AGENT_ADMIN_ENABLED", "1")


# ── 1. resolve_scope decision table ─────────────────────────────────────────────
@pytest.mark.parametrize("role", ["user", "tenant", "maintainer"])
@pytest.mark.parametrize("req", ["admin", None, "self"])
@pytest.mark.parametrize("route", ["/workbench/topics/42/edit", "/dashboard"])
def test_non_staff_never_admin(admin_on, role, req, route):
    """Non-staff are always self, no matter what the frontend passes or which route they're on."""
    got = resolve_scope(_U(role), requested_scope=req, topic_id=42,
                        page_context={"route": route})
    assert got == "self", f"role={role} req={req} route={route} 竟然拿到了 admin 线"


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_staff_needs_topic(admin_on, role):
    """No explicit project means no admin line -- the admin line may not fall back to guessing a project."""
    assert resolve_scope(_U(role), requested_scope="admin", topic_id=None,
                         page_context={"route": "/workbench/topics/42/edit"}) == "self"


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_master_switch_off(monkeypatch, role):
    """With the master switch off the whole line disappears (emergency takedown = change one env + restart)."""
    monkeypatch.setenv("AGENT_ADMIN_ENABLED", "0")
    assert resolve_scope(_U(role), requested_scope="admin", topic_id=42,
                         page_context={"route": "/workbench/topics/42/edit"}) == "self"


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_staff_explicit_scope(admin_on, role):
    assert resolve_scope(_U(role), requested_scope="admin", topic_id=42, page_context=None) == "admin"


# The ops console moved from /workbench to /ops on 2026-09-08; old frontends still send /workbench, so both must be recognized.
@pytest.mark.parametrize("prefix", ["/ops", "/workbench"])
def test_route_fallback_requires_matching_topic(admin_on, prefix):
    """When the frontend omits scope, fall back to the route; but the project id in the route must match the passed topic_id."""
    ok = resolve_scope(_U("operator"), requested_scope=None, topic_id=42,
                       page_context={"route": f"{prefix}/topics/42/edit"})
    mismatch = resolve_scope(_U("operator"), requested_scope=None, topic_id=42,
                             page_context={"route": f"{prefix}/topics/99/edit"})
    other = resolve_scope(_U("operator"), requested_scope=None, topic_id=42,
                          page_context={"route": "/dashboard/topics/42/profile"})
    assert (ok, mismatch, other) == ("admin", "self", "self")


# ── 2. Mechanism proof of project locking ───────────────────────────────────────────────
_FORBIDDEN_PARAMS = {"topic_id", "account_id", "user_id", "locked_topic_id", "scope"}


@pytest.mark.parametrize("tool", ALL_ADMIN, ids=lambda t: t.__name__)
def test_admin_tool_cannot_switch_project(tool):
    """Admin tool signatures **must never** contain project/account ids -- if the model can't fill them, it can't switch projects.

    This is the only hard guarantee of "scope = within the currently selected project", more reliable than any prompt.
    """
    params = set(inspect.signature(tool).parameters) - {"ctx"}
    bad = params & _FORBIDDEN_PARAMS
    assert not bad, f"{tool.__name__} 的签名里有 {bad} —— 模型可借此操作别的项目/账号"


@pytest.mark.parametrize("tool", ALL_ADMIN, ids=lambda t: t.__name__)
def test_admin_tool_first_param_is_ctx(tool):
    assert list(inspect.signature(tool).parameters)[0] == "ctx"


@pytest.mark.parametrize("tool", ALL_ADMIN, ids=lambda t: t.__name__)
def test_admin_tool_docstring_prefix(tool):
    """The first sentence of the docstring must carry a 【读】/【写】/【动作】 ("read" / "write" / "action") prefix -- the system prompt's write constraints rely on it."""
    doc = (tool.__doc__ or "").strip()
    assert doc, f"{tool.__name__} 没有 docstring(它就是给模型看的工具说明)"
    # Variants with modifiers are allowed: 【动作·不可逆】【写·对外】【读·核价】 ("action - irreversible", "write - external", "read - price check") -- consistent with the existing style in tools.py
    assert re.match(r"^【(读|写|动作)[^】]*】", doc), (
        f"{tool.__name__} 的 docstring 首句缺 【读】/【写】/【动作】前缀:{doc[:40]!r}"
    )


# Irreversible / money-spending tools must have two-step confirmation. Add names here when batches 2-4 add tools; a missing one turns red.
_MUST_CONFIRM = {
    "admin_confirm_plan", "admin_batch_generate_docs", "admin_approve_doc",
    "admin_reject_doc", "admin_regenerate_doc", "admin_create_publish_tasks",
    "admin_publish_paid", "admin_reorder_plan", "admin_add_plan_item",
    "admin_remove_plan_items", "admin_delete_doc", "admin_cancel_publish_task",
    "admin_publish_doc",
}


# Tools in the irreversible/paid group **allowed** to lack two-step confirmation -- only side-effect-free or reversible ones, each with a written reason.
_CONFIRM_EXEMPT = {
    "admin_publish_preview": "只核价,不下单、不改数据",
    "admin_auto_select_media": "只推草稿建 planned 任务,不扣费,任务可取消",
}


def test_every_act_tool_is_gated_or_explicitly_exempt():
    """Tools in the irreversible / paid groups either carry confirm_token or are in the exemption list with a reason.

    Stricter than the name-maintained _MUST_CONFIRM: if a tool is later added to the ACT/PAID group without confirmation, this turns red immediately,
    without anyone needing to remember to sync a list.
    """
    from agents.geo.admin_tools import ADMIN_ACT_TOOLS, ADMIN_PAID_TOOLS

    ungated = [
        t.__name__ for t in (ADMIN_ACT_TOOLS + ADMIN_PAID_TOOLS)
        if "confirm_token" not in inspect.signature(t).parameters
        and t.__name__ not in _CONFIRM_EXEMPT
    ]
    assert not ungated, (
        f"这些不可逆/付费工具没有两步确认,也没在 _CONFIRM_EXEMPT 里说明理由:{ungated}"
    )


def test_confirmable_tools_call_guard_with_their_own_name():
    """Any tool with confirm_token in its signature must call confirm.guard with **its own name** in the function body.

    Passing the wrong tool name binds the HMAC ticket to a different action: either it never verifies (the tool is dead),
    or two actions' tickets become interchangeable -- the latter means two-step confirmation is bypassed. Neither raises an error;
    they only surface during some real operation.

    Tools without confirm_token are handled by _CONFIRM_EXEMPT; no second list is maintained here.
    """
    import pathlib

    from agents.geo.admin_tools import ADMIN_ACT_TOOLS, ADMIN_PAID_TOOLS

    # Derive the path from the module under test itself (the test directory layout differs across branches; hardcoding parents[N] goes wrong)
    import agents.geo.admin_tools as _mod

    src = pathlib.Path(_mod.__file__).resolve().read_text(encoding="utf-8")
    missing = [
        fn.__name__ for fn in ADMIN_ACT_TOOLS + ADMIN_PAID_TOOLS
        if "confirm_token" in inspect.signature(fn).parameters
        and f'guard(ctx.deps, confirm_token, tool="{fn.__name__}"' not in src
    ]
    assert not missing, f"这些工具没用自己的名字调 confirm.guard:{missing}"


def test_write_tools_are_not_in_read_group():
    """Reversible-write / irreversible-write tools must not mix into the read-only group -- the read-only group loads even when can_write=False."""
    from agents.geo.admin_tools import (
        ADMIN_ACT_TOOLS, ADMIN_READ_TOOLS, ADMIN_WRITE_TOOLS,
    )

    read = _names(ADMIN_READ_TOOLS)
    leaked = read & (_names(ADMIN_WRITE_TOOLS) | _names(ADMIN_ACT_TOOLS))
    assert not leaked, f"写工具混进只读组:{leaked}"
    for t in ADMIN_READ_TOOLS:
        assert (t.__doc__ or "").strip().startswith("【读"), (
            f"{t.__name__} 在只读组里,但 docstring 不是【读…】开头"
        )


def test_must_confirm_names_all_exist():
    """Tools in the list must actually exist -- otherwise after a rename this gate silently stops working."""
    have = {t.__name__ for t in ALL_ADMIN}
    missing = sorted(_MUST_CONFIRM - have)
    assert not missing, f"_MUST_CONFIRM 里这些工具不存在(改名了?):{missing}"


def test_irreversible_tools_have_confirm_token():
    by_name = {t.__name__: t for t in ALL_ADMIN}
    for name in sorted(_MUST_CONFIRM & set(by_name)):
        assert "confirm_token" in inspect.signature(by_name[name]).parameters, (
            f"{name} 是不可逆/花钱动作,必须有 confirm_token 参数走两步确认"
        )


# ── 3. Cross-line isolation (both directions) ────────────────────────────────────────────────
def test_admin_line_excludes_customer_write_tools():
    """The ops console line contains no customer-side write tools.

    _account_topic is already scope-aware; if these tools were loaded into the admin line, they'd write customer projects with customer semantics --
    _review_doc doesn't trigger publish_preset or send notifications, so the draft passes but not a single placement task is created, and the page shows nothing wrong.
    """
    admin = _names(_line_tools("admin", can_write=True))
    assert not (admin & _names(_GEO_WRITE)), admin & _names(_GEO_WRITE)
    assert not (admin & _names(_SENTIMENT_WRITE)), admin & _names(_SENTIMENT_WRITE)
    assert not (admin & _names(_SENTIMENT_READ)), admin & _names(_SENTIMENT_READ)


@pytest.mark.parametrize("line", ["geo", "sentiment", "both"])
def test_customer_lines_exclude_admin_tools(line):
    """Conversely: the customer line can never reach ops console tools."""
    got = _names(_line_tools(line, can_write=True, include_publish=True)) & _ADMIN_NAMES
    assert not got, f"{line} 线里混进了运营台工具:{got}"


def test_admin_line_has_reused_readonly_tools():
    """Reused customer-side read-only tools must be present -- otherwise the ops console can't see report center data."""
    admin = _names(_line_tools("admin", can_write=True))
    for t in ("get_report_overview", "get_period_report", "get_publish_status"):
        assert t in admin, f"{t} 不在运营台线工具集里"


def test_embed_agent_downgrades_admin_line(monkeypatch):
    """An external embed token passing line="admin" is downgraded to geo -- outsiders can never reach the ops console.

    Intercept _make_agent to take only the toolset, without really building an Agent (that needs DEEPSEEK_API_KEY, which CI doesn't have).
    """
    from agents.geo import agent as A

    captured: list = []
    monkeypatch.setattr(A, "_make_agent", lambda tools, *a, **kw: captured.append(tools))
    A.build_embed_agent.cache_clear()
    try:
        A.build_embed_agent(True, "chat", "admin")
    finally:
        A.build_embed_agent.cache_clear()

    assert captured, "_make_agent 没被调用"
    got = _names(captured[0]) & _ADMIN_NAMES
    assert not got, f"对外 embed agent 够到了运营台工具:{got}"


# ── 4. Paid external actions loaded by env ──────────────────────────────────────────────
def test_paid_tools_hidden_when_guard_off(monkeypatch):
    """With the guardrail off, paid tools don't even load their schema; the model can't see them, so it won't mention them."""
    monkeypatch.setenv("AGENT_ALLOW_PAID_PUBLISH", "0")
    assert "admin_publish_paid" not in _names(_line_tools("admin", can_write=True))
