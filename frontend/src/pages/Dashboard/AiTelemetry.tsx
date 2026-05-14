// AI 遥测 工作台页 — 配置话题 + 查看跑批结果.
//
// Tab 1「话题配置」:
//   - 列表:启用 / 话题名 / Query 数 / 引擎数 / 最近跑 / 状态 / 操作
//   - 新建/编辑 Modal:话题名 + Query 多行 + 引擎 10 复选(国内/海外分组)+ 启用开关
//   - 立即试跑:点「立即试跑一次」直接调 /run-now,结果就在 modal 底部展示
//
// Tab 2「跑批结果」:第一版占位 (Step 3 再做)
//
// 频率由后端固定为 daily,前端不暴露时间选择.
import { Fragment, useEffect, useMemo, useState, type ReactElement } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { PageHead } from '../../components/PageHead';
import {
  aiTelemetryApi, CN_ENGINES, GLOBAL_ENGINES,
  type EngineId, type Topic, type TopicPayload, type RunNowResult,
  type RunSummary, type ResponseRow, type Overview, type DomainCount,
  type IntentBreakdown,
  type OwnedSplit,
  type TrackingMatrix, type QueryHitCell, type EngineFirstHit,
  type CellDrawer, type CellInsight, type Briefing, type ShareOfVoice,
  type QueryCandidate, type ClusterMeta,
} from '../../services/aiTelemetryApi';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, Legend,
  AreaChart, Area, PieChart, Pie, Cell,
} from 'recharts';
import { Tooltip as HintTooltip } from '../../components/Tooltip';

type TabKey = 'overview' | 'tracking' | 'briefings' | 'config' | 'results';

const ENGINE_COLORS: Record<EngineId, string> = {
  deepseek: '#5B6CFF', doubao: '#FF7043', qwen: '#7E57C2',
  wenxin: '#26A69A', yuanbao: '#EF5350',
  chatgpt: '#10A37F', claude: '#C97650', gemini: '#4285F4',
  grok: '#9CA3AF', copilot: '#0078D4',
};

function InfoHint({ text }: { text: string }) {
  return (
    <HintTooltip content={text}>
      <span
        role="img"
        aria-label="info"
        className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full text-[9px] font-semibold cursor-help align-middle leading-none select-none"
        style={{
          background: 'var(--bg-input)',
          color: 'var(--text-muted)',
          border: '1px solid var(--border-color)',
        }}
      >?</span>
    </HintTooltip>
  );
}

export function AiTelemetry() {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [tab, setTab] = useState<TabKey>('overview');
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(false);
  // undefined = modal closed; null = creating; Topic = editing
  const [editing, setEditing] = useState<Topic | null | undefined>(undefined);

  const refresh = async () => {
    setLoading(true);
    try {
      setTopics(await aiTelemetryApi.listTopics(token));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const handleSave = async (payload: TopicPayload) => {
    if (editing && editing.id) {
      await aiTelemetryApi.updateTopic(editing.id, payload, token);
    } else {
      await aiTelemetryApi.createTopic(payload, token);
    }
    setEditing(undefined);
    refresh();
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm(t('common.confirmDelete') || 'Delete?')) return;
    await aiTelemetryApi.deleteTopic(id, token);
    refresh();
  };

  const handleRun = async (id: number) => {
    await aiTelemetryApi.triggerRun(id, token);
    window.alert(t('dashboard.aiTelemetry.results.started'));
    refresh();
  };

  // 编辑 / 新建模式:把整页内容换成 inline editor,200+ 候选才有空间选
  if (editing !== undefined) {
    return (
      <div className="space-y-4">
        <PageHead titleKey="dashboard.aiTelemetry.title" titleFallback="AI Telemetry" />
        <header className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setEditing(undefined)}
              className="text-sm px-2 py-1 rounded-md"
              style={{
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                color: 'var(--text-secondary)',
              }}
            >
              ← {t('dashboard.aiTelemetry.backToList')}
            </button>
            <h1 className="text-xl font-semibold text-primary leading-tight">
              {t(editing ? 'dashboard.aiTelemetry.editTopic' : 'dashboard.aiTelemetry.newTopic')}
            </h1>
          </div>
        </header>
        <TopicEditor
          initial={editing}
          token={token}
          onCancel={() => setEditing(undefined)}
          onSave={handleSave}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHead titleKey="dashboard.aiTelemetry.title" titleFallback="AI Telemetry" />

      <header className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span
            className="inline-flex items-center justify-center w-9 h-9 rounded-lg"
            style={{ background: 'var(--accent-primary)', color: 'white' }}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M2 12h3l2-6 4 12 2-6 2 3h3 4" />
            </svg>
          </span>
          <div>
            <h1 className="text-xl font-semibold text-primary leading-tight">
              {t('dashboard.aiTelemetry.title')}
            </h1>
            <p className="text-xs text-secondary mt-0.5">{t('dashboard.aiTelemetry.subtitle')}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setEditing(null)}
          className="px-3 py-1.5 text-sm rounded-md text-white shadow-sm hover:opacity-90 transition-opacity"
          style={{ background: 'var(--accent-primary)' }}
        >
          + {t('dashboard.aiTelemetry.newTopic')}
        </button>
      </header>

      <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
        {(['overview', 'tracking', 'briefings', 'config', 'results'] as TabKey[]).map(k => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            className="px-3 py-2 text-sm -mb-px"
            style={{
              borderBottom: tab === k ? '2px solid var(--accent-primary)' : '2px solid transparent',
              color: tab === k ? 'var(--accent-primary)' : 'var(--text-secondary)',
            }}
          >
            {t(`dashboard.aiTelemetry.tab${k.charAt(0).toUpperCase() + k.slice(1)}`)}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab topics={topics} token={token} />}
      {tab === 'tracking' && <TrackingTab topics={topics} token={token} onRun={handleRun} />}
      {tab === 'briefings' && <BriefingsTab topics={topics} token={token} />}
      {tab === 'config' && (
        <TopicTable
          topics={topics} loading={loading}
          onEdit={setEditing} onDelete={handleDelete} onRun={handleRun}
        />
      )}
      {tab === 'results' && <ResultsTab topics={topics} token={token} />}
    </div>
  );
}

// ── 话题列表 ───────────────────────────────────────────────────

function TopicTable({
  topics, loading, onEdit, onDelete, onRun,
}: {
  topics: Topic[]; loading: boolean;
  onEdit: (t: Topic) => void; onDelete: (id: number) => void;
  onRun: (id: number) => void;
}) {
  const { t } = useTranslation();
  if (loading) return <div className="py-12 text-center text-sm text-muted">…</div>;
  if (topics.length === 0) {
    return (
      <div
        className="py-12 text-center text-sm text-muted rounded-lg"
        style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}
      >
        {t('dashboard.aiTelemetry.empty')}
      </div>
    );
  }

  const c = (k: string) => t(`dashboard.aiTelemetry.col.${k}`);

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
            <th className="text-left px-3 py-2 font-medium">{c('enabled')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('name')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('queries')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('engines')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('lastRun')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('status')}</th>
            <th className="text-right px-3 py-2 font-medium">{c('actions')}</th>
          </tr>
        </thead>
        <tbody>
          {topics.map(tp => (
            <tr key={tp.id} style={{ borderTop: '1px solid var(--border-color)' }}>
              <td className="px-3 py-2">{tp.enabled ? '✓' : '—'}</td>
              <td className="px-3 py-2 text-primary">{tp.name}</td>
              <td className="px-3 py-2">{tp.queries.length}</td>
              <td className="px-3 py-2">{tp.engines.length}/10</td>
              <td className="px-3 py-2 text-secondary">{formatTime(tp.last_run_at)}</td>
              <td className="px-3 py-2">{renderStatus(tp.last_run_status)}</td>
              <td className="px-3 py-2 text-right space-x-2">
                <button
                  className="text-xs disabled:opacity-40"
                  style={{ color: 'var(--accent-primary)' }}
                  disabled={tp.last_run_status === 'running'}
                  onClick={() => onRun(tp.id)}
                >
                  {tp.last_run_status === 'running'
                    ? t('dashboard.aiTelemetry.actions.running')
                    : t('dashboard.aiTelemetry.actions.run')}
                </button>
                <button className="text-xs text-secondary hover:text-primary" onClick={() => onEdit(tp)}>
                  {t('dashboard.aiTelemetry.actions.edit')}
                </button>
                <button className="text-xs text-rose-500 hover:text-rose-400" onClick={() => onDelete(tp.id)}>
                  {t('dashboard.aiTelemetry.actions.delete')}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

function renderStatus(s?: string | null) {
  if (!s) return <span className="text-muted">—</span>;
  const map: Record<string, [string, string]> = {
    success: ['✓', 'text-emerald-500'],
    failed: ['✗', 'text-rose-500'],
    running: ['…', 'text-blue-500'],
  };
  const [icon, color] = map[s] || ['?', 'text-muted'];
  return <span className={color}>{icon} {s}</span>;
}

// ── 概览 ───────────────────────────────────────────────────────

function OverviewTab({ topics, token }: { topics: Topic[]; token: string }) {
  const { t } = useTranslation();
  const [topicId, setTopicId] = useState<number | null>(null);
  const [period, setPeriod] = useState<7 | 30 | 90>(30);
  const [data, setData] = useState<Overview | null>(null);
  const [sov, setSoV] = useState<ShareOfVoice | null>(null);
  const [intent, setIntent] = useState<IntentBreakdown | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (topicId === null && topics.length > 0) setTopicId(topics[0].id);
  }, [topics, topicId]);

  useEffect(() => {
    if (topicId === null) return;
    setLoading(true);
    Promise.all([
      aiTelemetryApi.getOverview(topicId, period, token),
      aiTelemetryApi.getShareOfVoice(topicId, period, token).catch(() => null),
      aiTelemetryApi.getIntentBreakdown(topicId, period, token).catch(() => null),
    ]).then(([ov, s, ib]) => {
      setData(ov);
      setSoV(s);
      setIntent(ib);
    }).finally(() => setLoading(false));
  }, [topicId, period, token]);

  if (topics.length === 0) {
    return <EmptyHint text={t('dashboard.aiTelemetry.empty')} />;
  }

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg-input)', border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  };

  return (
    <div className="space-y-4">
      {/* 顶部控制行 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="text-xs text-secondary">
          {t('dashboard.aiTelemetry.overview.selectTopic')}
          <select
            value={topicId ?? ''} onChange={e => setTopicId(Number(e.target.value))}
            className="ml-2 px-2 py-1 text-sm rounded" style={inputStyle}
          >
            {topics.map(tp => <option key={tp.id} value={tp.id}>{tp.name}</option>)}
          </select>
        </label>
        <div className="flex gap-1" style={{ background: 'var(--bg-input)', borderRadius: 6, padding: 2 }}>
          {([7, 30, 90] as const).map(p => (
            <button
              key={p} type="button"
              onClick={() => setPeriod(p)}
              className="px-3 py-1 text-xs rounded"
              style={{
                background: period === p ? 'var(--accent-primary)' : 'transparent',
                color: period === p ? 'white' : 'var(--text-secondary)',
              }}
            >
              {t(`dashboard.aiTelemetry.overview.period${p}`)}
            </button>
          ))}
        </div>
      </div>

      {data && data.brand_keywords.length === 0 && (
        <div className="text-xs text-amber-500 px-3 py-2 rounded"
          style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)' }}>
          ⚠ {t('dashboard.aiTelemetry.overview.noBrand')}
        </div>
      )}

      {/* 4 KPI 卡 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard
          label={t('dashboard.aiTelemetry.overview.kpiVisibility')}
          value={data?.visibility.value ?? 0}
          unit={t('dashboard.aiTelemetry.overview.kpiVisibilityUnit')}
          delta={data?.visibility.delta_pct ?? null}
          sparkline={data?.visibility.sparkline ?? []}
          loading={loading}
          icon="eye"
          hint={t('dashboard.aiTelemetry.overview.tipVisibility')}
        />
        <KpiCard
          label={t('dashboard.aiTelemetry.overview.kpiCitations')}
          value={data?.citations.value ?? 0}
          delta={data?.citations.delta_pct ?? null}
          sparkline={data?.citations.sparkline ?? []}
          loading={loading}
          fmt="int"
          icon="link"
          hint={t('dashboard.aiTelemetry.overview.tipCitations')}
        />
        <KpiCard
          label={t('dashboard.aiTelemetry.overview.kpiGrowth')}
          value={data?.growth.value ?? 0}
          unit="%"
          delta={null}
          sparkline={[]}
          loading={loading}
          accentByValue
          icon="trend"
          hint={t('dashboard.aiTelemetry.overview.tipGrowth')}
        />
        <KpiCard
          label={t('dashboard.aiTelemetry.overview.kpiEngines')}
          value={data?.engines_covered.value ?? 0}
          unit={`/${data?.engines_total ?? 0}`}
          delta={data?.engines_covered.delta_pct ?? null}
          sparkline={[]}
          loading={loading}
          fmt="int"
          icon="cpu"
          hint={t('dashboard.aiTelemetry.overview.tipEngines')}
        />
      </div>

      {/* v1.3 — SAIV 声量份额 + 竞品引用份额差 */}
      <ShareOfVoiceBlock data={sov} loading={loading} />

      {/* 趋势图 */}
      <TrendChart data={data} loading={loading} />

      {/* intent 分布(picker 端聚出的簇 × 本期 mention 率)*/}
      <IntentBreakdownBlock data={intent} loading={loading} />

      {/* 引用分析:Top 10 + Owned vs Other */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <TopDomainsBlock data={data?.top_domains ?? []} loading={loading} />
        </div>
        <OwnedSplitBlock data={data?.owned_split} loading={loading} />
      </div>

      {/* 引擎 × 平台 heatmap */}
      <EngineDomainMatrix
        engines={data?.engines ?? []}
        topDomains={data?.top_domains ?? []}
        matrix={data?.engine_domain_matrix ?? {}}
        loading={loading}
      />
    </div>
  );
}

// ── v1.3 SAIV 声量份额 + 竞品引用份额差 + 命中位置分布 ───────────

function ShareOfVoiceBlock({ data, loading }: { data: ShareOfVoice | null; loading: boolean }) {
  const { t } = useTranslation();
  if (loading && !data) {
    return (
      <div className="rounded-lg p-4 text-sm text-muted text-center"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>…</div>
    );
  }
  if (!data) return null;
  const hasSignal = data.brand_count + data.competitors_count_total > 0;

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      {/* SAIV KPI 卡 */}
      <div className="rounded-lg p-4"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <h3 className="text-sm font-medium text-primary mb-2 flex items-center gap-1">
          <span style={{ color: 'var(--accent-primary)' }}>📣</span>
          {t('dashboard.aiTelemetry.overview.saivTitle')}
          <InfoHint text={t('dashboard.aiTelemetry.overview.tipSaiv')} />
        </h3>
        <div className="flex items-baseline gap-1">
          <span className="text-3xl font-semibold tabular-nums text-primary">
            {data.saiv_pct.toFixed(1)}
          </span>
          <span className="text-muted text-sm">%</span>
        </div>
        <p className="text-[11px] text-muted mt-1">
          {t('dashboard.aiTelemetry.overview.saivHint', {
            brand: data.brand_count,
            total: data.brand_count + data.competitors_count_total,
          })}
        </p>
        {/* 命中位置分布(检索排名简化版) */}
        {hasSignal && data.brand_count > 0 && (
          <div className="mt-3">
            <div className="text-[11px] font-semibold text-secondary uppercase tracking-wider mb-1 inline-flex items-center gap-1">
              {t('dashboard.aiTelemetry.overview.positionTitle')}
              <InfoHint text={t('dashboard.aiTelemetry.overview.tipPosition')} />
            </div>
            <PositionBar dist={data.position_dist} />
          </div>
        )}
        <div className="mt-3 text-[11px] text-muted">
          {t('dashboard.aiTelemetry.overview.optimalRate')}
          <InfoHint text={t('dashboard.aiTelemetry.overview.tipOptimalRate')} />:
          <span className="text-primary font-semibold ml-1">
            {data.optimal_rate_pct.toFixed(1)}%
          </span>
          <span className="ml-1">
            ({t('dashboard.aiTelemetry.overview.optimalRateHint', { runs: data.total_runs })})
          </span>
        </div>
      </div>

      {/* 竞品份额对比 */}
      <div className="rounded-lg p-4 md:col-span-2"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <h3 className="text-sm font-medium text-primary mb-3 inline-flex items-center gap-1">
          {t('dashboard.aiTelemetry.overview.competitorShareTitle')}
          <InfoHint text={t('dashboard.aiTelemetry.overview.tipCompetitorShare')} />
        </h3>
        {!hasSignal && (
          <div className="text-xs text-muted py-6 text-center">
            {t('dashboard.aiTelemetry.overview.competitorShareEmpty')}
          </div>
        )}
        {hasSignal && (
          <ul className="space-y-1.5">
            {/* 把品牌自身和竞品一起排序展示 */}
            {[
              { name: `★ ${data.target}`, count: data.brand_count,
                pct: data.brand_count / Math.max(1, data.brand_count + data.competitors_count_total) * 100,
                isBrand: true },
              ...data.competitors.map(c => ({ ...c, isBrand: false })),
            ]
              .sort((a, b) => b.count - a.count)
              .slice(0, 8)
              .map((c, i) => {
                const max = Math.max(
                  data.brand_count,
                  ...data.competitors.map(x => x.count),
                );
                const width = Math.max(4, (c.count / Math.max(1, max)) * 100);
                return (
                  <li key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-32 truncate"
                      style={{
                        color: c.isBrand ? 'var(--accent-primary)' : 'var(--text-primary)',
                        fontWeight: c.isBrand ? 600 : 400,
                      }}>{c.name}</span>
                    <div className="flex-1 h-3 rounded relative"
                      style={{ background: 'var(--bg-input)' }}>
                      <div className="h-3 rounded transition-all" style={{
                        width: `${width}%`,
                        background: c.isBrand ? 'var(--accent-primary)' : '#94a3b8',
                      }} />
                    </div>
                    <span className="w-10 text-right text-primary tabular-nums">{c.count}</span>
                    <span className="w-12 text-right text-muted tabular-nums">
                      {c.pct.toFixed(1)}%
                    </span>
                  </li>
                );
              })}
          </ul>
        )}
      </div>
    </div>
  );
}

function PositionBar({ dist }: { dist: ShareOfVoice['position_dist'] }) {
  const total = dist.lead + dist.body + dist.tail + dist.unknown;
  if (total === 0) return <p className="text-[11px] text-muted">—</p>;
  const segs = [
    { key: 'lead', color: '#10b981', n: dist.lead },     // 绿:开头
    { key: 'body', color: '#3b82f6', n: dist.body },     // 蓝:中段
    { key: 'tail', color: '#f59e0b', n: dist.tail },     // 黄:末尾
    { key: 'unknown', color: '#9ca3af', n: dist.unknown },
  ];
  return (
    <div>
      <div className="flex rounded overflow-hidden h-2"
        style={{ background: 'var(--bg-tertiary)' }}>
        {segs.map(s => s.n > 0 && (
          <div key={s.key} title={`${s.key}: ${s.n}`}
            style={{ width: `${s.n / total * 100}%`, background: s.color }} />
        ))}
      </div>
      <div className="flex gap-2 mt-1 text-[10px] text-muted">
        {segs.filter(s => s.n > 0).map(s => (
          <span key={s.key}>
            <span style={{ color: s.color }}>●</span> {s.key} {s.n}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── intent 分布 ────────────────────────────────────────────

function IntentBreakdownBlock({ data, loading }: { data: IntentBreakdown | null; loading: boolean }) {
  const { t } = useTranslation();
  if (loading && !data) {
    return (
      <div className="rounded-lg p-4 text-sm text-muted text-center"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>…</div>
    );
  }
  if (!data) return null;
  const hasSignal = data.clusters.length > 0 || data.uncategorized.response_count > 0;
  if (!hasSignal) return null;

  // 一行显示所有有 response 的簇 + 兜底桶。按 mention_rate 升序排,先看到弱簇 → 内容补强目标
  const items = [
    ...data.clusters,
    ...(data.uncategorized.response_count > 0 ? [data.uncategorized] : []),
  ].filter(c => c.response_count > 0)
    .sort((a, b) => a.mention_rate - b.mention_rate);

  if (items.length === 0) return null;

  const maxRate = Math.max(...items.map(c => c.mention_rate), 0.01);
  return (
    <div className="rounded-lg p-4"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <h3 className="text-sm font-medium text-primary mb-3 inline-flex items-center gap-1">
        {t('dashboard.aiTelemetry.overview.intentTitle')}
        <InfoHint text={t('dashboard.aiTelemetry.overview.tipIntent')} />
      </h3>
      <div className="text-xs text-muted mb-3">
        {t('dashboard.aiTelemetry.overview.intentHint')}
      </div>
      <div className="space-y-2">
        {items.map(c => {
          const rate = c.mention_rate * 100;
          const barPct = (c.mention_rate / maxRate) * 100;
          const barColor = c.mention_rate >= 0.5 ? 'var(--accent-primary)'
            : c.mention_rate >= 0.25 ? '#f59e0b'
            : '#ef4444';
          return (
            <div key={c.cluster_id} className="flex items-center gap-3 text-xs">
              <div className="w-40 shrink-0 break-all" style={{ color: 'var(--text-primary)' }}>
                {c.cluster_id === -1
                  ? t('dashboard.aiTelemetry.overview.intentUncategorized')
                  : c.label}
              </div>
              <div className="flex-1 relative h-4 rounded"
                style={{ background: 'var(--bg-input)' }}>
                <div className="absolute inset-y-0 left-0 rounded"
                  style={{ width: `${barPct}%`, background: barColor }} />
              </div>
              <div className="w-12 text-right tabular-nums font-mono shrink-0"
                style={{ color: 'var(--text-primary)' }}>
                {rate.toFixed(0)}%
              </div>
              <div className="w-24 text-right text-muted shrink-0">
                {t('dashboard.aiTelemetry.overview.intentResponseCount', {
                  m: c.mention_count, n: c.response_count,
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ── 引用分析:Top 10 ────────────────────────────────────────────

function TopDomainsBlock({ data, loading }: { data: DomainCount[]; loading: boolean }) {
  const { t } = useTranslation();
  const max = Math.max(1, ...data.map(d => d.count));

  return (
    <div
      className="rounded-lg p-4 h-full"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      <h3 className="text-sm font-medium text-primary mb-3 inline-flex items-center gap-1">
        {t('dashboard.aiTelemetry.overview.topDomainsTitle')}
        <InfoHint text={t('dashboard.aiTelemetry.overview.tipTopDomains')} />
      </h3>
      {loading && <div className="text-sm text-muted py-6 text-center">…</div>}
      {!loading && data.length === 0 && (
        <div className="text-sm text-muted py-6 text-center">
          {t('dashboard.aiTelemetry.overview.topDomainsEmpty')}
        </div>
      )}
      {!loading && data.length > 0 && (
        <ul className="space-y-1">
          {data.map((d, i) => {
            const rank = i + 1;
            const rankBg = rank <= 3 ? 'var(--accent-primary)' : 'var(--bg-input)';
            const rankFg = rank <= 3 ? 'white' : 'var(--text-muted)';
            return (
              <li
                key={d.domain}
                className="flex items-center gap-2 text-xs py-1 px-1 rounded transition-colors hover:bg-[var(--bg-input)]"
              >
                <span
                  className="inline-flex items-center justify-center w-5 h-5 rounded text-[10px] font-semibold"
                  style={{ background: rankBg, color: rankFg }}
                >
                  {rank}
                </span>
                <img
                  src={`https://www.google.com/s2/favicons?domain=${d.domain}&sz=32`}
                  alt="" width={16} height={16}
                  className="rounded flex-shrink-0"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }}
                />
                <span className="text-primary truncate flex-shrink-0 w-40">{d.domain}</span>
                <div className="flex-1 h-2.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-input)' }}>
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(d.count / max) * 100}%`,
                      background: 'linear-gradient(90deg, var(--accent-primary), var(--accent-primary))',
                      opacity: 0.9,
                    }}
                  />
                </div>
                <span className="text-primary w-10 text-right tabular-nums font-medium">{d.count}</span>
                <span className="text-muted w-12 text-right tabular-nums">{d.pct.toFixed(1)}%</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ── 引用分析:自家 vs 其他 ─────────────────────────────────────

function OwnedSplitBlock({ data, loading }: { data: OwnedSplit | undefined; loading: boolean }) {
  const { t } = useTranslation();
  const empty = !data || (data.owned === 0 && data.other === 0);
  const pieData = data ? [
    { name: 'owned', value: data.owned },
    { name: 'other', value: data.other },
  ] : [];

  return (
    <div
      className="rounded-lg p-4 h-full flex flex-col"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      <h3 className="text-sm font-medium text-primary mb-3 inline-flex items-center gap-1">
        {t('dashboard.aiTelemetry.overview.ownedTitle')}
        <InfoHint text={t('dashboard.aiTelemetry.overview.tipOwned')} />
      </h3>
      {loading && <div className="flex-1 flex items-center justify-center text-sm text-muted">…</div>}
      {!loading && empty && (
        <div className="flex-1 flex items-center justify-center text-sm text-muted text-center px-2">
          {t('dashboard.aiTelemetry.overview.topDomainsEmpty')}
        </div>
      )}
      {!loading && !empty && data && (
        <>
          <div className="h-44 relative">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData} dataKey="value" nameKey="name"
                  cx="50%" cy="50%" innerRadius={52} outerRadius={76}
                  paddingAngle={data.owned > 0 && data.other > 0 ? 2 : 0}
                  strokeWidth={0} isAnimationActive={false}
                >
                  <Cell fill="var(--accent-primary)" />
                  <Cell fill="var(--bg-input)" />
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              <span className="text-3xl font-semibold text-primary tabular-nums">
                {data.owned_pct.toFixed(1)}<span className="text-lg text-muted">%</span>
              </span>
              <span className="text-[11px] text-muted mt-0.5">
                {t('dashboard.aiTelemetry.overview.ownedLegendOwned')}
              </span>
            </div>
          </div>
          {data.delta_pct !== null && data.delta_pct !== undefined && (
            <div className="text-xs text-center mb-2">
              <span className={data.delta_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                {data.delta_pct >= 0 ? '↑' : '↓'} {Math.abs(data.delta_pct).toFixed(1)}%
              </span>
              <span className="text-muted ml-1">vs 上期</span>
            </div>
          )}
          <div className="flex justify-around text-xs">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: 'var(--accent-primary)' }} />
              <span className="text-secondary">{t('dashboard.aiTelemetry.overview.ownedLegendOwned')}</span>
              <span className="text-primary font-semibold tabular-nums">{data.owned}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }} />
              <span className="text-secondary">{t('dashboard.aiTelemetry.overview.ownedLegendOther')}</span>
              <span className="text-primary font-semibold tabular-nums">{data.other}</span>
            </div>
          </div>
          <div className="text-[10px] text-muted mt-3 text-center leading-relaxed">
            {t('dashboard.aiTelemetry.overview.ownedHint')}
          </div>
        </>
      )}
    </div>
  );
}

// ── 引用分析:引擎 × 平台 heatmap ──────────────────────────────

function EngineDomainMatrix({
  engines, topDomains, matrix, loading,
}: {
  engines: EngineId[];
  topDomains: DomainCount[];
  matrix: Partial<Record<EngineId, Record<string, number>>>;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const empty = engines.length === 0 || topDomains.length === 0;

  let maxVal = 0;
  for (const e of engines) {
    const row = matrix[e] || {};
    for (const d of topDomains) {
      maxVal = Math.max(maxVal, row[d.domain] || 0);
    }
  }
  if (maxVal === 0) maxVal = 1;

  const cellBg = (v: number): string => {
    if (v === 0) return 'var(--bg-card)';
    // 5 段量化色阶,跨度 0.30 - 0.95 alpha,对比清晰
    const ratio = v / maxVal;
    const alpha = 0.30 + ratio * 0.65;
    return `rgba(91, 108, 255, ${alpha.toFixed(2)})`;
  };

  return (
    <div
      className="rounded-lg p-4"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="text-sm font-medium text-primary inline-flex items-center gap-1">
          {t('dashboard.aiTelemetry.overview.matrixTitle')}
          <InfoHint text={t('dashboard.aiTelemetry.overview.tipMatrix')} />
        </h3>
        {!empty && (
          <div className="flex items-center gap-1.5 text-[10px] text-muted">
            <span>{t('dashboard.aiTelemetry.overview.matrixHint')}</span>
            <span className="flex items-center gap-px ml-1">
              {[0.30, 0.45, 0.60, 0.78, 0.95].map(a => (
                <span
                  key={a}
                  className="w-3 h-3 inline-block"
                  style={{ background: `rgba(91, 108, 255, ${a})` }}
                />
              ))}
            </span>
          </div>
        )}
      </div>
      {loading && <div className="text-sm text-muted py-6 text-center">…</div>}
      {!loading && empty && (
        <div className="text-sm text-muted py-6 text-center">
          {t('dashboard.aiTelemetry.overview.matrixEmpty')}
        </div>
      )}
      {!loading && !empty && (
        <div className="overflow-x-auto">
          <table className="text-xs border-separate" style={{ borderSpacing: 2 }}>
            <thead>
              <tr>
                <th
                  className="text-left text-muted font-normal sticky left-0 z-10 pr-3"
                  style={{ background: 'var(--bg-card)' }}
                />
                {topDomains.map(d => (
                  <th
                    key={d.domain}
                    className="font-normal text-secondary tabular-nums text-[10px] px-1 align-bottom"
                    style={{ minWidth: 42 }}
                  >
                    <div
                      className="whitespace-nowrap leading-tight inline-block origin-bottom-left"
                      style={{
                        transform: 'rotate(-45deg)',
                        transformOrigin: 'bottom left',
                        height: 70,
                        paddingTop: 14,
                      }}
                    >
                      {d.domain}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {engines.map(e => {
                const row = matrix[e] || {};
                return (
                  <tr key={e}>
                    <td
                      className="text-primary sticky left-0 font-medium pr-3 whitespace-nowrap"
                      style={{ background: 'var(--bg-card)' }}
                    >
                      <span className="flex items-center gap-1.5">
                        <span
                          className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                          style={{ background: ENGINE_COLORS[e] || '#888' }}
                        />
                        {t(`dashboard.aiTelemetry.engine.${e}`, e)}
                      </span>
                    </td>
                    {topDomains.map(d => {
                      const v = row[d.domain] || 0;
                      return (
                        <td
                          key={d.domain}
                          className="text-center tabular-nums rounded transition-transform hover:scale-110 cursor-default"
                          style={{
                            background: cellBg(v),
                            color: v >= maxVal * 0.55 ? 'white' : (v === 0 ? 'transparent' : 'var(--text-primary)'),
                            width: 42, height: 32,
                            fontWeight: v >= maxVal * 0.7 ? 600 : 500,
                            border: v === 0 ? '1px dashed var(--border-color)' : 'none',
                          }}
                          title={`${e} × ${d.domain}: ${v}`}
                        >
                          {v || '·'}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface KpiCardProps {
  label: string;
  value: number;
  unit?: string;
  delta: number | null;
  sparkline: number[];
  loading: boolean;
  fmt?: 'int' | 'float';
  /** 大数字的颜色:value 为正绿、负红 */
  accentByValue?: boolean;
  icon?: 'eye' | 'link' | 'trend' | 'cpu';
  hint?: string;
}

const KPI_ICONS: Record<string, ReactElement> = {
  eye: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.6} d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z M12 9a3 3 0 100 6 3 3 0 000-6z" />,
  link: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.6} d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71 M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" />,
  trend: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.6} d="M23 6l-9.5 9.5-5-5L1 18 M17 6h6v6" />,
  cpu: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.6} d="M4 4h16v16H4z M9 9h6v6H9z M9 1v3 M15 1v3 M9 20v3 M15 20v3 M20 9h3 M20 14h3 M1 9h3 M1 14h3" />,
};

function KpiCard({ label, value, unit, delta, sparkline, loading, fmt, accentByValue, icon, hint }: KpiCardProps) {
  const display = fmt === 'int'
    ? Math.round(value).toLocaleString()
    : (Number.isInteger(value) ? value.toString() : value.toFixed(1));
  const accent = accentByValue
    ? (value > 0 ? 'text-emerald-500' : value < 0 ? 'text-rose-500' : 'text-primary')
    : 'text-primary';
  const hasSparkline = sparkline.length > 1 && sparkline.some(v => v > 0);
  const hasDelta = delta !== null && delta !== undefined;

  return (
    <div
      className="rounded-lg px-4 py-3 transition-all hover:shadow-md"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-secondary inline-flex items-center gap-1">
          {label}
          {hint && <InfoHint text={hint} />}
        </span>
        {icon && (
          <svg
            className="w-4 h-4 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor"
            style={{ color: 'var(--accent-primary)' }}
          >
            {KPI_ICONS[icon]}
          </svg>
        )}
      </div>
      <div className="flex items-baseline gap-1">
        <span className={`text-3xl font-semibold tabular-nums ${accent}`}>
          {loading ? '…' : (accentByValue && value > 0 ? '+' : '') + display}
        </span>
        {unit && <span className="text-sm text-muted">{unit}</span>}
      </div>
      {(hasDelta || hasSparkline) && (
        <div className="flex items-center justify-between mt-2 h-8">
          {hasDelta ? (
            <span className={`text-xs ${delta! >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
              {delta! >= 0 ? '↑' : '↓'} {Math.abs(delta!).toFixed(0)}%
            </span>
          ) : <span />}
          {hasSparkline && (
            <div className="w-20 h-8">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={sparkline.map((v, i) => ({ i, v }))}>
                  <defs>
                    <linearGradient id={`spark-${label}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity={0.4} />
                      <stop offset="100%" stopColor="var(--accent-primary)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <Area
                    type="monotone" dataKey="v"
                    stroke="var(--accent-primary)" fill={`url(#spark-${label})`}
                    strokeWidth={1.8} dot={false} isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TrendChart({ data, loading }: { data: Overview | null; loading: boolean }) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <div
        className="h-64 rounded-lg flex items-center justify-center text-sm text-muted"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >…</div>
    );
  }

  if (!data || data.engines.length === 0) {
    return (
      <div
        className="h-64 rounded-lg flex items-center justify-center text-sm text-muted px-4 text-center"
        style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}
      >
        {t('dashboard.aiTelemetry.overview.trendEmpty')}
      </div>
    );
  }

  const chartData = data.trend.map(p => {
    const row: any = { date: p.date.slice(5) };
    for (const e of data.engines) {
      row[e] = p.values[e] ?? 0;
    }
    return row;
  });

  // X 轴稀疏:总点数 ≤ 14 时全显示;否则每 N 个显示一次,目标 8-10 个 tick
  const totalPoints = chartData.length;
  const tickInterval = totalPoints <= 14 ? 0 : Math.ceil(totalPoints / 8) - 1;

  return (
    <div
      className="rounded-lg p-4"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="text-sm font-medium text-primary inline-flex items-center gap-1">
          {t('dashboard.aiTelemetry.overview.trendTitle')}
          <InfoHint text={t('dashboard.aiTelemetry.overview.tipTrend')} />
        </h3>
        <div className="flex flex-wrap gap-3 text-xs">
          {data.engines.map(e => (
            <span key={e} className="flex items-center gap-1.5 text-secondary">
              <span
                className="inline-block w-2.5 h-2.5 rounded-full"
                style={{ background: ENGINE_COLORS[e] || '#888' }}
              />
              {t(`dashboard.aiTelemetry.engine.${e}`, e)}
            </span>
          ))}
        </div>
      </div>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 8, right: 16, left: -8, bottom: 4 }}>
            <defs>
              {data.engines.map(e => (
                <linearGradient key={e} id={`g-${e}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={ENGINE_COLORS[e] || '#888'} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={ENGINE_COLORS[e] || '#888'} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" opacity={0.3} vertical={false} />
            <XAxis
              dataKey="date"
              interval={tickInterval}
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--border-color)' }}
              tickLine={false}
              padding={{ left: 8, right: 8 }}
            />
            <YAxis
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              allowDecimals={false}
              width={32}
            />
            <RTooltip
              contentStyle={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-color)',
                borderRadius: 8,
                fontSize: 12,
                boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
              }}
              labelStyle={{ color: 'var(--text-primary)', fontWeight: 600 }}
              cursor={{ stroke: 'var(--accent-primary)', strokeDasharray: '3 3', strokeOpacity: 0.5 }}
            />
            <Legend wrapperStyle={{ display: 'none' }} />
            {data.engines.map(e => (
              <Line
                key={e}
                type="monotone"
                dataKey={e}
                stroke={ENGINE_COLORS[e] || '#888'}
                strokeWidth={2.2}
                dot={{ r: 3, strokeWidth: 0, fill: ENGINE_COLORS[e] || '#888' }}
                activeDot={{ r: 6, strokeWidth: 2, stroke: '#fff' }}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ── 跑批结果 ───────────────────────────────────────────────────

function ResultsTab({ topics, token }: { topics: Topic[]; token: string }) {
  const { t } = useTranslation();
  const [topicId, setTopicId] = useState<number | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [responses, setResponses] = useState<ResponseRow[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [loadingResp, setLoadingResp] = useState(false);

  useEffect(() => {
    if (topicId === null && topics.length > 0) setTopicId(topics[0].id);
  }, [topics, topicId]);

  useEffect(() => {
    if (topicId === null) return;
    setLoadingRuns(true);
    aiTelemetryApi.listRuns(topicId, token)
      .then(rs => {
        setRuns(rs);
        setRunId(rs[0]?.id ?? null);
      })
      .finally(() => setLoadingRuns(false));
  }, [topicId, token]);

  useEffect(() => {
    if (runId === null) { setResponses([]); return; }
    setLoadingResp(true);
    aiTelemetryApi.listResponses(runId, token)
      .then(setResponses)
      .finally(() => setLoadingResp(false));
  }, [runId, token]);

  if (topics.length === 0) {
    return <EmptyHint text={t('dashboard.aiTelemetry.results.noTopics')} />;
  }

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg-input)', border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs text-secondary">
          {t('dashboard.aiTelemetry.results.selectTopic')}
          <select
            value={topicId ?? ''} onChange={e => setTopicId(Number(e.target.value))}
            className="ml-2 px-2 py-1 text-sm rounded" style={inputStyle}
          >
            {topics.map(tp => <option key={tp.id} value={tp.id}>{tp.name}</option>)}
          </select>
        </label>
        <label className="text-xs text-secondary">
          {t('dashboard.aiTelemetry.results.selectRun')}
          <select
            value={runId ?? ''} onChange={e => setRunId(Number(e.target.value))}
            disabled={runs.length === 0}
            className="ml-2 px-2 py-1 text-sm rounded disabled:opacity-50" style={inputStyle}
          >
            {runs.map(r => (
              <option key={r.id} value={r.id}>
                {`#${r.id} ${r.status} · ${formatTime(r.started_at)} · ${r.response_count} 条`}
              </option>
            ))}
          </select>
        </label>
        {runId !== null && runs.find(r => r.id === runId) && (
          <RunStatusChip run={runs.find(r => r.id === runId)!} />
        )}
      </div>

      {loadingRuns && <div className="text-sm text-muted py-6 text-center">…</div>}
      {!loadingRuns && runs.length === 0 && (
        <EmptyHint text={t('dashboard.aiTelemetry.results.noRuns')} />
      )}

      {runs.length > 0 && (
        <ResponseTable
          responses={responses}
          loading={loadingResp}
          expanded={expanded}
          onToggle={(id) => {
            setExpanded(prev => {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id); else next.add(id);
              return next;
            });
          }}
        />
      )}
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div
      className="py-12 text-center text-sm text-muted rounded-lg"
      style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}
    >
      {text}
    </div>
  );
}

function RunStatusChip({ run }: { run: RunSummary }) {
  const color = run.status === 'success' ? 'text-emerald-500'
    : run.status === 'failed' ? 'text-rose-500'
    : 'text-blue-500';
  return (
    <span className={`text-xs ${color}`}>
      ● {run.status}
      {run.finished_at && (
        <span className="text-muted ml-2">
          ({Math.max(1, Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000))}s)
        </span>
      )}
    </span>
  );
}

function ResponseTable({
  responses, loading, expanded, onToggle,
}: {
  responses: ResponseRow[]; loading: boolean;
  expanded: Set<number>; onToggle: (id: number) => void;
}) {
  const { t } = useTranslation();
  if (loading) return <div className="text-sm text-muted py-6 text-center">…</div>;
  if (responses.length === 0) return null;

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
            <th className="text-left px-3 py-2 font-medium w-8"></th>
            <th className="text-left px-3 py-2 font-medium">
              {t(`dashboard.aiTelemetry.engine.${responses[0].engine}`, responses[0].engine)
                ? '引擎' : 'Engine'}
            </th>
            <th className="text-left px-3 py-2 font-medium">Query</th>
            <th className="text-left px-3 py-2 font-medium">答案摘要 / Answer</th>
            <th className="text-left px-3 py-2 font-medium">引用 / Cites</th>
            <th className="text-left px-3 py-2 font-medium">{t('dashboard.aiTelemetry.results.video')}</th>
          </tr>
        </thead>
        <tbody>
          {responses.map(r => {
            const open = expanded.has(r.id);
            return (
              <Fragment key={r.id}>
                <tr
                  style={{ borderTop: '1px solid var(--border-color)', cursor: 'pointer' }}
                  onClick={() => onToggle(r.id)}
                >
                  <td className="px-3 py-2 text-muted">{open ? '▼' : '▶'}</td>
                  <td className="px-3 py-2 text-primary font-medium">
                    <EngineLabel id={r.engine} />
                  </td>
                  <td className="px-3 py-2 text-secondary max-w-[18rem] truncate">{r.query}</td>
                  <td className="px-3 py-2 text-secondary max-w-[28rem] truncate">
                    {r.error ? (
                      <span className="text-rose-500">⚠ {r.error}</span>
                    ) : (
                      r.answer || <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2">{r.citations.length}</td>
                  <td className="px-3 py-2">
                    {r.video_url ? (
                      <a
                        href={r.video_url} target="_blank" rel="noreferrer"
                        className="text-xs hover:underline"
                        style={{ color: 'var(--accent-primary)' }}
                        onClick={e => e.stopPropagation()}
                      >▶</a>
                    ) : <span className="text-muted">—</span>}
                  </td>
                </tr>
                {open && (
                  <tr style={{ borderTop: '1px solid var(--border-color)', background: 'var(--bg-secondary)' }}>
                    <td colSpan={6} className="px-4 py-3">
                      <ResponseDetail row={r} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function EngineLabel({ id }: { id: EngineId }) {
  const { t } = useTranslation();
  return <span>{t(`dashboard.aiTelemetry.engine.${id}`, id)}</span>;
}

function ResponseDetail({ row }: { row: ResponseRow }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 text-sm">
      <div>
        <div className="text-xs text-muted mb-1">Answer</div>
        <div className="text-primary whitespace-pre-wrap leading-relaxed">
          {row.answer || <span className="text-muted">—</span>}
        </div>
      </div>
      <div>
        <div className="text-xs text-muted mb-1">Citations ({row.citations.length})</div>
        {row.citations.length === 0 ? (
          <div className="text-muted text-xs">{t('dashboard.aiTelemetry.results.noCitations')}</div>
        ) : (
          <ul className="text-xs space-y-1">
            {row.citations.map((c, i) => (
              <li key={i}>
                <span className="text-muted mr-2">[{i + 1}]</span>
                <a
                  href={c.url} target="_blank" rel="noreferrer"
                  className="hover:underline" style={{ color: 'var(--accent-primary)' }}
                >{c.title || c.url}</a>
                <span className="text-muted ml-2">· {c.domain}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ── 新建/编辑 Modal ───────────────────────────────────────────

interface TopicEditorProps {
  initial: Topic | null;
  token: string;
  onCancel: () => void;
  onSave: (payload: TopicPayload) => Promise<void>;
}

function TopicEditor({ initial, token, onCancel, onSave }: TopicEditorProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name || '');
  const [target, setTarget] = useState(initial?.target || '');
  const [aliasesText, setAliasesText] = useState((initial?.target_aliases || []).join(', '));
  const [engines, setEngines] = useState<Set<EngineId>>(
    new Set(initial?.engines || ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao'])
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResults, setRunResults] = useState<RunNowResult[] | null>(null);

  // Query picker — 不再允许手填,候选全部走 DeepSeek 生成
  // 编辑场景:把 initial.queries 当作"已存在候选",默认勾选
  const SUGGEST_COUNT = 200;
  const QUERY_MAX_PICK = 50;
  const [seed, setSeed] = useState('');
  const [industry, setIndustry] = useState('');
  const [suggesting, setSuggesting] = useState(false);
  const [suggestErr, setSuggestErr] = useState<string | null>(null);
  // 编辑场景:initial.queries 当作"已存在候选"塞进 suggestions(无分数),如果话题
  // 已存过 cluster_ids 就把簇 ID 一并回填,允许下次保存继续按簇分组
  const [suggestions, setSuggestions] = useState<QueryCandidate[]>(() => {
    const qs = initial?.queries || [];
    const cids = initial?.query_cluster_ids || [];
    return qs.map((text, i) => ({
      text, score: 0, sources: [],
      ...(typeof cids[i] === 'number' && cids[i] >= 0 ? { cluster_id: cids[i] } : {}),
    }));
  });
  const [clusters, setClusters] = useState<ClusterMeta[]>(
    () => (initial?.clusters || []).map(c => ({
      cluster_id: c.cluster_id, label: c.label, size: c.size,
      medoid_index: 0,  // 持久化时没存 medoid_index,这里用 0 占位
    })),
  );
  const [collapsedClusters, setCollapsedClusters] = useState<Set<number>>(new Set());
  const [picked, setPicked] = useState<Set<string>>(new Set(initial?.queries || []));
  const [queryFilter, setQueryFilter] = useState('');
  const [sortByScore, setSortByScore] = useState(true);

  const queries = useMemo(() => Array.from(picked).slice(0, QUERY_MAX_PICK), [picked]);
  const aliases = useMemo(
    () => aliasesText.split(/[,,\n]/).map(s => s.trim()).filter(Boolean).slice(0, 10),
    [aliasesText],
  );

  const filteredSuggestions = useMemo(() => {
    const f = queryFilter.trim().toLowerCase();
    let out = f ? suggestions.filter(q => q.text.toLowerCase().includes(f)) : suggestions;
    if (sortByScore) {
      // 已勾选的固定置顶(避免重排时跳走),分数同高的稳定保留 LLM 原序
      out = [...out].sort((a, b) => {
        const pa = picked.has(a.text) ? 1 : 0;
        const pb = picked.has(b.text) ? 1 : 0;
        if (pa !== pb) return pb - pa;
        return b.score - a.score;
      });
    }
    return out;
  }, [suggestions, queryFilter, sortByScore, picked]);

  // 按 cluster 分组渲染。clusters 为空时返回 null → 走平铺渲染回退
  const groupedByCluster = useMemo<{ cluster: ClusterMeta; items: QueryCandidate[] }[] | null>(() => {
    if (clusters.length === 0) return null;
    const byId = new Map<number, QueryCandidate[]>();
    for (const q of filteredSuggestions) {
      const cid = q.cluster_id ?? -1;
      if (!byId.has(cid)) byId.set(cid, []);
      byId.get(cid)!.push(q);
    }
    return clusters
      .map(c => ({ cluster: c, items: byId.get(c.cluster_id) || [] }))
      .filter(g => g.items.length > 0);
  }, [filteredSuggestions, clusters]);

  const toggleClusterCollapse = (cid: number) => {
    setCollapsedClusters(prev => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid); else next.add(cid);
      return next;
    });
  };
  const pickAllInCluster = (cid: number) => {
    setPicked(prev => {
      const next = new Set(prev);
      const items = suggestions.filter(q => (q.cluster_id ?? -1) === cid);
      for (const q of items) {
        if (next.size >= QUERY_MAX_PICK) break;
        next.add(q.text);
      }
      return next;
    });
  };

  const pickedCap = picked.size >= QUERY_MAX_PICK;
  const valid = name.trim().length > 0 && target.trim().length > 0
    && queries.length > 0 && engines.size > 0;

  const toggleEngine = (e: EngineId) => {
    setEngines(prev => {
      const next = new Set(prev);
      if (next.has(e)) next.delete(e); else next.add(e);
      return next;
    });
  };

  const buildPayload = (): TopicPayload => {
    // 按 queries 顺序回填 cluster_id;suggestions 里没记 cluster_id 的就给 -1
    const textToCluster = new Map<string, number>();
    for (const q of suggestions) {
      if (typeof q.cluster_id === 'number') textToCluster.set(q.text, q.cluster_id);
    }
    const query_cluster_ids = queries.map(q => textToCluster.get(q) ?? -1);
    const hasAnyCluster = query_cluster_ids.some(c => c >= 0);
    // cluster_ids 和 clusters 必须同进同出 — 只发其一会留下 "queries 没簇但
    // clusters_json 还在" 的孤儿态(topic#2 就是这么坏的)。
    // hasAnyCluster=false 时两个都不发,backend `_queries_with_meta` 会按 text
    // 沿用历史 cluster_id;clusters_json 也保持不动。
    return {
      name: name.trim(),
      target: target.trim(),
      target_aliases: aliases,
      queries,
      ...(hasAnyCluster ? {
        query_cluster_ids,
        clusters: clusters.map(c => ({
          cluster_id: c.cluster_id, label: c.label, size: c.size,
        })),
      } : {}),
      engines: Array.from(engines),
      enabled,
    };
  };

  const handleSave = async () => {
    if (!valid) return;
    setSaving(true);
    try { await onSave(buildPayload()); }
    finally { setSaving(false); }
  };

  const handleRunNow = async () => {
    if (!valid) return;
    setRunning(true);
    try {
      const res = await aiTelemetryApi.runNow(buildPayload(), token);
      setRunResults(res);
    } finally {
      setRunning(false);
    }
  };

  const handleSuggest = async () => {
    const s = seed.trim();
    if (!s || suggesting) return;
    setSuggesting(true);
    setSuggestErr(null);
    try {
      const res = await aiTelemetryApi.suggestQueries({
        seed: s, count: SUGGEST_COUNT,
        target: target.trim(),
        aliases,
        industry: industry.trim(),
      }, token);
      // 追加(去重),不覆盖 — 用户可能想多轮生成、不同 seed 累积候选
      setSuggestions(prev => {
        const seen = new Set(prev.map(q => q.text));
        const out = [...prev];
        for (const q of res.queries) {
          if (!seen.has(q.text)) { seen.add(q.text); out.push(q); }
        }
        return out;
      });
      // clusters 直接覆盖(每次挖掘是一次完整聚类,旧簇 ID 与新簇 ID 不可比)
      setClusters(res.clusters || []);
      setCollapsedClusters(new Set());
    } catch (e: unknown) {
      setSuggestErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSuggesting(false);
    }
  };

  const togglePicked = (q: string) => {
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(q)) {
        next.delete(q);
      } else if (next.size < QUERY_MAX_PICK) {
        next.add(q);
      }
      return next;
    });
  };

  const clearPicked = () => setPicked(new Set());
  const clearSuggestions = () => {
    // 只清掉未勾选的候选,保留已勾选的(否则 valid 状态会瞬间崩)
    setSuggestions(prev => prev.filter(q => picked.has(q.text)));
  };

  return (
    <section
      className="rounded-xl"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <div className="px-5 py-5 grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* 左列:基本字段 + 引擎 + 试跑结果 */}
        <div className="lg:col-span-5 space-y-4">
          <label className="block">
            <span className="text-xs text-secondary">{t('dashboard.aiTelemetry.form.name')}*</span>
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              placeholder={t('dashboard.aiTelemetry.form.namePlaceholder') || ''}
              className="mt-1 w-full px-3 py-1.5 rounded-md text-sm"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
          </label>

          <label className="block">
            <span className="text-xs text-secondary">
              <span style={{ color: 'var(--accent-primary)' }}>★ </span>
              {t('dashboard.aiTelemetry.form.target')}*
            </span>
            <input
              type="text" value={target} onChange={e => setTarget(e.target.value)}
              placeholder={t('dashboard.aiTelemetry.form.targetPlaceholder') || ''}
              className="mt-1 w-full px-3 py-1.5 rounded-md text-sm"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
            <span className="text-xs text-muted">
              {t('dashboard.aiTelemetry.form.targetHint')}
            </span>
          </label>

          <label className="block">
            <span className="text-xs text-secondary">{t('dashboard.aiTelemetry.form.aliases')}</span>
            <input
              type="text" value={aliasesText} onChange={e => setAliasesText(e.target.value)}
              placeholder={t('dashboard.aiTelemetry.form.aliasesPlaceholder') || ''}
              className="mt-1 w-full px-3 py-1.5 rounded-md text-sm"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
            <span className="text-xs text-muted">
              {t('dashboard.aiTelemetry.form.aliasesHint', { count: aliases.length })}
            </span>
          </label>

          <div>
            <span className="text-xs text-secondary">{t('dashboard.aiTelemetry.form.engines')}*</span>
            <div className="mt-2 space-y-2">
              <EngineRow
                label={t('dashboard.aiTelemetry.form.enginesCN')}
                engines={CN_ENGINES} selected={engines} onToggle={toggleEngine}
              />
              <EngineRow
                label={t('dashboard.aiTelemetry.form.enginesGlobal')}
                engines={GLOBAL_ENGINES} selected={engines} onToggle={toggleEngine}
              />
            </div>
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
            <span className="text-sm text-primary">{t('dashboard.aiTelemetry.form.enabled')}</span>
          </label>

          <p className="text-xs text-muted">{t('dashboard.aiTelemetry.form.scheduleNote')}</p>

          {runResults && (
            <div
              className="rounded-md p-3 text-xs space-y-2 max-h-60 overflow-y-auto"
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {runResults.map((r, i) => (
                <div key={i} className="border-b pb-2 last:border-0" style={{ borderColor: 'var(--border-color)' }}>
                  <div className="text-primary font-medium">{r.engine} · {r.query}</div>
                  {r.error ? (
                    <div className="text-rose-500 mt-1">⚠ {r.error}</div>
                  ) : (
                    <>
                      <div className="text-secondary mt-1 line-clamp-3">{r.answer}</div>
                      {r.citations.length > 0 && (
                        <div className="text-muted mt-1">引用 {r.citations.length} 条</div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右列:Query picker(200 候选 / 50 选) */}
        <div className="lg:col-span-7">
          <div
            className="rounded-md p-3 space-y-3"
            style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs text-secondary font-medium">
                {t('dashboard.aiTelemetry.form.queriesPickerTitle')}*
              </span>
              <span
                className="text-xs font-mono"
                style={{ color: pickedCap ? 'var(--accent-primary)' : 'var(--text-muted)' }}
              >
                {picked.size} / {QUERY_MAX_PICK}
              </span>
            </div>
            <div className="text-xs text-muted">
              {t('dashboard.aiTelemetry.form.queriesPickerHint', { max: QUERY_MAX_PICK, count: SUGGEST_COUNT })}
            </div>

            {/* seed + industry + 生成按钮 */}
            <div className="flex gap-2">
              <input
                type="text" value={seed} onChange={e => setSeed(e.target.value)}
                placeholder={t('dashboard.aiTelemetry.form.suggestPlaceholder') || ''}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleSuggest(); } }}
                className="flex-1 px-3 py-1.5 rounded-md text-sm"
                style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              />
              <button
                type="button" onClick={handleSuggest} disabled={!seed.trim() || suggesting}
                className="px-3 py-1.5 rounded-md text-sm whitespace-nowrap"
                style={{
                  background: 'var(--accent-primary)', color: '#fff',
                  opacity: !seed.trim() || suggesting ? 0.55 : 1,
                }}
              >
                {suggesting
                  ? t('dashboard.aiTelemetry.form.suggestRunningLong', { count: SUGGEST_COUNT })
                  : t('dashboard.aiTelemetry.form.suggestGenerateN', { count: SUGGEST_COUNT })}
              </button>
            </div>
            <input
              type="text" value={industry} onChange={e => setIndustry(e.target.value)}
              placeholder={t('dashboard.aiTelemetry.form.suggestIndustryPlaceholder') || ''}
              className="w-full px-3 py-1.5 rounded-md text-sm"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
            <div className="text-xs text-muted">
              {target.trim()
                ? t('dashboard.aiTelemetry.form.suggestContextOn', { target: target.trim() })
                : t('dashboard.aiTelemetry.form.suggestContextOff')}
            </div>
            {suggestErr && (
              <div className="text-xs text-rose-500">⚠ {suggestErr}</div>
            )}

            {/* 候选池 — 过滤 + 滚动列表 */}
            {suggestions.length === 0 ? (
              <div
                className="rounded-md p-4 text-xs text-muted text-center"
                style={{ background: 'var(--bg-input)', border: '1px dashed var(--border-color)' }}
              >
                {t('dashboard.aiTelemetry.form.queriesPickerEmpty')}
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <input
                    type="text" value={queryFilter} onChange={e => setQueryFilter(e.target.value)}
                    placeholder={t('dashboard.aiTelemetry.form.queriesFilterPlaceholder') || ''}
                    className="flex-1 px-3 py-1 rounded-md text-xs"
                    style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                  />
                  <button
                    type="button" onClick={() => setSortByScore(s => !s)}
                    className="px-2 py-1 rounded text-xs whitespace-nowrap"
                    style={{
                      background: sortByScore ? 'var(--accent-primary)' : 'transparent',
                      color: sortByScore ? '#fff' : 'var(--text-secondary)',
                      border: '1px solid var(--border-color)',
                    }}
                    title={sortByScore
                      ? t('dashboard.aiTelemetry.form.sortByScoreOn') || ''
                      : t('dashboard.aiTelemetry.form.sortByScoreOff') || ''}
                  >
                    {sortByScore
                      ? t('dashboard.aiTelemetry.form.sortByScoreOn')
                      : t('dashboard.aiTelemetry.form.sortByScoreOff')}
                  </button>
                  <span className="text-xs text-muted whitespace-nowrap">
                    {t('dashboard.aiTelemetry.form.queriesFilterCount', {
                      shown: filteredSuggestions.length, total: suggestions.length,
                    })}
                  </span>
                </div>
                <div
                  className="overflow-y-auto rounded-md p-2"
                  style={{
                    maxHeight: 'clamp(360px, 65vh, 720px)',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border-color)',
                  }}
                >
                  {(() => {
                    const renderRow = (q: QueryCandidate) => {
                      const isPicked = picked.has(q.text);
                      const disabled = !isPicked && pickedCap;
                      const showScore = q.score > 0;
                      const scoreColor = q.score >= 75 ? 'var(--accent-primary)'
                        : q.score >= 60 ? 'var(--text-primary)'
                        : 'var(--text-muted)';
                      return (
                        <label
                          key={q.text}
                          className="flex items-start gap-2 text-xs px-1 py-0.5 rounded"
                          style={{
                            cursor: disabled ? 'not-allowed' : 'pointer',
                            opacity: disabled ? 0.4 : 1,
                            background: isPicked ? 'var(--bg-card)' : 'transparent',
                            color: 'var(--text-primary)',
                          }}
                        >
                          <input
                            type="checkbox" checked={isPicked} disabled={disabled}
                            onChange={() => togglePicked(q.text)}
                            className="mt-0.5"
                          />
                          {showScore && (
                            <span
                              className="font-mono shrink-0 tabular-nums"
                              style={{ color: scoreColor, minWidth: '1.8rem' }}
                              title={q.sources.join(', ')}
                            >
                              {q.score}
                            </span>
                          )}
                          <span className="break-all">{q.text}</span>
                        </label>
                      );
                    };
                    if (!groupedByCluster) {
                      return (
                        <div className="space-y-1">
                          {filteredSuggestions.map(renderRow)}
                        </div>
                      );
                    }
                    return (
                      <div className="space-y-2">
                        {groupedByCluster.map(({ cluster, items }) => {
                          const collapsed = collapsedClusters.has(cluster.cluster_id);
                          const pickedInCluster = items.filter(q => picked.has(q.text)).length;
                          return (
                            <div key={cluster.cluster_id}>
                              <div
                                className="flex items-center gap-2 px-1 py-1 rounded sticky top-0"
                                style={{
                                  background: 'var(--bg-secondary)',
                                  borderBottom: '1px solid var(--border-color)',
                                }}
                              >
                                <button
                                  type="button"
                                  onClick={() => toggleClusterCollapse(cluster.cluster_id)}
                                  className="text-xs px-1"
                                  style={{ color: 'var(--text-secondary)', cursor: 'pointer' }}
                                  title={collapsed
                                    ? t('dashboard.aiTelemetry.form.clusterExpand') || ''
                                    : t('dashboard.aiTelemetry.form.clusterCollapse') || ''}
                                >
                                  {collapsed ? '▶' : '▼'}
                                </button>
                                <span className="text-xs font-medium break-all flex-1"
                                      style={{ color: 'var(--text-primary)' }}>
                                  {cluster.label}
                                </span>
                                <span className="text-xs font-mono tabular-nums shrink-0"
                                      style={{ color: 'var(--text-muted)' }}>
                                  {pickedInCluster} / {items.length}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => pickAllInCluster(cluster.cluster_id)}
                                  disabled={pickedCap}
                                  className="text-xs px-2 py-0.5 rounded shrink-0"
                                  style={{
                                    background: 'transparent',
                                    border: '1px solid var(--border-color)',
                                    color: 'var(--text-secondary)',
                                    opacity: pickedCap ? 0.4 : 1,
                                  }}
                                >
                                  {t('dashboard.aiTelemetry.form.clusterPickAll')}
                                </button>
                              </div>
                              {!collapsed && (
                                <div className="space-y-1 pl-3 pt-1">
                                  {items.map(renderRow)}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    );
                  })()}
                </div>
                <div className="flex items-center justify-between text-xs">
                  <button
                    type="button" onClick={clearPicked} disabled={picked.size === 0}
                    className="px-2 py-1 rounded"
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--border-color)',
                      color: 'var(--text-secondary)',
                      opacity: picked.size === 0 ? 0.45 : 1,
                    }}
                  >
                    {t('dashboard.aiTelemetry.form.queriesClearPicked')}
                  </button>
                  <button
                    type="button" onClick={clearSuggestions}
                    className="px-2 py-1 rounded"
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--border-color)',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    {t('dashboard.aiTelemetry.form.queriesClearUnpicked')}
                  </button>
                </div>
              </>
            )}
          </div>

        </div>
      </div>

      <footer
        className="px-5 py-3 flex items-center justify-end gap-2"
        style={{ borderTop: '1px solid var(--border-color)' }}
      >
        <button type="button" onClick={onCancel} className="px-3 py-1.5 text-sm rounded-md text-secondary">
          {t('dashboard.aiTelemetry.form.cancel')}
        </button>
        <button
          type="button" onClick={handleRunNow} disabled={!valid || running}
          className="px-3 py-1.5 text-sm rounded-md disabled:opacity-40"
          style={{ background: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
        >
          {running ? '…' : t('dashboard.aiTelemetry.form.runNow')}
        </button>
        <button
          type="button" onClick={handleSave} disabled={!valid || saving}
          className="px-3 py-1.5 text-sm rounded-md text-white disabled:opacity-40"
          style={{ background: 'var(--accent-primary)' }}
        >
          {saving ? '…' : t('dashboard.aiTelemetry.form.save')}
        </button>
      </footer>
    </section>
  );
}

function EngineRow({
  label, engines, selected, onToggle,
}: {
  label: string; engines: EngineId[];
  selected: Set<EngineId>; onToggle: (e: EngineId) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-muted w-10">{label}</span>
      {engines.map(e => {
        const active = selected.has(e);
        return (
          <button
            key={e} type="button" onClick={() => onToggle(e)}
            className="px-2 py-1 rounded text-xs"
            style={{
              background: active ? 'var(--accent-primary)' : 'var(--bg-input)',
              color: active ? 'white' : 'var(--text-secondary)',
              border: '1px solid var(--border-color)',
            }}
          >
            {t(`dashboard.aiTelemetry.engine.${e}`)}
          </button>
        );
      })}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
//  v1 引用追踪 Tab
// ═══════════════════════════════════════════════════════════════

function TrackingTab({ topics, token, onRun }: {
  topics: Topic[]; token: string; onRun: (id: number) => void;
}) {
  const { t } = useTranslation();
  const [topicId, setTopicId] = useState<number | null>(topics[0]?.id ?? null);
  const [matrix, setMatrix] = useState<TrackingMatrix | null>(null);
  const [sov, setSoV] = useState<ShareOfVoice | null>(null);
  const [loading, setLoading] = useState(false);
  const [openCell, setOpenCell] = useState<{ query: string; engine: EngineId } | null>(null);

  useEffect(() => {
    if (topicId == null) return;
    setLoading(true);
    Promise.all([
      aiTelemetryApi.getTrackingMatrix(topicId, token),
      aiTelemetryApi.getShareOfVoice(topicId, 90, token).catch(() => null),
    ]).then(([m, s]) => {
      setMatrix(m);
      setSoV(s);
    }).finally(() => setLoading(false));
  }, [topicId, token]);

  if (topics.length === 0) {
    return (
      <div className="py-12 text-center text-sm text-muted">
        {t('dashboard.aiTelemetry.tracking.noTopics')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label className="text-xs text-secondary">{t('dashboard.aiTelemetry.tracking.selectTopic')}</label>
        <select
          value={topicId ?? ''} onChange={e => setTopicId(Number(e.target.value))}
          className="px-3 py-1.5 rounded-md text-sm"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
        >
          {topics.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        {topicId != null && (
          <button type="button" onClick={() => onRun(topicId)}
            className="text-xs px-3 py-1.5 rounded-md text-white"
            style={{ background: 'var(--accent-primary)' }}>
            ⏵ {t('dashboard.aiTelemetry.tracking.runNow')}
          </button>
        )}
      </div>

      {loading && <div className="py-12 text-center text-sm text-muted">…</div>}
      {!loading && matrix && (
        <>
          <TrackingHeader matrix={matrix} sov={sov} />
          <TimelineRow timeline={matrix.timeline} />
          <MatrixGrid matrix={matrix} onPick={(q, e) => setOpenCell({ query: q, engine: e })} />
        </>
      )}

      {openCell && topicId != null && (
        <CellDrawerView
          topicId={topicId} query={openCell.query} engine={openCell.engine}
          token={token} onClose={() => setOpenCell(null)}
        />
      )}
    </div>
  );
}

function TrackingHeader({ matrix, sov }: { matrix: TrackingMatrix; sov: ShareOfVoice | null }) {
  const { t } = useTranslation();
  const started = matrix.started_at ? new Date(matrix.started_at).toLocaleDateString() : '-';
  return (
    <div className="rounded-md p-3 text-xs flex flex-wrap items-center gap-x-4 gap-y-1"
      style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
      <span className="text-secondary">
        {t('dashboard.aiTelemetry.tracking.targetLabel')}:
        <strong className="text-primary ml-1">{matrix.target || '-'}</strong>
        {matrix.target_aliases.length > 0 && (
          <span className="text-muted ml-1">
            ({matrix.target_aliases.join(' / ')})
          </span>
        )}
      </span>
      <span className="text-secondary">
        {t('dashboard.aiTelemetry.tracking.startedAt')}: <span className="text-primary">{started}</span>
      </span>
      <span className="text-secondary">
        {t('dashboard.aiTelemetry.tracking.totalRuns')}: <span className="text-primary tabular-nums">{matrix.total_runs}</span>
      </span>
      <span className="text-secondary">
        {t('dashboard.aiTelemetry.tracking.hitCells')}:
        <span className="text-primary tabular-nums ml-1">{matrix.hit_cells}</span>
        <span className="text-muted ml-0.5">/{matrix.total_cells}</span>
        <span className="ml-1 px-1.5 py-0.5 rounded text-[10px]"
          style={{ background: 'rgba(14,165,233,0.15)', color: 'var(--accent-primary)' }}>
          {matrix.hit_cells_pct.toFixed(1)}%
        </span>
      </span>
      {sov && (
        <>
          <span className="text-secondary">
            {t('dashboard.aiTelemetry.tracking.optimalRate')}:
            <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold"
              style={{
                background: sov.optimal_rate_pct >= 70
                  ? 'rgba(16,185,129,0.18)' : sov.optimal_rate_pct >= 40
                    ? 'rgba(245,158,11,0.18)' : 'rgba(239,68,68,0.18)',
                color: sov.optimal_rate_pct >= 70
                  ? '#10b981' : sov.optimal_rate_pct >= 40 ? '#f59e0b' : '#ef4444',
              }}>
              {sov.optimal_rate_pct.toFixed(1)}%
            </span>
          </span>
          <span className="text-secondary">
            {t('dashboard.aiTelemetry.tracking.saivLabel')}:
            <span className="text-primary tabular-nums ml-1">{sov.saiv_pct.toFixed(1)}%</span>
            <span className="text-muted ml-1">
              ({sov.brand_count} vs {sov.competitors_count_total})
            </span>
          </span>
        </>
      )}
    </div>
  );
}

function TimelineRow({ timeline }: { timeline: EngineFirstHit[] }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-md p-3 text-xs space-y-1.5"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="text-xs font-semibold text-secondary uppercase tracking-wider mb-1">
        {t('dashboard.aiTelemetry.tracking.timelineTitle')}
      </div>
      {timeline.map(e => {
        const hit = e.first_hit_at;
        return (
          <div key={e.engine} className="flex items-center gap-2">
            <span style={{
              display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
              background: hit ? '#10b981' : 'var(--border-color)',
            }} />
            <span className="w-20" style={{ color: ENGINE_COLORS[e.engine] }}>
              {t(`dashboard.aiTelemetry.engine.${e.engine}`)}
            </span>
            {hit ? (
              <>
                <span className="text-primary tabular-nums">
                  {new Date(hit).toLocaleDateString()}
                </span>
                <span className="text-muted">
                  · {t('dashboard.aiTelemetry.tracking.firstHitDay', { n: e.days_after_start ?? '?' })}
                </span>
                {e.first_hit_query && (
                  <span className="text-muted truncate flex-1">· {e.first_hit_query}</span>
                )}
              </>
            ) : (
              <span className="text-muted">{t('dashboard.aiTelemetry.tracking.notYet')}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MatrixGrid({ matrix, onPick }: {
  matrix: TrackingMatrix;
  onPick: (query: string, engine: EngineId) => void;
}) {
  const { t } = useTranslation();
  const byKey = useMemo(() => {
    const m = new Map<string, QueryHitCell>();
    for (const c of matrix.cells) m.set(`${c.query}${c.engine}`, c);
    return m;
  }, [matrix.cells]);

  return (
    <div className="rounded-md overflow-x-auto"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
            <th className="text-left px-3 py-2 font-medium text-secondary sticky left-0"
              style={{ background: 'var(--bg-card)', minWidth: 240 }}>
              {t('dashboard.aiTelemetry.tracking.queryEngineHeader')}
            </th>
            {matrix.engines.map(e => (
              <th key={e} className="px-3 py-2 font-medium text-center"
                style={{ color: ENGINE_COLORS[e] }}>
                {t(`dashboard.aiTelemetry.engine.${e}`)}
              </th>
            ))}
            <th className="px-3 py-2 font-medium text-center text-secondary">
              {t('dashboard.aiTelemetry.tracking.rowHits')}
            </th>
          </tr>
        </thead>
        <tbody>
          {matrix.queries.map(q => {
            let rowHits = 0;
            let rowTotal = 0;
            for (const e of matrix.engines) {
              const c = byKey.get(`${q}${e}`);
              if (c) { rowTotal += 1; if (c.total_hits >= 1) rowHits += 1; }
            }
            return (
              <tr key={q} style={{ borderBottom: '1px solid var(--border-color)' }}>
                <td className="px-3 py-2 sticky left-0" style={{ background: 'var(--bg-card)' }}>
                  <div className="truncate text-primary">{q}</div>
                </td>
                {matrix.engines.map(e => {
                  const cell = byKey.get(`${q}${e}`);
                  return (
                    <td key={e} className="px-1 py-1.5 text-center">
                      <CellBadge cell={cell} onClick={() => onPick(q, e)} />
                    </td>
                  );
                })}
                <td className="px-3 py-2 text-center text-secondary tabular-nums">
                  {rowHits}/{rowTotal}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="px-3 py-2 text-[11px] text-muted"
        style={{ borderTop: '1px solid var(--border-color)' }}>
        {t('dashboard.aiTelemetry.tracking.legend')}
      </div>
    </div>
  );
}

function CellBadge({ cell, onClick }: { cell: QueryHitCell | undefined; onClick: () => void }) {
  if (!cell || cell.status === 'pending') {
    return (
      <button type="button" onClick={onClick}
        className="px-2 py-1 rounded text-muted cursor-pointer"
        title="待做">⌛</button>
    );
  }
  if (cell.status === 'running') {
    return (
      <button type="button" onClick={onClick}
        className="px-2 py-1 rounded text-muted cursor-pointer"
        title="进行中">⏳</button>
    );
  }
  // done
  const hit = cell.total_hits >= 1;
  const date = cell.first_hit_at ? new Date(cell.first_hit_at).toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' }) : '';
  return (
    <button type="button" onClick={onClick}
      className="px-2 py-1 rounded transition-colors cursor-pointer"
      style={{
        background: hit ? 'rgba(16,185,129,0.15)' : 'transparent',
        color: hit ? '#10b981' : 'var(--text-muted)',
        border: hit ? '1px solid rgba(16,185,129,0.4)' : '1px solid transparent',
      }}
      title={hit ? `${cell.total_hits}/${cell.total_runs} 命中` : `${cell.total_runs} 次跑批 0 命中`}>
      {hit ? `✓ ${date}` : '✓'}
    </button>
  );
}

// ── drawer:cell 详情 + 历次答复 + 诊断按钮 ────────────────────

function CellDrawerView({
  topicId, query, engine, token, onClose,
}: {
  topicId: number; query: string; engine: EngineId; token: string; onClose: () => void;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<CellDrawer | null>(null);
  const [insight, setInsight] = useState<CellInsight | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    aiTelemetryApi.getCellDrawer(topicId, query, engine, token).then(d => {
      setData(d);
      setInsight(d.insight);
    });
  }, [topicId, query, engine, token]);

  const handleAnalyze = async (force = false) => {
    setAnalyzing(true);
    try {
      const r = await aiTelemetryApi.fetchCellInsight(topicId, query, engine, token, force);
      setInsight(r);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleFeedback = async (f: 'helpful' | 'not_helpful' | 'wrong') => {
    if (!insight) return;
    await aiTelemetryApi.postCellInsightFeedback(insight.id, f, token);
    setInsight({ ...insight, feedback: f });
  };

  const node = (
    <div className="fixed inset-0 z-[1100] flex" onMouseDown={onClose}
      style={{ background: 'rgba(0,0,0,0.35)' }}>
      <div className="ml-auto h-full overflow-y-auto"
        style={{ width: 560, background: 'var(--bg-card)', borderLeft: '1px solid var(--border-color)' }}
        onMouseDown={e => e.stopPropagation()}>
        <header className="sticky top-0 px-5 py-3 flex items-center justify-between z-10"
          style={{ background: 'var(--bg-card)', borderBottom: '1px solid var(--border-color)' }}>
          <div>
            <div className="text-sm font-semibold text-primary">
              {t(`dashboard.aiTelemetry.engine.${engine}`)} · {query}
            </div>
            {data && (
              <div className="text-[11px] text-muted mt-0.5">
                {data.cell.total_hits}/{data.cell.total_runs} {t('dashboard.aiTelemetry.tracking.hits')}
                {data.cell.first_hit_at && (
                  <> · {t('dashboard.aiTelemetry.tracking.firstHit')}: {new Date(data.cell.first_hit_at).toLocaleDateString()}</>
                )}
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} className="text-muted hover:text-primary text-lg px-2">✕</button>
        </header>

        <div className="px-5 py-4 space-y-4 text-xs">
          {!data && <div className="text-muted py-6 text-center">…</div>}

          {/* 诊断块(LLM) */}
          {data && (
            <section className="rounded-md p-3"
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
              <div className="flex items-center justify-between mb-2">
                <strong className="text-primary">🔍 {t('dashboard.aiTelemetry.insight.title')}</strong>
                {insight ? (
                  <button type="button" onClick={() => handleAnalyze(true)} disabled={analyzing}
                    className="text-[11px] text-muted hover:text-primary">
                    ↻ {t('dashboard.aiTelemetry.insight.refresh')}
                  </button>
                ) : (
                  <button type="button" onClick={() => handleAnalyze(false)} disabled={analyzing}
                    className="text-[11px] px-2 py-1 rounded text-white"
                    style={{ background: 'var(--accent-primary)' }}>
                    {analyzing ? '…' : t('dashboard.aiTelemetry.insight.analyze')}
                  </button>
                )}
              </div>
              {analyzing && <div className="text-muted">🤔 {t('dashboard.aiTelemetry.insight.analyzing')}</div>}
              {!analyzing && !insight && (
                <p className="text-muted">{t('dashboard.aiTelemetry.insight.empty')}</p>
              )}
              {insight && <InsightBlock insight={insight} onFeedback={handleFeedback} />}
            </section>
          )}

          {/* 历次答复 */}
          {data && data.evidence.length > 0 && (
            <section>
              <div className="text-xs font-semibold text-secondary uppercase tracking-wider mb-2">
                {t('dashboard.aiTelemetry.tracking.evidenceTitle')}
              </div>
              <div className="space-y-2">
                {data.evidence.map(e => <EvidenceCard key={e.response_id} ev={e} />)}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(node, document.body);
}

function InsightBlock({ insight, onFeedback }: {
  insight: CellInsight;
  onFeedback: (f: 'helpful' | 'not_helpful' | 'wrong') => void;
}) {
  const { t } = useTranslation();
  const verdictColor: Record<string, string> = {
    hit_stable: '#10b981', hit_unstable: '#f59e0b', near_miss: '#3b82f6',
    no_signal: '#9ca3af', negative_mention: '#ef4444',
  };
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] px-1.5 py-0.5 rounded font-bold"
          style={{ background: 'rgba(0,0,0,0.05)', color: verdictColor[insight.verdict] || '#9ca3af' }}>
          {t(`dashboard.aiTelemetry.insight.verdict.${insight.verdict}`)}
        </span>
        <span className="text-muted">{insight.llm_model}</span>
      </div>
      <p className="text-primary">{insight.summary}</p>

      {insight.competitors_top3.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-muted uppercase mb-0.5">
            {t('dashboard.aiTelemetry.insight.competitors')}
          </div>
          <ul className="space-y-0.5 ml-3">
            {insight.competitors_top3.map((c, i) => (
              <li key={i} className="text-muted">
                ▸ <span className="text-primary">{c.name}</span>
                <span className="ml-1">({c.count})</span>
                {c.snippet && <span className="text-muted text-[11px] block ml-3">「{c.snippet}」</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {insight.answer_format && (
        <div className="text-muted">
          {t('dashboard.aiTelemetry.insight.format')}:
          <span className="text-primary ml-1">{insight.answer_format}</span>
        </div>
      )}

      {insight.citation_domains.length > 0 && (
        <div className="text-muted">
          {t('dashboard.aiTelemetry.insight.citationDomains')}:
          <span className="text-primary ml-1">{insight.citation_domains.slice(0, 6).join(' · ')}</span>
        </div>
      )}

      {insight.recommendations.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-muted uppercase mb-1">
            {t('dashboard.aiTelemetry.insight.recommendations')}
          </div>
          <ul className="space-y-1.5">
            {insight.recommendations.map((r, i) => (
              <li key={i} className="rounded p-2"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                <span className="text-[10px] px-1.5 py-0.5 rounded font-bold mr-2"
                  style={{
                    background: r.priority === 'P0' ? '#ef4444' : r.priority === 'P1' ? '#f59e0b' : '#9ca3af',
                    color: 'white',
                  }}>{r.priority}</span>
                <strong className="text-primary">{r.title}</strong>
                <p className="mt-1 text-secondary">{r.action}</p>
                {r.why && <p className="mt-0.5 text-muted text-[11px]">— {r.why}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <span className="text-muted text-[11px]">{t('dashboard.aiTelemetry.insight.feedbackQ')}:</span>
        {(['helpful', 'not_helpful', 'wrong'] as const).map(f => (
          <button key={f} type="button" onClick={() => onFeedback(f)}
            className="text-[11px] px-2 py-0.5 rounded"
            style={{
              background: insight.feedback === f ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
              color: insight.feedback === f ? 'white' : 'var(--text-secondary)',
            }}>
            {f === 'helpful' ? '👍' : f === 'not_helpful' ? '👎' : '⚠'}
            {' '}{t(`dashboard.aiTelemetry.insight.feedback.${f}`)}
          </button>
        ))}
      </div>
    </div>
  );
}

function EvidenceCard({ ev }: { ev: CellDrawer['evidence'][number] }) {
  const { t } = useTranslation();
  const dt = ev.created_at ? new Date(ev.created_at).toLocaleString() : '';
  const posColor: Record<string, { bg: string; fg: string }> = {
    lead: { bg: 'rgba(16,185,129,0.18)', fg: '#10b981' },
    body: { bg: 'rgba(59,130,246,0.18)', fg: '#3b82f6' },
    tail: { bg: 'rgba(245,158,11,0.18)', fg: '#f59e0b' },
    unknown: { bg: 'rgba(156,163,175,0.18)', fg: '#9ca3af' },
  };
  const pos = ev.mention_position;
  return (
    <div className="rounded p-2"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-center gap-2 text-[11px] text-muted flex-wrap">
        <span className={ev.hit ? 'text-emerald-500' : 'text-muted'}>
          {ev.hit ? '✓' : '○'}
        </span>
        <span>{dt}</span>
        {ev.hit && pos && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold"
            style={{
              background: (posColor[pos] || posColor.unknown).bg,
              color: (posColor[pos] || posColor.unknown).fg,
            }}>
            {t(`dashboard.aiTelemetry.insight.position.${pos}`)}
          </span>
        )}
        {ev.source_url && (
          <a href={ev.source_url} target="_blank" rel="noreferrer" className="text-primary hover:underline truncate">
            📎 {ev.source_url.replace(/^https?:\/\//, '').slice(0, 40)}…
          </a>
        )}
      </div>
      {ev.hit && ev.hit_excerpt && (
        <p className="mt-1 text-primary text-xs italic">「{ev.hit_excerpt}」</p>
      )}
      {!ev.hit && ev.answer && (
        <p className="mt-1 text-muted text-[11px] line-clamp-3">{ev.answer.slice(0, 200)}…</p>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
//  v1.2 优化建议(周报) Tab
// ═══════════════════════════════════════════════════════════════

function BriefingsTab({ topics, token }: { topics: Topic[]; token: string }) {
  const { t } = useTranslation();
  const [topicId, setTopicId] = useState<number | null>(topics[0]?.id ?? null);
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [generating, setGenerating] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (topicId == null) return;
    setLoading(true);
    aiTelemetryApi.listBriefings(topicId, token).then(list => {
      setBriefings(list);
      setSelectedId(list[0]?.id ?? null);
    }).finally(() => setLoading(false));
  }, [topicId, token]);

  const selected = useMemo(
    () => briefings.find(b => b.id === selectedId) ?? null,
    [briefings, selectedId],
  );

  const handleGenerate = async () => {
    if (topicId == null) return;
    setGenerating(true);
    try {
      const fresh = await aiTelemetryApi.triggerBriefing(topicId, token);
      const list = await aiTelemetryApi.listBriefings(topicId, token);
      setBriefings(list);
      setSelectedId(fresh.id);
    } finally {
      setGenerating(false);
    }
  };

  if (topics.length === 0) {
    return (
      <div className="py-12 text-center text-sm text-muted">
        {t('dashboard.aiTelemetry.briefings.noTopics')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-xs text-secondary">{t('dashboard.aiTelemetry.briefings.selectTopic')}</label>
        <select
          value={topicId ?? ''} onChange={e => setTopicId(Number(e.target.value))}
          className="px-3 py-1.5 rounded-md text-sm"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
        >
          {topics.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        {briefings.length > 0 && (
          <select value={selectedId ?? ''} onChange={e => setSelectedId(Number(e.target.value))}
            className="px-3 py-1.5 rounded-md text-sm"
            style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}>
            {briefings.map(b => (
              <option key={b.id} value={b.id}>
                {new Date(b.period_start).toLocaleDateString()} ~ {new Date(b.period_end).toLocaleDateString()}
              </option>
            ))}
          </select>
        )}
        <button type="button" onClick={handleGenerate} disabled={generating || topicId == null}
          className="text-xs px-3 py-1.5 rounded-md text-white"
          style={{ background: 'var(--accent-primary)' }}>
          {generating ? '…' : `📧 ${t('dashboard.aiTelemetry.briefings.regenerate')}`}
        </button>
      </div>

      {loading && <div className="py-12 text-center text-sm text-muted">…</div>}
      {!loading && briefings.length === 0 && (
        <div className="py-12 text-center text-sm text-muted">
          {t('dashboard.aiTelemetry.briefings.empty')}
        </div>
      )}
      {!loading && selected && <BriefingView briefing={selected} token={token} />}
    </div>
  );
}

function BriefingView({ briefing, token }: { briefing: Briefing; token: string }) {
  const { t } = useTranslation();
  const [score, setScore] = useState<number | null>(briefing.feedback_score);

  const handleScore = async (n: number) => {
    setScore(n);
    await aiTelemetryApi.postBriefingFeedback(briefing.id, n, token);
  };

  return (
    <article className="rounded-md p-5"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <header className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-primary">
          📰 {new Date(briefing.period_start).toLocaleDateString()} - {new Date(briefing.period_end).toLocaleDateString()}
        </h3>
        <span className="text-[11px] text-muted">
          {briefing.llm_model} · {t('dashboard.aiTelemetry.briefings.generatedAt')} {new Date(briefing.generated_at).toLocaleString()}
        </span>
      </header>

      <div className="prose prose-sm max-w-none text-primary whitespace-pre-wrap">
        {briefing.body_md}
      </div>

      {briefing.top_actions.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold text-secondary uppercase tracking-wider mb-2">
            🎯 {t('dashboard.aiTelemetry.briefings.topActions')}
          </div>
          <ul className="space-y-2">
            {briefing.top_actions.map((a, i) => (
              <li key={i} className="rounded p-2 text-xs"
                style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
                <span className="text-[10px] px-1.5 py-0.5 rounded font-bold mr-2"
                  style={{
                    background: a.priority === 'P0' ? '#ef4444' : a.priority === 'P1' ? '#f59e0b' : '#9ca3af',
                    color: 'white',
                  }}>{a.priority}</span>
                <strong className="text-primary">{a.title}</strong>
                <p className="mt-1 text-muted">{a.why}</p>
                <p className="mt-0.5 text-secondary">→ {a.how}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 flex items-center gap-2 text-xs">
        <span className="text-muted">{t('dashboard.aiTelemetry.briefings.rate')}:</span>
        {[1, 2, 3, 4, 5].map(n => (
          <button key={n} type="button" onClick={() => handleScore(n)}
            className="hover:scale-110 transition-transform">
            <span style={{ color: (score ?? 0) >= n ? '#f59e0b' : 'var(--text-muted)' }}>★</span>
          </button>
        ))}
      </div>
    </article>
  );
}
