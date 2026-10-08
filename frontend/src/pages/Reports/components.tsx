// Report center shared components — referenced only by pages under reports/, without importing other pages' private components.
// InfoHint is the report center's local version (icon is i; brand growth still uses the ? version, independent of each other).

import { useEffect, useId, useState } from 'react';
import {
  reportsApi, type DeliverableStat, type KpiMetric, type MetricKey, type MonthlyKpiCard,
  type PeriodType, type PlatformMetricRow, type Progress,
} from '../../services/reportsApi';
import { engineLabel } from '../../constants/engines';
import { useHintShift } from '../../hooks/useHintShift';
import { useReportsLang } from './lang';

// ── Dedicated colors for the six metrics (option A): CSS variables defined in the .reports-shell scope of index.css, adapting to all three themes ──
export const METRIC_HUE: Record<MetricKey, string> = {
  exposure: 'var(--mh-exposure)', top1: 'var(--mh-top1)', top3: 'var(--mh-top3)',
  accuracy: 'var(--mh-accuracy)', citation: 'var(--mh-citation)', mention: 'var(--mh-mention)',
};
export const METRIC_TINT: Record<MetricKey, string> = {
  exposure: 'var(--mht-exposure)', top1: 'var(--mht-top1)', top3: 'var(--mht-top3)',
  accuracy: 'var(--mht-accuracy)', citation: 'var(--mht-citation)', mention: 'var(--mht-mention)',
};

// ── Period navigation: page through the list of periods that actually exist for the topic, disabled at the ends (no more blind date arithmetic) ──
export function usePeriodKeys(topicId: number | undefined, token: string, periodType: PeriodType): string[] {
  const [keys, setKeys] = useState<string[]>([]);
  useEffect(() => {
    if (!topicId) return;
    reportsApi.listReports(topicId, token, periodType, 200)
      .then(l => setKeys(l.items.map(it => it.key)))   // Newest first
      .catch(() => setKeys([]));
  }, [topicId, token, periodType]);
  return keys;
}

export function PeriodNavButtons({ keys, current, onGo }: {
  keys: string[]; current: string; onGo: (key: string) => void;
}) {
  const L = useReportsLang();
  const idx = keys.indexOf(current);
  const prev = idx >= 0 ? keys[idx + 1] : undefined;   // List is newest first: the next item = an earlier period
  const next = idx > 0 ? keys[idx - 1] : undefined;
  return (
    <>
      <button type="button" disabled={!prev} onClick={() => prev && onGo(prev)}
        className="px-2 py-1 text-xs rounded disabled:opacity-40 disabled:cursor-not-allowed"
        style={{ background: 'var(--bg-input)' }}>
        ‹ {L.prevPeriod}
      </button>
      <button type="button" disabled={!next} onClick={() => next && onGo(next)}
        className="px-2 py-1 text-xs rounded disabled:opacity-40 disabled:cursor-not-allowed"
        style={{ background: 'var(--bg-input)' }}>
        {L.nextPeriod} ›
      </button>
    </>
  );
}

// ── Info hover tooltip (i icon) ──
export function InfoHint({ text }: { text: string }) {
  const [shift, onShow] = useHintShift();
  return (
    <span className="relative inline-flex group" tabIndex={0} onMouseEnter={onShow} onFocus={onShow}>
      <span
        className="w-3.5 h-3.5 rounded-full text-[9px] font-serif italic font-semibold flex items-center justify-center cursor-help select-none"
        style={{ background: 'var(--bg-input)', color: 'var(--text-muted)', border: '1px solid var(--border-color)' }}
        aria-label={text}
      >
        i
      </span>
      <span
        className="hidden group-hover:block group-focus-within:block absolute left-1/2 top-full mt-1.5 z-50 w-64 p-2 text-[11px] rounded shadow-lg"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          color: 'var(--text-secondary)',
          pointerEvents: 'none',
          whiteSpace: 'pre-line',
          transform: `translateX(calc(-50% + ${shift}px))`,
        }}
      >
        {text}
      </span>
    </span>
  );
}

// ── Period-over-period badge: ↑ green ↓ red — gray (unit %) ──
export function TrendBadge({ deltaPp }: { deltaPp: number | null }) {
  if (deltaPp === null) return <span className="text-muted">—</span>;
  const up = deltaPp > 0;
  const flat = deltaPp === 0;
  const conf = flat
    ? { fg: 'var(--neutral-fg)', bg: 'var(--neutral-bg)' }
    : up
      ? { fg: 'var(--success-fg)', bg: 'var(--success-bg)' }
      : { fg: 'var(--danger-fg)', bg: 'var(--danger-bg)' };
  return (
    <span className="tabular-nums font-medium px-1.5 py-0.5 rounded text-[11px] whitespace-nowrap"
      style={{ color: conf.fg, background: conf.bg }}>
      {flat ? '—' : up ? '▲' : '▼'} {up ? '+' : ''}{deltaPp}%
    </span>
  );
}

// ── Target-met badge ──
export function ProgressBadge({ progress }: { progress: Progress | null }) {
  const L = useReportsLang();
  if (!progress) return <span className="text-muted">{L.noTarget}</span>;
  const conf = {
    met: { bg: 'var(--success-bg)', fg: 'var(--success-fg)', text: L.progressMet },
    near: { bg: 'var(--warn-bg)', fg: 'var(--warn-fg)', text: L.progressNear },
    missed: { bg: 'var(--danger-bg)', fg: 'var(--danger-fg)', text: L.progressMissed },
  }[progress];
  return (
    <span className="px-2 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap"
      style={{ background: conf.bg, color: conf.fg }}>
      {conf.text}
    </span>
  );
}

// ── Mini sparkline (pure SVG, no recharts): gradient area + stroke animation + last-point marker.
//    color supports metric-specific colors; when axisLabels passes [start, middle, end] dates, a timeline (line + ticks + dates) is drawn below ──
export function Sparkline({ values, width = 120, height = 30, color = 'var(--data-accent)', axisLabels = null }: {
  values: number[]; width?: number; height?: number; color?: string;
  axisLabels?: (string | null)[] | null;
}) {
  // useId ensures gradient ids don't collide across instances (hooks must be called unconditionally)
  const gid = `rpt-spark-${useId().replace(/[^a-zA-Z0-9-]/g, '')}`;
  const withAxis = axisLabels !== null;
  const axisH = withAxis ? 15 : 0;             // Timeline area height (line + ticks + dates)
  const chartH = height - axisH;
  if (values.length < 2) return <div style={{ height }} />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coords = values.map((v, i) => {
    const x = (i / (values.length - 1)) * (width - 4) + 2;
    const y = chartH - 3 - ((v - min) / span) * (chartH - 6);
    return [x, y] as const;
  });
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `2,${chartH - 1} ${line} ${width - 2},${chartH - 1}`;
  const [lastX, lastY] = coords[coords.length - 1];
  const axisY = chartH + 1.5;
  const ticks: [number, 'start' | 'middle' | 'end'][] = [[2, 'start'], [width / 2, 'middle'], [width - 2, 'end']];
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none" className="block">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${gid})`} />
      <polyline points={line} fill="none" stroke={color} strokeWidth="1.5"
        className="rpt-spark-line" />
      <circle cx={lastX.toFixed(1)} cy={lastY.toFixed(1)} r="2.2" fill={color} />
      {withAxis && (
        <>
          <line x1="0" y1={axisY} x2={width} y2={axisY} stroke="var(--rpt-hairline)" strokeWidth="1" />
          {ticks.map(([x, anchor], i) => (
            <g key={anchor}>
              <line x1={x} y1={axisY} x2={x} y2={axisY + 3} stroke="var(--border-color)" strokeWidth="1" />
              {axisLabels?.[i] && (
                <text x={x} y={height - 1} textAnchor={anchor} fontSize="7.5"
                  fill="var(--text-muted)">{axisLabels[i]}</text>
              )}
            </g>
          ))}
        </>
      )}
    </svg>
  );
}

// ── Target text: tier ranges show a range (upper bound 100 treated as "≥ lower bound"), contract metric definition shows a single value ──
export function targetText(target: number, targetHigh: number | null): string {
  if (targetHigh === null) return `${target}%`;
  return targetHigh >= 100 ? `≥${target}%` : `${target}–${targetHigh}%`;
}

// ── Target progress bar (met is judged by the target lower bound; option A: metric color fill + "Target progress" left / status right on one line) ──
export function TargetProgressBar({ current, target, targetHigh = null, color = null }: {
  current: number; target: number | null; targetHigh?: number | null; color?: string | null;
}) {
  const L = useReportsLang();
  if (target === null) {
    return <div className="text-[10px] text-muted">{L.target}: {L.noTarget}</div>;
  }
  const ratio = Math.min(1, target > 0 ? current / target : 0);
  const met = current >= target;
  const near = !met && current >= target * 0.95;
  const semantic = met ? 'var(--success-solid)' : near ? 'var(--warn-solid)' : 'var(--danger-solid)';
  const fg = met ? 'var(--success-fg)' : near ? 'var(--warn-fg)' : 'var(--danger-fg)';
  return (
    <div className="grid gap-1">
      <div className="h-[4px] rounded-sm overflow-hidden" style={{ background: 'var(--bg-input)' }}>
        <div className="h-[4px] rounded-sm transition-all"
          style={{ width: `${ratio * 100}%`, background: color ?? semantic }} />
      </div>
      <div className="text-[10px] flex items-center justify-between gap-1 whitespace-nowrap">
        <span className="text-muted">{L.targetProgress} · {targetText(target, targetHigh)}</span>
        <span className="font-bold" style={{ color: fg }}>
          {met ? L.progressMet : near ? L.progressNear : L.progressMissed}
        </span>
      </div>
    </div>
  );
}

// ── Metric card (option A: standalone colored card, metric-color top edge + pale gradient at the bottom + metric-color mini chart/progress bar) ──
export function KpiCard({ metric, sparkLabels = null }: {
  metric: KpiMetric; sparkLabels?: (string | null)[] | null;
}) {
  const L = useReportsLang();
  const hue = METRIC_HUE[metric.key];
  return (
    <div className="rpt-kpi px-3.5 pt-3 pb-2.5 grid gap-2 content-start"
      style={{
        ['--kpi-hue' as string]: hue,
        backgroundImage: `linear-gradient(180deg, ${METRIC_TINT[metric.key]}, var(--bg-card) 58%)`,
      }}>
      <div className="flex items-center justify-between gap-1.5">
        <span className="text-[10.5px] font-bold text-secondary tracking-[0.05em] whitespace-nowrap flex items-center gap-1">
          {L.metricLabels[metric.key]}
          <InfoHint text={L.metricHints[metric.key]} />
        </span>
        <TrendBadge deltaPp={metric.delta_pp} />
      </div>
      <div className="rpt-num text-[25px] font-bold text-primary leading-none">
        {metric.current}<span className="text-xs font-medium text-muted ml-0.5">%</span>
      </div>
      <Sparkline values={metric.sparkline} color={hue}
        height={sparkLabels ? 44 : 30} axisLabels={sparkLabels} />
      <TargetProgressBar current={metric.current} target={metric.target}
        targetHigh={metric.target_high} color={hue} />
    </div>
  );
}

export function KpiCardRow({ metrics, sparkLabels = null }: {
  metrics: KpiMetric[]; sparkLabels?: (string | null)[] | null;
}) {
  // Option A: six standalone colored cards; with a count other than 6, fall back to adaptive columns
  return (
    <div className="rpt-kpis"
      style={metrics.length !== 6
        ? { gridTemplateColumns: `repeat(auto-fit, minmax(170px, 1fr))` }
        : undefined}>
      {metrics.map(m => <KpiCard key={m.key} metric={m} sparkLabels={sparkLabels} />)}
    </div>
  );
}

// ── Engine badge ──
export function EngineBadge({ engine }: { engine: string }) {
  return (
    <span className="px-2 py-0.5 rounded-full text-[10px] whitespace-nowrap"
      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
      {engineLabel(engine)}
    </span>
  );
}

// ── Stance dot ──
export function SentimentDot({ sentiment }: { sentiment: string }) {
  const L = useReportsLang();
  const color = sentiment === 'positive' ? 'var(--success-solid)'
    : sentiment === 'negative' ? 'var(--danger-solid)'
    : sentiment === 'neutral' ? 'var(--neutral-fg)' : 'transparent';
  if (sentiment === 'unknown') return <span className="text-muted">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="w-2 h-2 rounded-full inline-block" style={{ background: color }} />
      <span className="text-[11px]">{L.sentimentLabels[sentiment] || sentiment}</span>
    </span>
  );
}

// ── Rank label (the backend gives stable keys: top1/top3/r4_10/exposed/miss/nodata, translated and rendered here) ──
export function RankBadge({ label }: { label: string }) {
  const L = useReportsLang();
  // top1 and top3 are both green: top1 gets a pale border for emphasis (four rank tiers: dark green/green/neutral/red)
  const conf: Record<string, { bg: string; fg: string; border?: string }> = {
    top1: { bg: 'var(--success-bg)', fg: 'var(--success-fg)', border: 'var(--success-border)' },
    top3: { bg: 'var(--success-bg)', fg: 'var(--success-fg)' },
    r4_10: { bg: 'var(--neutral-bg)', fg: 'var(--neutral-fg)' },
    exposed: { bg: 'var(--info-bg)', fg: 'var(--info-fg)' },
    miss: { bg: 'var(--danger-bg)', fg: 'var(--danger-fg)' },
    nodata: { bg: 'var(--bg-input)', fg: 'var(--text-secondary)' },
  };
  const c = conf[label] || conf.nodata;
  return (
    <span className="px-2 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap"
      style={{ background: c.bg, color: c.fg, border: c.border ? `1px solid ${c.border}` : '1px solid transparent' }}>
      {L.rankBadge[label] || label}
    </span>
  );
}

// ── Section card (the shell of each report page section; direction A: accent vertical bar + title + flexible thin line) ──
// Cell heat for the per-platform matrix: normalized by the column maximum.
// ★ Weekly and monthly reports share this one copy -- writing separate ones will drift (on 2026-08-07 the weekly report was blue and the monthly green).
export function heatCellBg(value: number, max: number): string {
  if (max <= 0) return 'transparent';
  const alpha = Math.min(0.35, (value / max) * 0.35);
  return `rgba(34,197,94,${alpha.toFixed(3)})`;
}

export function SectionCard({ title, hint, children, right }: {
  title: string; hint?: string; children: React.ReactNode; right?: React.ReactNode;
}) {
  return (
    <section className="rpt-card px-4 pt-3.5 pb-4">
      <div className="rpt-sechead">
        <h2 className="text-xs font-bold tracking-[0.08em] flex items-center gap-1.5"
          style={{ color: 'var(--text-secondary)' }}>
          {title}
          {hint && <InfoHint text={hint} />}
        </h2>
        <span className="rpt-sechead-rule" />
        {right}
      </div>
      {children}
    </section>
  );
}

// ── Table base styles (unified across report pages) ──
export const TH = ({ children, right }: { children?: React.ReactNode; right?: boolean }) => (
  <th className={`px-2.5 pb-2 pt-0.5 text-[11px] font-medium text-muted whitespace-nowrap ${right ? 'text-right' : 'text-left'}`}>
    {children}
  </th>
);

export const TD = ({ children, right, className = '' }: {
  children?: React.ReactNode; right?: boolean; className?: string;
}) => (
  <td className={`px-2.5 py-2 text-xs ${right ? 'text-right tabular-nums' : ''} ${className}`}
    style={{ borderTop: '1px solid var(--border-color)' }}>
    {children}
  </td>
);

// ── Skeleton screen (shapes match the final layout, replacing plain-text loading) ──
export function Skeleton({ className = '', style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={`animate-pulse rounded-lg ${className}`}
    style={{ background: 'var(--bg-input)', ...style }} />;
}

export function KpiRowSkeleton({ n = 6 }: { n?: number }) {
  return (
    <div className="rpt-kpis"
      style={n !== 6 ? { gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' } : undefined}>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="rpt-kpi px-3.5 pt-3 pb-2.5 grid gap-2.5 content-start">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-7 w-24" />
          <Skeleton className="w-full" style={{ height: 30 }} />
          <Skeleton className="h-[3px] w-full" />
        </div>
      ))}
    </div>
  );
}

export function SectionSkeleton({ rows = 4, chart = false }: { rows?: number; chart?: boolean }) {
  return (
    <div className="rpt-card p-5 grid gap-3">
      <Skeleton className="h-3 w-32" />
      {chart
        ? <Skeleton className="w-full" style={{ height: 220 }} />
        : Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-8 w-full" />)}
    </div>
  );
}

// ── Report body blocks (same display in weekly / monthly reports) ───────────────────────────────────────
// ★ These four sections share one implementation on both sides -- separate copies inevitably drift (the weekly report once had four extra sections of its own).

/** (1) KPI overview: before -> after optimization, with a check mark when met */
export function KpiCardGrid({ cards, rankCoverage }: {
  cards: MonthlyKpiCard[];
  /** Rank collection coverage for this period; 0 = no rank collected at all, so the TOP1/TOP3 cards show "not collected" instead of 0% */
  rankCoverage?: number | null;
}) {
  const L = useReportsLang();
  const rankMissing = (key: string) =>
    (key === 'top1' || key === 'top3') && rankCoverage === 0;
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
      {cards.map(c => (
        <div key={c.key} className="p-3 rounded-lg relative"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
          {c.met && (
            <span className="absolute top-2 right-2 px-1.5 py-0.5 rounded text-[10px]"
              style={{ background: 'var(--success-bg)', color: '#15803d' }}>
              ✓ {L.metBadge}
            </span>
          )}
          <div className="text-[11px] text-secondary mb-1 flex items-center gap-1.5">
            {L.metricLabels[c.key]}
            <InfoHint text={L.metricHints[c.key]} />
          </div>
          {rankMissing(c.key) ? (
            <div className="text-base text-muted" title={L.hintRankMissing}>{L.rankMissing}</div>
          ) : (
            <>
              <div className="text-base text-primary tabular-nums">
                {c.before}% → <span className="text-xl font-semibold">{c.after}%</span>
              </div>
              <div className="text-[11px] mt-1"
                style={{ color: (c.relative_pct ?? 0) >= 0 ? '#15803d' : '#b91c1c' }}>
                {c.relative_pct === null ? (c.met ? L.zeroToSome : L.noTarget)
                  : `${L.relative} ${c.relative_pct >= 0 ? '+' : ''}${c.relative_pct}%`}
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

/** (2) Per platform × six metrics: heat normalized within each column */
export function PlatformMatrixTable({ rows }: { rows: PlatformMetricRow[] }) {
  const L = useReportsLang();
  // col (data field) <-> metric (metric definition key): all six column headers must carry numerator/denominator notes -- of the six,
  // four exclude brand queries / two include them, three are computed per "query × platform" cell / three per response count,
  // and without spelling it out nobody can infer the definition from the numbers (see docs/03-系统设计/报告与指标/报告口径-六指标定义.md).
  const cols = [
    { col: 'exposure_pct', metric: 'exposure' },
    { col: 'top1_pct', metric: 'top1' },
    { col: 'top3_pct', metric: 'top3' },
    { col: 'accuracy_pct', metric: 'accuracy' },
    { col: 'citation_pct', metric: 'citation' },
    { col: 'mention_pct', metric: 'mention' },
  ] as const;
  const maxByCol = Object.fromEntries(
    cols.map(c => [c.col, Math.max(...rows.map(r => r[c.col]), 0)]),
  ) as Record<(typeof cols)[number]['col'], number>;
  // When ranks weren't collected, TOP1/TOP3 must not show 0% -- 0% means "AI didn't rank us near the top",
  // which is entirely different from "no ranks were extracted this period at all" (in 2026-08 extraction broke for 20 days and reports showed 0% throughout).
  const rankMissing = (r: PlatformMetricRow, metric: string) =>
    (metric === 'top1' || metric === 'top3') && r.rank_coverage === 0;
  return (
    <table className="w-full">
      <thead>
        <tr>
          <TH>{L.colEngine}</TH>
          <TH right><span className="inline-flex items-center gap-1">{L.colHits}<InfoHint text={L.hintHits} /></span></TH>
          {cols.map(({ metric }) => (
            <TH key={metric} right>
              <span className="inline-flex items-center gap-1">
                {L.metricLabels[metric]}<InfoHint text={L.metricHints[metric]} />
              </span>
            </TH>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.engine}>
            <TD className="text-primary font-medium">{engineLabel(r.engine)}</TD>
            <TD right className="tabular-nums">
              <span className="font-semibold text-primary">{r.hit_count}</span>
              <span className="text-muted"> / {r.valid_count}</span>
            </TD>
            {cols.map(({ col, metric }) => (
              <td key={col} className="px-2 py-1.5 text-xs text-right tabular-nums"
                style={{
                  borderTop: '1px solid var(--border-color)',
                  background: rankMissing(r, metric) ? undefined : heatCellBg(r[col], maxByCol[col]),
                }}>
                {rankMissing(r, metric)
                  ? <span className="text-muted" title={L.hintRankMissing}>{L.rankMissing}</span>
                  : `${r[col]}%`}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** (5) Deliverables this period: when delta is an empty string that row isn't rendered (no previous period to compare) */
export function DeliverableGrid({ items }: { items: DeliverableStat[] }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {items.map(d => (
        <div key={d.label} className="p-3 rounded-lg" style={{ background: 'var(--bg-input)' }}>
          <div className="text-[11px] text-secondary mb-1">{d.label}</div>
          <div className="text-base font-semibold text-primary">{d.value}</div>
          {d.delta && (
            <div className="text-[11px] mt-0.5"
              style={{ color: d.delta.startsWith('-') ? '#b91c1c'
                : d.delta.startsWith('+') ? '#15803d' : 'var(--text-muted)' }}>
              {d.delta}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** (6) Next period plan: rule-generated item list */
export function NextPlanList({ items }: { items: string[] }) {
  return (
    <ol className="space-y-2 list-none">
      {items.map((p, i) => (
        <li key={i} className="flex items-start gap-2 text-xs text-secondary">
          <span className="w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 text-[10px] font-medium"
            style={{ background: 'var(--bg-input)', color: 'var(--text-primary)' }}>
            {i + 1}
          </span>
          <span>{p}</span>
        </li>
      ))}
    </ol>
  );
}
