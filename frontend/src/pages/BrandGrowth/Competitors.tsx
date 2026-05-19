import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type ShareOfVoice, type CompetitorSubstitutionResp,
  type CompetitorSubstitutionItem,
} from '../../services/aiTelemetryApi';

export function Competitors() {
  return (
    <BrandGrowthShell title="竞品分析" breadcrumb={[{ label: '品牌增长', to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const [sov, setSov] = useState<ShareOfVoice | null>(null);
  const [subs, setSubs] = useState<CompetitorSubstitutionResp | null>(null);
  const [selectedComp, setSelectedComp] = useState<string | null>(null);

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getShareOfVoice(topic.id, period, token).then(setSov).catch(() => setSov(null));
    aiTelemetryApi.getCompetitorSubstitutions(topic.id, period, token, { limit: 100 })
      .then(setSubs).catch(() => setSubs(null));
  }, [token, topic?.id, period]);

  if (!topic) return null;

  const filteredItems = selectedComp
    ? (subs?.items ?? []).filter(i => i.competitor_name === selectedComp)
    : (subs?.items ?? []);

  return (
    <div className="max-w-[1100px] mx-auto grid gap-4">
      {sov && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <h3 className="text-sm font-medium text-primary mb-2">声量份额 SAIV</h3>
            <div className="text-3xl font-bold text-primary">{sov.saiv_pct.toFixed(1)}%</div>
            <div className="text-xs text-muted">本品 {sov.brand_count} / 竞品总 {sov.competitors_count_total}</div>
          </div>
          <div className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <h3 className="text-sm font-medium text-primary mb-2">竞品排行</h3>
            <ul className="space-y-1.5 max-h-48 overflow-y-auto">
              {sov.competitors.slice(0, 10).map(c => (
                <li key={c.name} className="flex items-center gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => setSelectedComp(selectedComp === c.name ? null : c.name)}
                    className="text-left flex-1 truncate hover:underline"
                    style={{ color: selectedComp === c.name ? 'var(--accent-primary)' : 'var(--text-primary)' }}
                  >
                    {c.name}
                  </button>
                  <span className="text-muted tabular-nums">{c.count}</span>
                  <span className="text-muted tabular-nums">{c.pct.toFixed(1)}%</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-primary">
            被替代证据 — 提了竞品没提我{selectedComp && `(${selectedComp})`}
          </h3>
          {selectedComp && (
            <button type="button" className="text-xs text-muted" onClick={() => setSelectedComp(null)}>清除筛选</button>
          )}
        </div>
        {filteredItems.length === 0 ? (
          <div className="text-xs text-muted text-center py-6">暂无替代证据(或本期未抽取出竞品)</div>
        ) : (
          <SubstitutionTable items={filteredItems} topicId={topic.id} />
        )}
      </div>
    </div>
  );
}

function SubstitutionTable({ items, topicId }: { items: CompetitorSubstitutionItem[]; topicId: number }) {
  const navigate = useNavigate();
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-muted">
          <th className="text-left px-2 py-1.5">Query</th>
          <th className="text-left px-2 py-1.5">竞品</th>
          <th className="text-right px-2 py-1.5">次数</th>
          <th className="text-left px-2 py-1.5">证据</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {items.slice(0, 50).map((i, idx) => (
          <tr key={idx} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
            <td className="px-2 py-1.5 text-primary">{i.query}</td>
            <td className="px-2 py-1.5 text-primary">{i.competitor_name}</td>
            <td className="px-2 py-1.5 text-right tabular-nums">{i.competitor_count}</td>
            <td className="px-2 py-1.5 text-secondary truncate max-w-[260px]">{i.sample_snippet}</td>
            <td className="px-2 py-1.5">
              <button
                type="button"
                onClick={() => navigate(`/brand-growth/matrix?topic=${topicId}&q=${encodeURIComponent(i.query)}`)}
                className="text-xs text-accent hover:underline"
              >
                查矩阵 →
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
