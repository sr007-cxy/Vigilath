// Report center recharts shared pieces — unified styles for grid / axes / tooltip + gradient area for the main series.
// All values come from CSS variables, adapting to three themes (peec / light / dark); color-mix() is forbidden (html2canvas 1.4.1 doesn't parse it).

import type { CSSProperties } from 'react';
import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import type { BasisChange, TrendWeekPoint } from '../../services/reportsApi';
import { useReportsLang } from './lang';

// Grid: keep only faint horizontal lines, drop vertical lines (the data line is the star)
export const chartGridProps = {
  strokeDasharray: '3 3',
  stroke: 'var(--grid-line)',
  vertical: false,
} as const;

// Axes: no axis lines or tick marks, thin muted text
export const chartAxisProps = {
  tickLine: false,
  axisLine: false,
  tick: { fontSize: 10, fill: 'var(--text-muted)' },
} as const;

// Tooltip container: follows the theme card background (fixes recharts' default glaring white background in the dark theme)
export const chartTooltipStyle: CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
  borderRadius: 8,
  fontSize: 11,
  color: 'var(--text-primary)',
  boxShadow: 'var(--shadow-card)',
};

export const chartTooltipLabelStyle: CSSProperties = {
  color: 'var(--text-muted)',
  marginBottom: 2,
};

// ── Metric definition change markers ────────────────────────────────────────────────────────────
// Draw a vertical line at the period where the numerator/denominator base or definition changed; the curve itself isn't broken (users still want the whole trend),
// and the specific reason goes into the note bar below the chart.

/** One period may have several changes (e.g. the query list changed and an engine was added on the same day) -> draw only one line per period.
 *  The label sits to the right of the line by default (the interval after the change is the new definition); near the right edge it flips left to avoid clipping.
 *  xKeys = the full sequence of x-axis values on the chart, used to determine where the marker falls. */
export function basisChangeLines(changes: BasisChange[], xOf: (key: string) => string,
                                 flagLabel: string, xKeys: string[] = []) {
  const seen = new Set<string>();
  return changes
    .filter(c => (seen.has(c.key) ? false : (seen.add(c.key), true)))
    .map(c => {
      const x = xOf(c.key);
      const i = xKeys.indexOf(x);
      const nearRight = xKeys.length > 0 && i >= 0 && i > xKeys.length * 0.75;
      return (
        <ReferenceLine key={`basis-${c.key}`} x={x}
          stroke="var(--warn-solid)" strokeDasharray="3 3" strokeWidth={1.5}
          label={{ value: `⚑ ${flagLabel}`,
            position: nearRight ? 'insideTopRight' : 'insideTopLeft',
            fontSize: 10, fill: 'var(--warn-fg)' }} />
      );
    });
}

/** Note bar below the chart -- one line per change: date + type + base before/after.
 *  Layout uses inline-block (the overview page is screenshot-exported to PDF, and flex + gap misaligns on the export fallback path). */
export function BasisChangeNote({ changes, title, hint, kindLabel, desc }: {
  changes: BasisChange[];
  title: string;
  hint: string;
  kindLabel: (kind: BasisChange['kind']) => string;
  desc: (c: BasisChange) => string;
}) {
  if (!changes.length) return null;
  return (
    <div className="mt-3 pt-2.5 text-[11px]"
      style={{ borderTop: '1px dashed var(--rpt-hairline)', color: 'var(--text-secondary)' }}>
      <div className="font-bold mb-1" style={{ color: 'var(--warn-fg)' }}>⚑ {title}</div>
      {changes.map(c => (
        <div key={`${c.key}-${c.kind}`} className="leading-5">
          <span className="rpt-num" style={{ color: 'var(--text-muted)' }}>{c.day}</span>
          <span className="ml-2 font-semibold" style={{ color: 'var(--text-primary)' }}>
            {kindLabel(c.kind)}
          </span>
          {desc(c) && <span className="ml-2 rpt-num">{desc(c)}</span>}
        </div>
      ))}
      <div className="mt-1.5" style={{ color: 'var(--text-muted)' }}>{hint}</div>
    </div>
  );
}

// Main series gradient area fill (placed in the chart's <defs>, referenced by Area via fill={`url(#${id})`})
// Data coloring uses --data-accent (direction A: buttons ink-blue, data blue, the two kept separate)
export function AreaGradientDefs({ id }: { id: string }) {
  return (
    <defs>
      <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="var(--data-accent)" stopOpacity={0.16} />
        <stop offset="100%" stopColor="var(--data-accent)" stopOpacity={0} />
      </linearGradient>
    </defs>
  );
}

// ── Weekly trend and forecast of overall exposure rate (shared by weekly / monthly reports) ────────────────────────────────
// Solid line = historical weeks; dashed line = 4 weeks linearly extrapolated at the current slope; target line + green interval from the first target-met week;
// a vertical line at the week of a metric definition change, with the reason in the note bar below the chart.

export function ExposureTrendLegend() {
  const L = useReportsLang();
  return (
    <div className="flex items-center gap-3 text-[10px] text-muted">
      <span className="flex items-center gap-1">
        <span className="inline-block w-4 h-0.5" style={{ background: 'var(--data-accent)' }} />
        {L.historyLegend}
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block w-4 border-t border-dashed" style={{ borderColor: 'var(--data-accent)' }} />
        {L.forecastLegend}
      </span>
    </div>
  );
}

export function ExposureTrendChart({ trend, basisChanges, exposureTarget }: {
  trend: TrendWeekPoint[];
  basisChanges: BasisChange[];
  exposureTarget: number | null;
}) {
  const L = useReportsLang();
  const history = trend.filter(t => !t.forecast);
  const chartData = trend.map(t => ({
    week: t.week_key.slice(5),
    history: t.forecast ? null : t.exposure_pct,
    forecast: t.forecast ? t.exposure_pct : null,
  }));
  // Connect the forecast line to the historical line
  const lastHistIdx = history.length - 1;
  if (lastHistIdx >= 0 && chartData[lastHistIdx]) {
    chartData[lastHistIdx].forecast = chartData[lastHistIdx].history;
  }
  // Target marker: the target comes from the backend (tier range lower bound, baseline × 1.3 when untagged); find the first target-met week and color the interval after it green
  const target = exposureTarget
    ?? (history.length ? Math.round(history[0].exposure_pct * 1.3 * 10) / 10 : null);
  const firstMetIdx = target !== null ? history.findIndex(t => t.exposure_pct >= target) : -1;
  const firstMetWeek = firstMetIdx >= 0 ? chartData[firstMetIdx].week : null;
  const lastHistWeek = lastHistIdx >= 0 ? chartData[lastHistIdx].week : null;
  // Metric definition changes: mark historical weeks only (extrapolated weeks have no real denominator); the x axis is likewise week numbers after slice(5)
  const weekKeys = new Set(history.map(t => t.week_key));
  const changes = (basisChanges ?? []).filter(c => weekKeys.has(c.key));

  return (
    <>
      <div style={{ height: 240 }}>
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
            <XAxis dataKey="week" tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} unit="%" />
            <Tooltip contentStyle={{ fontSize: 11 }} />
            {basisChangeLines(changes, k => k.slice(5), L.basisChangeFlag,
              chartData.map(d => d.week))}
            {firstMetWeek && lastHistWeek && firstMetWeek !== lastHistWeek && (
              <ReferenceArea x1={firstMetWeek} x2={lastHistWeek} fill="#22c55e" fillOpacity={0.07} />
            )}
            {target !== null && (
              <ReferenceLine y={target} stroke="#15803d" strokeDasharray="6 4"
                label={{ value: `${L.target} ${target}%`, position: 'insideTopRight',
                  fontSize: 10, fill: '#15803d' }} />
            )}
            {firstMetWeek && (
              <ReferenceLine x={firstMetWeek} stroke="#22c55e" strokeDasharray="4 3"
                label={{ value: `✓ ${L.progressMet} ${firstMetWeek}`, position: 'insideTopLeft',
                  fontSize: 10, fill: '#15803d' }} />
            )}
            <Line type="monotone" dataKey="history" name={L.historyLegend}
              stroke="var(--data-accent)" strokeWidth={2} dot={{ r: 2.5 }} connectNulls={false} />
            <Line type="monotone" dataKey="forecast" name={L.forecastLegend}
              stroke="var(--data-accent)" strokeWidth={1.5} strokeDasharray="5 4"
              dot={false} connectNulls={false} opacity={0.6} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <BasisChangeNote changes={changes} title={L.basisChangeTitle}
        hint={L.basisChangeHint} kindLabel={k => L.basisChangeKinds[k]}
        desc={L.basisChangeDesc} />
    </>
  );
}
