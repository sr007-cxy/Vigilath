import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type Overview, type ResponseRow,
} from '../../services/aiTelemetryApi';

type FilterMode = 'all' | 'owned' | 'third_party' | 'authoritative';

export function Sources() {
  return (
    <BrandGrowthShell title="信源分析" breadcrumb={[{ label: '品牌增长', to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const [params, setParams] = useSearchParams();
  const filter: FilterMode = (params.get('filter') as FilterMode) || 'all';
  const [overview, setOverview] = useState<Overview | null>(null);
  const [drawerDomain, setDrawerDomain] = useState<string | null>(null);

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getOverview(topic.id, period, token).then(setOverview).catch(() => setOverview(null));
  }, [token, topic?.id, period]);

  if (!topic || !overview) return <div className="text-muted">加载中…</div>;

  // owned 判定:domain 命中 brand_keywords(简化:domain 含 target 关键词)
  const brandKeys = overview.brand_keywords.map(k => k.toLowerCase());
  const isOwned = (d: string) => brandKeys.some(k => d.toLowerCase().includes(k));

  let domains = overview.top_domains;
  if (filter === 'owned') domains = domains.filter(d => isOwned(d.domain));
  if (filter === 'third_party') domains = domains.filter(d => !isOwned(d.domain));

  const setFilter = (f: FilterMode) => {
    const next = new URLSearchParams(params);
    if (f === 'all') next.delete('filter'); else next.set('filter', f);
    setParams(next, { replace: true });
  };

  return (
    <div className="max-w-[1100px] mx-auto grid gap-4">
      <div className="flex gap-1 p-0.5 rounded w-fit" style={{ background: 'var(--bg-input)' }}>
        {(['all', 'owned', 'third_party'] as FilterMode[]).map(f => (
          <button
            key={f} type="button" onClick={() => setFilter(f)}
            className="px-3 py-1 text-xs rounded"
            style={{
              background: filter === f ? 'var(--accent-primary)' : 'transparent',
              color: filter === f ? 'white' : 'var(--text-secondary)',
            }}
          >
            {f === 'all' ? '全部' : f === 'owned' ? '自有 / 权威' : '第三方'}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <h3 className="text-sm font-medium text-primary mb-3">Top 引用域名</h3>
          {domains.length === 0 ? (
            <div className="text-xs text-muted py-6 text-center">该过滤条件下暂无数据</div>
          ) : (
            <ul>
              {domains.map(d => (
                <li key={d.domain} className="flex items-center gap-2 py-1.5">
                  <span className="text-sm text-primary flex-shrink-0 w-44 truncate">{d.domain}</span>
                  <div className="flex-1 h-2 rounded overflow-hidden" style={{ background: 'var(--bg-input)' }}>
                    <div className="h-full" style={{ width: `${d.pct}%`, background: 'var(--accent-primary)' }} />
                  </div>
                  <span className="text-xs text-muted tabular-nums w-12 text-right">{d.count}</span>
                  <span className="text-xs text-muted tabular-nums w-16 text-right">{d.pct.toFixed(1)}%</span>
                  <button
                    type="button"
                    onClick={() => setDrawerDomain(d.domain)}
                    className="text-xs text-accent hover:underline"
                  >
                    样本
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <h3 className="text-sm font-medium text-primary mb-3">自有 vs 第三方</h3>
          <OwnedSplitPie owned={overview.owned_split.owned} other={overview.owned_split.other}
            ownedPct={overview.owned_split.owned_pct} />
        </div>
      </div>

      {drawerDomain && (
        <DomainSamplesDrawer
          topicId={topic.id} domain={drawerDomain} period={period} token={token}
          onClose={() => setDrawerDomain(null)}
        />
      )}
    </div>
  );
}

function OwnedSplitPie({ owned, other, ownedPct }: { owned: number; other: number; ownedPct: number }) {
  const total = owned + other;
  if (total === 0) return <div className="text-xs text-muted text-center py-6">暂无数据</div>;
  return (
    <div>
      <div className="flex h-3 rounded overflow-hidden" style={{ background: 'var(--bg-input)' }}>
        <div className="h-full" style={{ width: `${ownedPct}%`, background: 'var(--accent-primary)' }} />
      </div>
      <div className="flex justify-between mt-2 text-xs">
        <span><span className="inline-block w-2 h-2 mr-1 align-middle" style={{ background: 'var(--accent-primary)' }} />自有 {owned} ({ownedPct.toFixed(1)}%)</span>
        <span className="text-muted">第三方 {other}</span>
      </div>
    </div>
  );
}

function DomainSamplesDrawer({
  topicId, domain, period, token, onClose,
}: {
  topicId: number; domain: string; period: number; token: string; onClose: () => void;
}) {
  const [rows, setRows] = useState<ResponseRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    aiTelemetryApi.listTopicResponses(topicId, token, { domain, period, limit: 50 })
      .then(setRows).catch(() => setRows([])).finally(() => setLoading(false));
  }, [topicId, domain, period, token]);

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end" onClick={onClose}>
      <div className="w-full max-w-2xl h-full overflow-y-auto p-4" onClick={e => e.stopPropagation()}
        style={{ background: 'var(--bg-card)' }}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-primary">引用样本 · {domain}</h3>
          <button type="button" onClick={onClose} className="text-xs text-muted">关闭</button>
        </div>
        {loading ? (
          <div className="text-xs text-muted text-center py-6">加载中…</div>
        ) : rows.length === 0 ? (
          <div className="text-xs text-muted text-center py-6">该域名近 {period} 天无引用样本</div>
        ) : (
          <ul className="space-y-2">
            {rows.map(r => (
              <li key={r.id} className="p-2 rounded text-xs" style={{ background: 'var(--bg-input)' }}>
                <div className="flex justify-between mb-1">
                  <span className="text-primary">{r.engine} · {r.query}</span>
                  <span className="text-muted">{new Date(r.created_at).toLocaleDateString()}</span>
                </div>
                {r.hit_excerpt && <div className="text-secondary line-clamp-3">{r.hit_excerpt}</div>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
