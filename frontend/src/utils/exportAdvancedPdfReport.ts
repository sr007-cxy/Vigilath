// Per-mode PDF templates for the 6 advanced check modes. Each mode has
// its own buildBlocks function that returns an array of HTML blocks; the
// shared pipeline in pdfPrimitives.ts captures them, paginates, and saves.
//
// Different modes show different things — e.g. visibility highlights an
// AI score with dimension breakdown, crawlTest highlights crawler access
// rather than a score, compare shows a multi-site leaderboard.

import type { TFunction } from 'i18next';
import type {
  AdvancedMode,
  AiVisibilityResponse,
  AuthorityAuditResponse,
  CitationCheckResponse,
  CompareResponse,
  CrawlTestResponse,
  EntityAuditResponse,
} from '../types/advanced';
import {
  blockWrapClose,
  blockWrapOpen,
  composeAndSavePdf,
  escapeHtml,
  makeStandardHeaderFooter,
} from './pdfPrimitives';

// ---------- Shared building blocks ----------

const fmtPercent = (n: number): string => `${Math.round(n)}%`;
const fmtScore = (earned: number, max: number): string => `${earned}/${max}`;
const safeFile = (s: string): string => s.replace(/[^a-zA-Z0-9]/g, '-').slice(0, 60) || 'site';

const todayStamp = (): string => {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
};

const localeFor = (language: string): string => (language.startsWith('zh') ? 'zh-CN' : 'en-US');

interface CoverRow { label: string; value: string; }

const coverBlock = (args: {
  badge: string;
  title: string;
  subtitle?: string;
  rows: CoverRow[];
  accent?: string;
}): string => {
  const accent = args.accent || '#0ea5e9';
  const tableRows = args.rows
    .map(
      (r) =>
        `<tr>` +
        `<td style="padding:3px 0;width:120px;color:#94a3b8;">${escapeHtml(r.label)}</td>` +
        `<td style="padding:3px 0;font-weight:600;color:#0f172a;word-break:break-all;">${escapeHtml(r.value)}</td>` +
        `</tr>`,
    )
    .join('');
  return (
    blockWrapOpen(`padding:4px 0 18px 0;border-bottom:3px solid ${accent};margin-bottom:8px;`) +
    `<div style="font-size:11px;letter-spacing:0.22em;color:${accent};text-transform:uppercase;font-weight:700;margin-bottom:6px;">${escapeHtml(args.badge)}</div>` +
    `<div style="font-size:24px;font-weight:800;line-height:1.25;margin-bottom:6px;color:#0f172a;">${escapeHtml(args.title)}</div>` +
    (args.subtitle
      ? `<div style="font-size:12px;color:#475569;margin-bottom:14px;line-height:1.5;">${escapeHtml(args.subtitle)}</div>`
      : '') +
    `<table style="width:100%;font-size:11.5px;color:#334155;border-collapse:collapse;">${tableRows}</table>` +
    blockWrapClose
  );
};

const sectionHeading = (text: string, accent = '#0ea5e9'): string =>
  blockWrapOpen('padding:10px 0 4px 0;') +
  `<div style="font-size:15px;font-weight:800;color:#0f172a;border-left:4px solid ${accent};padding-left:10px;">${escapeHtml(text)}</div>` +
  blockWrapClose;

type StatTone = 'good' | 'warn' | 'bad' | 'neutral';
const TONE_BG: Record<StatTone, { bg: string; fg: string }> = {
  good: { bg: '#dcfce7', fg: '#166534' },
  warn: { bg: '#fef3c7', fg: '#92400e' },
  bad: { bg: '#fee2e2', fg: '#991b1b' },
  neutral: { bg: '#dbeafe', fg: '#1e40af' },
};

interface SubStat { label: string; value: string | number; tone?: StatTone; }

// Hero block: dark gradient score card on the left, optional sub-stats grid on the right.
const scoreHero = (args: {
  scoreLabel: string;
  scoreValue: string | number;
  scoreSuffix?: string;
  grade?: string;
  gradeNote?: string;
  rightStats?: SubStat[];
  rightCaption?: string;
}): string => {
  const grade = args.grade
    ? `<div style="margin-top:10px;display:inline-block;padding:5px 16px;border-radius:999px;background:rgba(6,182,212,0.15);border:1px solid rgba(6,182,212,0.4);font-size:15px;font-weight:700;color:#22d3ee;">${escapeHtml(args.grade)}${args.gradeNote ? ` · ${escapeHtml(args.gradeNote)}` : ''}</div>`
    : '';
  const tiles = (args.rightStats || [])
    .map((s) => {
      const c = TONE_BG[s.tone || 'neutral'];
      return `<div style="flex:1;background:${c.bg};border-radius:8px;padding:10px 6px;text-align:center;"><div style="font-size:18px;font-weight:800;color:${c.fg};">${escapeHtml(s.value)}</div><div style="font-size:9px;color:${c.fg};font-weight:600;margin-top:3px;">${escapeHtml(s.label)}</div></div>`;
    })
    .join('');
  const right =
    args.rightStats && args.rightStats.length > 0
      ? `<div style="flex:1;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:14px;box-sizing:border-box;">` +
        (args.rightCaption
          ? `<div style="font-size:11px;font-weight:700;color:#0f172a;margin-bottom:8px;">${escapeHtml(args.rightCaption)}</div>`
          : '') +
        `<div style="display:flex;gap:8px;">${tiles}</div>` +
        `</div>`
      : '';
  return (
    blockWrapOpen('padding:0 0 8px 0;') +
    `<div style="display:flex;gap:14px;align-items:stretch;">` +
    `<div style="flex-shrink:0;width:180px;background:linear-gradient(135deg,#0f172a,#1e293b);color:#fff;border-radius:12px;padding:18px;text-align:center;box-sizing:border-box;">` +
    `<div style="font-size:10px;letter-spacing:0.16em;color:#94a3b8;text-transform:uppercase;margin-bottom:6px;">${escapeHtml(args.scoreLabel)}</div>` +
    `<div style="font-size:42px;font-weight:800;line-height:1;color:#22d3ee;">${escapeHtml(args.scoreValue)}</div>` +
    (args.scoreSuffix ? `<div style="font-size:11px;color:#94a3b8;margin-top:4px;">${escapeHtml(args.scoreSuffix)}</div>` : '') +
    grade +
    `</div>` +
    right +
    `</div>` +
    blockWrapClose
  );
};

// Generic key→value table.
const kvTable = (rows: Array<{ k: string; v: string; vColor?: string }>): string => {
  if (rows.length === 0) return '';
  const body = rows
    .map(
      (r) =>
        `<tr>` +
        `<td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#475569;font-size:11px;width:40%;">${escapeHtml(r.k)}</td>` +
        `<td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:${r.vColor || '#0f172a'};font-size:11.5px;font-weight:600;">${escapeHtml(r.v)}</td>` +
        `</tr>`,
    )
    .join('');
  return (
    blockWrapOpen('padding:4px 0 8px 0;') +
    `<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">${body}</table>` +
    blockWrapClose
  );
};

interface DataTableCell { value: string; align?: 'left' | 'center' | 'right'; color?: string; bold?: boolean; }
type DataRow = Array<string | DataTableCell>;

const dataTable = (headers: string[], rows: DataRow[]): string => {
  if (rows.length === 0) return '';
  const head = headers
    .map(
      (h, i) =>
        `<th style="padding:9px 10px;text-align:${i === 0 ? 'left' : 'center'};font-size:10px;color:#475569;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;">${escapeHtml(h)}</th>`,
    )
    .join('');
  const body = rows
    .map(
      (r) =>
        `<tr>${r
          .map((cell, i) => {
            if (typeof cell === 'string') {
              return `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:${i === 0 ? 'left' : 'center'};color:#0f172a;${i === 0 ? 'font-weight:600;' : ''}">${escapeHtml(cell)}</td>`;
            }
            const align = cell.align || (i === 0 ? 'left' : 'center');
            const color = cell.color || '#0f172a';
            const bold = cell.bold ? 'font-weight:700;' : '';
            return `<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:${align};color:${color};${bold}">${escapeHtml(cell.value)}</td>`;
          })
          .join('')}</tr>`,
    )
    .join('');
  return (
    blockWrapOpen('padding:4px 0 10px 0;') +
    `<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;font-size:11.5px;">` +
    `<thead><tr style="background:#f1f5f9;">${head}</tr></thead>` +
    `<tbody>${body}</tbody>` +
    `</table>` +
    blockWrapClose
  );
};

const callout = (text: string, tone: StatTone | 'neutral' = 'neutral'): string => {
  const c = TONE_BG[tone];
  const borderMap: Record<StatTone, string> = {
    good: '#10b981',
    warn: '#f59e0b',
    bad: '#ef4444',
    neutral: '#3b82f6',
  };
  return (
    blockWrapOpen('padding:2px 0 4px 0;') +
    `<div style="font-size:11px;color:${c.fg};background:${c.bg};border-left:3px solid ${borderMap[tone]};padding:10px 12px;border-radius:6px;line-height:1.6;">${escapeHtml(text)}</div>` +
    blockWrapClose
  );
};

const bulletList = (heading: string, items: string[], emptyText?: string): string => {
  if (items.length === 0 && !emptyText) return '';
  const body =
    items.length > 0
      ? `<ul style="margin:0;padding:0 0 0 18px;font-size:11.5px;color:#334155;line-height:1.7;">${items
          .map((i) => `<li style="margin-bottom:3px;">${escapeHtml(i)}</li>`)
          .join('')}</ul>`
      : `<div style="font-size:11px;color:#94a3b8;font-style:italic;">${escapeHtml(emptyText || '')}</div>`;
  return (
    blockWrapOpen('padding:6px 0 4px 0;') +
    `<div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:6px;">${escapeHtml(heading)}</div>` +
    body +
    blockWrapClose
  );
};

// Tone derivation from a 0-100 percent.
const toneForPercent = (p: number): StatTone => (p >= 75 ? 'good' : p >= 50 ? 'warn' : 'bad');

// ---------- compare ----------

const buildCompareBlocks = (data: CompareResponse, t: TFunction, language: string): string[] => {
  const blocks: string[] = [];
  const generatedAt = new Date().toLocaleString(localeFor(language));
  const subtitle = t('home.advanced.cards.compare.desc', { defaultValue: '' }) as string;

  blocks.push(
    coverBlock({
      badge: t('home.advanced.cards.compare.title', { defaultValue: 'Competitive Comparison' }) as string,
      title: t('result.advancedPdf.compare.title', { defaultValue: 'Competitive GEO Comparison' }) as string,
      subtitle,
      rows: [
        { label: t('result.advancedPdf.urlsCount', { defaultValue: 'URLs compared' }) as string, value: String(data.urls.length) },
        { label: t('result.pdfReport.generatedAt') as string, value: generatedAt },
      ],
      accent: '#06b6d4',
    }),
  );

  if (data.winner) {
    blocks.push(
      scoreHero({
        scoreLabel: t('result.advancedPdf.compare.winner', { defaultValue: 'Winner' }) as string,
        scoreValue: data.winner.score,
        scoreSuffix: '/ 100',
        grade: data.winner.grade,
        gradeNote: data.winner.domain,
        rightStats: [
          { label: t('result.advancedPdf.compare.lead', { defaultValue: 'Lead (pts)' }) as string, value: data.winner.lead, tone: 'good' },
          { label: t('result.advancedPdf.compare.contestants', { defaultValue: 'Sites' }) as string, value: data.results.length, tone: 'neutral' },
        ],
      }),
    );
  }

  blocks.push(
    sectionHeading(t('result.advancedPdf.compare.leaderboard', { defaultValue: 'Site Leaderboard' }) as string, '#06b6d4'),
  );
  const sorted = [...data.results].sort((a, b) => b.score - a.score);
  blocks.push(
    dataTable(
      [
        '#',
        t('result.advancedPdf.compare.domain', { defaultValue: 'Domain' }) as string,
        t('result.advancedPdf.compare.score', { defaultValue: 'Score' }) as string,
        t('result.advancedPdf.compare.grade', { defaultValue: 'Grade' }) as string,
      ],
      sorted.map((r, i) => [
        { value: String(i + 1), align: 'center' as const },
        { value: r.domain, align: 'left' as const, bold: true },
        { value: String(r.score), align: 'center' as const, bold: true, color: i === 0 ? '#166534' : '#0f172a' },
        { value: r.grade, align: 'center' as const, color: i === 0 ? '#166534' : '#0f172a' },
      ]),
    ),
  );

  if (data.advantages && data.advantages.length > 0) {
    blocks.push(
      sectionHeading(
        t('result.advancedPdf.compare.advantages', { defaultValue: 'Per-Category Leaders' }) as string,
        '#8b5cf6',
      ),
    );
    blocks.push(
      dataTable(
        [
          t('home.advanced.result.compare.category', { defaultValue: 'Category' }) as string,
          t('result.advancedPdf.compare.topDomain', { defaultValue: 'Top domain' }) as string,
          t('result.advancedPdf.compare.share', { defaultValue: 'Share' }) as string,
        ],
        data.advantages.map((adv) => {
          const top = adv.ranked[0];
          return [
            adv.category,
            { value: top?.domain || '—', align: 'center' as const, bold: true },
            { value: top ? fmtPercent(top.percent) : '—', align: 'center' as const },
          ];
        }),
      ),
    );
  }

  return blocks;
};

// ---------- crawlTest ----------

const buildCrawlTestBlocks = (data: CrawlTestResponse, t: TFunction, language: string): string[] => {
  const blocks: string[] = [];
  const generatedAt = new Date().toLocaleString(localeFor(language));

  blocks.push(
    coverBlock({
      badge: t('home.advanced.cards.crawlTest.title', { defaultValue: 'AI Crawler Test' }) as string,
      title: t('result.advancedPdf.crawlTest.title', { defaultValue: 'AI Crawler Accessibility' }) as string,
      subtitle: t('home.advanced.cards.crawlTest.desc', { defaultValue: '' }) as string,
      rows: [
        { label: t('result.pdfReport.targetSite') as string, value: data.url },
        { label: t('home.advanced.result.crawl.targetDomain') as string, value: data.domain },
        { label: t('result.pdfReport.generatedAt') as string, value: generatedAt },
      ],
      accent: '#8b5cf6',
    }),
  );

  const robotsAllowed = data.robots.bots.filter((b) => b.status === 'allowed' || b.status === 'allow').length;
  const wafPassed = data.waf.bots.filter((b) => b.result === 'pass' || b.result === 'ok').length;
  const issuesTone: StatTone = data.total_issues === 0 ? 'good' : data.total_issues > 3 ? 'bad' : 'warn';

  blocks.push(
    scoreHero({
      scoreLabel: t('home.advanced.result.crawl.totalIssues') as string,
      scoreValue: data.total_issues,
      scoreSuffix: data.total_issues === 0
        ? (t('home.advanced.result.crawl.allClear') as string)
        : (t('home.advanced.result.crawl.needsFix') as string),
      grade: data.total_issues === 0 ? 'A' : data.total_issues > 3 ? 'F' : 'C',
      rightStats: [
        { label: t('home.advanced.result.crawl.robotsAllowed') as string, value: `${robotsAllowed}/${data.robots.bots.length}`, tone: 'neutral' },
        { label: t('home.advanced.result.crawl.wafAllowed') as string, value: `${wafPassed}/${data.waf.bots.length}`, tone: issuesTone },
      ],
    }),
  );

  // robots.txt section
  blocks.push(sectionHeading(t('home.advanced.result.crawl.robotsTitle') as string, '#8b5cf6'));
  if (!data.robots.found) {
    blocks.push(callout(t('home.advanced.result.crawl.robotsMissingWarning') as string, 'warn'));
  } else {
    blocks.push(
      dataTable(
        [
          t('result.advancedPdf.crawlTest.bot', { defaultValue: 'Bot' }) as string,
          t('result.advancedPdf.crawlTest.userAgent', { defaultValue: 'User-Agent' }) as string,
          t('result.advancedPdf.crawlTest.status', { defaultValue: 'Status' }) as string,
        ],
        data.robots.bots.map((b) => {
          const allowed = b.status === 'allowed' || b.status === 'allow';
          return [
            { value: b.bot, align: 'left' as const, bold: true },
            { value: b.ua, align: 'left' as const },
            { value: b.status, align: 'center' as const, color: allowed ? '#166534' : '#991b1b', bold: true },
          ];
        }),
      ),
    );
  }

  // WAF section
  blocks.push(sectionHeading(t('home.advanced.result.crawl.wafTitle') as string, '#ec4899'));
  blocks.push(
    callout(
      t('home.advanced.result.crawl.wafBaseline', {
        status: data.waf.baseline_status ?? '—',
        size: Math.round(data.waf.baseline_size / 1024),
      }) as string,
      'neutral',
    ),
  );
  blocks.push(
    dataTable(
      [
        t('result.advancedPdf.crawlTest.bot', { defaultValue: 'Bot' }) as string,
        t('result.advancedPdf.crawlTest.statusCode', { defaultValue: 'HTTP' }) as string,
        t('result.advancedPdf.crawlTest.sizeKb', { defaultValue: 'Size (KB)' }) as string,
        t('result.advancedPdf.crawlTest.result', { defaultValue: 'Result' }) as string,
      ],
      data.waf.bots.map((b) => {
        const ok = b.result === 'pass' || b.result === 'ok';
        return [
          { value: b.bot, align: 'left' as const, bold: true },
          { value: b.status_code !== null ? String(b.status_code) : '—', align: 'center' as const },
          { value: String(Math.round(b.size / 1024)), align: 'center' as const },
          { value: b.error || b.result, align: 'center' as const, color: ok ? '#166534' : '#991b1b', bold: true },
        ];
      }),
    ),
  );

  // Common Crawl section
  blocks.push(sectionHeading(t('home.advanced.result.crawl.commonCrawlTitle') as string, '#0ea5e9'));
  if (data.common_crawl.found) {
    blocks.push(
      callout(
        `${t('home.advanced.result.crawl.foundInCcPrefix') as string}${data.common_crawl.count}${t('home.advanced.result.crawl.foundInCcSuffix') as string}`,
        'good',
      ),
    );
    if (data.common_crawl.samples.length > 0) {
      blocks.push(
        bulletList(
          t('result.advancedPdf.crawlTest.ccSamples', { defaultValue: 'Sample indexed pages' }) as string,
          data.common_crawl.samples.slice(0, 8),
        ),
      );
    }
  } else {
    blocks.push(callout(t('home.advanced.result.crawl.notIndexed') as string, 'warn'));
  }

  return blocks;
};

// ---------- authority ----------

const buildAuthorityBlocks = (data: AuthorityAuditResponse, t: TFunction, language: string): string[] => {
  const blocks: string[] = [];
  const generatedAt = new Date().toLocaleString(localeFor(language));

  blocks.push(
    coverBlock({
      badge: t('home.advanced.cards.authority.title', { defaultValue: 'Authority Audit' }) as string,
      title: t('result.advancedPdf.authority.title', { defaultValue: 'Off-Page Authority Audit' }) as string,
      subtitle: t('home.advanced.cards.authority.desc', { defaultValue: '' }) as string,
      rows: [
        { label: t('result.pdfReport.targetSite') as string, value: data.url },
        { label: t('result.advancedPdf.brand', { defaultValue: 'Brand' }) as string, value: data.brand },
        { label: t('result.pdfReport.generatedAt') as string, value: generatedAt },
      ],
      accent: '#f59e0b',
    }),
  );

  blocks.push(
    scoreHero({
      scoreLabel: t('result.advancedPdf.authority.score', { defaultValue: 'Authority Score' }) as string,
      scoreValue: fmtPercent(data.percent),
      scoreSuffix: fmtScore(data.score, data.max_score),
      grade: data.grade,
      rightStats: [
        {
          label: t('result.advancedPdf.authority.reviews', { defaultValue: 'Review platforms' }) as string,
          value: data.reviews.platforms.length,
          tone: data.reviews.platforms.length >= 3 ? 'good' : 'warn',
        },
        {
          label: t('result.advancedPdf.authority.awards', { defaultValue: 'Award signals' }) as string,
          value: data.awards.signal_count,
          tone: data.awards.signal_count >= 2 ? 'good' : 'neutral',
        },
        {
          label: t('result.advancedPdf.authority.kg', { defaultValue: 'Knowledge Graph' }) as string,
          value: data.google.wikipedia_or_wikidata ? '✓' : '—',
          tone: data.google.wikipedia_or_wikidata ? 'good' : 'warn',
        },
      ],
    }),
  );

  // Reviews
  blocks.push(sectionHeading(t('result.advancedPdf.authority.reviewsSection', { defaultValue: 'Review Coverage' }) as string, '#f59e0b'));
  blocks.push(
    kvTable([
      {
        k: t('result.advancedPdf.authority.platforms', { defaultValue: 'Platforms found' }) as string,
        v: data.reviews.platforms.length > 0 ? data.reviews.platforms.join(', ') : '—',
      },
      {
        k: t('result.advancedPdf.authority.reviewSchema', { defaultValue: 'Review schema present' }) as string,
        v: data.reviews.has_schema ? '✓' : '—',
        vColor: data.reviews.has_schema ? '#166534' : '#991b1b',
      },
    ]),
  );

  // Awards
  blocks.push(sectionHeading(t('result.advancedPdf.authority.awardsSection', { defaultValue: 'Awards & Trust Signals' }) as string, '#ef4444'));
  blocks.push(
    kvTable([
      {
        k: t('result.advancedPdf.authority.awardKeywords', { defaultValue: 'Keywords detected' }) as string,
        v: data.awards.keywords.length > 0 ? data.awards.keywords.slice(0, 6).join(', ') : '—',
      },
      {
        k: t('result.advancedPdf.authority.badges', { defaultValue: 'Badges' }) as string,
        v: data.awards.badges.length > 0 ? data.awards.badges.slice(0, 6).join(', ') : '—',
      },
      {
        k: t('result.advancedPdf.authority.signalCount', { defaultValue: 'Total signals' }) as string,
        v: String(data.awards.signal_count),
      },
    ]),
  );

  // Google KG
  blocks.push(sectionHeading(t('result.advancedPdf.authority.googleSection', { defaultValue: 'Google Knowledge Graph' }) as string, '#0ea5e9'));
  blocks.push(
    kvTable([
      {
        k: t('result.advancedPdf.authority.indexedPages', { defaultValue: 'Indexed pages (estimate)' }) as string,
        v: data.google.indexed_pages !== null ? String(data.google.indexed_pages) : '—',
      },
      {
        k: t('result.advancedPdf.authority.kgFields', { defaultValue: 'KG schema fields' }) as string,
        v: String(data.google.kg_schema_fields),
      },
      {
        k: t('result.advancedPdf.authority.wikipedia', { defaultValue: 'Wikipedia / Wikidata' }) as string,
        v: data.google.wikipedia_or_wikidata ? '✓' : '—',
        vColor: data.google.wikipedia_or_wikidata ? '#166534' : '#991b1b',
      },
    ]),
  );

  // Mentions
  if (data.mentions.platforms.length > 0) {
    blocks.push(sectionHeading(t('result.advancedPdf.authority.mentionsSection', { defaultValue: 'Brand Mentions' }) as string, '#8b5cf6'));
    blocks.push(
      bulletList(
        t('result.advancedPdf.authority.mentionPlatforms', { defaultValue: 'Mentioned on' }) as string,
        data.mentions.platforms,
      ),
    );
  }

  return blocks;
};

// ---------- citation ----------

const buildCitationBlocks = (data: CitationCheckResponse, t: TFunction, language: string): string[] => {
  const blocks: string[] = [];
  const generatedAt = new Date().toLocaleString(localeFor(language));

  blocks.push(
    coverBlock({
      badge: t('home.advanced.cards.citation.title', { defaultValue: 'AI Citation Check' }) as string,
      title: t('result.advancedPdf.citation.title', { defaultValue: 'AI Citation Check' }) as string,
      subtitle: t('home.advanced.cards.citation.desc', { defaultValue: '' }) as string,
      rows: [
        { label: t('result.pdfReport.targetSite') as string, value: data.url },
        { label: t('result.advancedPdf.brand', { defaultValue: 'Brand' }) as string, value: data.brand },
        { label: t('result.advancedPdf.engine', { defaultValue: 'Engine' }) as string, value: data.engine },
        { label: t('result.pdfReport.generatedAt') as string, value: generatedAt },
      ],
      accent: '#10b981',
    }),
  );

  blocks.push(
    scoreHero({
      scoreLabel: t('home.advanced.result.citation.overallScore') as string,
      scoreValue: fmtPercent(data.citation_rate * 100),
      scoreSuffix: t('home.advanced.result.citation.queriesSuffix', { count: data.total_queries }) as string,
      grade: data.grade,
      rightStats: [
        {
          label: t('home.advanced.result.citation.cited') as string,
          value: `${data.cited_queries}/${data.valid_queries}`,
          tone: toneForPercent((data.cited_queries / Math.max(1, data.valid_queries)) * 100),
        },
        {
          label: t('home.advanced.result.citation.directCitations') as string,
          value: data.total_citations,
          tone: data.total_citations > 0 ? 'good' : 'bad',
        },
      ],
    }),
  );

  blocks.push(sectionHeading(t('home.advanced.result.citation.perQuery') as string, '#10b981'));
  blocks.push(
    dataTable(
      [
        '#',
        t('result.advancedPdf.citation.query', { defaultValue: 'Query' }) as string,
        t('home.advanced.result.citation.cited') as string,
        t('result.advancedPdf.citation.citationCount', { defaultValue: 'Citations' }) as string,
      ],
      data.queries.map((q, i) => [
        { value: String(i + 1), align: 'center' as const },
        { value: q.query, align: 'left' as const },
        {
          value: q.cited ? '✓' : '—',
          align: 'center' as const,
          color: q.cited ? '#166534' : '#991b1b',
          bold: true,
        },
        { value: String(q.citations.length), align: 'center' as const },
      ]),
    ),
  );

  return blocks;
};

// ---------- visibility ----------

const buildVisibilityBlocks = (data: AiVisibilityResponse, t: TFunction, language: string): string[] => {
  const blocks: string[] = [];
  const generatedAt = new Date().toLocaleString(localeFor(language));

  blocks.push(
    coverBlock({
      badge: t('home.advanced.cards.visibility.title', { defaultValue: 'AI Visibility Audit' }) as string,
      title: t('result.advancedPdf.visibility.title', { defaultValue: 'Multi-Engine AI Visibility Audit' }) as string,
      subtitle: t('home.advanced.cards.visibility.desc', { defaultValue: '' }) as string,
      rows: [
        { label: t('result.pdfReport.targetSite') as string, value: data.url },
        { label: t('result.advancedPdf.brand', { defaultValue: 'Brand' }) as string, value: data.brand },
        {
          label: t('result.advancedPdf.engines', { defaultValue: 'Engines' }) as string,
          value: data.engines.join(', '),
        },
        { label: t('result.pdfReport.generatedAt') as string, value: generatedAt },
      ],
      accent: '#a855f7',
    }),
  );

  blocks.push(
    scoreHero({
      scoreLabel: t('result.advancedPdf.visibility.score', { defaultValue: 'Visibility Score' }) as string,
      scoreValue: fmtScore(data.total_score, data.max_score),
      scoreSuffix: t('home.advanced.result.visibility.queryBreakdown', {
        count: data.query_count,
        runs: data.stability_runs,
      }) as string,
      grade: data.grade,
      gradeNote: data.grade_label,
      rightStats: [
        { label: t('result.advancedPdf.visibility.dimVisibility', { defaultValue: 'Visibility' }) as string, value: data.scores.visibility, tone: 'neutral' },
        { label: t('result.advancedPdf.visibility.dimEntity', { defaultValue: 'Entity' }) as string, value: data.scores.entity, tone: 'neutral' },
        { label: t('result.advancedPdf.visibility.dimStability', { defaultValue: 'Stability' }) as string, value: data.scores.stability, tone: 'neutral' },
      ],
    }),
  );

  // Dimension scores
  blocks.push(sectionHeading(t('result.advancedPdf.visibility.dimensionScores', { defaultValue: 'Dimension Scores' }) as string, '#a855f7'));
  blocks.push(
    kvTable([
      { k: t('result.advancedPdf.visibility.dimVisibility', { defaultValue: 'Visibility' }) as string, v: String(data.scores.visibility) },
      { k: t('result.advancedPdf.visibility.dimEntity', { defaultValue: 'Entity' }) as string, v: String(data.scores.entity) },
      { k: t('result.advancedPdf.visibility.dimCompetitor', { defaultValue: 'Competitor' }) as string, v: String(data.scores.competitor) },
      { k: t('result.advancedPdf.visibility.dimStability', { defaultValue: 'Stability' }) as string, v: String(data.scores.stability) },
      { k: t('result.advancedPdf.visibility.dimContentGap', { defaultValue: 'Content gap' }) as string, v: String(data.scores.content_gap) },
    ]),
  );

  // Per-engine rates
  blocks.push(sectionHeading(t('home.advanced.result.visibility.perEngineRate') as string, '#06b6d4'));
  blocks.push(
    dataTable(
      [
        t('result.advancedPdf.visibility.engine', { defaultValue: 'Engine' }) as string,
        t('result.advancedPdf.visibility.rate', { defaultValue: 'Visibility' }) as string,
      ],
      Object.entries(data.per_engine_rates).map(([engine, rate]) => [
        { value: engine, align: 'left' as const, bold: true },
        {
          value: fmtPercent(rate * 100),
          align: 'center' as const,
          color: rate >= 0.5 ? '#166534' : rate >= 0.25 ? '#92400e' : '#991b1b',
          bold: true,
        },
      ]),
    ),
  );

  // Competitors
  if (data.top_competitors.length > 0) {
    blocks.push(sectionHeading(t('home.advanced.result.visibility.competitors') as string, '#ec4899'));
    blocks.push(
      dataTable(
        [
          t('result.advancedPdf.compare.domain', { defaultValue: 'Domain' }) as string,
          t('result.advancedPdf.visibility.mentions', { defaultValue: 'Mentions' }) as string,
        ],
        data.top_competitors.slice(0, 10).map((c) => [
          { value: c.domain, align: 'left' as const, bold: true },
          { value: String(c.mentions), align: 'center' as const },
        ]),
      ),
    );
  }

  // Framings
  if (Object.keys(data.framings).length > 0) {
    blocks.push(sectionHeading(t('home.advanced.result.visibility.framings') as string, '#f59e0b'));
    blocks.push(
      kvTable(
        Object.entries(data.framings).map(([k, v]) => ({ k, v: String(v) })),
      ),
    );
  }

  // Content gaps
  if (data.content_gaps.length > 0) {
    blocks.push(sectionHeading(t('home.advanced.result.visibility.contentGaps') as string, '#8b5cf6'));
    blocks.push(
      bulletList(
        t('result.advancedPdf.visibility.gapsHeader', { defaultValue: 'Topics where competitors win' }) as string,
        data.content_gaps,
      ),
    );
  }

  return blocks;
};

// ---------- entity ----------

const buildEntityBlocks = (data: EntityAuditResponse, t: TFunction, language: string): string[] => {
  const blocks: string[] = [];
  const generatedAt = new Date().toLocaleString(localeFor(language));

  blocks.push(
    coverBlock({
      badge: t('home.advanced.cards.entity.title', { defaultValue: 'Entity GEO Audit' }) as string,
      title: t('result.advancedPdf.entity.title', { defaultValue: 'Entity Recognition Audit' }) as string,
      subtitle: t('home.advanced.cards.entity.desc', { defaultValue: '' }) as string,
      rows: [
        { label: t('result.advancedPdf.entity.name', { defaultValue: 'Entity' }) as string, value: data.entity },
        { label: t('result.advancedPdf.entity.type', { defaultValue: 'Type' }) as string, value: data.entity_type },
        { label: t('result.pdfReport.generatedAt') as string, value: generatedAt },
      ],
      accent: '#ec4899',
    }),
  );

  blocks.push(
    scoreHero({
      scoreLabel: t('result.advancedPdf.entity.score', { defaultValue: 'Entity Score' }) as string,
      scoreValue: fmtPercent(data.percent),
      scoreSuffix: fmtScore(data.total_score, data.max_score),
      grade: data.grade,
      rightStats: [
        {
          label: t('home.advanced.result.entity.recognitionRate') as string,
          value: fmtPercent(data.recognition_rate * 100),
          tone: toneForPercent(data.recognition_rate * 100),
        },
        {
          label: t('result.advancedPdf.entity.platformsFound', { defaultValue: 'Platforms' }) as string,
          value: data.platforms.found.length,
          tone: data.platforms.found.length >= 3 ? 'good' : 'warn',
        },
      ],
    }),
  );

  // Knowledge graph
  blocks.push(sectionHeading(t('home.advanced.result.entity.kgTitle') as string, '#ec4899'));
  blocks.push(
    kvTable([
      {
        k: 'Wikipedia',
        v: data.knowledge_graph.wikipedia ? '✓' : '—',
        vColor: data.knowledge_graph.wikipedia ? '#166534' : '#991b1b',
      },
      {
        k: 'Wikidata',
        v: data.knowledge_graph.wikidata
          ? data.knowledge_graph.wikidata_id || '✓'
          : '—',
        vColor: data.knowledge_graph.wikidata ? '#166534' : '#991b1b',
      },
      {
        k: t('result.advancedPdf.entity.googleKg', { defaultValue: 'Google KG' }) as string,
        v: data.knowledge_graph.google_kg ? '✓' : '—',
        vColor: data.knowledge_graph.google_kg ? '#166534' : '#991b1b',
      },
    ]),
  );

  // Platforms
  blocks.push(sectionHeading(t('home.advanced.result.entity.platforms') as string, '#f59e0b'));
  blocks.push(
    bulletList(
      t('result.advancedPdf.entity.platformsCovered', { defaultValue: 'Covered platforms' }) as string,
      data.platforms.found,
      t('result.advancedPdf.entity.noPlatforms', { defaultValue: 'No platforms detected.' }) as string,
    ),
  );
  if (data.platforms.not_found.length > 0) {
    blocks.push(
      bulletList(
        t('result.advancedPdf.entity.platformsMissing', { defaultValue: 'Missing platforms' }) as string,
        data.platforms.not_found,
      ),
    );
  }

  // Sentiment
  blocks.push(sectionHeading(t('home.advanced.result.entity.sentimentTitle') as string, '#8b5cf6'));
  blocks.push(
    kvTable([
      { k: t('home.advanced.result.entity.overallSentiment') as string, v: data.sentiment },
      { k: t('home.advanced.result.entity.bestFraming') as string, v: data.best_framing },
    ]),
  );

  // Content gaps
  if (data.content_gaps.length > 0) {
    blocks.push(sectionHeading(t('home.advanced.result.entity.contentGaps') as string, '#0ea5e9'));
    blocks.push(
      bulletList(
        t('result.advancedPdf.entity.gapsHeader', { defaultValue: 'Topics to cover' }) as string,
        data.content_gaps,
      ),
    );
  }

  return blocks;
};

// ---------- Dispatcher ----------

interface ModeArgsMap {
  compare: CompareResponse;
  crawlTest: CrawlTestResponse;
  authority: AuthorityAuditResponse;
  citation: CitationCheckResponse;
  visibility: AiVisibilityResponse;
  entity: EntityAuditResponse;
}

export interface ExportAdvancedArgs<M extends AdvancedMode> {
  mode: M;
  data: ModeArgsMap[M];
  t: TFunction;
  language: string;
}

const buildBlocksFor = <M extends AdvancedMode>(args: ExportAdvancedArgs<M>): string[] => {
  switch (args.mode) {
    case 'compare':
      return buildCompareBlocks(args.data as CompareResponse, args.t, args.language);
    case 'crawlTest':
      return buildCrawlTestBlocks(args.data as CrawlTestResponse, args.t, args.language);
    case 'authority':
      return buildAuthorityBlocks(args.data as AuthorityAuditResponse, args.t, args.language);
    case 'citation':
      return buildCitationBlocks(args.data as CitationCheckResponse, args.t, args.language);
    case 'visibility':
      return buildVisibilityBlocks(args.data as AiVisibilityResponse, args.t, args.language);
    case 'entity':
      return buildEntityBlocks(args.data as EntityAuditResponse, args.t, args.language);
  }
  return [];
};

// Pick a header-right text + filename subject from each mode's data.
const headerSubjectFor = <M extends AdvancedMode>(args: ExportAdvancedArgs<M>): { headerRight: string; filenameSubject: string } => {
  const { mode, data, t } = args;
  const labelSite = t('result.pdfReport.headerSite') as string;
  switch (mode) {
    case 'compare': {
      const d = data as CompareResponse;
      const winner = d.winner?.domain || d.results[0]?.domain || 'compare';
      return { headerRight: `${labelSite}: ${d.urls.length} URLs`, filenameSubject: winner };
    }
    case 'crawlTest':
    case 'authority':
    case 'citation':
    case 'visibility': {
      const d = data as { url: string; domain?: string; brand?: string };
      return { headerRight: `${labelSite}: ${d.url || ''}`, filenameSubject: d.domain || d.brand || d.url || mode };
    }
    case 'entity': {
      const d = data as EntityAuditResponse;
      return { headerRight: `${t('result.advancedPdf.entity.name', { defaultValue: 'Entity' }) as string}: ${d.entity}`, filenameSubject: d.entity };
    }
  }
  return { headerRight: labelSite, filenameSubject: mode };
};

export async function exportAdvancedPdfReport<M extends AdvancedMode>(
  args: ExportAdvancedArgs<M>,
): Promise<void> {
  const blocks = buildBlocksFor(args);
  const { headerRight, filenameSubject } = headerSubjectFor(args);
  const fileName = `geo-${args.mode}-${safeFile(filenameSubject)}-${todayStamp()}.pdf`;
  await composeAndSavePdf(
    blocks,
    makeStandardHeaderFooter({ rightHeaderText: headerRight, t: args.t }),
    fileName,
  );
}
