// Publish plan "table view" — long table + weekly pages, suited to reading through and cross-day edits.
// Add/remove rows / change date / change query / pick template (from the drawer) / pick platform / notes / ↑↓ reorder.
//
// 2026-09-10 — after merging with the month view (PlanCalendarTab) into one tab, the draft, full template set, row edit modal,
// and save/confirm all moved up into planDraft.usePlanDraft (owned by ExecutionPlan); this file only keeps "how to draw the table".
// Unsaved edits survive view switches precisely because the draft isn't here.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  adminReviewApi, type ExecutionPlan, type PublishPlanItem,
} from '../../services/adminReviewApi';
import { useAuth } from '../../contexts/AuthContext';
import type { PlanDraft } from './planDraft';
import { RowWhy } from './WhyBlock';
import { type ContentTemplate } from '../../services/contentTemplatesApi';
import { TemplateDrawer } from './TemplateDrawer';
import { UiSelect } from '../../components/ui/Select';
import { platformLabel } from '../../components/publish/publishShared';
import { uiConfirm } from '../../components/ui/dialogs';
import {
  addDays, buildWeeks, dayOf, daySummary, defaultWeekIdx, fmtDayLabel, groupByDay,
  todayISO,
} from './planWeeks';
import { WeekPager } from './WeekPager';

// 2026-05-28 — combo option values moved to comboOptions.ts (shared with the "Generate for date" modal)
import { COMBO_DIRECTIONS, COMBO_TYPES } from './comboOptions';
// 2026-08-06 — row structure / validation / conversion moved to planRow.ts, sharing one metric definition with the calendar page
import {
  isRwjRow, OWN_BLOG, PAID_PLATFORMS, platformOptionsFor,
  resolveRowKey, toDatetimeLocal, type RowDraft,
} from './planRow';
// 2026-08-14 — prompt picker now collapses into groups by seed, shared by three modals
import { SeedQueryPicker } from './SeedQueryPicker';
import { useRegisterAgentActions } from '../../components/AgentChat/agentActions';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';

interface Props {
  plan: ExecutionPlan;
  topicId: number;
  /** Draft layer shared with the month view (rows / full template set / row edit modal / save-confirm all live in it) */
  draft: PlanDraft;
  // Full editing in draft state; insert-mode editing in confirmed state (same component, submitting does not revert to draft)
  mode: 'draft' | 'inline-edit';
  /** The three plan-level modal toggles are held by the parent (shared by both views); this is just an entry point for agent actions */
  onOpenGenerate: () => void;
  onOpenCandidates: () => void;
  onOpenTemplates: () => void;
}

export function PublishingPlanEditor({
  plan, topicId, draft, mode, onOpenGenerate, onOpenCandidates, onOpenTemplates,
}: Props) {
  const {
    rows, setRows, templates, tmplById, monitored, monSet, rowIssues, err,
    setEditKey, focusDay, setFocusDay, updateRow, refreshTemplates,
  } = draft;

  // Row-level template drawer (opened from a row to pick a template). The **global** one has moved to the tab action row --
  // only the "pick a template for this row" use remains here, so drawerTarget is always non-null.
  const [drawerOpen, setDrawerOpen] = useState(false);
  // 2026-08-04 — bulk edit: check multiple rows -> change date/template/platform/outlet/notes/prompts/writing preferences at once, or bulk delete.
  const [selKeys, setSelKeys] = useState<string[]>([]);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [drawerTarget, setDrawerTarget] = useState<string | null>(null); // localKey of row

  // 2026-07-30 — weekly pages + grouping by day (replaces the old mixed seed-group × row-pagination structure).
  // Display order fixed as (date, seq); defaults to the current week.
  const sortedRows = useMemo(
    () => [...rows].sort((a, b) =>
      dayOf(a.publish_date) === dayOf(b.publish_date)
        ? a.seq - b.seq
        : dayOf(a.publish_date).localeCompare(dayOf(b.publish_date))),
    [rows],
  );
  const weeks = useMemo(() => buildWeeks(rows.map(r => dayOf(r.publish_date))), [rows]);
  const [weekIdxRaw, setWeekIdx] = useState(() => defaultWeekIdx(weeks));
  // Which week to show: if the draft layer's focusDay is set it decides (new row / candidate insert / validation failure where
  // the container must take the user to that row); otherwise use the manually paged week. Clamp when row changes alter the week count.
  //
  // This is **derived**, not written back via setWeekIdx in an effect -- writing back causes cascading renders,
  // and since weeks update asynchronously with rows, the write-back version relies on "clear focusDay after writing" to stay consistent.
  // The derived version has no such intermediate state: focusDay always points at the target; just clear it on manual paging.
  const focusIdx = focusDay
    ? weeks.findIndex(w => w.start <= focusDay && focusDay <= w.end) : -1;
  const weekIdx = Math.min(focusIdx >= 0 ? focusIdx : weekIdxRaw,
                           Math.max(0, weeks.length - 1));
  const gotoWeek = (i: number) => { setFocusDay(null); setWeekIdx(i); };
  const week = weeks[weekIdx];
  const undatedRows = useMemo(() => sortedRows.filter(r => !dayOf(r.publish_date)), [sortedRows]);
  const pageRows = useMemo(
    () => week
      ? sortedRows.filter(r => {
          const d = dayOf(r.publish_date);
          return d && week.start <= d && d <= week.end;
        })
      : sortedRows,
    [sortedRows, week],
  );
  const dayGroups = useMemo(
    () => groupByDay([...pageRows, ...undatedRows], r => r.publish_date),
    [pageRows, undatedRows],
  );

  const newRow = (): RowDraft => {
    const last = rows[rows.length - 1];
    const date = last ? addDays(last.publish_date, 1) : todayISO();
    const defaultTmpl = templates.find(t => t.scope === 'system') || templates[0];
    const defaultPlat = defaultTmpl?.target_platforms?.[0] || '公众号';
    const localKey = `new-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    // New default is seed-based: seed first, query left empty (ops type the seed text).
    // 2026-08-06 — for legacy plans (query only, seeds all empty, e.g. early topics),
    // new rows used to have both seed and queries empty -> saving was blocked by "fill in at least one of seed or prompt",
    // and the error rendered at the top of a long page, so users only saw "clicking save does nothing". Here we inherit the previous row's
    // prompts so new rows are **valid by default**; ops can change them in the edit modal.
    const inheritQueries = last && !last.seed.trim() ? [...last.queries] : [];
    return {
      id: undefined,
      localKey,
      seq: rows.length,
      publish_date: date,
      seed: last?.seed || '',
      query: '',
      queries: inheritQueries,
      template_id: defaultTmpl ? defaultTmpl.id : null,
      platform: defaultPlat,
      // Check the official-site blog by default (unless the main platform is already the official-site blog)
      extra_platforms: defaultPlat === OWN_BLOG ? [] : [OWN_BLOG],
      channel: '',
      media: '',
      rwj_resource_id: null,
      note: '',
      creation_directions: [],
      copywriting_types: [],
    };
  };

  const removeRow = (key: string) => {
    setRows(rs => rs.filter(r => r.localKey !== key).map((r, i) => ({ ...r, seq: i })));
    setSelKeys(ks => ks.filter(k => k !== key));
  };

  // ── Bulk edit ─────────────────────────────────────────────
  // The checked set only keeps rows that still exist (auto-converges after row deletion / plan rebuild)
  const selSet = useMemo(() => {
    const alive = new Set(rows.map(r => r.localKey));
    return new Set(selKeys.filter(k => alive.has(k)));
  }, [rows, selKeys]);
  const selCount = selSet.size;
  const toggleSel = (key: string) => {
    setSelKeys(ks => ks.includes(key) ? ks.filter(k => k !== key) : [...ks, key]);
  };
  const setSelForKeys = (keys: string[], on: boolean) => {
    setSelKeys(ks => on
      ? [...new Set([...ks, ...keys])]
      : ks.filter(k => !keys.includes(k)));
  };
  // Apply a patch to all selected rows; the patch may be computed per row (e.g. date shift)
  const patchSelected = (patch: (r: RowDraft) => Partial<RowDraft>) => {
    setRows(rs => rs.map(r => selSet.has(r.localKey) ? { ...r, ...patch(r) } : r));
  };
  const removeSelected = async () => {
    if (selCount === 0) return;
    const ok = await uiConfirm(`删除勾选的 ${selCount} 行?保存后生效,已出稿的文章不会被删除。`, {
      title: '批量删除发文行', okText: '删除', danger: true,
    });
    if (!ok) return;
    setRows(rs => rs.filter(r => !selSet.has(r.localKey)).map((r, i) => ({ ...r, seq: i })));
    setSelKeys([]);
  };

  // Reorder within the same day (display is grouped by day; to move across days change the date, not ▲▼)
  const moveRowInDay = (key: string, dir: -1 | 1) => {
    setRows(rs => {
      const target = rs.find(r => r.localKey === key);
      if (!target) return rs;
      const day = dayOf(target.publish_date);
      const dayRows = rs.filter(r => dayOf(r.publish_date) === day)
                        .sort((a, b) => a.seq - b.seq);
      const i = dayRows.findIndex(r => r.localKey === key);
      const j = i + dir;
      if (j < 0 || j >= dayRows.length) return rs;
      const other = dayRows[j];
      return rs.map(r =>
        r.localKey === target.localKey ? { ...r, seq: other.seq }
        : r.localKey === other.localKey ? { ...r, seq: target.seq } : r);
    });
  };

  // Append a row on a given date (the + button on the day group header)
  const addRowForDay = (day: string) => {
    setRows(rs => {
      const row = { ...newRow(), publish_date: day, seq: rs.length };
      return [...rs, row];
    });
    setFocusDay(day);
  };

  // ── Agent page actions ────────────────────────────────────────────────
  // **confirmPlan is not registered**: one click on "Confirm and generate articles" dispatches drafting for dozens to hundreds of rows -- real money, irreversible.
  // The agent only takes the user to the button (setWeek + focusRow + openRowEdit); a human clicks it.
  // Row addressing accepts only localKey / date+nth (see planRow.resolveRowKey); plain indexes are forbidden.
  const [highlightKey, setHighlightKey] = useState<string | null>(null);
  useEffect(() => {
    if (!highlightKey) return;
    const t = setTimeout(() => setHighlightKey(null), 2500);
    return () => clearTimeout(t);
  }, [highlightKey]);

  const focusRow = useCallback((key: string) => {
    const row = rows.find(r => r.localKey === key);
    if (row) setFocusDay(dayOf(row.publish_date) || null);   // Page to that week first
    setHighlightKey(key);
    // Week paging is async; scroll on the next frame
    window.setTimeout(() => {
      document.querySelector(`[data-plan-row="${key}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 60);
  }, [rows, setFocusDay]);

  useRegisterAgentActions({
    page: 'admin_publishing_plan',
    scope: 'planRows',
    weight: 20,
    describe: '执行计划书 · 发文计划长表(按周翻页;行用 key 或 日期+第几篇 定位)',
    // Snapshot **only the current week's** rows: caps candidates at ~35 rows, controlling tokens and shrinking room for "addressing the wrong row"
    snapshot: {
      当前周: week ? `${week.start} ~ ${week.end}` : '(全部)',
      共几周: weeks.length,
      本周行数: pageRows.length,
      已勾选: selCount,
      发文行总数: rows.length,
      本周行: pageRows.slice(0, 35).map((r, i) => ({
        key: r.localKey, 日期: dayOf(r.publish_date), 当天第几篇: i + 1,
        种子: (r.seed || '').slice(0, 30), 平台: r.platform || '', 模板id: r.template_id,
      })),
    },
    actions: {
      setWeek: (a) => {
        if (a.date) { setFocusDay(String(a.date)); return; }
        const n = Number(a.index);
        if (!Number.isFinite(n)) throw new Error('要给 index(第几周,从 0 起)或 date');
        gotoWeek(Math.min(Math.max(0, Math.trunc(n)), Math.max(0, weeks.length - 1)));
      },
      focusRow: (a) => focusRow(resolveRowKey(rows, a as { key?: string; date?: string; nth?: number })),
      openRowEdit: (a) => {
        const k = resolveRowKey(rows, a as { key?: string; date?: string; nth?: number });
        focusRow(k); setEditKey(k);
      },
      closeRowEdit: () => setEditKey(null),
      selectRows: (a) => {
        if (a.weekAll) { setSelForKeys(pageRows.map(r => r.localKey), true); return; }
        if (a.date) {
          const keys = rows.filter(r => dayOf(r.publish_date) === String(a.date)).map(r => r.localKey);
          if (!keys.length) throw new Error(`${a.date} 这天没有发文行`);
          setSelForKeys(keys, true);
          return;
        }
        const want = Array.isArray(a.keys) ? a.keys.map(String) : [];
        if (!want.length) throw new Error('要给 keys,或 date(勾当天),或 weekAll(勾本周页)');
        setSelForKeys(want.map(k => resolveRowKey(rows, { key: k })), true);
      },
      clearSelection: () => setSelKeys([]),
      openBulkEdit: () => setBulkOpen(true),
      openTemplateDrawer: (a) => {
        // If a row is named, open the row-level drawer (this component); otherwise open the global one (on the tab action row)
        if (a.key || a.date) {
          setDrawerTarget(resolveRowKey(rows, a as { key?: string; date?: string; nth?: number }));
          setDrawerOpen(true);
        } else {
          onOpenTemplates();
        }
      },
      openCandidateDrawer: () => onOpenCandidates(),
      openGenerateOptions: () => onOpenGenerate(),

      // ── Local reversible writes: only change the local draft, nothing persists to DB until saved. Execute directly; focusRow highlighting makes it visible on the spot.
      updateRow: (a) => {
        const key = resolveRowKey(rows, a as { key?: string; date?: string; nth?: number });
        const p = (a.patch || {}) as Record<string, unknown>;
        const patch: Partial<RowDraft> = {};
        // Field allowlist + value validation: model-supplied values must be options that really exist in this table, otherwise failing only at save time is too late
        if (p.publish_date != null) patch.publish_date = String(p.publish_date);
        if (p.note != null) patch.note = String(p.note);
        if (p.seed != null) patch.seed = String(p.seed);
        if (p.media != null) patch.media = String(p.media);
        if (p.platform != null) {
          const v = String(p.platform);
          if (!platformOptionsFor([v]).includes(v)) throw new Error(`平台「${v}」不在可选列表里`);
          patch.platform = v;
        }
        if (p.template_id != null) {
          const id = Number(p.template_id);
          if (!templates.some((t2) => t2.id === id)) throw new Error(`模板 ${id} 不存在`);
          patch.template_id = id;
        }
        if (Array.isArray(p.queries)) {
          const qs = p.queries.map(String);
          const bad = qs.filter((q) => !monSet.has(q));
          if (bad.length) throw new Error(`这些不在提示词里:${bad.slice(0, 3).join('、')}`);
          patch.queries = qs;
        }
        if (!Object.keys(patch).length) throw new Error('没有给出任何可改的字段');
        updateRow(key, patch);
        focusRow(key);
      },
      addRowForDay: (a) => {
        const d = String(a.date || '');
        if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) throw new Error('date 要写成 2026-08-20 这种格式');
        addRowForDay(d);
      },
      removeRow: (a) => removeRow(resolveRowKey(rows, a as { key?: string; date?: string; nth?: number })),
      moveRowInDay: (a) => {
        const key = resolveRowKey(rows, a as { key?: string; date?: string; nth?: number });
        const dir = Number(a.dir);
        if (dir !== 1 && dir !== -1) throw new Error('dir 只能是 1(往后)或 -1(往前)');
        moveRowInDay(key, dir as -1 | 1);
        focusRow(key);
      },
      removeSelected: () => { void removeSelected(); },   // Has its own uiConfirm

      // ── DB-persisting writes: uiConfirm inside the executor, text spells out the impact
      saveDraft: async () => {
        const changed = rows.length;
        const ok = await uiConfirm(`保存这份发文计划(共 ${changed} 行)?保存后按新排期出稿。`, {
          title: '保存发文计划', okText: '保存',
        });
        if (!ok) throw new Error('用户取消了保存');
        await draft.save();
      },
    },
  });

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-primary">
          发文计划({mode === 'draft' ? '草稿' : '编辑中'} · {rows.length} 篇)
        </h2>
        <div className="flex-1" />
        {/* ⚙️ Generate plan / 🎯 Candidate topics / 📋 Template drawer used to live here -- they are **plan-level** actions,
            unrelated to viewing by day or by row, yet forced the month view to switch views just to re-lay the whole table. They moved to the tab
            action row (the ✍️ Generate for date row in ExecutionPlan), reachable from both views.
            Only "+ Add a row" stays here: it purely appends a local draft row, a long-table editing technique;
            the month view's counterpart is "+ Add an article on this day" (with date, via QuickAddModal). */}
        <button type="button"
                onClick={() => {
                  const row = newRow();
                  setRows(rs => [...rs, { ...row, seq: rs.length }]);
                  setFocusDay(dayOf(row.publish_date));
                }}
                className="text-xs px-2.5 py-1 rounded-md"
                style={{ background: 'var(--accent-primary)', color: '#fff' }}>
          + 添加一行
        </button>
      </header>

      <WeekPager weeks={weeks} idx={weekIdx} onIdx={gotoWeek}
                 extraLabel={undatedRows.length ? `+ 未排期 ${undatedRows.length} 行` : null} />

      {/* Bulk edit toolbar -- appears once any row is checked */}
      <div className="flex flex-wrap items-center gap-2 text-xs rounded-md px-2 py-1.5"
           style={{
             background: selCount > 0 ? 'rgba(59,130,246,0.08)' : 'var(--bg-secondary)',
             border: `1px solid ${selCount > 0 ? 'var(--accent-primary)' : 'var(--border-color)'}`,
           }}>
        <label className="inline-flex items-center gap-1.5 cursor-pointer"
               title="勾选本页(本周)全部行">
          <input type="checkbox"
                 checked={pageRows.length > 0 && pageRows.every(r => selSet.has(r.localKey))}
                 onChange={e => setSelForKeys(pageRows.map(r => r.localKey), e.target.checked)} />
          <span className="text-muted">选本周({pageRows.length} 行)</span>
        </label>
        <button type="button" onClick={() => setSelForKeys(rows.map(r => r.localKey), true)}
                className="px-1.5 py-0.5 rounded-md text-muted hover:text-primary"
                style={{ border: '1px solid var(--border-color)' }}>
          选全部 {rows.length} 行
        </button>
        {selCount > 0 && (
          <button type="button" onClick={() => setSelKeys([])}
                  className="px-1.5 py-0.5 rounded-md text-muted hover:text-primary"
                  style={{ border: '1px solid var(--border-color)' }}>
            清空勾选
          </button>
        )}
        <div className="flex-1" />
        <span style={{ color: selCount > 0 ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
          已勾选 {selCount} 行
        </span>
        <button type="button" disabled={selCount === 0} onClick={() => setBulkOpen(true)}
                className="px-2.5 py-1 rounded-md text-white"
                style={{ background: 'var(--accent-primary)', opacity: selCount === 0 ? 0.4 : 1 }}>
          ✎ 批量编辑
        </button>
        <button type="button" disabled={selCount === 0} onClick={removeSelected}
                className="px-2.5 py-1 rounded-md"
                style={{ color: 'var(--danger-fg)', border: '1px solid rgba(239,68,68,0.4)',
                         opacity: selCount === 0 ? 0.4 : 1 }}>
          批量删除
        </button>
      </div>

      {err && (
        <div className="rounded-md p-2 text-xs"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
          {err}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ color: 'var(--text-muted)' }}>
              <th className="py-2 px-2 text-left w-6" title="勾选行做批量编辑" />
              <th className="py-2 px-2 text-left">#</th>
              <th className="py-2 px-2 text-left">种子</th>
              <th className="py-2 px-2 text-left">提示词</th>
              <th className="py-2 px-2 text-left">命中%</th>
              <th className="py-2 px-2 text-left">模板</th>
              <th className="py-2 px-2 text-left">平台</th>
              <th className="py-2 px-2 text-left">状态</th>
              <th className="py-2 px-2 text-left">备注</th>
              <th className="py-2 px-2 text-center">操作</th>
            </tr>
          </thead>
          <tbody>
            {(() => {
              // 2026-06-12 — row = read-only compact text; clicking the row (or ✎) opens the unified edit modal; no more inline expansion.
              const renderItem = (r: RowDraft, idxInDay: number): React.ReactNode[] => {
                const tmpl = r.template_id ? tmplById.get(r.template_id) : null;
                const orig = plan.publishing_plan.find(x => x.id === r.id);
                const cov = orig?.coverage_pct ?? 0;
                const issue = rowIssues.get(r.localKey);
                // Rows missing required fields override the hit-rate background -- it decides whether the table can be saved, higher priority
                const tint = issue ? 'var(--danger-bg)'
                  : cov === 0 ? 'rgba(239,68,68,0.06)'
                  : cov < 50 ? 'rgba(234,179,8,0.06)' : 'rgba(16,185,129,0.06)';
                const comboN = r.creation_directions.length + r.copywriting_types.length;
                const docState = orig?.doc_status === 'generating'
                  ? { label: '⏳ 生成中', c: '#eab308' }
                  : orig?.doc_id
                    ? { label: '✓ 已出稿', c: '#10b981' }
                    : { label: '○ 未生成', c: 'var(--text-muted)' };
                return [
                  <tr key={r.localKey}
                      // After the agent edits a row it calls focusRow: scroll into view + highlight 2.5s, so a wrong-row edit is visible immediately
                      data-plan-row={r.localKey}
                      onClick={() => setEditKey(r.localKey)}
                      style={{
                        background: highlightKey === r.localKey ? 'var(--accent-soft, rgba(99,102,241,.18))' : tint,
                        borderTop: '1px solid var(--border-color)',
                        boxShadow: highlightKey === r.localKey ? 'inset 3px 0 0 var(--accent-primary)' : undefined,
                        transition: 'background .2s',
                        cursor: 'pointer',
                      }}>
                    <td className="py-2 px-2" onClick={e => e.stopPropagation()}>
                      <input type="checkbox" checked={selSet.has(r.localKey)}
                             onChange={() => toggleSel(r.localKey)}
                             title="勾选本行做批量编辑" />
                    </td>
                    <td className="py-2 px-2 tabular-nums text-muted whitespace-nowrap">
                      <span className="inline-flex items-center gap-1">
                        {idxInDay + 1}
                        {r.publish_date.includes('T') && (
                          <span className="text-[10px]">{r.publish_date.slice(11, 16)}</span>
                        )}
                        {/* 9px arrows stacked tightly made clicking "up" easily hit "down" -- give each 14px height */}
                        <span className="inline-flex flex-col" onClick={(e) => e.stopPropagation()}>
                          <button type="button"
                                  onClick={(e) => { e.stopPropagation(); moveRowInDay(r.localKey, -1); }}
                                  className="inline-flex items-center justify-center w-4 h-3.5
                                             text-[9px] leading-none text-muted hover:text-primary
                                             hover:bg-[var(--bg-tertiary)] rounded-sm"
                                  title="同一天内上移">▲</button>
                          <button type="button"
                                  onClick={(e) => { e.stopPropagation(); moveRowInDay(r.localKey, 1); }}
                                  className="inline-flex items-center justify-center w-4 h-3.5
                                             text-[9px] leading-none text-muted hover:text-primary
                                             hover:bg-[var(--bg-tertiary)] rounded-sm"
                                  title="同一天内下移">▼</button>
                        </span>
                      </span>
                    </td>
                    <td className="py-2 px-2 max-w-[220px]">
                      <div className="truncate text-primary" title={r.seed}>
                        {r.seed || <span className="text-muted">—</span>}
                      </div>
                    </td>
                    <td className="py-2 px-2 whitespace-nowrap"
                        title={r.queries.join('\n')}>
                      {r.queries.length > 0 ? (
                        <span className="text-primary">{r.queries.length} 条</span>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="py-2 px-2 tabular-nums whitespace-nowrap">
                      <span style={{ color: cov === 0 ? 'var(--danger-fg)' : cov < 50 ? 'var(--warn-fg)' : 'var(--success-fg)' }}>
                        {cov.toFixed(1)}%
                      </span>
                    </td>
                    <td className="py-2 px-2 max-w-[140px]">
                      <div className="truncate" style={{ color: 'var(--text-secondary)' }}
                           title={tmpl?.name}>
                        {tmpl?.name || <span className="text-muted">未选</span>}
                      </div>
                    </td>
                    <td className="py-2 px-2 whitespace-nowrap" style={{ color: 'var(--text-secondary)' }}>
                      {r.platform || <span className="text-muted">—</span>}
                      {r.extra_platforms.length > 0 && (
                        <span className="ml-1 text-[10px]" style={{ color: 'var(--accent-primary)' }}
                              title={`同时投放:${r.extra_platforms.join('、')}`}>
                          +{r.extra_platforms.length}
                        </span>
                      )}
                      {r.media && <span className="text-muted"> / {r.media}</span>}
                    </td>
                    <td className="py-2 px-2 whitespace-nowrap text-[10px]"
                        style={{ color: issue ? 'var(--danger-fg)' : docState.c }}
                        title={issue || undefined}>
                      {issue ? `⚠ ${issue}` : docState.label}
                    </td>
                    <td className="py-2 px-2 max-w-[120px]">
                      <div className="truncate text-muted" title={r.note}>
                        {r.note}
                        {comboN > 0 && (
                          <span className="ml-1 text-[10px]" style={{ color: 'var(--accent-primary)' }}
                                title="本行自定义了创作偏好">偏好×{comboN}</span>
                        )}
                      </div>
                    </td>
                    {/* Swallow onClick: the whole row has "click to open edit", so without swallowing, a slightly
                        off click on delete opens the edit modal. A missed click doing nothing is correct. */}
                    <td className="py-2 px-2 text-center whitespace-nowrap"
                        onClick={(e) => e.stopPropagation()}>
                      <span className="inline-flex items-center justify-center gap-1.5">
                        <RowActionButton label="✎" title="编辑本行"
                                         onClick={() => setEditKey(r.localKey)} />
                        <RowActionButton label="×" title="删除本行" tone="danger"
                                         onClick={() => removeRow(r.localKey)} />
                      </span>
                    </td>
                  </tr>,
                ];
              };

              // Render grouped by day: date header (article count + drafting summary + add one for the day) -> that day's rows
              const out: React.ReactNode[] = [];
              if (dayGroups.length === 0) {
                out.push(
                  <tr key="empty">
                    <td colSpan={10} className="py-6 text-center text-muted"
                        style={{ borderTop: '1px solid var(--border-color)' }}>
                      本周没有排期 — 可点右上「+ 添加一行」或切到其他周
                    </td>
                  </tr>,
                );
                return out;
              }
              for (const [day, dayRows] of dayGroups) {
                const done = dayRows.filter(r => {
                  const o = plan.publishing_plan.find(x => x.id === r.id);
                  return o?.doc_id && o.doc_status !== 'generating';
                }).length;
                const generating = dayRows.filter(r => {
                  const o = plan.publishing_plan.find(x => x.id === r.id);
                  return o?.doc_status === 'generating';
                }).length;
                const pending = dayRows.length - done - generating;
                const isToday = day === todayISO();
                out.push(
                  <tr key={`day:${day || 'undated'}`}
                      style={{ background: 'var(--bg-secondary)',
                               borderTop: '2px solid var(--border-color)' }}>
                    <td colSpan={10} className="py-1.5 px-2">
                      {/* Bulk check by day -- a day is usually the same batch of seeds × templates, often edited together */}
                      <input type="checkbox" className="mr-1.5 align-middle"
                             checked={dayRows.length > 0 && dayRows.every(r => selSet.has(r.localKey))}
                             onChange={e => setSelForKeys(dayRows.map(r => r.localKey), e.target.checked)}
                             title="勾选这一天的全部行" />
                      <span className="font-semibold"
                            style={{ color: isToday ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
                        ▍{day ? fmtDayLabel(day) : '未排期 / 日期异常'}{isToday && '(今天)'}
                      </span>
                      <span className="text-muted ml-2">
                        · {dayRows.length} 篇
                        {daySummary({ done, generating, pending }) &&
                          ` · ${daySummary({ done, generating, pending })}`}
                      </span>
                      {day && (
                        <button type="button"
                                onClick={(e) => { e.stopPropagation(); addRowForDay(day); }}
                                className="ml-2 text-[10px] px-1.5 py-0.5 rounded-md"
                                style={{ color: 'var(--accent-primary)',
                                         border: '1px solid var(--border-color)' }}
                                title="在这一天追加一篇">
                          + 加一篇
                        </button>
                      )}
                    </td>
                  </tr>,
                );
                dayRows.forEach((r, i) => out.push(...renderItem(r, i)));
              }
              return out;
            })()}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-muted">
        共 {rows.length} 篇。同一 Query 可排多行(不同时间 / 不同平台)。
        命中率 0% 行底色浅红,&lt;50% 浅黄,≥50% 浅绿;跨天挪动直接在行编辑里改日期。
      </p>

      {/* Row-level template drawer: pick a template for a row. The global one is on the tab action row, shared by both views */}
      <TemplateDrawer open={drawerOpen} topicId={topicId}
                      onClose={() => { setDrawerOpen(false); setDrawerTarget(null); refreshTemplates(); }}
                      onPick={drawerTarget ? (t) => {
                        const plat = t.target_platforms[0] || '';
                        updateRow(drawerTarget, { template_id: t.id, platform: plat });
                        setDrawerOpen(false);
                        setDrawerTarget(null);
                        refreshTemplates();
                      } : undefined} />

      {bulkOpen && (
        <BulkEditModal
          count={selCount}
          monitored={monitored}
          seedQueries={plan.seed_queries}
          templates={templates}
          onClose={() => setBulkOpen(false)}
          onApply={patch => { patchSelected(patch); setBulkOpen(false); }}
        />
      )}

    </div>
  );
}


// 2026-07-30 — generation options modal: when rebuilding the publish table choose articles per day / template rotation / generation source
// (seed × template, or by strategy plan keywords). Whole-table replacement; the backend persists to DB directly.
export function GenerateOptionsModal({
  topicId, templates, currentRows, onClose, onGenerated,
}: {
  topicId: number;
  templates: ContentTemplate[];
  currentRows: number;
  onClose: () => void;
  onGenerated: (updated: ExecutionPlan) => void;
}) {
  const token = localStorage.getItem('token') || '';
  const [source, setSource] = useState<'seeds' | 'strategy'>('seeds');
  const [perDay, setPerDay] = useState(5);
  const [tmplIds, setTmplIds] = useState<number[]>([]);
  // Placement platforms: allocate by measured cited-source share (default), or keep the template's own target_platforms
  const [platformMode, setPlatformMode] = useState<'sources' | 'template'>('sources');
  const [months, setMonths] = useState(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggleTmpl = (id: number) => {
    setTmplIds(ids => ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id]);
  };

  const handleGenerate = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const updated = await adminReviewApi.generatePlanItems(topicId, {
        source,
        per_day: Math.min(10, Math.max(1, perDay)),
        template_ids: tmplIds,
        platform_mode: platformMode,
        months,
      }, token);
      onGenerated(updated);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = {
    background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
    border: '1px solid var(--border-color)',
  } as const;
  const SCOPE_LABEL: Record<string, string> = {
    system: '系统', tenant: '租户', topic: '主题',
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4"
         style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}>
      <div className="w-[560px] max-w-full rounded-md p-4 flex flex-col gap-3"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                    maxHeight: '85vh', overflowY: 'auto' }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-primary">重铺发文表</h3>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </div>

        <div className="text-xs">
          <div className="text-muted mb-1.5">排期来源</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {([
              { v: 'seeds' as const, title: '种子 × 模板轮换',
                desc: '用主题里已通过的种子提示词按天轮换排期(默认方式)' },
              { v: 'strategy' as const, title: '按策略方案铺',
                desc: '用战略方案的关键词体系按天轮换排期;需先在主题页生成战略方案' },
            ]).map(opt => (
              <button key={opt.v} type="button" onClick={() => setSource(opt.v)}
                      className="text-left rounded-md p-2.5"
                      style={{
                        background: source === opt.v ? 'rgba(59,130,246,0.08)' : 'var(--bg-tertiary)',
                        border: source === opt.v
                          ? '1px solid var(--accent-primary)'
                          : '1px solid var(--border-color)',
                      }}>
                <div className="font-semibold"
                     style={{ color: source === opt.v ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
                  {source === opt.v ? '● ' : '○ '}{opt.title}
                </div>
                <div className="text-[10px] text-muted mt-0.5">{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="text-xs">
          <div className="text-muted mb-1.5">投放平台</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {([
              { v: 'sources' as const, title: '按实测信源分配',
                desc: '按各引擎实际引用的域名占比分平台配额;依据看「信源分析」页签' },
              { v: 'template' as const, title: '沿用模板默认',
                desc: '用每个内容模板自带的目标平台(老行为)' },
            ]).map(opt => (
              <button key={opt.v} type="button" onClick={() => setPlatformMode(opt.v)}
                      className="text-left rounded-md p-2.5"
                      style={{
                        background: platformMode === opt.v ? 'rgba(59,130,246,0.08)' : 'var(--bg-tertiary)',
                        border: platformMode === opt.v
                          ? '1px solid var(--accent-primary)'
                          : '1px solid var(--border-color)',
                      }}>
                <div className="font-semibold"
                     style={{ color: platformMode === opt.v ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
                  {platformMode === opt.v ? '● ' : '○ '}{opt.title}
                </div>
                <div className="text-[10px] text-muted mt-0.5">{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-end gap-6 text-xs">
          <div>
            <label className="block text-muted mb-1">每天几篇(1-10)</label>
            <input type="number" min={1} max={10}
                   className="w-24 rounded-md px-2 py-1.5" style={inputStyle}
                   value={perDay}
                   onChange={e => setPerDay(Number(e.target.value) || 1)} />
          </div>
          <div>
            <div className="text-muted mb-1.5">排期周期</div>
            <div className="flex flex-wrap gap-1.5">
              {[1, 2, 3].map(m => (
                <button key={m} type="button" onClick={() => setMonths(m)}
                        className="px-2.5 py-1 rounded-full text-xs"
                        style={{
                          background: months === m ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                          color: months === m ? '#fff' : 'var(--text-secondary)',
                          border: '1px solid var(--border-color)',
                        }}>
                  {months === m && '✓ '}{m} 个月
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="text-xs">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-muted">模板选择(按点选顺序轮换)</span>
            <span className="text-muted">已选 {tmplIds.length}</span>
            {tmplIds.length > 0 && (
              <button type="button" onClick={() => setTmplIds([])}
                      className="text-[11px] px-1.5 py-0.5 rounded-md"
                      style={{ color: 'var(--text-secondary)',
                               border: '1px solid var(--border-color)' }}>
                清空
              </button>
            )}
          </div>
          <div className="overflow-y-auto rounded-md"
               style={{ border: '1px solid var(--border-color)', maxHeight: 180 }}>
            {templates.length === 0 ? (
              <div className="p-3 text-center text-muted">暂无可用模板</div>
            ) : templates.map(t => {
              const idx = tmplIds.indexOf(t.id);
              return (
                <label key={t.id}
                       className="flex items-center gap-2 px-3 py-1.5 cursor-pointer"
                       style={{ borderBottom: '1px solid var(--border-color)',
                                color: 'var(--text-primary)' }}>
                  <input type="checkbox" checked={idx >= 0}
                         onChange={() => toggleTmpl(t.id)} className="shrink-0" />
                  <span className="flex-1 truncate" title={t.name}>{t.name}</span>
                  {t.kind && <span className="text-[10px] text-muted shrink-0">{t.kind}</span>}
                  <span className="text-[10px] text-muted shrink-0">
                    {SCOPE_LABEL[t.scope] || t.scope}
                  </span>
                  {idx >= 0 && (
                    <span className="text-[10px] shrink-0 tabular-nums"
                          style={{ color: 'var(--accent-primary)' }}>#{idx + 1}</span>
                  )}
                </label>
              );
            })}
          </div>
          <p className="text-[10px] text-muted mt-1">
            不选任何模板时,自动挑选类型互不相同的系统模板;每天的多篇文章按所选模板顺序轮换。
          </p>
        </div>

        {err && (
          <div className="rounded-md p-2 text-xs"
               style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
            {err}
          </div>
        )}

        <div className="rounded-md p-2 text-[11px]"
             style={{ background: 'rgba(234,179,8,0.08)', color: 'var(--warn-fg)',
                      border: '1px solid rgba(234,179,8,0.25)' }}>
          只重排<b>今天及以后</b>的排期,今天之前的历史行原样保留(现有共 {currentRows} 行,
          含未保存的编辑)。今天起已出稿的文章不会被删除,但会与旧行断开关联;
          确认并出稿后才会为新行写文章。每月自动续期沿用本次设置的节奏。
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            取消
          </button>
          <button type="button" onClick={handleGenerate} disabled={busy}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
            {busy ? '铺表中…' : '⚙️ 重铺发文表'}
          </button>
        </div>
      </div>
    </div>
  );
}


// 2026-06-12 — unified row edit modal: all fields of a row are edited here, changes apply immediately, closing means done.
export function RowEditModal({
  row, planItem, dayItems, monitored, seedQueries, templates, tmplById,
  geoOptions, onPatch, onClose,
}: {
  row: RowDraft;
  /** Backend-persisted raw row -- the scheduling rationale (why / origin) hangs off it */
  planItem?: PublishPlanItem | null;
  /** All rows of the day, used to state "this is article N of the day" */
  dayItems?: PublishPlanItem[];
  monitored: string[];
  /** Seed -> prompts under that seed (the picker collapses groups by it) */
  seedQueries?: Record<string, string[]>;
  templates: ContentTemplate[];
  tmplById: Map<number, ContentTemplate>;
  geoOptions: string[];
  onPatch: (patch: Partial<RowDraft>) => void;
  onClose: () => void;
}) {
  const { isStaff } = useAuth();
  const tmpl = row.template_id ? tmplById.get(row.template_id) : null;
  const platOpts = tmpl?.target_platforms?.length
    ? tmpl.target_platforms
    : ['公众号', '小红书', '抖音', '视频号'];

  const inputStyle = {
    background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
    border: '1px solid var(--border-color)',
  } as const;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4"
         style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}>
      {/* Two fixed-height columns, no more whole-modal scrolling:
          left column "how to write" (time/template/platform/outlet + notes + writing preferences), right column "what to write" (prompts).
          It used to be a 640px single column stacking seven blocks vertically; the prompt picker alone took most of the screen,
          and changing a platform meant scrolling up and back. Each column scrolls independently, the bottom action bar is pinned, "Done" is always reachable. */}
      <div className="w-[1040px] max-w-[95vw] rounded-md flex flex-col overflow-hidden"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                    height: 'min(88vh, 760px)' }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 pt-4 pb-3 shrink-0">
          <h3 className="text-sm font-semibold text-primary">编辑发文行(#{row.seq + 1})</h3>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-2">
        {/* Left column */}
        <div className="min-h-0 overflow-y-auto px-4 pb-4 flex flex-col gap-3"
             style={{ borderRight: '1px solid var(--border-color)' }}>

        {/* Basis for scheduling this row (read-only) -- placed before the form: first explain why the machine scheduled it, then let the user edit */}
        <RowWhy item={planItem} dayItems={dayItems} isStaff={isStaff} />

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <label className="block text-muted mb-1">发布时间(精确到分)</label>
            <input type="datetime-local" className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                   value={toDatetimeLocal(row.publish_date)}
                   onChange={e => onPatch({ publish_date: e.target.value })} />
          </div>
          <div>
            {/* Checking prompts auto-fills the owning seed; legacy rows / custom topics can still be typed manually */}
            <label className="block text-muted mb-1">种子提示词(选问题时自动带出)</label>
            <input type="text" className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                   placeholder="种子提示词文本…"
                   value={row.seed}
                   onChange={e => onPatch({ seed: e.target.value })} />
          </div>
          <div>
            <label className="block text-muted mb-1">内容模板</label>
            <UiSelect className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                      value={row.template_id ? String(row.template_id) : ''}
                      onChange={val => {
                        const newId = Number(val) || null;
                        const nt = newId ? tmplById.get(newId) : null;
                        const newPlat = (nt && nt.target_platforms.length && !nt.target_platforms.includes(row.platform))
                          ? nt.target_platforms[0]
                          : row.platform;
                        onPatch({ template_id: newId, platform: newPlat });
                      }}
                      options={[
                        { value: '', label: '—' },
                        ...templates.map(t => ({ value: String(t.id), label: t.name })),
                      ]} />
          </div>
          <div className="col-span-2">
            {/* options include this row's current value: platforms suggested by outlets (Cnblogs/CSDN...) aren't in the fixed pool;
                missing it, the picker would show empty and one click would wipe the platform */}
            <PlatformMultiSelect
              options={platformOptionsFor([...platOpts, row.platform].filter(Boolean))}
              platform={row.platform}
              extras={row.extra_platforms}
              onChange={(platform, extras) => onPatch({ platform, extra_platforms: extras })}
            />
          </div>
          <div>
            <label className="block text-muted mb-1">
              投放媒体/账号(选填 — 审核通过自动建投放任务)
            </label>
            <input type="text" className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                   placeholder={isRwjRow(row) ? '媒体名称(与资源编号配套)' : '账号 / 媒体名'}
                   value={row.media}
                   onChange={e => onPatch({ media: e.target.value })} />
          </div>
          {isRwjRow(row) && (
            <div>
              <label className="block text-muted mb-1">
                代发资源编号(匠)
                {row.platform && !PAID_PLATFORMS.includes(row.platform) && (
                  <span className="text-[10px] ml-1" style={{ color: 'var(--warn-fg)' }}>
                    · 投{row.platform},走付费代发
                  </span>
                )}
              </label>
              <input type="number" className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                     placeholder="如 13464"
                     value={row.rwj_resource_id ?? ''}
                     onChange={e => onPatch({ rwj_resource_id: Number(e.target.value) || null })} />
              <p className="text-[10px] text-muted mt-0.5">
                排期时已按出稿率和价格自动选好;改这里就换成你指定的媒体。
                过审只建「待确认付费」任务,真实扣费仍要在发布中心两步确认。
              </p>
            </div>
          )}
        </div>

        <div>
          {/* Clearly separated from the machine rationale above: that block is the scheduling algorithm's judgment, this is human-written */}
          <label className="block text-xs text-muted mb-1">备注(人工填写)</label>
          <input type="text" className="w-full rounded-md px-2 py-1.5 text-xs" style={inputStyle}
                 placeholder="备注…"
                 value={row.note}
                 onChange={e => onPatch({ note: e.target.value })} />
        </div>

        <div>
          <div className="text-[11px] text-muted mb-1.5">
            自定义本行创作偏好(空则用画像默认;非空则只用本行勾的)
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <ChipMultiSelect
              label={`创作方向(${row.creation_directions.length})`}
              options={COMBO_DIRECTIONS}
              value={row.creation_directions}
              onChange={v => onPatch({ creation_directions: v })}
            />
            <ChipMultiSelect
              label={`文案类型(${row.copywriting_types.length})`}
              options={COMBO_TYPES}
              value={row.copywriting_types}
              onChange={v => onPatch({ copywriting_types: v })}
            />
          </div>
        </div>

        </div>

        {/* Right column: prompts -- the tallest block, give it full column height instead of squeezing it in the middle */}
        <div className="min-h-0 flex flex-col px-4 pb-4 pt-0 lg:pt-0">
          <div className="text-xs text-muted mb-1.5 shrink-0">提示词(决定这篇写什么)</div>
          <div className="flex-1 min-h-0">
            <SeedQueryPicker
              seedQueries={seedQueries}
              monitored={monitored}
              picked={row.queries}
              // Backfill this row's seed with the seed of the first checked prompt; leave the seed alone when all are cleared
              // (legacy rows / manually typed custom topics should not be wiped by one clear)
              onChange={(queries, seed) =>
                onPatch(seed ? { queries, seed } : { queries })}
              filterChips={geoOptions}
              maxHeight={520}
            />
          </div>
        </div>
        </div>

        {/* Pinned action bar: however the columns scroll, "Done" stays put */}
        <div className="flex justify-end px-4 py-3 shrink-0"
             style={{ borderTop: '1px solid var(--border-color)' }}>
          <button type="button" onClick={onClose}
                  className="px-4 py-1.5 text-xs rounded-md text-white"
                  style={{ background: 'var(--accent-primary)' }}>
            完成
          </button>
        </div>
      </div>
    </div>
  );
}


// The "change this field or not" toggle in the bulk edit modal
function BulkToggle({ on, onToggle, label }: {
  on: boolean; onToggle: () => void; label: string;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 cursor-pointer">
      <input type="checkbox" checked={on} onChange={onToggle} />
      <span style={{ color: on ? 'var(--text-primary)' : 'var(--text-muted)' }}>{label}</span>
    </label>
  );
}


// 2026-08-04 — bulk edit modal: only fields with the toggle on are changed; other fields keep each row's value.
// Date supports "set to a day" and "shift all by ±N days"; prompts support append / replace / clear.
/**
 * Icon buttons at the end of table rows (✎ / × / ↻ / View).
 *
 * Extracted for one reason only: **a large enough click target**. Previously ✎ was `px-1.5 py-0.5` and × had no padding,
 * the two 4px apart, with an actual clickable area under 10px wide; and the table view row also has "click to open edit",
 * so a slightly off click on delete opened the edit modal. Now each button is 28×28 with 6px spacing,
 * and the outer <td> swallows clicks -- a missed click does nothing and no longer triggers edit by mistake.
 *
 * The month view's day detail uses the same component, so the two sides don't each write their own sizing (that's how the addDays bug came about).
 */
export function RowActionButton({
  label, title, onClick, disabled, tone = 'normal',
}: {
  label: React.ReactNode;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'normal' | 'accent' | 'danger';
}) {
  const color = tone === 'danger' ? 'var(--danger-fg)'
    : tone === 'accent' ? 'var(--accent-primary)' : 'var(--text-secondary)';
  return (
    <button type="button" disabled={disabled} title={title}
            onClick={(e) => { e.stopPropagation(); onClick(); }}
            className="inline-flex items-center justify-center min-w-7 h-7 px-1.5 rounded-md
                       text-xs hover:bg-[var(--bg-tertiary)] disabled:cursor-not-allowed"
            style={{ color, opacity: disabled ? 0.5 : 1 }}>
      {label}
    </button>
  );
}


// Bulk edit modal -- shared by the table page (multiple checked rows) and the calendar page (whole day); it only produces a patch function,
// and the caller decides which rows it applies to.
export function BulkEditModal({
  count, monitored, seedQueries, templates, onClose, onApply,
}: {
  count: number;
  monitored: string[];
  /** Seed -> prompts under that seed (the picker collapses groups by it) */
  seedQueries?: Record<string, string[]>;
  templates: ContentTemplate[];
  onClose: () => void;
  onApply: (patch: (r: RowDraft) => Partial<RowDraft>) => void;
}) {
  const [dateMode, setDateMode] = useState<'none' | 'set' | 'shift'>('none');
  const [dateValue, setDateValue] = useState(`${todayISO()}T11:00`);
  const [shiftDays, setShiftDays] = useState(1);

  const [tmplOn, setTmplOn] = useState(false);
  const [tmplId, setTmplId] = useState<number | null>(null);

  const [platOn, setPlatOn] = useState(false);
  const [platform, setPlatform] = useState('公众号');
  const [extras, setExtras] = useState<string[]>([OWN_BLOG]);

  const [mediaOn, setMediaOn] = useState(false);
  const [media, setMedia] = useState('');

  const [noteOn, setNoteOn] = useState(false);
  const [note, setNote] = useState('');

  const [qMode, setQMode] = useState<'none' | 'append' | 'replace' | 'clear'>('none');
  const [qPicked, setQPicked] = useState<string[]>([]);

  const [comboOn, setComboOn] = useState(false);
  const [dirs, setDirs] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);

  const inputStyle = {
    background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
    border: '1px solid var(--border-color)',
  } as const;

  // Date shift: keep the original time of day, only move the day
  const shiftOf = (v: string, days: number): string => {
    if (!v) return v;
    const day = addDays(v.slice(0, 10), days);
    return v.includes('T') ? `${day}${v.slice(10, 16)}` : day;
  };

  const nothingPicked = dateMode === 'none' && !tmplOn && !platOn && !mediaOn
    && !noteOn && qMode === 'none' && !comboOn;

  const handleApply = () => {
    onApply((r: RowDraft) => {
      const patch: Partial<RowDraft> = {};
      if (dateMode === 'set') patch.publish_date = dateValue;
      if (dateMode === 'shift') patch.publish_date = shiftOf(r.publish_date, shiftDays);
      if (tmplOn) patch.template_id = tmplId;
      if (platOn) {
        patch.platform = platform;
        patch.extra_platforms = extras.filter(p => p !== platform);
      }
      if (mediaOn) patch.media = media;
      if (noteOn) patch.note = note;
      if (qMode === 'append') patch.queries = [...new Set([...r.queries, ...qPicked])];
      if (qMode === 'replace') patch.queries = [...qPicked];
      if (qMode === 'clear') patch.queries = [];
      if (comboOn) {
        patch.creation_directions = dirs;
        patch.copywriting_types = types;
      }
      return patch;
    });
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4"
         style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}>
      <div className="w-[640px] max-w-full rounded-md p-4 flex flex-col gap-3 overflow-y-auto"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                    maxHeight: '85vh' }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-primary">批量编辑({count} 行)</h3>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </div>
        <p className="text-[11px] text-muted">
          只有勾上的项会被改写,其余字段保持每行原值;改完还要点下面的「保存」才落库。
        </p>

        {/* Publish time */}
        <div className="text-xs rounded-md p-2.5" style={{ border: '1px solid var(--border-color)' }}>
          <div className="flex flex-wrap items-center gap-3">
            <BulkToggle on={dateMode === 'set'} label="发布时间设为"
                    onToggle={() => setDateMode(m => m === 'set' ? 'none' : 'set')} />
            <input type="datetime-local" className="rounded-md px-2 py-1" style={inputStyle}
                   disabled={dateMode !== 'set'}
                   value={dateValue} onChange={e => setDateValue(e.target.value)} />
          </div>
          <div className="flex flex-wrap items-center gap-3 mt-2">
            <BulkToggle on={dateMode === 'shift'} label="整体平移"
                    onToggle={() => setDateMode(m => m === 'shift' ? 'none' : 'shift')} />
            <input type="number" className="w-20 rounded-md px-2 py-1" style={inputStyle}
                   disabled={dateMode !== 'shift'}
                   value={shiftDays} onChange={e => setShiftDays(Number(e.target.value) || 0)} />
            <span className="text-muted">天(负数往前挪,时分保持不变)</span>
          </div>
        </div>

        {/* Template / outlet / notes */}
        <div className="text-xs rounded-md p-2.5 space-y-2" style={{ border: '1px solid var(--border-color)' }}>
          <div className="flex flex-wrap items-center gap-3">
            <BulkToggle on={tmplOn} label="内容模板" onToggle={() => setTmplOn(v => !v)} />
            <UiSelect className="rounded-md px-2 py-1 min-w-[200px]" style={inputStyle}
                      value={tmplId ? String(tmplId) : ''}
                      onChange={val => setTmplId(Number(val) || null)}
                      disabled={!tmplOn}
                      options={[
                        { value: '', label: '—(清空模板)' },
                        ...templates.map(t => ({ value: String(t.id), label: t.name })),
                      ]} />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <BulkToggle on={mediaOn} label="投放媒体/账号" onToggle={() => setMediaOn(v => !v)} />
            <input type="text" className="rounded-md px-2 py-1 flex-1 min-w-[180px]" style={inputStyle}
                   disabled={!mediaOn} placeholder="留空 = 清掉各行的媒体"
                   value={media} onChange={e => setMedia(e.target.value)} />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <BulkToggle on={noteOn} label="备注" onToggle={() => setNoteOn(v => !v)} />
            <input type="text" className="rounded-md px-2 py-1 flex-1 min-w-[180px]" style={inputStyle}
                   disabled={!noteOn} placeholder="留空 = 清掉各行的备注"
                   value={note} onChange={e => setNote(e.target.value)} />
          </div>
        </div>

        {/* Publish platforms (multi-select) */}
        <div className="text-xs rounded-md p-2.5" style={{ border: '1px solid var(--border-color)' }}>
          <BulkToggle on={platOn} label="发布平台(改写勾选行的主平台 + 附加平台)"
                  onToggle={() => setPlatOn(v => !v)} />
          {platOn && (
            <div className="mt-2">
              <PlatformMultiSelect
                options={platformOptionsFor()}
                platform={platform}
                extras={extras}
                onChange={(p, ex) => { setPlatform(p); setExtras(ex); }}
              />
            </div>
          )}
        </div>

        {/* Prompts */}
        <div className="text-xs rounded-md p-2.5" style={{ border: '1px solid var(--border-color)' }}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted">提示词</span>
            {([
              { v: 'none' as const, label: '不改' },
              { v: 'append' as const, label: '追加所选' },
              { v: 'replace' as const, label: '替换为所选' },
              { v: 'clear' as const, label: '清空' },
            ]).map(o => (
              <button key={o.v} type="button" onClick={() => setQMode(o.v)}
                      className="px-2 py-0.5 rounded-full text-[11px]"
                      style={{
                        background: qMode === o.v ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                        color: qMode === o.v ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border-color)',
                      }}>
                {qMode === o.v && '✓ '}{o.label}
              </button>
            ))}
          </div>
          {(qMode === 'append' || qMode === 'replace') && (
            <div className="mt-2">
              {/* Bulk edits span multiple rows with different seeds -- don't show the "check 1 = topic" hint,
                  and don't backfill seeds (that's the single-row edit modal's job) */}
              <SeedQueryPicker
                seedQueries={seedQueries}
                monitored={monitored}
                picked={qPicked}
                onChange={qs => setQPicked(qs)}
                showTopicHint={false}
                maxHeight={180}
              />
            </div>
          )}
        </div>

        {/* Writing preferences */}
        <div className="text-xs rounded-md p-2.5" style={{ border: '1px solid var(--border-color)' }}>
          <BulkToggle on={comboOn} label="创作偏好(覆盖勾选行;都不选 = 回到画像默认)"
                  onToggle={() => setComboOn(v => !v)} />
          {comboOn && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
              <ChipMultiSelect label={`创作方向(${dirs.length})`} options={COMBO_DIRECTIONS}
                               value={dirs} onChange={setDirs} />
              <ChipMultiSelect label={`文案类型(${types.length})`} options={COMBO_TYPES}
                               value={types} onChange={setTypes} />
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            取消
          </button>
          <button type="button" onClick={handleApply} disabled={nothingPicked}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: nothingPicked ? 0.4 : 1 }}>
            应用到 {count} 行
          </button>
        </div>
      </div>
    </div>
  );
}


// 2026-08-04 — publish platform multi-select: one draft can be placed on multiple platforms (after approval each platform gets its own placement task).
// The main platform is marked separately -- body writing rules follow only the main platform; additional platforms only decide where it's placed.
export function PlatformMultiSelect({
  options, platform, extras, onChange,
}: {
  options: string[];
  platform: string;
  extras: string[];
  onChange: (platform: string, extras: string[]) => void;
}) {
  const picked = [platform, ...extras].filter(Boolean);
  const pickedSet = new Set(picked);
  const toggle = (p: string) => {
    if (!pickedSet.has(p)) {
      if (!platform) onChange(p, extras.filter(x => x !== p));
      else onChange(platform, [...extras.filter(x => x !== p), p]);
      return;
    }
    // Unchecking: if the main platform is unchecked, promote the first additional platform to main
    if (p === platform) {
      const [next, ...rest] = extras;
      onChange(next || '', rest);
    } else {
      onChange(platform, extras.filter(x => x !== p));
    }
  };
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <label className="text-muted">发布平台(可多选 · 已选 {picked.length})</label>
        {!platform && <span className="text-[10px]" style={{ color: 'var(--danger-fg)' }}>至少选一个</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        {options.map(p => {
          const on = pickedSet.has(p);
          const isMain = p === platform;
          return (
            <button key={p} type="button" onClick={() => toggle(p)}
                    className="px-2 py-0.5 rounded-full text-[11px]"
                    title={isMain ? '主平台:正文按这个平台的写作规则生成' : (on ? '附加投放平台' : '点击加入投放')}
                    style={{
                      background: isMain ? 'var(--accent-primary)'
                        : on ? 'rgba(59,130,246,0.14)' : 'var(--bg-tertiary)',
                      color: isMain ? '#fff' : on ? 'var(--accent-primary)' : 'var(--text-secondary)',
                      border: `1px solid ${on ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                    }}>
              {isMain ? '★ ' : on ? '✓ ' : ''}{platformLabel(p)}
            </button>
          );
        })}
      </div>
      {picked.length > 1 && (
        <div className="flex items-center gap-1.5 mt-1.5">
          <span className="text-[10px] text-muted">主平台(正文按它的写作规则):</span>
          <UiSelect className="rounded-md px-2 py-0.5 text-[11px]"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                             border: '1px solid var(--border-color)' }}
                    value={platform}
                    onChange={val => onChange(val, picked.filter(x => x !== val))}
                    options={picked.map(p => ({ value: p, label: platformLabel(p) }))} />
        </div>
      )}
      <p className="text-[10px] text-muted mt-1">
        官网博客是自有站点(免费、可控),默认勾上;其余平台过审后各建一条投放任务,
        付费代发只按主平台的资源编号下单。
      </p>
    </div>
  );
}


// 2026-05-28 — small chip multi-select, used for inline combo overrides.
export function ChipMultiSelect({
  label, options, value, onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const set = new Set(value || []);
  const toggle = (v: string) => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v); else n.add(v);
    onChange(Array.from(n));
  };
  return (
    <div>
      <div className="text-[11px] mb-1" style={{ color: 'var(--text-secondary)' }}>{label}</div>
      <div className="flex flex-wrap gap-1">
        {options.map(opt => {
          const checked = set.has(opt.value);
          return (
            <button
              key={opt.value} type="button"
              onClick={(e) => { e.stopPropagation(); toggle(opt.value); }}
              className="px-2 py-0.5 rounded-full text-[10px]"
              style={{
                background: checked ? 'var(--accent-primary)' : 'var(--bg-card)',
                color: checked ? '#fff' : 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
              }}
            >
              {checked && '✓ '}{opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
