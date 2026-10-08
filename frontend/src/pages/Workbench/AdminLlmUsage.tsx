// Model call usage (admin-only) -- "which feature burned how many tokens / made how many calls".
//
// Background: on 2026-08-13 the DeepSeek key balance ran out (402), and the diagnosis narrative layer / Yuanbao sentiment synthesis / chat assistant
// all half-died together, while the application layer had zero records of LLM calls at the time; we could only check a total on the third-party platform.
//
// Metric definitions (consistent with backend geo/api/admin_llm_usage.py, also labeled on the page for users):
//   · "Call count" = every request actually sent, including retries and failures; multiple tool round trips in one chat assistant turn count multiple times
//   · Historical data comes from the daily summary table; today's numbers are aggregated live from the details -- so instrumentation is visible the same day
//   · A token value of "—" means the provider didn't return usage, not 0
import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageHead } from '../../components/PageHead';
import { SectionCard } from '../../components/ui/SectionCard';
import { UiSelect } from '../../components/ui/Select';
import { toast } from '../../components/ui/toast';
import {
  CALL_KIND_LABEL, SERVICE_LABEL, adminLlmUsageApi, fmtCompact, fmtInt,
} from '../../services/adminLlmUsageApi';
import type {
  UsageFeatureRank as RankData, UsageFilters, UsageLogPage, UsageOverview, UsageQuery, UsageTrend,
} from '../../services/adminLlmUsageApi';
import { UsageKpiRow } from './llmUsage/UsageKpiRow';
import type { KpiItem } from './llmUsage/UsageKpiRow';
import { UsageTrendChart } from './llmUsage/UsageTrendChart';
import type { TrendMetric } from './llmUsage/UsageTrendChart';
import { UsageFeatureRank, featureLabel } from './llmUsage/UsageFeatureRank';
import { UsageLogTable } from './llmUsage/UsageLogTable';

const inputStyle = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
};

const RANGES = [
  { value: '7', label: '近 7 天' },
  { value: '30', label: '近 30 天' },
  { value: '90', label: '近 90 天' },
];

export function AdminLlmUsage() {
  const token = localStorage.getItem('token') || '';

  const [days, setDays] = useState('30');
  const [service, setService] = useState('');
  const [provider, setProvider] = useState('');
  const [callKind, setCallKind] = useState('');
  const [metric, setMetric] = useState<TrendMetric>('total_tokens');
  const [trendGroup, setTrendGroup] = useState('none');
  const [orderBy, setOrderBy] = useState('total_tokens');

  // Independent filters for the details area (scenario / status / pagination)
  const [feature, setFeature] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const [overview, setOverview] = useState<UsageOverview | null>(null);
  const [trend, setTrend] = useState<UsageTrend | null>(null);
  const [rank, setRank] = useState<RankData | null>(null);
  const [logs, setLogs] = useState<UsageLogPage | null>(null);
  const [filters, setFilters] = useState<UsageFilters | null>(null);
  const [loading, setLoading] = useState(false);
  const [logLoading, setLogLoading] = useState(false);

  const baseQuery: UsageQuery = useMemo(() => ({
    days: Number(days), service: service || undefined,
    provider: provider || undefined, callKind: callKind || undefined,
  }), [days, service, provider, callKind]);

  const loadTop = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [ov, tr, rk] = await Promise.all([
        adminLlmUsageApi.overview(token, baseQuery),
        adminLlmUsageApi.trend(token, { ...baseQuery, groupBy: trendGroup }),
        adminLlmUsageApi.byFeature(token, { ...baseQuery, orderBy, limit: 20 }),
      ]);
      setOverview(ov); setTrend(tr); setRank(rk);
    } catch (e) {
      toast.error(String((e as Error).message || e));
    } finally {
      setLoading(false);
    }
  }, [token, baseQuery, trendGroup, orderBy]);

  const loadLogs = useCallback(async () => {
    if (!token) return;
    setLogLoading(true);
    try {
      setLogs(await adminLlmUsageApi.logs(token, {
        ...baseQuery, feature: feature || undefined, status: status || undefined,
        page, pageSize,
      }));
    } catch (e) {
      toast.error(String((e as Error).message || e));
    } finally {
      setLogLoading(false);
    }
  }, [token, baseQuery, feature, status, page, pageSize]);

  useEffect(() => { loadTop(); }, [loadTop]);
  useEffect(() => { loadLogs(); }, [loadLogs]);
  useEffect(() => {
    if (!token) return;
    adminLlmUsageApi.filters(token).then(setFilters).catch(() => { /* Missing filter options don't affect the main data */ });
  }, [token]);
  // Top filters changed -> details back to page one, otherwise it would sit on an empty page
  useEffect(() => { setPage(1); }, [days, service, provider, callKind, feature, status]);

  const kpis: KpiItem[] = useMemo(() => {
    const k = overview?.kpi;
    const d = overview?.delta;
    if (!k) return [];
    const coverWarn = k.token_coverage_pct < 100
      ? `另有 ${fmtInt(k.calls_without_tokens)} 次调用未取得 token 用量(provider 没返回),未计入本数字`
      : undefined;
    return [
      { key: 'calls', label: '调用次数', value: fmtInt(k.calls), hue: 'var(--data-accent)',
        sub: `含重试与失败 · 计费 ${fmtInt(k.ok_calls)} 次`, deltaPct: d?.calls_pct },
      { key: 'tokens', label: 'token 总量', value: fmtCompact(k.total_tokens), hue: '#7c9cf5',
        sub: `覆盖 ${k.token_coverage_pct}% 的调用`, warn: coverWarn,
        deltaPct: d?.total_tokens_pct },
      { key: 'prompt', label: '输入 token', value: fmtCompact(k.prompt_tokens), hue: '#5bc0a8',
        sub: k.cached_tokens > 0 ? `其中命中缓存 ${fmtCompact(k.cached_tokens)}` : undefined },
      { key: 'completion', label: '输出 token', value: fmtCompact(k.completion_tokens), hue: '#e0a45e' },
      { key: 'err', label: '失败率', value: `${k.error_rate_pct}%`, hue: '#d97b7b',
        sub: `白跑 ${fmtInt(k.error_calls)} 次`, lowerIsBetter: true,
        deltaPct: d ? d.error_rate_pp : undefined },
      { key: 'lat', label: '平均耗时', value: `${(k.avg_latency_ms / 1000).toFixed(1)}s`, hue: '#c485d6',
        sub: `${fmtInt(k.rows_n)} 条明细` },
    ];
  }, [overview]);

  const opt = (vals: string[] | undefined, labels: Record<string, string>, allLabel: string) => [
    { value: '', label: allLabel },
    ...(vals || []).map((v) => ({ value: v, label: labels[v] || v })),
  ];

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.llmUsage.pageTitle" titleFallback="模型用量 · 工作台" />

      <div className="flex items-start gap-2 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-primary">模型调用用量</h1>
          <div className="text-[11px] text-muted mt-0.5">
            每次真正发出的模型请求都记一行(含重试与失败)。历史数据来自日汇总,
            今天的数实时聚合明细 —— 刚接上的埋点当天就能看到。
          </div>
        </div>
        <button type="button" onClick={() => { loadTop(); loadLogs(); }}
                className="ml-auto px-2 py-1 rounded-md text-xs" style={inputStyle}>
          刷新
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">时间范围</span>
        <UiSelect value={days} onChange={setDays} className="px-2 py-1 rounded-md"
                  style={{ ...inputStyle, minWidth: 100 }} options={RANGES} />
        <span className="text-muted">来源服务</span>
        <UiSelect value={service} onChange={setService} className="px-2 py-1 rounded-md"
                  style={{ ...inputStyle, minWidth: 110 }}
                  options={opt(filters?.service, SERVICE_LABEL, '全部服务')} />
        <span className="text-muted">供应商</span>
        <UiSelect value={provider} onChange={setProvider} className="px-2 py-1 rounded-md"
                  style={{ ...inputStyle, minWidth: 110 }}
                  options={opt(filters?.provider, {}, '全部供应商')} />
        <span className="text-muted">调用形态</span>
        <UiSelect value={callKind} onChange={setCallKind} className="px-2 py-1 rounded-md"
                  style={{ ...inputStyle, minWidth: 100 }}
                  options={opt(filters?.call_kind, CALL_KIND_LABEL, '全部形态')} />
        {overview && (
          <span className="ml-auto text-muted tabular-nums">
            {overview.range.start} ~ {overview.range.end}
            {loading && ' · 加载中…'}
          </span>
        )}
      </div>

      <UsageKpiRow items={kpis} />

      <SectionCard
        title="每日趋势"
        hint="面积为所选指标;单序列时右轴虚线是调用次数 —— 用来分辨「调得更多」还是「单次更贵」。"
        right={(
          <div className="flex items-center gap-2">
            <UiSelect value={metric} onChange={(v) => setMetric(v as TrendMetric)}
                      className="px-2 py-1 rounded-md text-xs"
                      style={{ ...inputStyle, minWidth: 110 }}
                      options={[
                        { value: 'total_tokens', label: 'token 用量' },
                        { value: 'calls', label: '调用次数' },
                        { value: 'error_calls', label: '失败请求' },
                      ]} />
            <UiSelect value={trendGroup} onChange={setTrendGroup}
                      className="px-2 py-1 rounded-md text-xs"
                      style={{ ...inputStyle, minWidth: 110 }}
                      options={[
                        { value: 'none', label: '不分组' },
                        { value: 'service', label: '按来源服务' },
                        { value: 'provider', label: '按供应商' },
                        { value: 'model', label: '按模型' },
                        { value: 'call_kind', label: '按调用形态' },
                      ]} />
          </div>
        )}
      >
        <UsageTrendChart data={trend} metric={metric} />
      </SectionCard>

      <SectionCard title="功能场景排行"
                   hint="按场景归集的用量 —— 回答「是哪个功能在烧钱」。">
        <UsageFeatureRank data={rank} orderBy={orderBy} onOrderByChange={setOrderBy}
                          onPick={(f) => setFeature(f === feature ? '' : f)} />
      </SectionCard>

      <SectionCard
        title="调用明细"
        hint="一行 = 一次发出的请求。token 显示「—」表示供应商没返回用量,不是 0。"
        right={(
          <div className="flex items-center gap-2 text-xs">
            {feature && (
              <button type="button" onClick={() => setFeature('')}
                      className="px-2 py-1 rounded-md text-accent" style={inputStyle}>
                场景:{featureLabel(feature)} ✕
              </button>
            )}
            <UiSelect value={status} onChange={setStatus} className="px-2 py-1 rounded-md"
                      style={{ ...inputStyle, minWidth: 110 }}
                      options={[
                        { value: '', label: '全部状态' },
                        { value: 'ok', label: '成功' },
                        { value: 'http_error', label: '接口报错' },
                        { value: 'network_error', label: '网络未达' },
                        { value: 'truncated', label: '输出被截断' },
                        { value: 'parse_error', label: '响应解析失败' },
                      ]} />
            <span className="text-muted tabular-nums">共 {fmtInt(logs?.total || 0)} 条</span>
          </div>
        )}
      >
        <UsageLogTable data={logs} loading={logLoading} token={token}
                       page={page} pageSize={pageSize}
                       onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1); }} />
      </SectionCard>
    </div>
  );
}

export default AdminLlmUsage;
