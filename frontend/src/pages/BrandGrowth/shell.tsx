import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { PageHead } from '../../components/PageHead';
import { aiTelemetryApi, type Topic } from '../../services/aiTelemetryApi';
import { useBgLang, engineLabel } from './lang';

export type PeriodDays = 7 | 30 | 90;

export interface ShellState {
  token: string;
  topics: Topic[];
  topic: Topic | null;
  topicId: number | null;
  setTopicId: (id: number) => void;
  period: PeriodDays;
  setPeriod: (p: PeriodDays) => void;
  // 模型多选(2026-05-21):空数组 = 全选/汇总语义
  selectedEngines: string[];
  setSelectedEngines: (es: string[]) => void;
  loading: boolean;
}

export function useShellState(): ShellState {
  const [searchParams, setSearchParams] = useSearchParams();
  const token = localStorage.getItem('token') || '';
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);

  const urlTopic = Number(searchParams.get('topic') || '0');
  const urlPeriod = Number(searchParams.get('period') || '30');
  const period = ([7, 30, 90].includes(urlPeriod) ? urlPeriod : 30) as PeriodDays;
  const topicId = urlTopic > 0 ? urlTopic : (topics[0]?.id ?? null);
  const topic = topics.find(t => t.id === topicId) || null;

  // engines 参数:逗号分隔,空 / 缺省 = 全选语义
  const urlEnginesRaw = searchParams.get('engines') || '';
  const urlEngines = urlEnginesRaw
    .split(',').map(s => s.trim()).filter(Boolean);
  // 与 topic.engines 取交集兜底 — 切 topic 时上一份选择可能不再适用
  const topicEngines = topic?.engines ?? [];
  const selectedEngines = topicEngines.length === 0
    ? urlEngines
    : urlEngines.filter(e => topicEngines.includes(e as Topic['engines'][number]));

  useEffect(() => {
    if (!token) { setLoading(false); return; }
    aiTelemetryApi.listTopics(token)
      .then(list => setTopics(list))
      .catch(() => setTopics([]))
      .finally(() => setLoading(false));
  }, [token]);

  const setTopicId = (id: number) => {
    const next = new URLSearchParams(searchParams);
    next.set('topic', String(id));
    setSearchParams(next, { replace: true });
  };
  const setPeriod = (p: PeriodDays) => {
    const next = new URLSearchParams(searchParams);
    next.set('period', String(p));
    setSearchParams(next, { replace: true });
  };
  const setSelectedEngines = (es: string[]) => {
    const next = new URLSearchParams(searchParams);
    if (es.length === 0) next.delete('engines');
    else next.set('engines', es.join(','));
    setSearchParams(next, { replace: true });
  };

  return {
    token, topics, topic, topicId, setTopicId, setPeriod, period,
    selectedEngines, setSelectedEngines, loading,
  };
}

export function BrandGrowthHeader({
  title, breadcrumb, state,
}: {
  title: string;
  breadcrumb?: { label: string; to: string }[];
  state: ShellState;
}) {
  const navigate = useNavigate();
  const L = useBgLang();
  const periodLabel = `${L.topicPicker === '主题' ? `近 ${state.period} 天` : `last ${state.period}d`}`;
  const subtitle = state.topic
    ? `${state.topic.name} · ${periodLabel}`
    : periodLabel;
  const isSubPage = (breadcrumb?.length ?? 0) > 0;
  const backTo = breadcrumb && breadcrumb.length > 0 ? breadcrumb[0].to : null;
  return (
    <div className="px-6 py-4 border-b backdrop-blur sticky top-16 z-30"
      style={{ borderColor: 'var(--border-color)', background: 'color-mix(in srgb, var(--bg-card) 90%, transparent)' }}>
      <div className="flex flex-wrap items-center gap-3 justify-between max-w-[1400px] mx-auto">
        <div className="flex items-center gap-3 min-w-0">
          {isSubPage && backTo ? (
            <button
              type="button"
              onClick={() => {
                if (window.history.length > 1) navigate(-1);
                else navigate(backTo);
              }}
              className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 hover:scale-105 transition"
              style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
              title={L.back}
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
              </svg>
            </button>
          ) : (
            <span
              className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{
                background: 'linear-gradient(135deg, var(--accent-primary), color-mix(in srgb, var(--accent-primary) 60%, #8b5cf6))',
                color: 'white',
              }}
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
                  d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
              </svg>
            </span>
          )}
          <div className="flex flex-col min-w-0">
            {breadcrumb && breadcrumb.length > 0 && (
              <div className="text-[11px] text-muted truncate">
                {breadcrumb.map((b, i) => (
                  <span key={i}>
                    <Link to={b.to} className="hover:underline">{b.label}</Link>
                    {i < breadcrumb.length - 1 ? ' · ' : ''}
                  </span>
                ))}
                <span> · {title}</span>
              </div>
            )}
            <div className="flex items-baseline gap-2 min-w-0">
              <h1 className="text-lg font-semibold text-primary truncate">{title}</h1>
              <span className="text-xs text-muted truncate">{subtitle}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <TopicPicker state={state} />
          <EngineChips state={state} />
          <PeriodChips state={state} />
        </div>
      </div>
    </div>
  );
}

function TopicPicker({ state }: { state: ShellState }) {
  const L = useBgLang();
  if (state.topics.length === 0) {
    return <span className="text-xs text-muted">{L.noTopic}</span>;
  }
  return (
    <label className="text-xs text-secondary flex items-center gap-2">
      {L.topicPicker}
      <select
        value={state.topicId ?? ''}
        onChange={e => state.setTopicId(Number(e.target.value))}
        className="px-2 py-1 text-sm rounded"
        style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
      >
        {state.topics.map(t => (
          <option key={t.id} value={t.id}>{t.name}</option>
        ))}
      </select>
    </label>
  );
}

function EngineChips({ state }: { state: ShellState }) {
  const L = useBgLang();
  const topicEngines = state.topic?.engines ?? [];
  if (topicEngines.length === 0) return null;
  const sole = state.selectedEngines.length === 1 ? state.selectedEngines[0] : null;
  const allActive =
    state.selectedEngines.length === 0 ||
    state.selectedEngines.length >= topicEngines.length;
  const chipStyle = (active: boolean) => ({
    background: active ? 'var(--accent-primary)' : 'transparent',
    color: active ? 'white' : 'var(--text-secondary)',
  });
  return (
    <div className="flex gap-1 p-0.5 rounded flex-wrap" style={{ background: 'var(--bg-input)' }}>
      <button
        type="button"
        onClick={() => state.setSelectedEngines([])}
        className="px-3 py-1 text-xs rounded"
        style={chipStyle(allActive)}
      >
        {L.engineSelectAll}
      </button>
      {topicEngines.map(eng => (
        <button
          key={eng}
          type="button"
          onClick={() => state.setSelectedEngines([eng])}
          className="px-3 py-1 text-xs rounded"
          style={chipStyle(sole === eng)}
        >
          {engineLabel(eng)}
        </button>
      ))}
    </div>
  );
}

function PeriodChips({ state }: { state: ShellState }) {
  const L = useBgLang();
  const dayUnit = L.topicPicker === '主题' ? '天' : 'd';
  return (
    <div className="flex gap-1 p-0.5 rounded" style={{ background: 'var(--bg-input)' }}>
      {([7, 30, 90] as PeriodDays[]).map(p => (
        <button
          key={p}
          type="button"
          onClick={() => state.setPeriod(p)}
          className="px-3 py-1 text-xs rounded"
          style={{
            background: state.period === p ? 'var(--accent-primary)' : 'transparent',
            color: state.period === p ? 'white' : 'var(--text-secondary)',
          }}
        >
          {p}{dayUnit}
        </button>
      ))}
    </div>
  );
}

export function BrandGrowthShell({
  title, breadcrumb, children,
}: {
  title: string;
  breadcrumb?: { label: string; to: string }[];
  children: (state: ShellState) => React.ReactNode;
}) {
  const state = useShellState();
  return (
    <div className="min-h-[calc(100vh-4rem)]" style={{ background: 'var(--bg-primary)' }}>
      <PageHead titleKey="pageMeta.dashboard.title" descriptionKey="pageMeta.dashboard.description" />
      <BrandGrowthHeader title={title} breadcrumb={breadcrumb} state={state} />
      <ShellMain state={state}>{children}</ShellMain>
    </div>
  );
}

function ShellMain({ state, children }: {
  state: ShellState; children: (state: ShellState) => React.ReactNode;
}) {
  const L = useBgLang();
  return (
    <main className="px-6 py-6">
      <div className="max-w-[1400px] mx-auto">
        {state.loading ? (
          <div className="text-center py-20 text-muted">{L.loading}</div>
        ) : state.topics.length === 0 ? (
          <EmptyState />
        ) : (
          children(state)
        )}
      </div>
    </main>
  );
}

function EmptyState() {
  return (
    <div className="max-w-md mx-auto py-20 text-center">
      <div className="text-lg font-medium text-primary mb-2">还没有任何监测主题</div>
      <div className="text-sm text-muted mb-4">
        监测主题由管理员配置。请联系管理员为您的账户添加主题、种子提示词与扩展提示。
      </div>
    </div>
  );
}
