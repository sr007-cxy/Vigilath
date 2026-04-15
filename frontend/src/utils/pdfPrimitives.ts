// Shared primitives for PDF report generation. The default 23-category
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
export const CANVAS_SCALE = 2;

export const STATUS_COLOR: Record<string, { bg: string; fg: string; key: 'pass' | 'warn' | 'fail' | 'info' }> = {
  PASS: { bg: '#dcfce7', fg: '#166534', key: 'pass' },
  WARN: { bg: '#fef3c7', fg: '#92400e', key: 'warn' },
  FAIL: { bg: '#fee2e2', fg: '#991b1b', key: 'fail' },
  INFO: { bg: '#dbeafe', fg: '#1e40af', key: 'info' },
};

export const FONT_STACK = `-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Helvetica Neue',Arial,sans-serif`;

export const escapeHtml = (s: string | number | null | undefined): string =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

export const blockWrapOpen = (extra = ''): string =>
  `<div data-pdf-block style="font-family:${FONT_STACK};color:#0f172a;background:#fff;width:${CONTENT_W_PX}px;box-sizing:border-box;${extra}">`;
export const blockWrapClose = `</div>`;

export const statusBadge = (status: string, t: TFunction): string => {
  const color = STATUS_COLOR[status] || STATUS_COLOR.INFO;
  const label = t(`result.pdfReport.statusLabels.${color.key}`);
  return `<span style="display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:600;background:${color.bg};color:${color.fg};">${escapeHtml(label)}</span>`;
};

// ---------- Capture & pagination pipeline ----------

export type CapturedBlock = {
  dataUrl: string;
  widthPx: number;
  heightPx: number;
  widthPt: number;
  heightPt: number;
};

export const captureBlocks = async (htmlBlocks: string[]): Promise<CapturedBlock[]> => {
  const container = document.createElement('div');
  container.setAttribute('aria-hidden', 'true');
  container.style.cssText = `position:fixed;left:-10000px;top:0;width:${CONTENT_W_PX}px;background:#fff;z-index:-1;`;
  container.innerHTML = htmlBlocks.join('');
  document.body.appendChild(container);

  try {
    await new Promise((r) => setTimeout(r, 60));
    const nodes = Array.from(container.querySelectorAll('[data-pdf-block]')) as HTMLElement[];
    const captured: CapturedBlock[] = [];
    for (const node of nodes) {
      const canvas = await html2canvas(node, {
        scale: CANVAS_SCALE,
        backgroundColor: '#ffffff',
        useCORS: true,
        logging: false,
        windowWidth: CONTENT_W_PX,
      });
      const widthPx = canvas.width;
      const heightPx = canvas.height;
      const widthPt = CONTENT_W_PT;
      const heightPt = (heightPx * CONTENT_W_PT) / widthPx;
      captured.push({
        dataUrl: canvas.toDataURL('image/jpeg', 0.92),
        widthPx,
        heightPx,
        widthPt,
        heightPt,
      });
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
      widthPt: CONTENT_W_PT,
      heightPt: (chunkHeightPx * CONTENT_W_PT) / block.widthPx,
    });
    y += chunkHeightPx;
  }
  return chunks;
};

export type PlacedBlock = { block: CapturedBlock; x: number; y: number; pageIndex: number };

export const packBlocks = (blocks: CapturedBlock[]): { placed: PlacedBlock[]; pageCount: number } => {
  const placed: PlacedBlock[] = [];
  let pageIndex = 0;
  let cursorY = CONTENT_TOP_PT;
  for (const b of blocks) {
    if (cursorY + b.heightPt > CONTENT_BOTTOM_PT && cursorY > CONTENT_TOP_PT) {
      pageIndex += 1;
      cursorY = CONTENT_TOP_PT;
    }
    if (b.heightPt > CONTENT_H_PT) {
      placed.push({ block: b, x: MARGIN_X_PT, y: cursorY, pageIndex });
      pageIndex += 1;
      cursorY = CONTENT_TOP_PT;
      continue;
    }
    placed.push({ block: b, x: MARGIN_X_PT, y: cursorY, pageIndex });
    cursorY += b.heightPt + BLOCK_GAP_PT;
  }
  return { placed, pageCount: pageIndex + 1 };
};

// ---------- Header/footer + entry point ----------

export interface DrawHeaderFooterContext {
  pdf: jsPDF;
  pageNum: number;
  totalPages: number;
}
export type HeaderFooterDrawer = (ctx: DrawHeaderFooterContext) => void;

export interface StandardHeaderFooterOptions {
  rightHeaderText: string;
  t: TFunction;
}

// Default brand header/footer used by every report. `rightHeaderText` is
// usually the target site (default report) or the audited subject (advanced).
export const makeStandardHeaderFooter = (
  opts: StandardHeaderFooterOptions,
): HeaderFooterDrawer => {
  const { rightHeaderText, t } = opts;
  return ({ pdf, pageNum, totalPages }) => {
    pdf.setFont('helvetica', 'normal');
    pdf.setTextColor(120, 120, 130);
    pdf.setFontSize(9);
    pdf.text('GEO Checker', MARGIN_X_PT, 24);

    const maxWidth = CONTENT_W_PT - 100;
    const truncated =
      pdf.getTextWidth(rightHeaderText) > maxWidth
        ? rightHeaderText.slice(
            0,
            Math.max(10, Math.floor((maxWidth / pdf.getTextWidth(rightHeaderText)) * rightHeaderText.length) - 1),
          ) + '…'
        : rightHeaderText;
    pdf.text(truncated, PAGE_W_PT - MARGIN_X_PT, 24, { align: 'right' });

    pdf.setDrawColor(226, 232, 240);
    pdf.setLineWidth(0.5);
    pdf.line(MARGIN_X_PT, 32, PAGE_W_PT - MARGIN_X_PT, 32);
    pdf.line(MARGIN_X_PT, PAGE_H_PT - FOOTER_H_PT + 8, PAGE_W_PT - MARGIN_X_PT, PAGE_H_PT - FOOTER_H_PT + 8);

    pdf.setFontSize(9);
    pdf.setTextColor(148, 163, 184);
    const year = new Date().getFullYear();
    pdf.text(`GEO Checker · © ${year}`, MARGIN_X_PT, PAGE_H_PT - 14);
    const pageText = t('result.pdfReport.pageOf', { current: pageNum, total: totalPages });
    pdf.text(pageText, PAGE_W_PT - MARGIN_X_PT, PAGE_H_PT - 14, { align: 'right' });
  };
};

export async function composeAndSavePdf(
  htmlBlocks: string[],
  drawHeaderFooter: HeaderFooterDrawer,
  fileName: string,
): Promise<void> {
  const rawCaptured = await captureBlocks(htmlBlocks);

  const captured: CapturedBlock[] = [];
  for (const b of rawCaptured) {
    if (b.heightPt > CONTENT_H_PT) {
      const sliced = await sliceOversizedBlock(b);
      captured.push(...sliced);
    } else {
      captured.push(b);
    }
  }

  const { placed, pageCount } = packBlocks(captured);
  const pdf = new jsPDF({ unit: 'pt', format: 'a4', orientation: 'portrait' });

  for (let p = 0; p < pageCount; p++) {
    if (p > 0) pdf.addPage();
    drawHeaderFooter({ pdf, pageNum: p + 1, totalPages: pageCount });
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
