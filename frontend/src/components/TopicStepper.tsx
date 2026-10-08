// TopicStepper -- the shared "6-step pipeline" nav bar for project detail pages, aligned with the project progress board.
// Usage: any /ops/topics/:topicId/* page renders a <TopicStepper topicId={tid} active="plan" /> at the top
// 6 steps: (1) profile (2) diagnosis & plan pre-assessment (3) GEO strategy optimization plan (4) plan document (5) copy (6) effect verification & update
// Status symbols: ✓ done / ⋯ running / ─ idle / ✗ failed
//
// Design doc: docs/02-需求分析/产品需求/需求/no-audit-flow-design.md §4.4

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { adminReviewApi, type ExecutionPlan, type TopicReviewDetail, type TopicStrategicSolution } from '../services/adminReviewApi';

export type StepKey = 'profile' | 'review' | 'solution' | 'plan' | 'docs' | 'insight';
// editing is new: in draft state the plan step enters "waiting for ops to edit the publish table", highlighted in blue.
export type StepStatus = 'idle' | 'running' | 'done' | 'failed' | 'editing';

interface Props {
  topicId: number;
  active: StepKey;
}

interface StepDef {
  key: StepKey;
  label: string;
  status: StepStatus;
  hint?: string;     // subtitle (progress numbers etc.)
  onClick: () => void;
}

export function TopicStepper({ topicId, active }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const [topic, setTopic] = useState<TopicReviewDetail | null>(null);
  const [solution, setSolution] = useState<TopicStrategicSolution | null>(null);
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!topicId) return;
    let cancelled = false;
    adminReviewApi.getTopicReview(topicId, token)
      .then((d) => { if (!cancelled) setTopic(d); })
      .catch((e) => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); });
    // Health diagnosis report status via getStrategicSolution; errors aren't shown on failure (the stepper only uses status)
    adminReviewApi.getStrategicSolution(topicId, token)
      .then((s) => { if (!cancelled) setSolution(s); })
      .catch(() => {});
    // Execution plan status: plan.status + article generation progress -> plan step chip
    adminReviewApi.getExecutionPlan(topicId, token)
      .then((p) => { if (!cancelled) setPlan(p); })
      .catch(() => {});  // 404 before the project starts; don't bother the user
    return () => { cancelled = true; };
  }, [topicId, token]);

  // Infer each step's status. Profile = required profile fields complete + at least 1 selected query (profile and prompts are merged in the design).
  // Copy / placement are merged into "copy". Health diagnosis report derives from solution.status: ready=done / generating=running / failed=failed.
  const profileOk = !!(topic?.profile?.company_short_name)
                    && ((topic?.selected_query_count ?? 0) > 0
                        || (topic?.query_selected?.some(Boolean) ?? false));
  // Diagnosis & plan pre-assessment: derived from submission_status. Replicates reviewRaw from cockpit deriveStages.
  // pending has no separate visual state in the stepper, merged into running; rejected goes to failed.
  const sub = topic?.submission_status;
  const reviewChip: StepStatus =
    sub === 'approved' ? 'done'
    : sub === 'rejected' ? 'failed'
    : sub === 'pending' ? 'running'
    : 'idle';
  // Plan step chip: plan.status first; without a plan, fall back to the old last_run_status.
  // draft -> editing (blue) / confirmed + docs still running or monitoring running -> running / confirmed + all good -> done.
  const planChip: StepStatus = (() => {
    if (plan) {
      if (plan.status === 'failed') return 'failed';
      if (plan.status === 'draft' || plan.status === 'generating') return 'editing';
      // confirmed / ready compatibility
      const docsTotal = plan.publishing_plan?.length ?? 0;
      const docsPending = (plan.publishing_plan || []).some(
        it => !it.doc_id || it.doc_status === 'generating' || it.doc_status === 'draft',
      );
      const monRunning = plan.run_status === 'running';
      if (docsTotal === 0 || docsPending || monRunning) return 'running';
      return 'done';
    }
    return topic?.last_run_status === 'success' ? 'done'
         : topic?.last_run_status === 'running' ? 'running'
         : topic?.last_run_status === 'failed' ? 'failed'
         : 'idle';
  })();
  const solChip: StepStatus = solution?.status === 'ready' ? 'done'
                            : solution?.status === 'generating' ? 'running'
                            : solution?.status === 'failed' ? 'failed'
                            : 'idle';
  // Effect verification & update: replicates backend admin_review._insight_state -- approved + has had a run counts as non-idle.
  // success -> done / running -> running / failed -> failed / other -> idle.
  const insChip: StepStatus = (() => {
    if (topic?.submission_status !== 'approved' || !topic?.last_run_at) return 'idle';
    const s = (topic?.last_run_status || '').toLowerCase();
    return s === 'success' ? 'done'
      : s === 'running' ? 'running'
      : s === 'failed' ? 'failed'
      : 'idle';
  })();

  // All 6 segments jump to the matching step of the "profile" main flow, no longer to separate review/solution/plan/docs/insight detail pages.
  //   profile→step 1  review→step 3  solution→step 4  plan→step 5  docs→step 6  insight→step 7
  const editBase = `/ops/topics/${topicId}/edit`;
  const steps: StepDef[] = [
    {
      key: 'profile', label: t('workbench.topicStepper.profile'),
      status: profileOk ? 'done' : 'idle',
      hint: profileOk ? String(topic?.selected_query_count ?? 0) + ' Q' : undefined,
      onClick: () => navigate(`${editBase}?step=1`),
    },
    {
      key: 'review', label: t('workbench.topicStepper.review'),
      status: reviewChip,
      onClick: () => navigate(`${editBase}?step=3`),
    },
    {
      key: 'solution', label: t('workbench.topicStepper.solution'),
      status: solChip,
      onClick: () => navigate(`${editBase}?step=4`),
    },
    {
      key: 'plan', label: t('workbench.topicStepper.plan'),
      status: planChip,
      hint: plan && plan.status === 'draft' ? '编辑中' : undefined,
      onClick: () => navigate(`${editBase}?step=5`),
    },
    {
      key: 'docs', label: t('workbench.topicStepper.docs'),
      status: 'idle',
      onClick: () => navigate(`${editBase}?step=6`),
    },
    {
      key: 'insight', label: t('workbench.topicStepper.insight'),
      status: insChip,
      onClick: () => navigate(`${editBase}?step=7`),
    },
  ];

  return (
    <div className="rounded-md p-4 mb-4" style={{
      background: 'var(--bg-card)', border: '1px solid var(--border-color)',
    }}>
      {/* Header: breadcrumbs (auxiliary buttons merged into the step 4 health diagnosis report) */}
      <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
        <div className="text-xs">
          {topic && (
            <span className="text-muted">
              <button type="button"
                      onClick={() => navigate(`/ops/accounts/${topic.user_id}/topics`)}
                      className="hover:underline">
                ← {topic.target || topic.user_email || `用户 #${topic.user_id}`}
              </button>
              <span className="mx-1">/</span>
              <span className="text-primary font-medium">{topic.name}</span>
            </span>
          )}
          {err && <span className="text-red-500">{err}</span>}
        </div>
      </div>

      {/* Stepper bar */}
      <div className="flex items-stretch gap-1 overflow-x-auto scrollbar-hide">
        {steps.map((s, i) => (
          <StepCell key={s.key}
                    step={s}
                    active={s.key === active}
                    isLast={i === steps.length - 1} />
        ))}
      </div>
    </div>
  );
}

const STATUS_PALETTE: Record<StepStatus, { fg: string; bg: string; line: string; mark: string }> = {
  done:    { fg: '#10b981', bg: 'var(--success-bg)', line: '#10b981', mark: '✓' },
  running: { fg: '#eab308', bg: 'rgba(234,179,8,0.10)',  line: '#eab308', mark: '⋯' },
  failed:  { fg: '#ef4444', bg: 'var(--danger-bg)',  line: '#ef4444', mark: '✗' },
  editing: { fg: '#3b82f6', bg: 'rgba(59,130,246,0.10)', line: '#3b82f6', mark: '✎' },
  idle:    { fg: 'var(--text-muted)', bg: 'var(--bg-tertiary)', line: 'var(--border-color)', mark: '─' },
};

function StepCell({ step, active, isLast }: { step: StepDef; active: boolean; isLast: boolean }) {
  const p = STATUS_PALETTE[step.status];
  return (
    <>
      <button type="button" onClick={step.onClick}
              className="flex-1 min-w-[100px] flex flex-col items-center gap-1 px-2 py-2 rounded transition-colors"
              style={{
                background: active ? p.bg : 'transparent',
                border: active ? `1px solid ${p.line}` : '1px solid transparent',
                cursor: 'pointer',
              }}>
        <div className="flex items-center justify-center w-6 h-6 rounded-full font-semibold text-xs"
             style={{ background: p.bg, color: p.fg, border: `1px solid ${p.line}` }}>
          {p.mark}
        </div>
        <div className="text-xs font-medium" style={{ color: active ? p.fg : 'var(--text-secondary)' }}>
          {step.label}
        </div>
        {step.hint && (
          <div className="text-[10px] text-muted">{step.hint}</div>
        )}
      </button>
      {!isLast && (
        <div className="flex items-center px-0.5">
          <div className="w-3 h-px" style={{ background: 'var(--border-color)' }} />
        </div>
      )}
    </>
  );
}
