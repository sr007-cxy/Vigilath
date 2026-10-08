// Shared primitives for PDF report generation. The default 25-category
// exporter (exportPdfReport.ts) and the 6 advanced-mode exporters
// (exportAdvancedPdfReport.ts) both build a list of HTML "blocks" and
// hand them to composeAndSavePdf, which captures, paginates, decorates
// with header/footer, and saves.

import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';
import type { TFunction } from 'i18next';

// ----- Page geometry (A4 portrait, pt) -----
export const PAGE_W_PT = 595.28;
export const PAGE_H_PT = 841.89;
export const MARGIN_X_PT = 40;
export const HEADER_H_PT = 44;
export const FOOTER_H_PT = 30;
export const CONTENT_TOP_PT = HEADER_H_PT;
export const CONTENT_BOTTOM_PT = PAGE_H_PT - FOOTER_H_PT;
export const CONTENT_W_PT = PAGE_W_PT - 2 * MARGIN_X_PT;
export const CONTENT_H_PT = CONTENT_BOTTOM_PT - CONTENT_TOP_PT;
export const BLOCK_GAP_PT = 6;

// Offscreen container width in CSS px. 1pt ≈ 1.333 px @ 96dpi → 515pt × 1.333 ≈ 687 px.
export const CONTENT_W_PX = 690;
// Full-bleed blocks (cover / section dividers) are laid out at A4 @96dpi and
// drawn edge-to-edge, ignoring the text margins.
export const PAGE_W_PX = 794;
export const PAGE_H_PX = 1123;
export const CANVAS_SCALE = 2;

export const STATUS_COLOR: Record<string, { bg: string; fg: string; key: 'pass' | 'warn' | 'fail' | 'info' }> = {
  PASS: { bg: '#dcfce7', fg: '#166534', key: 'pass' },
  WARN: { bg: '#fef3c7', fg: '#92400e', key: 'warn' },
  FAIL: { bg: '#fee2e2', fg: '#991b1b', key: 'fail' },
  INFO: { bg: '#dbeafe', fg: '#1e40af', key: 'info' },
};

// The two Noto / Source Han fonts at the end are for the **server**: backend Chrome printing runs on Linux,
// where PingFang / Microsoft YaHei do not exist, and fontconfig's sans-serif resolves to DejaVu Sans by default
// (no CJK glyphs). List them explicitly so Chinese does not fall back glyph by glyph and look different on each machine.
export const FONT_STACK = `-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Helvetica Neue',Arial,'Noto Sans CJK SC','Source Han Sans SC',sans-serif`;

/**
 * Font stack used inside SVG. `-apple-system` / `BlinkMacSystemFont` are CSS keywords
 * that have no effect in an SVG font-family, so only real font families are listed.
 */
export const SVG_FONT = `'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Helvetica Neue',Arial,'Noto Sans CJK SC','Source Han Sans SC',sans-serif`;

export const escapeHtml = (s: string | number | null | undefined): string =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

// ---------- Layout pieces with text on a background color: always SVG ----------
//
// html2canvas 1.4.1 draws text with its own font metrics; with CJK fallback fonts the computed
// baseline is 2-4px higher than the browser's (the larger the font, the bigger the offset). On a white background it is invisible, but once there is
// a background box behind it - status badges, index blocks, metric cells, color bands - you get visible "text and background misalignment".
// None of the alternatives worked: `line-height` equal to the box height still shifts, and `display:table-cell` vertical
// centering makes the text disappear entirely in the screenshot.
//
// In SVG, a <text>'s y is the baseline, a deterministic value identical before and after the screenshot. Headers/footers and score rings
// already worked this way; this extracts that approach into shared components.

/** The visual center of digits/Latin glyphs sits about 0.355em above the baseline; CJK fits too. */
const BASELINE_RATIO = 0.355;

let _measureCtx: CanvasRenderingContext2D | null = null;

/** Measure the pixel width of text at a given font size/weight - SVG does not grow on its own, so it must be computed first. */
export const measureTextPx = (text: string, fontSize: number, weight = 400): number => {
  if (!_measureCtx) _measureCtx = document.createElement('canvas').getContext('2d');
  if (!_measureCtx) return text.length * fontSize * 0.62;   // rough estimate when no 2D context is available
  _measureCtx.font = `${weight} ${fontSize}px ${SVG_FONT}`;
  return _measureCtx.measureText(text).width;
};

export interface SvgChipOpts {
  text: string;
  /** Text color. */
  fg: string;
  /** Background color; transparent if omitted. */
  bg?: string;
  /** Stroke color (outlined badge). */
  border?: string;
  fontSize?: number;
  weight?: number;
  height?: number;
  /** Horizontal padding; only applies when the width is measured from the text. */
  padX?: number;
  radius?: number;
  minWidth?: number;
  /** When a width is given, the text is not measured - used for square/circular index blocks. */
  width?: number;
  align?: 'center' | 'left';
  /**
   * Badges that occupy a cell alone (index blocks) use `block`: an inline element creates a line box with
   * half a line of descender space at the bottom, so text on the same row misaligns and line spacing gets stretched.
   * Badges mixed with text keep the default `inline-block`.
   */
  display?: 'inline-block' | 'block';
}

/** Small single-line badge with a background box, the whole thing one inline SVG. */
export const svgChip = (o: SvgChipOpts): string => {
  const fontSize = o.fontSize ?? 9.5;
  const weight = o.weight ?? 700;
  const height = o.height ?? Math.round(fontSize * 1.7);
  const padX = o.padX ?? 6;
  const radius = o.radius ?? 4;
  const width =
    o.width ?? Math.max(o.minWidth ?? 0, Math.ceil(measureTextPx(o.text, fontSize, weight) + padX * 2));
  const align = o.align ?? 'center';
  // Draw the stroke on the half pixel so the rx rounded corners do not lose half an edge
  const inset = o.border ? 0.5 : 0;
  const rect =
    o.bg || o.border
      ? `<rect x="${inset}" y="${inset}" width="${width - inset * 2}" height="${height - inset * 2}" rx="${radius}" ` +
        `fill="${o.bg || 'none'}"${o.border ? ` stroke="${o.border}"` : ''}/>`
      : '';
  // display:inline-block must be hard-coded inline: Tailwind preflight has a rule
  // `svg { display: block }`; once the badges become block-level each takes its own line, and the
  // one-line "dimension + category name + status" breaks into three lines. Inline styles beat the stylesheet.
  return (
    `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" ` +
    `font-family="${SVG_FONT}" style="display:${o.display ?? 'inline-block'};vertical-align:middle;">` +
    rect +
    `<text x="${align === 'center' ? width / 2 : padX}" y="${(height / 2 + fontSize * BASELINE_RATIO).toFixed(2)}" ` +
    `text-anchor="${align === 'center' ? 'middle' : 'start'}" font-size="${fontSize}" font-weight="${weight}" ` +
    `fill="${o.fg}">${escapeHtml(o.text)}</text>` +
    `</svg>`
  );
};

export interface SvgBandText {
  text: string;
  fontSize: number;
  weight?: number;
  color: string;
}

/** A full color band: title on the left + note on the right. Each text is centered within the band height, not relying on line boxes. */
export const svgBand = (o: {
  width: number;
  height: number;
  left: SvgBandText;
  right?: SvgBandText;
  bg?: string;
  radius?: number;
  padX?: number;
}): string => {
  const padX = o.padX ?? 10;
  const baseline = (fs: number) => (o.height / 2 + fs * BASELINE_RATIO).toFixed(2);
  const seg = (s: SvgBandText, x: number, anchor: 'start' | 'end') =>
    `<text x="${x}" y="${baseline(s.fontSize)}" text-anchor="${anchor}" font-size="${s.fontSize}" ` +
    `font-weight="${s.weight ?? 400}" fill="${s.color}">${escapeHtml(s.text)}</text>`;
  return (
    `<svg width="${o.width}" height="${o.height}" viewBox="0 0 ${o.width} ${o.height}" ` +
    `xmlns="http://www.w3.org/2000/svg" font-family="${SVG_FONT}" style="display:block;">` +
    (o.bg ? `<rect x="0" y="0" width="${o.width}" height="${o.height}" rx="${o.radius ?? 6}" fill="${o.bg}"/>` : '') +
    seg(o.left, padX, 'start') +
    (o.right ? seg(o.right, o.width - padX, 'end') : '') +
    `</svg>`
  );
};

export const blockWrapOpen = (extra = ''): string =>
  `<div data-pdf-block style="font-family:${FONT_STACK};color:#0f172a;background:#fff;width:${CONTENT_W_PX}px;box-sizing:border-box;${extra}">`;
export const blockWrapClose = `</div>`;

/** Start this block on a fresh page instead of packing it after the previous one. */
export const blockWrapOpenNewPage = (extra = ''): string =>
  `<div data-pdf-block data-pdf-break style="font-family:${FONT_STACK};color:#0f172a;background:#fff;width:${CONTENT_W_PX}px;box-sizing:border-box;${extra}">`;

/**
 * A section whose content may exceed a full page (long tables like the audit details).
 *
 * The backend Chrome printing path gives every section `break-inside:avoid` by default; oversized blocks need this
 * marker to allow page breaks between table rows - otherwise Chrome has to cram them and the tail gets cut off.
 * The html2canvas path ignores this attribute (oversized blocks are sliced into pages by sliceOversizedBlock).
 */
export const blockWrapOpenBreakable = (extra = ''): string =>
  `<div data-pdf-block data-pdf-breakable style="font-family:${FONT_STACK};color:#0f172a;background:#fff;width:${CONTENT_W_PX}px;box-sizing:border-box;${extra}">`;

/**
 * A block that owns a whole page edge-to-edge — no margins, no header, no
 * footer. Used for the cover and the section dividers, which are the two
 * places where a report should look like a document rather than a printout.
 */
export const fullPageWrapOpen = (extra = ''): string =>
  `<div data-pdf-block data-pdf-fullpage style="font-family:${FONT_STACK};background:#fff;width:${PAGE_W_PX}px;height:${PAGE_H_PX}px;overflow:hidden;box-sizing:border-box;${extra}">`;

export const statusBadge = (status: string, t: TFunction): string => {
  const color = STATUS_COLOR[status] || STATUS_COLOR.INFO;
  const label = t(`result.pdfReport.statusLabels.${color.key}`);
  return `<span style="font-size:12px;font-weight:700;color:${color.fg};">${escapeHtml(label)}</span>`;
};

// ---------- Capture & pagination pipeline ----------

export type CapturedBlock = {
  dataUrl: string;
  widthPx: number;
  heightPx: number;
  widthPt: number;
  heightPt: number;
  fullPage: boolean;
  breakBefore: boolean;
};

export const captureBlocks = async (htmlBlocks: string[]): Promise<CapturedBlock[]> => {
  const container = document.createElement('div');
  container.setAttribute('aria-hidden', 'true');
  // Container is A4-wide so full-bleed blocks lay out at their true width;
  // ordinary blocks pin themselves to CONTENT_W_PX via blockWrapOpen.
  container.style.cssText = `position:fixed;left:-10000px;top:0;width:${PAGE_W_PX}px;background:#fff;z-index:-1;`;
  document.body.appendChild(container);

  try {
    const captured: CapturedBlock[] = [];
    // One block in the DOM at a time. html2canvas clones the whole document on
    // every call, so keeping all ~70 blocks mounted made capture cost O(n²) —
    // a full-report export took minutes. Swapping innerHTML per block keeps
    // each clone small.
    for (const html of htmlBlocks) {
      container.innerHTML = html;
      await new Promise((r) => setTimeout(r, 0));
      const nodes = Array.from(container.querySelectorAll('[data-pdf-block]')) as HTMLElement[];
      for (const node of nodes) {
        // Layout flags travel on the wrapper element itself rather than in a
        // parallel array, so a builder is free to emit several blocks from one
        // string without the flags drifting onto the wrong one.
        const fullPage = node.hasAttribute('data-pdf-fullpage');
        const breakBefore = node.hasAttribute('data-pdf-break');
        const canvas = await html2canvas(node, {
          scale: CANVAS_SCALE,
          backgroundColor: '#ffffff',
          useCORS: true,
          logging: false,
          windowWidth: fullPage ? PAGE_W_PX : CONTENT_W_PX,
        });
        const widthPx = canvas.width;
        const heightPx = canvas.height;
        const widthPt = fullPage ? PAGE_W_PT : CONTENT_W_PT;
        const heightPt = (heightPx * widthPt) / widthPx;
        captured.push({
          dataUrl: canvas.toDataURL('image/jpeg', 0.92),
          widthPx,
          heightPx,
          widthPt,
          heightPt,
          fullPage,
          breakBefore,
        });
      }
    }
    return captured;
  } finally {
    document.body.removeChild(container);
  }
};

export const sliceOversizedBlock = async (block: CapturedBlock): Promise<CapturedBlock[]> => {
  const pxPerPt = block.widthPx / block.widthPt;
  const maxChunkPx = Math.floor(CONTENT_H_PT * pxPerPt);
  if (block.heightPx <= maxChunkPx) return [block];

  const img = await new Promise<HTMLImageElement>((res, rej) => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = rej;
    im.src = block.dataUrl;
  });

  const chunks: CapturedBlock[] = [];
  let y = 0;
  while (y < block.heightPx) {
    const chunkHeightPx = Math.min(maxChunkPx, block.heightPx - y);
    const c = document.createElement('canvas');
    c.width = block.widthPx;
    c.height = chunkHeightPx;
    const ctx = c.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D context unavailable');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, -y);
    chunks.push({
      dataUrl: c.toDataURL('image/jpeg', 0.92),
      widthPx: c.width,
      heightPx: c.height,
      widthPt: block.widthPt,
      heightPt: (chunkHeightPx * block.widthPt) / block.widthPx,
      fullPage: false,
      breakBefore: chunks.length === 0 ? block.breakBefore : false,
    });
    y += chunkHeightPx;
  }
  return chunks;
};

export type PlacedBlock = { block: CapturedBlock; x: number; y: number; pageIndex: number };

export const packBlocks = (
  blocks: CapturedBlock[],
): { placed: PlacedBlock[]; pageCount: number; barePages: Set<number> } => {
  const placed: PlacedBlock[] = [];
  const barePages = new Set<number>();
  let pageIndex = 0;
  let cursorY = CONTENT_TOP_PT;
  const newPage = () => {
    pageIndex += 1;
    cursorY = CONTENT_TOP_PT;
  };
  for (const b of blocks) {
    // Full-bleed block: takes the page it lands on all to itself.
    if (b.fullPage) {
      if (cursorY > CONTENT_TOP_PT) newPage();
      placed.push({ block: b, x: 0, y: 0, pageIndex });
      barePages.add(pageIndex);
      newPage();
      continue;
    }
    if (b.breakBefore && cursorY > CONTENT_TOP_PT) newPage();
    if (cursorY + b.heightPt > CONTENT_BOTTOM_PT && cursorY > CONTENT_TOP_PT) newPage();
    if (b.heightPt > CONTENT_H_PT) {
      placed.push({ block: b, x: MARGIN_X_PT, y: cursorY, pageIndex });
      newPage();
      continue;
    }
    placed.push({ block: b, x: MARGIN_X_PT, y: cursorY, pageIndex });
    cursorY += b.heightPt + BLOCK_GAP_PT;
  }
  // A trailing newPage() from a full-bleed block can leave an empty last page.
  const usedPages = new Set(placed.map((p) => p.pageIndex));
  let pageCount = pageIndex + 1;
  while (pageCount > 1 && !usedPages.has(pageCount - 1)) pageCount -= 1;
  return { placed, pageCount, barePages };
};

// ---------- Header/footer + entry point ----------

export interface StandardHeaderFooterOptions {
  rightHeaderText: string;
  t: TFunction;
}

// Capture a small HTML snippet to a data-URL image via html2canvas.
// Used for header/footer so CJK text renders correctly (jsPDF built-in
// fonts don't support Chinese characters).
const captureHtmlToImage = async (
  html: string,
  widthPx: number,
): Promise<{ dataUrl: string; widthPx: number; heightPx: number }> => {
  const container = document.createElement('div');
  container.setAttribute('aria-hidden', 'true');
  container.style.cssText = `position:fixed;left:-10000px;top:0;width:${widthPx}px;background:#fff;z-index:-1;`;
  container.innerHTML = html;
  document.body.appendChild(container);
  try {
    await new Promise((r) => setTimeout(r, 20));
    const canvas = await html2canvas(container, {
      scale: CANVAS_SCALE,
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false,
      windowWidth: widthPx,
    });
    return {
      dataUrl: canvas.toDataURL('image/png'),
      widthPx: canvas.width,
      heightPx: canvas.height,
    };
  } finally {
    document.body.removeChild(container);
  }
};

// Pre-render all per-page header/footer images so CJK text works.
//
// IMPORTANT: the left and right text of headers/footers must share the baseline. In HTML, the Latin-only "Vigilath"
// and the CJK-mixed right text (containing Chinese) are computed as two separate line-boxes by html2canvas, with baselines a few px apart,
// and even vertical-align:middle on both does not align them. The only reliable approach: make the whole bar a single SVG,
// with both <text> nodes at exactly the same y (in SVG, y is the baseline, deterministic).
const renderHeaderFooterImages = async (
  opts: StandardHeaderFooterOptions,
  totalPages: number,
  barePages: Set<number> = new Set(),
): Promise<{ headers: string[]; footers: (string | null)[] }> => {
  const { rightHeaderText, t } = opts;
  const year = new Date().getFullYear();
  const widthPx = CONTENT_W_PX;

  // SVG headers/footers - Latin+CJK text share the baseline, no need to deal with line-box offsets
  const HDR_FONT = SVG_FONT;
  const headerHtml =
    `<div style="width:${widthPx}px;background:#fff;">` +
    `<svg width="${widthPx}" height="22" xmlns="http://www.w3.org/2000/svg" font-family="${HDR_FONT}">` +
    `<text x="0" y="14" font-size="11" font-weight="600" fill="#78788c">Vigilath</text>` +
    `<text x="${widthPx}" y="14" text-anchor="end" font-size="11" fill="#78788c">${escapeHtml(rightHeaderText)}</text>` +
    `<line x1="0" y1="21" x2="${widthPx}" y2="21" stroke="#e2e8f0" stroke-width="1"/>` +
    `</svg></div>`;
  const headerImg = await captureHtmlToImage(headerHtml, widthPx);
  const headerDataUrl = headerImg.dataUrl;

  // Cover / divider pages carry no chrome and are excluded from the numbering
  // too — "第 1 页" ("Page 1") should land on the first page that actually has content.
  const numberedTotal = totalPages - barePages.size;
  const footers: (string | null)[] = [];
  let numbered = 0;
  for (let p = 1; p <= totalPages; p++) {
    if (barePages.has(p - 1)) {
      footers.push(null);
      continue;
    }
    numbered += 1;
    const pageText = t('result.pdfReport.pageOf', { current: numbered, total: numberedTotal });
    const footerHtml =
      `<div style="width:${widthPx}px;background:#fff;">` +
      `<svg width="${widthPx}" height="22" xmlns="http://www.w3.org/2000/svg" font-family="${HDR_FONT}">` +
      `<line x1="0" y1="1" x2="${widthPx}" y2="1" stroke="#e2e8f0" stroke-width="1"/>` +
      `<text x="0" y="15" font-size="10" fill="#94a3b8">Vigilath · © ${year}</text>` +
      `<text x="${widthPx}" y="15" text-anchor="end" font-size="10" fill="#94a3b8">${escapeHtml(pageText)}</text>` +
      `</svg></div>`;
    const footerImg = await captureHtmlToImage(footerHtml, widthPx);
    footers.push(footerImg.dataUrl);
  }

  const headers = Array(totalPages).fill(headerDataUrl) as string[];
  return { headers, footers };
};

export async function composeAndSavePdf(
  htmlBlocks: string[],
  headerFooterOpts: StandardHeaderFooterOptions,
  fileName: string,
): Promise<void> {
  const rawCaptured = await captureBlocks(htmlBlocks);

  const captured: CapturedBlock[] = [];
  for (const b of rawCaptured) {
    // Full-bleed blocks are page-sized by construction — slicing them against
    // the *text* height would cut the cover in two.
    if (!b.fullPage && b.heightPt > CONTENT_H_PT) {
      const sliced = await sliceOversizedBlock(b);
      captured.push(...sliced);
    } else {
      captured.push(b);
    }
  }

  const { placed, pageCount, barePages } = packBlocks(captured);

  // Pre-render header/footer as images (supports CJK text).
  const { headers, footers } = await renderHeaderFooterImages(headerFooterOpts, pageCount, barePages);

  const pdf = new jsPDF({ unit: 'pt', format: 'a4', orientation: 'portrait' });

  const hdrWidthPt = CONTENT_W_PT;
  const hdrHeightPt = HEADER_H_PT - 4;
  const ftrWidthPt = CONTENT_W_PT;
  const ftrHeightPt = FOOTER_H_PT - 4;

  for (let p = 0; p < pageCount; p++) {
    if (p > 0) pdf.addPage();
    if (!barePages.has(p)) {
      // Draw header image
      pdf.addImage(headers[p], 'PNG', MARGIN_X_PT, 6, hdrWidthPt, hdrHeightPt);
      // Draw footer image
      const footer = footers[p];
      if (footer) {
        pdf.addImage(footer, 'PNG', MARGIN_X_PT, PAGE_H_PT - FOOTER_H_PT + 2, ftrWidthPt, ftrHeightPt);
      }
    }
    // Draw content blocks
    for (const item of placed) {
      if (item.pageIndex !== p) continue;
      pdf.addImage(
        item.block.dataUrl,
        'JPEG',
        item.x,
        item.y,
        item.block.widthPt,
        item.block.heightPt,
      );
    }
  }

  pdf.save(fileName);
}
