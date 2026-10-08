"""Structural gate for the agent's right-side dock (frontend files; the gate lives here -- this pytest suite runs before deploy).

Guards two things prone to regressing:
  1. Making room (--agent-dock-w) **must happen in exactly one place**. Originally only WorkbenchLayout read it, so on other pages
     the chat window covered the content; writing it in two places doubles it and squeezes the content area by twice the width.
  2. The dock criterion is "has this page registered operable actions", not a path prefix. The value of this window is
     talk on the right, the left changes accordingly -- only operable pages are worth side-by-side; hardcoding paths would miss new pages.

Run: venv/bin/python -m pytest tests/geo_agent/test_frontend_agent_dock.py -q   # cwd = backend/
"""
from __future__ import annotations

import pathlib
import re

import pytest

def _frontend_src() -> pathlib.Path:
    """Walk up to find the repo root containing frontend/src.

    ★ Don't hardcode parents[N]: the test directory layout differs across branches (develop has backend/tests/geo_agent/,
      the release branch still has backend/agents/geo/eval/); a hardcoded depth computes a nonexistent path,
      raising FileNotFoundError rather than an assertion failure -- sending troubleshooting completely astray (we hit this once).
    """
    for d in pathlib.Path(__file__).resolve().parents:
        if (d / "frontend" / "src").is_dir():
            return d / "frontend" / "src"
    raise AssertionError("向上找不到 frontend/src")


FRONTEND = _frontend_src()
WIDGET = FRONTEND / "components" / "AgentChat" / "AgentChatWidget.tsx"
BUS = FRONTEND / "components" / "AgentChat" / "agentActions.tsx"
APP = FRONTEND / "App.tsx"


def _read(p: pathlib.Path) -> str:
    assert p.exists(), f"文件不在了:{p}"
    return p.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Strip comments -- comments inevitably mention variable names to explain things."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


# ── 1. Making room happens in one place only ─────────────────────────────────────────────────────
def test_dock_spacing_is_applied_exactly_once():
    hits = []
    for p in FRONTEND.rglob("*.tsx"):
        if p.name == "AgentChatWidget.tsx":      # it sets the value; it isn't the one making room
            continue
        if "var(--agent-dock-w" in _code_only(_read(p)):
            hits.append(p.name)
    assert hits == ["App.tsx"], f"让位点应当只有站点外壳 App.tsx,实际:{hits}"


def test_widget_still_sets_the_var():
    assert "--agent-dock-w" in _code_only(_read(WIDGET)), "没人设这个变量了,让位永远是 0"


def test_spacing_defaults_to_zero():
    """Must return to 0 when collapsed -- a hardcoded width makes users without the chat open lose a column for nothing."""
    assert "var(--agent-dock-w, 0px)" in _read(APP)


# ── 2. Dock criterion ────────────────────────────────────────────────────────
def test_docked_is_capability_based_not_path_based():
    code = _code_only(_read(WIDGET))
    assert "useHasAgentActions()" in code, "dock 判据没有走「本页有没有动作」"
    assert "pathname.startsWith('/workbench')" not in code, \
        "又按路径前缀写死了 —— 新页面登记了动作也享受不到"


def test_user_can_override_the_default():
    code = _code_only(_read(WIDGET))
    assert "agent_dock" in code, "没有用户偏好开关"
    for v in ("'dock'", "'float'", "'auto'"):
        assert v in code, f"偏好缺少 {v} 档"


def test_localstorage_read_is_guarded():
    """Reading localStorage in private mode throws; it must not take down the whole chat window."""
    code = _read(WIDGET)
    i = code.index("agent_dock")
    assert "catch" in code[max(0, i - 400): i + 400]


# ── 3. Bus subscription ─────────────────────────────────────────────────────────
def test_bus_exposes_a_cheap_liveness_probe():
    """getSnapshot is called frequently by React; composition and clipSnapshot must not run inside it."""
    code = _code_only(_read(BUS))
    assert "hasLive" in code and "useSyncExternalStore" in code
    i = code.index("const hasLive")
    body = code[i:i + 260]
    assert "clipSnapshot" not in body, "hasLive 里跑了 clipSnapshot,每次快照都要付一遍代价"


def test_bus_notifies_on_register_and_unregister():
    code = _code_only(_read(BUS))
    i = code.index("const register")
    body = code[i:code.index("const current", i)]
    assert body.count("notify()") >= 2, "注册/注销至少要各通知一次,否则 dock 态不跟着变"


def test_composition_logic_untouched():
    """Multi-source composition and snapshot clipping were fixed after hitting pitfalls; this batch must not be touched."""
    code = _read(BUS)
    for marker in ("MAX_ACTIONS", "MAX_SNAPSHOT_CHARS", "clipSnapshot", "weight"):
        assert marker in code, f"合成逻辑里的 {marker} 没了"


# ── 4. "Watch it change" feedback ───────────────────────────────────────────────
def test_steps_are_reported_progressively():
    """Progress item by item, not one summary after everything finishes -- reporting only at the end means never seeing the process."""
    code = _code_only(_read(WIDGET))
    assert "'pending'" in code and "'running'" in code, "没有逐步状态机"
    assert "setSteps(list)" in code, "没有先把整串铺出来(用户看不到共几步)"


def test_before_after_is_shown_when_provided():
    code = _read(WIDGET)
    assert "st.before" in code and "st.after" in code, "只报动作名,没报改成什么"


def test_changed_element_is_highlighted():
    """The left side changed but the user is looking right -- without a highlight they have to find it themselves."""
    code = _code_only(_read(WIDGET))
    assert "flashElement" in code
    i = code.index("function flashElement")
    body = code[i:i + 700]
    assert "outline" in body and "border" not in body, "用 border 会改盒模型把布局挤动"


def test_result_type_is_optional():
    """Must keep working even if the executor returns nothing -- zero changes to the 10 registered pages is a precondition."""
    code = _read(BUS)
    assert "export type AgentActionOutcome = void | AgentActionResult;" in code


# ── 5. Dangerous action confirmation ─────────────────────────────────────────────────────
def test_risky_actions_are_confirmed_in_page():
    code = _code_only(_read(WIDGET))
    assert "isRiskyAction(" in code and "uiConfirm(" in code


def test_no_native_confirm():
    """Repo rule: new code must not use native confirm/alert; always go through components/ui."""
    code = _code_only(_read(WIDGET))
    assert "window.confirm" not in code and re.search(r"(?<![.\w])confirm\(", code) is None


def test_self_confirming_pages_are_exempt():
    """★ When the page itself already shows a confirmation, the generic gate must step aside -- confirming twice in a row makes users click the second one blindly,
    which actually weakens the gate."""
    bus = _code_only(_read(BUS))
    assert "selfConfirm" in bus, "没有豁免机制"
    i = bus.index("export function isRiskyAction")
    assert "return false" in bus[i:i + 420], "isRiskyAction 没有对 selfConfirm 提前返回"
    code = _code_only(_read(WIDGET))
    assert "isRiskyAction(a.action, ctx?.selfConfirm)" in code, "widget 没把豁免名单传进去"


@pytest.mark.parametrize("page,action", [
    ("Admin/SourceInsightsPanel.tsx", "replanBySources"),
    ("Admin/PlacementStrategyTab.tsx", "replanByStrategy"),
])
def test_known_self_confirming_actions_declared(page, action):
    """These two have their own uiConfirm in handleRegen / handleReplan, with copy more precise than the generic one."""
    code = _read(FRONTEND / "pages" / page)
    assert f"selfConfirm: ['{action}']" in code


def test_runbatch_is_still_gated():
    """runBatch calls triggerRun directly, and the page side has **no** confirmation at all -- this gate was added for it."""
    bus = _read(BUS)
    assert "'runBatch'" in bus
    shell = _read(FRONTEND / "pages" / "BrandGrowth" / "shell.tsx")
    i = shell.index("runBatch:")
    assert "uiConfirm" not in shell[i:i + 300], \
        "runBatch 自己加了确认?那要么删掉这道通用闸,要么给它声明 selfConfirm"


# ── 6. Docked-state styles must be separate from the floating window ───────────────────────────────────────
#
# The floating window is "a hovering card": 16px rounded corners, a downward shadow, and a large avatar poking halfway above the title bar
# (top:-32, revealed via overflow:visible). The docked state is "a wall on the right", where none of these hold:
#   - the container is already square; rounded title bar/input area -> the four corners show the background
#   - the panel sits at top:0, so the protruding half avatar is clipped by the viewport -- leaving half a face
#   - a downward shadow looks like floating above the page rather than embedded on the right

def _panel_block() -> str:
    code = _read(WIDGET)
    return code[code.index("{/* Chat panel"):]


@pytest.mark.parametrize("prop", ["borderTopLeftRadius: 16", "borderBottomLeftRadius: 16"])
def test_panel_chrome_radius_is_conditional_on_dock(prop):
    """The 16px rounded corners of the panel shell (title bar / input area) must hang off the dockPanel condition.

    Only match `: 16` -- message bubbles also have borderBottomLeftRadius, but that's the shape of the bubble tail
    (`? 4 : 14`), unrelated to docking, and shouldn't be hit by this gate.
    """
    block = _panel_block()
    i = block.index(prop)
    ctx = block[max(0, i - 300): i]
    assert "dockPanel" in ctx, f"{prop} 没有按形态区分,贴边态会露拐角"


def test_avatar_does_not_overflow_when_docked():
    """★ top:-32 may appear only in the floating branch -- the docked panel sits at top:0, and the protruding half would be clipped by the viewport."""
    block = _panel_block()
    i = block.index("top: -32")
    ctx = block[max(0, i - 300): i]
    assert "dockPanel" in ctx, "大头像的溢出定位没有限定在浮窗分支"


def test_shadow_direction_follows_shape():
    """Floating casts downward (a hovering card), docked casts leftward (embedded on the right)."""
    block = _panel_block()
    i = block.index("boxShadow: dockPanel")
    seg = block[i:i + 130]
    assert "-8px 0" in seg and "0 16px" in seg


def test_dock_header_has_no_avatar_gutter():
    """The floating window's 84px left padding makes room for the protruding large avatar; in the docked state the avatar is inline, so keeping it is an odd indent."""
    block = _panel_block()
    i = block.index("9px 84px")
    assert "dockPanel" in block[max(0, i - 260): i]


# ── SSE parsing must live in only one place (2026-08-25) ────────────────────────────────────────
# WecomAgent once kept its own bare fetch that only handled delta -- cards / ui_actions / confirms
# were all dropped. So WeCom users couldn't see the six-metric or competitor structured cards, nor two-step confirmation cards,
# and this kind of "handles fewer event types" regression is completely invisible on the page (text still streams out).
def test_only_one_place_parses_the_agent_sse_stream():
    """Any page connecting directly to /agent/chat must consume cards -- otherwise it's yet another copy of an incomplete parser."""
    src = _frontend_src()
    offenders = []
    for f in src.rglob("*.tsx"):
        text = f.read_text(encoding="utf-8")
        if "'/agent/chat'" not in text and "/agent/chat`" not in text:
            continue
        if "streamAgentChat" in text:
            continue                      # uses the shared implementation, compliant
        if "onCards" in text or "ev.cards" in text or "obj.cards" in text:
            continue                      # parses on its own but at least doesn't drop cards
        offenders.append(str(f.relative_to(src)))
    assert not offenders, (
        f"这些页面自己解析 agent SSE 又没处理 cards:{offenders}。"
        "改用 services/agentApi.streamAgentChat。"
    )


def test_wecom_page_renders_cards():
    """The WeCom page must actually render the cards, not just receive them."""
    text = (_frontend_src() / "pages" / "WecomAgent.tsx").read_text(encoding="utf-8")
    assert "streamAgentChat" in text, "企微页没走共用 SSE 解析"
    assert "CardView" in text, "企微页收了卡片但没渲染"
