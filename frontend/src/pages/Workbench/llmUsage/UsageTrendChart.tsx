// Model usage -- daily trend. Area = tokens, right-axis line = call count.
// Style constants come from pages/Reports/chartkit.tsx (color-mix forbidden; html2canvas 1.4.1 doesn't parse it).
import { useMemo } from 'react';
import {
  Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  AreaGradientDefs, chartAxisProps, chartGridProps, chartTooltipLabelStyle, chartTooltipStyle,
} from '../../Reports/chartkit';
import { fmtCompact } from '../../../services/adminLlmUsageApi';
import type { UsageTrend } from '../../../services/adminLlmUsageApi';

const SERIES_HUES = [
  'var(--data-accent)', '#7c9cf5', '#5bc0a8', '#e0a45e', '#c485d6', '#7fb3d5',
];

export type TrendMetric = 'total_tokens' | 'calls' | 'error_calls';

const METRIC_LABEL: Record<TrendMetric, string> = {
  total_tokens: 'token 用量',
  calls: '调用次数',
  error_calls: '失败请求',
};

export function UsageTrendChart({
  data, metric, height = 260,
}: { data: UsageTrend | null; metric: TrendMetric; height?: number }) {
  const rows = useMemo(() => {
    if (!data) return [];
    return data.days.map((d, i) => {
      const row: Record<string, string | number> = { day: d.slice(5) };
      data.series.forEach((s) => { row[s.key] = s[metric][i] ?? 0; });
      // With a single series, additionally draw call count on the right axis, to see at a glance "are calls rising or is each call getting pricier"
      if (data.series.length === 1) row.__calls = data.series[0].calls[i] ?? 0;
      return row;
    });
  }, [data, metric]);

  if (!data || !data.series.length) {
    return <div className="text-xs text-muted py-10 text-center">该时间范围内没有调用记录</div>;
  }
  const single = data.series.length === 1;

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <AreaGradientDefs id="llm-usage-trend" />
          <CartesianGrid {...chartGridProps} />
          <XAxis dataKey="day" {...chartAxisProps} minTickGap={24} />
          <YAxis {...chartAxisProps} tickFormatter={(v: number) => fmtCompact(v)} />
          {single && (
            <YAxis yAxisId="r" orientation="right" {...chartAxisProps}
                   tickFormatter={(v: number) => fmtCompact(v)} />
          )}
          <Tooltip
            contentStyle={chartTooltipStyle}
            labelStyle={chartTooltipLabelStyle}
            formatter={(v, name) =>
              [fmtCompact(Number(v)), name === '__calls' ? '调用次数' : String(name)]}
          />
          {!single && <Legend wrapperStyle={{ fontSize: 11 }} />}
          {data.series.map((s, i) => (
            <Area key={s.key} type="monotone" dataKey={s.key}
                  name={single ? METRIC_LABEL[metric] : s.key}
                  stroke={SERIES_HUES[i % SERIES_HUES.length]} strokeWidth={2}
                  fill={single ? 'url(#llm-usage-trend)' : SERIES_HUES[i % SERIES_HUES.length]}
                  fillOpacity={single ? 1 : 0.12}
                  dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--bg-card)' }} />
          ))}
          {single && metric !== 'calls' && (
            <Line yAxisId="r" type="monotone" dataKey="__calls" name="调用次数"
                  stroke="var(--text-muted)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
