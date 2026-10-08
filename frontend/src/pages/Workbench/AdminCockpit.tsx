// Project progress — the admin main entry, showing each topic's progress through the GEO pipeline on one screen.
// Route: /ops/cockpit (workbench default page)
// Instead of making admins click through the sidebar stage by stage, lay out all topics + show each stage's status with a chip
// (done / in progress / pending / error / not started); admins click a chip to jump to the detail page.

import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import type { TFunction } from 'i18next';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { Pagination } from '../../components/Pagination';
import { UiSelect } from '../../components/ui/Select';
import { useOverlayDismiss } from '../../components/ui/useOverlayDismiss';
import { adminReviewApi, type StageState, type TopicReviewListItem } from '../../services/adminReviewApi';
import { authApi } from '../../services/authApi';

type StageKey = 'submit' | 'review' | 'solution' | 'plan' | 'content' | 'insight';

const STAGE_ORDER: StageKey[] = ['submit', 'review', 'solution', 'plan', 'content', 'insight'];

// Sortable columns; null = unsorted, keeping the backend's original order.
type SortKey = 'id' | 'topic' | 'customer' | 'progress';
type SortDir = 'asc' | 'desc';

const STATE_COLOR: Record<StageState, { c: string; bg: string; border: string }> = {
  done: { c: 'var(--wb-status-fg)', bg: 'var(--wb-status-bg)', border: 'var(--wb-status-border)' },
  running: { c: 'var(--wb-status-strong-fg)', bg: 'var(--wb-status-strong-bg)', border: 'var(--wb-status-strong-border)' },
  pending: { c: 'var(--wb-status-fg)', bg: 'var(--wb-status-bg)', border: 'var(--wb-line-strong)' },
  blocked: { c: 'var(--wb-status-strong-fg)', bg: 'var(--wb-status-strong-bg)', border: 'var(--wb-status-strong-border)' },
  idle: { c: 'var(--wb-status-muted-fg)', bg: 'var(--wb-status-muted-bg)', border: 'var(--wb-line)' },
};

function deriveStages(t: TopicReviewListItem): Record<StageKey, { state: StageState; to: string }> {
  const tid = t.topic_id;
  const sub = t.submission_status;
  // stage 1 submit: done if there's a topic.
  // stage 2 review: inferred from submission_status.
  // stage 3-6: backend admin_review.py already computes them from each table's state (idle/running/done/blocked/pending),
  //           the frontend reads the fields directly; when the backend lacks a field Pydantic falls back to 'idle'.
  const reviewRaw: StageState =
    sub === 'approved' ? 'done' :
      sub === 'rejected' ? 'blocked' :
        sub === 'pending' ? 'pending' : 'idle';

  // Workflow-style pipeline — backward infer: any stage whose state is non-idle (done / running /
  // pending / blocked) implies the upstream already happened, even if the upstream DB row is missing. Promote the upstream
  // idle to done, avoiding the mismatch "downstream done, upstream not started".
  //
  // This rule replaces the old "forward gate" (upstream not done -> downstream forced idle).
  // With old data/missing rows the forward gate would force genuinely completed downstream stages to idle —
  // e.g. user Zhang, topic_id=2: the plan row was never generated, but 70 content articles were published
  // and telemetry succeeded; the plan's "missing sample" shouldn't drag content down to not started.
  const rawByKey: Record<StageKey, StageState> = {
    submit: 'done',
    review: reviewRaw,
    solution: t.diagnose_status,
    plan: t.plan_status,
    content: t.content_status,
    insight: t.insight_status,
  };
  const effective: Record<StageKey, StageState> = { ...rawByKey };
  let downstreamProgressed = false;
  for (let i = STAGE_ORDER.length - 1; i >= 0; i--) {
    const key = STAGE_ORDER[i];
    if (downstreamProgressed && effective[key] === 'idle') {
      effective[key] = 'done';
    }
    if (effective[key] !== 'idle') downstreamProgressed = true;
  }
  // Forward gate: results verification depends on content being published — nothing published means no results to verify.
  // Backend _insight_state judges done directly from last_run_status === 'success', which, when the batch has finished
  // but drafts are still under review/unpublished, causes the visual glitch "stage 6 done, stage 5 not done".
  // Here, when content !== done, insight done is forced down to running (waiting for content to be published).
  if (effective.content !== 'done' && effective.insight === 'done') {
    effective.insight = 'running';
  }
  // User preference: the project progress board doesn't show "error"; blocked is always shown as "in progress".
  // For the real failure state check the stage detail pages (execution plan / review / monitoring pages all restore the raw status).
  for (const key of STAGE_ORDER) {
    if (effective[key] === 'blocked') effective[key] = 'running';
  }

  // All 6 stage chips point to the corresponding step of the "Profile" main flow; only this one main flow is kept.
  //   submit (brand and topic creation)            -> step 1 settings / materials
  //   review (diagnosis and plan pre-assessment)   -> step 3 prompts (seeds + full view of queries)
  //   solution (GEO strategy optimization plan)    -> step 4 health diagnosis report
  //   plan (execution strategy and planning)       -> step 5 plan (inline)
  //   content (content publishing and review)      -> step 6 copy review (inline)
  //   insight (results verification and updates)   -> step 7 monitoring feedback (inline)
  const base = `/ops/topics/${tid}/edit`;
  return {
    submit: { state: effective.submit, to: `${base}?step=1` },
    review: { state: effective.review, to: `${base}?step=3` },
    solution: { state: effective.solution, to: `${base}?step=4` },
    plan: { state: effective.plan, to: `${base}?step=5` },
    content: { state: effective.content, to: `${base}?step=6` },
    insight: { state: effective.insight, to: `${base}?step=7` },
  };
}

export function AdminCockpit() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const [topics, setTopics] = useState<TopicReviewListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // Search / filter / sort -- all done on the frontend: the list endpoint returns all topics at once and the volume is small (hundreds),
  // not worth adding backend pagination params; switch to backend filtering if it really reaches thousands.
  const [kw, setKw] = useState('');
  // Current stage = the first "not completed" of the 6 stages; 'alldone' when all are complete.
  const [stageFilter, setStageFilter] = useState<'' | StageKey | 'alldone'>('');
  const [stateFilter, setStateFilter] = useState<'' | StageState>('');
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  // "New project" entry: the same create-account modal as the "Profile" page (AdminAccounts.tsx); on success
  // jump to /ops/accounts/{id}/topics?new=1, and AdminAccountTopics automatically opens the new topic editor.
  const [modalOpen, setModalOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);

  useEffect(() => {
    adminReviewApi.listTopicReviews(token)
      .then(setTopics)
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (!modalOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) setModalOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener('keydown', onKey);
    };
  }, [modalOpen, submitting]);

  const openModal = () => {
    setEmail(''); setPassword(''); setName(''); setFormErr(null);
    setModalOpen(true);
  };
  const closeModal = () => {
    if (submitting) return;
    setModalOpen(false);
  };
  // Overlay close checks both mousedown and mouseup, so selecting text in an input and releasing outside the box doesn't close it by mistake
  const dismissModal = useOverlayDismiss(closeModal);
  const handleCreate = async () => {
    setFormErr(null);
    const e = email.trim().toLowerCase();
    const p = password;
    const n = name.trim();
    if (!e) { setFormErr(t('workbench.adminAccounts.createForm.emailRequired')); return; }
    if (p.length < 6) { setFormErr(t('workbench.adminAccounts.createForm.passwordTooShort')); return; }
    setSubmitting(true);
    try {
      const u = await authApi.register(e, p, n || undefined, token);
      setModalOpen(false);
      navigate(`/ops/accounts/${u.id}/topics?new=1`);
    } catch (ex: unknown) {
      setFormErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setSubmitting(false);
    }
  };

  const rows = useMemo(
    () => topics.map(tp => {
      const stages = deriveStages(tp);
      const doneCount = STAGE_ORDER.filter(k => stages[k].state === 'done').length;
      // Current stage: the first non-done of the 6 stages; all done -> 'alldone'.
      const current = STAGE_ORDER.find(k => stages[k].state !== 'done');
      return {
        topic: tp,
        stages,
        doneCount,
        current: (current ?? 'alldone') as StageKey | 'alldone',
        currentState: current ? stages[current].state : null,
      };
    }),
    [topics],
  );

  // Search (topic name / brand name / short name / industry / client email / ID) + stage filter + sorting.
  const viewRows = useMemo(() => {
    const q = kw.trim().toLowerCase().replace(/^#/, '');
    let out = rows.filter(r => {
      if (q) {
        const hay = [
          r.topic.topic_name, r.topic.profile_name, r.topic.company_short_name,
          r.topic.industry, r.topic.user_email, String(r.topic.topic_id),
        ].filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (stageFilter && r.current !== stageFilter) return false;
      // Status filtering judges by "the current stage's status": fully completed rows have no current status, so selecting a status excludes them.
      if (stateFilter && r.currentState !== stateFilter) return false;
      return true;
    });
    if (sortKey) {
      const dir = sortDir === 'asc' ? 1 : -1;
      const txt = (a: string, b: string) => a.localeCompare(b, 'zh-Hans-CN');
      out = [...out].sort((a, b) => dir * (
        sortKey === 'id' ? a.topic.topic_id - b.topic.topic_id
          : sortKey === 'progress'
            // With the same number of completed stages, rows whose current stage is further along come first (deeper progress)
            ? (a.doneCount - b.doneCount) || (STAGE_ORDER.indexOf(a.current as StageKey) - STAGE_ORDER.indexOf(b.current as StageKey))
            : sortKey === 'customer' ? txt(a.topic.user_email, b.topic.user_email)
              : txt(a.topic.profile_name || a.topic.topic_name, b.topic.profile_name || b.topic.topic_name)
      ));
    }
    return out;
  }, [rows, kw, stageFilter, stateFilter, sortKey, sortDir]);

  // Clicking the same column again: ascending -> descending -> cancel (back to the endpoint's original order)
  const toggleSort = (key: SortKey) => {
    setPage(1);
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir(key === 'progress' ? 'desc' : 'asc');
      return;
    }
    if (sortDir === 'asc') setSortDir('desc');
    else { setSortKey(null); setSortDir('asc'); }
  };

  const filtering = !!kw.trim() || !!stageFilter || !!stateFilter;
  const clearFilters = () => { setKw(''); setStageFilter(''); setStateFilter(''); setPage(1); };

  // When page size / filters change, pull an out-of-range page number back into the valid range
  useEffect(() => { setPage(1); }, [pageSize]);
  useEffect(() => { setPage(1); }, [kw, stageFilter, stateFilter]);
  const pagedRows = viewRows.slice((page - 1) * pageSize, page * pageSize);

  // To-do counts (review flow removed):
  //   - Not started = approved but plan is idle (created directly by an admin without clicking "Start")
  //   - Batch run pending = plan_status === 'blocked' (run failed) or 'pending'
  //   - Copy pending review = content_status === 'pending'
  const todoCounts = useMemo(() => {
    let notStarted = 0, runStuck = 0, docPending = 0;
    for (const tp of topics) {
      if (tp.submission_status === 'approved' && tp.plan_status === 'idle') notStarted++;
      if (tp.plan_status === 'blocked' || tp.plan_status === 'pending') runStuck++;
      if (tp.content_status === 'pending') docPending++;
    }
    return { notStarted, runStuck, docPending };
  }, [topics]);

  const completedProjects = useMemo(
    () => rows.filter(row => row.doneCount === STAGE_ORDER.length).length,
    [rows],
  );
  const completionRate = rows.length ? Math.round((completedProjects / rows.length) * 100) : 0;

  return (
    <div className="wb-page">
      <PageHead titleKey="workbench.adminCockpit.title" titleFallback="项目进度" />
      <header className="wb-page-header">
        <div>
          <div className="wb-eyebrow">
            {t('workbench.adminCockpit.overviewLabel', { defaultValue: '交付总览' })}
          </div>
          <h1 className="text-xl font-semibold text-primary">
            {t('workbench.adminCockpit.heading')}
          </h1>
          <p className="wb-subtitle">
            {t('workbench.adminCockpit.subtitle')}
          </p>
        </div>
        <button type="button" onClick={openModal}
                className="wb-button wb-button-primary">
          <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor"
               strokeWidth="1.8" aria-hidden>
            <path d="M8 2.5v11M2.5 8h11" strokeLinecap="round" />
          </svg>
          {t('workbench.adminCockpit.newProject')}
        </button>
      </header>

      {/* Overview band: overall project progress + three kinds of ops to-dos. */}
      {!loading && !err && topics.length > 0 && (
        <div className="wb-overview">
          <div className="wb-overview-primary">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="wb-overview-label">
                  {t('workbench.adminCockpit.totalProjects', { defaultValue: '全部项目' })}
                </div>
                <div className="wb-overview-number mt-1">{rows.length}</div>
              </div>
              <div className="text-right">
                <div className="wb-overview-label">
                  {t('workbench.adminCockpit.completedProjects', { defaultValue: '已完成' })}
                </div>
                <div className="text-sm font-semibold text-primary mt-1 tabular-nums">
                  {completedProjects} / {rows.length}
                </div>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-3">
              <div className="wb-progress-track flex-1" aria-hidden>
                <div className="wb-progress-fill" style={{ width: `${completionRate}%` }} />
              </div>
              <span className="text-[11px] font-semibold text-secondary tabular-nums">
                {completionRate}%
              </span>
            </div>
          </div>
          <OverviewStat count={todoCounts.notStarted}
                        label={t('workbench.adminCockpit.todo.notStarted')}
                        color="var(--wb-ink)"
                        to="/ops/accounts" />
          <OverviewStat count={todoCounts.runStuck}
                        label={t('workbench.adminCockpit.todo.runStuck')}
                        color="var(--wb-ink-secondary)"
                        to="/ops/accounts" />
          <OverviewStat count={todoCounts.docPending}
                        label={t('workbench.adminCockpit.todo.docPending')}
                        color="var(--wb-ink)"
                        to="/ops/content-review?status=pending_review" />
        </div>
      )}

      {loading ? (
        <CockpitSkeleton />
      ) : err ? (
        <StatePanel kind="error" title={t('common.loadFailed', { defaultValue: '项目列表加载失败' })}
                    description={err} />
      ) : rows.length === 0 ? (
        <StatePanel title={t('workbench.adminCockpit.empty')}
                    description={t('workbench.adminCockpit.emptyHint', {
                      defaultValue: '创建第一个项目后，交付阶段和待办会集中显示在这里。',
                    })}
                    action={<button type="button" onClick={openModal}
                                    className="wb-button wb-button-primary mt-4">
                      {t('workbench.adminCockpit.newProject')}
                    </button>} />
      ) : viewRows.length === 0 ? (
        <div className="wb-panel">
          <CockpitToolbar
            kw={kw} setKw={setKw} stageFilter={stageFilter} setStageFilter={setStageFilter}
            stateFilter={stateFilter} setStateFilter={setStateFilter} filtering={filtering}
            clearFilters={clearFilters} shown={viewRows.length} total={rows.length} t={t}
          />
          <StatePanel title={t('workbench.adminCockpit.noMatch')}
                      description={t('workbench.adminCockpit.noMatchHint', {
                        defaultValue: '尝试更换关键词或清除筛选条件。',
                      })}
                      action={<button type="button" onClick={clearFilters}
                                      className="wb-button wb-button-quiet mt-3">
                        {t('workbench.adminCockpit.clearFilters')}
                      </button>} bare />
        </div>
      ) : (
        <div className="wb-panel overflow-hidden">
          <CockpitToolbar
            kw={kw} setKw={setKw} stageFilter={stageFilter} setStageFilter={setStageFilter}
            stateFilter={stateFilter} setStateFilter={setStateFilter} filtering={filtering}
            clearFilters={clearFilters} shown={viewRows.length} total={rows.length} t={t}
          />
          {/* With many rows the list scrolls vertically, the header frozen at the top of the container (same approach as batch-run results / monitoring runs).
              The pagination bar sits outside the scroll container, always visible. */}
          <div className="wb-table-scroll">
            <table className="wb-table">
              <thead>
                <tr>
                  <SortTh label={t('workbench.adminCockpit.colTopic')} sortKey="topic"
                    active={sortKey} dir={sortDir} onSort={toggleSort} align="left"
                    hint={t('workbench.adminCockpit.sortHint')} sticky />
                  <SortTh label={t('workbench.adminCockpit.colCustomer')} sortKey="customer"
                    active={sortKey} dir={sortDir} onSort={toggleSort} align="left"
                    hint={t('workbench.adminCockpit.sortHint')} />
                  <SortTh label={t('workbench.adminCockpit.colProgress')} sortKey="progress"
                    active={sortKey} dir={sortDir} onSort={toggleSort} align="center"
                    hint={t('workbench.adminCockpit.sortHint')} />
                  {STAGE_ORDER.map((s, i) => (
                    <th key={s} className="text-center">
                      <span className="text-muted">{i + 1}.</span>{' '}
                      {t(`workbench.adminCockpit.stage.${s}`)}
                    </th>
                  ))}
                  <th className="text-center">
                    {t('workbench.adminCockpit.colReports')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {pagedRows.map(({ topic, stages, doneCount }) => (
                  <tr key={topic.topic_id}>
                    <td className="wb-sticky-col">
                      <div className="wb-project-cell">
                        <div className="wb-project-mark" aria-hidden>
                          {(topic.profile_name || topic.topic_name || '项').trim().slice(0, 1)}
                        </div>
                        <div className="min-w-0">
                          <div className="font-semibold text-primary truncate max-w-[14rem]">
                            {topic.profile_name || topic.topic_name}
                          </div>
                          <div className="text-[10px] text-muted mt-0.5 truncate max-w-[14rem]">
                            #{topic.topic_id}{topic.industry ? ` · ${topic.industry}` : ''}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="block max-w-[12rem] truncate" title={topic.user_email}>
                        {topic.user_email}
                      </span>
                    </td>
                    {/* Completion progress — the number of completed stages out of 6, also the sort key for the "Progress" column */}
                    <td>
                      <div className="wb-row-progress">
                        <div className="wb-progress-track" aria-hidden>
                          <div className="wb-progress-fill"
                               style={{ width: `${(doneCount / STAGE_ORDER.length) * 100}%` }} />
                        </div>
                        <span className="text-[11px] font-semibold tabular-nums"
                              style={{ color: doneCount === STAGE_ORDER.length ? 'var(--wb-ink)' : 'var(--text-secondary)' }}>
                          {doneCount}/{STAGE_ORDER.length}
                        </span>
                      </div>
                    </td>
                    {STAGE_ORDER.map(key => {
                      const { state, to } = stages[key];
                      const c = STATE_COLOR[state];
                      return (
                        <td key={key} className="text-center">
                          <Link to={to}
                            className="wb-stage-link"
                            style={{
                              '--state-color': c.c,
                              '--state-bg': c.bg,
                              '--state-border': c.border,
                            } as CSSProperties}>
                            <span className="wb-stage-dot" aria-hidden />
                            {t(`workbench.adminCockpit.stageStatus.${state}`)}
                          </Link>
                        </td>
                      );
                    })}
                    {/* Client-view report center entry — admins see exactly the /reports the client sees */}
                    <td className="text-center">
                      <Link to={`/reports?topic=${topic.topic_id}`}
                        className="wb-button wb-button-quiet !min-h-[1.8rem] !px-2 !py-1 text-[11px]">
                        {t('workbench.adminCockpit.viewReports')}
                        <span aria-hidden>↗</span>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="wb-table-footer">
            <Pagination page={page} pageSize={pageSize} total={viewRows.length}
              onPageChange={setPage} onPageSizeChange={setPageSize} />
          </div>
        </div>
      )}

      {/* "New project" modal — same source as "Profile" AdminAccounts.tsx, reusing the same i18n dictionary. */}
      {modalOpen && (
        <div className="wb-modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4"
             {...dismissModal}>
          <div className="wb-modal w-full max-w-md p-5"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-3 mb-4">
              <h2 className="text-base font-semibold text-primary">
                {t('workbench.adminAccounts.createForm.title')}
              </h2>
              <button type="button" onClick={closeModal} disabled={submitting}
                      className="text-muted hover:text-primary text-lg leading-none"
                      aria-label="close">×</button>
            </div>
            <div className="space-y-3">
              <label className="block text-xs text-secondary">
                <span className="block mb-1">
                  {t('workbench.adminAccounts.createForm.emailLabel')} *
                </span>
                <input type="email" autoFocus autoComplete="off" value={email}
                       onChange={e => setEmail(e.target.value)}
                       onKeyDown={e => { if (e.key === 'Enter') handleCreate(); }}
                       placeholder={t('workbench.adminAccounts.createForm.emailPlaceholder')}
                       className="wb-input w-full" />
              </label>
              <label className="block text-xs text-secondary">
                <span className="block mb-1">
                  {t('workbench.adminAccounts.createForm.passwordLabel')} *
                </span>
                <input type="text" autoComplete="off" value={password}
                       onChange={e => setPassword(e.target.value)}
                       onKeyDown={e => { if (e.key === 'Enter') handleCreate(); }}
                       placeholder={t('workbench.adminAccounts.createForm.passwordPlaceholder')}
                       className="wb-input w-full" />
              </label>
              <label className="block text-xs text-secondary">
                <span className="block mb-1">
                  {t('workbench.adminAccounts.createForm.nameLabel')}
                </span>
                <input type="text" autoComplete="off" value={name}
                       onChange={e => setName(e.target.value)}
                       onKeyDown={e => { if (e.key === 'Enter') handleCreate(); }}
                       placeholder={t('workbench.adminAccounts.createForm.namePlaceholder')}
                       className="wb-input w-full" />
              </label>
            </div>
            {formErr && (
              <div className="mt-3 text-xs text-red-500">{formErr}</div>
            )}
            <div className="mt-5 flex items-center justify-end gap-2">
              <button type="button" disabled={submitting} onClick={closeModal}
                      className="wb-button">
                {t('workbench.adminAccounts.createForm.cancel')}
              </button>
              <button type="button" disabled={submitting} onClick={handleCreate}
                      className="wb-button wb-button-primary"
                      style={{ opacity: submitting ? 0.5 : 1 }}>
                {submitting
                  ? t('workbench.adminAccounts.createForm.submitting')
                  : t('workbench.adminAccounts.createForm.submit')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Sortable table header -- clicking toggles ascending -> descending -> cancel; the current sort column shows an arrow.
function SortTh({ label, sortKey, active, dir, onSort, align, hint, sticky = false }: {
  label: string;
  sortKey: SortKey;
  active: SortKey | null;
  dir: SortDir;
  onSort: (k: SortKey) => void;
  align: 'left' | 'center';
  hint: string;
  sticky?: boolean;
}) {
  const on = active === sortKey;
  return (
    <th className={`${sticky ? 'wb-sticky-col' : ''} ${align === 'left' ? 'text-left' : 'text-center'}`}>
      <button type="button" onClick={() => onSort(sortKey)} title={hint}
        className="inline-flex items-center gap-1 hover:text-primary transition-colors"
        style={{ color: on ? 'var(--accent-primary)' : 'inherit' }}>
        {label}
        <span className="text-[9px]" style={{ opacity: on ? 1 : 0.35 }}>
          {on ? (dir === 'asc' ? '▲' : '▼') : '↕'}
        </span>
      </button>
    </th>
  );
}

function OverviewStat({ count, label, color, to }: {
  count: number; label: string; color: string; to: string;
}) {
  return (
    <Link to={to}
          className="wb-overview-stat"
          data-empty={count === 0}
          style={{ '--stat-color': color } as CSSProperties}>
      <div className="flex items-center gap-3 min-w-0">
        <span className="wb-stat-dot" aria-hidden />
        <div className="min-w-0">
          <div className="wb-overview-label truncate">{label}</div>
          <div className="text-lg font-semibold tabular-nums mt-0.5" style={{ color: count ? color : undefined }}>
            {count}
          </div>
        </div>
      </div>
      <svg className="w-3.5 h-3.5 shrink-0 text-muted" fill="none" viewBox="0 0 16 16"
           stroke="currentColor" strokeWidth="1.5" aria-hidden>
        <path d="M3 8h9M9 4.5 12.5 8 9 11.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </Link>
  );
}

function CockpitToolbar({
  kw, setKw, stageFilter, setStageFilter, stateFilter, setStateFilter,
  filtering, clearFilters, shown, total, t,
}: {
  kw: string;
  setKw: (value: string) => void;
  stageFilter: '' | StageKey | 'alldone';
  setStageFilter: (value: '' | StageKey | 'alldone') => void;
  stateFilter: '' | StageState;
  setStateFilter: (value: '' | StageState) => void;
  filtering: boolean;
  clearFilters: () => void;
  shown: number;
  total: number;
  t: TFunction;
}) {
  return (
    <div className="wb-toolbar">
      <label className="wb-field flex-1 min-w-[15rem] max-w-[24rem]">
        <span className="wb-field-label">
          {t('workbench.adminCockpit.searchLabel', { defaultValue: '搜索项目' })}
        </span>
        <span className="relative block">
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted"
               fill="none" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.5" aria-hidden>
            <circle cx="7" cy="7" r="4.25" />
            <path d="m10.25 10.25 3 3" strokeLinecap="round" />
          </svg>
          <input type="search" value={kw} onChange={e => setKw(e.target.value)}
                 placeholder={t('workbench.adminCockpit.searchPlaceholder')}
                 className="wb-input w-full !pl-8" />
        </span>
      </label>
      <label className="wb-field">
        <span className="wb-field-label">{t('workbench.adminCockpit.filterStage')}</span>
        <UiSelect value={stageFilter}
          onChange={v => setStageFilter(v as '' | StageKey | 'alldone')}
          className="wb-select w-[12.5rem]"
          options={[
            { value: '', label: t('workbench.adminCockpit.filterStageAll') },
            ...STAGE_ORDER.map((s, i) => ({
              value: s,
              label: `${i + 1}. ${t(`workbench.adminCockpit.stage.${s}`)}`,
            })),
            { value: 'alldone', label: t('workbench.adminCockpit.filterAllDone') },
          ]} />
      </label>
      <label className="wb-field">
        <span className="wb-field-label">{t('workbench.adminCockpit.filterState')}</span>
        <UiSelect value={stateFilter}
          onChange={v => setStateFilter(v as '' | StageState)}
          className="wb-select w-[8rem]"
          options={[
            { value: '', label: t('workbench.adminCockpit.filterStateAll') },
            { value: 'running', label: t('workbench.adminCockpit.stageStatus.running') },
            { value: 'pending', label: t('workbench.adminCockpit.stageStatus.pending') },
            { value: 'idle', label: t('workbench.adminCockpit.stageStatus.idle') },
          ]} />
      </label>
      {filtering && (
        <button type="button" onClick={clearFilters} className="wb-button wb-button-quiet">
          {t('workbench.adminCockpit.clearFilters')}
        </button>
      )}
      <span className="wb-result-count">
        {t('workbench.adminCockpit.resultCount', { shown, total })}
      </span>
    </div>
  );
}

function CockpitSkeleton() {
  return (
    <div className="wb-panel overflow-hidden" aria-label="加载中" aria-busy="true">
      <div className="wb-toolbar">
        <div className="wb-skeleton h-9 w-72 rounded-lg" />
        <div className="wb-skeleton h-9 w-40 rounded-lg" />
        <div className="wb-skeleton h-9 w-28 rounded-lg" />
      </div>
      <div className="p-4 space-y-3">
        {Array.from({ length: 7 }, (_, i) => (
          <div key={i} className="grid grid-cols-[1.4fr_1fr_0.6fr_repeat(4,0.5fr)] gap-4 items-center">
            <div className="wb-skeleton h-9 rounded-lg" />
            <div className="wb-skeleton h-4 rounded" />
            <div className="wb-skeleton h-2 rounded" />
            {Array.from({ length: 4 }, (__, j) => (
              <div key={j} className="wb-skeleton h-6 rounded-md" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function StatePanel({ title, description, action, kind = 'empty', bare = false }: {
  title: string;
  description?: string;
  action?: ReactNode;
  kind?: 'empty' | 'error';
  bare?: boolean;
}) {
  return (
    <div className={`${bare ? '' : 'wb-panel'} wb-empty`}>
      <div>
        <div className="wb-empty-icon"
             style={kind === 'error' ? { color: 'var(--danger-fg)', background: 'var(--danger-bg)' } : undefined}>
          {kind === 'error' ? (
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.7">
              <path d="M12 8v5m0 3h.01M10.3 3.9 2.8 17a2 2 0 0 0 1.74 3h14.92a2 2 0 0 0 1.74-3L13.7 3.9a2 2 0 0 0-3.4 0Z" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : (
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.7">
              <path d="M5 5h14v14H5zM8 9h8M8 13h5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </div>
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
        {description && <p className="mt-1.5 text-xs leading-5 text-secondary max-w-md">{description}</p>}
        {action}
      </div>
    </div>
  );
}
