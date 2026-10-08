// Model usage -- overview KPI card row.
// Doesn't reuse KpiCard from pages/Reports/components.tsx: its metric.key is bound to the palette of the six report metrics,
// it hard-appends % after the value, and it carries a target progress bar. This only borrows its visual structure (top color bar + big number + subtitle).
import type { ReactNode } from 'react';

export interface KpiItem {
  key: string;
  label: string;
  value: string;
  /** Small text below the big number (metric definition note / absolute value supplement) */
  sub?: ReactNode;
  /** Period-over-period change; null = previous period was 0, can't compare */
  deltaPct?: number | null;
  /** This metric is "lower is better" (failure rate), so an increase is marked red */
  lowerIsBetter?: boolean;
  hue: string;
  /** Top-right reminder (e.g. insufficient token coverage) */
  warn?: string;
}

function DeltaTag({ pct, lowerIsBetter }: { pct?: number | null; lowerIsBetter?: boolean }) {
  if (pct === undefined) return null;
  if (pct === null) return <span className="text-[11px] text-muted">环比 —</span>;
  const up = pct > 0;
  const good = lowerIsBetter ? !up : up;
  const color = pct === 0 ? 'var(--text-muted)'
    : good ? 'var(--success-fg)' : 'var(--warn-fg)';
  return (
    <span className="text-[11px] tabular-nums" style={{ color }}>
      环比 {up ? '↑' : pct < 0 ? '↓' : ''}{Math.abs(pct)}%
    </span>
  );
}

export function UsageKpiRow({ items }: { items: KpiItem[] }) {
  return (
    <div className="grid gap-3"
         style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
      {items.map((it) => (
        <div key={it.key} className="rounded-md p-3 relative overflow-hidden"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <div className="absolute left-0 top-0 h-[2px] w-full" style={{ background: it.hue }} />
          <div className="flex items-start gap-2">
            <div className="text-[11px] text-muted">{it.label}</div>
            {it.warn && (
              <span className="ml-auto text-[10px] px-1 rounded"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-muted)' }}
                    title={it.warn}>!</span>
            )}
          </div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-primary">{it.value}</div>
          <div className="mt-1 flex items-center gap-2">
            {it.sub && <span className="text-[11px] text-muted">{it.sub}</span>}
            <span className="ml-auto"><DeltaTag pct={it.deltaPct} lowerIsBetter={it.lowerIsBetter} /></span>
          </div>
        </div>
      ))}
    </div>
  );
}
