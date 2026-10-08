// Topic info (client version) — after the workbench opened externally, clients view/edit their own project topics here.
// Adapted from the admin "Configure topics" page (AdminAccountTopics):
//   1. All data uses self-service metric definitions -- topics via listTopics (own), pipeline via listMyTopicReviews (own);
//   2. Hide actions clients have no permission for -- batch-run tiers (daily/weekly/pause), trigger batch run now, start project;
//   3. Editing uses the embedded TopicEditor in non-admin mode (createTopic/updateTopic, backend checks ownership),
//      not jumping to the admin /ops/topics/:id/edit full-page wizard.
import { Fragment, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  aiTelemetryApi, type Topic, type TopicPayload,
} from '../../services/aiTelemetryApi';
import { adminReviewApi, type StageState, type TopicReviewListItem } from '../../services/adminReviewApi';
import { TopicEditor } from '../Dashboard/AiTelemetry';
import { useAuth } from '../../contexts/AuthContext';
import { useAgencyContext } from '../../contexts/AgencyContext';

// The order of the 6 project progress stages, aligned with cockpit / AdminAccountTopics.
type StageKey = 'submit' | 'review' | 'solution' | 'plan' | 'content' | 'insight';
const STAGE_ORDER: StageKey[] = ['submit', 'review', 'solution', 'plan', 'content', 'insight'];

// Each stage maps to a wizard step of the "Profile" main flow — same source as AdminAccountTopics.
type WizardStep = 1 | 2 | 3 | 4 | 5 | 6 | 7;
const STAGE_TO_STEP: Record<StageKey, WizardStep> = {
  submit: 1, review: 3, solution: 4, plan: 5, content: 6, insight: 7,
};

const PAGE_SIZE = 10;
type PipelineChipState = 'idle' | 'running' | 'done' | 'failed';

function stageToChip(s: StageState): PipelineChipState {
  return s === 'done' ? 'done'
    : s === 'running' || s === 'pending' ? 'running'
    : s === 'blocked' ? 'failed'
    : 'idle';
}

// backward-infer / forward-gate from the same source as AdminAccountTopics.
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
  if (eff.content !== 'done' && eff.insight === 'done') {
    eff.insight = 'running';
  }
  return eff;
}

// Status of the latest batch run -- this used to print the **raw DB value** of `tp.last_run_status`
// (running / success / failed), in gray text with no label. So "I stopped it manually" and
// "it crashed on its own" looked identical on the card, while the real reason (run.error, reading
// 「aborted: 人工终止 —— 需 84900 积分,余额 21000」 ("aborted: manual stop -- needs 84900 credits, balance 21000")) sat in the DB with nobody surfacing it.
//
// Now: the status in plain language + a short reason summary after it, with the full text in title on hover.
const RUN_LABEL: Record<string, { text: string; fg: string }> = {
  running:   { text: '监测中',   fg: '#b45309' },
  success:   { text: '上轮完成', fg: '#16a34a' },
  failed:    { text: '上轮失败', fg: '#dc2626' },
  cancelled: { text: '上轮取消', fg: 'var(--text-muted)' },
};

/** Compress the raw run.error into a phrase that fits on the card. Unrecognized ones are truncated as-is; don't guess. */
function runErrorBrief(error?: string | null): string {
  const e = (error || '').trim();
  if (!e) return '';
  if (/INSUFFICIENT_BALANCE|余额不足|积分不足/i.test(e)) return '积分不足';
  if (/^aborted/i.test(e) || /人工终止/.test(e)) return '人工终止';
  if (/rolled|cycle ended|superseded|topic inactive/i.test(e)) return '周期收尾';
  return e.length > 16 ? `${e.slice(0, 16)}…` : e;
}

function LastRunBadge({ status, error }: {
  status?: string | null; error?: string | null;
}) {
  if (!status) return <div className="text-muted mt-1">未跑批</div>;
  const s = RUN_LABEL[status] ?? { text: status, fg: 'var(--text-muted)' };
  // The reason is shown only for **abnormal** states. For success, error holds a closing note
  // (things like "cycle ended", "topic inactive", 「人工终止」 ("manual stop"), as scheduler.py always writes),
  // and printing it alongside the status would read "Last run completed · manual stop" -- sounding like something went wrong when nothing did.
  // The full text is still in title for anyone who wants to check.
  const brief = status === 'success' ? '' : runErrorBrief(error);
  return (
    <div className="mt-1" title={error || undefined}>
      <span style={{ color: s.fg }}>{s.text}</span>
      {brief && <span className="text-muted"> · {brief}</span>}
    </div>
  );
}

export function CustomerTopics() {
  const { t } = useTranslation();
  const { isAgency } = useAuth();
  const { enterpriseUserId, enterprises, canWrite } = useAgencyContext();
  const token = localStorage.getItem('token') || '';
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  const [stagesByTid, setStagesByTid] = useState<Record<number, TopicReviewListItem>>({});
  // undefined = list; null = create; Topic = edit
  const [editing, setEditing] = useState<Topic | null | undefined>(undefined);
  // The step to land on when entering edit from a stepper node (defaults to step 1)
  const [editStep, setEditStep] = useState<WizardStep | undefined>(undefined);
  const [page, setPage] = useState(1);

  const reload = () => {
    setLoading(true);
    aiTelemetryApi.listTopics(token, isAgency ? enterpriseUserId : null)
      .then(setTopics).catch(() => setTopics([])).finally(() => setLoading(false));
  };

  useEffect(() => {
    let cancelled = false;
    // loading already starts as true (loading on mount), avoiding a synchronous setState inside the effect.
    const selectedEnterpriseId = isAgency ? enterpriseUserId : null;
    aiTelemetryApi.listTopics(token, selectedEnterpriseId)
      .then(r => {
        if (!cancelled) {
          setTopics(r);
          setPage(1);
        }
      })
      .catch(() => { if (!cancelled) setTopics([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    // 6 project progress stages -- same agency enterprise scope as the topic list.
    adminReviewApi.listMyTopicReviews(token, undefined, selectedEnterpriseId)
      .then(items => {
        if (cancelled) return;
        const m: Record<number, TopicReviewListItem> = {};
        for (const it of items) m[it.topic_id] = it;
        setStagesByTid(m);
      })
      .catch(() => { if (!cancelled) setStagesByTid({}); });
    return () => { cancelled = true; };
  }, [token, isAgency, enterpriseUserId]);

  const totalPages = Math.max(1, Math.ceil(topics.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedTopics = topics.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const openEditor = (tp: Topic | null, step?: WizardStep) => {
    setEditStep(step);
    setEditing(tp);
  };

  // Both edit / create use the user self-service metric definition (backend checks ownership).
  const handleSave = async (payload: TopicPayload): Promise<Topic> => {
    if (!canWrite) throw new Error('当前企业业务处于只读状态');
    if (editing && editing.id) {
      return aiTelemetryApi.updateTopic(editing.id, payload, token);
    }
    return aiTelemetryApi.createTopic({
      ...payload,
      ...(isAgency && enterpriseUserId ? { enterprise_user_id: enterpriseUserId } : {}),
    }, token);
  };

  if (editing !== undefined) {
    return (
      <div className="space-y-4">
        <div className="text-xs text-muted">
          <button type="button" className="hover:underline"
            onClick={() => { setEditing(undefined); setEditStep(undefined); }}>
            主题信息
          </button>
          {' / '}
          {editing ? '编辑主题' : '新建主题'}
        </div>
        <h1 className="text-xl font-semibold text-primary">
          {editing ? '编辑主题' : '新建主题'}
        </h1>
        <TopicEditor
          initial={editing}
          token={token}
          mode={canWrite ? 'edit' : 'view'}
          customerView
          enterpriseUserId={isAgency ? enterpriseUserId : null}
          initialStep={editStep}
          onCancel={() => { setEditing(undefined); setEditStep(undefined); }}
          onSave={handleSave}
          onSaveDone={(saved) => {
            reload();
            if (saved) setEditing(saved);
          }}
        />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-semibold text-primary">主题信息</h1>
          <p className="text-xs text-secondary mt-0.5">
            查看你的每个项目主题及其进度,点主题卡片可编辑配置
          </p>
        </div>
        {canWrite && <button
          type="button"
          onClick={() => openEditor(null)}
          className="px-3 py-1.5 text-sm rounded-md text-white"
          style={{ background: 'var(--accent-primary)' }}
        >
          + 新建主题
        </button>}
      </div>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading')}</div>
      ) : topics.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">
          还没有主题,点右上角「+ 新建主题」创建第一个项目
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3">
          {pagedTopics.map(tp => {
            const stages = deriveStages(stagesByTid[tp.id]);
            const currentIdx = (() => {
              for (let i = 0; i < STAGE_ORDER.length; i++) {
                if (stageToChip(stages[STAGE_ORDER[i]]) !== 'done') return i;
              }
              return STAGE_ORDER.length - 1;
            })();
            return (
              <li key={tp.id}
                onClick={() => openEditor(tp)}
                className="flex flex-col p-5 rounded-xl cursor-pointer transition-colors hover:border-[var(--accent-primary)]"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                {/* Header: title + target / industry + Q/E counts + latest batch-run status (read-only) */}
                <div className="flex items-start justify-between gap-3 mb-4">
                  <div className="min-w-0 flex-1">
                    <h3 className="text-base font-semibold text-primary truncate" title={tp.name}>
                      {tp.name}
                    </h3>
                    <p className="text-xs text-muted mt-1 truncate">
                      {tp.target || '—'}{tp.industry ? ` · ${tp.industry}` : ''}
                    </p>
                  </div>
                  <div className="text-right text-[11px] leading-tight shrink-0">
                    {isAgency && (
                      <div className="text-muted mb-1">
                        {enterprises.find(row => row.enterprise.id === tp.user_id)?.enterprise.display_name || `企业 #${tp.user_id}`}
                      </div>
                    )}
                    <div className="text-secondary">
                      <span className="tabular-nums text-primary">{tp.queries.length}</span>
                      <span className="text-muted ml-1">Q</span>
                      <span className="mx-1 text-muted">/</span>
                      <span className="tabular-nums text-primary">{tp.engines.length}</span>
                      <span className="text-muted ml-1">E</span>
                    </div>
                    <LastRunBadge status={tp.last_run_status} error={tp.last_run_error} />
                  </div>
                </div>

                {/* 6-stage project progress stepper — clicking a node opens the corresponding edit step; idle steps aren't clickable */}
                <div className="flex items-start mt-1">
                  {STAGE_ORDER.map((k, i) => {
                    const state = stageToChip(stages[k]);
                    const isCurrent = i === currentIdx && state !== 'done';
                    const next = i < STAGE_ORDER.length - 1
                      ? stageToChip(stages[STAGE_ORDER[i + 1]]) : null;
                    const connectorDone = state === 'done' && (next === 'done' || next === 'running');
                    // The "Execution plan" stage must be clickable even when idle -- idle here means
                    // "no plan row yet", which is exactly a state **with something to do**: that page now has an empty state
                    // and a "⚡ Start project" button. Under the old rule (state !== 'idle') it was gray, so on the client
                    // side there was no way to reach the start entry except stepping "Next" all the way from step 1.
                    // The criterion is "are there prompts" rather than whether review passed: submit-for-review was
                    // retired with the review flow, and client-created topics stay in draft forever, so judging by review would
                    // block exactly the people who need rescuing. Without queries it's still gray -- then they should go back to step 3 first.
                    const isInteractive = state !== 'idle'
                      || (k === 'plan' && (tp.queries?.length || 0) > 0);
                    return (
                      <Fragment key={k}>
                        <button
                          type="button"
                          disabled={!isInteractive}
                          onClick={(e) => {
                            e.stopPropagation();
                            openEditor(tp, STAGE_TO_STEP[k]);
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
    </div>
  );
}

// Circles for the 6-stage stepper — same source as AdminAccountTopics.
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
