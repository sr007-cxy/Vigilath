// The publish plan "draft layer" -- the same editable state shared by the month view and the table view.
//
// Why this layer exists: before the merge the two views had opposite save models --
//   Table view (PublishingPlanEditor): rows never resync from props after mount, protecting the local draft by ignoring polling;
//   Month view (PlanCalendarTab): no draft, every action PUTs the whole table directly, since cell colors rely on polling to refresh doc_status.
// Just adding a view switcher would mean "switch views and lose unsaved edits". The fix is to split the two layers:
//
//   rows: RowDraft[]        editable fields, local draft, not reset on view switch
//   serverItems             PublishPlanItem from polling, provides only derived read-only state
//                           (doc_id / doc_status / coverage_pct / why / origin / template_name)
//   items: PlanViewItem[]   merged view of both aligned by row uuid -- the month view reads it
//
// When unsaved, polling may not overwrite rows (the table view's old behavior); when clean, follow polling automatically (the month view's old behavior).
// Both are needed, so both are kept.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  adminReviewApi, type ExecutionPlan, type PublishPlanItem,
} from '../../services/adminReviewApi';
import { contentTemplatesApi, type ContentTemplate } from '../../services/contentTemplatesApi';
import { fromPlanItem, rowIssue, toPlanItemDraft, type RowDraft } from './planRow';
import { dayOf, fmtDayLabel } from './planWeeks';
import { toast } from '../../components/ui/toast';

/** Draft row + server-derived state. localKey lets the month view address draft rows directly too (to open the row edit modal). */
export type PlanViewItem = PublishPlanItem & { localKey: string };

/** New rows not yet persisted have no server-derived state; fill with this empty base -- with all fields present, downstream needn't null-check everywhere */
const EMPTY_DERIVED = {
  day: 0, coverage_pct: 0, priority: 'high' as const, suggested_platforms: [] as string[],
};

const rowsOf = (plan: ExecutionPlan | null): RowDraft[] =>
  [...(plan?.publishing_plan || [])].sort((a, b) => a.seq - b.seq).map(fromPlanItem);

/** Normalized form for comparison (localKey is a frontend-only key and doesn't count toward "has it changed";
 *  set to undefined rather than stripped via destructuring, since JSON.stringify skips it) */
const serialize = (rows: RowDraft[]): string =>
  JSON.stringify(rows.map(r => ({ ...r, localKey: undefined })));

export interface PlanDraft {
  rows: RowDraft[];
  setRows: React.Dispatch<React.SetStateAction<RowDraft[]>>;
  /** Draft rows ⨝ server-derived state, aligned by row uuid */
  items: PlanViewItem[];
  /** There are unsaved local changes */
  dirty: boolean;
  busy: '' | 'save' | 'confirm';
  err: string | null;
  setErr: (v: string | null) => void;

  monitored: string[];
  monSet: Set<string>;
  /** localKey -> what this row is missing (same yardstick as save validation) */
  rowIssues: Map<string, string>;
  templates: ContentTemplate[];
  tmplById: Map<number, ContentTemplate>;
  refreshTemplates: () => Promise<void>;
  geoOptions: string[];

  /** Which row is being edited (localKey) -- both views share the same row edit modal */
  editKey: string | null;
  setEditKey: (k: string | null) => void;
  /** The day the table view should page to (for week paging); the month view ignores it */
  focusDay: string | null;
  setFocusDay: (d: string | null) => void;

  updateRow: (key: string, patch: Partial<RowDraft>) => void;
  /**
   * Local change + immediate whole-table PUT (month-view actions use it, keeping "edit then generate now" a single two-step flow).
   * Returns the plan the server just sent back -- new rows' uuids exist only in this version, and dispatching generation must address by it.
   */
  commit: (next: RowDraft[], okMsg: string) => Promise<ExecutionPlan | null>;
  /** Validate + whole-table PUT (toolbar "Save") */
  save: (opts?: { silent?: boolean }) => Promise<ExecutionPlan | null>;
  /** Validate + PUT + confirm (toolbar "Confirm and generate articles") */
  confirm: () => Promise<boolean>;
  /**
   * Prerequisite for actions that require persisting to DB (dispatch generation etc.): save first if there are changes.
   * Non-null plan = it was actually saved and the caller should use its rows (new row uuids are there); null = already clean.
   */
  saveIfDirty: () => Promise<{ ok: boolean; plan: ExecutionPlan | null }>;
  /** Whole-table replacement (generate plan modal: the backend already persisted to DB) */
  resetFrom: (updated: ExecutionPlan) => void;
  /** Merge in only new rows missing locally (candidate insert: keeps unsaved inline edits) */
  mergeFrom: (updated: ExecutionPlan) => void;
}

export function usePlanDraft(
  /** null while the plan hasn't loaded: the draft lives on ExecutionPlanView (not unmounted on tab switch), and hooks can't be called conditionally */
  plan: ExecutionPlan | null,
  topicId: number,
  opts: {
    /** On validation failure, take the user to that row (switch to table view + page the week + open the edit modal) */
    onInvalid?: (row: RowDraft, reason: string) => void;
    /** Backfill the latest plan after a successful save */
    onSaved: (updated: ExecutionPlan) => void;
    onConfirmed?: (updated: ExecutionPlan) => void;
  },
): PlanDraft {
  const token = localStorage.getItem('token') || '';
  const { onInvalid, onSaved, onConfirmed } = opts;

  const [rows, setRows] = useState<RowDraft[]>(() => rowsOf(plan));
  const [templates, setTemplates] = useState<ContentTemplate[]>([]);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [focusDay, setFocusDay] = useState<string | null>(null);
  const [busy, setBusy] = useState<'' | 'save' | 'confirm'>('');
  const [err, setErr] = useState<string | null>(null);

  // The version "last in sync with the server"; both dirty detection and polling sync rely on it.
  // Lazy init: useRef's initial-value expression is evaluated every render, and here it's a whole-table JSON.
  const baseline = useRef<string | null>(null);
  if (baseline.current === null) baseline.current = serialize(rowsOf(plan));
  const dirty = serialize(rows) !== baseline.current;
  // The polling callback must read the latest dirty; hold it in a ref to escape the closure
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  const syncTo = useCallback((next: RowDraft[]) => {
    baseline.current = serialize(next);
    setRows(next);
  }, []);

  // Topic switch: the old draft is void. The draft outlives tabs; if not cleared it stays "dirty", and polling sync
  // doesn't overwrite while dirty -- the new topic's table would be blocked forever by the previous topic's draft.
  const topicRef = useRef(topicId);
  useEffect(() => {
    if (topicRef.current === topicId) return;
    topicRef.current = topicId;
    syncTo([]);      // After clearing, dirty becomes false and the next plan will sync in
    setEditKey(null);
    setFocusDay(null);
    setErr(null);
  }, [topicId, syncTo]);

  // Follow polling: let the server's new table overwrite local only when there are no unsaved changes (cron extensions and edits elsewhere become visible);
  // with changes, hold still -- this is the lifeline of the table view's old "no prop resync after mount".
  useEffect(() => {
    if (!plan) return;
    if (dirtyRef.current) return;
    const next = rowsOf(plan);
    if (serialize(next) === baseline.current) return;   // Unchanged: don't swap the reference, avoiding a whole-table recompute
    syncTo(next);
  }, [plan, syncTo]);

  const monitored = useMemo(() => plan?.monitored_queries || [], [plan?.monitored_queries]);
  const monSet = useMemo(() => new Set(monitored), [monitored]);

  const rowIssues = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of rows) {
      const issue = rowIssue(r, monSet);
      if (issue) m.set(r.localKey, issue);
    }
    return m;
  }, [rows, monSet]);

  const refreshTemplates = useCallback(async () => {
    try {
      const [sys, tenant, topic] = await Promise.all([
        contentTemplatesApi.list(token, { scope: 'system' }),
        contentTemplatesApi.list(token, { scope: 'tenant' }),
        contentTemplatesApi.list(token, { scope: 'topic', topicId }),
      ]);
      setTemplates([...sys, ...tenant, ...topic]);
    } catch (e: unknown) {
      // Failing to fetch templates only affects dropdown options; it shouldn't bring down the whole page
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [token, topicId]);
  const hasPlan = !!plan;
  useEffect(() => { if (hasPlan) void refreshTemplates(); }, [hasPlan, refreshTemplates]);

  const tmplById = useMemo(() => {
    const m = new Map<number, ContentTemplate>();
    for (const t of templates) m.set(t.id, t);
    return m;
  }, [templates]);

  // Region shortcut terms for picking prompts in the row edit modal, split from the plan overview's service region
  const geoOptions = useMemo(() => {
    const raw = String((plan?.overview as Record<string, unknown>)?.service_geo ?? '');
    const parts = raw.split(/[、,,/|;;+\s]+/).map(x => x.trim()).filter(x => x.length >= 2);
    return [...new Set(parts)].slice(0, 12);
  }, [plan?.overview]);

  // ── Merged view: draft fields laid over the server-derived state ─────────────────────────
  const items = useMemo<PlanViewItem[]>(() => {
    const srvById = new Map<string, PublishPlanItem>();
    for (const it of plan?.publishing_plan || []) srvById.set(it.id, it);
    return rows.map(r => {
      const srv = r.id ? srvById.get(r.id) : undefined;
      const tmpl = r.template_id != null ? tmplById.get(r.template_id) : undefined;
      return {
        ...EMPTY_DERIVED,
        ...(srv || {}),
        // Draft overlay: unsaved changes must also be visible in the month view's cells and day detail
        id: r.id || r.localKey,
        localKey: r.localKey,
        seq: r.seq,
        publish_date: r.publish_date,
        seed: r.seed,
        query: r.query,
        queries: r.queries,
        template_id: r.template_id,
        platform: r.platform,
        extra_platforms: r.extra_platforms,
        channel: r.channel,
        media: r.media,
        rwj_resource_id: r.rwj_resource_id,
        note: r.note,
        creation_directions: r.creation_directions,
        copywriting_types: r.copywriting_types,
        // The template name comes from a server-side join; if the template was changed locally, show the new one right away instead of a misleading old name
        template_name: srv && srv.template_id === r.template_id
          ? srv.template_name : (tmpl?.name ?? null),
        template_kind: srv && srv.template_id === r.template_id
          ? srv.template_kind : (tmpl?.kind ?? null),
      } as PlanViewItem;
    });
  }, [rows, plan?.publishing_plan, tmplById]);

  const updateRow = useCallback((key: string, patch: Partial<RowDraft>) => {
    setRows(rs => rs.map(r => r.localKey === key ? { ...r, ...patch } : r));
  }, []);

  // ── Validation ───────────────────────────────────────────────────────
  // On failure, take the user to the offending row (switch view + page the week + open the edit modal). Both red text and a toast are needed:
  // the save button is in the toolbar, and the top banner is often outside the viewport.
  const validate = useCallback((source: RowDraft[]): ReturnType<typeof toPlanItemDraft>[] | null => {
    if (source.length === 0) {
      setErr('发文表为空,请至少添加一行');
      toast.error('发文表为空,请至少添加一行');
      return null;
    }
    const drafts = [];
    for (const r of source) {
      const issue = rowIssue(r, monSet);
      if (issue) {
        const msg = `${fmtDayLabel(dayOf(r.publish_date)) || '未排期'} 这一行${issue}`;
        setErr(msg);
        toast.error(`保存失败:${msg}`);
        onInvalid?.(r, issue);
        return null;
      }
      drafts.push(toPlanItemDraft(r));
    }
    return drafts;
  }, [monSet, onInvalid]);

  const put = useCallback(async (source: RowDraft[]): Promise<ExecutionPlan | null> => {
    const drafts = validate(source);
    if (!drafts) return null;
    const updated = await adminReviewApi.updateExecutionPlan(topicId, drafts, token);
    // The table returned by the server is the new source of truth: new rows get their uuids only at this step
    syncTo(rowsOf(updated));
    onSaved(updated);
    return updated;
  }, [validate, topicId, token, syncTo, onSaved]);

  const save = useCallback(async (o?: { silent?: boolean }): Promise<ExecutionPlan | null> => {
    setBusy('save'); setErr(null);
    try {
      const updated = await put(rows);
      if (updated && !o?.silent) toast.success(`已保存发文计划(${rows.length} 篇)`);
      return updated;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg); toast.error(`保存失败:${msg}`);
      return null;
    } finally {
      setBusy('');
    }
  }, [put, rows]);

  const commit = useCallback(
    async (next: RowDraft[], okMsg: string): Promise<ExecutionPlan | null> => {
      setBusy('save'); setErr(null);
      setRows(next);                  // Put local into the target state first; no rollback on failure (same as the old calendar)
      try {
        const updated = await put(next);
        if (updated) toast.success(okMsg);
        return updated;
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        setErr(msg); toast.error(`保存失败:${msg}`);
        return null;
      } finally {
        setBusy('');
      }
    }, [put]);

  const saveIfDirty = useCallback(async (): Promise<{ ok: boolean; plan: ExecutionPlan | null }> => {
    if (!dirtyRef.current) return { ok: true, plan: null };
    const updated = await save({ silent: true });
    return { ok: !!updated, plan: updated };
  }, [save]);

  const confirm = useCallback(async (): Promise<boolean> => {
    setBusy('confirm'); setErr(null);
    try {
      if (!(await put(rows))) return false;
      const updated = await adminReviewApi.confirmExecutionPlan(topicId, token);
      syncTo(rowsOf(updated));
      onConfirmed?.(updated);
      toast.success('计划已确认,文章开始生成');
      return true;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg); toast.error(`确认失败:${msg}`);
      return false;
    } finally {
      setBusy('');
    }
  }, [put, rows, topicId, token, syncTo, onConfirmed]);

  const resetFrom = useCallback((updated: ExecutionPlan) => {
    syncTo(rowsOf(updated));
    onSaved(updated);
  }, [syncTo, onSaved]);

  // Candidate insert: the backend already wrote into the plan. Merge only rows missing locally, no full reset -- keeps unsaved inline edits.
  const mergeFrom = useCallback((updated: ExecutionPlan) => {
    setRows(rs => {
      const known = new Set(rs.map(r => r.id).filter(Boolean));
      const fresh = [...(updated.publishing_plan || [])]
        .sort((a, b) => a.seq - b.seq)
        .filter(it => !known.has(it.id))
        .map(fromPlanItem);
      if (fresh[0]) setFocusDay(dayOf(fresh[0].publish_date));
      return [...rs, ...fresh];
    });
    onSaved(updated);
  }, [onSaved]);

  return {
    rows, setRows, items, dirty, busy, err, setErr,
    monitored, monSet, rowIssues, templates, tmplById, refreshTemplates, geoOptions,
    editKey, setEditKey, focusDay, setFocusDay,
    updateRow, commit, save, confirm, saveIfDirty, resetFrom, mergeFrom,
  };
}
