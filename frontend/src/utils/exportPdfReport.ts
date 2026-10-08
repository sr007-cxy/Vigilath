import type { TFunction } from 'i18next';
import type { GeoTestResult, SeoTestResult, CheckResult } from '../types/geo';
import { categoryLabel } from '../i18n';
import { renderCheckMessage, renderCheckFix } from './checkText';
import {
  blockWrapClose,
  blockWrapOpen,
  blockWrapOpenBreakable,
  blockWrapOpenNewPage,
  fullPageWrapOpen,
  composeAndSavePdf,
  escapeHtml,
  svgBand,
  svgChip,
  CONTENT_W_PX,
  PAGE_H_PX,
  PAGE_W_PX,
  SVG_FONT,
} from './pdfPrimitives';

/**
 * PDF report for the standard audit (the URL input box on the home page).
 *
 * The layout is planned as a PDF document rather than copying the result page: full-page cover -> executive summary -> category score overview ->
 * priority fix list -> audit details for both dimensions -> appendix. AI visibility (GEO) and traditional SEO are
 * two parallel threads in the same report, no longer exported separately.
 *
 * Three hard constraints (all learned the hard way):
 * 1. We go through html2canvas screenshots, where `display:flex` gap / centering shifts - layout always uses
 *    table / table-cell / inline-block.
 * 2. Whenever text sits on a background color (badges / index blocks / metric cells / color bands), make the whole block an SVG.
 *    html2canvas puts the text baseline several pixels higher than the browser does, while the background box is drawn from the real layout,
 *    so writing it in HTML is guaranteed to misalign - see the svgChip / svgBand comments in pdfPrimitives.
 * 3. Copy must go through renderCheckMessage / renderCheckFix; reading check.message directly
 *    would export English (the backend's original text), which would not match the Chinese on the page.
 */

type GroupStat = {
  tab: string;
  total: number;
  passed: number;
  failed: number;
  warned: number;
  info: number;
  passRate: number;
};

/** All data needed to export one dimension (GEO or SEO), produced by the result page's useDimensionView. */
export interface DimensionSlice {
  result: GeoTestResult | SeoTestResult;
  /** Tab -> category name; determines the grouping order of the detail sections. */
  groups: Record<string, string[]>;
  checksByCategory: Record<string, CheckResult[]>;
  otherCategories: string[];
  groupStats: GroupStat[];
}

export interface ExportArgs {
  geo: DimensionSlice;
  /** null when the SEO half failed - the report is still produced, just with one thread missing. */
  seo?: DimensionSlice | null;
  t: TFunction;
  language: string;
}

// ---------- Design constants ----------

const C = {
  ink: '#0b1524',
  inkSoft: '#13263f',
  geo: '#06b6d4',
  geoDeep: '#0891b2',
  seo: '#818cf8',
  seoDeep: '#4f46e5',
  text: '#0f172a',
  sub: '#475569',
  mute: '#94a3b8',
  line: '#e2e8f0',
  lineSoft: '#f1f5f9',
  surface: '#f8fafc',
  pass: '#16a34a',
  warn: '#d97706',
  fail: '#dc2626',
  info: '#2563eb',
};

const STATUS_RANK: Record<string, number> = { FAIL: 0, WARN: 1, INFO: 2, PASS: 3 };

const STATUS_STYLE: Record<string, { fg: string; bg: string; key: string }> = {
  PASS: { fg: C.pass, bg: '#dcfce7', key: 'pass' },
  WARN: { fg: C.warn, bg: '#fef3c7', key: 'warn' },
  FAIL: { fg: C.fail, bg: '#fee2e2', key: 'fail' },
  INFO: { fg: C.info, bg: '#dbeafe', key: 'info' },
};

// Fix suggestions are a starter / growth / scale entitlement. The backend already withholds fix copy for lower tiers;
// this is a second guard: even if a field leaks through, it is not rendered.
const FIX_ALLOWED_TIERS: ReadonlySet<string> = new Set(['starter', 'growth', 'scale']);

const scoreColor = (score: number): string => {
  if (score >= 90) return '#22c55e';
  if (score >= 75) return '#06b6d4';
  if (score >= 60) return '#eab308';
  if (score >= 40) return '#f97316';
  return '#ef4444';
};

const scoreLevelKey = (score: number): string => {
  if (score >= 90) return 'excellent';
  if (score >= 75) return 'good';
  if (score >= 60) return 'average';
  if (score >= 40) return 'poor';
  return 'critical';
};

const tierLabel = (tier: string | null | undefined, t: TFunction): string => {
  const slug = (tier || 'free').toLowerCase();
  return t(`result.pdfReport.tierLabels.${slug}`, { defaultValue: slug.toUpperCase() });
};

const truncate = (s: string, max: number): string => (s.length > max ? `${s.slice(0, max - 1)}…` : s);

// ---------- Small components ----------

/** Status pill. It has a background color, so the whole block is SVG (in HTML the text floats to the top edge of the background in screenshots). */
const statusPill = (status: string, t: TFunction): string => {
  const s = STATUS_STYLE[status] || STATUS_STYLE.INFO;
  return svgChip({
    text: t(`result.pdfReport.statusLabels.${s.key}`),
    fg: s.fg,
    bg: s.bg,
    fontSize: 9.5,
    weight: 700,
    height: 17,
    padX: 7,
    minWidth: 40,
  });
};

/** Index block: square (rounded) or circle, with a number inside. */
const indexChip = (n: number | string, size: number, radius: number, bg: string, fg: string, fontSize: number): string =>
  svgChip({ text: String(n), fg, bg, width: size, height: size, radius, fontSize, weight: 800, display: 'block' });

/** Pass-rate progress bar - plain nested blocks, no flex. */
const progressBar = (pct: number, color: string, width = '100%'): string =>
  `<div style="background:${C.lineSoft};border-radius:999px;height:6px;width:${width};">` +
  `<div style="background:${color};height:6px;border-radius:999px;width:${Math.max(0, Math.min(100, pct))}%;"></div>` +
  `</div>`;

/**
 * Score ring. Built as a single SVG: the ring and the number are positioned in the SVG coordinate system,
 * not stacked with position:absolute (that is the pattern html2canvas shifts the most).
 * Grade and band name go outside the ring; only a number stays inside - two lines would crowd together at small sizes.
 */
const ringSvg = (opts: { size: number; score: number; color: string; numColor?: string }): string => {
  const { size, score, color } = opts;
  const numColor = opts.numColor || color;
  const cx = size / 2;
  const stroke = Math.round(size * 0.095);
  const r = cx - stroke / 2 - 1;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - Math.max(0, Math.min(100, score)) / 100);
  const numSize = Math.round(size * 0.4);
  return (
    `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" font-family="${SVG_FONT}">` +
    `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="#e2e8f0" stroke-width="${stroke}"/>` +
    `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"` +
    ` stroke-dasharray="${circ.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}" transform="rotate(-90 ${cx} ${cx})"/>` +
    `<text x="${cx}" y="${cx + numSize * 0.36}" text-anchor="middle" font-size="${numSize}" font-weight="800" fill="${numColor}">${score}</text>` +
    `</svg>`
  );
};

// ---------- Cover (full-page SVG) ----------

const buildCoverBlock = (args: ExportArgs): string => {
  const { geo, seo, t, language } = args;
  const g = geo.result;
  const s = seo?.result || null;
  const generatedAt = new Date().toLocaleString(language.startsWith('zh') ? 'zh-CN' : 'en-US');
  const totalChecks = (g.summary?.total_checks || 0) + (s?.summary?.total_checks || 0);

  const W = PAGE_W_PX;
  const H = PAGE_H_PX;
  const geoColor = scoreColor(g.score || 0);
  const seoScoreColor = s ? scoreColor(s.score || 0) : '#475569';

  // Two score rings side by side, centers hard-coded in SVG coordinates - no dependency on any box-model layout.
  const ringBlock = (cx: number, score: number, grade: string, color: string, label: string, available: boolean): string => {
    const r = 74;
    const stroke = 13;
    const circ = 2 * Math.PI * r;
    const offset = circ * (1 - Math.max(0, Math.min(100, score)) / 100);
    const cy = 700;
    if (!available) {
      return (
        `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#1e3a56" stroke-width="${stroke}"/>` +
        `<text x="${cx}" y="${cy + 14}" text-anchor="middle" font-size="40" font-weight="800" fill="#475569">—</text>` +
        `<text x="${cx}" y="${cy + r + 40}" text-anchor="middle" font-size="15" font-weight="600" fill="#64748b">${escapeHtml(label)}</text>` +
        `<text x="${cx}" y="${cy + r + 62}" text-anchor="middle" font-size="11" fill="#475569">${escapeHtml(t('result.pdfReport.coverSeoMissing'))}</text>`
      );
    }
    return (
      `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#1e3a56" stroke-width="${stroke}"/>` +
      `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"` +
      ` stroke-dasharray="${circ.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}" transform="rotate(-90 ${cx} ${cy})"/>` +
      `<text x="${cx}" y="${cy + 14}" text-anchor="middle" font-size="52" font-weight="800" fill="#ffffff">${score}</text>` +
      `<text x="${cx}" y="${cy + 38}" text-anchor="middle" font-size="12" fill="#7d93ad">/ 100</text>` +
      `<text x="${cx}" y="${cy + r + 40}" text-anchor="middle" font-size="15" font-weight="600" fill="#e2e8f0">${escapeHtml(label)}</text>` +
      `<text x="${cx}" y="${cy + r + 64}" text-anchor="middle" font-size="13" font-weight="700" fill="${color}">${escapeHtml(t('result.pdfReport.gradeLabel', { grade }))}</text>`
    );
  };

  const metaCol = (x: number, label: string, value: string, anchor = 'start'): string =>
    `<text x="${x}" y="905" text-anchor="${anchor}" font-size="10.5" fill="#5f7796" letter-spacing="1">${escapeHtml(label)}</text>` +
    `<text x="${x}" y="930" text-anchor="${anchor}" font-size="14" font-weight="600" fill="#e2e8f0">${escapeHtml(value)}</text>`;

  const svg =
    `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" font-family="${SVG_FONT}">` +
    `<defs>` +
    `<linearGradient id="cov-bg" x1="0" y1="0" x2="0.6" y2="1">` +
    `<stop offset="0%" stop-color="${C.ink}"/><stop offset="60%" stop-color="${C.inkSoft}"/><stop offset="100%" stop-color="#0d1b2e"/>` +
    `</linearGradient>` +
    `<linearGradient id="cov-rule" x1="0" y1="0" x2="1" y2="0">` +
    `<stop offset="0%" stop-color="${C.geo}"/><stop offset="100%" stop-color="${C.seoDeep}" stop-opacity="0.15"/>` +
    `</linearGradient>` +
    `</defs>` +
    `<rect x="0" y="0" width="${W}" height="${H}" fill="url(#cov-bg)"/>` +
    // Decoration: concentric arcs at the top right, low contrast, pure geometry
    `<circle cx="${W + 30}" cy="90" r="230" fill="none" stroke="#17304d" stroke-width="1"/>` +
    `<circle cx="${W + 30}" cy="90" r="180" fill="none" stroke="#17304d" stroke-width="1"/>` +
    `<circle cx="${W + 30}" cy="90" r="130" fill="none" stroke="#17304d" stroke-width="1"/>` +
    // Top brand row
    `<text x="64" y="92" font-size="16" font-weight="700" letter-spacing="6" fill="#67e8f9">VIGILATH</text>` +
    `<text x="${W - 64}" y="92" text-anchor="end" font-size="11" fill="#5f7796">${escapeHtml(t('result.pdfReport.coverBrandLine'))}</text>` +
    `<rect x="64" y="112" width="${W - 128}" height="2" fill="url(#cov-rule)"/>` +
    // Main title area
    `<text x="64" y="248" font-size="13" letter-spacing="5" font-weight="600" fill="#38bdf8">${escapeHtml(t('result.pdfReport.coverEyebrow'))}</text>` +
    `<text x="64" y="322" font-size="45" font-weight="800" fill="#ffffff">${escapeHtml(t('result.pdfReport.coverTitleL1'))}</text>` +
    `<text x="64" y="382" font-size="45" font-weight="800" fill="#ffffff">${escapeHtml(t('result.pdfReport.coverTitleL2'))}</text>` +
    `<text x="64" y="424" font-size="13.5" fill="#8fa6bd">${escapeHtml(t('result.pdfReport.coverSubtitle'))}</text>` +
    // Target site panel
    `<rect x="64" y="464" width="${W - 128}" height="76" rx="10" fill="#ffffff" fill-opacity="0.05" stroke="#24405f"/>` +
    `<text x="88" y="496" font-size="10.5" letter-spacing="2" fill="#5f7796">${escapeHtml(t('result.pdfReport.targetSite'))}</text>` +
    `<text x="88" y="522" font-size="19" font-weight="600" fill="#ffffff">${escapeHtml(truncate(g.url || '', 46))}</text>` +
    // Dual score rings
    ringBlock(255, g.score || 0, g.grade || 'F', geoColor, t('result.dimensions.geo.scoreLabel'), true) +
    ringBlock(539, s?.score || 0, s?.grade || '', seoScoreColor, t('result.dimensions.seo.scoreLabel'), !!s) +
    // Vertical divider between the two rings
    `<line x1="397" y1="640" x2="397" y2="760" stroke="#22405e" stroke-width="1"/>` +
    // Bottom meta info
    `<line x1="64" y1="866" x2="${W - 64}" y2="866" stroke="#20374f" stroke-width="1"/>` +
    metaCol(64, t('result.pdfReport.generatedAt'), generatedAt) +
    metaCol(W / 2, t('result.pdfReport.tier'), tierLabel(g.tier, t), 'middle') +
    metaCol(W - 64, t('result.pdfReport.checksTotal'), t('result.pdfReport.itemsCount', { count: totalChecks }), 'end') +
    // Footer
    `<text x="64" y="1046" font-size="10.5" fill="#4a627e">${escapeHtml(t('result.pdfReport.coverFooter'))}</text>` +
    `<text x="${W - 64}" y="1046" text-anchor="end" font-size="10.5" fill="#4a627e">vigilath.cn</text>` +
    `</svg>`;

  return fullPageWrapOpen() + svg + blockWrapClose;
};

// ---------- Common sections ----------

/** Section title bar: index + title + note on the right, the whole bar is one table, no flex. */
const buildSectionBand = (
  index: string,
  title: string,
  note: string,
  accent: string,
  newPage = false,
): string =>
  (newPage ? blockWrapOpenNewPage('padding:2px 0 6px 0;') : blockWrapOpen('padding:12px 0 6px 0;')) +
  `<table style="width:100%;border-collapse:collapse;table-layout:fixed;">` +
  `<tr>` +
  `<td style="width:34px;vertical-align:middle;">${indexChip(index, 26, 7, accent, '#fff', 12)}</td>` +
  `<td style="vertical-align:middle;">` +
  `<div style="font-size:17px;font-weight:800;color:${C.text};line-height:1.3;">${escapeHtml(title)}</div>` +
  `</td>` +
  `<td style="vertical-align:middle;text-align:right;width:250px;">` +
  `<div style="font-size:10.5px;color:${C.mute};line-height:1.4;">${escapeHtml(note)}</div>` +
  `</td>` +
  `</tr>` +
  `</table>` +
  `<div style="height:2px;background:${accent};margin-top:7px;opacity:0.85;"></div>` +
  blockWrapClose;

/** Executive summary: conclusion paragraph + four metric cells + dual-dimension score cards. */
const buildSummaryBlock = (args: ExportArgs): string => {
  const { geo, seo, t } = args;
  const g = geo.result;
  const s = seo?.result || null;
  const gs = g.summary || { pass_count: 0, warn_count: 0, fail_count: 0, info_count: 0, total_checks: 0 };
  const ss = s?.summary || { pass_count: 0, warn_count: 0, fail_count: 0, info_count: 0, total_checks: 0 };
  const total = gs.total_checks + ss.total_checks;
  const pass = gs.pass_count + ss.pass_count;
  const warn = gs.warn_count + ss.warn_count;
  const fail = gs.fail_count + ss.fail_count;

  const lead = s
    ? t('result.pdfReport.summaryLead', {
        total, pass, warn, fail,
        geoScore: g.score || 0, geoGrade: g.grade || 'F',
        seoScore: s.score || 0, seoGrade: s.grade || 'F',
      })
    : t('result.pdfReport.summaryLeadGeoOnly', {
        total, pass, warn, fail, geoScore: g.score || 0, geoGrade: g.grade || 'F',
      });

  // The four metric cells are one SVG: numbers, labels and background blocks share one coordinate system, so they cannot drift apart.
  const kpiRow = (cells: { value: number; label: string; color: string; bg: string }[]): string => {
    const H = 62;
    const GAP = 10;
    const tileW = (CONTENT_W_PX - GAP * (cells.length - 1)) / cells.length;
    const tiles = cells
      .map((c, i) => {
        const x = i * (tileW + GAP);
        const cx = (x + tileW / 2).toFixed(2);
        return (
          `<rect x="${x.toFixed(2)}" y="0" width="${tileW.toFixed(2)}" height="${H}" rx="10" fill="${c.bg}"/>` +
          `<text x="${cx}" y="33" text-anchor="middle" font-size="22" font-weight="800" fill="${c.color}">${c.value}</text>` +
          `<text x="${cx}" y="47" text-anchor="middle" font-size="10" font-weight="600" fill="${c.color}" fill-opacity="0.85">${escapeHtml(c.label)}</text>`
        );
      })
      .join('');
    return (
      `<svg width="${CONTENT_W_PX}" height="${H}" viewBox="0 0 ${CONTENT_W_PX} ${H}" ` +
      `xmlns="http://www.w3.org/2000/svg" font-family="${SVG_FONT}" style="display:block;margin-top:12px;">${tiles}</svg>`
    );
  };

  // Dimension score cards: ring + grade + interpretation. Two equal-width cards; table-layout:fixed keeps them from crowding.
  const scoreCard = (
    label: string, score: number, grade: string, accent: string,
    levelsPrefix: string, available: boolean,
  ): string => {
    if (!available) {
      return (
        `<td style="width:50%;padding:0 5px;vertical-align:top;">` +
        `<div style="border:1px solid ${C.line};border-radius:12px;padding:14px;background:${C.surface};box-sizing:border-box;">` +
        `<div style="font-size:12px;font-weight:700;color:${C.sub};">${escapeHtml(label)}</div>` +
        `<div style="font-size:11.5px;color:${C.mute};line-height:1.7;padding-top:10px;">${escapeHtml(t('result.dimensions.seo.unavailable'))}</div>` +
        `</div></td>`
      );
    }
    return (
      `<td style="width:50%;padding:0 5px;vertical-align:top;">` +
      `<div style="border:1px solid ${C.line};border-top:3px solid ${accent};border-radius:12px;padding:13px 14px 14px 14px;background:#fff;box-sizing:border-box;">` +
      `<table style="width:100%;border-collapse:collapse;table-layout:fixed;">` +
      `<tr>` +
      `<td style="width:74px;vertical-align:top;">${ringSvg({ size: 68, score, color: scoreColor(score), numColor: C.text })}</td>` +
      `<td style="vertical-align:top;padding-left:8px;">` +
      `<div style="font-size:12.5px;font-weight:800;color:${C.text};padding-top:5px;">${escapeHtml(label)}</div>` +
      `<div style="font-size:10.5px;color:${C.sub};padding-top:5px;">` +
      `<span style="font-weight:700;color:${scoreColor(score)};">${escapeHtml(t('result.pdfReport.gradeLabel', { grade }))}</span>` +
      `<span style="color:${C.mute};"> · ${escapeHtml(t(`result.pdfReport.scoreBandName.${scoreLevelKey(score)}`))}</span></div>` +
      `<div style="padding-top:9px;">${progressBar(score, scoreColor(score))}</div>` +
      `</td></tr></table>` +
      `<div style="font-size:10.5px;color:${C.sub};line-height:1.65;padding-top:11px;">${escapeHtml(t(`${levelsPrefix}.${scoreLevelKey(score)}`))}</div>` +
      `</div></td>`
    );
  };

  return (
    blockWrapOpen('padding:6px 0 4px 0;') +
    `<div style="font-size:11.5px;color:${C.sub};line-height:1.85;background:${C.surface};border-left:3px solid ${C.geo};border-radius:0 8px 8px 0;padding:12px 14px;">${escapeHtml(lead)}</div>` +
    kpiRow([
      { value: total, label: t('result.summary.totalChecks'), color: '#334155', bg: C.lineSoft },
      { value: pass, label: t('result.summary.passed'), color: '#15803d', bg: '#dcfce7' },
      { value: warn, label: t('result.summary.warnings'), color: '#b45309', bg: '#fef3c7' },
      { value: fail, label: t('result.summary.failed'), color: '#b91c1c', bg: '#fee2e2' },
    ]) +
    `<table style="width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;margin-top:12px;"><tr>` +
    scoreCard(t('result.dimensions.geo.scoreLabel'), g.score || 0, g.grade || 'F', C.geo, 'result.pdfReport.scoreLevels', true) +
    scoreCard(t('result.dimensions.seo.scoreLabel'), s?.score || 0, s?.grade || 'F', C.seoDeep, 'result.dimensions.seo.scoreLevels', !!s) +
    `</tr></table>` +
    blockWrapClose
  );
};

/** Key findings: 2-4 conclusions derived from the stats of both dimensions. */
const buildFindingsBlock = (args: ExportArgs): string => {
  const { geo, seo, t } = args;
  const lines: string[] = [];

  const allStats: { stat: GroupStat; dim: 'geo' | 'seo' }[] = [
    ...geo.groupStats.map((stat) => ({ stat, dim: 'geo' as const })),
    ...(seo?.groupStats || []).map((stat) => ({ stat, dim: 'seo' as const })),
  ].filter((x) => x.stat.total > 0);

  const gs = geo.result.summary;
  const ss = seo?.result.summary;
  const total = (gs?.total_checks || 0) + (ss?.total_checks || 0);
  const pass = (gs?.pass_count || 0) + (ss?.pass_count || 0);
  const fail = (gs?.fail_count || 0) + (ss?.fail_count || 0);
  const warn = (gs?.warn_count || 0) + (ss?.warn_count || 0);
  const passRate = total ? Math.round((pass / total) * 100) : 0;

  lines.push(t('result.pdfReport.findingPassRate', { rate: passRate, pass, total }));

  if (fail > 0) {
    const failCats = [
      ...geo.result.checks.filter((c) => c.status === 'FAIL'),
      ...(seo?.result.checks || []).filter((c) => c.status === 'FAIL'),
    ]
      .map((c) => categoryLabel(c.category))
      .filter((v, i, a) => a.indexOf(v) === i)
      .slice(0, 4)
      .join('、');
    lines.push(t('result.pdfReport.findingFailCount', { count: fail, cats: failCats }));
  } else {
    lines.push(t('result.pdfReport.findingNoFail'));
  }

  const weakest = allStats.slice().sort((a, b) => a.stat.passRate - b.stat.passRate)[0];
  if (weakest && weakest.stat.passRate < 100) {
    lines.push(
      t('result.pdfReport.findingWeakest', {
        name: t(`result.categories.${weakest.stat.tab}`),
        dim: t(weakest.dim === 'geo' ? 'result.dimensions.geo.tab' : 'result.dimensions.seo.tab'),
        rate: weakest.stat.passRate,
      }),
    );
  }
  if (warn > 0) lines.push(t('result.pdfReport.findingWarn', { count: warn }));

  const items = lines
    .map(
      (line, i) =>
        `<tr>` +
        `<td style="width:22px;vertical-align:top;padding:5px 0;">${indexChip(i + 1, 16, 8, C.lineSoft, C.sub, 9)}</td>` +
        `<td style="vertical-align:top;padding:5px 0;font-size:11.5px;color:${C.sub};line-height:1.7;">${escapeHtml(line)}</td>` +
        `</tr>`,
    )
    .join('');

  return (
    blockWrapOpen('padding:10px 0 2px 0;') +
    `<div style="font-size:12.5px;font-weight:800;color:${C.text};padding-bottom:6px;">${escapeHtml(t('result.pdfReport.findingsTitle'))}</div>` +
    `<table style="width:100%;border-collapse:collapse;">${items}</table>` +
    blockWrapClose
  );
};

const buildLockedNoticeBlock = (lockedCount: number, t: TFunction): string =>
  blockWrapOpen('padding:8px 0 2px 0;') +
  `<div style="font-size:10.5px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-left:3px solid #f59e0b;padding:9px 12px;border-radius:6px;line-height:1.6;">${escapeHtml(t('result.pdfReport.lockedNotice', { count: lockedCount }))}</div>` +
  blockWrapClose;

/** Category score table: one per dimension. */
const buildGroupTableBlock = (
  title: string,
  stats: GroupStat[],
  accent: string,
  t: TFunction,
): string => {
  const rows = stats
    .filter((g) => g.total > 0)
    .map((g, i) => {
      const zebra = i % 2 === 1 ? `background:${C.surface};` : '';
      return (
        `<tr style="${zebra}">` +
        `<td style="padding:7px 10px;border-bottom:1px solid ${C.line};font-size:11px;font-weight:600;color:${C.text};">${escapeHtml(t(`result.categories.${g.tab}`))}</td>` +
        `<td style="padding:7px 6px;border-bottom:1px solid ${C.line};text-align:center;font-size:11px;color:${C.sub};">${g.total}</td>` +
        `<td style="padding:7px 6px;border-bottom:1px solid ${C.line};text-align:center;font-size:11px;color:${C.pass};font-weight:600;">${g.passed}</td>` +
        `<td style="padding:7px 6px;border-bottom:1px solid ${C.line};text-align:center;font-size:11px;color:${C.warn};font-weight:600;">${g.warned || '·'}</td>` +
        `<td style="padding:7px 6px;border-bottom:1px solid ${C.line};text-align:center;font-size:11px;color:${C.fail};font-weight:600;">${g.failed || '·'}</td>` +
        `<td style="padding:7px 10px 7px 6px;border-bottom:1px solid ${C.line};">` +
        `<table style="width:100%;border-collapse:collapse;table-layout:fixed;"><tr>` +
        `<td style="vertical-align:middle;">${progressBar(g.passRate, accent)}</td>` +
        `<td style="width:34px;text-align:right;vertical-align:middle;font-size:10px;color:${C.sub};font-weight:600;">${g.passRate}%</td>` +
        `</tr></table>` +
        `</td></tr>`
      );
    })
    .join('');

  const th = (label: string, extra = '') =>
    `<th style="padding:7px 8px;font-size:9.5px;font-weight:700;color:${C.mute};letter-spacing:0.04em;border-bottom:1px solid ${C.line};${extra}">${escapeHtml(label)}</th>`;

  return (
    blockWrapOpen('padding:10px 0 2px 0;') +
    `<table style="width:100%;border-collapse:collapse;table-layout:fixed;">` +
    `<tr><td style="width:4px;background:${accent};border-radius:2px;"></td>` +
    `<td style="padding-left:8px;font-size:12.5px;font-weight:800;color:${C.text};">${escapeHtml(title)}</td></tr></table>` +
    `<table style="width:100%;border-collapse:collapse;table-layout:fixed;margin-top:7px;border:1px solid ${C.line};border-radius:8px;">` +
    `<thead><tr style="background:${C.lineSoft};">` +
    th(t('result.pdfReport.groupLabel'), 'text-align:left;') +
    // 58px: the four characters 「总检查项」 ("total checks") must fit; at 46px they wrap to two lines
    th(t('result.summary.totalChecks'), 'text-align:center;width:58px;') +
    th(t('result.summary.passed'), 'text-align:center;width:42px;') +
    th(t('result.summary.warnings'), 'text-align:center;width:42px;') +
    th(t('result.summary.failed'), 'text-align:center;width:42px;') +
    th(t('result.pdfReport.passRate'), 'text-align:left;width:150px;') +
    `</tr></thead><tbody>${rows}</tbody></table>` +
    blockWrapClose
  );
};

// ---------- Priority fix list ----------

type FixItem = { check: CheckResult; dim: 'geo' | 'seo' };

const buildFixItemBlock = (item: FixItem, idx: number, t: TFunction): string => {
  const { check, dim } = item;
  const accent = dim === 'geo' ? C.geo : C.seoDeep;
  const dimLabel = t(dim === 'geo' ? 'result.dimensions.geo.tab' : 'result.dimensions.seo.tab');
  const fix = renderCheckFix(check, t) || t('result.pdfReport.noFix');
  const msg = renderCheckMessage(check, t);
  return (
    blockWrapOpen('padding:3px 0;') +
    `<table style="width:100%;border-collapse:collapse;table-layout:fixed;border:1px solid ${C.line};border-left:3px solid ${accent};border-radius:8px;background:#fff;">` +
    `<tr>` +
    `<td style="width:36px;vertical-align:top;padding:11px 0 11px 10px;">${indexChip(idx + 1, 22, 6, C.text, '#fff', 10.5)}</td>` +
    `<td style="vertical-align:top;padding:11px 12px 11px 0;">` +
    `<div style="padding-bottom:5px;">` +
    svgChip({ text: dimLabel, fg: accent, border: accent, fontSize: 9.5, weight: 700, height: 17, padX: 6 }) +
    `<span style="display:inline-block;font-size:11px;font-weight:700;color:${C.text};padding:0 6px;vertical-align:middle;">${escapeHtml(categoryLabel(check.category))}</span>` +
    statusPill(check.status, t) +
    `</div>` +
    `<div style="font-size:11px;color:${C.text};line-height:1.65;">${escapeHtml(msg)}</div>` +
    `<div style="font-size:10.5px;color:${C.sub};line-height:1.7;background:${C.surface};border-radius:6px;padding:7px 9px;margin-top:6px;">` +
    `<span style="font-weight:700;color:${C.text};">${escapeHtml(t('result.pdfReport.fixLabel'))}：</span>${escapeHtml(fix)}</div>` +
    `</td></tr></table>` +
    blockWrapClose
  );
};

// ---------- Audit details ----------

const buildTabHeadingBlock = (tabKey: string, stat: GroupStat | undefined, accent: string, t: TFunction): string =>
  blockWrapOpen('padding:12px 0 3px 0;') +
  svgBand({
    width: CONTENT_W_PX,
    height: 28,
    bg: C.surface,
    left: { text: t(`result.categories.${tabKey}`), fontSize: 12, weight: 800, color: accent },
    right: stat
      ? {
          text: `${t('result.pdfReport.itemsCount', { count: stat.total })} · ${t('result.pdfReport.passRate')} ${stat.passRate}%`,
          fontSize: 10,
          color: C.mute,
        }
      : undefined,
  }) +
  blockWrapClose;

const buildCategoryBlock = (
  cat: string,
  checks: CheckResult[],
  t: TFunction,
  showFix: boolean,
): string => {
  const catDesc = t(`result.categoryDescriptions.${cat}`, { defaultValue: '' });
  const rows = checks
    .map((c) => {
      const msg = renderCheckMessage(c, t);
      const fix = showFix && c.status !== 'PASS' ? renderCheckFix(c, t) : '';
      return (
        `<tr>` +
        `<td style="width:52px;vertical-align:top;padding:6px 8px 6px 0;">${statusPill(c.status, t)}</td>` +
        `<td style="vertical-align:top;padding:6px 0;border-bottom:1px solid ${C.lineSoft};">` +
        `<div style="font-size:11px;color:${C.text};line-height:1.6;">${escapeHtml(msg)}</div>` +
        (fix
          ? `<div style="font-size:10px;color:${C.sub};line-height:1.6;padding-top:3px;"><span style="color:${C.mute};">${escapeHtml(t('result.pdfReport.fixLabel'))}：</span>${escapeHtml(fix)}</div>`
          : '') +
        `</td></tr>`
      );
    })
    .join('');

  return (
    // One category may contain a dozen or more checks, long enough to span pages - mark it as a breakable block
    blockWrapOpenBreakable('padding:6px 0 2px 0;') +
    `<div style="font-size:11.5px;font-weight:700;color:${C.text};padding-bottom:3px;">${escapeHtml(categoryLabel(cat))}</div>` +
    (catDesc
      ? `<div style="font-size:10px;color:${C.mute};line-height:1.6;padding-bottom:5px;">${escapeHtml(catDesc)}</div>`
      : '') +
    `<table style="width:100%;border-collapse:collapse;table-layout:fixed;">${rows}</table>` +
    blockWrapClose
  );
};

// ---------- Appendix ----------

const buildAppendixBlock = (t: TFunction): string => {
  const legendCell = (status: string): string => {
    const s = STATUS_STYLE[status];
    return (
      `<td style="width:25%;padding:0 5px;vertical-align:top;">` +
      `<div style="border:1px solid ${C.line};border-radius:8px;padding:9px 10px;">` +
      `<div>${statusPill(status, t)}</div>` +
      `<div style="font-size:10px;color:${C.sub};line-height:1.6;padding-top:6px;">${escapeHtml(t(`result.pdfReport.legend.${s.key}`))}</div>` +
      `</div></td>`
    );
  };

  const bands: { key: string; range: string }[] = [
    { key: 'excellent', range: '90 – 100' },
    { key: 'good', range: '75 – 89' },
    { key: 'average', range: '60 – 74' },
    { key: 'poor', range: '40 – 59' },
    { key: 'critical', range: '0 – 39' },
  ];
  const bandRows = bands
    .map(
      (b) =>
        `<tr>` +
        `<td style="width:70px;padding:5px 0;font-size:10.5px;font-weight:700;color:${C.text};">${escapeHtml(b.range)}</td>` +
        `<td style="width:60px;padding:5px 0;font-size:10.5px;font-weight:600;color:${scoreColor(b.key === 'excellent' ? 95 : b.key === 'good' ? 80 : b.key === 'average' ? 65 : b.key === 'poor' ? 45 : 20)};">${escapeHtml(t(`result.pdfReport.scoreBandName.${b.key}`))}</td>` +
        `<td style="padding:5px 0;font-size:10.5px;color:${C.sub};line-height:1.6;">${escapeHtml(t(`result.pdfReport.scoreBandDesc.${b.key}`))}</td>` +
        `</tr>`,
    )
    .join('');

  return (
    blockWrapOpen('padding:8px 0 4px 0;') +
    `<div style="font-size:12px;font-weight:800;color:${C.text};padding-bottom:7px;">${escapeHtml(t('result.pdfReport.legendTitle'))}</div>` +
    `<table style="width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;"><tr>` +
    legendCell('PASS') + legendCell('WARN') + legendCell('FAIL') + legendCell('INFO') +
    `</tr></table>` +
    `<div style="font-size:12px;font-weight:800;color:${C.text};padding:16px 0 5px 0;">${escapeHtml(t('result.pdfReport.bandsTitle'))}</div>` +
    `<table style="width:100%;border-collapse:collapse;table-layout:fixed;">${bandRows}</table>` +
    `<div style="margin-top:16px;padding:12px 14px;background:${C.surface};border:1px solid ${C.line};border-radius:10px;">` +
    `<div style="font-size:11.5px;font-weight:800;color:${C.text};padding-bottom:5px;">${escapeHtml(t('result.pdfReport.appendixSection'))}</div>` +
    `<div style="font-size:10.5px;color:${C.sub};line-height:1.8;">${escapeHtml(t('result.pdfReport.appendixBody'))}</div>` +
    `<div style="font-size:10px;color:${C.mute};line-height:1.7;padding-top:8px;">${escapeHtml(t('result.pdfReport.disclaimer'))}</div>` +
    `</div>` +
    blockWrapClose
  );
};

// ---------- Assembly ----------

const buildDetailBlocks = (
  slice: DimensionSlice,
  accent: string,
  t: TFunction,
  showFix: boolean,
): string[] => {
  const blocks: string[] = [];
  const lockedSet = new Set(slice.result.locked_categories || []);
  const statByTab = new Map(slice.groupStats.map((s) => [s.tab, s]));
  const tabs = [...Object.keys(slice.groups), ...(slice.otherCategories.length ? ['other'] : [])];
  for (const tab of tabs) {
    const cats = tab === 'other' ? slice.otherCategories : slice.groups[tab] || [];
    const visible = cats.filter((c) => !lockedSet.has(c) && (slice.checksByCategory[c]?.length ?? 0) > 0);
    if (visible.length === 0) continue;
    blocks.push(buildTabHeadingBlock(tab, statByTab.get(tab), accent, t));
    for (const cat of visible) {
      blocks.push(buildCategoryBlock(cat, slice.checksByCategory[cat] || [], t, showFix));
    }
  }
  return blocks;
};

/**
 * Produce the section HTML of the whole report, in order.
 *
 * Shared by both export paths:
 * - Backend Chrome printing (preferred): pages/ResultPrint.tsx stitches these sections into one print document,
 *   and Playwright `page.pdf()` produces a vector PDF
 * - Frontend html2canvas (fallback): composeAndSavePdf screenshots each block and assembles a bitmap PDF
 *
 * So the markup in the sections must work for both - which is also why badges / metric cells insist on SVG
 * (see constraint 2 in the file header): SVG does not misalign in either path.
 */
export const buildReportBlocks = (args: ExportArgs): string[] => {
  const { geo, seo, t } = args;
  const showFix = FIX_ALLOWED_TIERS.has((geo.result.tier || '').toLowerCase());
  const blocks: string[] = [];

  // 1 Cover (full page)
  blocks.push(buildCoverBlock(args));

  // 2 Executive summary
  blocks.push(buildSectionBand('1', t('result.pdfReport.summarySection'), t('result.pdfReport.summaryNote'), C.geo, true));
  blocks.push(buildSummaryBlock(args));
  blocks.push(buildFindingsBlock(args));
  const lockedCount = new Set([
    ...(geo.result.locked_categories || []),
    ...(seo?.result.locked_categories || []),
  ]).size;
  if (lockedCount > 0) blocks.push(buildLockedNoticeBlock(lockedCount, t));

  // 3 Category score overview
  blocks.push(buildSectionBand('2', t('result.pdfReport.groupSection'), t('result.pdfReport.groupNote'), C.geo, true));
  blocks.push(buildGroupTableBlock(t('result.dimensions.geo.scoreLabel'), geo.groupStats, C.geo, t));
  if (seo) {
    blocks.push(buildGroupTableBlock(t('result.dimensions.seo.scoreLabel'), seo.groupStats, C.seoDeep, t));
  }

  // 4 Priority fix list (paid entitlement)
  if (showFix) {
    const items: FixItem[] = [
      ...geo.result.checks.filter((c) => c.status === 'FAIL' || c.status === 'WARN').map((check) => ({ check, dim: 'geo' as const })),
      ...(seo?.result.checks || []).filter((c) => c.status === 'FAIL' || c.status === 'WARN').map((check) => ({ check, dim: 'seo' as const })),
    ]
      .sort((a, b) => (STATUS_RANK[a.check.status] ?? 9) - (STATUS_RANK[b.check.status] ?? 9))
      .slice(0, 12);

    blocks.push(buildSectionBand('3', t('result.pdfReport.recommendationsSection'), t('result.pdfReport.topFixesIntro'), '#ec4899', true));
    if (items.length === 0) {
      blocks.push(
        blockWrapOpen('padding:8px 0 4px 0;') +
          `<div style="font-size:11.5px;color:${C.sub};padding:14px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;">${escapeHtml(t('result.pdfReport.noFailItems'))}</div>` +
          blockWrapClose,
      );
    } else {
      items.forEach((item, i) => blocks.push(buildFixItemBlock(item, i, t)));
    }
  }

  // 5 / 6 Audit details
  const detailIndex = showFix ? 4 : 3;
  blocks.push(
    buildSectionBand(String(detailIndex), t('result.pdfReport.detailGeoSection'), t('result.pdfReport.detailNote'), C.geo, true),
  );
  blocks.push(...buildDetailBlocks(geo, C.geoDeep, t, showFix));

  if (seo) {
    blocks.push(
      buildSectionBand(String(detailIndex + 1), t('result.pdfReport.detailSeoSection'), t('result.pdfReport.detailNote'), C.seoDeep, true),
    );
    blocks.push(...buildDetailBlocks(seo, C.seoDeep, t, showFix));
  }

  // 7 Appendix
  blocks.push(
    buildSectionBand(String(detailIndex + (seo ? 2 : 1)), t('result.pdfReport.legendSection'), '', '#64748b', true),
  );
  blocks.push(buildAppendixBlock(t));
  return blocks;
};

// ---------- Entry point ----------

export async function exportPdfReport(args: ExportArgs): Promise<void> {
  const { geo, t } = args;
  const htmlBlocks = buildReportBlocks(args);
  const safeUrl = (geo.result.url || 'site').replace(/[^a-zA-Z0-9]/g, '-').slice(0, 60);
  const baseName = t('result.pdfReport.fileName');
  const headerRight = `${t('result.pdfReport.headerSite')}: ${geo.result.url || ''}`;
  await composeAndSavePdf(
    htmlBlocks,
    { rightHeaderText: headerRight, t },
    `${baseName}-${safeUrl}.pdf`,
  );
}
