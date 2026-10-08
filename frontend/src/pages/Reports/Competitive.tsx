// Competitor comparison analysis — modeled on the reference design: competitor comparison / market competition insights / keyword heat map / product leaderboard.
// Data from /competitive-analysis (aggregated on read; brand = hit/brand_rank, competitors = competitors_json).
// CompetitiveFull is used in the overview; CompetitiveCompact is embedded in daily/weekly/monthly reports.

import { useEffect, useState, type CSSProperties } from 'react';
import {
  PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer,
} from 'recharts';
import { reportsApi, type CompetitiveAnalysis, type HeatWord } from '../../services/reportsApi';
import { engineLabel } from '../../constants/engines';
import { InfoHint, SectionCard, SectionSkeleton } from './components';
import { useReportsLang } from './lang';
import { BoardSections } from './BoardSections';

// Sequential blue scale (dataviz-validated palette 100->700): stronger heat/score is darker, light = near zero, dark = highest
const SEQ_BLUE = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];

export function useCompetitive(topicId: number | undefined, token: string,
  periodType: string, key?: string) {
  const [data, setData] = useState<CompetitiveAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  // nonce: after the heat map collects search heat, refetch in place without refreshing the whole page
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!topicId) return;
    setLoading(true);
    reportsApi.competitiveAnalysis(topicId, token, periodType, key)
      .then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  }, [topicId, token, periodType, key, nonce]);
  return { data, loading, reload: () => setNonce(n => n + 1) };
}

// ── Competitor comparison: grouped comparison bars (overview and daily/weekly/monthly reports share the same BattleMetric) ──

// In-group rank: same yardstick as the product leaderboard (the brand's position in the composite score ranking); falls back to average rank when the leaderboard is abnormal
function brandPos(d: CompetitiveAnalysis): number {
  const i = d.ranking.findIndex(r => r.is_brand);
  return i >= 0 ? i + 1 : (d.avg_rank.current ? Math.round(d.avg_rank.current) : 0);
}

// Head-to-head row data for the three metrics: brand row (diff=null, no direction) + each competitor row (with diff) + industry average row
// The backend diff is already normalized to "positive = we lead"; the rank bar uses the inverse mapping (10 - rank), so higher ranks get longer bars
function buildBattleRows(d: CompetitiveAnalysis, L: ReturnType<typeof useReportsLang>) {
  const ourRank = brandPos(d);
  const cov: BattleRow[] = [
    { name: d.brand, value: d.coverage.current, display: `${d.coverage.current}%`, isBrand: true, diff: null },
    ...d.competitors.map(c => ({
      name: c.name, value: c.coverage, display: `${c.coverage}%`, isBrand: false, diff: c.coverage_diff,
    })),
    { name: L.industryAvg, value: d.industry_avg.coverage, display: `${d.industry_avg.coverage}%`, isBrand: false, diff: null },
  ];
  const men: BattleRow[] = [
    { name: d.brand, value: d.mention.current, display: `${d.mention.current}%`, isBrand: true, diff: null },
    ...d.competitors.map(c => ({
      name: c.name, value: c.mention, display: `${c.mention}%`, isBrand: false, diff: c.mention_diff,
    })),
    { name: L.industryAvg, value: d.industry_avg.mention, display: `${d.industry_avg.mention}%`, isBrand: false, diff: null },
  ];
  const rank: BattleRow[] = [
    { name: d.brand, value: ourRank ? 10 - ourRank : 0.1, display: L.rankValue(ourRank), isBrand: true, diff: null },
    ...d.competitors.map(c => ({
      name: c.name, value: 10 - c.rank, display: L.rankValue(c.rank), isBrand: false, diff: c.rank_diff,
    })),
    { name: L.industryAvg, value: 10 - d.industry_avg.rank, display: L.rankValue(d.industry_avg.rank), isBrand: false, diff: null },
  ];
  return { cov, men, rank };
}

// Inverse rank normalization: the higher the rank, the higher the score ((10-rank)/9), same yardstick as buildBattleRows
const rankScore = (rank: number) => Math.max(0, Math.min(100, ((10 - rank) / 9) * 100));

// ── Competitor comparison data (option A): overlaid radar on the left + three-metric head-to-head cards on the right ──
// Radar five dimensions = overall exposure rate / brand share of voice / product rank score (rankScore inverse-normalized, same yardstick as the head-to-head bars)
// + content neutral-positive rate / article citation rate (true values for the brand, same metric definition as the six metric cards; tier reference estimates for competitors, from the backend);
// Three outlines = us (blue fill) / leading competitor (amber) / industry average (gray dashed), colors from CSS variables, adapting to all three themes.
function CompareRadar({ d }: { d: CompetitiveAnalysis }) {
  const L = useReportsLang();
  const top = d.competitors[0];
  if (!top) return null;
  const data = [
    { dim: L.kwCoverage, me: d.coverage.current, top: top.coverage, ind: d.industry_avg.coverage },
    { dim: L.prodMentionShort, me: d.mention.current, top: top.mention, ind: d.industry_avg.mention },
    { dim: L.prodRank, me: rankScore(brandPos(d)), top: rankScore(top.rank), ind: rankScore(d.industry_avg.rank) },
  ];
  // With an old backend lacking these two fields, keep three dimensions instead of drawing fake zero-value outlines
  if (d.brand_accuracy != null && top.accuracy != null) {
    data.push({
      dim: L.metricLabels.accuracy, me: d.brand_accuracy,
      top: top.accuracy, ind: d.industry_avg.accuracy ?? 0,
    });
  }
  if (d.brand_citation != null && top.citation != null) {
    data.push({
      dim: L.metricLabels.citation, me: d.brand_citation,
      top: top.citation, ind: d.industry_avg.citation ?? 0,
    });
  }
  const legends = [
    { name: d.brand, color: 'var(--data-accent)' },
    { name: `${L.cmpTop} · ${top.name}`, color: 'var(--warn-solid)' },
    { name: L.industryAvg, color: 'var(--neutral-fg)' },
  ];
  return (
    <div>
      <div style={{ height: 248 }}>
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <RadarChart data={data} outerRadius="74%">
            <PolarGrid stroke="var(--grid-line)" />
            <PolarAngleAxis dataKey="dim" tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} />
            <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
            <Radar name={L.industryAvg} dataKey="ind" stroke="var(--neutral-fg)"
              strokeDasharray="4 4" strokeWidth={1.5} fill="var(--neutral-fg)" fillOpacity={0} />
            <Radar name={top.name} dataKey="top" stroke="var(--warn-solid)"
              strokeWidth={1.8} fill="var(--warn-solid)" fillOpacity={0.1} />
            <Radar name={d.brand} dataKey="me" stroke="var(--data-accent)"
              strokeWidth={2.4} fill="var(--data-accent)" fillOpacity={0.18}
              dot={{ r: 3, fill: 'var(--data-accent)' }} />
          </RadarChart>
        </ResponsiveContainer>
      </div>
      {/* Legend: inline-block, PDF-safe */}
      <div style={{ textAlign: 'center', marginTop: 2 }}>
        {legends.map(lg => (
          <span key={lg.name}
            style={{ display: 'inline-block', margin: '0 8px', fontSize: 11.5, color: 'var(--text-secondary)' }}>
            <span style={{
              display: 'inline-block', width: 10, height: 10, borderRadius: 3,
              background: lg.color, marginRight: 5, verticalAlign: -1,
            }} />
            {lg.name}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Single-metric head-to-head card (option A): brand gradient bar vs. competitor gray bars vs. industry average dashed bar + gap summary at the bottom.
// PDF-safe: inline layout uses grid / inline-block, not flex-gap.
function BattleCard({ label, hint, rows, higherBetter, footer }: {
  label: string; hint: string; rows: BattleRow[]; higherBetter: boolean; footer: string;
}) {
  const L = useReportsLang();
  const industryName = L.industryAvg;
  const max = Math.max(...rows.map(r => r.value), 0.01);
  return (
    <div className="p-3.5 rounded-[10px] grid gap-2 content-start"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="text-xs font-bold text-primary flex items-center gap-1.5">
        {label}<InfoHint text={hint} />
      </div>
      {rows.map(r => {
        const ratio = Math.max(0.06, r.value / max);
        // The backend diff is already normalized to "positive = we lead" (likewise for rank differences); judge direction directly
        const lead = r.diff !== null && r.diff > 0;
        return (
          <div key={r.name} className="grid gap-1">
            <div className="grid items-center gap-2" style={{ gridTemplateColumns: 'minmax(0, 1fr) auto' }}>
              <span className={`text-[11px] whitespace-nowrap overflow-hidden text-ellipsis ${r.isBrand ? 'font-bold text-primary' : 'text-secondary'}`}>
                {r.isBrand ? `★ ${r.name}` : r.name}
              </span>
              <span className="text-right whitespace-nowrap">
                <span className={`text-[11px] tabular-nums ${r.isBrand ? 'font-bold text-primary' : 'text-secondary'}`}>
                  {r.display}
                </span>
                {r.diff !== null && r.diff !== 0 && (
                  <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap inline-block"
                    style={lead
                      ? { background: 'var(--success-bg)', color: 'var(--success-fg)' }
                      : { background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
                    {lead ? L.leadBadge : L.trailBadge} {Math.abs(r.diff)}{higherBetter ? '%' : L.rankUnit}
                  </span>
                )}
              </span>
            </div>
            <div className="h-[9px] rounded-full overflow-hidden" style={{ background: 'var(--bg-input)' }}>
              <div className="h-[9px] rounded-full"
                style={{
                  width: `${ratio * 100}%`,
                  background: r.isBrand ? BRAND_BAR : r.name === industryName ? INDUSTRY_BAR : COMP_BAR,
                  opacity: r.isBrand ? 1 : 0.75,
                }} />
            </div>
          </div>
        );
      })}
      <div className="text-[10.5px] text-muted pt-2 mt-0.5" style={{ borderTop: '1px dashed var(--rpt-hairline)' }}>
        {footer}
      </div>
    </div>
  );
}

// ── Market competition insights: the same single-row light banner as the monthly report -- conclusion + gap + opportunity queries, no action button
function InsightBand({ d }: { d: CompetitiveAnalysis }) {
  const L = useReportsLang();
  const top = d.competitors[0];
  if (!top) return null;
  const leads = [top.coverage_diff > 0, top.mention_diff > 0, top.rank_diff > 0].filter(Boolean).length;
  const good = leads >= 2;
  return (
    <SectionCard title={L.secInsight} hint={L.hintContrast}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 rounded-xl"
        style={{
          background: good ? 'var(--success-bg)' : 'var(--warn-bg)',
          border: `1px solid ${good ? 'var(--success-border)' : 'var(--warn-border)'}`,
        }}>
        <span className="text-sm font-semibold" style={{ color: good ? 'var(--success-fg)' : 'var(--warn-fg)' }}>
          {good ? '🏆' : '⚔️'} {L.cmpVerdict(top.name, leads, 3)}
        </span>
        <span className="text-xs text-secondary">
          {L.gapTop2}
          <span className="text-base font-bold tabular-nums ml-1.5"
            style={{ color: d.coverage_gap_top2 >= 0 ? 'var(--success-fg)' : 'var(--warn-fg)' }}>
            {d.coverage_gap_top2 > 0 ? '+' : ''}{d.coverage_gap_top2}%
          </span>
        </span>
        <span className="text-xs text-secondary">
          {L.optimizeKw}
          <span className="text-base font-bold tabular-nums ml-1.5 text-primary">{d.keywords_to_optimize}</span>
        </span>
      </div>
    </SectionCard>
  );
}

// ── Keyword heat map: heat mosaic (treemap-style) ──
// There is only one metric definition: search = external search heat, real market demand. The backend only reads the keyword_heat cache
// (Baidu Index / Google Trends / Ocean Engine Index / model estimate); uncollected queries show "pending collection", and "Refresh" at the top right collects on demand.
//
// ★ 2026-09-09 removed the former second tier "hit heat": its value range was just the number of platforms (5 in this system),
//   too little resolution, and after deduping to (query, platform) cells it measured the same thing as the exposure rate at the top;
//   its 0-4000 scale was manufactured from `hit share × rank decay` -- even when 10 queries had exactly the same hit count
//   it still drew a 4000->1800 gradient, a pure visual illusion. For hit info, see the exposure rate card and the daily report detail table.
//   The backend still returns hit_heat / hits (the industry library public page's word cloud reads the heat field); it's just no longer shown here.
// One block per query: color depth encodes search heat; packed by row, each row split evenly to fill 100% (row 1: 2 blocks / row 2: 3 blocks / the rest: 5 blocks).
// PDF-safe: one grid per row (equal columns + gap), block widths identical and left/right edges flush;
// grid+gap isn't on the html2canvas red list (the red list covers only the flex family; the overview target banner grid already passed export verification).

function heatOf(w: HeatWord): number | null {
  return w.search_heat ?? null;
}

function KeywordHeat({ d }: { d: CompetitiveAnalysis }) {
  const L = useReportsLang();
  if (d.heat_words.length === 0) {
    return <div className="text-xs text-muted text-center py-8">{L.empty}</div>;
  }
  // The current metric definition decides sorting: under search heat, uncollected queries go last (heat treated as 0)
  const words = d.heat_words.slice(0, 10)
    .slice()
    .sort((a, b) => (heatOf(b) ?? -1) - (heatOf(a) ?? -1));
  const max = Math.max(...words.map(w => heatOf(w) ?? 0), 1);
  // Source distribution to the right of the legend (different sources have different scales, so totals aren't mixed)
  const srcCount = words.reduce<Record<string, number>>((acc, w) => {
    if (w.search_source && w.search_heat != null) {
      acc[w.search_source] = (acc[w.search_source] || 0) + 1;
    }
    return acc;
  }, {});
  const srcSummary = Object.entries(srcCount)
    .map(([s, n]) => `${L.heatSourceLabels[s] || s} ${n}`).join(' · ');

  // Split queries into rows at a "2, 3, 5, 5..." rhythm; head queries get fewer rows with bigger blocks, the long tail more rows with smaller blocks
  // Each block used to carry a global index i to print "#3" inside it. A heat map is about **the values themselves**;
  // rank is already conveyed by color depth and position, printing it again is just noise -- the index was removed, and i along with it.
  const rows: typeof words[number][][] = [];
  const pattern = [2, 3, 5];
  let idx = 0, pi = 0;
  while (idx < words.length) {
    const size = Math.min(pattern[Math.min(pi, pattern.length - 1)], words.length - idx);
    rows.push(words.slice(idx, idx + size));
    idx += size; pi++;
  }
  // If the last row has only 1 block left (it would stretch across the whole row, overemphasizing the long tail), merge it back into the previous row
  if (rows.length > 1 && rows[rows.length - 1].length === 1) {
    const last = rows.pop()!;
    rows[rows.length - 1].push(...last);
  }

  return (
    <div>
      {rows.map((row, ri) => (
        <div key={ri} style={{
          display: 'grid', gridTemplateColumns: `repeat(${row.length}, minmax(0, 1fr))`,
          gap: 7, marginBottom: 7,
        }}>
          {row.map((w) => {
            const val = heatOf(w);
            const ratio = (val ?? 0) / max;
            const ci = Math.min(SEQ_BLUE.length - 1, Math.round(ratio * (SEQ_BLUE.length - 1)));
            const bg = SEQ_BLUE[ci];
            const ink = ci >= 4 ? '#fff' : '#0d366b';
            const subInk = ci >= 4 ? 'rgba(255,255,255,0.65)' : 'var(--text-muted)';
            // Subtitle: label the source (+ stale hint); the actual core query used is in the tooltip
            const sub = w.search_source && val != null
              ? (L.heatSourceLabels[w.search_source] || w.search_source)
                + (w.stale ? ` · ${L.heatStaleTag}` : '')
              : '';
            const searchText = w.search_heat != null
              ? `${w.search_heat}${w.search_source ? ` · ${L.heatSourceLabels[w.search_source] || w.search_source}` : ''}`
                + (w.search_raw != null ? ` (${w.search_raw})` : '')
                + (w.core_word ? `\n${L.heatCoreWord}: ${w.core_word}` : '')
              : L.heatNoSearch;
            return (
              <div key={w.query}
                title={`${w.query}\n${L.heatTooltip(searchText)}`}
                style={{ borderRadius: 10, padding: '11px 13px', minHeight: 66, background: bg, display: 'grid', gap: 8, alignContent: 'space-between' }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.25, color: ink, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const }}>{w.query}</div>
                <div>
                  {val != null ? (
                    <span className="tabular-nums" style={{ fontSize: 15, fontWeight: 750, letterSpacing: '-0.02em', color: ink }}>{val}</span>
                  ) : (
                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)' }}>{L.heatNoSearch}</span>
                  )}
                  {sub && (
                    <span style={{ fontSize: 9.5, fontWeight: 600, color: subInk, marginLeft: 6 }}>{sub}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ))}
      {/* Legend: color scale + totals (inline-block, PDF-safe) */}
      <div style={{ marginTop: 6, fontSize: 10, color: 'var(--text-muted)' }}>
        <span style={{ verticalAlign: 'middle' }}>{L.heatLow}</span>
        <span style={{ display: 'inline-block', height: 8, borderRadius: 4, overflow: 'hidden', width: 150, margin: '0 8px', verticalAlign: 'middle' }}>
          {SEQ_BLUE.map(c => <span key={c} style={{ display: 'inline-block', width: `${100 / SEQ_BLUE.length}%`, height: 8, background: c, verticalAlign: 'top' }} />)}
        </span>
        <span style={{ verticalAlign: 'middle' }}>{L.heatHigh}</span>
        <span className="tabular-nums" style={{ float: 'right' }}>
          {srcSummary || L.heatNoSearch}
        </span>
      </div>
    </div>
  );
}

// ── Heat map block: metric definition switch + search heat collection button ──
// Search heat is a cached value (re-collected by a scheduled job); "Refresh" here synchronously collects the Top10 -- external sources are slow and
// subject to anti-abuse controls, so it's never on the page's initial load path. After collecting, refetch competitive data to refresh the blocks.
function KeywordHeatSection({ d, topicId, token, onReloaded }: {
  d: CompetitiveAnalysis; topicId: number; token: string; onReloaded: () => void;
}) {
  const L = useReportsLang();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const cache = d.heat_cache;

  const doRefresh = async () => {
    setBusy(true); setNote('');
    try {
      const r = await reportsApi.refreshKeywordHeat(topicId, token);
      // Skipped sources must be shown together with the **reason**. Previously only source names were listed ("Disabled sources: Baidu Index"),
      // while the real reason is often actionable -- Baidu Index status=10018 means a Cipher-Text header must be supplied,
      // and from the source name alone nobody knew what to do, so this card stayed full of "pending collection" with nobody attending to it.
      const skipped = Object.entries(r.skipped || {})
        .map(([src, why]) => `${L.heatSourceLabels[src] || src}:${why}`).join('\n');
      setNote(L.heatRefreshDone(r.written) + (skipped ? `\n${L.heatRefreshSkipped(skipped)}` : ''));
      onReloaded();
    } catch {
      setNote(L.heatRefreshFailed);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard title={L.secHeat} hint={L.hintHeat}
      right={
        <div className="flex items-center gap-2">
          {cache && (
            <span className="text-[10px] text-muted tabular-nums">
              {L.heatCollected(cache.fresh, cache.total)}
            </span>
          )}
          <button type="button" onClick={doRefresh} disabled={busy} data-export-hide
            className="px-2 py-0.5 rounded-md text-[10px] font-semibold border transition-colors disabled:opacity-60"
            style={{ color: 'var(--accent-primary)', borderColor: 'var(--accent-primary)' }}>
            {busy ? L.heatRefreshing : L.heatRefresh}
          </button>
        </div>
      }>
      <KeywordHeat d={d} />
      {note && <div className="mt-2 text-[10px] text-muted whitespace-pre-line" data-export-hide>{note}</div>}
    </SectionCard>
  );
}

// ── Product leaderboard: engine × product heat matrix ──
// Rows = products (this brand highlighted), columns = per-engine exposure rate (color depth = strength), last column the composite score.
// Globally normalized (all cells share min/max), so you can read vertically "which engine favors whom" and horizontally a single product's distribution.
// PDF-safe: native <table>, no flex.
function ProductRanking({ d }: { d: CompetitiveAnalysis }) {
  const L = useReportsLang();
  const engines = d.engines;
  const allScores = d.ranking.flatMap(r => engines.map(e => r.scores[e] ?? 0));
  const sMax = Math.max(...allScores, 1);
  const sMin = Math.min(...allScores, 0);
  const heatCell = (s: number) => {
    const ratio = (s - sMin) / (sMax - sMin || 1);
    const ci = Math.min(SEQ_BLUE.length - 1, Math.round(ratio * (SEQ_BLUE.length - 1)));
    const bg = SEQ_BLUE[ci];
    const ink = ci >= 4 ? '#fff' : ci <= 1 ? 'var(--text-muted)' : '#0d366b';
    return (
      <div style={{ height: 30, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 600, margin: '0 2px', background: bg, color: ink }}>
        {s.toFixed(0)}
      </div>
    );
  };
  const thBase: CSSProperties = { fontSize: 10.5, fontWeight: 600, color: 'var(--text-secondary)', textAlign: 'center', padding: '0 0 9px', letterSpacing: '0.03em' };
  return (
    <div>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} className="tabular-nums">
        <thead>
          <tr>
            <th style={{ ...thBase, textAlign: 'left', width: 28 }}>#</th>
            <th style={{ ...thBase, textAlign: 'left', paddingLeft: 2 }}>{L.rankingProductCol}</th>
            {engines.map(e => <th key={e} style={thBase}>{engineLabel(e)}</th>)}
            <th style={{ ...thBase, textAlign: 'right', width: 64, paddingRight: 2 }}>{L.compositeCol}</th>
          </tr>
        </thead>
        <tbody>
          {d.ranking.map((row, i) => (
            <tr key={row.name} style={row.is_brand ? { background: 'var(--info-bg)' } : undefined}>
              <td style={{ padding: '4px 3px', borderTop: i ? '1px solid var(--border-color)' : undefined, fontSize: 12, fontWeight: 650, color: 'var(--text-muted)', textAlign: 'center' }}>{i + 1}</td>
              <td style={{ padding: '4px 3px 4px 2px', borderTop: i ? '1px solid var(--border-color)' : undefined, fontSize: 13, fontWeight: 550, color: 'var(--text-primary)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {row.name}
                {row.is_brand && (
                  <span style={{ fontSize: 9, color: 'var(--info-fg)', border: '1px solid var(--info-fg)', borderRadius: 5, padding: '0 5px', marginLeft: 6, fontWeight: 600, verticalAlign: 'middle' }}>{L.brandSelf}</span>
                )}
              </td>
              {engines.map(e => (
                <td key={e} style={{ padding: '4px 3px', borderTop: i ? '1px solid var(--border-color)' : undefined }}>{heatCell(row.scores[e] ?? 0)}</td>
              ))}
              <td style={{ padding: '4px 2px 4px 3px', borderTop: i ? '1px solid var(--border-color)' : undefined, fontSize: 14, fontWeight: 750, textAlign: 'right', color: row.is_brand ? 'var(--info-fg)' : 'var(--text-primary)' }}>{row.composite.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {/* Legend: cell meaning + color scale */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 13, fontSize: 10, color: 'var(--text-muted)' }}>
        <span>{L.rankHeatHint}</span>
        <span style={{ marginLeft: 'auto' }}>{L.heatWeak}</span>
        <span style={{ display: 'inline-flex', height: 8, borderRadius: 4, overflow: 'hidden', width: 130 }}>
          {SEQ_BLUE.map(c => <span key={c} style={{ flex: 1, background: c }} />)}
        </span>
        <span>{L.heatStrong}</span>
      </div>
    </div>
  );
}

// ── Overview: full competitor analysis area (comparison data -> insight banner -> heat map -> leaderboard -> rank/sources) ──
export function CompetitiveFull({ topicId, token }: { topicId: number; token: string }) {
  const L = useReportsLang();
  const { data: d, loading, reload } = useCompetitive(topicId, token, 'overview');
  if (loading) return <SectionSkeleton rows={3} />;
  if (!d) return null;
  const { cov, men, rank } = buildBattleRows(d, L);
  const ourRank = brandPos(d);
  // Head-to-head card summary: gap from the industry average (for rank it's the rank difference, positive = we're ahead)
  const covDiff = Math.round((d.coverage.current - d.industry_avg.coverage) * 10) / 10;
  const menDiff = Math.round((d.mention.current - d.industry_avg.mention) * 10) / 10;
  const rankDiff = Math.round(d.industry_avg.rank - ourRank);
  return (
    <>
      <SectionCard title={L.secContrast} hint={L.hintContrast}>
        <div className="rpt-cmpgrid">
          <CompareRadar d={d} />
          <div className="rpt-battles">
            <BattleCard label={L.kwCoverage} hint={L.hintKwCoverage} rows={cov}
              higherBetter footer={L.vsIndustryPp(covDiff)} />
            <BattleCard label={L.prodMentionShort} hint={L.hintProdMention} rows={men}
              higherBetter footer={L.vsIndustryPp(menDiff)} />
            <BattleCard label={L.prodRank} hint={L.hintProdRank} rows={rank}
              higherBetter={false} footer={L.vsIndustryRank(rankDiff)} />
          </div>
        </div>
      </SectionCard>
      <InsightBand d={d} />
      <KeywordHeatSection d={d} topicId={topicId} token={token} onReloaded={reload} />
      <SectionCard title={L.secRanking} hint={L.hintRanking}>
        <ProductRanking d={d} />
      </SectionCard>
      {/* Metrics board content merged into the overview (rank distribution + source analysis) */}
      <BoardSections topicId={topicId} token={token} />
    </>
  );
}

// ── Embedded in daily/weekly/monthly reports: competitor head-to-head (conclusion banner + three-metric comparison bars + insights) ──

const BRAND_BAR = 'linear-gradient(90deg,#2563eb,#7c3aed)';
const COMP_BAR = '#94a3b8';
const INDUSTRY_BAR = 'repeating-linear-gradient(90deg,#cbd5e1 0 6px,transparent 6px 10px)';

interface BattleRow { name: string; value: number; display: string; isBrand: boolean; diff: number | null }

// Single-metric head-to-head bar: brand gradient bar vs. competitor gray bars vs. industry average dashed bar, with a lead/lag badge on the right
function BattleMetric({ label, hint, rows, higherBetter }: {
  label: string; hint: string; rows: BattleRow[]; higherBetter: boolean;
}) {
  const L = useReportsLang();
  const industryName = L.industryAvg;
  const max = Math.max(...rows.map(r => r.value), 0.01);
  return (
    <div className="p-3 rounded-xl grid gap-2 content-start"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="text-xs font-semibold text-primary flex items-center gap-1.5">
        {label}<InfoHint text={hint} />
      </div>
      {rows.map(r => {
        const ratio = Math.max(0.06, r.value / max);
        // The backend diff is already normalized to "positive = we lead" (likewise for rank differences); judge direction directly
        const lead = r.diff !== null && r.diff > 0;
        return (
          <div key={r.name} className="grid gap-0.5">
            <div className="flex items-center justify-between gap-2">
              <span className={`text-[11px] whitespace-nowrap ${r.isBrand ? 'font-semibold text-primary' : 'text-secondary'}`}>
                {r.isBrand ? `★ ${r.name}` : r.name}
              </span>
              <span className="flex items-center gap-1.5">
                <span className={`text-[11px] tabular-nums ${r.isBrand ? 'font-bold text-primary' : 'text-secondary'}`}>
                  {r.display}
                </span>
                {r.diff !== null && r.diff !== 0 && (
                  <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap"
                    style={lead
                      ? { background: 'var(--success-bg)', color: 'var(--success-fg)' }
                      : { background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
                    {lead ? L.leadBadge : L.trailBadge} {Math.abs(r.diff)}{higherBetter ? '%' : L.rankUnit}
                  </span>
                )}
              </span>
            </div>
            <div className="h-2.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-input)' }}>
              <div className="h-2.5 rounded-full transition-all"
                style={{
                  width: `${ratio * 100}%`,
                  background: r.isBrand ? BRAND_BAR : r.name === industryName ? INDUSTRY_BAR : COMP_BAR,
                  opacity: r.isBrand ? 1 : 0.75
                }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function CompetitiveCompact({ topicId, token, periodType, periodKey, title }: {
  topicId: number; token: string; periodType: string; periodKey: string; title: string;
}) {
  const L = useReportsLang();
  const { data: d, loading } = useCompetitive(topicId, token, periodType, periodKey);
  if (loading || !d || d.competitors.length === 0) return null;

  // Conclusion: against the leading competitor, judge lead or not on each of the three items (coverage/mentions/rank)
  const top = d.competitors[0];
  const leads = [top.coverage_diff > 0, top.mention_diff > 0, top.rank_diff > 0].filter(Boolean).length;
  const verdictGood = leads >= 2;

  const { cov: covRows, men: menRows, rank: rankRows } = buildBattleRows(d, L);

  return (
    <SectionCard title={title} hint={L.hintContrast}>
      <div className="grid gap-3">
        {/* Conclusion banner: large conclusion text + big numbers for gap/opportunity queries */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 rounded-xl"
          style={{
            background: verdictGood ? 'var(--success-bg)' : 'var(--warn-bg)',
            border: `1px solid ${verdictGood ? 'var(--success-border)' : 'var(--warn-border)'}`,
          }}>
          <span className="text-sm font-semibold" style={{ color: verdictGood ? 'var(--success-fg)' : 'var(--warn-fg)' }}>
            {verdictGood ? '🏆' : '⚔️'} {L.cmpVerdict(top.name, leads, 3)}
          </span>
          <span className="text-xs text-secondary">
            {L.gapTop2}
            <span className="text-base font-bold tabular-nums ml-1.5"
              style={{ color: d.coverage_gap_top2 >= 0 ? 'var(--success-fg)' : 'var(--warn-fg)' }}>
              {d.coverage_gap_top2 > 0 ? '+' : ''}{d.coverage_gap_top2}%
            </span>
          </span>
          <span className="text-xs text-secondary">
            {L.optimizeKw}
            <span className="text-base font-bold tabular-nums ml-1.5 text-primary">{d.keywords_to_optimize}</span>
          </span>
        </div>

        {/* Three-metric head-to-head bars */}
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))' }}>
          <BattleMetric label={L.kwCoverage} hint={L.hintKwCoverage} rows={covRows} higherBetter />
          <BattleMetric label={L.prodMentionShort} hint={L.hintProdMention} rows={menRows} higherBetter />
          <BattleMetric label={L.prodRank} hint={L.hintProdRank} rows={rankRows} higherBetter={false} />
        </div>
      </div>
    </SectionCard>
  );
}
