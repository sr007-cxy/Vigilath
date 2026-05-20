import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type Overview, type ResponseRow,
} from '../../services/aiTelemetryApi';
import {
  DonutChart, DonutLegend, HorizontalBarChart, InfoHint, CHART_PALETTE,
  type DonutSlice, type BarItem,
} from './charts';
import { useBgLang, engineLabel } from './lang';

type FilterMode = 'all' | 'owned' | 'third_party';

export function Sources() {
  const L = useBgLang();
  return (
    <BrandGrowthShell title={L.sourcesTitle} breadcrumb={[{ label: L.pageTitle, to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const L = useBgLang();
  const [params, setParams] = useSearchParams();
  const filter: FilterMode = (params.get('filter') as FilterMode) || 'all';
  const [overview, setOverview] = useState<Overview | null>(null);
  const [drawerDomain, setDrawerDomain] = useState<string | null>(null);

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getOverview(topic.id, period, token).then(setOverview).catch(() => setOverview(null));
  }, [token, topic?.id, period]);

  if (!topic || !overview) return <div className="text-muted">{L.loading}</div>;

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

  // Top 域名构成 donut(top 7 + 其它)
  const topNSlices: DonutSlice[] = overview.top_domains.slice(0, 7).map((d, i) => ({
    label: d.domain, value: d.count, color: CHART_PALETTE[i % CHART_PALETTE.length],
  }));
  const otherCount = overview.top_domains.slice(7).reduce((s, d) => s + d.count, 0);
  if (otherCount > 0) topNSlices.push({ label: '其它', value: otherCount, color: '#475569' });

  const barItems: BarItem[] = domains.map((d, i) => ({
    label: d.domain, value: d.count, color: CHART_PALETTE[i % CHART_PALETTE.length],
  }));

  const totalCitations = overview.citations.value;
  const uniqueDomains = overview.top_domains.length;
  const ownedPct = overview.owned_split.owned_pct;

  return (
    <div className="grid gap-4">
      {/* 顶部摘要 + 筛选 */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-4 items-start">
        <div className="grid grid-cols-3 gap-3">
          <StatTile label={L.sourcesCenterTotal} value={totalCitations.toLocaleString()} hint={L.hintTopMetrics}
            tint={{ bg: 'rgba(59,130,246,0.10)', fg: '#2563eb' }} />
          <StatTile label="唯一域名数" value={uniqueDomains.toLocaleString()} hint={L.hintSourcesUnique}
            tint={{ bg: 'rgba(16,185,129,0.10)', fg: '#10b981' }} />
          <StatTile label={L.sourcesCenterOwned} value={`${ownedPct.toFixed(1)}%`} hint={L.hintSourcesOwnedPct}
            tint={{ bg: 'rgba(244,114,182,0.10)', fg: '#db2777' }} />
        </div>
        <div className="flex items-center gap-1.5">
          <div className="flex gap-1 p-0.5 rounded w-fit" style={{ background: 'var(--bg-input)' }}>
            {(['all', 'owned', 'third_party'] as FilterMode[]).map(f => (
              <button
                key={f} type="button" onClick={() => setFilter(f)}
                className="px-3 py-1 text-xs rounded whitespace-nowrap"
                style={{
                  background: filter === f ? 'var(--accent-primary)' : 'transparent',
                  color: filter === f ? 'white' : 'var(--text-secondary)',
                }}
              >
                {f === 'all' ? L.sourcesFilterAll : f === 'owned' ? L.sourcesFilterOwned : L.sourcesFilterThird}
              </button>
            ))}
          </div>
          <InfoHint text={L.hintSourcesFilter} />
        </div>
      </div>

      {/* 主图区:donut + bar 横排 */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card title={L.sourcesTopComposition} hint={L.hintSources} className="lg:col-span-2">
          {topNSlices.length === 0 ? (
            <div className="text-xs text-muted py-10 text-center">{L.sourcesNoData}</div>
          ) : (
            <div className="flex flex-col items-center gap-3 flex-1">
              <DonutChart slices={topNSlices} size={200}
                centerText={totalCitations.toLocaleString()} centerSub={L.sourcesCenterTotal} />
              <div className="w-full max-h-44 overflow-y-auto px-1">
                <DonutLegend slices={topNSlices} />
              </div>
            </div>
          )}
        </Card>

        <Card
          title={
            filter === 'all' ? L.sourcesTopList(domains.length)
              : filter === 'owned' ? L.sourcesOwnedList
              : L.sourcesThirdPartyList
          }
          className="lg:col-span-3"
        >
          {domains.length === 0 ? (
            <div className="text-xs text-muted py-10 text-center">{L.sourcesNoData}</div>
          ) : (
            <HorizontalBarChart items={barItems} formatValue={v => v.toLocaleString()} />
          )}
        </Card>
      </div>

      {/* 域名 × 引擎 拆分 */}
      {domains.length > 0 && (
        <Card title="域名 × 引擎 引用拆分" hint={L.hintSourcesDomainEngine}>
          <p className="text-[11px] text-muted mb-3">
            点击域名查看具体引用样本;条形按引擎拆分,看每个 AI 引擎都引用了谁。
          </p>
          <ul className="space-y-2">
            {domains.slice(0, 15).map(d => (
              <li key={d.domain}>
                <DomainRow
                  domain={d.domain}
                  total={d.count}
                  engineCounts={extractEngineCounts(overview.engine_domain_matrix, d.domain)}
                  engines={overview.engines}
                  onClick={() => setDrawerDomain(d.domain)}
                />
              </li>
            ))}
          </ul>
        </Card>
      )}

      {drawerDomain && (
        <DomainSamplesDrawer
          topicId={topic.id} domain={drawerDomain} period={period} token={token}
          onClose={() => setDrawerDomain(null)}
        />
      )}
    </div>
  );
}

function extractEngineCounts(
  matrix: Partial<Record<string, Record<string, number>>>,
  domain: string,
): Array<{ engine: string; count: number }> {
  const out: Array<{ engine: string; count: number }> = [];
  for (const [eng, dmap] of Object.entries(matrix || {})) {
    const v = dmap?.[domain] || 0;
    if (v > 0) out.push({ engine: eng, count: v });
  }
  out.sort((a, b) => b.count - a.count);
  return out;
}

function DomainRow({
  domain, total, engineCounts, onClick,
}: {
  domain: string;
  total: number;
  engineCounts: Array<{ engine: string; count: number }>;
  engines: string[];
  onClick: () => void;
}) {
  const sum = engineCounts.reduce((s, x) => s + x.count, 0) || total;
  return (
    <button
      type="button" onClick={onClick}
      className="w-full grid grid-cols-[10rem_1fr_auto] items-center gap-3 px-2 py-1.5 rounded text-xs hover:bg-[var(--bg-input)] transition text-left"
    >
      <span className="text-primary truncate" title={domain}>{domain}</span>
      <div className="flex h-3 rounded overflow-hidden" style={{ background: 'var(--bg-input)' }}>
        {engineCounts.map((ec, i) => {
          const w = (ec.count / sum) * 100;
          const color = CHART_PALETTE[i % CHART_PALETTE.length];
          return (
            <div
              key={ec.engine}
              style={{ width: `${w}%`, background: color }}
              title={`${engineLabel(ec.engine)}: ${ec.count}`}
            />
          );
        })}
      </div>
      <span className="tabular-nums text-muted w-14 text-right">{total}</span>
      <span className="col-span-3 text-[10px] text-muted flex flex-wrap gap-2 pl-[10.5rem]">
        {engineCounts.map((ec, i) => (
          <span key={ec.engine} className="inline-flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-sm"
              style={{ background: CHART_PALETTE[i % CHART_PALETTE.length] }} />
            {engineLabel(ec.engine)} {ec.count}
          </span>
        ))}
      </span>
    </button>
  );
}

function StatTile({ label, value, tint, hint }: {
  label: string; value: string; tint: { bg: string; fg: string }; hint?: string;
}) {
  return (
    <div className="p-3 rounded-lg flex items-center gap-3"
      style={{ background: tint.bg, border: '1px solid var(--border-color)' }}>
      <span className="w-1.5 h-8 rounded-sm flex-shrink-0" style={{ background: tint.fg }} />
      <div className="min-w-0">
        <div className="text-[10px] text-muted truncate flex items-center gap-1">
          {label}{hint && <InfoHint text={hint} />}
        </div>
        <div className="text-lg font-bold tabular-nums" style={{ color: tint.fg }}>{value}</div>
      </div>
    </div>
  );
}

function Card({ title, hint, className, children }: {
  title: string; hint?: string; className?: string; children: React.ReactNode;
}) {
  return (
    <div className={`p-4 rounded-lg flex flex-col ${className ?? ''}`}
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <h3 className="text-sm font-medium text-primary mb-3 pb-2 border-b flex items-center gap-2"
        style={{ borderColor: 'var(--border-color)' }}>
        <span className="w-1 h-4 rounded-sm" style={{ background: 'var(--accent-primary)' }} />
        {title}
        {hint && <InfoHint text={hint} />}
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
  const L = useBgLang();
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
          <h3 className="text-sm font-medium text-primary">{L.sourcesDrawerTitle(domain)}</h3>
          <button type="button" onClick={onClose} className="text-xs text-muted">{L.matrixDrawerClose}</button>
        </div>
        {loading ? (
          <div className="text-xs text-muted text-center py-6">{L.loading}</div>
        ) : rows.length === 0 ? (
          <div className="text-xs text-muted text-center py-6">{L.sourcesDrawerEmpty(period)}</div>
        ) : (
          <ul className="space-y-2">
            {rows.map(r => (
              <li key={r.id} className="p-2 rounded text-xs" style={{ background: 'var(--bg-input)' }}>
                <div className="flex justify-between mb-1">
                  <span className="text-primary">{engineLabel(r.engine)} · {r.query}</span>
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
