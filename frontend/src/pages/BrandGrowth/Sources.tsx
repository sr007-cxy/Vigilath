import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type Overview, type ResponseRow,
} from '../../services/aiTelemetryApi';
import {
  DonutChart, DonutLegend, HorizontalBarChart, CHART_PALETTE,
  type DonutSlice, type BarItem,
} from './charts';

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

  // owned 判定:domain 命中 brand_keywords
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

  const ownedPct = overview.owned_split.owned_pct;
  const ownedSlices: DonutSlice[] = overview.owned_split.owned + overview.owned_split.other > 0
    ? [
        { label: '自有 / 权威', value: overview.owned_split.owned, color: '#10b981' },
        { label: '第三方', value: overview.owned_split.other, color: '#94a3b8' },
      ]
    : [];

  const topNSlices: DonutSlice[] = overview.top_domains.slice(0, 7).map((d, i) => ({
    label: d.domain, value: d.count, color: CHART_PALETTE[i % CHART_PALETTE.length],
  }));
  const otherCount = overview.top_domains.slice(7).reduce((s, d) => s + d.count, 0);
  if (otherCount > 0) topNSlices.push({ label: '其它', value: otherCount, color: '#475569' });

  const barItems: BarItem[] = domains.map((d, i) => ({
    label: d.domain, value: d.count, color: CHART_PALETTE[i % CHART_PALETTE.length],
  }));

  return (
    <div className="grid gap-4">
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

      {/* 双 donut 行:自有/第三方 + Top 域名构成 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card title="自有 vs 第三方">
          <div className="grid grid-cols-[auto_1fr] gap-4 items-center flex-1">
            <DonutChart
              slices={ownedSlices}
              centerText={`${ownedPct.toFixed(1)}%`}
              centerSub="自有占比"
            />
            <DonutLegend slices={ownedSlices} />
          </div>
        </Card>
        <Card title="Top 域名构成">
          <div className="grid grid-cols-[auto_1fr] gap-4 items-center flex-1">
            <DonutChart slices={topNSlices} centerText={String(overview.citations.value)} centerSub="总引用" />
            <DonutLegend slices={topNSlices} />
          </div>
        </Card>
      </div>

      {/* Top 引用域名条形图 */}
      <Card
        title={filter === 'all' ? `Top ${domains.length} 引用域名`
          : filter === 'owned' ? '自有 / 权威域名'
          : '第三方域名'}
      >
        {domains.length === 0 ? (
          <div className="text-xs text-muted py-6 text-center">该过滤条件下暂无数据</div>
        ) : (
          <>
            <HorizontalBarChart items={barItems} formatValue={v => v.toLocaleString()} />
            <div className="mt-3 flex flex-wrap gap-2">
              {domains.slice(0, 12).map(d => (
                <button
                  key={d.domain} type="button"
                  onClick={() => setDrawerDomain(d.domain)}
                  className="text-xs px-2 py-0.5 rounded hover:underline"
                  style={{ background: 'var(--bg-input)', color: 'var(--accent-primary)' }}
                >
                  {d.domain} · 看样本 →
                </button>
              ))}
            </div>
          </>
        )}
      </Card>

      {drawerDomain && (
        <DomainSamplesDrawer
          topicId={topic.id} domain={drawerDomain} period={period} token={token}
          onClose={() => setDrawerDomain(null)}
        />
      )}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-4 rounded-lg flex flex-col"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <h3 className="text-sm font-medium text-primary mb-3 pb-2 border-b flex items-center gap-2"
        style={{ borderColor: 'var(--border-color)' }}>
        <span className="w-1 h-4 rounded-sm" style={{ background: 'var(--accent-primary)' }} />
        {title}
      </h3>
      <div className="flex-1 flex flex-col">{children}</div>
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
