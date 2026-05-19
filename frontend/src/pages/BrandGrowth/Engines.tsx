import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import { aiTelemetryApi, type Overview } from '../../services/aiTelemetryApi';

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

  return (
    <div className="max-w-[1100px] mx-auto grid gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {engines.map(e => {
          const matrixRow = matrix[e] || {};
          const total = Object.values(matrixRow).reduce((s, n) => s + (n || 0), 0);
          return (
            <button
              key={e}
              type="button"
              onClick={() => navigate(`/brand-growth/responses?topic=${topic.id}&engine=${e}`)}
              className="p-3 rounded text-left hover:scale-[1.02] transition"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
            >
              <div className="text-sm text-primary font-medium">{e}</div>
              <div className="text-xs text-muted">引用 {total}</div>
            </button>
          );
        })}
      </div>

      <div className="p-4 rounded-lg overflow-x-auto" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <h3 className="text-sm font-medium text-primary mb-3">引擎 × 域名 热力图</h3>
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
    </div>
  );
}
