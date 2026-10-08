"""Structural gate for the assistant-ui token namespace (frontend files, but the gate lives here -- this pytest suite is what runs before deploy).

Elements is shadcn source copied into the repo and consumes names like `--background` / `--muted` by default.
Running `npx shadcn init` directly would overwrite same-named keys in `tailwind.config.js`; `muted` here is a
**text color** while in shadcn it's a **background color** -- the site's 1374 `text-muted` usages would silently get a background color as text color,
no error, no crash, just grayed text site-wide. This pins the isolation.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_frontend_aui_tokens.py -q
"""
from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[3] / "frontend"
CONFIG = (FRONTEND / "tailwind.config.js").read_text(encoding="utf-8")
CSS = (FRONTEND / "src" / "index.css").read_text(encoding="utf-8")

# Frozen: the site's existing semantic color keys. Losing even one may silently recolor thousands of classNames.
LEGACY_COLOR_KEYS = {
    "border", "border-strong", "card", "tertiary", "surface", "surface-hover",
    "accent-primary", "accent-secondary", "muted", "primary", "secondary",
}


def _color_keys() -> set[str]:
    block = CONFIG[CONFIG.index("colors: {"):]
    return set(re.findall(r"^\s{8}'?([a-z][a-z0-9-]*)'?:", block, re.M))


def test_legacy_color_keys_untouched():
    """shadcn init would grab these names. If any is missing, someone let it."""
    assert LEGACY_COLOR_KEYS <= _color_keys(), f"既有色板 key 被动过:{sorted(LEGACY_COLOR_KEYS - _color_keys())}"


def test_muted_is_still_a_text_color():
    """The worst one: muted is a text color here; overridden with shadcn semantics, site-wide text turns into the background color."""
    assert "muted: 'var(--text-muted)'" in CONFIG


@pytest.mark.parametrize("key", ["aui-background", "aui-foreground", "aui-card", "aui-primary",
                                 "aui-muted", "aui-muted-foreground", "aui-border", "aui-ring"])
def test_aui_palette_present(key):
    assert f"'{key}': 'var(--{key})'" in CONFIG


def test_aui_keys_only_reference_aui_vars():
    """aui-* may only point to --aui-*; pointing at site-wide variables welds the two palettes back together."""
    for key, var in re.findall(r"'(aui-[a-z-]+)': 'var\((--[a-z-]+)\)'", CONFIG):
        assert var == f"--{key}", f"{key} 指向了 {var}"


def test_aui_vars_defined_on_body_not_root():
    """If defined on :root, var() resolves to peec's values at :root and doesn't follow theme switches (hit on 2026-08-23)."""
    body_rule = CSS[CSS.index("\n  body {"):]
    body_rule = body_rule[:body_rule.index("\n  }")]
    assert "--aui-card: var(--bg-card)" in body_rule
    root_rule = CSS[CSS.index(":root {"):CSS.index("\n  body {")]
    assert "--aui-" not in root_rule, "aui token 跑回 :root 了,主题切换会失效"


def test_dark_overrides_primary_foreground():
    """In dark mode --accent-primary is neon cyan, white text doesn't hold up on it, so it must be flipped dark separately."""
    dark = CSS[CSS.index('body[data-theme="dark"] {'):]
    assert "--aui-primary-foreground:" in dark[:dark.index("\n  }")]


# --- Copied-in Elements components ---

AUI_DIR = FRONTEND / "src" / "components" / "aui"
VENDORED = sorted(p for p in AUI_DIR.glob("*.ts*")) if AUI_DIR.exists() else []


def test_vendored_components_exist():
    assert VENDORED, "components/aui/ 空了 —— Elements 组件被删了?"


def _code_only(path: pathlib.Path) -> str:
    """Comments inevitably mention `bg-background` / `dark:` for clarity; strip them before scanning."""
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


@pytest.mark.parametrize("path", VENDORED, ids=lambda p: p.name)
def test_vendored_uses_no_raw_shadcn_tokens(path):
    """Copied components must switch to aui-*; leaving bg-background / text-foreground requires global tokens,
    which we deliberately don't define -- leaving them renders as transparent/inherited color, silent and hard to track down."""
    raw = re.findall(r"\b(?:bg|text|border|ring)-(background|foreground|card|popover|primary|secondary|muted|accent|destructive|border|input|ring)\b", _code_only(path))
    assert not raw, f"{path.name} 里还有未改写的 shadcn token:{sorted(set(raw))}"


@pytest.mark.parametrize("path", VENDORED, ids=lambda p: p.name)
def test_vendored_has_no_dark_variant(path):
    """The site theme is body[data-theme=dark], while Tailwind's default darkMode is prefers-color-scheme --
    `dark:` would follow the system, not the site theme. --aui-* already flips per theme, so no dark: variants are needed."""
    assert "dark:" not in _code_only(path), f"{path.name} 用了 dark: 变体"


# --- On by default + blast radius ---

WORKBENCH = (FRONTEND / "src" / "pages" / "SalesAgent" / "SalesAgentWorkbench.tsx").read_text(encoding="utf-8")
THREAD = (FRONTEND / "src" / "components" / "aui" / "SalesAgentThread.tsx").read_text(encoding="utf-8")


def test_new_ui_is_the_default():
    """The new UI is on by default. That function is kept only as an emergency off switch, not a gradual-rollout toggle."""
    assert "return window.localStorage.getItem('sales_agent_aui_shell') !== '0'" in THREAD


def test_kill_switch_still_reachable():
    """When things break it must be switchable off without redeploying -- both env and localStorage paths must exist."""
    assert "VITE_SALES_AGENT_AUI_SHELL === '0'" in THREAD


def test_other_pages_untouched():
    """Site-wide palette and CSS variables are append-only -- changing them would affect home, reports, sentiment monitoring, admin.

    This is the hard constraint of "touch only the sales-agent page", not a style matter.
    """
    assert "muted: 'var(--text-muted)'" in CONFIG
    assert "--text-muted: #9ca3af" in CSS       # peec default untouched
    assert "--bg-card: #ffffff" in CSS


def test_button_constants_stay_page_local():
    """primaryBtn / ghostBtn must stay inside the workbench file. Moving them to a shared module would affect other pages."""
    assert "const primaryBtn: CSSProperties" in WORKBENCH
    assert "export const primaryBtn" not in WORKBENCH


# --- Tool names must not leak to users ---

def test_unknown_tool_never_shows_its_key():
    """Unrecognized tool names must use the Chinese fallback label. On 2026-08-23 production once exposed
    `sales_agent_seller_profile_summary` -- prompts.py explicitly forbids exposing tool/function/table names,
    and a fallback that shows the key directly bypasses that rule."""
    assert "TOOL_LABELS[tool] || TOOL_LABEL_FALLBACK" in WORKBENCH
    assert "TOOL_LABELS[tool] || [tool, tool]" not in WORKBENCH


def test_card_pseudo_tools_are_mapped():
    """Cards injected by the deterministic flow carry pseudo tool names, not among the 12 Pydantic AI tools, so they need their own mapping."""
    for pseudo in ("sales_agent_seller_profile_summary", "sales_agent_guidance",
                   "sales_agent_detection_url", "sales_agent_detection_keywords"):
        assert f"{pseudo}: [" in WORKBENCH, f"{pseudo} 没有中文说法"


# --- Two-line layout of recommendation rows ---

def _row_grid_columns() -> list[str]:
    """The four column definitions of recommendation rows, in the source's ternary order:
    readonlyHistory ? (compact : wide) : (compact : wide) -- so the first two have no checkbox, the last two do.

    No longer freezing any set's literal: column width values change with design, and each tweak turns the test red, while what really needs guarding is
    the two invariants "column count == child count" and "last column is max-content".
    """
    m = re.search(r"gridTemplateColumns: readonlyHistory(.*?)gap:", WORKBENCH, re.S)
    assert m, "推荐行的列定义找不到了 —— 结构真变了就连这几条断言一起改"
    cols = re.findall(r"'([^']*(?:px|fr|max-content|auto)[^']*)'", m.group(1))
    assert len(cols) == 4, f"应该是 readonlyHistory × compact 四套,实际 {len(cols)} 套"
    return cols


def _tracks(cols: str) -> list[str]:
    """Split tracks on top-level spaces -- the space inside `minmax(150px, 1fr)` isn't a separator."""
    out: list[str] = []
    depth = 0
    cur = ""
    for ch in cols:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == " " and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def test_row_grid_columns_match_children():
    """Column definitions must match the cell count: checkbox / expand / company name / metrics / buttons.

    Happened twice in production on 2026-08-23: first the column count didn't shrink with the children, the button fell into a 68px fractional track and got squeezed
    into a vertical 「生/成/报/告」 ("generate report"); **you can't see it by reading the code** -- fewer children but columns remain, no error or warning.

    There are now four sets (readonlyHistory x compact). readonlyHistory drops the checkbox; compact replaces the
    "metrics cell" with a single match-score cell -- the latter **replaces** rather than **adds**, so only the checkbox affects column count:
    readonlyHistory has 4 columns, the rest 5.
    """
    readonly, normal = _row_grid_columns()[:2], _row_grid_columns()[2:]
    for cols in readonly:
        assert len(_tracks(cols)) == 4, f"readonlyHistory 没有勾选框,应该 4 列:{cols}"
    for cols in normal:
        assert len(_tracks(cols)) == 5, f"带勾选框应该 5 列:{cols}"


def test_action_column_uses_max_content_not_auto():
    """`auto` falls back to min-content when space is tight -- for a row of buttons that's the single-character width of the narrowest button,
    so it wraps character by character. Only `max-content` guarantees the actual content width.

    All four sets must be guarded: both compact branches were once written as `auto`, and only nowrap on the buttons kept it from recurring.
    """
    for cols in _row_grid_columns():
        assert _tracks(cols)[-1] == "max-content", f"按钮列必须是 max-content:{cols}"
    assert "minmax(92px, .55fr) auto" not in WORKBENCH


def test_metrics_cell_is_stacked():
    """The metrics cell has two internal lines (score / status), the same height as the company name cell -- row height unchanged,
    while status gets the full column width, so long text needs neither truncation nor hover."""
    assert "Metrics cell: two lines inside, same height as the company name cell" in WORKBENCH


def test_action_container_cannot_shrink():
    """Fallback: even if column widths are changed later, buttons must not go vertical."""
    assert "flexShrink: 0, whiteSpace: 'nowrap' }}>" in WORKBENCH
