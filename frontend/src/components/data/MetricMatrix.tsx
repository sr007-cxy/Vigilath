// Six-metric matrix -- pure display core.
//
// The report center (monthly "per platform x six metrics") and one-click full diagnosis share the same rendering,
// keeping the metric definitions and look delivered to customers consistent -- a copy would inevitably drift.
//
// The core depends on no i18n, no API types, and no scope-private CSS variables;
// everything comes in via props (the diagnosis page isn't inside .reports-shell and can't get the --border-color redefined there;
// the very same pitfall as when an undefined --data-accent made the bars transparent).

import type { CSSProperties, ReactNode } from 'react';

export type MetricKey = 'exposure' | 'top1' | 'top3' | 'accuracy' | 'citation' | 'mention';

/** Column order is uniform site-wide */
export const METRIC_ORDER: MetricKey[] = [
  'exposure', 'top1', 'top3', 'accuracy', 'citation', 'mention',
];

export interface MetricMatrixRow {
  key: string;
  label: string;
  values: Partial<Record<MetricKey, number | null>>;
  /** Optional denominator, shown on hover as "12 / 40" */
  denom?: Partial<Record<MetricKey, number>>;
  /** Row-end badge, e.g. "not collected successfully this time" */
  note?: string;
}

export interface MetricMatrixProps {
  rows: MetricMatrixRow[];
  columns?: MetricKey[];
  columnLabels: Record<MetricKey, string>;
  columnHints?: Partial<Record<MetricKey, string>>;
  rowHeader: string;
  /** Per-column heat coloring. Only for "higher is better" metrics; if inverse metrics are added later the scheme must change */
  heat?: 'column' | 'none';
  /** CSS variable name for the heat base color; its value must be an RGB triple (e.g. `34, 197, 94`) */
  heatVar?: string;
  /** Injected tooltip renderer; the core itself doesn't depend on any specific component */
  renderHint?: (text: string) => ReactNode;
  renderTh?: (content: ReactNode, key: string) => ReactNode;
  emptyText?: string;
  dense?: boolean;
}

export function MetricMatrix({
  rows,
  columns = METRIC_ORDER,
  columnLabels,
  columnHints,
  rowHeader,
  heat = 'column',
  heatVar = '--matrix-heat',
  renderHint,
  emptyText = '—',
  dense = false,
}: MetricMatrixProps) {
  // Per-column max/min values, for normalized coloring
  const stats = new Map<MetricKey, { max: number; min: number; any: boolean }>();
  for (const c of columns) {
    let max = -Infinity, min = Infinity, any = false;
    for (const r of rows) {
      const v = r.values[c];
      if (v == null || Number.isNaN(v)) continue;
      any = true;
      if (v > max) max = v;
      if (v < min) min = v;
    }
    stats.set(c, { max, min, any });
  }

  const cellBg = (c: MetricKey, v: number | null | undefined): CSSProperties => {
    if (heat !== 'column' || v == null) return {};
    const s = stats.get(c);
    // No coloring when all values in a column are equal (including an all-0 column) -- otherwise an all-0 column turns solid green and readers think everything is great
    if (!s || !s.any || s.max === s.min || s.max <= 0) return {};
    const alpha = Math.min(0.35, (v / s.max) * 0.35);
    return { background: `rgba(var(${heatVar}), ${alpha.toFixed(3)})` };
  };

  const pad = dense ? 'px-2 py-1.5' : 'px-3 py-2';

  return (
    <div className="overflow-x-auto rounded-xl border border-soft">
      <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ background: 'var(--bg-tertiary)' }}>
            <th className={`text-left ${pad} font-medium text-secondary whitespace-nowrap`}>
              {rowHeader}
            </th>
            {columns.map((c) => {
              const s = stats.get(c);
              const dead = !s?.any;   // whole column has no data: mark it but don't hide it; hiding would misalign horizontal comparison
              return (
                <th key={c}
                    className={`${pad} font-medium text-secondary text-center whitespace-nowrap`}
                    style={dead ? { opacity: 0.5 } : undefined}>
                  <span className="inline-flex items-center gap-1">
                    {columnLabels[c]}
                    {columnHints?.[c] && renderHint?.(columnHints[c]!)}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} style={{ borderTop: '1px solid var(--grid-line)' }}>
              <td className={`${pad} text-primary whitespace-nowrap`}>
                {r.label}
                {r.note && (
                  <span className="ml-2 text-[11px] px-1.5 py-0.5 rounded align-middle"
                        style={{ background: 'var(--neutral-bg)', color: 'var(--neutral-fg)' }}>
                    {r.note}
                  </span>
                )}
              </td>
              {columns.map((c) => {
                const v = r.values[c];
                const d = r.denom?.[c];
                return (
                  <td key={c}
                      className={`${pad} text-center tabular-nums`}
                      style={cellBg(c, v)}
                      title={d != null && v != null ? `${Math.round((v / 100) * d)} / ${d}` : undefined}>
                    {v == null
                      ? <span className="text-muted">{emptyText}</span>
                      : <span className="text-primary">{v}%</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
