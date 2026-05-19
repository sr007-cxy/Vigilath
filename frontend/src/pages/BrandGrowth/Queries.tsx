import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type TrackingMatrix, type IntentBreakdown,
} from '../../services/aiTelemetryApi';

export function Queries() {
  return (
    <BrandGrowthShell title="关键词管理" breadcrumb={[{ label: '品牌增长', to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const navigate = useNavigate();
  const [matrix, setMatrix] = useState<TrackingMatrix | null>(null);
  const [intent, setIntent] = useState<IntentBreakdown | null>(null);

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getTrackingMatrix(topic.id, token).then(setMatrix).catch(() => setMatrix(null));
    aiTelemetryApi.getIntentBreakdown(topic.id, period, token).then(setIntent).catch(() => setIntent(null));
  }, [token, topic?.id, period]);

  const queryRows = useMemo(() => {
    if (!matrix) return [];
    return matrix.queries.map(q => {
      const cells = matrix.cells.filter(c => c.query === q);
      const totalRuns = cells.reduce((s, c) => s + c.total_runs, 0);
      const totalHits = cells.reduce((s, c) => s + c.total_hits, 0);
      const firstHitEngine = cells.filter(c => c.first_hit_at).sort((a, b) =>
        (a.first_hit_at || '').localeCompare(b.first_hit_at || ''))[0]?.engine || '';
      return {
        query: q, totalRuns, totalHits,
        rate: totalRuns ? totalHits / totalRuns : 0,
        firstHitEngine,
      };
    });
  }, [matrix]);

  if (!topic || !matrix) return <div className="text-muted">加载中…</div>;

  return (
    <div className="grid gap-4">
      <div className="text-xs text-muted">
        共 {matrix.queries.length} 条 query · {matrix.engines.length} 个引擎
        · 编辑入口在 admin 工作台
      </div>

      <div className="p-3 rounded-lg overflow-x-auto" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted">
              <th className="text-left px-2 py-2">Query</th>
              <th className="text-right px-2 py-2">命中率</th>
              <th className="text-right px-2 py-2">runs</th>
              <th className="text-right px-2 py-2">hits</th>
              <th className="text-left px-2 py-2">首次命中引擎</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {queryRows.map(r => (
              <tr key={r.query} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                <td className="px-2 py-2 text-primary truncate max-w-[360px]">{r.query}</td>
                <td className="px-2 py-2 text-right tabular-nums">{(r.rate * 100).toFixed(1)}%</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.totalRuns}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.totalHits}</td>
                <td className="px-2 py-2 text-primary">{r.firstHitEngine || '—'}</td>
                <td className="px-2 py-2">
                  <button
                    type="button"
                    onClick={() => navigate(`/brand-growth/matrix?topic=${topic.id}&q=${encodeURIComponent(r.query)}`)}
                    className="text-xs text-accent hover:underline"
                  >
                    查矩阵 →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {intent && intent.clusters.length > 0 && (
        <div className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <h3 className="text-sm font-medium text-primary mb-3">Intent 簇分布</h3>
          <ul className="space-y-2">
            {intent.clusters.map(c => (
              <li key={c.cluster_id} className="flex items-center gap-2 text-xs">
                <span className="text-primary w-40 truncate">{c.label}</span>
                <div className="flex-1 h-2 rounded overflow-hidden" style={{ background: 'var(--bg-input)' }}>
                  <div className="h-full" style={{
                    width: `${c.mention_rate * 100}%`,
                    background: c.mention_rate >= 0.5 ? 'var(--accent-primary)' : c.mention_rate >= 0.25 ? '#f59e0b' : '#ef4444',
                  }} />
                </div>
                <span className="text-muted tabular-nums w-20 text-right">
                  {(c.mention_rate * 100).toFixed(0)}% ({c.mention_count}/{c.response_count})
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
