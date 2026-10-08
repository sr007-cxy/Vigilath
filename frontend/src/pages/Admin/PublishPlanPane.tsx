// Presentation layer of the "Publish plan" tab -- status and primary action / month-table view switch / read-only long table.
//
// Division of labor with ExecutionPlan (container): the container owns data, polling, the draft layer (usePlanDraft) and tabs;
// this file only owns "what the plan looks like and what can be clicked", holding no state that must survive across tabs.
//
// PlanStatusActions renders on the right of the container's tab row (not in this component's DOM); it lives in the same file because
// it and PublishPlanPane describe two halves of the same thing: status + actions / view + content.

import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { type ExecutionPlan } from '../../services/adminReviewApi';
import {
  PublishingPlanEditor, RowEditModal } from './PublishingPlanEditor';
import { type PlanDraft } from './planDraft';
import { PlanCalendarTab } from './PlanCalendarTab';
import type { SourceInsights } from '../../services/adminReviewApi';
import { SectionCard } from '../../components/ui/SectionCard';
import { Menu } from '../../components/ui/Menu';
import {
  buildWeeks, dayOf, daySummary, defaultWeekIdx, fmtDayLabel, groupByDay,
  todayISO } from './planWeeks';
import { WeekPager } from './WeekPager';


export function PlanStatusActions({
  plan, draft, canAct, editing, onGoFix, onStartEdit, onCancelEdit,
}: {
  plan: ExecutionPlan;
  draft: PlanDraft;
  canAct: boolean;
  /** In confirmed state, currently in "insert-mode editing" */
  editing: boolean;
  /** Destination for "N rows need completion": switch to the table view and open that row */
  onGoFix: (day: string, localKey: string) => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
}) {
  const { dirty, busy, rowIssues } = draft;
  const saving = busy !== '';
  const isDraft = plan.status === 'draft';

  return (
    <>
      {/* Draft / confirmed -- replaces the old full-width yellow hint bar */}
      <span className="text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap"
            style={isDraft
              ? { background: 'rgba(234,179,8,0.12)', color: 'var(--warn-fg)',
                  border: '1px solid rgba(234,179,8,0.35)' }
              : { background: 'var(--success-bg)', color: 'var(--success-fg)',
                  border: '1px solid rgba(16,185,129,0.3)' }}
            title={isDraft ? '点右边「确认并出稿」之后才会开始写文章' : '已确认,按排期出稿'}>
        {isDraft ? '草稿' : '已确认'}
      </span>

      {dirty && (
        <span className="text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap"
              style={{ background: 'rgba(234,179,8,0.12)', color: 'var(--warn-fg)',
                       border: '1px solid rgba(234,179,8,0.35)' }}
              title="有改动还没保存 —— 切视图、切页签都不会丢,但要点保存才落库">
          ● 未保存
        </span>
      )}

      {rowIssues.size > 0 && (
        <button type="button"
                onClick={() => {
                  const bad = draft.rows.find(r => rowIssues.has(r.localKey));
                  if (bad) onGoFix(dayOf(bad.publish_date), bad.localKey);
                }}
                className="text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap"
                style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                         border: '1px solid rgba(239,68,68,0.3)' }}
                title="这些行缺必填项,保存会被拦下 — 点这里跳到第一条">
          ⚠ {rowIssues.size} 行待完善
        </button>
      )}

      {canAct && (
        <>
          {/* Toggle for editing the publish table. It used to be buried in the "Project overview" card -- a collapsed-by-default archive card,
              while this is the **only** entry to start editing the table in confirmed state and shouldn't be hidden there.
              Open / cancel / save are three states of one thing; they only read clearly in one place. */}
          {!isDraft && !editing && (
            <button type="button" onClick={onStartEdit}
                    className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                    style={{ background: 'transparent', color: 'var(--text-secondary)',
                             border: '1px solid var(--border-color)' }}
                    title="改这份已确认计划的发文表(提交后不退回草稿)">
              ✎ 编辑发文表
            </button>
          )}
          {!isDraft && editing && (
            <button type="button" onClick={onCancelEdit} disabled={saving}
                    className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                    style={{ background: 'transparent', color: 'var(--text-secondary)',
                             border: '1px solid var(--border-color)',
                             opacity: saving ? 0.5 : 1 }}>
              取消
            </button>
          )}
          {(isDraft || editing) && (
          <button type="button" onClick={() => { void draft.save(); }}
                  disabled={saving || !dirty}
                  className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)',
                           opacity: saving || !dirty ? 0.5 : 1 }}
                  title={dirty ? '把改动写回计划' : '没有未保存的改动'}>
            {busy === 'save' ? '保存中…' : '💾 保存'}
          </button>
          )}
          {isDraft && (
            <button type="button" onClick={() => { void draft.confirm(); }} disabled={saving}
                    className="text-xs px-3 py-1.5 rounded-md text-white whitespace-nowrap"
                    style={{ background: 'var(--accent-primary)', opacity: saving ? 0.5 : 1 }}
                    title="先保存当前计划,再确认并开始出稿">
              {busy === 'confirm' ? '出稿中…' : '✅ 确认并出稿 →'}
            </button>
          )}
        </>
      )}
    </>
  );
}


export function PublishPlanPane({
  plan, topicId, draft, view, onView, canAct, isStaff, insights, editMode,
  onPlan, onRefresh, onAddForDay, readOnlyTable,
  onOpenGenerate, onOpenCandidates, onOpenTemplates,
  onOpenBatch, onRegenSnapshot, onRerun, needRerun,
}: {
  plan: ExecutionPlan;
  topicId: number;
  /** Owned by ExecutionPlanView -- switching tabs unmounts this component, so the draft can't live here */
  draft: PlanDraft;
  view: 'month' | 'table';
  onView: (v: 'month' | 'table') => void;
  canAct: boolean;
  isStaff: boolean;
  insights: SourceInsights | null;
  /** Whether the table view is editable (draft state, or confirmed state after clicking "✎ Edit publish table"). The month view is not governed by it -- same as before the merge */
  editMode: boolean;
  onPlan: (p: ExecutionPlan) => void;
  onRefresh: () => void;
  onAddForDay: (day: string) => void;
  /** Plan-level actions -- tucked into the "⊕ Add" and "⋯" menus on row 2 */
  onOpenGenerate: () => void;      // Re-lay the publish table (whole-table replacement)
  onOpenCandidates: () => void;    // Pick from candidate topics
  onOpenTemplates: () => void;     // Template library
  onOpenBatch: () => void;         // Batch drafting
  onRegenSnapshot: () => void;     // Rebuild plan snapshot
  onRerun: () => void;             // Rerun baseline audit
  /** Offer "Rerun baseline audit" only when the baseline failed / never ran -- otherwise it's noise */
  needRerun: boolean;
  /** Read-only long table (shown by the table view when confirmed and not in edit mode) */
  readOnlyTable: React.ReactNode;
}) {
  // Counts for this screen -- right next to the content they describe, no longer in the collapsed overview card above
  const counts = useMemo(() => {
    const items = draft.items;
    return {
      total: items.length,
      done: items.filter(it => it.doc_id && it.doc_status !== 'generating' && !it.doc_failed).length,
      published: items.filter(it => it.doc_status === 'published').length,
      failed: items.filter(it => it.doc_failed).length,
    };
  }, [draft.items]);

  const tabBtn = (v: 'month' | 'table', label: string, title: string) => (
    <button key={v} type="button" onClick={() => onView(v)} title={title}
            className="text-xs px-2.5 py-1 rounded-md"
            style={{
              background: view === v ? 'var(--bg-tertiary)' : 'transparent',
              color: view === v ? 'var(--accent-primary)' : 'var(--text-secondary)',
              border: `1px solid ${view === v ? 'var(--accent-primary)' : 'var(--border-color)'}`,
            }}>
      {label}
    </button>
  );

  return (
    <div className="space-y-3">
      {/* ── Row 2: how to view this plan + what to add ──────────────────────────
          View switch on the left, counts for this screen in the middle, two menus on the right.
          This used to lay out 7 plan-level actions flat, all equally large and prominent, while only one or two are clicked daily;
          flat doesn't mean "everything is findable", it means "nothing stands out". Tucked into the "⊕ Add" and "⋯" menus,
          split by **what they act on**: adding things goes to the former, everything else to the latter.
          Status and save/confirm have moved up to row 1 (the tab row); not repeated here. */}
      <div className="flex flex-wrap items-center gap-2">
        {tabBtn('month', '📅 月视图', '按天看:这天排了几篇、出稿没、发了没')}
        {tabBtn('table', '▦ 表视图', '按行看:长表 + 周分页,适合通读和跨天批改')}

        <span className="text-[11px] text-muted tabular-nums ml-1">
          {counts.total} 篇
          {counts.done > 0 && ` · 已出稿 ${counts.done}`}
          {counts.published > 0 && ` · 已发布 ${counts.published}`}
        </span>
        {counts.failed > 0 && (
          <span className="text-[11px] px-2 py-0.5 rounded-full tabular-nums whitespace-nowrap"
                style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                         border: '1px solid rgba(239,68,68,0.3)' }}
                title="这些计划行绑了稿件行,但正文没写出来 —— 点开那一行重新出稿">
            ⚠ {counts.failed} 篇生成失败
          </span>
        )}

        <div className="flex-1" />

        {canAct && (
          <>
            <Menu label="⊕ 新增 ▾" title="往这份计划里加内容"
                  items={[
                    { label: '新增一篇',
                      hint: '选日期 + 模板/平台,可以直接出稿',
                      onClick: () => onAddForDay('') },
                    { label: '从候选选题挑',
                      hint: '把候选池里的选题插进计划',
                      onClick: onOpenCandidates },
                  ]} />
            <Menu label="⋯" title="更多动作"
                  items={[
                    { label: '批量出稿',
                      hint: '按天数 / 条数一次性派发,先囤一批稿',
                      onClick: onOpenBatch },
                    { label: '重铺发文表',
                      hint: '按种子×模板重排整张表 —— 整表替换,不产出文章',
                      danger: true,
                      onClick: onOpenGenerate },
                    { label: '模板库',
                      hint: '管内容模板,改完发文行的模板下拉跟着变',
                      onClick: onOpenTemplates },
                    { label: '重建计划书快照',
                      hint: '只刷新概览与日志,发文表和已出的稿都不动',
                      onClick: onRegenSnapshot },
                    ...(needRerun ? [{
                      label: '重跑基线检测',
                      hint: '重新跑一轮检测,按实测信源定投放平台',
                      onClick: onRerun,
                    }] : []),
                  ]} />
          </>
        )}
      </div>

      {view === 'month' ? (
        <PlanCalendarTab topicId={topicId} plan={plan} draft={draft} canAct={canAct}
                         isStaff={isStaff} insights={insights}
                         onChanged={onPlan} onRefresh={onRefresh}
                         onAddForDay={onAddForDay}
                         // Unscheduled rows don't fit on the calendar; the table view pins them at the end of each week page, switch over to see them
                         onGoUndated={() => onView('table')} />
      ) : editMode ? (
        <SectionCard title={plan.status === 'draft' ? '编辑发文计划(草稿)' : '编辑发文计划'}>
          <PublishingPlanEditor
            plan={plan}
            topicId={topicId}
            draft={draft}
            mode={plan.status === 'draft' ? 'draft' : 'inline-edit'}
            onOpenGenerate={onOpenGenerate}
            onOpenCandidates={onOpenCandidates}
            onOpenTemplates={onOpenTemplates}
          />
        </SectionCard>
      ) : readOnlyTable}

      {/* Row edit modal: shared by both views -- clicking ✎ on a row in the month view opens it too */}
      {draft.editKey && (() => {
        const key = draft.editKey!;
        const row = draft.rows.find(x => x.localKey === key);
        if (!row) return null;
        return (
          <RowEditModal
            row={row}
            // The scheduling rationale comes from the backend-persisted raw row (RowDraft in the draft has no machine fields)
            planItem={plan.publishing_plan.find(x => x.id === row.id) || null}
            dayItems={plan.publishing_plan.filter(
              x => dayOf(x.publish_date) === dayOf(row.publish_date))}
            monitored={draft.monitored}
            seedQueries={plan.seed_queries}
            templates={draft.templates}
            tmplById={draft.tmplById}
            geoOptions={draft.geoOptions}
            onPatch={patch => draft.updateRow(key, patch)}
            onClose={() => {
              draft.setEditKey(null);
              // The month view keeps the pre-merge semantics "every calendar action persists to DB directly": closing the modal saves.
              // The table view still accumulates a draft and saves from the header -- that's how long-table bulk editing works there.
              if (view === 'month' && draft.dirty) void draft.save();
            }}
          />
        );
      })()}
    </div>
  );
}


export function PublishingPlanSection({
  plan, onRegenerate,
}: {
  plan: ExecutionPlan;
  onRegenerate?: (itemId: string) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const items = useMemo(
    () => [...(plan.publishing_plan || [])].sort((a, b) =>
      dayOf(a.publish_date) === dayOf(b.publish_date)
        ? a.seq - b.seq
        : dayOf(a.publish_date).localeCompare(dayOf(b.publish_date))),
    [plan.publishing_plan],
  );
  // 2026-07-30 — weekly pages + grouping by day (replaces the full flat list): defaults to the current week.
  // When the week count changes after a plan rebuild, clamp via a derived value; don't write back to state.
  const weeks = useMemo(() => buildWeeks(items.map(it => dayOf(it.publish_date))), [items]);
  const [weekIdxRaw, setWeekIdx] = useState(() => defaultWeekIdx(weeks));
  const weekIdx = Math.min(weekIdxRaw, Math.max(0, weeks.length - 1));
  const week = weeks[weekIdx];
  const undated = useMemo(() => items.filter(it => !dayOf(it.publish_date)), [items]);
  const pageItems = useMemo(
    () => week
      ? items.filter(it => {
          const d = dayOf(it.publish_date);
          return d && week.start <= d && d <= week.end;
        })
      : items,
    [items, week],
  );
  const dayGroups = useMemo(
    () => groupByDay([...pageItems, ...undated], it => it.publish_date),
    [pageItems, undated],
  );
  // 2026-07-31 — rows where "Generate now / Regenerate" was just clicked: before the backend sets the doc to generating,
  // optimistically show "Generating" locally, auto-cleared after 30s (afterwards doc_status=generating is authoritative).
  const [justKicked, setJustKicked] = useState<Record<string, boolean>>({});
  const markKicked = (id: string) => {
    setJustKicked(m => ({ ...m, [id]: true }));
    window.setTimeout(() => setJustKicked(m => {
      const next = { ...m };
      delete next[id];
      return next;
    }), 30000);
  };

  if (items.length === 0) {
    return null;
  }
  const canRegenerate = plan.status === 'confirmed' && onRegenerate !== undefined;
  const PRI_STYLE: Record<string, { c: string; bg: string; label: string }> = {
    high: { c: '#ef4444', bg: 'var(--danger-bg)', label: t('admin.executionPlan.priority.high') },
    med:  { c: '#eab308', bg: 'rgba(234,179,8,0.10)', label: t('admin.executionPlan.priority.med') },
    low:  { c: '#10b981', bg: 'var(--success-bg)', label: t('admin.executionPlan.priority.low') },
  };
  const DOC_STATUS_LABEL: Record<string, string> = {
    generating: t('admin.executionPlan.docStatus.generating'),
    draft: t('admin.executionPlan.docStatus.draft'),
    pending_review: t('admin.executionPlan.docStatus.pending_review'),
    approved: t('admin.executionPlan.docStatus.approved'),
    rejected: t('admin.executionPlan.docStatus.rejected'),
    published: t('admin.executionPlan.docStatus.published'),
  };
  return (
    <SectionCard title={t('admin.executionPlan.section.publishingPlan', { n: items.length })}>
      <p className="text-xs text-muted mb-3">
        {t('admin.executionPlan.publishingPlanHint')}
      </p>
      <div className="mb-2">
        <WeekPager weeks={weeks} idx={weekIdx} onIdx={setWeekIdx}
                   extraLabel={undated.length ? `+ 未排期 ${undated.length} 行` : null} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left" style={{ color: 'var(--text-muted)' }}>
              <th className="py-2 px-2 font-medium">#</th>
              <th className="py-2 px-2 font-medium">主题(种子 / query)</th>
              <th className="py-2 px-2 font-medium text-center">{t('admin.executionPlan.col.coverage')}</th>
              <th className="py-2 px-2 font-medium text-center">{t('admin.executionPlan.col.priority')}</th>
              <th className="py-2 px-2 font-medium">模板/平台</th>
              <th className="py-2 px-2 font-medium">{t('admin.executionPlan.col.doc')}</th>
              {canRegenerate && <th className="py-2 px-2 font-medium text-center">操作</th>}
            </tr>
          </thead>
          <tbody>
            {dayGroups.length === 0 && (
              <tr>
                <td colSpan={canRegenerate ? 7 : 6}
                    className="py-6 text-center text-muted"
                    style={{ borderTop: '1px solid var(--border-color)' }}>
                  本周没有排期
                </td>
              </tr>
            )}
            {dayGroups.map(([day, dayItems]) => {
              const done = dayItems.filter(
                x => x.doc_id && x.doc_status !== 'generating' && !x.doc_failed).length;
              const generating = dayItems.filter(x => x.doc_status === 'generating').length;
              const failed = dayItems.filter(x => x.doc_failed).length;
              const pending = dayItems.length - done - generating - failed;
              const isToday = day === todayISO();
              return [
                <tr key={`day:${day}`}
                    style={{ background: 'var(--bg-secondary)',
                             borderTop: '2px solid var(--border-color)' }}>
                  <td colSpan={canRegenerate ? 7 : 6} className="py-1.5 px-2">
                    <span className="font-semibold"
                          style={{ color: isToday ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
                      ▍{fmtDayLabel(day)}{isToday && '(今天)'}
                    </span>
                    <span className="text-muted ml-2">
                      · {dayItems.length} 篇
                      {daySummary({ done, generating, pending }) &&
                        ` · ${daySummary({ done, generating, pending })}`}
                    </span>
                  </td>
                </tr>,
                ...dayItems.map((it, i) => {
              const pri = PRI_STYLE[it.priority] || PRI_STYLE.med;
              return (
                <tr key={it.id || `${day}:${i}`}
                    style={{ borderTop: '1px solid var(--border-color)' }}>
                  <td className="py-2 px-2 tabular-nums text-muted whitespace-nowrap cursor-pointer"
                      onClick={() => navigate(`/ops/topics/${plan.topic_id}/edit?step=6`)}>
                    #{it.seq + 1}
                    {it.publish_date.includes('T') && (
                      <span className="ml-1 text-[10px]">{it.publish_date.slice(11, 16)}</span>
                    )}
                  </td>
                  <td className="py-2 px-2 max-w-[280px] cursor-pointer"
                      title={[it.seed, ...(it.queries?.length ? it.queries : [it.query])]
                        .filter(Boolean).join('\n')}
                      onClick={() => navigate(`/ops/topics/${plan.topic_id}/edit?step=6`)}>
                    {it.seed ? (
                      <div className="truncate">
                        <span className="text-primary">{it.seed}</span>
                        <span className="text-[10px] text-muted ml-1">种子</span>
                      </div>
                    ) : (
                      <div className="truncate">
                        <span className="text-primary">
                          {(it.queries?.length ? it.queries[0] : it.query)}
                        </span>
                      </div>
                    )}
                    {/* Multi-select prompts: both seed rows and single-query rows may carry a set; show a count badge */}
                    {(it.queries?.length ?? 0) > (it.seed ? 0 : 1) && (
                      <div className="text-[10px] text-muted truncate">
                        覆盖 {it.queries!.length} 条提示词
                      </div>
                    )}
                  </td>
                  <td className="py-2 px-2 text-center tabular-nums">
                    <span style={{ color: it.coverage_pct === 0 ? 'var(--danger-fg)'
                                       : it.coverage_pct < 50 ? '#eab308' : '#10b981' }}>
                      {it.coverage_pct.toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-2 px-2 text-center">
                    <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold"
                          style={{ background: pri.bg, color: pri.c }}>
                      {pri.label}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-[10px]">
                    {it.template_name && (
                      <span className="block text-primary">{it.template_name}</span>
                    )}
                    {it.platform && (
                      <span className="inline-block px-1.5 py-0.5 rounded-full"
                            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
                        {it.platform}
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-2">
                    {it.doc_id ? (
                      // 2026-07-31 — open this draft's detail directly instead of jumping to the content list and making the user search
                      <span className="text-[10px] cursor-pointer"
                            style={{ color: 'var(--accent-primary)' }}
                            title="打开这篇稿件"
                            onClick={() => navigate(
                              `/ops/content-review/${it.doc_id}?topic=${plan.topic_id}`)}>
                        #{it.doc_id}
                        {it.doc_failed ? (
                          <span className="ml-1" style={{ color: 'var(--danger-fg)' }}
                                title={it.doc_error || '生成失败'}>
                            (生成失败)
                          </span>
                        ) : it.doc_status && (
                          <span className="ml-1 text-muted">
                            ({DOC_STATUS_LABEL[it.doc_status] || it.doc_status})
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-muted text-[10px]">
                        {t('admin.executionPlan.docStatus.notGenerated')}
                      </span>
                    )}
                  </td>
                  {canRegenerate && (() => {
                    // Different action by drafting status: not generated -> Generate now; generating -> Generating (can dispatch again);
                    // drafted -> Regenerate.
                    const busy = it.doc_status === 'generating' || !!justKicked[it.id];
                    const act = busy
                      ? { label: '⏳ 生成中', c: '#eab308',
                          title: '正在生成,点击可重新派发一次' }
                      : it.doc_failed
                        ? { label: '↻ 重试', c: '#ef4444',
                            title: `上次生成失败:${it.doc_error || '未知错误'} —— 点击重新出稿` }
                        : it.doc_id
                        ? { label: '↻ 重生', c: 'var(--text-secondary)',
                            title: '重新出稿(会覆写正文并退回审核)' }
                        : { label: '⚡ 立即出稿', c: 'var(--accent-primary)',
                            title: '现在就为这一行出稿' };
                    return (
                      <td className="py-2 px-2 text-center">
                        <button type="button"
                                onClick={() => { markKicked(it.id); onRegenerate?.(it.id); }}
                                className="text-[10px] px-2 py-0.5 rounded-md whitespace-nowrap"
                                style={{ background: 'transparent', color: act.c,
                                         border: '1px solid var(--border-color)' }}
                                title={act.title}>
                          {act.label}
                        </button>
                      </td>
                    );
                  })()}
                </tr>
              );
                }),
              ];
            })}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}
