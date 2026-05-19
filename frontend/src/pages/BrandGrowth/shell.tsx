import { Link, useSearchParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { PageHead } from '../../components/PageHead';
import { aiTelemetryApi, type Topic } from '../../services/aiTelemetryApi';

export type PeriodDays = 7 | 30 | 90;

export interface ShellState {
  token: string;
  topics: Topic[];
  topic: Topic | null;
  topicId: number | null;
  setTopicId: (id: number) => void;
  period: PeriodDays;
  setPeriod: (p: PeriodDays) => void;
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

  return { token, topics, topic, topicId, setTopicId, setPeriod, period, loading };
}

export function BrandGrowthHeader({
  title, breadcrumb, state,
}: {
  title: string;
  breadcrumb?: { label: string; to: string }[];
  state: ShellState;
}) {
  return (
    <div className="px-6 py-5 border-b" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-card)' }}>
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div className="flex flex-col gap-1">
          {breadcrumb && breadcrumb.length > 0 && (
            <div className="text-xs text-muted">
              {breadcrumb.map((b, i) => (
                <span key={i}>
                  <Link to={b.to} className="hover:underline">{b.label}</Link>
                  {i < breadcrumb.length - 1 ? ' / ' : ''}
                </span>
              ))}
            </div>
          )}
          <h1 className="text-xl font-semibold text-primary">{title}</h1>
        </div>
        <div className="flex items-center gap-3">
          <TopicPicker state={state} />
          <PeriodChips state={state} />
        </div>
      </div>
    </div>
  );
}

function TopicPicker({ state }: { state: ShellState }) {
  if (state.topics.length === 0) {
    return <span className="text-xs text-muted">无主题</span>;
  }
  return (
    <label className="text-xs text-secondary flex items-center gap-2">
      主题
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

function PeriodChips({ state }: { state: ShellState }) {
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
          {p}天
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
      <main className="px-6 py-6">
        {state.loading ? (
          <div className="text-center py-20 text-muted">加载中…</div>
        ) : state.topics.length === 0 ? (
          <EmptyState />
        ) : (
          children(state)
        )}
      </main>
    </div>
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
