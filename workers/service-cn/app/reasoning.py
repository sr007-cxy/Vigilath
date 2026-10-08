"""Chain-of-thought capture (2026-09-16): the engine's "deep thinking / thinking process / referenced N sources" part, captured separately from the body.

Users asked for it to be captured for all engines, and hit detection counts it (telemetry side detect_hit_full). Each site renders this part
as a **collapsible block**: the title row is clickable (「已深度思考(用时 12 秒)」 ("Thought deeply (12 s)"), 「共参考 34 篇资料」 ("34 sources referenced")),
and the body is collapsed by default - innerText cannot read display:none content, so expand it first, then read.

Selectors have three layers: built-in defaults <- central config (engine_selectors, backend /engine-selectors-config,
keys reasoning_scope / reasoning_sels / reasoning_toggle_sels). When a site redesigns, just change the config,
without code changes and redeploying. Measured sources:
  · wenxin (chat.baidu.com, measured on the test host 2026-09-16): inside the last div.conversation-flow-answer-container,
    .ai-entry-block.ai-thinking-steps, title .thinking-steps-title-extra 「共参考 N 篇资料」 ("N sources referenced"),
    expandable header [class*='could-expand']; in smartMode it contains retrieval steps/source lists, with deep thinking on it is reasoning text.
  · qwen / doubao: no measured samples yet (the test host could not get accounts + sliders); below are guesses based on the sites'
    usual naming (think / reasoning / thought); if not captured, return None without affecting the body.

Return value convention (same as EngineResult.reasoning): None = this block was not found on the page; "" = found
but empty; anything else is the thinking text (with the title row removed).
"""
from __future__ import annotations

from typing import Optional

from . import engine_selectors

_DEFAULTS: dict[str, dict] = {
    "wenxin": {
        "scope": "div.conversation-flow-answer-container",
        "sels": [".ai-thinking-steps", "[class*='thinking-steps']"],
        "toggle_sels": [".ai-thinking-steps [class*='could-expand']",
                        ".ai-thinking-steps header"],
        "strip_prefix": ["共参考", "已完成思考", "已深度思考", "思考过程", "深度思考"],
    },
    "qwen": {
        "scope": "[data-chat-answers-wrap]",
        "sels": ["[class*='think']", "[class*='reasoning']", "[class*='thought']",
                 "[class*='deep-search']"],
        "toggle_sels": ["[class*='think'] [class*='header']", "[class*='think'] [class*='title']",
                        "[class*='reasoning'] [class*='header']"],
        "strip_prefix": ["已深度思考", "深度思考", "思考过程", "已完成思考", "已搜索", "搜索"],
    },
    "doubao": {
        "scope": "[data-testid='receive_message'], [data-testid*='message']",
        "sels": ["[data-testid*='think']", "[class*='think']", "[class*='reasoning']",
                 "[data-testid*='reason']"],
        "toggle_sels": ["[data-testid*='think'] button", "[data-testid*='think'] [class*='header']",
                        "[class*='think'] [class*='header']"],
        "strip_prefix": ["已深度思考", "深度思考", "思考过程", "已完成思考", "已搜索"],
    },
}
_DEFAULTS["doubao_mobile"] = _DEFAULTS["doubao"]

# Executed in the page: within the **last** match of scope (the latest answer round), expand the collapsible header first, then read the container text.
# Pure DOM operations in the browser, independent of site JS; a failed expand does not throw, unreadable returns null.
_JS = r"""
async ([scopeSel, sels, toggleSels, stripPrefixes]) => {
  const vis = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  let root = document;
  if (scopeSel) {
    const scopes = document.querySelectorAll(scopeSel);
    if (scopes.length) root = scopes[scopes.length - 1];
  }
  const pick = (r, list) => {
    for (const s of list) {
      let els; try { els = r.querySelectorAll(s); } catch (e) { continue; }
      if (els.length) return els[els.length - 1];
    }
    return null;
  };
  let box = pick(root, sels);
  if (!box) return {found: false, text: null};
  // 折叠的先点开:点头部;点完等一小会儿让它渲染
  const before = (box.innerText || "").trim().length;
  for (const s of toggleSels) {
    let t; try { t = box.querySelector(s) || root.querySelector(s); } catch (e) { continue; }
    if (t && vis(t)) {
      try { t.click(); } catch (e) {}
      await new Promise((r) => setTimeout(r, 600));
      const after = (box.innerText || "").trim().length;
      if (after > before) break;
      // 点反了(本来是开的被收起)→ 再点一次复原
      if (after < before) { try { t.click(); } catch (e) {} await new Promise((r) => setTimeout(r, 400)); }
    }
  }
  box = pick(root, sels) || box;
  let text = (box.innerText || "").replace(/\r/g, "").trim();
  // 去掉标题行(「已深度思考(用时 8 秒)」「共参考 34 篇资料」这种)
  const lines = text.split("\n");
  while (lines.length && stripPrefixes.some((p) => lines[0].trim().startsWith(p)) && lines[0].trim().length < 40) lines.shift();
  text = lines.join("\n").trim();
  return {found: true, text};
}
"""


def config_for(engine: str) -> dict:
    base = dict(_DEFAULTS.get(engine) or _DEFAULTS.get(engine.split("_")[0], {}) or {})
    try:
        central = engine_selectors.get(engine) or {}
    except Exception:  # noqa: BLE001
        central = {}
    if central.get("reasoning_scope"):
        base["scope"] = central["reasoning_scope"]
    if central.get("reasoning_sels"):
        base["sels"] = list(central["reasoning_sels"])
    if central.get("reasoning_toggle_sels"):
        base["toggle_sels"] = list(central["reasoning_toggle_sels"])
    return base


async def extract_reasoning(page, engine: str, *, timeout_ms: int = 8000) -> Optional[str]:
    """Read the chain of thought of the latest answer round on the current page. Any exception is swallowed and returns None - this is extra info,
    and must never cause the already obtained body to be lost."""
    cfg = config_for(engine)
    if not cfg.get("sels"):
        return None
    try:
        import asyncio
        r = await asyncio.wait_for(
            page.evaluate(_JS, [cfg.get("scope"), list(cfg["sels"]),
                                list(cfg.get("toggle_sels") or []),
                                list(cfg.get("strip_prefix") or [])]),
            timeout=timeout_ms / 1000.0,
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(r, dict) or not r.get("found"):
        return None
    t = r.get("text")
    return (t or "").strip() if t is not None else ""
