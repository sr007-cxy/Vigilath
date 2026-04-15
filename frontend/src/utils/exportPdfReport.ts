import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';
import type { TFunction } from 'i18next';
import type { GeoTestResult, CheckResult } from '../types/geo';

type GroupStat = {
  tab: string;
  total: number;
  passed: number;
  failed: number;
  warned: number;
  info: number;
  passRate: number;
  isPaid: boolean;
  isTabLocked: boolean;
};

interface ExportArgs {
  result: GeoTestResult;
  t: TFunction;
  language: string;
  groupStats: GroupStat[];
  categoryGroups: Record<string, string[]>;
  checksByCategory: Record<string, CheckResult[]>;
  otherCategories: string[];
}

// ----- Page geometry (A4 portrait, pt) -----
const PAGE_W_PT = 595.28;
const PAGE_H_PT = 841.89;
const MARGIN_X_PT = 40;
const HEADER_H_PT = 44;
const FOOTER_H_PT = 30;
const CONTENT_TOP_PT = HEADER_H_PT;
const CONTENT_BOTTOM_PT = PAGE_H_PT - FOOTER_H_PT;
const CONTENT_W_PT = PAGE_W_PT - 2 * MARGIN_X_PT;
const CONTENT_H_PT = CONTENT_BOTTOM_PT - CONTENT_TOP_PT;
const BLOCK_GAP_PT = 6;

// Offscreen container width in CSS px. 1pt ≈ 1.333 px @ 96dpi → 515pt × 1.333 ≈ 687 px.
// Rounded slightly up so html2canvas has integer-friendly layout.
const CONTENT_W_PX = 690;
const CANVAS_SCALE = 2;

const STATUS_COLOR: Record<string, { bg: string; fg: string; key: 'pass' | 'warn' | 'fail' | 'info' }> = {
  PASS: { bg: '#dcfce7', fg: '#166534', key: 'pass' },
  WARN: { bg: '#fef3c7', fg: '#92400e', key: 'warn' },
  FAIL: { bg: '#fee2e2', fg: '#991b1b', key: 'fail' },
  INFO: { bg: '#dbeafe', fg: '#1e40af', key: 'info' },
};

const STATUS_RANK: Record<string, number> = { FAIL: 0, WARN: 1, INFO: 2, PASS: 3 };

// Fix recommendations in the PDF mirror the on-screen gating: only starter /
// growth / scale tiers get the "Top Fixes" section and the per-item fix text.
// Lower tiers (free, pro) get a fix-free report. Backend also strips fix text
// for those tiers, so this is belt-and-braces — if a fix field slips through
// we still don't render it.
const FIX_ALLOWED_TIERS: ReadonlySet<string> = new Set(['starter', 'growth', 'scale']);
const canIncludeFix = (result: GeoTestResult): boolean =>
  FIX_ALLOWED_TIERS.has((result.tier || '').toLowerCase());

const escapeHtml = (s: string): string =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

const interpretScore = (score: number, t: TFunction): string => {
  if (score >= 90) return t('result.pdfReport.scoreLevels.excellent');
  if (score >= 75) return t('result.pdfReport.scoreLevels.good');
  if (score >= 60) return t('result.pdfReport.scoreLevels.average');
  if (score >= 40) return t('result.pdfReport.scoreLevels.poor');
  return t('result.pdfReport.scoreLevels.critical');
};

const statusBadge = (status: string, t: TFunction): string => {
  const color = STATUS_COLOR[status] || STATUS_COLOR.INFO;
  const label = t(`result.pdfReport.statusLabels.${color.key}`);
  return `<span style="display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:600;background:${color.bg};color:${color.fg};">${escapeHtml(label)}</span>`;
};

const tierLabel = (tier: string | null | undefined, t: TFunction): string => {
  const slug = (tier || 'free').toLowerCase();
  return t(`result.pdfReport.tierLabels.${slug}`, { defaultValue: slug.toUpperCase() });
};

// Shared font stack — covers CJK + Latin via browser-installed system fonts.
const FONT_STACK = `-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Helvetica Neue',Arial,sans-serif`;

// Wrapper applied to every block so html2canvas sees a proper root with
// identical font metrics and width constraints.
const blockWrapOpen = (extra = '') =>
  `<div data-pdf-block style="font-family:${FONT_STACK};color:#0f172a;background:#fff;width:${CONTENT_W_PX}px;box-sizing:border-box;${extra}">`;
const blockWrapClose = `</div>`;

// ---------- Block builders ----------

const buildCoverBlock = (args: ExportArgs): string => {
  const { result, t, language } = args;
  const generatedAt = new Date().toLocaleString(language.startsWith('zh') ? 'zh-CN' : 'en-US');
  return (
    blockWrapOpen('padding:4px 0 18px 0;border-bottom:3px solid #0ea5e9;margin-bottom:8px;') +
    `<div style="font-size:11px;letter-spacing:0.22em;color:#06b6d4;text-transform:uppercase;font-weight:700;margin-bottom:6px;">${escapeHtml(t('result.pdfReport.coverBadge'))}</div>` +
    `<div style="font-size:26px;font-weight:800;line-height:1.25;margin-bottom:6px;color:#0f172a;">${escapeHtml(t('result.pdfReport.title'))}</div>` +
    `<div style="font-size:12px;color:#475569;margin-bottom:14px;">${escapeHtml(t('result.pdfReport.subtitle'))}</div>` +
    `<table style="width:100%;font-size:11.5px;color:#334155;border-collapse:collapse;">` +
    `<tr><td style="padding:3px 0;width:110px;color:#94a3b8;">${escapeHtml(t('result.pdfReport.targetSite'))}</td><td style="padding:3px 0;font-weight:600;color:#0f172a;word-break:break-all;">${escapeHtml(result.url || '')}</td></tr>` +
    `<tr><td style="padding:3px 0;color:#94a3b8;">${escapeHtml(t('result.pdfReport.generatedAt'))}</td><td style="padding:3px 0;color:#0f172a;">${escapeHtml(generatedAt)}</td></tr>` +
    `<tr><td style="padding:3px 0;color:#94a3b8;">${escapeHtml(t('result.pdfReport.tier'))}</td><td style="padding:3px 0;color:#0f172a;">${escapeHtml(tierLabel(result.tier, t))}</td></tr>` +
    `</table>` +
    blockWrapClose
  );
};

const buildScoreBlock = (args: ExportArgs): string => {
  const { result, t } = args;
  const score = result.score || 0;
  const grade = result.grade || 'F';
  const summary = result.summary || { pass_count: 0, warn_count: 0, fail_count: 0, info_count: 0, total_checks: 0 };
  return (
    blockWrapOpen('padding:0 0 8px 0;') +
    `<div style="display:flex;gap:14px;align-items:stretch;">` +
    `<div style="flex-shrink:0;width:170px;background:linear-gradient(135deg,#0f172a,#1e293b);color:#fff;border-radius:12px;padding:18px;text-align:center;box-sizing:border-box;">` +
    `<div style="font-size:10px;letter-spacing:0.16em;color:#94a3b8;text-transform:uppercase;margin-bottom:6px;">${escapeHtml(t('result.pdfReport.overallScore'))}</div>` +
    `<div style="font-size:48px;font-weight:800;line-height:1;color:#22d3ee;">${score}</div>` +
    `<div style="font-size:11px;color:#94a3b8;margin-top:4px;">/ 100</div>` +
    `<div style="margin-top:12px;display:inline-block;padding:5px 16px;border-radius:999px;background:rgba(6,182,212,0.15);border:1px solid rgba(6,182,212,0.4);font-size:16px;font-weight:700;color:#22d3ee;">${escapeHtml(grade)}</div>` +
    `</div>` +
    `<div style="flex:1;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:16px;box-sizing:border-box;">` +
    `<div style="font-size:11px;font-weight:700;color:#0f172a;margin-bottom:6px;">${escapeHtml(t('result.pdfReport.scoreInterpretation'))}</div>` +
    `<div style="font-size:12px;color:#334155;line-height:1.7;">${escapeHtml(interpretScore(score, t))}</div>` +
    `<div style="display:flex;gap:8px;margin-top:14px;">` +
    `<div style="flex:1;background:#dcfce7;border-radius:8px;padding:8px 6px;text-align:center;"><div style="font-size:16px;font-weight:800;color:#166534;">${summary.pass_count}</div><div style="font-size:9px;color:#166534;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.passed'))}</div></div>` +
    `<div style="flex:1;background:#fef3c7;border-radius:8px;padding:8px 6px;text-align:center;"><div style="font-size:16px;font-weight:800;color:#92400e;">${summary.warn_count}</div><div style="font-size:9px;color:#92400e;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.warnings'))}</div></div>` +
    `<div style="flex:1;background:#fee2e2;border-radius:8px;padding:8px 6px;text-align:center;"><div style="font-size:16px;font-weight:800;color:#991b1b;">${summary.fail_count}</div><div style="font-size:9px;color:#991b1b;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.failed'))}</div></div>` +
    `<div style="flex:1;background:#dbeafe;border-radius:8px;padding:8px 6px;text-align:center;"><div style="font-size:16px;font-weight:800;color:#1e40af;">${summary.info_count}</div><div style="font-size:9px;color:#1e40af;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.info'))}</div></div>` +
    `</div>` +
    `</div>` +
    `</div>` +
    blockWrapClose
  );
};

const buildLockedNoticeBlock = (lockedCount: number, t: TFunction): string =>
  blockWrapOpen('padding:0 0 4px 0;') +
  `<div style="font-size:11px;color:#92400e;background:#fef3c7;border-left:3px solid #f59e0b;padding:10px 12px;border-radius:6px;">${escapeHtml(t('result.pdfReport.lockedNotice', { count: lockedCount }))}</div>` +
  blockWrapClose;

const buildSectionHeadingBlock = (text: string, accent: string): string =>
  blockWrapOpen('padding:10px 0 4px 0;') +
  `<div style="font-size:16px;font-weight:800;color:#0f172a;border-left:4px solid ${accent};padding-left:10px;">${escapeHtml(text)}</div>` +
  blockWrapClose;

const buildGroupTableBlock = (args: ExportArgs): string => {
  const { t, groupStats } = args;
  const tabLabel = (tab: string) => t(`result.categories.${tab}`);
  const rows = groupStats
    .filter((g) => g.total > 0)
    .map((g) => {
      return (
        `<tr>` +
        `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;font-weight:600;color:#0f172a;">${escapeHtml(tabLabel(g.tab))}</td>` +
        `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:center;color:#0f172a;">${g.total}</td>` +
        `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:center;color:#166534;font-weight:600;">${g.passed}</td>` +
        `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:center;color:#92400e;font-weight:600;">${g.warned}</td>` +
        `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:center;color:#991b1b;font-weight:600;">${g.failed}</td>` +
        `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;width:150px;">` +
        `<div style="background:#e5e7eb;border-radius:999px;height:7px;width:100%;overflow:hidden;"><div style="background:#0ea5e9;height:100%;width:${g.passRate}%;"></div></div>` +
        `<div style="font-size:10px;color:#475569;margin-top:3px;">${g.passRate}%</div>` +
        `</td>` +
        `</tr>`
      );
    })
    .join('');

  return (
    blockWrapOpen('padding:4px 0 10px 0;') +
    `<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;font-size:11.5px;">` +
    `<thead><tr style="background:#f1f5f9;">` +
    `<th style="padding:9px 10px;text-align:left;font-size:10px;color:#475569;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;">${escapeHtml(t('result.pdfReport.groupLabel'))}</th>` +
    `<th style="padding:9px 10px;text-align:center;font-size:10px;color:#475569;font-weight:700;">${escapeHtml(t('result.summary.totalChecks'))}</th>` +
    `<th style="padding:9px 10px;text-align:center;font-size:10px;color:#166534;font-weight:700;">${escapeHtml(t('result.summary.passed'))}</th>` +
    `<th style="padding:9px 10px;text-align:center;font-size:10px;color:#92400e;font-weight:700;">${escapeHtml(t('result.summary.warnings'))}</th>` +
    `<th style="padding:9px 10px;text-align:center;font-size:10px;color:#991b1b;font-weight:700;">${escapeHtml(t('result.summary.failed'))}</th>` +
    `<th style="padding:9px 10px;text-align:left;font-size:10px;color:#475569;font-weight:700;">${escapeHtml(t('result.groupProgress.title'))}</th>` +
    `</tr></thead>` +
    `<tbody>${rows}</tbody>` +
    `</table>` +
    blockWrapClose
  );
};

const buildTopFixBlock = (check: CheckResult, idx: number, t: TFunction): string => {
  const catLabel = t(`result.categoryLabels.${check.category}`, { defaultValue: check.category });
  const fix = check.fix && check.fix.trim() ? check.fix.trim() : t('result.pdfReport.noFix');
  return (
    blockWrapOpen('padding:3px 0;') +
    `<div style="display:flex;gap:10px;padding:11px 13px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;">` +
    `<div style="flex-shrink:0;width:24px;height:24px;border-radius:999px;background:#0f172a;color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;">${idx + 1}</div>` +
    `<div style="flex:1;min-width:0;">` +
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;">` +
    `<div style="font-size:11.5px;font-weight:700;color:#0f172a;">${escapeHtml(catLabel)}</div>` +
    statusBadge(check.status, t) +
    `</div>` +
    `<div style="font-size:11.5px;color:#334155;line-height:1.6;margin-bottom:4px;">${escapeHtml(check.message || '')}</div>` +
    `<div style="font-size:10.5px;color:#475569;line-height:1.6;"><span style="font-weight:600;color:#0f172a;">${escapeHtml(t('result.pdfReport.fixLabel'))}:</span> ${escapeHtml(fix)}</div>` +
    `</div>` +
    `</div>` +
    blockWrapClose
  );
};

const buildTabHeadingBlock = (tabKey: string, t: TFunction): string =>
  blockWrapOpen('padding:10px 0 4px 0;') +
  `<div style="font-size:14px;font-weight:800;color:#0ea5e9;border-bottom:2px solid #0ea5e9;padding-bottom:5px;">${escapeHtml(t(`result.categories.${tabKey}`))}</div>` +
  blockWrapClose;

const buildCategoryBlock = (
  cat: string,
  checks: CheckResult[],
  t: TFunction,
  showFix: boolean,
): string => {
  const catLabel = t(`result.categoryLabels.${cat}`, { defaultValue: cat });
  const catDesc = t(`result.categoryDescriptions.${cat}`, { defaultValue: '' });
  const itemsHtml = checks
    .map((c) => {
      const fix = c.fix && c.fix.trim() ? c.fix.trim() : t('result.pdfReport.noFix');
      return (
        `<div style="border:1px solid #e5e7eb;border-radius:8px;padding:11px 13px;margin-bottom:8px;background:#fff;">` +
        `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;gap:10px;">` +
        `<div style="font-size:12px;font-weight:600;color:#0f172a;flex:1;">${escapeHtml(c.message || '')}</div>` +
        `<div>${statusBadge(c.status, t)}</div>` +
        `</div>` +
        (showFix && c.status !== 'PASS'
          ? `<div style="font-size:11px;color:#475569;line-height:1.6;"><span style="font-weight:600;color:#0f172a;">${escapeHtml(t('result.pdfReport.fixLabel'))}:</span> ${escapeHtml(fix)}</div>`
          : '') +
        `</div>`
      );
    })
    .join('');

  return (
    blockWrapOpen('padding:6px 0 2px 0;') +
    `<div style="font-size:13.5px;font-weight:700;color:#0f172a;margin-bottom:4px;">${escapeHtml(catLabel)}</div>` +
    (catDesc
      ? `<div style="font-size:11px;color:#475569;line-height:1.6;margin-bottom:8px;padding:8px 10px;background:#f1f5f9;border-left:3px solid #06b6d4;border-radius:4px;">${escapeHtml(catDesc)}</div>`
      : '') +
    itemsHtml +
    blockWrapClose
  );
};

const buildAppendixBlock = (t: TFunction): string =>
  blockWrapOpen('padding:14px 0 4px 0;') +
  `<div style="padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">` +
  `<div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:6px;">${escapeHtml(t('result.pdfReport.appendixSection'))}</div>` +
  `<div style="font-size:11px;color:#475569;line-height:1.7;">${escapeHtml(t('result.pdfReport.appendixBody'))}</div>` +
  `</div>` +
  blockWrapClose;

// ---------- Compose ----------

const buildAllBlocks = (args: ExportArgs): string[] => {
  const { result, t, categoryGroups, checksByCategory, otherCategories } = args;
  const lockedSet = new Set(result.locked_categories || []);
  const showFix = canIncludeFix(result);
  const blocks: string[] = [];

  blocks.push(buildCoverBlock(args));
  blocks.push(buildScoreBlock(args));
  if (lockedSet.size > 0) blocks.push(buildLockedNoticeBlock(lockedSet.size, t));

  blocks.push(buildSectionHeadingBlock(t('result.pdfReport.groupSection'), '#0ea5e9'));
  blocks.push(buildGroupTableBlock(args));

  // Top-Fixes / recommendations block is a paid perk — omit entirely for
  // tiers that don't see fix recommendations, so the PDF layout matches the
  // on-screen experience.
  if (showFix) {
    const failsAndWarns = (result.checks || [])
      .filter((c) => c.status === 'FAIL' || c.status === 'WARN')
      .sort((a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9))
      .slice(0, 12);

    blocks.push(buildSectionHeadingBlock(t('result.pdfReport.recommendationsSection'), '#ec4899'));
    blocks.push(
      blockWrapOpen('padding:2px 0 4px 0;') +
        `<div style="font-size:11px;color:#475569;">${escapeHtml(t('result.pdfReport.topFixesIntro'))}</div>` +
        blockWrapClose,
    );
    if (failsAndWarns.length === 0) {
      blocks.push(
        blockWrapOpen('padding:4px 0;') +
          `<div style="font-size:11.5px;color:#475569;padding:12px;background:#f1f5f9;border-radius:8px;">${escapeHtml(t('result.pdfReport.noFailItems'))}</div>` +
          blockWrapClose,
      );
    } else {
      failsAndWarns.forEach((c, idx) => blocks.push(buildTopFixBlock(c, idx, t)));
    }
  }

  blocks.push(buildSectionHeadingBlock(t('result.pdfReport.detailSection'), '#8b5cf6'));
  const allTabs = [...Object.keys(categoryGroups), ...(otherCategories.length ? ['other'] : [])];
  for (const tab of allTabs) {
    const cats = tab === 'other' ? otherCategories : categoryGroups[tab] || [];
    const visibleCats = cats.filter((c) => !lockedSet.has(c) && (checksByCategory[c]?.length ?? 0) > 0);
    if (visibleCats.length === 0) continue;
    blocks.push(buildTabHeadingBlock(tab, t));
    for (const cat of visibleCats) {
      blocks.push(buildCategoryBlock(cat, checksByCategory[cat] || [], t, showFix));
    }
  }

  blocks.push(buildAppendixBlock(t));
  return blocks;
};

// ---------- Capture & pagination ----------

type CapturedBlock = {
  dataUrl: string;
  widthPx: number;
  heightPx: number;
  widthPt: number;
  heightPt: number;
};

const captureBlocks = async (htmlBlocks: string[]): Promise<CapturedBlock[]> => {
  // Stage all blocks in one offscreen container so they share a layout context.
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

// For oversized blocks (taller than a page's content area), pre-slice the source
// canvas image vertically into page-sized chunks. We do this by re-rendering the
// JPEG onto an intermediate canvas, cropping, and exporting.
const sliceOversizedBlock = async (block: CapturedBlock): Promise<CapturedBlock[]> => {
  // max chunk height in pt = CONTENT_H_PT; convert to px via block ratio
  const pxPerPt = block.widthPx / block.widthPt;
  const maxChunkPx = Math.floor(CONTENT_H_PT * pxPerPt);
  if (block.heightPx <= maxChunkPx) return [block];

  // Load the jpeg back into an Image for cropping
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

type PlacedBlock = { block: CapturedBlock; x: number; y: number; pageIndex: number };

const packBlocks = (blocks: CapturedBlock[]): { placed: PlacedBlock[]; pageCount: number } => {
  const placed: PlacedBlock[] = [];
  let pageIndex = 0;
  let cursorY = CONTENT_TOP_PT;
  for (const b of blocks) {
    // If block does not fit and we're not at top of page, start a new page.
    if (cursorY + b.heightPt > CONTENT_BOTTOM_PT && cursorY > CONTENT_TOP_PT) {
      pageIndex += 1;
      cursorY = CONTENT_TOP_PT;
    }
    // If block still does not fit on a fresh page (shouldn't happen post-slice,
    // but be defensive), clip to remaining and continue.
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

const drawHeaderFooter = (
  pdf: jsPDF,
  pageNum: number,
  totalPages: number,
  args: ExportArgs,
): void => {
  const { result, t } = args;
  // Header: brand + site
  pdf.setFont('helvetica', 'normal');
  pdf.setTextColor(120, 120, 130);
  pdf.setFontSize(9);
  pdf.text('GEO Checker', MARGIN_X_PT, 24);

  const siteLabel = `${t('result.pdfReport.headerSite')}: ${result.url || ''}`;
  const maxWidth = CONTENT_W_PT - 100;
  const truncated =
    pdf.getTextWidth(siteLabel) > maxWidth
      ? siteLabel.slice(0, Math.max(10, Math.floor((maxWidth / pdf.getTextWidth(siteLabel)) * siteLabel.length) - 1)) + '…'
      : siteLabel;
  pdf.text(truncated, PAGE_W_PT - MARGIN_X_PT, 24, { align: 'right' });

  // Header rule
  pdf.setDrawColor(226, 232, 240);
  pdf.setLineWidth(0.5);
  pdf.line(MARGIN_X_PT, 32, PAGE_W_PT - MARGIN_X_PT, 32);

  // Footer rule
  pdf.line(MARGIN_X_PT, PAGE_H_PT - FOOTER_H_PT + 8, PAGE_W_PT - MARGIN_X_PT, PAGE_H_PT - FOOTER_H_PT + 8);

  // Footer text: left brand, right page n/N
  pdf.setFontSize(9);
  pdf.setTextColor(148, 163, 184);
  const year = new Date().getFullYear();
  pdf.text(`GEO Checker · © ${year}`, MARGIN_X_PT, PAGE_H_PT - 14);
  const pageText = t('result.pdfReport.pageOf', { current: pageNum, total: totalPages });
  pdf.text(pageText, PAGE_W_PT - MARGIN_X_PT, PAGE_H_PT - 14, { align: 'right' });
};

// ---------- Entry point ----------

export async function exportPdfReport(args: ExportArgs): Promise<void> {
  const htmlBlocks = buildAllBlocks(args);
  const rawCaptured = await captureBlocks(htmlBlocks);

  // Pre-slice any block that would overflow a single page.
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
    drawHeaderFooter(pdf, p + 1, pageCount, args);
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

  const safeUrl = (args.result.url || 'site').replace(/[^a-zA-Z0-9]/g, '-').slice(0, 60);
  const baseName = args.t('result.pdfReport.fileName');
  pdf.save(`${baseName}-${safeUrl}.pdf`);
}
