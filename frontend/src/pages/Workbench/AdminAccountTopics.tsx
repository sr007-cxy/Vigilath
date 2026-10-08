import { Fragment, useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  aiTelemetryApi, type AdminAccount, type ScheduleTier, type Topic,
  type TopicPayload, type TopicSchedulePatch,
} from '../../services/aiTelemetryApi';
import { adminReviewApi, type StageState, type TopicReviewListItem } from '../../services/adminReviewApi';
import { TopicEditor } from '../Dashboard/AiTelemetry';
import { StartProjectModal } from '../Admin/StartProjectModal';

// The order of the 6 project progress stages, aligned with cockpit `AdminCockpit.tsx`.
type StageKey = 'submit' | 'review' | 'solution' | 'plan' | 'content' | 'insight';
const STAGE_ORDER: StageKey[] = ['submit', 'review', 'solution', 'plan', 'content', 'insight'];

// Each stage maps to a URL step of the "Profile" main flow — same source as TopicStepper.tsx.
// Clicking a node = going straight to that step, so the old "Edit / Health diagnosis / Plan / Review copy" buttons were merged into the nodes.
const STAGE_TO_STEP: Record<StageKey, number> = {
  submit: 1, review: 3, solution: 4, plan: 5, content: 6, insight: 7,
};

const PAGE_SIZE = 10;

// Map the cockpit's StageState onto this card's chip palette (no pending; merged into running).
function stageToChip(s: StageState): PipelineChipState {
  return s === 'done' ? 'done'
    : s === 'running' || s === 'pending' ? 'running'
    : s === 'blocked' ? 'failed'
    : 'idle';
}

// Replicates the cockpit's backward-infer: when a downstream stage has progressed, an idle upstream is promoted to done.
function deriveStages(item: TopicReviewListItem | undefined): Record<StageKey, StageState> {
  const sub = item?.submission_status;
  const reviewRaw: StageState =
    sub === 'approved' ? 'done'
    : sub === 'rejected' ? 'blocked'
    : sub === 'pending' ? 'pending'
    : 'idle';
  const raw: Record<StageKey, StageState> = {
    submit: 'done',
    review: reviewRaw,
    solution: item?.diagnose_status ?? 'idle',
    plan: item?.plan_status ?? 'idle',
    content: item?.content_status ?? 'idle',
    insight: item?.insight_status ?? 'idle',
  };
  const eff = { ...raw };
  let progressed = false;
  for (let i = STAGE_ORDER.length - 1; i >= 0; i--) {
    const k = STAGE_ORDER[i];
    if (progressed && eff[k] === 'idle') eff[k] = 'done';
    if (eff[k] !== 'idle') progressed = true;
  }
  // Forward gate: results verification depends on content being published — same source as cockpit AdminCockpit.tsx.
  // When the batch run has finished but drafts aren't published yet, insight can't show done ahead of content.
  if (eff.content !== 'done' && eff.insight === 'done') {
    eff.insight = 'running';
  }
  return eff;
}

export function AdminAccountTopics() {
  const { userId } = useParams<{ userId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  // Client account (for showing the client name in the breadcrumb — brand_target / users.name). There's no single-item endpoint, so
  // find this user in adminListAccounts.
  const [account, setAccount] = useState<AdminAccount | null>(null);
  // The 6 project progress stage states, from the admin list endpoint, filtered by user_id + indexed by topic_id.
  const [stagesByTid, setStagesByTid] = useState<Record<number, TopicReviewListItem>>({});
  // undefined = list; null = create; Topic = edit (admins use the same editor)
  // With ?new=1 in the URL (redirected after registering from "Profile"), go straight into the create editor
  const [editing, setEditing] = useState<Topic | null | undefined>(
    searchParams.get('new') === '1' ? null : undefined,
  );
  const [enBusyId, setEnBusyId] = useState<number | null>(null);   // topic id currently toggling enabled
  const [page, setPage] = useState(1);

  // Start project (first time) — opens the config modal to set the scheduling metric definition; the modal calls /admin/topics/{id}/start internally.
  // The "Restart" entry is on the execution plan page (via /start?force=true), not on the card.
  const [startTopicTarget, setStartTopicTarget] = useState<Topic | null>(null);

  // Clear the URL param after entering create mode, so a page refresh doesn't auto-open it again
  useEffect(() => {
    if (searchParams.get('new') === '1') {
      const next = new URLSearchParams(searchParams);
      next.delete('new');
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // Client name display chain — same source as AdminAccounts.tsx (profile list):
  //   1. account.brand_target  -> brand name filled by the client (ai_telemetry_topics.target, most recent across topics)
  //   2. account.name          -> internal note entered by the admin at registration (e.g. "test")
  //   3. topics[0]?.target     -> fallback, in rare cases where the admin endpoint fails but the topic list succeeds
  //   4. `用户 #id` ("User #id") -> fallback when everything fails
  const customerName = account?.brand_target
    || account?.name
    || topics[0]?.target
    || `用户 #${userId}`;

  const reload = () => {
    if (!userId) return;
    setLoading(true);
    aiTelemetryApi.adminListUserTopics(Number(userId), token)
      .then(setTopics).catch(() => setTopics([])).finally(() => setLoading(false));
  };

  // Batch-run tiers: daily / weekly / off / paused N days. Uses a dedicated PATCH (partial update, other fields untouched);
  // enabled is derived by the backend from tier, the frontend no longer computes it. Takes effect at the next drip tick.
  const handlePatchSchedule = async (tp: Topic, patch: TopicSchedulePatch) => {
    if (enBusyId !== null) return;
    setEnBusyId(tp.id);
    try {
      await aiTelemetryApi.patchTopicSchedule(tp.id, patch, token);
      reload();
    } finally {
      setEnBusyId(null);
    }
  };

  const handlePauseDays = (tp: Topic, days: number) => {
    const until = new Date(Date.now() + days * 86400_000).toISOString();
    handlePatchSchedule(tp, { paused_until: until });
  };

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    setLoading(true);
    aiTelemetryApi.adminListUserTopics(Number(userId), token)
      .then(r => { if (!cancelled) setTopics(r); })
      .catch(() => { if (!cancelled) setTopics([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    // Fetch the 6 project progress stage states once, filter to this user, index by topic_id; failure doesn't affect the main list (chips show idle).
    adminReviewApi.listTopicReviews(token)
      .then(items => {
        if (cancelled) return;
        const m: Record<number, TopicReviewListItem> = {};
        const uid = Number(userId);
        for (const it of items) {
          if (it.user_id === uid) m[it.topic_id] = it;
        }
        setStagesByTid(m);
      })
      .catch(() => { if (!cancelled) setStagesByTid({}); });
    // Client account info (to get brand_target / users.name for the breadcrumb); there's no single-item GET, so find in the full table.
    aiTelemetryApi.adminListAccounts(token)
      .then(accs => {
        if (cancelled) return;
        setAccount(accs.find(a => a.id === Number(userId)) || null);
      })
      .catch(() => { if (!cancelled) setAccount(null); });
    return () => { cancelled = true; };
  }, [userId, token]);

  // topics shrink / client switches -> totalPages changes; safePage is clamped to a valid range at render time.
  const totalPages = Math.max(1, Math.ceil(topics.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedTopics = topics.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  if (!userId) return null;

  // Editing — admin PUTs on behalf of others, still via updateTopic (backend allow_admin_user is enabled).
  const handleSave = async (payload: TopicPayload): Promise<Topic> => {
    if (editing && editing.id) {
      return aiTelemetryApi.updateTopic(editing.id, payload, token);
    }
    // Create branch: TopicEditor actually goes through adminCreateTopicForUser internally;
    // this is only a fallback — onSave isn't called in admin-new mode.
    return aiTelemetryApi.adminCreateTopicForUser(Number(userId), payload, token);
  };

  if (editing !== undefined) {
    return (
      <div className="space-y-4">
        <div className="text-xs text-muted">
          <Link to="/ops/accounts" className="hover:underline">
            {t('workbench.adminAccounts.title')}
          </Link>
          {' / '}
          <Link to={`/ops/accounts/${userId}/topics`} className="hover:underline"
            onClick={(e) => { e.preventDefault(); setEditing(undefined); }}>
            {t('workbench.adminAccountTopics.title', { name: customerName })}
          </Link>
          {' / '}
          {editing ? t('workbench.adminAccountTopics.editTopic') : t('workbench.adminAccountTopics.newTopic')}
        </div>
        <h1 className="text-xl font-semibold text-primary">
          {editing ? t('workbench.adminAccountTopics.editTopic') : t('workbench.adminAccountTopics.newTopic')}
        </h1>
        <TopicEditor
          initial={editing}
          token={token}
          mode="edit"
          adminTargetUserId={Number(userId)}
          onCancel={() => setEditing(undefined)}
          onSave={handleSave}
          onSaveDone={(saved) => {
            // No longer close the editor or pop the "approval report" modal: TopicEditor automatically enters the step 4 embedded health report,
            // syncing happens in step 4's "View full report" / "Regenerate"; closing is triggered by step 4's "Done" button.
            reload();
            // Keep editing in sync with saved, so later actions (editing again etc.) get the new id
            if (saved) setEditing(saved);
          }}
        />
      </div>
    );
  }

  return (
    <div>
      <div className="text-xs text-muted mb-2">
        <Link to="/ops/accounts" className="hover:underline">
          {t('workbench.adminAccounts.title')}
        </Link>
        {' / '}
        {t('workbench.adminAccountTopics.title', { name: customerName })}
      </div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-primary">
          {t('workbench.adminAccountTopics.heading')}
        </h1>
        <button
          type="button"
          onClick={() => setEditing(null)}
          className="px-3 py-1.5 text-sm rounded-md text-white"
          style={{ background: 'var(--accent-primary)' }}
        >
          + {t('workbench.adminAccountTopics.newTopic')}
        </button>
      </div>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">
          {t('common.loading')}
        </div>
      ) : topics.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">
          {t('workbench.adminAccountTopics.emptyHint')}
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3">
          {pagedTopics.map(tp => {
            // "Has it ever started" is inferred from last_run_at: approve_topic / start_topic / rerun all get
            // a run_id, so having last_run_at means at least one batch run was triggered. Old approved topics (created directly by admins,
            // never run) take the last_run_at == null branch and show the "Start project" first-launch button.
            const hasRun = !!tp.last_run_at;
            // 6 project progress stages: via deriveStages from the same source as the cockpit; fall back to idle when data is missing.
            const stages = deriveStages(stagesByTid[tp.id]);
            // Index of the current step: the first non-done step, used for stepper visual highlighting.
            const currentIdx = (() => {
              for (let i = 0; i < STAGE_ORDER.length; i++) {
                const st = stageToChip(stages[STAGE_ORDER[i]]);
                if (st !== 'done') return i;
              }
              return STAGE_ORDER.length - 1;
            })();
            return (
              <li key={tp.id}
                onClick={() => navigate(`/ops/topics/${tp.id}/edit`)}
                className="flex flex-col p-5 rounded-xl cursor-pointer transition-colors hover:border-[var(--accent-primary)]"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                {/* Header: title + client name / industry + "Start project" CTA (when never run) */}
                <div className="flex items-start justify-between gap-3 mb-4">
                  <div className="min-w-0 flex-1">
                    <h3 className="text-base font-semibold text-primary truncate" title={tp.name}>
                      {tp.name}
                      <ScheduleBadge topic={tp} />
                    </h3>
                    <p className="text-xs text-muted mt-1 truncate">
                      {tp.target || '—'}{tp.industry ? ` · ${tp.industry}` : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <div className="text-right text-[11px] leading-tight">
                      <div className="text-secondary">
                        <span className="tabular-nums text-primary">{tp.queries.length}</span>
                        <span className="text-muted ml-1">Q</span>
                        <span className="mx-1 text-muted">/</span>
                        <span className="tabular-nums text-primary">{tp.engines.length}</span>
                        <span className="text-muted ml-1">E</span>
                      </div>
                      <div className="text-muted mt-1">
                        {tp.last_run_status || t('workbench.adminAccountTopics.neverRun')}
                      </div>
                    </div>
                    {!hasRun && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setStartTopicTarget(tp); }}
                        className="text-xs px-3 py-1.5 rounded-md text-white shrink-0"
                        style={{ background: 'var(--accent-primary)' }}
                      >
                        {t('workbench.adminAccountTopics.startProject')}
                      </button>
                    )}
                    <ScheduleControl
                      topic={tp}
                      busy={enBusyId === tp.id}
                      onPick={(patch) => handlePatchSchedule(tp, patch)}
                      onPauseDays={(d) => handlePauseDays(tp, d)}
                    />
                  </div>
                </div>

                {/* 6-stage project progress stepper — nodes are buttons themselves; idle steps are disabled */}
                <div className="flex items-start mt-1">
                  {STAGE_ORDER.map((k, i) => {
                    const state = stageToChip(stages[k]);
                    const isCurrent = i === currentIdx && state !== 'done';
                    const next = i < STAGE_ORDER.length - 1
                      ? stageToChip(stages[STAGE_ORDER[i + 1]]) : null;
                    const connectorDone = state === 'done' && (next === 'done' || next === 'running');
                    // idle = later parts of the flow not reached yet — no results and not clickable; done / running / failed are all viewable.
                    const isInteractive = state !== 'idle';
                    return (
                      <Fragment key={k}>
                        <button
                          type="button"
                          disabled={!isInteractive}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/ops/topics/${tp.id}/edit?step=${STAGE_TO_STEP[k]}`);
                          }}
                          className={`flex flex-col items-center px-1 py-1 rounded transition-colors ${
                            isInteractive
                              ? 'cursor-pointer hover:bg-[var(--bg-tertiary)]'
                              : 'cursor-default opacity-70'
                          }`}
                          style={{ width: 104 }}
                        >
                          <StepCircle state={state} index={i} isCurrent={isCurrent} />
                          <span className="text-[11px] text-secondary mt-1.5 text-center leading-tight">
                            {t(`workbench.adminCockpit.stage.${k}`)}
                          </span>
                          {/* Action chip under the node — only appears when there are results (non-idle) */}
                          {isInteractive && (
                            <span
                              className="mt-2 inline-block text-[11px] px-2 py-0.5 rounded-full transition-colors"
                              style={{
                                color: 'var(--accent-primary)',
                                border: '1px solid var(--accent-primary)',
                                background: 'transparent',
                              }}
                              // Inline style priority beats utility classes; hover changes both bg + color
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = 'var(--accent-primary)';
                                e.currentTarget.style.color = '#fff';
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = 'transparent';
                                e.currentTarget.style.color = 'var(--accent-primary)';
                              }}
                            >
                              {t(`workbench.adminAccountTopics.nodeAction.${k}`)}
                            </span>
                          )}
                        </button>
                        {i < STAGE_ORDER.length - 1 && (
                          <div className="flex-1 mt-[18px] h-[2px] rounded-full"
                               style={{ background: connectorDone ? 'var(--success-solid)' : 'var(--border-color)' }} />
                        )}
                      </Fragment>
                    );
                  })}
                </div>

              </li>
            );
          })}
        </ul>
      )}

      {topics.length > PAGE_SIZE && (
        <div className="flex items-center justify-between text-xs text-secondary mt-4">
          <span className="tabular-nums text-muted">
            {(safePage - 1) * PAGE_SIZE + 1}-{Math.min(safePage * PAGE_SIZE, topics.length)} / 共 {topics.length} 个
          </span>
          <div className="flex items-center gap-2">
            <button type="button" disabled={safePage <= 1}
                    onClick={() => setPage(Math.max(1, safePage - 1))}
                    className="px-2 py-1 rounded-md disabled:opacity-40"
                    style={{ background: 'var(--bg-tertiary)' }}>
              上一页
            </button>
            <span className="tabular-nums">{safePage} / {totalPages}</span>
            <button type="button" disabled={safePage >= totalPages}
                    onClick={() => setPage(Math.min(totalPages, safePage + 1))}
                    className="px-2 py-1 rounded-md disabled:opacity-40"
                    style={{ background: 'var(--bg-tertiary)' }}>
              下一页
            </button>
          </div>
        </div>
      )}

      {/* Start config modal: set the scheduling metric definition -> get measured sources (reuse if run recently, run a baseline only if not)
          -> lay out the publish table by source */}
      {startTopicTarget && (
        <StartProjectModal
          topicId={startTopicTarget.id}
          topic={startTopicTarget}
          onClose={() => setStartTopicTarget(null)}
          onStarted={() => {
            const tid = startTopicTarget.id;
            setStartTopicTarget(null);
            // After starting, go straight to the "Plan" step of the "Profile" main flow, no longer the standalone execution-plan page.
            navigate(`/ops/topics/${tid}/edit?step=5`);
          }}
        />
      )}
    </div>
  );
}

// Circles for the 6-stage stepper — done solid green, running yellow fill + yellow ring, failed solid red, idle hollow gray.
// The current step (isCurrent) gets an extra ring so users see at a glance where things are.
type PipelineChipState = 'idle' | 'running' | 'done' | 'failed';

function StepCircle({ state, index, isCurrent }: {
  state: PipelineChipState; index: number; isCurrent: boolean;
}) {
  const palette: Record<PipelineChipState, { bg: string; fg: string; border: string; mark: string }> = {
    done:    { bg: '#10b981', fg: '#fff',              border: '#10b981',          mark: '✓' },
    running: { bg: '#eab308', fg: '#fff',              border: '#eab308',          mark: String(index + 1) },
    failed:  { bg: '#ef4444', fg: '#fff',              border: '#ef4444',          mark: '✕' },
    idle:    { bg: 'var(--bg-card)', fg: 'var(--text-muted)', border: 'var(--border-color)', mark: String(index + 1) },
  };
  const p = palette[state];
  return (
    <span
      className="inline-flex items-center justify-center rounded-full text-[11px] font-semibold tabular-nums"
      style={{
        width: 28, height: 28,
        background: p.bg, color: p.fg,
        border: `1.5px solid ${p.border}`,
        boxShadow: isCurrent ? '0 0 0 3px rgba(234,179,8,0.25)' : 'none',
      }}
    >{p.mark}</span>
  );
}

// ── Batch-run tiers ────────────────────────────────────────────────
// The badge renders the backend's effective_state (the sole arbiter is geo/services/topic_schedule.py);
// the frontend doesn't derive it from tier+enabled+paused itself -- that's exactly the root cause of "switches invisible in the admin UI"
// like TELEMETRY_DRIP_ONLY_TOPICS; the state must always be visible.

const SCHEDULE_META: Record<string, { label: string; bg: string; fg: string }> = {
  daily:  { label: '每天跑', bg: 'var(--success-bg)',  fg: '#10b981' },
  weekly: { label: '每周跑', bg: 'rgba(59,130,246,0.12)',  fg: '#3b82f6' },
  off:    { label: '不跑',   bg: 'var(--danger-bg)',   fg: '#ef4444' },
  paused: { label: '暂停中', bg: 'rgba(234,179,8,0.14)',   fg: '#ca8a04' },
};

function effectiveOf(tp: Topic): string {
  // When the backend isn't upgraded (field missing), degrade to enabled so old data still gets a badge
  return tp.effective_state || (tp.enabled ? 'weekly' : 'off');
}

function ScheduleBadge({ topic }: { topic: Topic }) {
  const state = effectiveOf(topic);
  const meta = SCHEDULE_META[state] || SCHEDULE_META.weekly;
  const resume = topic.resumes_at ? new Date(topic.resumes_at) : null;
  const suffix = state === 'paused' && resume
    ? ` · ${resume.getMonth() + 1}-${resume.getDate()} 恢复` : '';
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded ml-2 align-middle font-normal"
          style={{ background: meta.bg, color: meta.fg }}
          title={state === 'paused' && resume ? `暂停至 ${resume.toLocaleString()}` : '跑批分级'}>
      {meta.label}{suffix}
    </span>
  );
}

function ScheduleControl({ topic, busy, onPick, onPauseDays }: {
  topic: Topic;
  busy: boolean;
  onPick: (patch: TopicSchedulePatch) => void;
  onPauseDays: (days: number) => void;
}) {
  const [openPause, setOpenPause] = useState(false);
  const state = effectiveOf(topic);
  const tier = topic.schedule_tier || (topic.enabled ? 'weekly' : 'off');

  const seg = (key: ScheduleTier, label: string, title: string) => (
    <button
      type="button" key={key} disabled={busy}
      onClick={(e) => { e.stopPropagation(); setOpenPause(false); onPick({ schedule_tier: key, clear_pause: true }); }}
      className="text-[11px] px-2 py-1 first:rounded-l-md last:rounded-r-md"
      title={title}
      style={{
        background: tier === key && state !== 'paused' ? 'var(--accent-primary)' : 'transparent',
        color: tier === key && state !== 'paused' ? '#fff' : 'var(--text-secondary)',
        border: '1px solid var(--border-color)',
        marginLeft: -1, opacity: busy ? 0.5 : 1,
      }}>
      {label}
    </button>
  );

  return (
    <div className="relative shrink-0" onClick={(e) => e.stopPropagation()}>
      <div className="inline-flex items-center">
        {seg('daily', '每天', '每天跑一轮(稀缺引擎会自动按容量尽力而为)')}
        {seg('weekly', '每周', '每周跑一轮')}
        {seg('off', '不跑', '不再为它跑批,配置保留')}
        <button
          type="button" disabled={busy}
          onClick={() => (state === 'paused' ? onPick({ clear_pause: true }) : setOpenPause(v => !v))}
          className="text-[11px] px-2 py-1 rounded-r-md"
          title={state === 'paused' ? '点此立即恢复' : '临时暂停,到点自动恢复'}
          style={{
            background: state === 'paused' ? '#ca8a04' : 'transparent',
            color: state === 'paused' ? '#fff' : 'var(--text-secondary)',
            border: '1px solid var(--border-color)', marginLeft: -1,
            opacity: busy ? 0.5 : 1,
          }}>
          {busy ? '…' : (state === 'paused' ? '恢复' : '暂停')}
        </button>
      </div>
      {openPause && (
        <div className="absolute right-0 mt-1 z-20 rounded-md p-1 flex gap-1"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          {[1, 3, 7].map(d => (
            <button key={d} type="button"
                    onClick={() => { setOpenPause(false); onPauseDays(d); }}
                    className="text-[11px] px-2 py-1 rounded"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
              {d} 天
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
