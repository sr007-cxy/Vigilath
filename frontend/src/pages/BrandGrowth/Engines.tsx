import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import { aiTelemetryApi, type Overview } from '../../services/aiTelemetryApi';
import { VerticalBarChart, CHART_PALETTE, type BarItem } from './charts';

export function Engines() {
  return (
    <BrandGrowthShell title="平台分析" breadcrumb={[{ label: '品牌增长', to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const navigate = useNavigate();
  const [overview, setOverview] = useState<Overview | null>(null);

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getOverview(topic.id, period, token).then(setOverview).catch(() => setOverview(null));
  }, [token, topic?.id, period]);

  if (!topic || !overview) return <div className="text-muted">加载中…</div>;

  const engines = overview.engines;
  const matrix = overview.engine_domain_matrix;
  const domains = overview.top_domains.slice(0, 12);

  const engineTotals: { engine: string; total: number }[] = engines.map(e => ({
    engine: e, total: Object.values(matrix[e] || {}).reduce((s, n) => s + (n || 0), 0),
  }));
  const barItems: BarItem[] = engineTotals.map((e, i) => ({
    label: e.engine, value: e.total, color: CHART_PALETTE[i % CHART_PALETTE.length],
  }));

  return (
    <div className="grid gap-4">
      {/* 引擎引用分布 — 柱状图 */}
      <Card title="各引擎引用次数">
        <VerticalBarChart items={barItems} height={220} formatValue={v => String(v)} />
      </Card>

      {/* 引擎卡片(可点跳详情) */}
      <Card title="引擎概览">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
          {engineTotals.map((e, i) => (
            <button
              key={e.engine}
              type="button"
              onClick={() => navigate(`/brand-growth/responses?topic=${topic.id}&engine=${e.engine}`)}
              className="p-3 rounded-lg text-left hover:scale-[1.03] transition flex items-center gap-2"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
            >
              <span className="w-2 h-8 rounded-sm flex-shrink-0"
                style={{ background: CHART_PALETTE[i % CHART_PALETTE.length] }} />
              <span className="flex-1 min-w-0">
                <span className="block text-sm text-primary font-medium truncate">{e.engine}</span>
                <span className="block text-xs text-muted">引用 {e.total}</span>
              </span>
            </button>
          ))}
        </div>
      </Card>

      {/* 引擎 × 域名 热力图 */}
      <Card title="引擎 × 域名 热力图">
        <div className="overflow-x-auto">
          <table className="text-xs">
            <thead>
              <tr>
                <th className="text-left px-2 py-1 sticky left-0" style={{ background: 'var(--bg-card)' }}>引擎</th>
                {domains.map(d => (
                  <th key={d.domain} className="px-2 py-1 text-muted whitespace-nowrap">{d.domain}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {engines.map(e => (
                <tr key={e}>
                  <td className="px-2 py-1 text-primary sticky left-0" style={{ background: 'var(--bg-card)' }}>{e}</td>
                  {domains.map(d => {
                    const v = matrix[e]?.[d.domain] || 0;
                    const intensity = Math.min(1, v / Math.max(1, d.count));
                    return (
                      <td key={d.domain} className="px-2 py-1 tabular-nums text-center"
                        style={{ background: v > 0 ? `rgba(59,130,246,${0.1 + intensity * 0.6})` : 'transparent' }}>
                        {v || ''}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <h3 className="text-sm font-medium text-primary mb-3 pb-2 border-b flex items-center gap-2"
        style={{ borderColor: 'var(--border-color)' }}>
        <span className="w-1 h-4 rounded-sm" style={{ background: 'var(--accent-primary)' }} />
        {title}
      </h3>
      {children}
    </div>
  );
}
