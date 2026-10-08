"""Report center PDF export — real screenshots via a backend headless browser.

Frontend DOM-cloning approaches (html2canvas / html-to-image) repeatedly distorted flex/gap, SVG charts,
and Safari foreignObject. Instead, Playwright opens the report page itself,
takes real screenshots of the core region [data-export-root] (the rendering engine is the browser, WYSIWYG),
then slices pages at A4 proportions and assembles them into a PDF.

Dependencies: playwright (already in requirements) + Pillow; the browser reuses the local
~/.cache/ms-playwright cache, falling back to an available executable on version mismatch.
"""
from __future__ import annotations

import asyncio
import glob
import html
import io
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from geo.api.ai_telemetry import _get_topic_or_404, get_db
from geo.api.auth import get_current_user
from geo.models.user import User
from geo.services.access_control import ACTION_READ
from sqlalchemy.orm import Session

router = APIRouter(prefix="/ai-telemetry")
logger = logging.getLogger(__name__)

# Frontend site root: on test host / prod, local nginx port 80 is the frontend; overridable via env (point to vite for local dev)
# The headless browser must **open the report page** before printing, so this must be "a site reachable by the rendering process":
# bare-metal deployment = local nginx (the default suffices); container deployment = frontend service name (e.g. http://frontend:3000) —
# 127.0.0.1 inside the container is the backend itself, with no port 80, so goto fails with ERR_CONNECTION_REFUSED.
_BASE_URL = os.getenv("REPORT_EXPORT_BASE_URL", "http://127.0.0.1")
# At most two screenshot jobs at a time, to keep peaks from overwhelming the machine
_SEM = asyncio.Semaphore(2)

_VIEWPORT_W = 1280
_GOTO_TIMEOUT_MS = 45_000
_SKELETON_TIMEOUT_MS = 15_000
# With scrolling sections expanded the report can be very long (the weekly "this week's response details" can reach 800 rows). Beyond this CSS height
# switch to 1x screenshots: a 2x bitmap would reach hundreds of millions of pixels, not worth the memory for PIL page assembly.
_CSS_SCALE_ABOVE_PX = 6000


class ExportPdfIn(BaseModel):
    topic_id: int
    path: str                      # Frontend route, e.g. /reports?topic=12 or /reports/daily/2026-07-14?topic=12
    lang: str = "zh"               # localStorage i18nextLng
    theme: str = ""                # localStorage theme; empty = default


def _chromium_executable() -> Optional[str]:
    """When the browser build expected by venv playwright is missing, fall back to one available in the ms-playwright cache."""
    home = os.path.expanduser("~")
    for pat in (
        f"{home}/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell",
        f"{home}/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    ):
        hits = sorted(glob.glob(pat), reverse=True)
        if hits:
            return hits[0]
    return None


async def _launch(p):
    args = ["--no-sandbox", "--no-proxy-server"]  # Only accesses the local frontend, bypassing the egress proxy env
    try:
        return await p.chromium.launch(args=args)
    except Exception:
        exe = _chromium_executable()
        if not exe:
            raise
        return await p.chromium.launch(executable_path=exe, args=args)


def _stored_user(user) -> str:
    """The frontend AuthContext only trusts localStorage.user for roles (does not query /me). If the screenshot context only seeded
    the token, staff viewing a customer topic would get isStaff=false → useShellState would not resolve others' topics
    → the page never renders [data-export-root] → the screenshot times out after 20s. So the profile is seeded too."""
    if user is None:
        return ""
    profile = {
        "id": getattr(user, "id", None),
        "email": getattr(user, "email", ""),
        "name": getattr(user, "name", None),
        "is_admin": bool(getattr(user, "is_admin", False)),
        "role": getattr(user, "role", None) or ("admin" if getattr(user, "is_admin", False) else "user"),
    }
    return f"localStorage.setItem('user', {json.dumps(json.dumps(profile, ensure_ascii=False))});"


# Page preparation before screenshots: hide things that are not part of the report + expand scroll areas + measure card boundaries.
# Returns {height, width, blocks:[{top,bottom}]}; coordinates are CSS px relative to the top of root.
_PREPARE_JS = """() => {
    const root = document.querySelector('[data-export-root]');
    if (!root) return { height: 0, width: 0, blocks: [] };

    // 1) 报告区之外的一切(站点页头、页脚备案信息、悬浮客服气泡)整个拿掉。
    //    用 display:none 而不是 visibility:hidden —— 后者只是不可见,元素仍占位,
    //    页脚照样会落进 root 的截图矩形里(PDF 末页出现备案号就是这么来的)。
    //    跳过 root 的祖先链,否则把报告自己也藏了。
    document.querySelectorAll('body *').forEach(el => {
        if (root.contains(el) || el.contains(root)) return;
        el.style.display = 'none';
    });

    // 2) 报告区**之内**的交互控件:导出/重新诊断按钮、右侧章节导航。
    //    .print-hidden 只在 @media print 下生效,截图走的是 screen media,
    //    所以这里必须显式藏掉;顺带认 [data-export-hide] 这个既有钩子。
    root.querySelectorAll('.print-hidden, [data-export-hide]').forEach(el => {
        el.style.display = 'none';
    });
    // 兜底:report 内如果还有 fixed/sticky 的浮层,同样不该进静态图
    root.querySelectorAll('*').forEach(el => {
        const pos = getComputedStyle(el).position;
        if (pos === 'fixed' || pos === 'sticky') el.style.display = 'none';
    });

    // 3) 带滚动条的区块(周报「本周应答明细」、日报持平行等)在静态图里
    //    只会露出可视的那一屏,导出前展开成全高,否则 PDF 天生缺一大截
    root.querySelectorAll('*').forEach(el => {
        const oy = getComputedStyle(el).overflowY;
        if (oy === 'auto' || oy === 'scroll') {
            el.style.maxHeight = 'none';
            el.style.height = 'auto';
            el.style.overflowY = 'visible';
        }
    });

    // 4) 量出「不该被切开」的块,分两级给分页用:
    //    chapters = root 的直接子节点(整章),分页优先切在章与章之间;
    //    blocks   = 章内部的卡片,只有整章高过一页、章级切点用不上时才启用。
    //    只用细粒度会把「02 五维评分」这类章节标题单独留在上一页页底(孤行),
    //    只用章级则遇到超长章节(二十五类逐项体检)退化成硬切 —— 所以两级都要。
    const rect = root.getBoundingClientRect();
    const seen = new Set();
    const collect = (el, sink) => {
        const r = el.getBoundingClientRect();
        if (r.height < 1) return;                       // 隐藏或塌陷的不算
        const bottom = r.bottom - rect.top;
        if (seen.has(bottom)) return;
        seen.add(bottom);
        sink.push({ top: r.top - rect.top, bottom });
    };
    const chapters = [], blocks = [];
    root.querySelectorAll(':scope > *').forEach(sec => {
        if (getComputedStyle(sec).display === 'none') return;
        collect(sec, chapters);
        sec.querySelectorAll(':scope > *, :scope > * > *').forEach(child => {
            if (getComputedStyle(child).display === 'none') return;
            collect(child, blocks);
        });
    });
    return { height: rect.height, width: rect.width, chapters, blocks };
}"""

# ── Print layout pipeline (vector PDF) ────────────────────────────────────────────
# The screenshot pipeline photographs the page into a bitmap and slices it into A4, with two inherent flaws: text is a bitmap (blurry when zoomed,
# not selectable/searchable), and to avoid cutting cards pages must break early, leaving large blank areas at the bottom.
# This uses the browser's own print engine instead: content is re-laid out for paper, pagination is decided by the layout engine,
# later content naturally flows up to fill pages, and the output is a vector PDF.
#
# Maintenance note: the CSS below is coupled to the diagnosis report's DOM structure (section.card + in-chapter .space-y-*).
# When the report page layout changes significantly, re-check this as well.
_PDF_CSS = """
@page { size: A4; margin: 12mm 0 14mm 0; }
html, body, #root, #root > * {
  background:#fff !important; margin:0 !important; padding:0 !important;
}
/* 装饰性网格是 fixed 伪元素,JS 关不掉,只能在 CSS 里灭 */
.grid-background::before, .grid-background::after { display:none !important; }

/* 报告按 1040px 排版(> lg 断点 1024px,保住桌面三栏),再整体缩到纸张宽度。
   直接让它按 A4 宽排会触发移动端断点,三栏塌成一栏。 */
[data-export-root] {
  width:1040px !important; max-width:none !important; zoom:0.68;
  margin:0 auto !important; padding:0 !important; background:transparent !important;
}

/* 卡片脱壳:网页的边框+阴影+圆角一旦跨页就断得很难看,打印改成文档流,
   章与章之间用一条细线分隔 */
[data-export-root] > section.card {
  border:0 !important; box-shadow:none !important; border-radius:0 !important;
  background:transparent !important;
  padding:0 0 7mm 0 !important; margin:0 0 7mm 0 !important;
  border-bottom:1px solid #e5e7eb !important;
  break-inside:auto;                       /* 整章允许跨页,否则页底大片空白 */
}
[data-export-root] > section.card:last-of-type { border-bottom:0 !important; }
/* 抬头保留封面观感 */
[data-export-root] > section:first-of-type {
  border-bottom:0 !important; border-radius:10px !important; padding:7mm !important;
}
/* 章节标题不许跟正文分家 */
[data-export-root] > section.card > div:first-child,
[data-export-root] > section.card > div:first-child + p { break-after:avoid; }
h1,h2,h3 { break-after:avoid; }
/* 真正要保护的是章内小节:单张卡片 / 图表 / 一条发现不许被切开 */
[data-export-root] section > div > * { break-inside:avoid; }
/* …但「不许切开」用在整块大内容上就会把它整体推到下一页,页底留一大片白
   (实测第 4 页只填了 45%)。凡是装着表格、长列表、卡片网格的块,一律放开跨页,
   让排版引擎自己填满页面;表头会随 thead 重复,读者不会丢上下文。 */
[data-export-root] section > div:has(table),
[data-export-root] section > div:has(> .grid),
[data-export-root] section > div:has(> .space-y-1),
[data-export-root] section > div:has(> .space-y-2) { break-inside:auto !important; }
/* 网格里的单张卡片仍然是原子单位 */
[data-export-root] .grid > * { break-inside:avoid; }
thead { display:table-header-group; }      /* 长表格跨页时表头重复 */
tr, img, svg { break-inside:avoid; }

/* 版面密度:网页那套间距在纸上偏松,章间距和卡片内边距各收一档 */
[data-export-root] .space-y-6 > * + * { margin-top:14px !important; }
[data-export-root] .space-y-4 > * + * { margin-top:10px !important; }
[data-export-root] table td, [data-export-root] table th { padding:4px 8px !important; }
/* 口径说明这类纯文字块压紧,别一条一行占半页 */
[data-export-root] p.text-xs { line-height:1.5 !important; margin:1px 0 !important; }
"""

# Preparation before printing. The first two steps share a source with the screenshot pipeline (hide the page shell and interactive controls, expand scroll areas);
# the third is print-specific: page.pdf() shrinks the layout viewport to paper width, which breaks Tailwind's
# lg:/sm: breakpoints (the radar group's lg:grid-cols-[300px_auto_1fr] collapses into one column),
# and emulate_media cannot save it. While the viewport is still wide, pin the computed layout as inline styles.
_PDF_PREPARE_JS = """() => {
    const root = document.querySelector('[data-export-root]');
    if (!root) return false;
    document.querySelectorAll('body *').forEach(el => {
        if (root.contains(el) || el.contains(root)) return;
        el.style.display = 'none';
    });
    root.querySelectorAll('.print-hidden, [data-export-hide]').forEach(el => {
        el.style.display = 'none';
    });
    root.querySelectorAll('*').forEach(el => {
        const s = getComputedStyle(el);
        if (s.position === 'fixed' || s.position === 'sticky') el.style.display = 'none';
        if (s.overflowY === 'auto' || s.overflowY === 'scroll') {
            el.style.maxHeight = 'none';
            el.style.height = 'auto';
            el.style.overflowY = 'visible';
        }
    });
    // 布局快照 —— 必须在上面的宽度 CSS 已生效之后跑,否则钉住的是旧宽度下的列宽
    root.querySelectorAll('*').forEach(el => {
        const s = getComputedStyle(el);
        if (s.display === 'grid' || s.display === 'inline-grid') {
            el.style.gridTemplateColumns = s.gridTemplateColumns;
            el.style.gridTemplateRows = s.gridTemplateRows;
        }
        if (s.display === 'flex' || s.display === 'inline-flex') {
            el.style.flexDirection = s.flexDirection;
            el.style.flexWrap = s.flexWrap;
        }
    });
    return true;
}"""


def _footer_html(title: str) -> str:
    """Footer: Chromium spreads the left/center/right parts apart, so center them on one line to keep it together."""
    safe = html.escape(title or "")
    return (
        '<div style="width:100%;font-size:7.5pt;color:#94a3b8;font-family:sans-serif;'
        f'text-align:center;">{safe}'
        '&nbsp;·&nbsp;第 <span class="pageNumber"></span> / '
        '<span class="totalPages"></span> 页</div>'
    )


# Header dedicated to document pages with their own paper layout (/result/print/*): brand on the left, audit target on the right.
def _doc_header_html(right: str) -> str:
    safe = html.escape(right or "")
    return (
        '<div style="width:100%;padding:0 13.7mm;box-sizing:border-box;'
        'font-size:7.5pt;color:#94a3b8;font-family:sans-serif;">'
        '<span style="float:left;font-weight:600;color:#78788c;">Vigilath</span>'
        f'<span style="float:right;">{safe}</span>'
        '</div>'
    )


def _doc_footer_html(title: str) -> str:
    safe = html.escape(title or "")
    return (
        '<div style="width:100%;padding:0 13.7mm;box-sizing:border-box;'
        'font-size:7.5pt;color:#94a3b8;font-family:sans-serif;">'
        f'<span style="float:left;">{safe}</span>'
        '<span style="float:right;">第 <span class="pageNumber"></span> / '
        '<span class="totalPages"></span> 页</span>'
        '</div>'
    )


def _merge_pdfs(parts: list[bytes]) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for part in parts:
        if part:
            writer.append(io.BytesIO(part))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# Paper document pages set this marker when rendering completes, see frontend/src/pages/ResultPrint.tsx
_PRINT_READY_JS = "document.documentElement.dataset.printReady === '1'"

# Before printing, remove everything that is not part of the document. The print route itself already uses a bare layout (App.tsx's
# Chrome has no header/footer), but the site root still hosts position:fixed things like the support bubble / toasts / modal host —
# Chrome printing **repeats fixed elements on every page**;
# the first version had a support avatar in the bottom-right of every page. This hides everything "not inside [data-export-root]"
# in one stroke, so nothing needs changing here when more is mounted at the site root later.
_DOC_PREPARE_JS = """() => {
    const root = document.querySelector('[data-export-root]');
    if (!root) return false;
    document.querySelectorAll('body *').forEach(el => {
        if (root.contains(el) || el.contains(root)) return;
        el.style.display = 'none';
    });
    return true;
}"""
_PRINT_READY_TIMEOUT_MS = 30_000
# 13.7mm on each side: A4 width 210mm minus these leaves exactly 182.6mm = 690px at 96dpi,
# aligned with the frontend block's CONTENT_W_PX, so the page does not need to be laid out again for printing.
_DOC_MARGIN = {"top": "14mm", "bottom": "13mm", "left": "13.7mm", "right": "13.7mm"}


async def capture_document_pdf(path: str, token: str, lang: str,
                               user=None, title: str = "",
                               header_right: str = "") -> bytes:
    """Print a **document page with its own paper layout**, producing a vector A4 PDF.

    Division of labor with `_capture_pdf`: that one prints the interactive report page as paper, relying first on
    `_PDF_PREPARE_JS` to hide the site shell, expand collapsed sections and pin responsive layout; the pages here
    (/result/print/*) are already laid out for A4 and need no preparation, only two page setups:

      1. Cover — margin 0, no header/footer, full bleed
      2. Body — margins + Chrome header/footer templates for page numbers

    A single `page.pdf()` cannot apply two setups, so print twice and merge with pypdf. The side effect is exactly
    what we want: page numbers are counted by the body pass itself, and the cover takes no page number, matching the old bitmap version.
    """
    from playwright.async_api import async_playwright

    seed = (
        f"localStorage.setItem('token', {json.dumps(token)});"
        f"localStorage.setItem('i18nextLng', {json.dumps(lang)});"
        # Deliverables are forced to light mode, not following the user's dark theme
        "localStorage.setItem('theme', 'light');"
        + _stored_user(user)
    )
    async with async_playwright() as p:
        browser = await _launch(p)
        try:
            ctx = await browser.new_context(
                viewport={"width": _VIEWPORT_W, "height": 900},
                reduced_motion="reduce",
            )
            await ctx.add_init_script(seed)
            page = await ctx.new_page()
            await page.goto(f"{_BASE_URL}{path}", wait_until="networkidle",
                            timeout=_GOTO_TIMEOUT_MS)
            # Only start printing when the page itself says "content complete". On render failure it sets 'error',
            # and waiting for '1' here times out — better a 502 than an empty report.
            await page.wait_for_function(_PRINT_READY_JS, timeout=_PRINT_READY_TIMEOUT_MS)
            if not await page.evaluate(_DOC_PREPARE_JS):
                raise RuntimeError("print document root not found")
            await page.wait_for_timeout(300)      # Let fonts settle

            await page.evaluate(
                "() => document.documentElement.setAttribute('data-print-pass', 'cover')")
            cover = await page.pdf(
                format="A4", print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )

            await page.evaluate(
                "() => document.documentElement.setAttribute('data-print-pass', 'body')")
            body = await page.pdf(
                format="A4", print_background=True,
                display_header_footer=True,
                header_template=_doc_header_html(header_right),
                footer_template=_doc_footer_html(title),
                margin=_DOC_MARGIN,
            )
            return await asyncio.to_thread(_merge_pdfs, [cover, body])
        finally:
            await browser.close()


async def _capture_pdf(path: str, token: str, lang: str, theme: str,
                       user=None, title: str = "") -> bytes:
    """Open the report page and produce a vector A4 PDF directly with the browser print engine."""
    from playwright.async_api import async_playwright

    seed = (
        f"localStorage.setItem('token', {json.dumps(token)});"
        f"localStorage.setItem('i18nextLng', {json.dumps(lang)});"
        # Reports are deliverables to print/forward, forced to light mode, not following the user's dark theme
        f"localStorage.setItem('theme', {json.dumps(theme or 'light')});"
        + _stored_user(user)
    )
    async with async_playwright() as p:
        browser = await _launch(p)
        try:
            ctx = await browser.new_context(
                viewport={"width": _VIEWPORT_W, "height": 900},
                reduced_motion="reduce",   # Disable stroke/fade-in animations; print the final state
            )
            await ctx.add_init_script(seed)
            page = await ctx.new_page()
            await page.goto(f"{_BASE_URL}{path}", wait_until="networkidle",
                            timeout=_GOTO_TIMEOUT_MS)
            await page.locator("[data-export-root]").wait_for(state="visible", timeout=20_000)
            try:
                await page.wait_for_function(
                    "!document.querySelector('[data-export-root] .animate-pulse')",
                    timeout=_SKELETON_TIMEOUT_MS,
                )
            except Exception:
                pass
            await page.wait_for_timeout(600)      # Let the charts' last frame stabilize
            await page.add_style_tag(content=_PDF_CSS)
            await page.wait_for_timeout(300)      # Wait for layout width to apply before snapshotting the layout
            if not await page.evaluate(_PDF_PREPARE_JS):
                raise RuntimeError("report root not found")
            await page.wait_for_timeout(400)      # Let reflow stabilize after the snapshot
            return await page.pdf(
                format="A4", print_background=True, prefer_css_page_size=True,
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=_footer_html(title),
                margin={"top": "12mm", "bottom": "14mm", "left": "10mm", "right": "10mm"},
            )
        finally:
            await browser.close()


async def _capture_png(path: str, token: str, lang: str, theme: str, user=None):
    from playwright.async_api import async_playwright

    seed = (
        f"localStorage.setItem('token', {json.dumps(token)});"
        f"localStorage.setItem('i18nextLng', {json.dumps(lang)});"
        + (f"localStorage.setItem('theme', {json.dumps(theme)});" if theme else "")
        + _stored_user(user)
    )
    async with async_playwright() as p:
        browser = await _launch(p)
        try:
            ctx = await browser.new_context(
                viewport={"width": _VIEWPORT_W, "height": 900},
                device_scale_factor=2,
                reduced_motion="reduce",   # Disable stroke/fade-in animations; capture the final state
            )
            await ctx.add_init_script(seed)
            page = await ctx.new_page()
            await page.goto(f"{_BASE_URL}{path}", wait_until="networkidle",
                            timeout=_GOTO_TIMEOUT_MS)
            root = page.locator("[data-export-root]")
            await root.wait_for(state="visible", timeout=20_000)
            # Wait for all skeletons to disappear (all data rendered); a timeout does not block, capture the current state
            try:
                await page.wait_for_function(
                    "!document.querySelector('[data-export-root] .animate-pulse')",
                    timeout=_SKELETON_TIMEOUT_MS,
                )
            except Exception:
                pass
            await page.wait_for_timeout(600)   # Let the charts' last frame stabilize
            meta = await page.evaluate(_PREPARE_JS)
            await page.wait_for_timeout(200)   # Let reflow stabilize after expanding / hiding
            root_h = meta.get("height") or 0
            scale = "css" if root_h > _CSS_SCALE_ABOVE_PX else "device"
            png = await root.screenshot(type="png", scale=scale)
            return png, meta
        finally:
            await browser.close()


# A page must use at least this much height before breaking early — below it, prefer cutting a card,
# otherwise a card taller than a page would leave most of the previous page blank.
_MIN_PAGE_FILL = 0.62
# When the last page's remainder is below this fraction of a page and the content is pure background (root's bottom padding/margin),
# drop the whole page so the PDF does not get an extra blank sheet; keep it if there is real content
_MIN_TAIL_FILL = 0.06


def _page_breaks(total_h: int, page_h: int,
                 chapters: list[int], blocks: list[int]) -> list[int]:
    """Compute each page's cut lines. Both inputs are "y coordinates where a cut is allowed" (block bottoms, sorted).

    Each page fills page_h as much as possible. Prefer cutting between chapters (chapters), so chapter titles are not
    left orphaned at the bottom of the previous page; when a whole chapter is taller than a page, fall back to cutting between cards in the chapter (blocks);
    only with neither, cut hard.
    """
    breaks: list[int] = []
    top = 0
    while total_h - top > page_h:
        limit = top + page_h
        floor = top + int(page_h * _MIN_PAGE_FILL)
        cut = max((y for y in chapters if floor <= y <= limit), default=0)
        if cut <= top:
            cut = max((y for y in blocks if floor <= y <= limit), default=0)
        if cut <= top:            # Defensive: with bad cut-point data degrade to a hard cut; must not loop forever
            cut = limit
        breaks.append(cut)
        top = cut
    breaks.append(total_h)
    return breaks


def _is_blank(img, box, pad_color) -> bool:
    """Is this region pure background — judged by extrema: any channel more than 8 away from the background counts as content."""
    piece = img.crop(box)
    if piece.size[0] == 0 or piece.size[1] == 0:
        return True
    lo, hi = zip(*piece.getextrema())      # Per-channel (min, max)
    return all(abs(v - c) <= 8 for v, c in zip(lo, pad_color)) and \
           all(abs(v - c) <= 8 for v, c in zip(hi, pad_color))


def _png_to_a4_pdf(png: bytes, meta: Optional[dict] = None) -> bytes:
    from PIL import Image

    # Long reports with expanded details easily exceed 100M pixels; PIL's default decompression-bomb threshold (~89M) warns or even refuses to open
    Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS or 0, 400_000_000)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    page_h = max(1, int(w * 297 / 210))        # A4 portrait ratio
    pad_color = img.getpixel((0, 0))           # Last-page padding uses the page background (not glaring in dark theme)

    # The browser measures CSS px, while the screenshot may be a 2x bitmap → convert to pixel coordinates by width ratio
    chapters: list[int] = []
    blocks: list[int] = []
    css_w = (meta or {}).get("width") or 0
    if css_w > 0:
        ratio = w / css_w
        # Cut points are each block's bottom edge: cutting here = the block stays whole on the previous page
        def _ys(key):
            return sorted({int(b["bottom"] * ratio) for b in (meta or {}).get(key, [])
                           if 0 < b["bottom"] * ratio < h})
        chapters, blocks = _ys("chapters"), _ys("blocks")

    breaks = _page_breaks(h, page_h, chapters, blocks)
    # The last page only has a sliver left (root's bottom padding) and it really is pure background → drop the whole page
    if len(breaks) >= 2 and (breaks[-1] - breaks[-2]) < page_h * _MIN_TAIL_FILL \
            and _is_blank(img, (0, breaks[-2], w, breaks[-1]), pad_color):
        breaks.pop()

    pages = []
    top = 0
    for cut in breaks:
        piece = img.crop((0, top, w, cut))
        if piece.size[1] < page_h:             # Early break / last page → pad the bottom to a full page
            full = Image.new("RGB", (w, page_h), pad_color)
            full.paste(piece, (0, 0))
            piece = full
        pages.append(piece)
        top = cut

    buf = io.BytesIO()
    dpi = w / 8.27                              # A4 width is 8.27 inches → PDF page is A4
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:],
                  resolution=dpi)
    return buf.getvalue()


@router.post("/reports/export-pdf")
async def export_report_pdf(
    body: ExportPdfIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_topic_or_404(
        db, body.topic_id, user.id,
        allow_admin_user=user, action=ACTION_READ,
    )

    # Only allow rendering the report center's own routes; reject arbitrary URLs
    if not body.path.startswith("/reports") or "//" in body.path:
        raise HTTPException(status_code=400, detail="invalid path")

    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="missing token")

    async with _SEM:
        try:
            png, meta = await _capture_png(body.path, token, body.lang, body.theme, user)
        except Exception as e:  # noqa: BLE001
            # On render failure the frontend only sees a 502 and quietly falls back to html-to-image, so the real cause must be logged
            logger.exception("report export render failed: path=%s user=%s", body.path, user.email)
            raise HTTPException(status_code=502, detail=f"render failed: {e}") from e

    pdf = await asyncio.to_thread(_png_to_a4_pdf, png, meta)
    return Response(content=pdf, media_type="application/pdf")
