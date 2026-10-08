// Publish plan "month view" -- view scheduling by month, click a day for its details: bulk-edit the day / add an article /
// edit a row / generate now / replan and regenerate the whole day.
//
// Relation to the "table view" in the same tab: the table view is a long table + weekly pages, suited to reading through and cross-day edits; the month view
// targets per-day ops like "what exactly is scheduled today/on some day, is it enough, draft it now". Both are two layouts of the same
// draft; row structure and validation live in planRow.ts, so there are never two metric definitions.
//
// 2026-09-10 — after merging with the table view into one tab, the draft moved up into planDraft.usePlanDraft:
// edit actions write the shared draft (survives view switches), but still **PUT the whole table immediately**, so "edit then generate now"
// remains a single two-step button. Actions that require persisting to DB (dispatch generation) run saveIfDirty first,
// saving any unsaved changes accumulated in the table view before dispatching.

import { useMemo, useState } from 'react';
import { useRegisterAgentActions } from '../../components/AgentChat/agentActions';
import {
  adminReviewApi, type ExecutionPlan, type PublishPlanItem, type SourceInsights,
} from '../../services/adminReviewApi';
import { contentApi, type ContentDoc } from '../../services/contentApi';
import { DocDetailDrawer } from '../Dashboard/Content';
import { type RowDraft } from './planRow';
import type { PlanDraft, PlanViewItem } from './planDraft';
import { addDays, dayOf, fmtDayLabel, todayISO } from './planWeeks';
import { BulkEditModal, RowActionButton } from './PublishingPlanEditor';
import { Breadcrumb } from '../../components/ui/Breadcrumb';
import { DayWhy } from './WhyBlock';
import { rowReasons, rowWhyShort } from './planWhy';
import { toast } from '../../components/ui/toast';
import { uiConfirm } from '../../components/ui/dialogs';
import { paidVendorOf } from '../../components/publish/publishShared';

const WEEK_HEAD = ['一', '二', '三', '四', '五', '六', '日'];

const pad = (n: number) => String(n).padStart(2, '0');
const isoOf = (y: number, m: number, d: number) => `${y}-${pad(m + 1)}-${pad(d)}`;

/** Calendar cells for a month: Monday first, padded to whole weeks, returns a 6×7 or 5×7 table of ISO dates */
function buildMonthGrid(year: number, month: number): string[][] {
  const first = new Date(year, month, 1);
  const lead = (first.getDay() + 6) % 7;            // Monday=0
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: string[] = [];
  // Tail of the previous month
  const prevDays = new Date(year, month, 0).getDate();
  for (let i = lead - 1; i >= 0; i--) {
    const d = prevDays - i;
    const pm = month === 0 ? 11 : month - 1;
    const py = month === 0 ? year - 1 : year;
    cells.push(isoOf(py, pm, d));
  }
  for (let d = 1; d <= daysInMonth; d++) cells.push(isoOf(year, month, d));
  while (cells.length % 7 !== 0) {
    const idx = cells.length - lead - daysInMonth + 1;
    const nm = month === 11 ? 0 : month + 1;
    const ny = month === 11 ? year + 1 : year;
    cells.push(isoOf(ny, nm, idx));
  }
  const rows: string[][] = [];
  for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7));
  return rows;
}

interface DayStat {
  total: number;
  published: number;    // Published (placed)
  done: number;         // Drafted, awaiting publish
  generating: number;   // Generating
  pending: number;      // Not generated
  issues: number;       // Rows missing required fields; saving would be blocked
}

// Overall status of a day -- priority high to low: items needing a human come first (missing fields > running),
// then tiers by placement progress. Calendar cell backgrounds, the legend and the day-detail title all use this one definition.
type DayKind = 'issue' | 'generating' | 'published' | 'partial' | 'drafted' | 'pending' | 'empty';

const DAY_KIND: Record<DayKind, { label: string; color: string; bg: string }> = {
  issue:      { label: '有行缺必填项', color: 'var(--danger-fg)', bg: 'var(--danger-bg)' },
  generating: { label: '生成中',       color: 'var(--warn-fg)', bg: 'rgba(234,179,8,0.14)' },
  published:  { label: '全部已发布',   color: '#8b5cf6', bg: 'rgba(139,92,246,0.16)' },
  partial:    { label: '部分已发布',   color: 'var(--info-fg)', bg: 'rgba(59,130,246,0.14)' },
  drafted:    { label: '已出稿待发布', color: 'var(--success-fg)', bg: 'var(--success-bg)' },
  pending:    { label: '还有未生成',   color: '#94a3b8', bg: 'rgba(148,163,184,0.14)' },
  empty:      { label: '没有排期',     color: 'var(--text-muted)', bg: 'transparent' },
};

const dayKindOf = (s?: DayStat): DayKind => {
  if (!s || s.total === 0) return 'empty';
  if (s.issues > 0) return 'issue';
  if (s.generating > 0) return 'generating';
  if (s.published === s.total) return 'published';
  if (s.published > 0) return 'partial';
  if (s.pending === 0) return 'drafted';
  return 'pending';
};

export function PlanCalendarTab({
  topicId, plan, draft, canAct, isStaff, insights, onChanged, onRefresh, onAddForDay,
  onGoUndated,
}: {
  topicId: number;
  plan: ExecutionPlan;
  /** Draft layer shared with the table view */
  draft: PlanDraft;
  canAct: boolean;
  /** For tiered display: internal metrics like resource IDs / rotation slots are shown only to ops.
      Can't use canAct -- it is also false for "ops in a read-only embed" and would block ops themselves */
  isStaff: boolean;
  insights: SourceInsights | null;
  /** After persisting the whole table, pass back the new plan; the parent replaces state */
  onChanged: (updated: ExecutionPlan) => void;
  /** When only generation tasks were dispatched (plan unchanged), have the parent refetch once */
  onRefresh: () => void;
  /** Add an article on a given day -- reuses the header "Generate for date" modal with the date prefilled */
  onAddForDay: (day: string) => void;
  /** Destination for "N unscheduled rows": switch to the table view and go to the unscheduled page */
  onGoUndated: () => void;
}) {
  const token = localStorage.getItem('token') || '';
  // Draft rows ⨝ server-derived state: cell colors read doc_status (from polling), date/platform read the draft (may be unsaved)
  const { monitored, items, templates, commit, saveIfDirty } = draft;

  const today = todayISO();
  const [cursor, setCursor] = useState(() => {
    const d = new Date();
    return { y: d.getFullYear(), m: d.getMonth() };
  });
  // null = stay on the month calendar; non-null = drill into a day's detail (replaces the calendar entirely, not attached below it)
  const [picked, setPicked] = useState<string | null>(null);
  // 'gen' / 'regen' are this page's dispatches; the saving busy state lives in the draft layer (draft.busy); `working` below merges both
  const [busy, setBusy] = useState<'' | 'gen' | 'regen'>('');
  const [bulkOpen, setBulkOpen] = useState(false);
  // Draft detail opened in place by "View". The row only has doc_id while the drawer needs the full ContentDoc,
  // so fetch it on open (not prefetched in the list: a dozen-plus rows per day, most never opened).
  const [viewDoc, setViewDoc] = useState<ContentDoc | null>(null);
  const [docLoading, setDocLoading] = useState<number | null>(null);

  const openDoc = async (docId: number) => {
    setDocLoading(docId);
    try {
      setViewDoc(await contentApi.getDoc(docId, token));
    } catch (e: unknown) {
      toast.error(`打不开这篇稿件:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDocLoading(null);
    }
  };

  // ── Agent page actions (batch 1: read-only). **generateDay is not registered** (dispatching a whole day of drafting really spends quota).
  useRegisterAgentActions({
    page: 'admin_plan_calendar',
    scope: 'calendar',
    weight: 20,
    describe: '执行计划书 · 发文计划月视图(按天看排了什么、出稿没)',
    snapshot: {
      当前月: `${cursor.y}-${String(cursor.m + 1).padStart(2, '0')}`,
      选中日: picked || '',
      本月排期数: items.filter((it) => (it.publish_date || '').startsWith(
        `${cursor.y}-${String(cursor.m + 1).padStart(2, '0')}`)).length,
      发文行总数: items.length,
    },
    actions: {
      setMonth: (a) => {
        const mm = String(a.month || '').match(/^(\d{4})-(\d{2})$/);
        if (!mm) throw new Error('月份要写成 2026-08 这种格式');
        setCursor({ y: Number(mm[1]), m: Number(mm[2]) - 1 });
      },
      prevMonth: () => setCursor((c) => (c.m === 0 ? { y: c.y - 1, m: 11 } : { y: c.y, m: c.m - 1 })),
      nextMonth: () => setCursor((c) => (c.m === 11 ? { y: c.y + 1, m: 0 } : { y: c.y, m: c.m + 1 })),
      pickDay: (a) => {
        const d = String(a.date || '');
        if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) throw new Error('日期要写成 2026-08-13 这种格式');
        setPicked(d);
      },
      closeDay: () => setPicked(null),
      openBulkEdit: () => setBulkOpen(true),
      openRowEdit: (a) => {
        const d = String(a.date || picked || '');
        const nth = Number(a.nth || 1);
        const day = items.filter((it) => dayOf(it.publish_date) === d)
                         .sort((x, y) => x.seq - y.seq);
        const row = day[nth - 1];
        if (!row) throw new Error(`${d || '这天'} 没有第 ${nth} 篇`);
        setPicked(d);
        draft.setEditKey(row.localKey);
      },
    },
  });

  // Per-day scheduling and drafting status
  const statByDay = useMemo(() => {
    const m = new Map<string, DayStat>();
    for (const it of items) {
      const day = dayOf(it.publish_date);
      if (!day) continue;
      const s = m.get(day)
        || { total: 0, published: 0, done: 0, generating: 0, pending: 0, issues: 0 };
      s.total += 1;
      if (it.doc_status === 'generating') s.generating += 1;
      else if (it.doc_status === 'published') s.published += 1;
      else if (it.doc_id) s.done += 1;
      else s.pending += 1;
      if (draft.rowIssues.has(it.localKey)) s.issues += 1;
      m.set(day, s);
    }
    return m;
  }, [items, draft.rowIssues]);

  // Get the rows of "the currently selected day" from a plan version (after saving, address them using the version the server just returned)
  const itemsOfDay = (p: ExecutionPlan): PublishPlanItem[] =>
    (p.publishing_plan || []).filter(it => dayOf(it.publish_date) === picked)
                             .sort((a, b) => a.seq - b.seq);

  const grid = useMemo(() => buildMonthGrid(cursor.y, cursor.m), [cursor]);
  const dayItems = useMemo(
    () => items.filter(it => dayOf(it.publish_date) === picked)
               .sort((a, b) => a.seq - b.seq),
    [items, picked],
  );
  const monthLabel = `${cursor.y} 年 ${cursor.m + 1} 月`;
  const shiftMonth = (delta: number) => setCursor(c => {
    const m = c.m + delta;
    return { y: c.y + Math.floor(m / 12), m: ((m % 12) + 12) % 12 };
  });

  // ── Persist to DB: whole-table PUT (all month-view edit actions go through it) ────────────────
  // Validation and error location are handled uniformly in the draft layer (failures take the user to that row in the table view); this only sends.
  const saveRows = (next: RowDraft[], okMsg: string) => commit(next, okMsg);

  // Pair by localKey, not id: rows just added in the table view have no server uuid yet, so id would miss them
  const dayKeySet = () => new Set(dayItems.map(it => it.localKey));

  // Edit the day -> persist -> then ask whether to draft from the new plan right away (the main "edit then generate now" path).
  // If the day already has drafts, rewrite (otherwise the new plan can't change generated bodies); if all empty, fill-generate.
  const applyBulkPatch = async (patch: (r: RowDraft) => Partial<RowDraft>) => {
    const keys = dayKeySet();
    const stayCount = dayItems.length;
    const docCount = dayItems.filter(it => it.doc_id).length;
    const pubCount = dayItems.filter(it => it.doc_status === 'published').length;
    const hadDocs = docCount > 0;
    const next = draft.rows.map(r => keys.has(r.localKey) ? { ...r, ...patch(r) } : r);
    setBulkOpen(false);
    const saved = await saveRows(next, `已改完 ${stayCount} 篇当天计划`);
    if (!saved) return;
    const go = await uiConfirm(
      hadDocs
        ? `已保存。这一天有 ${docCount} 篇已出稿 —— 现在按新计划重新出稿吗?已有正文会被覆盖。`
          + (pubCount > 0
            ? `\n\n其中 ${pubCount} 篇已发布,默认不动它们;确要重写请单独在该行点 ↻。`
            : '')
        : `已保存。现在就按新计划出这 ${stayCount} 篇稿吗?`,
      { title: hadDocs ? '改完重新出稿' : '改完立即出稿',
        okText: hadDocs ? '重新出稿' : '立即出稿', cancelText: '稍后再说' },
    );
    // Just saved; address by the version the server returned -- new rows in local dayItems have no uuid yet
    if (go) await generateDay(hadDocs, true, itemsOfDay(saved));
  };

  const removeDay = async () => {
    if (!picked) return;
    const label = fmtDayLabel(picked);
    if (!(await uiConfirm(
      `删掉 ${label} 的全部 ${dayItems.length} 行计划?已出稿的文章不会被删除。`,
      { title: '删除当天计划', okText: '删除', danger: true }))) return;
    const keys = dayKeySet();
    const next = draft.rows.filter(r => !keys.has(r.localKey))
                           .map((r, i) => ({ ...r, seq: i }));
    await saveRows(next, `已删除 ${label} 的 ${dayItems.length} 行`);
  };

  const removeOne = async (it: PlanViewItem) => {
    if (!(await uiConfirm('删掉这一行计划?已出稿的文章不会被删除。',
      { title: '删除计划行', okText: '删除', danger: true }))) return;
    const next = draft.rows.filter(r => r.localKey !== it.localKey)
                           .map((r, i) => ({ ...r, seq: i }));
    await saveRows(next, '已删除该行');
  };

  // ── Generate: address the day's plan rows, not limited by the "next N days" window (past days can be replanned/regenerated) ──
  //
  // Regeneration **overwrites the body in place** and resets status to pending review, and the backend ignores article status -- once a
  // published draft is overwritten, the live article no longer matches the one in the DB, and citation attribution drifts. So whole-day regeneration
  // excludes published rows by default; to really rewrite one, use the per-row ↻ (which warns separately).
  const generateDay = async (
    includeExisting: boolean, skipConfirm = false, pool?: PublishPlanItem[],
  ) => {
    const pick = (day: PublishPlanItem[]) => {
      const publishedItems = day.filter(it => it.doc_status === 'published');
      const targets = includeExisting
        ? day.filter(it => it.doc_status !== 'published')
        : day;
      return { publishedItems, ids: targets.map(it => it.id).filter(Boolean) };
    };
    const nothingToDo = (publishedItems: PublishPlanItem[]) => {
      toast.info(includeExisting && publishedItems.length > 0
        ? `这一天 ${publishedItems.length} 篇都已发布,已跳过 —— 确要重写请在该行点 ↻`
        : '没有可出稿的计划行');
    };

    let day = pool ?? (dayItems as PublishPlanItem[]);
    // First compute against the current (possibly unsaved) draft, only to get the article count right in the confirmation text --
    // saving has side effects; the user must not read the text, click "Cancel", and find the plan already changed.
    let { publishedItems, ids } = pick(day);
    if (ids.length === 0) { nothingToDo(publishedItems); return; }
    if (includeExisting && !skipConfirm && !(await uiConfirm(
      `把 ${fmtDayLabel(picked || '')} 的 ${ids.length} 篇重新出稿?`
      + '已出稿的正文会被覆盖、状态打回待审核。'
      + (publishedItems.length > 0
        ? `\n\n这一天另有 ${publishedItems.length} 篇已发布,默认不动它们`
          + '(覆盖已发布稿会让线上内容与库里对不上);确要重写请单独在该行点 ↻。'
        : ''),
      { title: '整天重新出稿', okText: '重新出稿', danger: true }))) return;

    // Persist only after confirmation: dispatch addresses rows by uuid, and the table view may hold unsaved changes;
    // new rows have no uuid before saving, so not saving first would miss them. After saving, recompute from the server's version.
    if (!pool) {
      const { ok, plan: saved } = await saveIfDirty();
      if (!ok) return;
      if (saved) {
        day = itemsOfDay(saved);
        ({ publishedItems, ids } = pick(day));
        if (ids.length === 0) { nothingToDo(publishedItems); return; }
      }
    }
    setBusy(includeExisting ? 'regen' : 'gen');
    try {
      const r = await adminReviewApi.batchGenerateDocs(
        topicId, { item_ids: ids, include_existing: includeExisting }, token);
      const skipPub = includeExisting && publishedItems.length > 0
        ? `(跳过已发布 ${publishedItems.length} 篇)` : '';
      if (r.queued === 0) {
        toast.info(r.skipped_existing > 0
          ? `这一天 ${r.skipped_existing} 篇都已出稿 — 想重写请用「整天重新生成」`
          : `没有可出稿的计划行${skipPub}`);
      } else {
        toast.success(`已派发 ${r.queued} 篇,生成中…`
          + (r.skipped_existing ? `(跳过已出稿 ${r.skipped_existing} 篇)` : skipPub));
      }
      onRefresh();
    } catch (e: unknown) {
      toast.error(`派发失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy('');
    }
  };

  // Per-row regeneration = the escape hatch left after whole-day regeneration skips published drafts: state the cost honestly and let the user decide.
  const regenerateOne = async (it: PlanViewItem) => {
    const isPublished = it.doc_status === 'published';
    if (!(await uiConfirm(
      isPublished
        ? '这篇已经发布出去了。重新出稿会覆盖库里的正文并打回待审核,'
          + '但已投放出去的那篇不会跟着变 —— 线上内容会和库里对不上,'
          + '后续引用归因也会失真。确定要重写吗?'
        : '给这一篇重新出稿?已有正文会被覆盖。',
      { title: isPublished ? '重写已发布的稿' : '重新出稿',
        okText: '重新出稿', danger: isPublished }))) return;
    // This row may carry unsaved changes (template/seed edited); without saving first it would rewrite with old params
    if (!(await saveIfDirty()).ok) return;
    setBusy('regen');
    try {
      const updated = await adminReviewApi.regeneratePlanItem(topicId, it.id, token);
      onChanged(updated);
      toast.success('已派发重新出稿');
    } catch (e: unknown) {
      toast.error(`重新生成失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy('');
    }
  };

  const cellStyle = (day: string, inMonth: boolean) => {
    const kind = dayKindOf(statByDay.get(day));
    const isToday = day === today;
    return {
      background: inMonth ? (kind === 'empty' ? 'var(--bg-card)' : DAY_KIND[kind].bg)
        : 'transparent',
      border: `1px solid ${isToday ? 'var(--accent-primary)' : 'var(--border-color)'}`,
      opacity: inMonth ? 1 : 0.3,
    };
  };

  const pickedStat = picked ? statByDay.get(picked) : undefined;
  const pickedKind = dayKindOf(pickedStat);
  const working = busy !== '' || draft.busy !== '';

  // Rows without a date (or with an invalid date) fit in no calendar cell -- effectively invisible in the month view,
  // yet they still take part in save validation. Don't invent cells on the calendar; a link to the table view is enough.
  const undatedCount = items.filter(it => !dayOf(it.publish_date)).length;
  const undatedBadge = undatedCount > 0 ? (
    <button type="button" onClick={onGoUndated}
            className="text-[11px] px-2 py-0.5 rounded-full"
            style={{ background: 'rgba(234,179,8,0.12)', color: 'var(--warn-fg)',
                     border: '1px solid rgba(234,179,8,0.35)' }}
            title="这些行没有排期日期,月历上放不下 —— 点这里去表视图处理">
      ⚠ 未排期 {undatedCount} 行
    </button>
  ) : null;

  // ── Month calendar (default view when no day is selected) ────────────────────────────
  if (!picked) {
    return (
      <div className="rounded-xl p-3"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <button type="button" onClick={() => shiftMonth(-1)}
                  className="text-xs px-2 py-1 rounded-md"
                  style={{ border: '1px solid var(--border-color)',
                           color: 'var(--text-secondary)' }}>‹ 上月</button>
          <span className="text-sm font-semibold text-primary">{monthLabel}</span>
          <button type="button" onClick={() => shiftMonth(1)}
                  className="text-xs px-2 py-1 rounded-md"
                  style={{ border: '1px solid var(--border-color)',
                           color: 'var(--text-secondary)' }}>下月 ›</button>
          <button type="button"
                  onClick={() => {
                    const d = new Date();
                    setCursor({ y: d.getFullYear(), m: d.getMonth() });
                  }}
                  className="text-xs px-2 py-1 rounded-md"
                  style={{ border: '1px solid var(--border-color)',
                           color: 'var(--accent-primary)' }}>回到本月</button>
          {undatedBadge}
          <button type="button" onClick={() => setPicked(today)}
                  className="text-xs px-2 py-1 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)' }}>
            看今天的计划 →
          </button>
          <div className="flex-1" />
          <span className="text-[10px] text-muted">点某一天进当天计划</span>
        </div>

        {/* Legend -- same definition as the cell backgrounds (DAY_KIND) */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-3 text-[10px]">
          {(['issue', 'generating', 'published', 'partial', 'drafted', 'pending'] as DayKind[])
            .map(k => (
              <span key={k} className="inline-flex items-center gap-1">
                <span className="inline-block w-2.5 h-2.5 rounded-sm"
                      style={{ background: DAY_KIND[k].bg,
                               border: `1px solid ${DAY_KIND[k].color}` }} />
                <span style={{ color: DAY_KIND[k].color }}>{DAY_KIND[k].label}</span>
              </span>
            ))}
          <span className="text-muted">
            格子里:🚀 已发布 · ✓ 已出稿 · ⏳ 生成中 · ○ 未生成 · ⚠ 缺必填项
          </span>
        </div>

        <div className="grid grid-cols-7 gap-1 mb-1">
          {WEEK_HEAD.map(w => (
            <div key={w} className="text-[10px] text-muted text-center py-1">{w}</div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {grid.flat().map(day => {
            const inMonth = Number(day.slice(5, 7)) === cursor.m + 1;
            const s = statByDay.get(day);
            const kind = dayKindOf(s);
            return (
              <button key={day} type="button" onClick={() => setPicked(day)}
                      className="rounded-md p-1.5 text-left min-h-[64px] transition-colors hover:brightness-110"
                      style={cellStyle(day, inMonth)}
                      title={s ? `${fmtDayLabel(day)} · ${s.total} 篇 · ${DAY_KIND[kind].label}`
                        : `${fmtDayLabel(day)} · 没有排期`}>
                <div className="flex items-center gap-1">
                  <span className="text-xs tabular-nums"
                        style={{ color: day === today ? 'var(--accent-primary)'
                          : 'var(--text-primary)',
                          fontWeight: day === today ? 700 : 400 }}>
                    {Number(day.slice(8, 10))}
                  </span>
                  {day === today && (
                    <span className="text-[9px]" style={{ color: 'var(--accent-primary)' }}>今天</span>
                  )}
                  {s && <span className="text-[10px] text-muted ml-auto">{s.total} 篇</span>}
                </div>
                {s && (
                  <div className="mt-1 flex flex-wrap gap-x-1.5 gap-y-0.5 text-[10px] leading-tight">
                    {s.published > 0 && <span style={{ color: '#8b5cf6' }}>🚀{s.published}</span>}
                    {s.done > 0 && <span style={{ color: 'var(--success-fg)' }}>✓{s.done}</span>}
                    {s.generating > 0 && <span style={{ color: 'var(--warn-fg)' }}>⏳{s.generating}</span>}
                    {s.pending > 0 && <span className="text-muted">○{s.pending}</span>}
                    {s.issues > 0 && <span style={{ color: 'var(--danger-fg)' }}>⚠{s.issues}</span>}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // ── Plan detail for one day (drill-down, replaces the calendar entirely) ──────────────────────
  return (
    <div className="space-y-3">
      <Breadcrumb items={[
        { label: '📅 日历', onClick: () => setPicked(null) },
        { label: fmtDayLabel(picked) + (picked === today ? '(今天)' : '') },
      ]} right={
        <span className="inline-flex items-center gap-1">
          <button type="button" onClick={() => setPicked(addDays(picked, -1))}
                  className="text-xs px-2 py-0.5 rounded-md"
                  style={{ border: '1px solid var(--border-color)',
                           color: 'var(--text-secondary)' }}
                  title="看前一天">‹ 前一天</button>
          <button type="button" onClick={() => setPicked(addDays(picked, 1))}
                  className="text-xs px-2 py-0.5 rounded-md"
                  style={{ border: '1px solid var(--border-color)',
                           color: 'var(--text-secondary)' }}
                  title="看后一天">后一天 ›</button>
        </span>
      } />

      <div className="rounded-xl p-3"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="flex items-center gap-2 flex-wrap mb-2">
          <span className="text-[11px] px-2 py-0.5 rounded-full"
                style={{ background: DAY_KIND[pickedKind].bg,
                         color: DAY_KIND[pickedKind].color,
                         border: `1px solid ${DAY_KIND[pickedKind].color}` }}>
            {DAY_KIND[pickedKind].label}
          </span>
          <span className="text-xs text-muted">
            共 {dayItems.length} 篇
            {pickedStat && ` · 🚀 已发布 ${pickedStat.published} · ✓ 已出稿 ${pickedStat.done}`
              + ` · ⏳ 生成中 ${pickedStat.generating} · ○ 未生成 ${pickedStat.pending}`
              + (pickedStat.issues ? ` · ⚠ 待完善 ${pickedStat.issues}` : '')}
          </span>
          <div className="flex-1" />
          {canAct && (
            <button type="button" disabled={working} onClick={() => onAddForDay(picked)}
                    className="text-xs px-2.5 py-1 rounded-md"
                    style={{ border: '1px solid var(--border-color)',
                             color: 'var(--accent-primary)', opacity: working ? 0.5 : 1 }}
                    title="在这一天加一篇(可选模板/参数,能直接出稿)">
              + 加一篇
            </button>
          )}
          {canAct && dayItems.length > 0 && (
            <>
              <button type="button" disabled={working} onClick={() => setBulkOpen(true)}
                      className="text-xs px-2.5 py-1 rounded-md"
                      style={{ border: '1px solid var(--border-color)',
                               color: 'var(--text-primary)', opacity: working ? 0.5 : 1 }}>
                ✎ 批量修改这一天
              </button>
              <button type="button" disabled={working} onClick={() => generateDay(false)}
                      className="text-xs px-2.5 py-1 rounded-md text-white"
                      style={{ background: 'var(--accent-primary)', opacity: working ? 0.5 : 1 }}
                      title="只给这一天还没出稿的行派发">
                {busy === 'gen' ? '派发中…' : '⚡ 立即出稿'}
              </button>
              <button type="button" disabled={working} onClick={() => generateDay(true)}
                      className="text-xs px-2.5 py-1 rounded-md"
                      style={{ border: '1px solid var(--border-color)',
                               color: 'var(--accent-primary)', opacity: working ? 0.5 : 1 }}
                      title="当天重写:已出稿的正文会被覆盖;已发布的稿默认跳过,要重写请单行点 ↻">
                {busy === 'regen' ? '派发中…' : '↻ 整天重新出稿'}
              </button>
              <button type="button" disabled={working} onClick={removeDay}
                      className="text-xs px-2.5 py-1 rounded-md"
                      style={{ border: '1px solid rgba(239,68,68,0.4)', color: 'var(--danger-fg)',
                               opacity: working ? 0.5 : 1 }}>
                删除当天
              </button>
            </>
          )}
        </div>

        {/* Why these articles are scheduled on this day -- collapsed by default, the action bar is already crowded */}
        <DayWhy dayItems={dayItems} insights={insights} isStaff={isStaff} />

        {dayItems.length === 0 ? (
          <div className="py-8 text-center text-xs text-muted">
            这一天没有排期 —— 点右上「+ 加一篇」直接排一篇(可选模板、可立即出稿)
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: 'var(--text-muted)' }}>
                  <th className="py-2 px-2 text-left">#</th>
                  <th className="py-2 px-2 text-left">种子 / 提示词</th>
                  <th className="py-2 px-2 text-left">平台</th>
                  <th className="py-2 px-2 text-left">状态</th>
                  <th className="py-2 px-2 text-left" title="这一行为什么这么排">为什么</th>
                  <th className="py-2 px-2 text-left">模板</th>
                  <th className="py-2 px-2 text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                {dayItems.map((it, i) => {
                  const issue = draft.rowIssues.get(it.localKey) || null;
                  const qs = it.queries || [];
                  const extras = it.extra_platforms || [];
                  // Paid placement channel: new rows use channel, for old rows platform is the channel name
                  const vendor = paidVendorOf(it.channel) || paidVendorOf(it.platform);
                  // Mark published ones separately: whole-day regeneration skips them, so it must be obvious which rows they are
                  const state = it.doc_status === 'generating'
                    ? { label: '⏳ 生成中', c: '#eab308' }
                    : it.doc_status === 'published'
                      ? { label: '🚀 已发布', c: DAY_KIND.published.color }
                      : it.doc_id ? { label: '✓ 已出稿', c: '#10b981' }
                        : { label: '○ 未生成', c: 'var(--text-muted)' };
                  return (
                    <tr key={it.id}
                        style={{ borderTop: '1px solid var(--border-color)',
                                 background: issue ? 'var(--danger-bg)' : undefined }}>
                      <td className="py-2 px-2 tabular-nums text-muted">{i + 1}</td>
                      <td className="py-2 px-2 max-w-[380px]">
                        <div className="truncate text-primary"
                             title={it.seed || qs.join('\n') || it.query}>
                          {it.seed || qs[0] || it.query || '—'}
                        </div>
                        {qs.length > 1 && (
                          <div className="text-[10px] text-muted">共 {qs.length} 条提示词</div>
                        )}
                      </td>
                      <td className="py-2 px-2 whitespace-nowrap"
                          style={{ color: 'var(--text-secondary)' }}>
                        {/* Paid placement rows: the platform value is just the channel name; what matters is which outlet --
                            show the outlet here, and collapse the vendor into a one-character badge (more vendors coming). */}
                        {vendor ? (
                          <>
                            {it.media || '待选媒体'}
                            <span className="ml-1 text-[10px] px-1 py-0.5 rounded"
                                  style={{ background: 'var(--warn-bg)', color: 'var(--warn-fg)' }}
                                  title={vendor.label}>
                              {vendor.code}
                            </span>
                          </>
                        ) : (
                          <>
                            {it.platform || '—'}
                            {it.media && (
                              <span className="ml-1 text-[10px] text-muted">· {it.media}</span>
                            )}
                          </>
                        )}
                        {extras.length > 0 && (
                          <span className="ml-1 text-[10px]"
                                style={{ color: 'var(--accent-primary)' }}
                                title={`同时投放:${extras.join('、')}`}>
                            +{extras.length}
                          </span>
                        )}
                      </td>
                      <td className="py-2 px-2 whitespace-nowrap text-[10px]"
                          style={{ color: issue ? 'var(--danger-fg)' : state.c }} title={issue || undefined}>
                        {issue ? `⚠ ${issue}` : state.label}
                      </td>
                      <td className="py-2 px-2 max-w-[150px]">
                        <div className="truncate text-muted"
                             title={rowReasons(it, { dayItems, insights })
                               .filter(r => !r.staffOnly || isStaff)
                               .map(r => `${r.title}:${r.text}`).join('\n')}>
                          {rowWhyShort(it)}
                        </div>
                      </td>
                      <td className="py-2 px-2 max-w-[180px]">
                        <div className="truncate text-muted" title={it.template_name || ''}>
                          {it.template_name || '—'}
                        </div>
                      </td>
                      <td className="py-2 px-2 text-center whitespace-nowrap">
                        {/* Sizing via RowActionButton (same as the table view) -- previously ✎/↻ were
                            px-1.5 py-0.5 and × had no padding at all, so clicking delete easily hit a neighbor. */}
                        <span className="inline-flex items-center justify-center gap-1.5">
                        {/* Drafted rows open the detail drawer in place -- the same DocDetailDrawer as the article
                            management page, no navigation and no new tab: the selected calendar day,
                            scroll position and month cursor all stay put. */}
                        {it.doc_id && (
                          <RowActionButton
                            label={docLoading === it.doc_id ? '打开中…' : '查看'}
                            title="在本页打开这篇的详情" tone="accent"
                            disabled={docLoading === it.doc_id}
                            onClick={() => openDoc(it.doc_id!)} />
                        )}
                        {canAct && (
                          <>
                            {/* Edit this row -- the same RowEditModal as the table view (rendered by the container),
                                so the month view can edit every field too, without switching to the table */}
                            <RowActionButton label="✎" tone="accent" disabled={working}
                                             title="改这一行(日期/种子/模板/平台/媒体/备注)"
                                             onClick={() => draft.setEditKey(it.localKey)} />
                            <RowActionButton label="↻" tone="accent" disabled={working}
                                             title="重新出稿(会覆写正文并退回审核)"
                                             onClick={() => regenerateOne(it)} />
                            <RowActionButton label="×" tone="danger" disabled={working}
                                             title="删除这一行"
                                             onClick={() => removeOne(it)} />
                          </>
                        )}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {bulkOpen && (
        <BulkEditModal
          count={dayItems.length}
          monitored={monitored}
          seedQueries={plan.seed_queries}
          templates={templates}
          onClose={() => setBulkOpen(false)}
          onApply={applyBulkPatch}
        />
      )}

      {/* Draft detail: in-place drawer. After a status change (approve/reject/publish) the parent refetches the plan,
          so the status column in the day table follows. */}
      {viewDoc && (
        <DocDetailDrawer
          doc={viewDoc}
          onClose={() => setViewDoc(null)}
          onChanged={onRefresh}
        />
      )}
    </div>
  );
}
