// Admin execution plan document -- edit the publish table (draft / insert-style editing) / view (confirmed).
// Route: /ops/topics/:topicId/execution-plan
//
// ── Verb table for this page (unified 2026-09-11; don't write 「生成」 ("generate") back) ────────────────────────
// There used to be **five** buttons all called 「生成」 ("generate"), meaning five different things: generate plan (rebuild the whole table, no articles) /
// confirm and generate articles (dispatch the whole table) / batch generate articles (dispatch by range) / generate on a given date (add row + draft) /
// regenerate plan document (only refresh the snapshot, publish table untouched). People couldn't tell what clicking would do. Now four verbs each own one thing:
//
//   出稿 ("draft")    produce articles -- confirm and draft / batch draft / draft now / redraft / add one
//   铺表 ("lay out")  change the publish plan itself -- relay the publish table (whole-table replacement)
//   重建 ("rebuild")  refresh the plan document snapshot -- rebuild plan snapshot (overview and logs; publish table and existing drafts untouched)
//   重跑 ("rerun")    run the baseline audit -- rerun baseline audit
//
// ★ Status words aren't part of this set: ⏳生成中 / ○未生成 / 生成失败 ("generating / not generated / generation failed") describe **the draft's own writing process**
//   (corresponding to doc_status='generating'), not user actions, and are kept as-is. Mixing them in would make it harder to read.


import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { TopicStepper } from '../../components/TopicStepper';
import { adminReviewApi, type ExecutionPlan } from '../../services/adminReviewApi';
import { aiTelemetryApi } from '../../services/aiTelemetryApi';
import {
  GenerateOptionsModal } from './PublishingPlanEditor';
import { CandidateDrawer } from './CandidateDrawer';
import { TemplateDrawer } from './TemplateDrawer';
import { usePlanDraft } from './planDraft';
import { SourceInsightsPanel } from './SourceInsightsPanel';
import type {} from '../../services/adminReviewApi';
import { PlacementStrategyTab } from './PlacementStrategyTab';
import { useSourceInsights } from './useSourceInsights';
import { PillTabs } from '../../components/ui/Tabs';
import { OverviewSection, ChangelogSection, ExpansionSection } from './planArchive';
import { QuickAddModal, BatchGenerateModal } from './planModals';
import { PlanStatusActions, PublishPlanPane, PublishingPlanSection } from './PublishPlanPane';
import { StartProjectModal } from './StartProjectModal';
import { useAuth } from '../../contexts/AuthContext';
import { uiConfirm } from '../../components/ui/dialogs';
import { useRegisterAgentActions } from '../../components/AgentChat/agentActions';
import { LoadingBlock } from '../../components/ui/Loading';
import {
  dayOf, todayISO } from './planWeeks';


export function AdminExecutionPlan() {
  const { topicId } = useParams();
  const tid = Number(topicId);
  return (
    <div className="space-y-4">
      <PageHead titleKey="admin.executionPlan.title" titleFallback="执行计划书" />
      <TopicStepper topicId={tid} active="plan" />
      <ExecutionPlanView topicId={tid} />
    </div>
  );
}

// Embedded version: no PageHead / TopicStepper, only renders the plan document body.
// Used for inline display in step 5 of the TopicEditor 7-step wizard.


// readOnly is no longer accepted: all actions on this page belong to the customer (see the canAct note below); the true read-only state
// (mode='view', e.g. an agency enterprise's business suspended) is blocked uniformly by the outer fieldset disabled.
export function ExecutionPlanView({ topicId, allowStart = true }: {
  topicId: number;
  /** Allow clicking "Start project". Pass false in true read-only scenarios. */
  allowStart?: boolean;
}) {
  const { t } = useTranslation();
  // The plan document step is **the customer's own deliverable**, with the same features as ops -- rerun / regenerate / edit the publish table /
  // confirm drafting are all given to them. Backend endpoints are open to owner-or-staff (ACTION_WRITE), and costs are charged to
  // the topic payer (drafting KIND_CONTENT_DOC, reruns via telemetry pre-deduction, 402 if it can't be deducted).
  //
  // ★ The criterion must not include readOnly: the customer side passes customerView ("the customer is looking"), always true,
  //   so writing `!readOnly` would lock out every customer -- the step 4 generate button and the start button here each fell into this once.
  const { isStaff } = useAuth();
  const canAct = true;
  // "Start project" shares the same source as above: the customer clicks it. allowStart is left for true read-only scenarios (mode='view') to turn off.
  const canStart = allowStart;
  const token = localStorage.getItem('token') || '';
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Project not started = backend 404 "no execution plan generated". Show a dedicated empty state + start button,
  // instead of throwing the backend's raw English error onto a red banner.
  const [notStarted, setNotStarted] = useState(false);
  // The "monitoring has actually been running all along" status to show in the empty state. Fetched only without a plan; pages with a plan have their own progress.
  // ★ No round id: it's an internal primary key of no use to users (same as the batch run status card in AiTelemetry).
  const [runFacts, setRunFacts] = useState<{ cells: string; money: string } | null>(null);
  const [rerunBusy, setRerunBusy] = useState(false);
  const [regenBusy, setRegenBusy] = useState(false);
  // Starting goes through a config dialog: set the scheduling metric definition first, then lay out the publish table from measured sources after the baseline runs
  const [startOpen, setStartOpen] = useState(false);
  // 2026-09-10 -- "Calendar" and "Publish plan" used to be two parallel tabs that read like two features, but were actually two layouts
  // of the same publishing_plan. Merged into one "Publish plan" tab + month/table view toggle (see
  // PublishPlanPane); four tabs became three, and the "go confirm in the publish plan" yellow bar went away too.
  // Tab order = daily workflow: first the publish plan (what's scheduled today, drafted or not), then source analysis (data),
  // and finally "placement strategy" (conclusions + scheduling basis).
  const [tab, setTab] = useState<'plan' | 'sources' | 'why'>('plan');
  const [inlineEdit, setInlineEdit] = useState(false);  // "edit publish table" toggle in confirmed state
  const [batchOpen, setBatchOpen] = useState(false);    // one-time batch generation dialog
  const [quickOpen, setQuickOpen] = useState(false);    // add-and-generate-on-a-date dialog
  // Date carried over when clicking "+ add one on this day" on a calendar day (empty = the dialog defaults to today)
  const [quickDate, setQuickDate] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  // Source analysis data is lifted to this level and fetched once: the "Source analysis", "Why it's scheduled this way" and "Calendar" views all need it,
  // and fetching per view would run that heavy full-table answer-scanning endpoint three times
  const insights = useSourceInsights(topicId, plan);

  const needsPolling = (p: ExecutionPlan): boolean => {
    if (p.run_status === 'running') return true;
    // confirmed state: keep polling while some articles are not generated yet (plan_item has no draft)
    if (p.status === 'confirmed') {
      const pending = (p.publishing_plan || []).some(
        it => !it.doc_id || it.doc_status === 'generating' || it.doc_status === 'draft',
      );
      if (pending) return true;
    }
    return false;
  };

  const fetchOnce = useCallback(async () => {
    if (!topicId) return;
    try {
      const p = await adminReviewApi.getExecutionPlan(topicId, token);
      setPlan(p);
      setNotStarted(false);
      setErr(null);
      if (!needsPolling(p)) {
        if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // 404 / backend "no execution plan" -> project not started yet; not an error, render the empty state.
      if (/no execution plan/i.test(msg)) {
        setNotStarted(true);
        setErr(null);
      } else {
        setErr(msg);
      }
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    }
  }, [topicId, token]);

  useEffect(() => {
    fetchOnce();
    pollRef.current = window.setInterval(fetchOnce, 3000);
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [fetchOnce]);

  // When the project is "not started yet", fetch the live batch-run status. If it fails, hide the whole line -- it is supplementary,
  // and a failing auxiliary endpoint should not take the start button down with it.
  useEffect(() => {
    if (!notStarted || !topicId) { setRunFacts(null); return; }
    let cancelled = false;
    aiTelemetryApi.getProgress(topicId, token).then(p => {
      if (cancelled || !p.run) return;
      setRunFacts({
        cells: `${p.queries.active} 词 × ${p.engines} 引擎`,
        money: p.run.charged_cents > 0 ? `本轮扣 ${p.run.net_cents} 积分` : '',
      });
    }).catch(() => { if (!cancelled) setRunFacts(null); });
    return () => { cancelled = true; };
  }, [notStarted, topicId, token]);

  const handleStarted = async () => {
    // Refetch immediately after starting; the baseline runs async and polling takes over (the publish table is laid out only after it finishes).
    setStartOpen(false);
    setNotStarted(false);
    await fetchOnce();
    if (!pollRef.current) {
      pollRef.current = window.setInterval(fetchOnce, 3000);
    }
  };

  const editMode = canAct && (plan?.status === 'draft' || inlineEdit);

  // The publish-plan draft and view selection live at **this level**, not inside tab content -- switching tabs
  // unmounts the inner components and unsaved edits would be lost. So they must outlive the tabs.
  // The cost: the draft is also alive under the "Source analysis" and "Placement strategy" tabs, which have whole-table replan actions:
  // after a replan, resetFrom must replace the draft too, otherwise a dirty draft hides the server's new table (see the two places below).
  const [view, setView] = useState<'month' | 'table'>('month');
  // 2026-09-10 — these three used to live in the table view header, but they are **plan-level** actions (re-lay the whole table /
  // insert rows into the plan / manage the template library), unrelated to viewing by day or by row. The consequence was: on the month view,
  // seeing a sparse month and wanting to re-lay meant switching to the table view first. Lifted to this level, both views can reach them.
  const [genOpen, setGenOpen] = useState(false);      // ⚙️ Generate plan (re-lay whole table)
  const [candOpen, setCandOpen] = useState(false);    // 🎯 Candidate topics
  const [tmplOpen, setTmplOpen] = useState(false);    // 📋 Template drawer (global; the row-level one is in the table view)
  const draft = usePlanDraft(plan, topicId, {
    onSaved: (updated) => {
      setPlan(updated);
      // In confirmed state, exit insert-mode editing after saving changes
      if (updated.status !== 'draft') setInlineEdit(false);
    },
    onConfirmed: (updated) => {
      setPlan(updated);
      setInlineEdit(false);
      if (!pollRef.current) pollRef.current = window.setInterval(fetchOnce, 3000);
    },
    // Validation failed: filling required fields is per-field work for the table view, so send the user there with the modal open
    onInvalid: (row) => {
      setTab('plan');
      setView('table');
      draft.setFocusDay(dayOf(row.publish_date));
      draft.setEditKey(row.localKey);
    },
  });

  // ── Agent page actions (batch 1: read-only) ────────────────────────────────────
  // Register only pure UI actions: switch tab, open modal, refresh. Buttons that actually dispatch drafting / spend money
  // ("Confirm and generate articles", "Rerun", "Generate whole day") are **intentionally not registered** -- the agent only takes the user
  // to the button (setTab + open*); a human clicks it.
  // The snapshot holds only aggregates, never the full publishing_plan: it enters the model context every turn and is persisted in session history.
  const planStats = useMemo(() => {
    const rows = plan?.publishing_plan || [];
    const done = rows.filter((r) => r.doc_id && !r.doc_failed).length;
    const failed = rows.filter((r) => r.doc_failed).length;
    const byPlatform: Record<string, number> = {};
    rows.forEach((r) => { const k = r.platform || '未指定'; byPlatform[k] = (byPlatform[k] || 0) + 1; });
    const dates = rows.map((r) => r.publish_date).filter(Boolean).sort();
    return {
      计划状态: plan?.status ?? '无计划',
      排期起: dates[0] ?? '',
      排期止: dates[dates.length - 1] ?? '',
      发文行总数: rows.length,
      已出稿: done,
      生成失败: failed,
      未出稿: rows.length - done - failed,
      按平台: byPlatform,
      基线进度: plan ? `${plan.progress_done}/${plan.progress_total}` : '',
    };
  }, [plan]);

  useRegisterAgentActions({
    page: 'admin_plan',
    scope: 'plan',
    weight: 10,   // When embedded in step 5 of the 7-step wizard, override the outer wizard's page/describe
    describe: '运营工作台 · 执行计划书(发文计划 / 信源分析 / 投放策略 三个页签;'
      + '发文计划页签内还分 月视图 / 表视图)',
    snapshot: { 当前页签: tab, 可编辑发文表: editMode, ...planStats },
    actions: {
      setTab: (a) => {
        const v = String(a.tab || '');
        if (!['plan', 'sources', 'why'].includes(v)) throw new Error('页签名不对');
        setTab(v as typeof tab);
      },
      refresh: () => { void fetchOnce(); },
      ...(canAct ? {
        setInlineEdit: (a) => setInlineEdit(a.on !== false),
        openQuickAdd: (a) => { setQuickDate(a.date ? String(a.date) : null); setQuickOpen(true); },
        openBatchGenerate: () => setBatchOpen(true),
        openStartProject: () => setStartOpen(true),
      } : {}),
    },
  });


  return (
    <div className="space-y-4">
      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {notStarted && (
        <div className="rounded-md p-6 text-center space-y-3"
             style={{ background: 'var(--bg-secondary)',
                      border: '1px dashed var(--border-color)' }}>
          {/* Say monitoring is running first -- "Project not started yet" alone conflicts with the batch-run status card next to it:
              batch runs (drip starts them itself) and the execution plan (someone must press Start) are two separate tracks. */}
          {runFacts && (
            <div className="text-sm text-primary">
              监测已在跑 · {runFacts.cells}
              {runFacts.money && <> · {runFacts.money}</>}
            </div>
          )}
          <div className="text-sm text-secondary">
            {/* With monitoring data already present we can't say "run a baseline audit first" -- the backend reuses any successful batch run
                in the last 7 days (_recent_success_run_id), and placement platforms are computed from all citations aggregated by topic. */}
            {!canStart
              ? '还没有执行方案 — 排好后这里会展示执行计划书与发文表'
              : runFacts
                ? '还没有执行方案 — 启动后直接按已经测到的信源定投放平台,再铺发文表,不用重跑'
                : '还没有执行方案 — 启动后先跑一批基线检测,按实测信源定投放平台再铺发文表'}
          </div>
          {canStart && (
            <button type="button" onClick={() => setStartOpen(true)}
                    className="px-4 py-1.5 text-sm rounded-md text-white"
                    style={{ background: 'var(--accent-primary)' }}>
              ⚡ 启动项目
            </button>
          )}
        </div>
      )}

      {!plan && !err && !notStarted && (
        <LoadingBlock label="正在加载计划书…" hint="拉取执行计划与发文表" />
      )}

      {plan && (
        <div className="space-y-4">
          {/* ── Row 1: tabs + this plan's status + the single primary action ──────────────────
              Status and save/confirm are **present under every tab**, not just the publish plan tab: the draft now outlives
              the tabs (usePlanDraft lives on this component), so the user may carry unsaved changes into "Source analysis"
              -- which is when seeing "● Unsaved" and the save button matters most.
              Keeping the primary action always visible also replaces the old "go to the publish plan tab to confirm" yellow bar. */}
          <PillTabs
            value={tab} onChange={setTab}
            items={[
              { value: 'plan' as const, label: '发文计划' },
              { value: 'sources' as const, label: '信源分析' },
              { value: 'why' as const, label: '投放策略' },
            ]}
            right={<PlanStatusActions
              plan={plan} draft={draft} canAct={canAct} editing={inlineEdit}
              onGoFix={(day, key) => {
                // Filling required fields is per-field work for the table view -- send the user to that row with the modal open
                setTab('plan'); setView('table');
                draft.setFocusDay(day); draft.setEditKey(key);
              }}
              onStartEdit={() => { setTab('plan'); setView('table'); setInlineEdit(true); }}
              onCancelEdit={() => setInlineEdit(false)} />}
          />


          {tab === 'sources' ? (
            <SourceInsightsPanel topicId={topicId} plan={plan} canAct={canAct}
                                 insights={insights} onRegenerated={(updated) => {
                                   // Replanning by source is a **whole-table replacement**: the draft must be replaced too,
                                   // otherwise old edits accumulated in the publish plan tab hide the new table
                                   draft.resetFrom(updated);
                                   setTab('why');   // After replanning, review the new rationale first, then check the table
                                 }} />
          ) : tab === 'why' ? (
            <PlacementStrategyTab topicId={topicId} plan={plan}
                                  insights={insights.data}
                                  insightsLoading={insights.loading}
                                  isStaff={isStaff} canAct={canAct}
                                  perDay={insights.perDay}
                                  onGoSources={() => setTab('sources')}
                                  onRegenerated={draft.resetFrom} />
          ) : (
          <>
          {/* Publish table still empty: projects with a start config wait for the baseline to finish before laying out the table by source */}
          {plan.plan_pending_reason === 'waiting_run' && (
            <div className="rounded-md p-4 text-sm text-secondary"
                 style={{ background: 'var(--bg-secondary)',
                          border: '1px dashed var(--border-color)' }}>
              基线检测进行中({plan.progress_done}/{plan.progress_total} 单元格)——
              跑完后按各引擎实际引用的信源域名占比决定投放平台,再自动铺出发文表。
            </div>
          )}
          {plan.plan_pending_reason === 'no_seeds' && (
            <div className="rounded-md p-4 text-sm"
                 style={{ background: 'rgba(234,179,8,0.10)', color: 'var(--warn-fg)' }}>
              基线跑完了,但没有可用的种子提示词 / 内容模板,排不出发文表 ——
              去主题里补种子词或在模板中心加模板后,用「生成发文计划」重排。
            </div>
          )}
          {/* Run progress moved to the GEO strategy optimization page (RunProgressCard) — the audit baseline belongs to the strategy context */}
          <PublishPlanPane
            plan={plan}
            topicId={topicId}
            draft={draft}
            view={view}
            onView={setView}
            canAct={canAct}
            isStaff={isStaff}
            insights={insights.data}
            editMode={editMode}
            onPlan={setPlan}
            onRefresh={fetchOnce}
            onAddForDay={(day) => { setQuickDate(day || null); setQuickOpen(true); }}
            onOpenGenerate={() => setGenOpen(true)}
            onOpenCandidates={() => setCandOpen(true)}
            onOpenTemplates={() => setTmplOpen(true)}
            onOpenBatch={() => setBatchOpen(true)}
            onRegenSnapshot={async () => {
              if (regenBusy) return;
              if (!(await uiConfirm(t('admin.executionPlan.regenConfirm')))) return;
              setRegenBusy(true); setErr(null);
              try {
                setPlan(await adminReviewApi.regenerateExecutionPlan(topicId, token));
              } catch (e: unknown) {
                setErr(e instanceof Error ? e.message : String(e));
              } finally {
                setRegenBusy(false);
              }
            }}
            onRerun={async () => {
              if (rerunBusy) return;
              setRerunBusy(true); setErr(null);
              try {
                setPlan(await adminReviewApi.rerunTopic(topicId, token));
                if (!pollRef.current) pollRef.current = window.setInterval(fetchOnce, 3000);
              } catch (e: unknown) {
                setErr(e instanceof Error ? e.message : String(e));
              } finally {
                setRerunBusy(false);
              }
            }}
            // Only offer this when the baseline failed / never ran -- otherwise it's noise
            needRerun={plan.status === 'failed' || !plan.run_id}
            readOnlyTable={
              <PublishingPlanSection plan={plan}
                                     onRegenerate={canAct ? async (itemId) => {
                                       try {
                                         const p = await adminReviewApi.regeneratePlanItem(topicId, itemId, token);
                                         setPlan(p);
                                         if (!pollRef.current) {
                                           pollRef.current = window.setInterval(fetchOnce, 3000);
                                         }
                                       } catch (e: unknown) {
                                         setErr(e instanceof Error ? e.message : String(e));
                                       }
                                     } : undefined} />
            }
          />
          {/* ── Archive area: three collapsed-by-default cards merged into one row ────────────────────────────
              Project overview / topic log / generalization log are "consult when verifying" items; each used to take a full row
              stacked above and below the content, squeezing the daily publish plan into the middle. Merged into one row, collapsed they take one line.
              items-start: expanding one card only grows that column, without stretching the other two. */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-start">
            <OverviewSection plan={plan} />
            <ChangelogSection plan={plan} />
            <ExpansionSection plan={plan} />
          </div>
          </>
          )}
        </div>
      )}

      {genOpen && plan && (
        <GenerateOptionsModal
          topicId={topicId}
          templates={draft.templates}
          currentRows={draft.rows.length}
          onClose={() => setGenOpen(false)}
          onGenerated={(updated) => {
            // Whole-table replacement: the backend already persisted to DB; unsaved local inline edits are overwritten by the new table (the modal warned)
            draft.resetFrom(updated);
            draft.setFocusDay(todayISO());   // The new plan starts today; go back to the current week
            setView('table');                // A whole table was just laid out; let the user read it through first
            setGenOpen(false);
          }}
        />
      )}

      <CandidateDrawer open={candOpen} topicId={topicId}
                       onClose={() => setCandOpen(false)}
                       onInserted={draft.mergeFrom} />

      {/* Global template drawer (not for a specific row). The row-level one is in the table view, where drawerTarget is always non-null */}
      <TemplateDrawer open={tmplOpen} topicId={topicId}
                      onClose={() => { setTmplOpen(false); void draft.refreshTemplates(); }} />

      {startOpen && (
        <StartProjectModal topicId={topicId}
                           onClose={() => setStartOpen(false)}
                           onStarted={handleStarted} />
      )}

      {quickOpen && plan && (
        <QuickAddModal
          topicId={topicId}
          plan={plan}
          presetDate={quickDate}
          onClose={() => { setQuickOpen(false); setQuickDate(null); }}
          onDone={(updated) => {
            // The backend appended just one row -- merge it into the draft (mergeFrom only adds rows missing locally);
            // with setPlan this row would be hidden by the dirty draft
            draft.mergeFrom(updated);
            setQuickOpen(false);
            setQuickDate(null);
            if (!pollRef.current) {
              pollRef.current = window.setInterval(fetchOnce, 3000);
            }
          }}
        />
      )}

      {batchOpen && plan && (
        <BatchGenerateModal
          topicId={topicId}
          plan={plan}
          onClose={() => setBatchOpen(false)}
          onQueued={() => {
            setBatchOpen(false);
            // After dispatching generation, rely on polling to refresh article status
            if (!pollRef.current) {
              pollRef.current = window.setInterval(fetchOnce, 3000);
            }
          }}
        />
      )}
    </div>
  );
}

// One-click generate-for-date modal — pick date + topic + template/platform/writing params,
// persist the plan row to DB and (optionally) dispatch generation immediately, without hunting for a slot in the editor.
