// AI telemetry workbench page — one component serving 3 routes:
//   /dashboard            views=['config']                  — topic config (new dashboard home)
//   /dashboard/ai-telemetry  views=['overview','tracking','results'] — telemetry dashboard
//   /dashboard/insights   views=['briefings']               — optimization suggestions
//
// Cadence / time / models are set by the user in the "Monitoring & batch runs" panel (components/MonitorRunPanel.tsx).
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { fmtTime } from '../../utils/datetime';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';

import { balanceApi } from '../../services/balanceApi';
import { PageHead } from '../../components/PageHead';
import { UiSelect } from '../../components/ui/Select';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { useRegisterAgentActions } from '../../components/AgentChat/agentActions';
import { TagInput } from './sentiment/components/TagInput';
import { BrandProfileForm } from '../../components/BrandProfileForm';
import { FixedDenominatorBox } from '../../components/FixedDenominatorBox';
import { ProfileImporter } from '../../components/ProfileImporter';
import { topicProfileApi } from '../../services/topicProfileApi';
import { reportsApi, type BrandTier, type IndustryClass } from '../../services/reportsApi';
import { adminReviewApi, type TopicStrategicSolution } from '../../services/adminReviewApi';
import { SolutionGeneratingBanner, SolutionView } from '../Admin/Solution';
import { TopicKpiOverviewCard } from '../../components/TopicKpiOverviewCard';
import { ExecutionPlanView } from '../Admin/ExecutionPlan';
import { AdminContentReview } from '../Admin/ContentReview';
import { Content } from './Content';
import { exportSolutionPdf } from '../../utils/exportSolutionPdf';
import { exportSolutionDocx } from '../../utils/exportSolutionDocx';
import {
  QUERY_CSV_ACCEPT, downloadTextFile, parseQueryCsv, queryCsvFilename, serializeQueryCsv,
} from '../../utils/queryCsv';
import {
  aiTelemetryApi, CN_ENGINES, EMPTY_BRAND_PROFILE, MAX_SELECTED_QUERIES,
  type BrandProfile,
  type EngineId, type Topic, type TopicPayload,
  type RunSummary, type ResponseRow, type Overview, type DomainCount,
  type IntentBreakdown,
  type OwnedSplit,
  type TrackingMatrix, type QueryHitCell, type EngineFirstHit,
  type CellDrawer, type CellInsight, type Briefing, type ShareOfVoice,
  type QueryCandidate,
  type SeedPrompt,
  type TopicProgress,
  type SceneType,
  type MatchedProduct,
} from '../../services/aiTelemetryApi';
import { TopicProgressStrip } from '../../components/TopicProgressStrip';
import { MarkSeedAsProduct } from '../../components/MarkSeedAsProduct';
import { MonitorRunPanel } from '../../components/MonitorRunPanel';

// 2026-05-28 — display metadata for the 4-dimension scenario tags (label via i18n, color inlined to avoid cross-theme bleed)
const SCENE_COLORS: Record<SceneType, string> = {
  search: '#6366f1',   // blue-violet
  qa: '#10b981',       // green
  intent: '#f59e0b',   // yellow
  brand: '#ec4899',    // pink
};
const SCENE_KEYS: SceneType[] = ['search', 'qa', 'intent', 'brand'];

// Brand basic queries — pure frontend template concatenation, generated instantly without an LLM: brand name × a fixed set of review/info suffixes.
// High-frequency generic queries about the brand itself, easiest for owned content to cover, done in one batch (modeled on competitors' bulk 「XX怎么样/口碑/实力」 ("how is XX / reputation / strength") queries).
// Shown under the brand scenario; no new dimension added.
const BRAND_BASIC_SUFFIXES = [
  '怎么样', '好不好', '口碑', '口碑怎么样', '评价如何', '客户评价如何', '用户评价怎么样',
  '实力怎么样', '有实力吗', '专业吗', '正规吗', '可信吗', '值得信任吗', '靠谱吗',
  '性价比怎么样', '服务怎么样', '产品怎么样', '产品好用吗', '满意度怎么样',
  '介绍', '详细介绍', '公司概况', '基本信息', '是做什么的',
];
function buildBrandBasicQueries(brand: string): string[] {
  const b = brand.trim();
  if (!b) return [];
  return [b, ...BRAND_BASIC_SUFFIXES.map(s => `${b}${s}`)];
}

import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, Legend,
  AreaChart, Area, PieChart, Pie, Cell,
} from 'recharts';
import { Tooltip as HintTooltip } from '../../components/Tooltip';
import { engineLabel } from '../../constants/engines';

type TabKey = 'today' | 'overview' | 'tracking' | 'briefings' | 'config' | 'results';

const ENGINE_COLORS: Record<EngineId, string> = {
  deepseek: '#5B6CFF',
  qwen: '#7E57C2', doubao: '#FF7043', wenxin: '#26A69A',
  yuanbao: '#EF5350', kimi: '#111111',
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

// "Active" badge for seed queries / prompts.
//
// Since 2026-09-09 neither seed nor expanded queries are **reviewed anymore** (see backend api/ai_telemetry.update_topic):
// whoever adds them, they are approved directly and join batch runs once persisted -- whether they run depends only on selected / deleted_at, not status
// (see models.active_query_texts). So pending / rejected are two dead states that can never render;
// drawing a "pending review" would only make users think the query is held back, when it actually runs that day and has already consumed credits.
//
// The badge itself stays: what users want confirmed is "does this run or not", not a review verdict -- so it always shows "Active"
// and no longer reads status.
function ReviewBadge() {
  const { t } = useTranslation();
  return (
    <span
      className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ color: '#10b981', background: 'var(--success-bg)' }}
    >
      ✓
      {t('dashboard.aiTelemetry.form.badgeApproved')}
    </span>
  );
}

const ALL_TABS: TabKey[] = ['today', 'overview', 'tracking', 'briefings', 'config', 'results'];

export function AiTelemetry({ views }: { views?: TabKey[] } = {}) {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const visibleTabs = (views && views.length > 0 ? views : ALL_TABS);
  const showTabBar = visibleTabs.length > 1;
  const showNewTopicBtn = visibleTabs.includes('config');
  const [tab, setTab] = useState<TabKey>(visibleTabs[0]);
  // clamp: if the component is reused (stale state), pull the tab back into the range allowed by the current view
  const currentTab: TabKey = visibleTabs.includes(tab) ? tab : visibleTabs[0];
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(false);
  // undefined = modal closed; null = creating; Topic = editing
  const [editing, setEditing] = useState<Topic | null | undefined>(undefined);
  // Mode when entering the editor: edit (writable) / view (read-only)
  const [editorMode, setEditorMode] = useState<'edit' | 'view'>('edit');
  // Topic selection shared by overview / citation tracking / audit details — kept across tab switches
  const [sharedTopicId, setSharedTopicId] = useState<number | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const list = await aiTelemetryApi.listTopics(token);
      setTopics(list);
      // When topics are first fetched, store the default selected topic in shared state
      setSharedTopicId(prev => (prev !== null ? prev : list[0]?.id ?? null));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const handleSave = async (payload: TopicPayload): Promise<Topic> => {
    const saved = editing && editing.id
      ? await aiTelemetryApi.updateTopic(editing.id, payload, token)
      : await aiTelemetryApi.createTopic(payload, token);
    return saved;
  };

  const handleSaveDone = () => {
    setEditing(undefined);
    refresh();
  };

  const handleToggleEnabled = async (tp: Topic) => {
    // Backend PUT /topics/{id} requires the full payload — rebuild it from the current topic data
    const payload: TopicPayload = {
      name: tp.name,
      target: tp.target,
      target_aliases: tp.target_aliases,
      industry: tp.industry || '',
      queries: tp.queries,
      query_cluster_ids: tp.query_cluster_ids,
      clusters: tp.clusters,
      engines: tp.engines,
      enabled: !tp.enabled,
    };
    await aiTelemetryApi.updateTopic(tp.id, payload, token);
    refresh();
  };

  // Edit / create mode: replace the whole page with the inline editor; only then is there room to choose among 200+ candidates
  if (editing !== undefined) {
    return (
      <div className="space-y-4">
        <PageHead titleKey="dashboard.aiTelemetry.title" titleFallback="AI Telemetry" />
        <header className="flex items-center justify-between gap-4">
          <h1 className="text-xl font-semibold text-primary leading-tight">
            {t(
              editorMode === 'view'
                ? 'dashboard.aiTelemetry.viewTopic'
                : editing
                  ? 'dashboard.aiTelemetry.editTopic'
                  : 'dashboard.aiTelemetry.newTopic',
            )}
          </h1>
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
        </header>
        <TopicEditor
          initial={editing}
          token={token}
          mode={editorMode}
          onCancel={() => setEditing(undefined)}
          onSave={handleSave}
          onSaveDone={handleSaveDone}
        />
      </div>
    );
  }

  // Title / subtitle: same source as the sidebar menu (dashboard.nav.*); subtitle is per view
  const headTitleKey =
    visibleTabs.length === 1 && visibleTabs[0] === 'config' ? 'dashboard.nav.config' :
    visibleTabs.length === 1 && visibleTabs[0] === 'briefings' ? 'dashboard.nav.insights' :
    'dashboard.nav.aiTelemetry';
  const subtitleKey =
    visibleTabs.length === 1 && visibleTabs[0] === 'config' ? 'dashboard.config.subtitle' :
    visibleTabs.length === 1 && visibleTabs[0] === 'briefings' ? 'dashboard.insights.subtitle' :
    'dashboard.aiTelemetry.subtitle';

  return (
    <div className="space-y-4">
      <PageHead titleKey={headTitleKey} titleFallback="AI Telemetry" />

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
              {t(headTitleKey)}
            </h1>
            <p className="text-xs text-secondary mt-0.5">{t(subtitleKey)}</p>
          </div>
        </div>
        {showNewTopicBtn && (
          <button
            type="button"
            onClick={() => { setEditorMode('edit'); setEditing(null); }}
            className="px-3 py-1.5 text-sm rounded-md text-white shadow-sm hover:opacity-90 transition-opacity"
            style={{ background: 'var(--accent-primary)' }}
          >
            + {t('dashboard.aiTelemetry.newTopic')}
          </button>
        )}
      </header>

      {showTabBar && (
        <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
          {visibleTabs.map(k => (
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
      )}

      {currentTab ==='today' && (
        <TodayTab topics={topics} token={token}
          topicId={sharedTopicId} onTopicChange={setSharedTopicId} />
      )}
      {currentTab ==='overview' && (
        <OverviewTab topics={topics} token={token}
          topicId={sharedTopicId} onTopicChange={setSharedTopicId} />
      )}
      {currentTab ==='tracking' && (
        <TrackingTab topics={topics} token={token}
          topicId={sharedTopicId} onTopicChange={setSharedTopicId} />
      )}
      {currentTab ==='briefings' && <BriefingsTab topics={topics} token={token} />}
      {currentTab ==='config' && (
        <TopicTable
          topics={topics} loading={loading}
          onView={(tp) => { setEditorMode('view'); setEditing(tp); }}
          onEdit={(tp) => { setEditorMode('edit'); setEditing(tp); }}
          onToggleEnabled={handleToggleEnabled}
        />
      )}
      {currentTab ==='results' && (
        <ResultsTab topics={topics} token={token}
          topicId={sharedTopicId} onTopicChange={setSharedTopicId} />
      )}
    </div>
  );
}

// ── Topic card grid (each topic rendered as its own card, responsive 2/3 columns) ──────────────

function TopicTable({
  topics, loading, onView, onEdit, onToggleEnabled,
}: {
  topics: Topic[]; loading: boolean;
  onView: (t: Topic) => void;
  onEdit: (t: Topic) => void;
  onToggleEnabled: (t: Topic) => void;
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

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {topics.map(tp => (
        <TopicBlock key={tp.id} topic={tp}
                    onView={onView} onEdit={onEdit} onToggleEnabled={onToggleEnabled} />
      ))}
    </div>
  );
}

function TopicBlock({
  topic: tp, onView, onEdit, onToggleEnabled,
}: {
  topic: Topic;
  onView: (t: Topic) => void;
  onEdit: (t: Topic) => void;
  onToggleEnabled: (t: Topic) => void;
}) {
  const { t } = useTranslation();
  const s = deriveTopicStatus(tp);
  const stat = TOPIC_STATUS_STYLE[s];
  return (
    <section className="rounded-lg p-4 flex flex-col gap-3"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-semibold text-primary truncate flex-1 min-w-0" title={tp.name}>
          {tp.name}
        </h3>
        <span className="inline-block px-2 py-0.5 rounded text-xs shrink-0"
              style={{ background: stat.bg, color: stat.fg,
                       border: '1px solid var(--border-color)' }}>
          {t(`dashboard.aiTelemetry.statuses.${s}`)}
        </span>
      </div>

      {typeof tp.version === 'number' && tp.version > 1 && (
        <span className="text-[10px] px-1.5 py-0.5 rounded-full tabular-nums self-start"
              style={{ background: 'var(--bg-tertiary)', color: 'var(--text-muted)' }}
              title={`第 ${tp.version} 次修订(每次编辑自增)`}>
          v{tp.version}
        </span>
      )}

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-secondary">
        <span>
          <span className="text-muted">{t('dashboard.aiTelemetry.col.queries')}:</span>{' '}
          <span className="text-primary tabular-nums">{tp.queries.length}</span>
        </span>
        <span>
          <span className="text-muted">{t('dashboard.aiTelemetry.col.engines')}:</span>{' '}
          <span className="text-primary tabular-nums">{tp.engines.length}/10</span>
        </span>
      </div>

      <div className="flex items-center gap-2 mt-auto pt-1 flex-wrap">
        <button type="button" onClick={() => onView(tp)}
                className="text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
                         border: '1px solid var(--border-color)' }}>
          {t('dashboard.aiTelemetry.actions.view')}
        </button>
        <button type="button" onClick={() => onEdit(tp)}
                className="text-xs px-3 py-1.5 rounded-md text-white"
                style={{ background: 'var(--accent-primary)' }}>
          {t('dashboard.aiTelemetry.actions.edit')}
        </button>
        <button type="button" onClick={() => onToggleEnabled(tp)}
                className="text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'transparent',
                         color: tp.enabled ? 'var(--text-muted)' : 'var(--accent-primary)',
                         border: '1px solid var(--border-color)' }}>
          {tp.enabled
            ? t('dashboard.aiTelemetry.actions.disable')
            : t('dashboard.aiTelemetry.actions.enable')}
        </button>
      </div>
    </section>
  );
}

type TopicStatus = 'incomplete' | 'draft' | 'reviewing' | 'rejected' | 'enabled' | 'disabled';

function deriveTopicStatus(tp: Topic): TopicStatus {
  // submission_status is the primary review truth; the old logic (checking seed_prompts.pending) would show "draft but seeds pending"
  // wrongly as "under review", so users thought they had submitted, but admins querying submission_status='pending' couldn't see it.
  const sub = tp.submission_status;
  if (sub === 'pending') return 'reviewing';
  if (sub === 'rejected') return 'rejected';
  if (sub === 'approved') return tp.enabled === false ? 'disabled' : 'enabled';
  // draft or undefined. Skeleton drafts where step1 "Next" only saved materials and no prompts are configured yet (queries empty)
  // are labeled "Draft" separately, distinguished from configured editable topics, to make abandoned half-finished ones easy to spot / clean up.
  if ((tp.queries?.length ?? 0) === 0) return 'incomplete';
  if (!tp.enabled) return 'disabled';
  return 'draft';
}

const TOPIC_STATUS_STYLE: Record<TopicStatus, { bg: string; fg: string }> = {
  incomplete:{ bg: 'rgba(148,163,184,0.18)', fg: '#64748b' },
  draft:    { bg: 'rgba(59,130,246,0.15)', fg: '#3b82f6' },
  reviewing:{ bg: 'rgba(234,179,8,0.15)',  fg: '#b45309' },
  rejected: { bg: 'var(--danger-bg)',  fg: '#dc2626' },
  enabled:  { bg: 'var(--success-bg)',  fg: '#16a34a' },
  disabled: { bg: 'var(--bg-input)',       fg: 'var(--text-muted)' },
};

function formatTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

// ── Today ───────────────────────────────────────────────────────

function TodayTab({ topics, token, topicId, onTopicChange }: {
  topics: Topic[]; token: string;
  topicId: number | null; onTopicChange: (id: number | null) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const setTopicId = onTopicChange;
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [responsesByRun, setResponsesByRun] = useState<Record<number, ResponseRow[]>>({});
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<TopicProgress | null>(null);

  useEffect(() => {
    if (topicId === null && topics.length > 0) setTopicId(topics[0].id);
  }, [topics, topicId, setTopicId]);

  const load = async (tid: number) => {
    setLoading(true);
    try {
      const rs = await aiTelemetryApi.listRuns(tid, token);
      setRuns(rs);
      // Load responses of today's + yesterday's runs (yesterday is used to compute the citations delta)
      const dayStart = startOfDay(new Date()).getTime();
      const need = rs.filter(r => {
        const ts = parseApiTs(r.started_at).getTime();
        return ts >= dayStart - 86400000 && ts < dayStart + 86400000;
      });
      const pairs = await Promise.all(need.map(async r => {
        try { return [r.id, await aiTelemetryApi.listResponses(r.id, token)] as const; }
        catch { return [r.id, [] as ResponseRow[]] as const; }
      }));
      const map: Record<number, ResponseRow[]> = {};
      for (const [id, rows] of pairs) map[id] = rows;
      setResponsesByRun(map);
      // Status bar: which step we're at and what's next. If unavailable, hide it without blocking the rest of the page.
      try { setProgress(await aiTelemetryApi.getProgress(tid, token)); }
      catch { setProgress(null); }
    } finally { setLoading(false); }
  };

  useEffect(() => {
    if (topicId === null) return;
    load(topicId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topicId, token]);

  if (topics.length === 0) {
    return <EmptyHint text={t('dashboard.aiTelemetry.today.noTopics')} />;
  }

  const topic = topics.find(tp => tp.id === topicId) ?? null;
  const dayStart = startOfDay(new Date()).getTime();
  const todayRuns = runs.filter(r => parseApiTs(r.started_at).getTime() >= dayStart);
  const yesterdayRuns = runs.filter(r => {
    const ts = parseApiTs(r.started_at).getTime();
    return ts >= dayStart - 86400000 && ts < dayStart;
  });
  const todayResponses = todayRuns.flatMap(r => responsesByRun[r.id] ?? []);
  const yesterdayResponses = yesterdayRuns.flatMap(r => responsesByRun[r.id] ?? []);

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg-input)', border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  };

  // "Audit now" used to kick off a full run right here. Since 2026-09-21 batch runs are triggered only from the "Monitoring & batch runs" panel
  // (topic settings page -> top of the prompts tab); this just takes the user there -- with three entry points each running separately,
  // users couldn't tell which click would charge them. The ops console embed path is topics/:id/profile; navigate by the current prefix.
  const openMonitorPanel = () => {
    if (topicId === null) return;
    const base = location.pathname.startsWith('/ops') ? '/ops' : '/dashboard';
    navigate(`${base}/topics/${topicId}/profile?tab=monitor`);
  };

  return (
    <div className="space-y-4">
      {/* Top: topic picker + audit now */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="text-xs text-secondary">
          {t('dashboard.aiTelemetry.today.selectTopic')}
          <UiSelect
            value={String(topicId ?? '')} onChange={val => setTopicId(Number(val))}
            className="ml-2 px-2 py-1 text-sm rounded" style={inputStyle}
            options={topics.map(tp => ({ value: String(tp.id), label: tp.name }))}
          />
        </label>
        <button
          type="button"
          onClick={openMonitorPanel}
          disabled={topicId === null}
          className="px-3 py-1.5 text-xs rounded-md text-white disabled:opacity-50"
          style={{ background: 'var(--accent-primary)' }}
        >
          监测与跑批 →
        </button>
      </div>

      {progress && <TopicProgressStrip p={progress} />}

      {loading && <div className="text-sm text-muted py-6 text-center">…</div>}

      {!loading && todayRuns.length === 0 && (
        <EmptyHint text={t('dashboard.aiTelemetry.today.noRunToday')} />
      )}

      {!loading && todayRuns.length > 0 && topic && (
        <TodayBody
          topic={topic}
          todayRuns={todayRuns}
          todayResponses={todayResponses}
          yesterdayResponses={yesterdayResponses}
        />
      )}
    </div>
  );
}


function startOfDay(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

// Backend datetime.utcnow() serializes as naive ISO (no Z); JS parses it in local time by default and is off by 8h —
// explicitly append Z here so it parses as UTC, leaving localization to toLocaleString and the like
function parseApiTs(s: string): Date {
  if (!s) return new Date(NaN);
  return new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z');
}

function TodayBody({ topic, todayRuns, todayResponses, yesterdayResponses }: {
  topic: Topic;
  todayRuns: RunSummary[];
  todayResponses: ResponseRow[];
  yesterdayResponses: ResponseRow[];
}) {
  const { t } = useTranslation();
  const enginesAll = topic.engines.length > 0 ? topic.engines : CN_ENGINES;
  const queriesAll = topic.queries;

  // Per-engine status: done (has success) / failed (errors only) / pending (not back yet)
  const engineStatus = useMemo(() => {
    const map: Record<string, 'done' | 'failed' | 'pending'> = {};
    const anyRunning = todayRuns.some(r => r.status === 'running');
    for (const e of enginesAll) {
      const rows = todayResponses.filter(r => r.engine === e);
      const ok = rows.filter(r => !r.error).length;
      const err = rows.filter(r => r.error).length;
      if (ok > 0) map[e] = 'done';
      else if (err > 0) map[e] = 'failed';
      else map[e] = anyRunning ? 'pending' : 'failed';
    }
    return map;
  }, [enginesAll, todayResponses, todayRuns]);

  const enginesDone = enginesAll.filter(e => engineStatus[e] === 'done').length;
  const enginesFailed = enginesAll.filter(e => engineStatus[e] === 'failed');
  const enginesRunning = enginesAll.filter(e => engineStatus[e] === 'pending').length;
  const allDone = enginesDone === enginesAll.length;
  const elapsedSecs = (() => {
    const finished = todayRuns.filter(r => r.finished_at);
    if (finished.length === 0) return 0;
    const start = Math.min(...todayRuns.map(r => parseApiTs(r.started_at).getTime()));
    const end = Math.max(...finished.map(r => parseApiTs(r.finished_at!).getTime()));
    return Math.max(1, Math.round((end - start) / 1000));
  })();

  // KPIs
  const okResponses = todayResponses.filter(r => !r.error);
  const hits = okResponses.filter(r => r.hit === true).length;
  const mentionRate = okResponses.length > 0 ? Math.round(hits / okResponses.length * 100) : 0;
  const citationsToday = okResponses.reduce((s, r) => s + r.citations.length, 0);
  const citationsYesterday = yesterdayResponses
    .filter(r => !r.error)
    .reduce((s, r) => s + r.citations.length, 0);
  const citationsDelta = citationsToday - citationsYesterday;

  // Matrix: queries × engines
  const cellMap = useMemo(() => {
    const m: Record<string, ResponseRow[]> = {};
    for (const r of todayResponses) {
      const k = `${r.query}|${r.engine}`;
      (m[k] = m[k] || []).push(r);
    }
    return m;
  }, [todayResponses]);
  const cellState = (q: string, e: string): 'hit' | 'miss' | 'err' | 'none' => {
    const rows = cellMap[`${q}|${e}`] || [];
    if (rows.length === 0) return 'none';
    if (rows.some(r => r.hit === true)) return 'hit';
    if (rows.some(r => !r.error && r.hit === false)) return 'miss';
    if (rows.some(r => r.error)) return 'err';
    return 'none';
  };
  const queryHitCount = (q: string) => {
    let hit = 0, ran = 0;
    for (const e of enginesAll) {
      const s = cellState(q, e);
      if (s === 'hit') { hit++; ran++; }
      else if (s === 'miss' || s === 'err') ran++;
    }
    return { hit, ran };
  };

  // Citations added today
  const citedDomains = useMemo(() => {
    const map = new Map<string, { url: string; title: string; domain: string; count: number; engines: Set<string> }>();
    for (const r of okResponses) {
      for (const c of r.citations) {
        const k = c.url;
        const cur = map.get(k);
        if (cur) { cur.count++; cur.engines.add(r.engine); }
        else map.set(k, { url: c.url, title: c.title, domain: c.domain, count: 1, engines: new Set([r.engine]) });
      }
    }
    return Array.from(map.values()).sort((a, b) => b.count - a.count);
  }, [okResponses]);

  // Blind spots: queries not mentioned by any engine today
  const blindQueries = queriesAll.filter(q => {
    const { hit, ran } = queryHitCount(q);
    return ran > 0 && hit === 0;
  });

  const cardBg: React.CSSProperties = {
    background: 'var(--bg-card)', border: '1px solid var(--border-color)',
  };

  return (
    <div className="space-y-4">
      {/* Status bar */}
      <div className="px-4 py-2.5 rounded-lg flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" style={cardBg}>
        <span className="text-secondary">{t('dashboard.aiTelemetry.today.runStatus')}:</span>
        {allDone
          ? <span className="text-emerald-500">
              ● {t('dashboard.aiTelemetry.today.runStatusAllDone', { total: enginesAll.length, secs: elapsedSecs })}
            </span>
          : <span className="text-blue-500">
              ● {t('dashboard.aiTelemetry.today.runStatusRunning', {
                  done: enginesDone, total: enginesAll.length, running: enginesRunning,
                })}
            </span>}
        {enginesFailed.length > 0 && (
          <span className="text-rose-500">
            ⚠ {t('dashboard.aiTelemetry.today.runStatusFailed', { failed: enginesFailed.length })}
          </span>
        )}
        <span className="text-muted ml-auto">
          {t('dashboard.aiTelemetry.today.runStartedAt', {
            time: new Date(Math.min(...todayRuns.map(r => parseApiTs(r.started_at).getTime()))).toLocaleTimeString(),
          })}
        </span>
      </div>

      {/* 4 KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <TodayKpiCard
          label={t('dashboard.aiTelemetry.today.kpiQueries')}
          value={queriesAll.length}
          sub={t('dashboard.aiTelemetry.today.kpiQueriesUnit', {
            engines: enginesAll.length,
            calls: queriesAll.length * enginesAll.length,
          })}
        />
        <TodayKpiCard
          label={t('dashboard.aiTelemetry.today.kpiMentionRate')}
          value={`${mentionRate}%`}
          sub={t('dashboard.aiTelemetry.today.kpiMentionRateUnit', { hits, total: okResponses.length })}
          hint={t('dashboard.aiTelemetry.today.tipMentionRate')}
        />
        <TodayKpiCard
          label={t('dashboard.aiTelemetry.today.kpiCitations')}
          value={citationsToday}
          sub={citationsDelta > 0
            ? t('dashboard.aiTelemetry.today.kpiCitationsDeltaUp', { n: citationsDelta })
            : citationsDelta < 0
              ? t('dashboard.aiTelemetry.today.kpiCitationsDeltaDown', { n: citationsDelta })
              : t('dashboard.aiTelemetry.today.kpiCitationsDeltaSame')}
          subColor={citationsDelta > 0 ? '#10b981' : citationsDelta < 0 ? '#ef4444' : undefined}
          hint={t('dashboard.aiTelemetry.today.tipCitations')}
        />
        <TodayKpiCard
          label={t('dashboard.aiTelemetry.today.kpiEngines')}
          value={t('dashboard.aiTelemetry.today.kpiEnginesUnit', { done: enginesDone, total: enginesAll.length })}
          sub={enginesFailed.length > 0
            ? t('dashboard.aiTelemetry.today.kpiEnginesFailed', {
                names: enginesFailed.map(e => engineName(e, t)).join(' / '),
              })
            : ''}
          subColor={enginesFailed.length > 0 ? '#ef4444' : undefined}
          hint={t('dashboard.aiTelemetry.today.tipEngines')}
        />
      </div>

      {/* Hit matrix */}
      <div className="rounded-lg overflow-hidden" style={cardBg}>
        <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border-color)' }}>
          <div className="text-sm font-medium text-primary">
            {t('dashboard.aiTelemetry.today.matrixTitle')}
          </div>
          <div className="text-xs text-muted mt-0.5">
            {t('dashboard.aiTelemetry.today.matrixHint')}
          </div>
        </div>
        {queriesAll.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted">
            {t('dashboard.aiTelemetry.today.matrixEmpty')}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                  <th className="text-left px-3 py-2 font-medium sticky left-0 z-10"
                      style={{ background: 'var(--bg-secondary)', minWidth: 240 }}>
                    {t('dashboard.aiTelemetry.today.matrixColQuery')}
                  </th>
                  {enginesAll.map(e => (
                    <th key={e} className="text-center px-2 py-2 font-medium">
                      <div>{engineName(e, t)}</div>
                      <EngineStatusDot status={engineStatus[e]} />
                    </th>
                  ))}
                  <th className="text-center px-3 py-2 font-medium">
                    {t('dashboard.aiTelemetry.today.matrixColHits')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {queriesAll.map((q, i) => {
                  const { hit, ran } = queryHitCount(q);
                  return (
                    <tr key={i} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td className="px-3 py-2 text-secondary sticky left-0"
                          style={{ background: 'var(--bg-card)', maxWidth: 320 }}
                          title={q}>
                        <span className="line-clamp-2">{q}</span>
                      </td>
                      {enginesAll.map(e => (
                        <td key={e} className="text-center px-2 py-2">
                          <CellGlyph state={cellState(q, e)} />
                        </td>
                      ))}
                      <td className="text-center px-3 py-2 text-secondary tabular-nums">
                        {ran === 0
                          ? '—'
                          : t('dashboard.aiTelemetry.today.matrixRowHitRate', { hits: hit, total: ran })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Citations added today + blind spots */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg" style={cardBg}>
          <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border-color)' }}>
            <div className="text-sm font-medium text-primary">
              {t('dashboard.aiTelemetry.today.citedTitle')}
            </div>
            <div className="text-xs text-muted mt-0.5">
              {t('dashboard.aiTelemetry.today.citedHint')}
            </div>
          </div>
          {citedDomains.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted">
              {t('dashboard.aiTelemetry.today.citedEmpty')}
            </div>
          ) : (
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                    <th className="text-left px-3 py-2 font-medium w-8">
                      {t('dashboard.aiTelemetry.today.citedColRank')}
                    </th>
                    <th className="text-left px-3 py-2 font-medium">
                      {t('dashboard.aiTelemetry.today.citedColTitle')}
                    </th>
                    <th className="text-left px-3 py-2 font-medium">
                      {t('dashboard.aiTelemetry.today.citedColEngines')}
                    </th>
                    <th className="text-right px-3 py-2 font-medium w-16">
                      {t('dashboard.aiTelemetry.today.citedColCount')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {citedDomains.map((c, i) => (
                    <tr key={c.url} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td className="px-3 py-2 text-muted tabular-nums">{i + 1}</td>
                      <td className="px-3 py-2">
                        <a href={c.url} target="_blank" rel="noreferrer"
                           className="hover:underline block truncate max-w-[18rem]"
                           style={{ color: 'var(--accent-primary)' }}
                           title={c.title || c.url}>
                          {c.title || c.url}
                        </a>
                        <div className="text-[10px] text-muted">{c.domain}</div>
                      </td>
                      <td className="px-3 py-2 text-secondary">
                        {Array.from(c.engines).map(e => engineName(e, t)).join(', ')}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-primary">{c.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="rounded-lg" style={cardBg}>
          <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border-color)' }}>
            <div className="text-sm font-medium text-primary">
              {t('dashboard.aiTelemetry.today.blindTitle')}
            </div>
            <div className="text-xs text-muted mt-0.5">
              {t('dashboard.aiTelemetry.today.blindHint')}
            </div>
          </div>
          {blindQueries.length === 0 ? (
            <div className="py-8 text-center text-sm text-emerald-500">
              ✓ {t('dashboard.aiTelemetry.today.blindEmpty')}
            </div>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {blindQueries.map((q, i) => (
                <li key={i}
                    className="px-4 py-2 text-sm text-primary"
                    style={{ borderTop: i === 0 ? 'none' : '1px solid var(--border-color)' }}>
                  <span className="text-rose-500 mr-2">✕</span>{q}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// Engine display names all come from the single authoritative source; the second parameter is kept only for compatibility with existing call-site signatures.
function engineName(e: string, _t?: ReturnType<typeof useTranslation>['t']): string {
  return engineLabel(e);
}

function TodayKpiCard({ label, value, sub, subColor, hint }: {
  label: string; value: number | string; sub?: string; subColor?: string; hint?: string;
}) {
  return (
    <div className="rounded-lg px-4 py-3"
         style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="text-xs text-secondary flex items-center gap-1">
        {label}{hint && <InfoHint text={hint} />}
      </div>
      <div className="text-xl font-semibold text-primary mt-1 tabular-nums">{value}</div>
      {sub && (
        <div className="text-[11px] mt-0.5" style={{ color: subColor || 'var(--text-muted)' }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function CellGlyph({ state }: { state: 'hit' | 'miss' | 'err' | 'none' }) {
  if (state === 'hit') return <span className="inline-block w-5 h-5 rounded-full text-emerald-500" title="hit">✓</span>;
  return <span className="inline-block w-5 h-5 text-muted" title="not mentioned">✕</span>;
}

function EngineStatusDot({ status }: { status?: 'done' | 'failed' | 'pending' }) {
  const color = status === 'done' ? '#10b981'
    : status === 'failed' ? '#ef4444'
    : '#f59e0b';
  return (
    <span className="inline-block w-1.5 h-1.5 rounded-full mt-0.5" style={{ background: color }} />
  );
}

// ── Overview ───────────────────────────────────────────────────────

function OverviewTab({ topics, token, topicId, onTopicChange }: {
  topics: Topic[]; token: string;
  topicId: number | null; onTopicChange: (id: number | null) => void;
}) {
  const { t } = useTranslation();
  const setTopicId = onTopicChange;
  const [period, setPeriod] = useState<7 | 30 | 90>(30);
  const [data, setData] = useState<Overview | null>(null);
  const [sov, setSoV] = useState<ShareOfVoice | null>(null);
  const [intent, setIntent] = useState<IntentBreakdown | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (topicId === null && topics.length > 0) setTopicId(topics[0].id);
  }, [topics, topicId, setTopicId]);

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
      {/* Top control row */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="text-xs text-secondary">
          {t('dashboard.aiTelemetry.overview.selectTopic')}
          <UiSelect
            value={String(topicId ?? '')} onChange={val => setTopicId(Number(val))}
            className="ml-2 px-2 py-1 text-sm rounded" style={inputStyle}
            options={topics.map(tp => ({ value: String(tp.id), label: tp.name }))}
          />
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

      {/* 4 KPI cards */}
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

      {/* v1.3 — SAIV share of voice + competitor citation share gap */}
      <ShareOfVoiceBlock data={sov} loading={loading} />

      {/* Trend chart */}
      <TrendChart data={data} loading={loading} />

      {/* intent distribution (clusters aggregated on the picker side × this period's mention rate) */}
      <IntentBreakdownBlock data={intent} loading={loading} />

      {/* Citation analysis: Top 10 + Owned vs Other */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <TopDomainsBlock data={data?.top_domains ?? []} loading={loading} />
        </div>
        <OwnedSplitBlock data={data?.owned_split} loading={loading} />
      </div>

      {/* Engine × platform heatmap */}
      <EngineDomainMatrix
        engines={data?.engines ?? []}
        topDomains={data?.top_domains ?? []}
        matrix={data?.engine_domain_matrix ?? {}}
        loading={loading}
      />
    </div>
  );
}

// ── v1.3 SAIV share of voice + competitor citation share gap + hit position distribution ───────────

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
      {/* SAIV KPI card */}
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
        {/* Hit position distribution (simplified retrieval ranking) */}
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

      {/* Competitor share comparison */}
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
            {/* Sort and show the brand itself together with competitors */}
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
    { key: 'lead', color: 'var(--success-fg)', n: dist.lead },     // green: opening
    { key: 'body', color: 'var(--info-fg)', n: dist.body },     // blue: middle
    { key: 'tail', color: 'var(--warn-fg)', n: dist.tail },     // yellow: end
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

// ── intent distribution ────────────────────────────────────────────

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

  // One row shows every cluster with responses + the fallback bucket. Sorted by mention_rate ascending, weak clusters first -> content reinforcement targets
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


// ── Citation analysis: Top 10 ────────────────────────────────────────────

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

// ── Citation analysis: own vs. others ─────────────────────────────────────

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
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
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

// ── Citation analysis: engine × platform heatmap ──────────────────────────────

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
    // 5-step quantized color scale, alpha spanning 0.30 - 0.95, clear contrast
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
                        {engineLabel(e)}
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
  /** Color of the big number: green for positive value, red for negative */
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
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
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

  // Sparse X axis: show all when total points ≤ 14; otherwise show every Nth, targeting 8-10 ticks
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
              {engineLabel(e)}
            </span>
          ))}
        </div>
      </div>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <LineChart data={chartData} margin={{ top: 8, right: 24, left: 0, bottom: 4 }}>
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
              width={40}
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

// ── Batch run results ───────────────────────────────────────────────────

function ResultsTab({ topics, token, topicId, onTopicChange }: {
  topics: Topic[]; token: string;
  topicId: number | null; onTopicChange: (id: number | null) => void;
}) {
  const { t } = useTranslation();
  const setTopicId = onTopicChange;
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [responses, setResponses] = useState<ResponseRow[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [loadingResp, setLoadingResp] = useState(false);

  useEffect(() => {
    if (topicId === null && topics.length > 0) setTopicId(topics[0].id);
  }, [topics, topicId, setTopicId]);

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
          <UiSelect
            value={String(topicId ?? '')} onChange={val => setTopicId(Number(val))}
            className="ml-2 px-2 py-1 text-sm rounded" style={inputStyle}
            options={topics.map(tp => ({ value: String(tp.id), label: tp.name }))}
          />
        </label>
        <label className="text-xs text-secondary">
          {t('dashboard.aiTelemetry.results.selectRun')}
          <UiSelect
            value={String(runId ?? '')} onChange={val => setRunId(Number(val))}
            disabled={runs.length === 0}
            className="ml-2 px-2 py-1 text-sm rounded disabled:opacity-50" style={inputStyle}
            options={runs.map(r => ({
              value: String(r.id),
              label: `#${r.id} ${r.status} · ${formatTime(r.started_at)} · ${r.response_count} 条`,
            }))}
          />
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
            <th className="text-left px-3 py-2 font-medium">{t('dashboard.aiTelemetry.results.colEngine')}</th>
            <th className="text-left px-3 py-2 font-medium">{t('dashboard.aiTelemetry.results.colQuery')}</th>
            <th className="text-left px-3 py-2 font-medium">{t('dashboard.aiTelemetry.results.colAnswer')}</th>
            <th className="text-left px-3 py-2 font-medium">{t('dashboard.aiTelemetry.results.colCitations')}</th>
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
                </tr>
                {open && (
                  <tr style={{ borderTop: '1px solid var(--border-color)', background: 'var(--bg-secondary)' }}>
                    <td colSpan={5} className="px-4 py-3">
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
  useTranslation();  // Subscribe to language changes to trigger re-render
  return <span>{engineLabel(id)}</span>;
}

function ResponseDetail({ row }: { row: ResponseRow }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 text-sm">
      {row.reasoning ? (
        // Chain of thought: shown collapsed, not mixed into the body; when the hit is only in the reasoning, hit_excerpt carries the 「[思考过程]」 ("[reasoning]") prefix
        <details className="rounded px-2 py-1" style={{ border: '1px dashed var(--border-color)' }}>
          <summary className="text-xs text-muted cursor-pointer select-none">
            {t('dashboard.aiTelemetry.results.reasoning')} ({row.reasoning.length})
          </summary>
          <div className="mt-1 text-secondary whitespace-pre-wrap leading-relaxed text-xs">
            {row.reasoning}
          </div>
        </details>
      ) : null}
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

// ── Create/edit modal ───────────────────────────────────────────

interface TopicEditorProps {
  initial: Topic | null;
  token: string;
  mode?: 'edit' | 'view';
  onCancel: () => void;
  onSave: (payload: TopicPayload) => Promise<Topic>;
  // The callback optionally receives the saved topic; the admin flow uses saved.id to jump to later steps.
  // Old call sites (user side) ignore the argument; the signature stays compatible.
  onSaveDone: (saved?: Topic) => void;
  // Admin configuring a topic for someone else: with user_id passed, creation goes through the admin channel and is approved directly,
  // skipping submit-for-review; editing still uses onSave.
  adminTargetUserId?: number;
  // The project progress board wires all 6 chips into this wizard, jumping to the matching step via initialStep:
  // submit→1 / review→3 / diagnose→4 / plan→5 / content→6 / insight→7
  initialStep?: 1 | 2 | 3 | 4 | 5 | 6 | 7;
  // Only for the client "Topic info" page: always "view my own deliverables" -- steps 4/5 read-only, step 6 uses
  // self-service "Article management" (not the AdminContentReview desk, which kicks non-admins to /dashboard).
  // Decoupled from role: in CustomerTopics the topic always belongs to the current user, no guessing via isStaff.
  customerView?: boolean;
  /** Enterprise owner when an agency role creates a topic; the owner is immutable when editing an existing topic. */
  enterpriseUserId?: number | null;
}

export function TopicEditor({
  initial, token, mode = 'edit', onCancel, onSave, onSaveDone, adminTargetUserId, initialStep,
  customerView = false, enterpriseUserId = null,
}: TopicEditorProps) {
  const readOnly = mode === 'view';
  const { t } = useTranslation();
  // Phase D — materials are the core of Step 1; name/target/industry come from the materials; only aliases keep a separate input
  const [profile, setProfile] = useState<BrandProfile>(
    () => ({ ...EMPTY_BRAND_PROFILE, ...(initial?.profile || {}) }),
  );
  // The materials have no aliases field, so keep a separate text input — used only by the topic table
  const name = profile.profile_name;
  const target = profile.company_short_name;
  const [aliasesText, setAliasesText] = useState((initial?.target_aliases || []).join(', '));
  // Report center brand tags — set from step1 from now on (report center shows them read-only). ''/new/mid/leader, ''/regular/special.
  const [brandTier, setBrandTier] = useState<string>(initial?.brand_tier || '');
  const [industryClass, setIndustryClass] = useState<string>(initial?.industry_class || '');
  // Without a topicId, images / videos are staged in the browser, held at the TopicEditor level to survive step switches.
  // Once persistTopic gets saved.id, they are flushed to the server in one go.
  const [pendingMediaFiles, setPendingMediaFiles] = useState<File[]>([]);
  // Engine selection UI is hidden — defaults to the 5 CN engines; editing keeps the existing config;
  const engines = useMemo<Set<EngineId>>(
    () => new Set((initial?.engines && initial.engines.length > 0) ? initial.engines : CN_ENGINES),
    [initial?.engines],
  );
  // enabled likewise "keeps the existing config when editing"; editing uses the topic's current state. It used to be hardcoded true --
  // after disabling a topic in the workbench, anyone editing and saving once would silently re-enable it and scheduling would resume batch runs
  // (fixed 2026-07-20).
  // Since 2026-09-21 **new topics default to false** (not in periodic batch runs): it used to default to true, and once step 3 saved,
  // the batch-run center would dispatch the whole matrix at the next hour and charge credits, when the user had only clicked "Save". Batch runs are now
  // triggered by step 4's "Generate GEO strategy optimization plan" / "New batch run" buttons; those wanting continuous monitoring switch
  // daily / weekly on the "Continuous monitoring" card themselves.
  const enabled = initial?.enabled ?? false;
  // 1-4 are editor wizard form steps; 5-7 are project progress downstream (plan / content & placement / results check), shown inline.
  // initialStep comes from cockpit/project progress chip links ?step=N; 5-7 require initial.id, otherwise fall back to 1.
  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5 | 6 | 7>(() => {
    const s = initialStep ?? 1;
    if (s >= 5 && !initial?.id) return 1;
    return s;
  });
  // When creating, initial.id is empty and the id is only available after saving; the step 4 health report component depends on it
  const [savedTopicId, setSavedTopicId] = useState<number | null>(initial?.id ?? null);
  const [saving, setSaving] = useState(false);
  // Step1 "Next" persists to DB right away: once materials are filled, create the draft / save the profile instead of saving everything at the end.
  const [step1Saving, setStep1Saving] = useState(false);
  const [step1Err, setStep1Err] = useState<string | null>(null);

  // Query picker — manual entry no longer allowed; all candidates are generated by DeepSeek
  // Editing: treat initial.queries as "existing candidates", checked by default
  // Total across 4 scenarios; the backend caps at MAX_EXPANSION_CANDIDATES (since 2026-09-21 120 = 30 per scenario, previously 200).
  // Lowered because producing 50 per intent/brand scenario made a single LLM call exceed 90s and time out entirely, yielding no intent queries at all.
  const SUGGEST_COUNT = 120;
  // The selection cap uses a single constant in the api layer (same value as backend MAX_SELECTED_QUERIES); expansion still accumulates 200/round over multiple rounds
  const QUERY_MAX_PICK = MAX_SELECTED_QUERIES;
  // Seed prompts — new seeds typed by the user this time. Previously submitted seeds are shown in the review status list,
  // no longer mixed into the input as removable tags (redesigned 2026-07-30)
  const [seeds, setSeeds] = useState<string[]>([]);
  // Recommended seeds (candidates offered by the materials-parsing LLM) — selected by default; not deletable, clicking only toggles "selected / grayed"
  const [recommendedSeeds, setRecommendedSeeds] = useState<{ text: string; active: boolean }[]>([]);
  const toggleRecommendedSeed = (text: string) =>
    setRecommendedSeeds(prev => prev.map(r => (r.text === text ? { ...r, active: !r.active } : r)));
  // Product names marked "featured" in the materials -> recommended seeds (2026-09-22). A seed matching a product triggers product mode in the backend:
  // category queries are expanded by its category and product evaluation questions go to brand queries, so the featured product name itself is the best seed.
  const featuredProductNames = (profile.products || [])
    .filter(p => p.is_featured && p.name.trim())
    .map(p => p.name.trim())
    .join('\n');
  useEffect(() => {
    if (!featuredProductNames) return;
    const taken = new Set([...seedPrompts.map(s => s.text), ...seeds.map(s => s.trim())]);
    setRecommendedSeeds(prev => {
      const have = new Set(prev.map(r => r.text));
      const merged = [...prev];
      for (const v of featuredProductNames.split('\n')) {
        if (v && !have.has(v) && !taken.has(v)) { merged.push({ text: v, active: true }); have.add(v); }
      }
      return merged.length === prev.length ? prev : merged;
    });
    // seeds / seedPrompts are only used for dedup and don't trigger a rerun
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [featuredProductNames]);
  // Seeds to submit this time = selected recommended seeds + user-typed seeds (deduplicated)
  const newSeedTexts = useMemo<string[]>(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const r of recommendedSeeds) {
      if (!r.active) continue;
      const t = r.text.trim();
      if (t && !seen.has(t)) { seen.add(t); out.push(t); }
    }
    for (const raw of seeds) {
      const t = raw.trim();
      if (t && !seen.has(t)) { seen.add(t); out.push(t); }
    }
    return out;
  }, [recommendedSeeds, seeds]);
  // industry also comes from profile (synced with profile.industry, same source as name/target)
  const industry = profile.industry;
  const [suggesting, setSuggesting] = useState(false);
  const [suggestErr, setSuggestErr] = useState<string | null>(null);
  // Per seed during query expansion, the first SSE meta frame reports "which product in the materials it matched"; unmatched seeds aren't in this map
  const [matchedProducts, setMatchedProducts] = useState<Record<string, MatchedProduct>>({});
  // Editing: put initial.queries into suggestions as "existing candidates" (no score);
  // since 2026-05-20 the picker groups by seed, with seed from initial.query_seeds ("" for legacy).
  // Since 2026-05-28 scene comes from initial.query_scene_types (search for legacy)
  const [suggestions, setSuggestions] = useState<QueryCandidate[]>(() => {
    const qs = initial?.queries || [];
    const seedsArr = initial?.query_seeds || [];
    const sceneArr = initial?.query_scene_types || [];
    const delArr = initial?.query_deleted || [];
    // Soft-deleted queries stay out of the candidate pool: they shouldn't be shown, nor checked and written back on save
    return qs.map((text, i) => ({
      text, score: 0, sources: [],
      ...(seedsArr[i] ? { seed: seedsArr[i] } : {}),
      ...(sceneArr[i] ? { scene: sceneArr[i] } : {}),
      ...(delArr[i] ? { deleted: true } : {}),
    })).filter(c => !c.deleted);
  });
  const [collapsedSeeds, setCollapsedSeeds] = useState<Set<string>>(new Set());
  // Soft-deleted queries stay out of the checked set -- they aren't shown in the list, so they shouldn't count as "checked by the user"
  const [picked, setPicked] = useState<Set<string>>(new Set(
    (initial?.queries || []).filter((_q, i) => !(initial?.query_deleted || [])[i]),
  ));
  const [queryFilter, setQueryFilter] = useState('');
  const [sortByScore, setSortByScore] = useState(true);
  // 2026-05-28 — candidate scene filter: null shows all, otherwise only the selected scene
  const [sceneFilter, setSceneFilter] = useState<SceneType | null>(null);

  const queries = useMemo(() => Array.from(picked).slice(0, QUERY_MAX_PICK), [picked]);
  const aliases = useMemo(
    () => aliasesText.split(/[,,\n]/).map(s => s.trim()).filter(Boolean).slice(0, 10),
    [aliasesText],
  );

  // 4-dimension scene counts — for the badges on scene filter buttons, showing each scene's count in the suggestions pool
  const sceneCounts = useMemo<Record<SceneType, number>>(() => {
    const c: Record<SceneType, number> = { search: 0, qa: 0, intent: 0, brand: 0 };
    for (const q of suggestions) {
      const s = q.scene;
      if (s && s in c) c[s] += 1;
    }
    return c;
  }, [suggestions]);

  const filteredSuggestions = useMemo(() => {
    const f = queryFilter.trim().toLowerCase();
    let out = suggestions;
    if (sceneFilter) out = out.filter(q => (q.scene || 'search') === sceneFilter);
    if (f) out = out.filter(q => q.text.toLowerCase().includes(f));
    if (sortByScore) {
      // Purely by score descending; checked items are no longer pulled to the top, so positions don't shift every time users check/uncheck
      out = [...out].sort((a, b) => b.score - a.score);
    }
    return out;
  }, [suggestions, queryFilter, sortByScore, sceneFilter]);

  // Render grouped by seed prompt. Group order: first the seeds pending submission (recommended selected + user-typed), then
  // those appearing in suggestions but not listed (persisted seeds / data drift all take this fallback).
  // Only group when there are ≥2 seeds; with a single seed / no seed, render flat.
  // Items with empty q.seed (legacy or manually added without seed) go to "Ungrouped", the last group.
  // brand-scene candidates don't follow seeds: whichever seed expanded them, they all go into the brand-name group, first
  // (together with brand basic queries). This only affects display grouping; q.seed is unchanged -- it is persisted via buildPayload,
  // and soft-deleting along with a seed relies on it. Without a brand name, group by seed as usual.
  const brandGroupKey = target.trim();
  const groupKeyOf = useCallback(
    (q: QueryCandidate) => (q.scene === 'brand' && brandGroupKey ? brandGroupKey : (q.seed || '').trim()),
    [brandGroupKey],
  );
  const groupedBySeed = useMemo<{ seed: string; items: QueryCandidate[] }[] | null>(() => {
    const order: string[] = [];
    const seen = new Set<string>();
    if (brandGroupKey && suggestions.some(q => q.scene === 'brand')) {
      seen.add(brandGroupKey); order.push(brandGroupKey);
    }
    for (const s of newSeedTexts) {
      if (!seen.has(s)) { seen.add(s); order.push(s); }
    }
    for (const q of suggestions) {
      const t = groupKeyOf(q);
      if (t && !seen.has(t)) { seen.add(t); order.push(t); }
    }
    if (order.length < 2) return null;
    const groups: { seed: string; items: QueryCandidate[] }[] = order.map(s => ({ seed: s, items: [] }));
    const byKey = new Map(groups.map(g => [g.seed, g]));
    const ungrouped: QueryCandidate[] = [];
    for (const q of filteredSuggestions) {
      const t = groupKeyOf(q);
      const g = t ? byKey.get(t) : undefined;
      if (g) g.items.push(q);
      else ungrouped.push(q);
    }
    const out = groups.filter(g => g.items.length > 0);
    if (ungrouped.length > 0) out.push({ seed: '', items: ungrouped });
    return out;
  }, [filteredSuggestions, newSeedTexts, suggestions, brandGroupKey, groupKeyOf]);

  const toggleSeedCollapse = (seed: string) => {
    setCollapsedSeeds(prev => {
      const next = new Set(prev);
      if (next.has(seed)) next.delete(seed); else next.add(seed);
      return next;
    });
  };
  const pickAllInSeed = (seed: string) => {
    setPicked(prev => {
      const next = new Set(prev);
      const items = suggestions.filter(q => groupKeyOf(q) === seed);
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

  const buildPayload = (): TopicPayload => {
    // Backfill seed in queries order — q.seed in suggestions is the seed query this candidate was expanded from.
    // backend `_queries_with_meta` persists seed into queries_json[].seed.
    const textToSeed = new Map<string, string>();
    const textToScene = new Map<string, SceneType>();
    for (const q of suggestions) {
      if (q.seed && !textToSeed.has(q.text)) textToSeed.set(q.text, q.seed);
      if (q.scene && !textToScene.has(q.text)) textToScene.set(q.text, q.scene);
    }
    const query_seeds = queries.map(q => textToSeed.get(q) || '');
    const hasAnySeed = query_seeds.some(s => s);
    // 2026-06-11 — scene tags are backfilled in queries order like seed; missing entries are empty strings (backend ignores them and keeps existing tags)
    const query_scene_types = queries.map(q => textToScene.get(q) || ('' as SceneType));
    const hasAnyScene = query_scene_types.some(s => s);
    // Phase C — submit all seed queries along; the backend dedups + auto-appends as pending
    // ★ Soft-deleted queries are **intentionally left out of the payload**: they aren't shown in the list, so they can't be checked. Backend
    //   _queries_with_meta passes through items in existing that have deleted_at, preserving them --
    //   don't stuff them back into the payload "just to be safe" here; that would restore them all.
    const seedTexts = newSeedTexts;
    return {
      ...(enterpriseUserId ? { enterprise_user_id: enterpriseUserId } : {}),
      name: name.trim(),
      target: target.trim(),
      target_aliases: aliases,
      industry: industry.trim(),
      queries,
      ...(hasAnySeed ? { query_seeds } : {}),
      ...(hasAnyScene ? { query_scene_types } : {}),
      engines: Array.from(engines),
      enabled,
      ...(seedTexts.length > 0 ? { seed_drafts: seedTexts } : {}),
      profile,
      brand_tier: brandTier,
      industry_class: industryClass,
    };
  };

  // The "save" flow extracted into a pure function returning the saved topic; reused by handleSave / handleSubmitForReview
  const persistTopic = async (): Promise<Topic | null> => {
    if (!valid) return null;
    // step1 may have already created a draft (savedTopicId); then it goes through update like "editing an existing topic",
    // and must not create again or it would duplicate. Only an empty effectiveId is truly brand new (admin create / draft creation failed).
    const effectiveId = savedTopicId ?? initial?.id ?? null;
    if (effectiveId) {
      const existingTexts = new Set(seedPrompts.map(s => s.text));
      const newSeeds = newSeedTexts.filter(s => !existingTexts.has(s));
      for (const s of newSeeds) {
        await handleSubmitSeed(s);
      }
    }
    // Admin creating a topic for someone else: via the admin channel, seeds go into the payload at once + approved on persist;
    // skip the submitSeedPrompt loop below (that endpoint forces seeds back to pending).
    const isAdminNew = !effectiveId && typeof adminTargetUserId === 'number';
    let saved: Topic | null;
    if (effectiveId) {
      // Existing topic (including a draft created in step1) -> incremental update; add queries/engines + submit for review
      saved = await aiTelemetryApi.updateTopic(effectiveId, buildPayload(), token);
    } else if (isAdminNew) {
      saved = await aiTelemetryApi.adminCreateTopicForUser(adminTargetUserId, buildPayload(), token);
    } else {
      // Fallback: brand-new topic whose step1 draft wasn't created still goes through create
      saved = await onSave(buildPayload());
      if (saved?.id) {
        const cleanSeeds = newSeedTexts;
        for (const s of cleanSeeds) {
          try {
            await aiTelemetryApi.submitSeedPrompt(saved.id, s, token);
          } catch (e) {
            setSeedSubmitErr(e instanceof Error ? e.message : String(e));
          }
        }
      }
    }
    // Flush images / videos staged in the browser onto the just-saved topic.
    // One failure doesn't affect the others — errors are aggregated and exposed via setSubmitErr without blocking the main flow.
    if (saved?.id && pendingMediaFiles.length > 0) {
      const failed: string[] = [];
      for (const f of pendingMediaFiles) {
        try {
          await topicProfileApi.uploadMedia(saved.id, f, token);
        } catch (e) {
          failed.push(`${f.name}: ${e instanceof Error ? e.message : String(e)}`);
        }
      }
      if (failed.length > 0) {
        setSubmitErr(`部分素材上传失败:\n${failed.join('\n')}`);
      }
      setPendingMediaFiles([]);
    }
    return saved;
  };

  // The "Save" button has been merged into "Submit" (handleSubmitForReview); saving triggers review.
  // To restore "save as draft" in the future, re-add const handleSave = ... wrapping persistTopic.

  // "Save" — with the review flow removed there's no "submit for review" anymore, just save. Admin mode uses adminCreateTopicForUser,
  // regular users use updateTopic. Then onSaveDone passes the saved topic out (for the modal/outer layer).
  const adminMode = typeof adminTargetUserId === 'number';
  const handleSubmitForReview = async () => {
    if (!valid || saving) return;
    setSaving(true); setSubmitErr(null);
    try {
      const saved = await persistTopic();
      if (saved?.id) setSavedTopicId(saved.id);
      // Order matters: setStep(4) before onSaveDone, so setStep isn't lost when the parent closes the editor.
      // The parent's onSaveDone should no longer unmount the editor (step 4's "Done" button handles closing).
      setStep(4);
      onSaveDone(saved ?? undefined);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setSubmitErr(msg);
    } finally { setSaving(false); }
  };

  const handleSuggest = async () => {
    const validSeeds = selectedSeedsForMining;
    if (validSeeds.length === 0 || suggesting) return;
    setSuggesting(true);
    setSuggestErr(null);
    try {
      // Multiple seeds fan out serially; each seed streams expansion via SSE -- as soon as a scene finishes its candidates are appended to the picker,
      // without waiting for the whole batch (a noticeable difference with slow models). Each candidate gets a `seed` field; the picker groups by seed.
      // queries are deduplicated globally (same text with different seeds counts as one candidate, keeping the first seed).
      const seenText = new Set(suggestions.map(q => q.text));

      // Brand basic queries: instant template concatenation (brand name + fixed suffixes), no LLM, filled on click.
      // Grouped separately under the brand name in the brand scene; queries about the brand itself are easiest for owned content to cover.
      const brandName = target.trim();
      if (brandName) {
        const basicAdds: QueryCandidate[] = [];
        for (const text of buildBrandBasicQueries(brandName)) {
          if (seenText.has(text)) continue;
          seenText.add(text);
          basicAdds.push({ text, score: 0, sources: [], scene: 'brand', seed: brandName });
        }
        if (basicAdds.length) setSuggestions(prev => [...prev, ...basicAdds]);
      }

      for (let i = 0; i < validSeeds.length; i++) {
        await aiTelemetryApi.suggestQueriesStream({
          seed: validSeeds[i], count: SUGGEST_COUNT,
          target: target.trim(),
          aliases,
          industry: industry.trim(),
          // Entity type — lets the backend switch expansion wording between service/manufacturing/brand
          entity_type: profile.entity_type || '',
          // Service region from the materials — lets the LLM lock the location dimension to the user's actual operating area
          service_geo: (profile.service_geo || '').trim(),
          // 2026-05-28 — profile fields passed through to the backend; the backend picks by scene:
          //   intent: uses profile_cases (case-traceback queries)
          //   brand:  uses everything (qualifications / tags / business description + cases)
          //   search / qa: ignored by the backend, so passing them doesn't affect output
          profile_cases: profile.case_stories || [],
          core_credentials: profile.core_credentials || [],
          brand_diff_tags: profile.brand_diff_tags || [],
          core_service_overview: profile.core_service_overview || '',
          // Product list from section 3 of the materials — a seed matching one of them uses product mode (backend find_product)
          products: profile.products || [],
        }, token, (_scene, queries) => {
          // A scene arrived — append incrementally after dedup, visible in the UI immediately
          const additions: QueryCandidate[] = [];
          for (const q of queries) {
            if (seenText.has(q.text)) continue;
            seenText.add(q.text);
            additions.push({ ...q, seed: validSeeds[i] });
          }
          if (additions.length) setSuggestions(prev => [...prev, ...additions]);
        }, meta => {
          const seedText = validSeeds[i];
          setMatchedProducts(prev => {
            if (meta.matched_product) return { ...prev, [seedText]: meta.matched_product };
            if (!(seedText in prev)) return prev;
            const next = { ...prev }; delete next[seedText]; return next;
          });
        });
      }

      setCollapsedSeeds(new Set());
    } catch (e: unknown) {
      setSuggestErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSuggesting(false);
    }
  };

  // Phase C — seed prompt review and persistence: list + submit + lock the query set
  const [seedPrompts, setSeedPrompts] = useState<SeedPrompt[]>(
    () => initial?.seed_prompts || []
  );
  const [seedSubmitErr, setSeedSubmitErr] = useState<string | null>(null);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  // "Seed selection" used in step 3 to mine candidates — sourced from seeds newly added in step 2 + seeds persisted on the server;
  // all selected by default; in step 3 users can uncheck individual seeds to fan out only on the rest
  const allSeedTexts = useMemo<string[]>(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const s of seedPrompts) {
      if (s.text && !seen.has(s.text)) { seen.add(s.text); out.push(s.text); }
    }
    for (const t of newSeedTexts) {
      if (!seen.has(t)) { seen.add(t); out.push(t); }
    }
    return out;
  }, [seedPrompts, newSeedTexts]);
  const [deselectedSeeds, setDeselectedSeeds] = useState<Set<string>>(() => new Set());
  const selectedSeedsForMining = useMemo<string[]>(
    () => allSeedTexts.filter(t => !deselectedSeeds.has(t)),
    [allSeedTexts, deselectedSeeds],
  );
  const toggleSeedSelection = (t: string) => {
    setDeselectedSeeds(prev => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t); else next.add(t);
      return next;
    });
  };

  const lockedQueryTexts = useMemo<Set<string>>(() => {
    const out = new Set<string>();
    const qs = initial?.queries || [];
    const sts = initial?.query_statuses || [];
    qs.forEach((q, i) => { if (sts[i] === 'approved') out.add(q); });
    return out;
  }, [initial?.queries, initial?.query_statuses]);
  // The "Active" badge shows when the query has **been persisted to DB**, not based on review progress (queries are no longer reviewed,
  // see the note above ReviewBadge). Freshly expanded, unsaved candidates have no badge -- they really aren't running yet.
  const savedQueryTexts = useMemo<Set<string>>(
    () => new Set(initial?.queries || []),
    [initial?.queries],
  );

  const handleSubmitSeed = async (rawText: string): Promise<boolean> => {
    const text = rawText.trim();
    // savedTopicId starts as initial?.id; after step1 creates a draft it becomes the draft id, so seeds can be persisted immediately too
    const topicId = savedTopicId ?? initial?.id ?? null;
    if (!text || !topicId) return false;
    setSeedSubmitErr(null);
    try {
      const updated = await aiTelemetryApi.submitSeedPrompt(topicId, text, token);
      setSeedPrompts(updated.seed_prompts || []);
      return true;
    } catch (e: unknown) {
      setSeedSubmitErr(e instanceof Error ? e.message : String(e));
      return false;
    }
  };

  // Admin deleting a seed query: the seed is removed from the list, and the prompts expanded from it (including the same-named is_seed one) are soft-deleted by the backend.
  // The frontend also clears the candidates under this seed in step 3, otherwise the just-deleted queries remain checkable in the picker.
  const [deletingSeed, setDeletingSeed] = useState<string | null>(null);
  const deleteSeedPrompt = async (text: string) => {
    const topicId = savedTopicId ?? initial?.id ?? null;
    if (!topicId || !adminMode || readOnly || deletingSeed) return;
    const ok = await uiConfirm(
      t('dashboard.aiTelemetry.form.seedDeleteConfirmBody', { q: text }),
      {
        title: t('dashboard.aiTelemetry.form.seedDeleteConfirmTitle'),
        okText: t('dashboard.aiTelemetry.form.seedDeleteConfirm'),
        danger: true,
      },
    );
    if (!ok) return;
    setDeletingSeed(text);
    setSeedSubmitErr(null);
    try {
      const updated = await aiTelemetryApi.deleteSeedPrompt(topicId, text, token);
      setSeedPrompts(updated.seed_prompts || []);
      setSuggestions(prev => prev.filter(q => q.seed !== text && q.text !== text));
      setPicked(prev => {
        const next = new Set(prev);
        next.delete(text);
        return next;
      });
      setDeselectedSeeds(prev => {
        const next = new Set(prev);
        next.delete(text);
        return next;
      });
      toast.success(t('dashboard.aiTelemetry.form.seedDeleted', { q: text }));
    } catch (e: unknown) {
      setSeedSubmitErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDeletingSeed(null);
    }
  };

  // Approved, active queries can't be removed by unchecking (backend _validate_query_diff returns 422);
  // to stop monitoring, soft-delete: the query and historical responses are kept, it just no longer joins batch runs / report metrics / publish query selection.
  const [deletingQuery, setDeletingQuery] = useState<string | null>(null);
  const deleteApprovedQuery = async (text: string) => {
    const topicId = savedTopicId ?? initial?.id ?? null;
    if (!topicId || readOnly || deletingQuery) return;
    const ok = await uiConfirm(
      t('topic.profile.monitor.deleteConfirmBody', { q: text }),
      {
        title: t('topic.profile.monitor.deleteConfirmTitle'),
        okText: t('topic.profile.monitor.deleteConfirm'),
        danger: true,
      },
    );
    if (!ok) return;
    setDeletingQuery(text);
    try {
      await topicProfileApi.deleteQueries(topicId, [{ text, deleted: true }], token);
      setSuggestions(prev => prev.filter(q => q.text !== text));
      setPicked(prev => {
        const next = new Set(prev);
        next.delete(text);
        return next;
      });
      toast.success(t('dashboard.aiTelemetry.form.queryDeleted', { q: text }));
    } catch (e: unknown) {
      setSuggestErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDeletingQuery(null);
    }
  };

  const togglePicked = (q: string) => {
    if (lockedQueryTexts.has(q)) return;   // approved can't be unchecked; remove via deleteApprovedQuery
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

  // With a search term / scene filter, "Clear selected" and "Remove unselected" only act on the currently filtered set;
  // candidates and check states outside the filter are untouched; without a filter the scope = everything (same as before).
  const isQueryFiltered = queryFilter.trim().length > 0 || sceneFilter !== null;
  const visibleQueryTexts = useMemo(
    () => new Set(filteredSuggestions.map(q => q.text)),
    [filteredSuggestions],
  );
  const inClearScope = (text: string) => !isQueryFiltered || visibleQueryTexts.has(text);
  const pickedInScopeCount = Array.from(picked)
    .filter(q => inClearScope(q) && !lockedQueryTexts.has(q)).length;
  const clearPicked = () => {
    // Active (approved) queries can't be unchecked, same as togglePicked
    setPicked(prev => new Set(Array.from(prev).filter(q => !inClearScope(q) || lockedQueryTexts.has(q))));
  };
  const clearSuggestions = () => {
    // Only clear unchecked candidates, keep checked ones (otherwise the valid state would collapse instantly)
    setSuggestions(prev => prev.filter(q => picked.has(q.text) || !inClearScope(q.text)));
  };

  // 2026-09-22 — candidate pool CSV import / export (pure frontend). Export = current candidate pool + check state;
  // import only writes the suggestions / picked / seeds states; persisting still goes through the existing "Save" path
  // (buildPayload backfills seed / scene; new seeds go into seed_drafts or per-item submitSeedPrompt).
  const importInputRef = useRef<HTMLInputElement>(null);
  const handleExportCsv = () => {
    if (suggestions.length === 0) return;
    const csv = serializeQueryCsv(
      suggestions.map(q => ({
        text: q.text, seed: q.seed || '', scene: q.scene || 'search', picked: picked.has(q.text),
      })),
      s => t(`topic.profile.monitor.scene.${s}.label`),
    );
    downloadTextFile(csv, queryCsvFilename(target.trim() || name.trim()));
  };
  const handleImportCsv = async (file: File | null) => {
    if (!file || readOnly) return;
    setSuggestErr(null);
    let rows: ReturnType<typeof parseQueryCsv>;
    try {
      rows = parseQueryCsv(await file.text());
    } catch (e) {
      setSuggestErr(t('dashboard.aiTelemetry.form.queriesImportParseErr', {
        err: e instanceof Error ? e.message : String(e),
      }) || `文件解析失败:${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    if (rows.length === 0) {
      setSuggestErr(t('dashboard.aiTelemetry.form.queriesImportEmpty') || '文件里没有可导入的提示词');
      return;
    }
    // Compute the diff first; change state only after confirmation
    const existingTexts = new Set(suggestions.map(q => q.text));
    const knownSeeds = new Set(allSeedTexts);
    const additions: QueryCandidate[] = [];
    const nextPicked = new Set(picked);
    const newSeeds: string[] = [];
    let dupSkipped = 0;
    let capSkipped = 0;
    let pickedAdded = 0;
    for (const r of rows) {
      if (existingTexts.has(r.text)) {
        dupSkipped += 1;
        // Existing queries only sync "monitored" to checked (the export -> edit in Excel -> import loop); unchecking isn't done from the file
        if (r.picked && !nextPicked.has(r.text) && !lockedQueryTexts.has(r.text)) {
          if (nextPicked.size < QUERY_MAX_PICK) { nextPicked.add(r.text); pickedAdded += 1; }
          else capSkipped += 1;
        }
        continue;
      }
      existingTexts.add(r.text);
      additions.push({
        text: r.text, score: 0, sources: [], scene: r.scene,
        ...(r.seed ? { seed: r.seed } : {}),
      });
      if (r.seed && !knownSeeds.has(r.seed)) { knownSeeds.add(r.seed); newSeeds.push(r.seed); }
      if (r.picked) {
        if (nextPicked.size < QUERY_MAX_PICK) { nextPicked.add(r.text); pickedAdded += 1; }
        else capSkipped += 1;
      }
    }
    if (additions.length === 0 && pickedAdded === 0) {
      toast.info(t('dashboard.aiTelemetry.form.queriesImportNothingNew', { dup: dupSkipped })
        || `文件里的 ${dupSkipped} 条都已在候选池里,没有新增`);
      return;
    }
    const seedPreview = newSeeds.slice(0, 5).join('、') + (newSeeds.length > 5 ? '…' : '');
    const ok = await uiConfirm(
      t('dashboard.aiTelemetry.form.queriesImportConfirmBody', {
        n: additions.length, k: pickedAdded, m: newSeeds.length, seeds: seedPreview,
        dup: dupSkipped, cap: capSkipped,
      }) || `将新增 ${additions.length} 条候选(勾选 ${pickedAdded} 条);新增种子词 ${newSeeds.length} 个`
        + (seedPreview ? `:${seedPreview}` : '') + `;重复跳过 ${dupSkipped} 条;超出上限未勾选 ${capSkipped} 条。`,
      {
        title: t('dashboard.aiTelemetry.form.queriesImportConfirmTitle') || '导入这份提示词表?',
        okText: t('dashboard.aiTelemetry.form.queriesImportCsv') || '导入',
      },
    );
    if (!ok) return;
    if (newSeeds.length > 0) setSeeds(prev => [...prev, ...newSeeds]);
    if (additions.length > 0) setSuggestions(prev => [...prev, ...additions]);
    setPicked(nextPicked);
    setCollapsedSeeds(new Set());
    toast.success(t('dashboard.aiTelemetry.form.queriesImportDone', {
      n: additions.length, k: pickedAdded, m: newSeeds.length,
    }) || `已导入 ${additions.length} 条,勾选 ${pickedAdded} 条,新增种子词 ${newSeeds.length} 个`);
  };

  const step1Valid = name.trim().length > 0 && target.trim().length > 0;
  // Persisted seeds count too — editing doesn't force adding new ones
  const step2Valid = newSeedTexts.length > 0 || seedPrompts.length > 0;

  // step1 "Next" persists to DB right away: materials (6 modules) + name/short name/industry saved immediately, not deferred to a single save in step3.
  //   - Existing topic / draft already created in step1 -> PUT /profile to save materials incrementally
  //   - Brand-new topic -> POST /topics/draft to create a draft and get an id for later steps
  //   - Admin on behalf of others / read-only -> no pre-save; step3's authoritative save is used (admin goes via adminCreate, avoiding lock-state validation)
  const persistStep1 = async () => {
    if (readOnly || typeof adminTargetUserId === 'number') { setStep(2); return; }
    if (step1Saving) return;
    setStep1Saving(true); setStep1Err(null);
    try {
      if (savedTopicId) {
        await topicProfileApi.updateProfile(savedTopicId, profile, token);
        // The materials endpoint doesn't include brand tags; write them back separately via the report center report-tags endpoint
        await reportsApi.updateReportTags(savedTopicId, token, {
          brand_tier: brandTier as BrandTier, industry_class: industryClass as IndustryClass,
        });
      } else {
        const saved = await aiTelemetryApi.createDraftTopic({
          ...(enterpriseUserId ? { enterprise_user_id: enterpriseUserId } : {}),
          name: name.trim(), target: target.trim(),
          target_aliases: aliases, industry: industry.trim(),
          engines: Array.from(engines), enabled, profile,
          brand_tier: brandTier, industry_class: industryClass,
        }, token);
        if (saved?.id) setSavedTopicId(saved.id);
      }
      setStep(2);
    } catch (e) {
      setStep1Err(e instanceof Error ? e.message : String(e));
    } finally { setStep1Saving(false); }
  };

  const goNext = () => {
    if (step === 1 && step1Valid) { void persistStep1(); }
    else if (step === 2 && step2Valid) setStep(3);
    else if (step === 3 && valid) setStep(4);
    else if (step === 4 && savedTopicId) setStep(5);
    else if (step === 5 && savedTopicId) setStep(6);
    else if (step === 6 && savedTopicId) setStep(7);
  };
  const goPrev = () => {
    if (step === 7) setStep(6);
    else if (step === 6) setStep(5);
    else if (step === 5) setStep(4);
    else if (step === 4) setStep(3);
    else if (step === 3) setStep(2);
    else if (step === 2) setStep(1);
  };

  // Register with the agent: fill brand materials for the user (step1) and advance the wizard. next/prev are equivalent to clicking "Next/Previous".
  useRegisterAgentActions(readOnly ? null : {
    page: 'topic_editor',
    describe: '主题编辑向导(可填写品牌名称/简称/行业/别名,并下一步保存)',
    snapshot: {
      当前步骤: step, 名称: name, 目标简称: target, 行业: industry,
      别名: aliasesText, 已填种子提示词: newSeedTexts, step1可保存: step1Valid,
    },
    actions: {
      setField: ({ name: f, value }) => {
        const v = String(value ?? '');
        if (f === 'name') setProfile(p => ({ ...p, profile_name: v }));
        else if (f === 'target') setProfile(p => ({ ...p, company_short_name: v }));
        else if (f === 'industry') setProfile(p => ({ ...p, industry: v }));
        else if (f === 'aliases') setAliasesText(v);
      },
      addSeed: ({ text }) => { void handleSubmitSeed(String(text ?? '')); },   // step2 add seed prompt (reuses the existing handler)
      next: () => goNext(),
      prev: () => goPrev(),
    },
  });

  return (
    <section
      className="rounded-xl"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <StepHeader
        step={step}
        onJump={(s) => {
          // 1-4: wizard form steps, setStep switches forms
          if (s === 1) setStep(1);
          else if (s === 2 && step1Valid) setStep(2);
          else if (s === 3 && step1Valid && step2Valid) setStep(3);
          else if (s === 4 && step1Valid && step2Valid) setStep(4);
          // 5-7: project progress downstream stages, shown inline; require a saved topic_id
          else if (s >= 5 && savedTopicId) {
            setStep(s as 5 | 6 | 7);
          }
        }}
        labels={[
          t('dashboard.aiTelemetry.form.step1'),
          t('dashboard.aiTelemetry.form.step2'),
          t('dashboard.aiTelemetry.form.step3'),
          t('dashboard.aiTelemetry.form.step4'),
          t('dashboard.aiTelemetry.form.step5'),
          t('dashboard.aiTelemetry.form.step6'),
          t('dashboard.aiTelemetry.form.step7'),
        ]}
      />

      <div className="px-5 pt-3 pb-1 text-xs text-muted">
        {step === 1 && t('dashboard.aiTelemetry.form.step1Hint')}
        {step === 2 && t('dashboard.aiTelemetry.form.step2Hint')}
        {step === 3 && t('dashboard.aiTelemetry.form.step3Hint')}
        {step === 4 && t('dashboard.aiTelemetry.form.step4Hint')}
      </div>

      <div className="px-5 py-5">
        <fieldset disabled={readOnly} className="border-0 p-0 m-0 min-w-0">
        {step === 1 && (
          <div className="space-y-4">
            <p className="text-xs text-muted">
              {t('dashboard.aiTelemetry.form.profileHint')}
            </p>
            <ProfileImporter profile={profile} onApply={setProfile}
                             token={token} disabled={readOnly}
                             topicId={initial?.id}
                             pendingMediaFiles={pendingMediaFiles}
                             onPendingMediaFilesChange={setPendingMediaFiles}
                             onApplySeeds={sugg => {
                               // Seed query candidates offered by the LLM -> recommendation area (selected by default, can be grayed, not deleted);
                               // deduplicated against submitted / user-typed ones
                               const taken = new Set([
                                 ...seedPrompts.map(s => s.text),
                                 ...seeds.map(s => s.trim()),
                               ]);
                               setRecommendedSeeds(prev => {
                                 const have = new Set(prev.map(r => r.text));
                                 const merged = [...prev];
                                 for (const s of sugg) {
                                   const v = (s || '').trim();
                                   if (v && !have.has(v) && !taken.has(v)) {
                                     merged.push({ text: v, active: true });
                                     have.add(v);
                                   }
                                 }
                                 return merged;
                               });
                             }} />
            {/* 6-module materials form (name/short name/industry are also topic.name/target/industry)
                Aliases (target_aliases) are folded into the "Basic identity" tab, no longer a separate section */}
            <BrandProfileForm
              profile={profile}
              onChange={setProfile}
              readOnly={readOnly}
              aliasesText={aliasesText}
              onAliasesTextChange={setAliasesText}
              aliasesCount={aliases.length}
              aliasesLabel={t('dashboard.aiTelemetry.form.aliases')}
              aliasesPlaceholder={t('dashboard.aiTelemetry.form.aliasesPlaceholder') || ''}
              aliasesHint={t('dashboard.aiTelemetry.form.aliasesHint', { count: aliases.length })}
            />
            {/* Report center brand tags — set here, shown read-only in the report center. Targets are matched against same-tier reference ranges */}
            <div className="rounded-md p-3 space-y-2"
                 style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
              <div className="text-xs font-semibold text-secondary">
                {t('dashboard.aiTelemetry.form.brandTagTitle')}
              </div>
              <p className="text-xs text-muted">{t('dashboard.aiTelemetry.form.brandTagHint')}</p>
              <div className="flex flex-wrap gap-4">
                <label className="text-xs text-secondary flex items-center gap-2">
                  {t('dashboard.aiTelemetry.form.brandTierLabel')}
                  <UiSelect value={brandTier} disabled={readOnly}
                    className="px-2 py-1 text-sm rounded"
                    style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                    onChange={val => setBrandTier(val)}
                    options={[
                      { value: '', label: t('dashboard.aiTelemetry.form.tagUnset') },
                      { value: 'new', label: t('dashboard.aiTelemetry.form.tierNew') },
                      { value: 'mid', label: t('dashboard.aiTelemetry.form.tierMid') },
                      { value: 'leader', label: t('dashboard.aiTelemetry.form.tierLeader') },
                    ]} />
                </label>
                <label className="text-xs text-secondary flex items-center gap-2">
                  {t('dashboard.aiTelemetry.form.industryClassLabel')}
                  <UiSelect value={industryClass} disabled={readOnly}
                    className="px-2 py-1 text-sm rounded"
                    style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                    onChange={val => setIndustryClass(val)}
                    options={[
                      { value: '', label: t('dashboard.aiTelemetry.form.tagUnset') },
                      { value: 'regular', label: t('dashboard.aiTelemetry.form.industryRegular') },
                      { value: 'special', label: t('dashboard.aiTelemetry.form.industrySpecial') },
                    ]} />
                </label>
              </div>
            </div>

            {/* Fixed response denominator — visible only when admins edit topics in the workbench; not rendered at all on the client side */}
            {adminMode && initial?.id ? (
              <FixedDenominatorBox topicId={initial.id} token={token} disabled={readOnly} />
            ) : null}
          </div>
        )}

        {step === 2 && (
          <div className="max-w-2xl space-y-4">
            {/* Phase C — review status of submitted seed queries (shown only when editing) */}
            {seedPrompts.length > 0 && (
              <div
                className="rounded-md p-3 space-y-2"
                style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
              >
                <div className="text-xs font-semibold text-secondary">
                  {t('dashboard.aiTelemetry.form.seedPromptsTitle')}
                </div>
                <ul className="space-y-1">
                  {seedPrompts.map((s, i) => (
                    <li key={i} className="flex items-center gap-2 text-xs">
                      <ReviewBadge />
                      <span className="text-primary">{s.text}</span>
                      {s.submitted_at && (
                        <span className="text-muted ml-auto">
                          {fmtTime(s.submitted_at)}
                        </span>
                      )}
                      {/* Only admins can delete seed queries; deleting takes the prompts expanded from it offline too */}
                      {adminMode && !readOnly && (
                        <button
                          type="button"
                          onClick={() => void deleteSeedPrompt(s.text)}
                          disabled={deletingSeed !== null}
                          title={t('dashboard.aiTelemetry.form.seedDeleteHint')}
                          className={`shrink-0 px-1.5 py-0.5 rounded text-[11px] ${s.submitted_at ? '' : 'ml-auto'}`}
                          style={{ color: 'var(--danger, #dc2626)', border: '1px solid var(--border-color)' }}
                        >
                          {deletingSeed === s.text
                            ? t('dashboard.aiTelemetry.form.seedDeleting')
                            : t('dashboard.aiTelemetry.form.seedDelete')}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Must wrap in a div, not a label: a label without htmlFor forwards any click inside it
                to the first associable control — which is the first tag's "×" button, so clicking anywhere deleted a seed (fixed 2026-07-30) */}
            <div>
              <div className="text-xs text-secondary">
                {t('dashboard.aiTelemetry.form.seedPromptsLabel')}*
              </div>

              {recommendedSeeds.length > 0 && (
                <div
                  className="mt-1.5 rounded-md p-2.5 space-y-1.5"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
                >
                  <div className="text-xs text-muted">
                    {t('dashboard.aiTelemetry.form.seedRecommendedHint')}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {recommendedSeeds.map(r => (
                      <button
                        key={r.text}
                        type="button"
                        onClick={() => toggleRecommendedSeed(r.text)}
                        className="rounded px-2 py-0.5 text-xs transition-colors"
                        style={r.active
                          ? { background: 'rgba(99,102,241,0.12)', color: 'var(--accent-primary)', border: '1px solid var(--accent-primary)' }
                          : { background: 'var(--bg-tertiary)', color: 'var(--text-muted)', border: '1px dashed var(--border-color)', opacity: 0.55 }}
                        title={t(r.active
                          ? 'dashboard.aiTelemetry.form.seedRecommendedOffTip'
                          : 'dashboard.aiTelemetry.form.seedRecommendedOnTip')}
                      >
                        {r.active ? '✓ ' : ''}{r.text}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-1.5">
                <TagInput
                  value={seeds}
                  onChange={setSeeds}
                  placeholder={t('dashboard.aiTelemetry.form.seedPromptsPlaceholder') || ''}
                  reserved={[...seedPrompts.map(s => s.text), ...recommendedSeeds.map(r => r.text)]}
                />
              </div>
              <div className="text-xs text-muted">
                {t('dashboard.aiTelemetry.form.seedPromptsListHint', { count: newSeedTexts.length })}
              </div>
              {/* Is the seed your own product? One click registers it in section 3 of the materials so step 3 query expansion goes by category (see MarkSeedAsProduct header comment) */}
              {newSeedTexts.length > 0 && (
                <div className="mt-2 space-y-1">
                  {newSeedTexts.map(sd => (
                    <MarkSeedAsProduct key={sd} seed={sd} products={profile.products} disabled={readOnly}
                                       onAdd={p => setProfile(prev => ({ ...prev, products: [...(prev.products || []), p] }))} />
                  ))}
                </div>
              )}
            </div>

            {seedSubmitErr && (
              <div className="text-xs text-rose-500">⚠ {seedSubmitErr}</div>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="space-y-5">
            {/* ── Part 1: pick seeds + trigger mining ─────────────────────── */}
            <section
              className="rounded-md p-4 space-y-3"
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold text-primary">
                  {t('dashboard.aiTelemetry.form.expandSectionTitle')}
                </div>
                <span className="text-xs text-muted whitespace-nowrap">
                  {t('dashboard.aiTelemetry.form.seedPickerLabel', {
                    selected: selectedSeedsForMining.length,
                    total: allSeedTexts.length,
                  })}
                </span>
              </div>

              {allSeedTexts.length === 0 ? (
                <div
                  className="rounded-md p-3 text-xs text-muted"
                  style={{ background: 'var(--bg-input)', border: '1px dashed var(--border-color)' }}
                >
                  {t('dashboard.aiTelemetry.form.seedPickerEmpty')}
                </div>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {allSeedTexts.map(t1 => {
                    const active = !deselectedSeeds.has(t1);
                    return (
                      <button
                        key={t1}
                        type="button"
                        onClick={() => toggleSeedSelection(t1)}
                        className="px-2.5 py-1 rounded-full text-xs"
                        style={{
                          background: active ? 'var(--accent-primary)' : 'var(--bg-input)',
                          color: active ? '#fff' : 'var(--text-secondary)',
                          border: '1px solid var(--border-color)',
                        }}
                      >
                        {active ? '✓ ' : ''}{t1}
                      </button>
                    );
                  })}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button" onClick={handleSuggest}
                  disabled={selectedSeedsForMining.length === 0 || suggesting}
                  className="px-4 py-2 rounded-md text-sm font-medium whitespace-nowrap disabled:opacity-50"
                  style={{ background: 'var(--accent-primary)', color: '#fff' }}
                >
                  {suggesting
                    ? t('dashboard.aiTelemetry.form.suggestRunningLong', { count: SUGGEST_COUNT })
                    : t('dashboard.aiTelemetry.form.suggestFromSeeds', { count: selectedSeedsForMining.length })}
                </button>
                <span className="text-xs text-muted">
                  {target.trim()
                    ? t('dashboard.aiTelemetry.form.suggestContextOn', { target: target.trim() })
                    : t('dashboard.aiTelemetry.form.suggestContextOff')}
                </span>
              </div>
              {suggestErr && (
                <div className="text-xs text-rose-500">⚠ {suggestErr}</div>
              )}
              {Object.entries(matchedProducts).map(([seedText, mp]) => (
                <div key={seedText} className="text-xs rounded-md px-3 py-1.5"
                     style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
                  {t('dashboard.aiTelemetry.form.matchedProduct', {
                    seed: seedText, name: mp.name,
                    category: mp.category || t('dashboard.aiTelemetry.form.matchedProductNoCategory'),
                  })}
                </div>
              ))}
            </section>

            {/* ── Part 2: candidate query list ────────────────────────── */}
            <section
              className="rounded-md p-4 space-y-3"
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-primary">
                    {t('dashboard.aiTelemetry.form.candidateSectionTitle', { max: QUERY_MAX_PICK })}
                  </div>
                  <div className="text-xs text-muted mt-0.5">
                    {t('dashboard.aiTelemetry.form.queriesPickerHint', { max: QUERY_MAX_PICK, count: SUGGEST_COUNT })}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {/* CSV import / export lives in the header rather than the bottom bar: the bottom bar only renders with a non-empty pool, and import must work on an empty pool */}
                  <input
                    ref={importInputRef} type="file" accept={QUERY_CSV_ACCEPT} hidden
                    onChange={e => {
                      const f = e.target.files?.[0] || null;
                      e.target.value = '';   // Allow selecting the same file again
                      void handleImportCsv(f);
                    }}
                  />
                  <button
                    type="button" onClick={() => importInputRef.current?.click()}
                    disabled={readOnly}
                    className="px-2 py-1 rounded text-xs disabled:opacity-45 whitespace-nowrap"
                    style={{ background: 'transparent', border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}
                    title={t('dashboard.aiTelemetry.form.queriesImportHint')
                           || 'CSV 列:提示词, 种子词, 场景, 是否监测;也可导 .txt 一行一条'}
                  >
                    {t('dashboard.aiTelemetry.form.queriesImportCsv') || '导入 CSV'}
                  </button>
                  <button
                    type="button" onClick={handleExportCsv}
                    disabled={suggestions.length === 0}
                    className="px-2 py-1 rounded text-xs disabled:opacity-45 whitespace-nowrap"
                    style={{ background: 'transparent', border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}
                  >
                    {t('dashboard.aiTelemetry.form.queriesExportCsv') || '导出 CSV'}
                  </button>
                  <span
                    className="text-sm font-mono tabular-nums"
                    style={{ color: pickedCap ? 'var(--accent-primary)' : 'var(--text-secondary)' }}
                  >
                    {picked.size} / {QUERY_MAX_PICK}
                  </span>
                </div>
              </div>

              {/* Manual add — must pick a seed prompt it belongs to; written into suggestions (score=0)
                  with the seed field set + auto-checked into picked. */}
              <ManualQueryAdder
                disabled={readOnly}
                pickedCap={pickedCap}
                seeds={allSeedTexts}
                addQuery={(text, seed, scene) => {
                  const v = text.trim();
                  if (!v) return false;
                  if (suggestions.some(q => q.text === v) || picked.has(v)) return false;
                  if (picked.size >= QUERY_MAX_PICK) return false;
                  setSuggestions(prev => [...prev, { text: v, score: 0, sources: [], seed, scene }]);
                  setPicked(prev => new Set(prev).add(v));
                  return true;
                }}
              />

              {suggestions.length === 0 ? (
                <div
                  className="rounded-md p-6 text-xs text-muted text-center"
                  style={{ background: 'var(--bg-input)', border: '1px dashed var(--border-color)' }}
                >
                  {t('dashboard.aiTelemetry.form.queriesPickerEmpty')}
                </div>
              ) : (
                <>
                  {/* 2026-05-28 — 4-dimension scene filter: click a chip to view a single scene, click again to cancel (back to all) */}
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-muted">
                      {t('dashboard.aiTelemetry.form.sceneFilterLabel')}
                    </span>
                    <button
                      type="button" onClick={() => setSceneFilter(null)}
                      className="px-2 py-0.5 rounded-full text-xs"
                      style={{
                        background: sceneFilter === null ? 'var(--accent-primary)' : 'var(--bg-input)',
                        color: sceneFilter === null ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      {t('dashboard.aiTelemetry.form.sceneFilterAll')}
                      <span className="ml-1 opacity-70">({suggestions.length})</span>
                    </button>
                    {SCENE_KEYS.map(s => {
                      const c = sceneCounts[s];
                      const isActive = sceneFilter === s;
                      const color = SCENE_COLORS[s];
                      return (
                        <button
                          key={s} type="button"
                          onClick={() => setSceneFilter(isActive ? null : s)}
                          disabled={c === 0}
                          className="px-2 py-0.5 rounded-full text-xs"
                          style={{
                            background: isActive ? color : `${color}33`,
                            color: isActive ? '#fff' : color,
                            border: `1px solid ${color}`,
                            fontWeight: 700,
                            opacity: c === 0 ? 0.6 : 1,
                            cursor: c === 0 ? 'not-allowed' : 'pointer',
                          }}
                        >
                          {t(`topic.profile.monitor.scene.${s}.label`)}
                          <span className="ml-1 opacity-80">({c})</span>
                        </button>
                      );
                    })}
                  </div>
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
                      maxHeight: 'clamp(360px, 60vh, 640px)',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border-color)',
                    }}
                  >
                    {(() => {
                      const renderRow = (q: QueryCandidate) => {
                        const isPicked = picked.has(q.text);
                        const isLocked = lockedQueryTexts.has(q.text);
                        const isSaved = savedQueryTexts.has(q.text);
                        const disabled = isLocked || (!isPicked && pickedCap);
                        const showScore = q.score > 0;
                        const scoreColor = q.score >= 75 ? 'var(--accent-primary)'
                          : q.score >= 60 ? 'var(--text-primary)'
                          : 'var(--text-muted)';
                        return (
                          <label
                            key={q.text}
                            className="flex items-start gap-2 text-xs px-1 py-0.5 rounded"
                            style={{
                              cursor: isLocked ? 'default' : disabled ? 'not-allowed' : 'pointer',
                              opacity: !isLocked && disabled ? 0.4 : 1,
                              background: isPicked ? 'var(--bg-card)' : 'transparent',
                              color: 'var(--text-primary)',
                            }}
                            title={isLocked ? (t('dashboard.aiTelemetry.form.queryLockedHint') || '') : undefined}
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
                            {/* 2026-05-28 — 4-dimension scene badge; 2026-06-04 changed to solid fill + white text, the old 13% translucent background was too faint */}
                            {q.scene && (
                              <span
                                className="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold leading-none tracking-wide"
                                style={{
                                  background: SCENE_COLORS[q.scene],
                                  color: '#fff',
                                  boxShadow: `0 1px 2px ${SCENE_COLORS[q.scene]}55`,
                                }}
                                title={t(`topic.profile.monitor.scene.${q.scene}.label`) as string}
                              >
                                {t(`topic.profile.monitor.scene.${q.scene}.label`)}
                              </span>
                            )}
                            <span className="break-all flex-1">{q.text}</span>
                            {isSaved && (
                              <span className="shrink-0"><ReviewBadge /></span>
                            )}
                            {/* Active queries can't be unchecked but can be soft-deleted (no more batch runs; history and restore preserved) */}
                            {isLocked && !readOnly && (
                              <button
                                type="button"
                                onClick={e => { e.preventDefault(); deleteApprovedQuery(q.text); }}
                                disabled={deletingQuery !== null}
                                className="shrink-0 px-1.5 py-0.5 rounded text-[11px] leading-none disabled:opacity-50"
                                style={{ color: 'var(--danger-fg, #b91c1c)', border: '1px solid var(--border-color)' }}
                                title={t('dashboard.aiTelemetry.form.queryDeleteHint') as string}
                              >
                                {deletingQuery === q.text
                                  ? t('dashboard.aiTelemetry.form.queryDeleting')
                                  : t('dashboard.aiTelemetry.form.queryDelete')}
                              </button>
                            )}
                          </label>
                        );
                      };
                      if (!groupedBySeed) {
                        return (
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-3 gap-y-1">
                            {filteredSuggestions.map(renderRow)}
                          </div>
                        );
                      }
                      return (
                        <div className="space-y-2">
                          {groupedBySeed.map(({ seed, items }) => {
                            const collapsed = collapsedSeeds.has(seed);
                            const pickedInGroup = items.filter(q => picked.has(q.text)).length;
                            const headerLabel = seed
                              || (t('dashboard.aiTelemetry.form.seedGroupUngrouped') || '未分组');
                            return (
                              <div key={seed || '__ungrouped__'}>
                                <div
                                  className="flex items-center gap-2 px-1 py-1 rounded sticky top-0"
                                  style={{
                                    background: 'var(--bg-secondary)',
                                    borderBottom: '1px solid var(--border-color)',
                                  }}
                                >
                                  <button
                                    type="button"
                                    onClick={() => toggleSeedCollapse(seed)}
                                    className="text-xs px-1"
                                    style={{ color: 'var(--text-secondary)', cursor: 'pointer' }}
                                  >
                                    {collapsed ? '▶' : '▼'}
                                  </button>
                                  <span className="text-xs font-medium break-all flex-1"
                                        style={{ color: 'var(--text-primary)' }}>
                                    {headerLabel}
                                  </span>
                                  <span className="text-xs font-mono tabular-nums shrink-0"
                                        style={{ color: 'var(--text-muted)' }}>
                                    {pickedInGroup} / {items.length}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() => pickAllInSeed(seed)}
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
                                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-3 gap-y-1 pl-3 pt-1">
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
                      type="button" onClick={clearPicked} disabled={pickedInScopeCount === 0}
                      className="px-2 py-1 rounded disabled:opacity-45"
                      style={{
                        background: 'transparent',
                        border: '1px solid var(--border-color)',
                        color: 'var(--text-secondary)',
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
            </section>

            <p className="text-xs text-muted text-center">
              {t('dashboard.aiTelemetry.form.scheduleNote')}
            </p>
          </div>
        )}
        {step === 4 && (
          <HealthReportStep topicId={savedTopicId}
                            initialWebsite={profile.website || ''}
                            token={token} />
        )}
        {step === 5 && savedTopicId && (
          // All plan actions belong to the client (backend opened them to owner-or-staff);
          // the only one disabled is the true read-only state (mode='view', e.g. the agency's enterprise business is suspended).
          <ExecutionPlanView topicId={savedTopicId} allowStart={!readOnly} />
        )}
        {step === 6 && savedTopicId && (
          customerView
            ? <Content lockedTopicId={savedTopicId} />
            : <AdminContentReview lockedTopicId={savedTopicId} />
        )}
        {step === 7 && savedTopicId && (
          <TopicBriefingsEmbed topicId={savedTopicId} token={token} />
        )}
        {step >= 5 && !savedTopicId && (
          <div className="py-12 text-center text-sm text-muted">
            {t('dashboard.aiTelemetry.form.step4NoTopic')}
          </div>
        )}
        </fieldset>
      </div>

      <footer
        className="px-5 py-3 flex items-center justify-between gap-2"
        style={{ borderTop: '1px solid var(--border-color)' }}
      >
        <button type="button" onClick={onCancel} className="px-3 py-1.5 text-sm rounded-md text-secondary">
          {t('dashboard.aiTelemetry.form.cancel')}
        </button>
        <div className="flex items-center gap-2">
          {step > 1 && (
            <button
              type="button" onClick={goPrev}
              className="px-3 py-1.5 text-sm rounded-md"
              style={{ background: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
            >
              ← {t('dashboard.aiTelemetry.form.prev')}
            </button>
          )}
          {(step < 3 || (step >= 4 && step < 7 && !!savedTopicId)) && (
            <button
              type="button" onClick={goNext}
              disabled={(step === 1 && (!step1Valid || step1Saving)) || (step === 2 && !step2Valid)}
              className="px-3 py-1.5 text-sm rounded-md text-white disabled:opacity-40"
              style={{ background: 'var(--accent-primary)' }}
            >
              {step === 1 && step1Saving ? '…' : `${t('dashboard.aiTelemetry.form.next')} →`}
            </button>
          )}
          {step === 3 && !readOnly && (
            <button
              type="button" onClick={handleSubmitForReview} disabled={!valid || saving}
              className="px-3 py-1.5 text-sm rounded-md text-white disabled:opacity-40"
              style={{ background: 'var(--accent-primary)' }}
            >
              {saving
                ? '…'
                : t(
                  adminMode
                    ? 'dashboard.aiTelemetry.form.adminCreate'
                    : 'dashboard.aiTelemetry.form.submit',
                )}
            </button>
          )}
          {step >= 4 && (
            <button
              type="button" onClick={onCancel}
              className="px-3 py-1.5 text-sm rounded-md text-white"
              style={{ background: 'var(--accent-primary)' }}
            >
              {t('dashboard.aiTelemetry.form.done')}
            </button>
          )}
        </div>
      </footer>
      {submitErr && (
        <div className="px-5 pb-3 text-xs" style={{ color: 'var(--danger-fg)' }}>
          ⚠ {submitErr}
        </div>
      )}
      {step1Err && (
        <div className="px-5 pb-3 text-xs" style={{ color: 'var(--danger-fg)' }}>
          ⚠ {step1Err}
        </div>
      )}
    </section>
  );
}

function ManualQueryAdder({
  disabled, pickedCap, seeds, addQuery,
}: {
  disabled: boolean;
  pickedCap: boolean;
  seeds: string[];
  addQuery: (text: string, seed: string, scene: SceneType) => boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState('');
  const [chosenSeed, setChosenSeed] = useState<string>(seeds[0] || '');
  // 2026-06-11 — manual additions must also belong to a scene tag (search/QA/intent/brand), default search
  const [chosenScene, setChosenScene] = useState<SceneType>('search');
  const [hint, setHint] = useState<string | null>(null);

  // When the seeds list changes (user adds/removes seeds in step 2), reset if the current selection is gone
  useEffect(() => {
    if (!seeds.includes(chosenSeed)) setChosenSeed(seeds[0] || '');
  }, [seeds, chosenSeed]);

  const noSeeds = seeds.length === 0;
  const cantSubmit = disabled || pickedCap || noSeeds || !chosenSeed || !draft.trim();

  const submit = () => {
    if (noSeeds || !chosenSeed) {
      setHint(t('dashboard.aiTelemetry.form.manualAddNeedSeed')
        || '请先填一条种子提示词');
      return;
    }
    const v = draft.trim();
    if (!v) return;
    const ok = addQuery(v, chosenSeed, chosenScene);
    if (ok) {
      setDraft('');
      setHint(null);
    } else {
      setHint(
        pickedCap
          ? t('dashboard.aiTelemetry.form.manualAddCap', { max: MAX_SELECTED_QUERIES })
          : t('dashboard.aiTelemetry.form.manualAddDuplicate'),
      );
    }
  };

  return (
    <div className="flex items-start gap-2">
      <UiSelect
        value={chosenSeed}
        onChange={val => { setChosenSeed(val); if (hint) setHint(null); }}
        disabled={disabled || pickedCap || noSeeds}
        className="px-2 py-1.5 rounded-md text-xs shrink-0"
        style={{
          background: 'var(--bg-input)',
          border: '1px solid var(--border-color)',
          color: 'var(--text-primary)',
          minWidth: 140, maxWidth: 220,
          opacity: (disabled || pickedCap || noSeeds) ? 0.5 : 1,
        }}
        title={noSeeds
          ? (t('dashboard.aiTelemetry.form.manualAddNeedSeed') || '请先填种子提示词')
          : (t('dashboard.aiTelemetry.form.manualAddSeedTitle') || '关联到种子提示词')}
        options={noSeeds
          ? [{ value: '', label: t('dashboard.aiTelemetry.form.manualAddSeedEmpty') || '无可用种子' }]
          : seeds.map(s => ({ value: s, label: s }))}
      />
      <UiSelect
        value={chosenScene}
        onChange={val => { setChosenScene(val as SceneType); if (hint) setHint(null); }}
        disabled={disabled || pickedCap || noSeeds}
        className="px-2 py-1.5 rounded-md text-xs shrink-0"
        style={{
          background: 'var(--bg-input)',
          border: '1px solid var(--border-color)',
          color: SCENE_COLORS[chosenScene],
          minWidth: 84,
          opacity: (disabled || pickedCap || noSeeds) ? 0.5 : 1,
        }}
        title={t('dashboard.aiTelemetry.form.manualAddSceneTitle') || '场景标签'}
        options={SCENE_KEYS.map(s => ({ value: s, label: t(`topic.profile.monitor.scene.${s}.label`) }))}
      />
      <div className="flex-1">
        <input
          type="text" value={draft}
          disabled={disabled || pickedCap || noSeeds}
          onChange={e => { setDraft(e.target.value); if (hint) setHint(null); }}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }}
          placeholder={t('dashboard.aiTelemetry.form.manualAddPlaceholder') || ''}
          className="w-full px-3 py-1.5 rounded-md text-xs"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)',
                   color: 'var(--text-primary)', opacity: (disabled || pickedCap || noSeeds) ? 0.5 : 1 }}
        />
        {hint && <div className="mt-1 text-[11px]" style={{ color: 'var(--warn-fg)' }}>{hint}</div>}
      </div>
      <button
        type="button" onClick={submit}
        disabled={cantSubmit}
        className="px-3 py-1.5 text-xs rounded-md text-white whitespace-nowrap"
        style={{
          background: 'var(--accent-primary)',
          opacity: cantSubmit ? 0.4 : 1,
        }}
      >
        {t('dashboard.aiTelemetry.form.manualAddBtn')}
      </button>
    </div>
  );
}

function StepHeader({
  step, labels, onJump,
}: {
  step: number;
  labels: string[];
  onJump: (s: number) => void;
}) {
  return (
    <div
      className="px-5 py-4 flex items-center gap-2"
      style={{ borderBottom: '1px solid var(--border-color)' }}
    >
      {labels.map((label, i) => {
        const idx = i + 1;
        const isActive = idx === step;
        const isDone = idx < step;
        const dotBg = isActive
          ? 'var(--accent-primary)'
          : isDone ? 'var(--accent-primary)' : 'var(--bg-input)';
        const dotColor = isActive || isDone ? '#fff' : 'var(--text-muted)';
        const labelColor = isActive
          ? 'var(--text-primary)'
          : isDone ? 'var(--text-secondary)' : 'var(--text-muted)';
        return (
          <Fragment key={idx}>
            <button
              type="button"
              onClick={() => onJump(idx)}
              className="flex items-center gap-2 px-1 py-0.5 rounded"
              style={{ cursor: 'pointer' }}
            >
              <span
                className="inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold"
                style={{
                  background: dotBg,
                  color: dotColor,
                  border: isActive ? '2px solid var(--accent-primary)' : '1px solid var(--border-color)',
                }}
              >
                {isDone ? '✓' : idx}
              </span>
              <span
                className="text-sm"
                style={{ color: labelColor, fontWeight: isActive ? 600 : 400 }}
              >
                {label}
              </span>
            </button>
            {i < labels.length - 1 && (
              <span
                className="flex-1 h-px mx-1"
                style={{
                  background: idx < step ? 'var(--accent-primary)' : 'var(--border-color)',
                  maxWidth: '6rem',
                }}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
//  v1 citation tracking tab
// ═══════════════════════════════════════════════════════════════

function TrackingTab({ topics, token, topicId, onTopicChange }: {
  topics: Topic[]; token: string;
  topicId: number | null; onTopicChange: (id: number | null) => void;
}) {
  const { t } = useTranslation();
  const setTopicId = onTopicChange;
  const [matrix, setMatrix] = useState<TrackingMatrix | null>(null);
  const [sov, setSoV] = useState<ShareOfVoice | null>(null);
  const [loading, setLoading] = useState(false);
  const [openCell, setOpenCell] = useState<{ query: string; engine: EngineId } | null>(null);

  useEffect(() => {
    if (topicId === null && topics.length > 0) setTopicId(topics[0].id);
  }, [topics, topicId, setTopicId]);

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
        <UiSelect
          value={String(topicId ?? '')} onChange={val => setTopicId(Number(val))}
          className="px-3 py-1.5 rounded-md text-sm"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
          options={topics.map(t => ({ value: String(t.id), label: t.name }))}
        />
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
  const started = matrix.started_at ? fmtTime(matrix.started_at, false).slice(0, 10) : '-';
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
                  ? 'var(--success-bg)' : sov.optimal_rate_pct >= 40
                    ? 'var(--warn-bg)' : 'var(--danger-bg)',
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
              background: hit ? 'var(--success-solid)' : 'var(--border-color)',
            }} />
            <span className="w-20" style={{ color: ENGINE_COLORS[e.engine] }}>
              {engineLabel(e.engine)}
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
                {engineLabel(e)}
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
  const date = cell.first_hit_at ? fmtTime(cell.first_hit_at, false).slice(5, 10) : '';
  return (
    <button type="button" onClick={onClick}
      className="px-2 py-1 rounded transition-colors cursor-pointer"
      style={{
        background: hit ? 'var(--success-bg)' : 'transparent',
        color: hit ? 'var(--success-fg)' : 'var(--text-muted)',
        border: hit ? '1px solid rgba(16,185,129,0.4)' : '1px solid transparent',
      }}
      title={hit ? `${cell.total_hits}/${cell.total_runs} 命中` : `${cell.total_runs} 次跑批 0 命中`}>
      {hit ? `✓ ${date}` : '✕'}
    </button>
  );
}

// ── drawer: cell details + past responses + diagnose button ────────────────────

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
              {engineLabel(engine)} · {query}
            </div>
            {data && (
              <div className="text-[11px] text-muted mt-0.5">
                {data.cell.total_hits}/{data.cell.total_runs} {t('dashboard.aiTelemetry.tracking.hits')}
                {data.cell.first_hit_at && (
                  <> · {t('dashboard.aiTelemetry.tracking.firstHit')}: {fmtTime(data.cell.first_hit_at, false).slice(0, 10)}</>
                )}
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} className="text-muted hover:text-primary text-lg px-2">✕</button>
        </header>

        <div className="px-5 py-4 space-y-4 text-xs">
          {!data && <div className="text-muted py-6 text-center">…</div>}

          {/* Diagnosis block (LLM) */}
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

          {/* Past responses */}
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
                    background: r.priority === 'P0' ? 'var(--danger-solid)' : r.priority === 'P1' ? 'var(--warn-solid)' : '#9ca3af',
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
  const dt = ev.created_at ? fmtTime(ev.created_at) : '';
  const posColor: Record<string, { bg: string; fg: string }> = {
    lead: { bg: 'var(--success-bg)', fg: '#10b981' },
    body: { bg: 'rgba(59,130,246,0.18)', fg: '#3b82f6' },
    tail: { bg: 'var(--warn-bg)', fg: '#f59e0b' },
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
      {ev.reasoning ? (
        <details className="mt-1">
          <summary className="text-[11px] text-muted cursor-pointer select-none">
            {t('dashboard.aiTelemetry.results.reasoning')} ({ev.reasoning.length})
          </summary>
          <p className="mt-1 text-muted text-[11px] whitespace-pre-wrap">{ev.reasoning}</p>
        </details>
      ) : null}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
//  v1.2 optimization suggestions (weekly report) tab
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
        <UiSelect
          value={String(topicId ?? '')} onChange={val => setTopicId(Number(val))}
          className="px-3 py-1.5 rounded-md text-sm"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
          options={topics.map(t => ({ value: String(t.id), label: t.name }))}
        />
        {briefings.length > 0 && (
          <UiSelect value={String(selectedId ?? '')} onChange={val => setSelectedId(Number(val))}
            className="px-3 py-1.5 rounded-md text-sm"
            style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            options={briefings.map(b => ({
              value: String(b.id),
              label: `${new Date(b.period_start).toLocaleDateString()} ~ ${new Date(b.period_end).toLocaleDateString()}`,
            }))} />
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

// Embedded version: fixed topicId, no topic picker. Used for step 7 of the TopicEditor 7-step wizard.
function TopicBriefingsEmbed({ topicId, token }: { topicId: number; token: string }) {
  const { t } = useTranslation();
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    setLoading(true);
    aiTelemetryApi.listBriefings(topicId, token)
      .then(list => {
        setBriefings(list);
        setSelectedId(list[0]?.id ?? null);
      })
      .finally(() => setLoading(false));
  }, [topicId, token]);

  const selected = useMemo(
    () => briefings.find(b => b.id === selectedId) ?? null,
    [briefings, selectedId],
  );

  const handleGenerate = async () => {
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

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        {briefings.length > 0 && (
          <UiSelect value={String(selectedId ?? '')} onChange={val => setSelectedId(Number(val))}
            className="px-3 py-1.5 rounded-md text-sm"
            style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            options={briefings.map(b => ({
              value: String(b.id),
              label: `${new Date(b.period_start).toLocaleDateString()} ~ ${new Date(b.period_end).toLocaleDateString()}`,
            }))} />
        )}
        <button type="button" onClick={handleGenerate} disabled={generating}
          className="text-xs px-3 py-1.5 rounded-md text-white disabled:opacity-50"
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
          {t('dashboard.aiTelemetry.briefings.generatedAt')} {fmtTime(briefing.generated_at)}
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
                    background: a.priority === 'P0' ? 'var(--danger-solid)' : a.priority === 'P1' ? 'var(--warn-solid)' : '#9ca3af',
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
            <span style={{ color: (score ?? 0) >= n ? 'var(--warn-fg)' : 'var(--text-muted)' }}>★</span>
          </button>
        ))}
      </div>
    </article>
  );
}

// TopicEditor step 4 — embedded health diagnosis report. Shows a generate button when idle, polls while generating, and when ready
// shows status + a link to the full report page. Doesn't reimplement AdminSolution's whole UI (~600 lines), only a summary +
// entry point; for details, admins click "View full report" to navigate away.
// Step 4 no longer takes readOnly: every action in this step belongs to the client (see the canAct note below);
// the true read-only state is disabled uniformly by the outer fieldset (mode='view'), so the component needn't check again.
function HealthReportStep({ topicId, initialWebsite, token }: {
  topicId: number | null;
  initialWebsite: string;
  token: string;
}) {
  const { t, i18n } = useTranslation();
  // Step 4 (GEO strategy optimization plan) is **the client's own deliverable**, with the same features as admins: generate / regenerate /
  // retry are all available to them. All three buttons hit the same endpoint POST solution/generate, which the backend opened to
  // owner-or-staff and bills per copy (KIND_SOLUTION), spending their own credits.
  const canAct = true;
  // canGenerate has the same source as canAct; a separate name is kept for the empty-state button to read, for clearer semantics.
  const canGenerate = canAct;
  const [sol, setSol] = useState<TopicStrategicSolution | null>(null);
  // Unit price + balance: clicking charges immediately, so before clicking users must see the cost and whether it's enough. If unavailable, hide this line
  // without blocking the button -- the real balance check is in the backend, which returns 402 with a message if it can't charge.
  const [fee, setFee] = useState<{ price: number; balance: number } | null>(null);
  useEffect(() => {
    let cancelled = false;
    balanceApi.get(token).then(b => {
      if (!cancelled) setFee({ price: b.price_solution_cents, balance: b.balance_cents });
    }).catch(() => { if (!cancelled) setFee(null); });
    return () => { cancelled = true; };
  }, [token]);
  const [loaded, setLoaded] = useState(false);   // UI can only be decided after the first fetch, avoiding a flash of the idle placeholder
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleExportPdf = async () => {
    if (!sol || sol.status !== 'ready' || exporting) return;
    setExporting(true); setErr(null);
    try { await exportSolutionPdf(sol, t, i18n.language || 'zh'); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setExporting(false); }
  };

  const handleExportDocx = async () => {
    if (!sol || sol.status !== 'ready' || exporting) return;
    setExporting(true); setErr(null);
    try { await exportSolutionDocx(sol, t, i18n.language || 'zh'); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setExporting(false); }
  };

  useEffect(() => {
    if (!topicId) { setLoaded(true); return; }
    let cancelled = false;
    let pollHandle: number | null = null;
    const tick = async () => {
      try {
        const s = await adminReviewApi.getStrategicSolution(topicId, token);
        if (cancelled) return;
        setSol(s);
        setLoaded(true);
        if (s.status !== 'generating' && pollHandle) {
          window.clearInterval(pollHandle);
          pollHandle = null;
        }
      } catch (e) {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : String(e));
          setLoaded(true);
        }
      }
    };
    tick();
    pollHandle = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      if (pollHandle) window.clearInterval(pollHandle);
    };
  }, [topicId, token]);

  // Auto-generate — no longer asks the user for a website; uses the one filled in the topic materials (initialWebsite).
  // An empty string is allowed too: backend generate degrades to 4 blocks.
  const handleGenerate = async () => {
    if (!topicId || busy) return;
    setBusy(true); setErr(null);
    try {
      const s = await adminReviewApi.generateStrategicSolution(topicId, (initialWebsite || '').trim(), token);
      setSol(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!topicId) {
    return (
      <div className="rounded-md p-4 text-sm text-muted text-center"
           style={{ background: 'var(--bg-secondary)', border: '1px dashed var(--border-color)' }}>
        {t('dashboard.aiTelemetry.form.step4NoTopic')}
      </div>
    );
  }

  // Show loading until the first fetch completes, so the idle placeholder doesn't flash before being replaced by ready.
  if (!loaded) {
    return (
      <div className="py-12 flex flex-col items-center gap-3 text-sm text-muted">
        <div className="animate-spin rounded-full h-6 w-6 border-2 border-t-transparent"
             style={{ borderColor: 'var(--accent-primary)', borderTopColor: 'transparent' }} />
        <div>{t('common.loading')}</div>
      </div>
    );
  }

  const status = sol?.status || 'idle';
  return (
    <div className="space-y-3">
      {err && (
        <div className="rounded-md p-3 text-xs"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {/* Monitoring & batch-run panel: which models / cadence and time / run new · run all once / current status.
          This is the only entry to start a new topic running (manual by default, not in periodic batch runs); the same component is also on the topic settings page. */}
      {topicId && <MonitorRunPanel topicId={topicId} token={token} />}

      {/* Audit result overview (six metrics, same metric definition as the report overview) — 2026-07-30 replaced the batch-run process display */}
      {topicId && <TopicKpiOverviewCard topicId={topicId} />}

      {status === 'idle' && (
        <div className="rounded-md p-4 space-y-3"
             style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
          <div className="text-sm text-secondary">
            {canGenerate
              ? t('dashboard.aiTelemetry.form.step4Idle')
              : t('dashboard.aiTelemetry.form.step4IdleCustomer')}
          </div>
          {canGenerate && fee && fee.price > 0 && (
            <div className="text-xs text-muted">
              生成一份扣 <span className="text-primary font-medium">{fee.price}</span> 积分
              {' '}· 余额 {fee.balance} 积分
              {fee.balance < fee.price && (
                <span style={{ color: '#dc2626' }}>
                  {' '}· 还差 {fee.price - fee.balance} 积分
                </span>
              )}
            </div>
          )}
          {canGenerate && (
            <button type="button" disabled={busy}
                    onClick={handleGenerate}
                    className="px-3 py-1.5 text-sm rounded-md text-white disabled:opacity-40"
                    style={{ background: 'var(--accent-primary)' }}>
              {busy ? '…' : t('dashboard.aiTelemetry.form.step4Generate')}
            </button>
          )}
        </div>
      )}

      {status === 'generating' && sol && <SolutionGeneratingBanner sol={sol} />}

      {status === 'ready' && sol && (
        <div className="space-y-4">
          {/* Top bar: status bar + export PDF / Word + regenerate; full content laid out directly below */}
          <div className="rounded-md p-3 flex items-center justify-between gap-3 flex-wrap"
               style={{ background: 'var(--success-bg)',
                        border: '1px solid rgba(16,185,129,0.30)' }}>
            <div className="text-sm text-primary">
              ✓ {t('dashboard.aiTelemetry.form.step4Ready', {
                at: sol.updated_at ? fmtTime(sol.updated_at) : '',
              })}
            </div>
            <div className="flex items-center gap-2">
              <button type="button" disabled={exporting} onClick={handleExportPdf}
                      className="text-xs px-3 py-1.5 rounded-md text-white whitespace-nowrap disabled:opacity-50"
                      style={{ background: 'var(--accent-primary)' }}>
                {exporting ? t('admin.solution.exporting') : t('admin.solution.export')}
              </button>
              <button type="button" disabled={exporting} onClick={handleExportDocx}
                      className="text-xs px-3 py-1.5 rounded-md text-white whitespace-nowrap disabled:opacity-50"
                      style={{ background: '#2563eb' }}>
                {exporting ? t('admin.solution.exporting') : t('admin.solution.exportDocx')}
              </button>
              {canAct && (
                <button type="button" disabled={busy} onClick={handleGenerate}
                        className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                        style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
                  {t('dashboard.aiTelemetry.form.step4Regen')}
                </button>
              )}
            </div>
          </div>
          <SolutionView sol={sol} />
        </div>
      )}

      {status === 'failed' && (
        <div className="rounded-md p-4 space-y-3"
             style={{ background: 'var(--danger-bg)',
                      border: '1px solid rgba(239,68,68,0.30)' }}>
          <div className="text-sm" style={{ color: 'var(--danger-fg)' }}>
            {t('dashboard.aiTelemetry.form.step4Failed', { err: sol?.error || '—' })}
          </div>
          {canAct && (
            <button type="button" disabled={busy} onClick={handleGenerate}
                    className="px-3 py-1.5 text-sm rounded-md text-white"
                    style={{ background: 'var(--accent-primary)' }}>
              {t('dashboard.aiTelemetry.form.step4Regen')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
