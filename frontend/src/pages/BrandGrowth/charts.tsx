// 通用 SVG 图表 — 纯 SVG, 0 依赖, 暗色/亮色 theme 通过 currentColor + CSS var 自适应。

export interface DonutSlice {
  label: string;
  value: number;
  color: string;
}

export function DonutChart({ slices, size = 180, hole = 0.6, centerText, centerSub }: {
  slices: DonutSlice[];
  size?: number;
  hole?: number;
  centerText?: string;
  centerSub?: string;
}) {
  const total = slices.reduce((s, x) => s + x.value, 0);
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 2;
  const innerR = r * hole;
  if (total <= 0) {
    return (
      <svg viewBox={`0 0 ${size} ${size}`} className="w-full" style={{ maxWidth: size }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border-color)" strokeWidth="1" />
        <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" fontSize="11" fill="var(--text-muted)">暂无数据</text>
      </svg>
    );
  }
  let acc = 0;
  const arcs = slices.map((sl, i) => {
    const startAngle = (acc / total) * Math.PI * 2 - Math.PI / 2;
    acc += sl.value;
    const endAngle = (acc / total) * Math.PI * 2 - Math.PI / 2;
    const large = endAngle - startAngle > Math.PI ? 1 : 0;
    const x1 = cx + r * Math.cos(startAngle);
    const y1 = cy + r * Math.sin(startAngle);
    const x2 = cx + r * Math.cos(endAngle);
    const y2 = cy + r * Math.sin(endAngle);
    const xi1 = cx + innerR * Math.cos(endAngle);
    const yi1 = cy + innerR * Math.sin(endAngle);
    const xi2 = cx + innerR * Math.cos(startAngle);
    const yi2 = cy + innerR * Math.sin(startAngle);
    const d = [
      `M ${x1} ${y1}`,
      `A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`,
      `L ${xi1} ${yi1}`,
      `A ${innerR} ${innerR} 0 ${large} 0 ${xi2} ${yi2}`,
      'Z',
    ].join(' ');
    return <path key={i} d={d} fill={sl.color} />;
  });
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full" style={{ maxWidth: size }}>
      {arcs}
      {centerText && (
        <text x={cx} y={cy - (centerSub ? 7 : 0)} textAnchor="middle" dominantBaseline="middle"
          fontSize="22" fontWeight="600" fill="var(--text-primary)">
          {centerText}
        </text>
      )}
      {centerSub && (
        <text x={cx} y={cy + 14} textAnchor="middle" dominantBaseline="middle"
          fontSize="10" fill="var(--text-muted)">
          {centerSub}
        </text>
      )}
    </svg>
  );
}

export function DonutLegend({ slices, total }: { slices: DonutSlice[]; total?: number }) {
  const t = total ?? slices.reduce((s, x) => s + x.value, 0);
  return (
    <ul className="space-y-1.5 text-xs">
      {slices.map((s, i) => {
        const pct = t > 0 ? (s.value / t * 100) : 0;
        return (
          <li key={i} className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ background: s.color }} />
            <span className="flex-1 truncate text-secondary">{s.label}</span>
            <span className="tabular-nums text-primary">{s.value.toLocaleString()}</span>
            <span className="tabular-nums text-muted w-12 text-right">{pct.toFixed(1)}%</span>
          </li>
        );
      })}
    </ul>
  );
}

export interface BarItem {
  label: string;
  value: number;
  color?: string;
}

// 横向条形图(用于 Top N 域名/查询/竞品)
export function HorizontalBarChart({ items, formatValue, max }: {
  items: BarItem[];
  formatValue?: (v: number) => string;
  max?: number;
}) {
  const upper = max ?? Math.max(1, ...items.map(i => i.value));
  return (
    <ul className="space-y-2 text-xs">
      {items.map((it, idx) => {
        const pct = (it.value / upper) * 100;
        return (
          <li key={idx} className="flex items-center gap-3">
            <span className="w-36 truncate text-primary flex-shrink-0">{it.label}</span>
            <div className="flex-1 h-5 rounded relative overflow-hidden" style={{ background: 'var(--bg-input)' }}>
              <div className="h-full rounded transition-all"
                style={{ width: `${pct}%`, background: it.color || 'var(--accent-primary)' }} />
            </div>
            <span className="tabular-nums text-muted w-14 text-right flex-shrink-0">
              {formatValue ? formatValue(it.value) : it.value.toLocaleString()}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// 纵向条形图(用于 Top1/3/5 命中分布 / 时间序列)
export function VerticalBarChart({ items, height = 200, formatValue }: {
  items: BarItem[];
  height?: number;
  formatValue?: (v: number) => string;
}) {
  const max = Math.max(1, ...items.map(i => i.value));
  const barW = items.length > 0 ? 100 / items.length : 0;
  const innerPad = 0.3;
  return (
    <div style={{ height }}>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="w-full h-full">
        {[0.25, 0.5, 0.75, 1].map(g => (
          <line key={g} x1="0" x2="100" y1={height - g * (height - 24)} y2={height - g * (height - 24)}
            stroke="var(--border-color)" strokeWidth="0.3" strokeDasharray="2 2" />
        ))}
        {items.map((it, idx) => {
          const h = (it.value / max) * (height - 24);
          const x = idx * barW + barW * innerPad / 2;
          const w = barW * (1 - innerPad);
          const y = height - h - 18;
          return (
            <g key={idx}>
              <rect x={x} y={y} width={w} height={h} rx="0.4"
                fill={it.color || 'var(--accent-primary)'} />
              <text x={x + w / 2} y={height - 4} textAnchor="middle" fontSize="6" fill="var(--text-muted)">
                {it.label}
              </text>
              {it.value > 0 && (
                <text x={x + w / 2} y={y - 2} textAnchor="middle" fontSize="6" fill="var(--text-secondary)">
                  {formatValue ? formatValue(it.value) : it.value}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// 默认配色 — 暖冷对照,适合 5-10 个分类
export const CHART_PALETTE = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1',
];
