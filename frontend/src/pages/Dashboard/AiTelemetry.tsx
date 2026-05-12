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
import { Fragment, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { PageHead } from '../../components/PageHead';
import {
  aiTelemetryApi, CN_ENGINES, GLOBAL_ENGINES,
  type EngineId, type Topic, type TopicPayload, type RunNowResult,
  type RunSummary, type ResponseRow, type Overview,
} from '../../services/aiTelemetryApi';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  AreaChart, Area,
} from 'recharts';

type TabKey = 'overview' | 'config' | 'results';

const ENGINE_COLORS: Record<EngineId, string> = {
  deepseek: '#5B6CFF', doubao: '#FF7043', qwen: '#7E57C2',
  wenxin: '#26A69A', yuanbao: '#EF5350',
  chatgpt: '#10A37F', claude: '#C97650', gemini: '#4285F4',
  grok: '#9CA3AF', copilot: '#0078D4',
};

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

  return (
    <div className="space-y-4">
      <PageHead titleKey="dashboard.aiTelemetry.title" titleFallback="AI Telemetry" />

      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-primary">{t('dashboard.aiTelemetry.title')}</h1>
          <p className="text-sm text-secondary mt-1">{t('dashboard.aiTelemetry.subtitle')}</p>
        </div>
        <button
          type="button"
          onClick={() => setEditing(null)}
          className="px-3 py-1.5 text-sm rounded-md text-white"
          style={{ background: 'var(--accent-primary)' }}
        >
          + {t('dashboard.aiTelemetry.newTopic')}
        </button>
      </header>

      <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
        {(['overview', 'config', 'results'] as TabKey[]).map(k => (
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
      {tab === 'config' && (
        <TopicTable
          topics={topics} loading={loading}
          onEdit={setEditing} onDelete={handleDelete} onRun={handleRun}
        />
      )}
      {tab === 'results' && <ResultsTab topics={topics} token={token} />}

      {editing !== undefined && (
        <TopicModal
          initial={editing}
          token={token}
          onCancel={() => setEditing(undefined)}
          onSave={handleSave}
        />
      )}
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
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (topicId === null && topics.length > 0) setTopicId(topics[0].id);
  }, [topics, topicId]);

  useEffect(() => {
    if (topicId === null) return;
    setLoading(true);
    aiTelemetryApi.getOverview(topicId, period, token)
      .then(setData)
      .finally(() => setLoading(false));
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
        />
        <KpiCard
          label={t('dashboard.aiTelemetry.overview.kpiCitations')}
          value={data?.citations.value ?? 0}
          delta={data?.citations.delta_pct ?? null}
          sparkline={data?.citations.sparkline ?? []}
          loading={loading}
          fmt="int"
        />
        <KpiCard
          label={t('dashboard.aiTelemetry.overview.kpiGrowth')}
          value={data?.growth.value ?? 0}
          unit="%"
          delta={null}
          sparkline={[]}
          loading={loading}
          accentByValue
        />
        <KpiCard
          label={t('dashboard.aiTelemetry.overview.kpiEngines')}
          value={data?.engines_covered.value ?? 0}
          unit={`/${data?.engines_total ?? 0}`}
          delta={data?.engines_covered.delta_pct ?? null}
          sparkline={[]}
          loading={loading}
          fmt="int"
        />
      </div>

      {/* 趋势图 */}
      <TrendChart data={data} loading={loading} />
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
}

function KpiCard({ label, value, unit, delta, sparkline, loading, fmt, accentByValue }: KpiCardProps) {
  const display = fmt === 'int'
    ? Math.round(value).toLocaleString()
    : (Number.isInteger(value) ? value.toString() : value.toFixed(1));
  const accent = accentByValue
    ? (value > 0 ? 'text-emerald-500' : value < 0 ? 'text-rose-500' : 'text-primary')
    : 'text-primary';

  return (
    <div
      className="rounded-lg px-4 py-3"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <div className="text-xs text-secondary mb-1">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className={`text-2xl font-semibold ${accent}`}>
          {loading ? '…' : (accentByValue && value > 0 ? '+' : '') + display}
        </span>
        {unit && <span className="text-xs text-muted">{unit}</span>}
      </div>
      <div className="flex items-center justify-between mt-1 h-8">
        <span className="text-xs">
          {delta !== null && delta !== undefined && (
            <span className={delta >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
              {delta >= 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(0)}%
            </span>
          )}
        </span>
        {sparkline.length > 1 && (
          <div className="w-20 h-8">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={sparkline.map((v, i) => ({ i, v }))}>
                <Area
                  type="monotone" dataKey="v"
                  stroke="var(--accent-primary)" fill="var(--accent-primary)" fillOpacity={0.15}
                  strokeWidth={1.5} dot={false} isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
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

  // 把 trend 数据 flatten 成 recharts 用的形式:每天一行,每个 engine 一列
  const chartData = data.trend.map(p => {
    const row: any = { date: p.date.slice(5) }; // 月-日
    for (const e of data.engines) {
      row[e] = p.values[e] ?? 0;
    }
    return row;
  });

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-primary">
          {t('dashboard.aiTelemetry.overview.trendTitle')}
        </h3>
        <div className="flex flex-wrap gap-3 text-xs">
          {data.engines.map(e => (
            <span key={e} className="flex items-center gap-1 text-secondary">
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ background: ENGINE_COLORS[e] || '#888' }}
              />
              {t(`dashboard.aiTelemetry.engine.${e}`, e)}
            </span>
          ))}
        </div>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" opacity={0.4} />
            <XAxis
              dataKey="date"
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--border-color)' }}
            />
            <YAxis
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--border-color)' }}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-color)',
                borderRadius: 6,
                fontSize: 12,
              }}
              labelStyle={{ color: 'var(--text-primary)' }}
            />
            <Legend wrapperStyle={{ display: 'none' }} />
            {data.engines.map(e => (
              <Line
                key={e}
                type="monotone"
                dataKey={e}
                stroke={ENGINE_COLORS[e] || '#888'}
                strokeWidth={2}
                dot={{ r: 3 }}
                activeDot={{ r: 5 }}
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

interface TopicModalProps {
  initial: Topic | null;
  token: string;
  onCancel: () => void;
  onSave: (payload: TopicPayload) => Promise<void>;
}

function TopicModal({ initial, token, onCancel, onSave }: TopicModalProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name || '');
  const [queriesText, setQueriesText] = useState((initial?.queries || []).join('\n'));
  const [engines, setEngines] = useState<Set<EngineId>>(
    new Set(initial?.engines || ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao'])
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResults, setRunResults] = useState<RunNowResult[] | null>(null);

  const queries = useMemo(
    () => queriesText.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 10),
    [queriesText],
  );

  const valid = name.trim().length > 0 && queries.length > 0 && engines.size > 0;

  const toggleEngine = (e: EngineId) => {
    setEngines(prev => {
      const next = new Set(prev);
      if (next.has(e)) next.delete(e); else next.add(e);
      return next;
    });
  };

  const buildPayload = (): TopicPayload => ({
    name: name.trim(),
    queries,
    engines: Array.from(engines),
    enabled,
  });

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

  const node = (
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        className="rounded-xl shadow-2xl w-full max-w-2xl max-h-[88vh] flex flex-col"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >
        <header
          className="px-5 py-3 flex items-center justify-between"
          style={{ borderBottom: '1px solid var(--border-color)' }}
        >
          <h3 className="text-sm font-semibold text-primary">
            {t(initial ? 'dashboard.aiTelemetry.editTopic' : 'dashboard.aiTelemetry.newTopic')}
          </h3>
          <button type="button" onClick={onCancel} className="text-muted hover:text-primary text-lg leading-none px-2">×</button>
        </header>

        <div className="px-5 py-4 space-y-4 overflow-y-auto">
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
            <span className="text-xs text-secondary">{t('dashboard.aiTelemetry.form.queries')}*</span>
            <textarea
              rows={5} value={queriesText} onChange={e => setQueriesText(e.target.value)}
              placeholder={t('dashboard.aiTelemetry.form.queriesPlaceholder') || ''}
              className="mt-1 w-full px-3 py-1.5 rounded-md text-sm font-mono"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
            <span className="text-xs text-muted">{queries.length} / 10</span>
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
              className="mt-2 rounded-md p-3 text-xs space-y-2 max-h-60 overflow-y-auto"
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
      </div>
    </div>
  );

  return createPortal(node, document.body);
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
