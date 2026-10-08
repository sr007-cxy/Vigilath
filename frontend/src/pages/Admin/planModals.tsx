// The plan's two drafting modals -- add one (for a date) / batch drafting.
//
// Both are one-off "pick a range -> dispatch drafting" actions with no shared state with the page body (only an onDone callback),
// so they were split out of ExecutionPlan. Entry points are in the "⊕ Add" and "⋯" menus on row 2.

import { useEffect, useState } from 'react';
import { adminReviewApi, type ExecutionPlan, type PlanItemDraft } from '../../services/adminReviewApi';
import { contentTemplatesApi, type ContentTemplate } from '../../services/contentTemplatesApi';
import { UiSelect } from '../../components/ui/Select';
import {
  ChipMultiSelect } from './PublishingPlanEditor';
import { SeedQueryPicker } from './SeedQueryPicker';
import type {} from '../../services/adminReviewApi';
import { COMBO_DIRECTIONS, COMBO_TYPES } from './comboOptions';
import { toast } from '../../components/ui/toast';
import {
  fmtDayLabel, todayISO } from './planWeeks';
import { platformLabel } from '../../components/publish/publishShared';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';


export function QuickAddModal({
  topicId, plan, presetDate, onClose, onDone,
}: {
  topicId: number;
  plan: ExecutionPlan;
  /** Prefilled with the date when entering from a calendar day; null = default to today */
  presetDate?: string | null;
  onClose: () => void;
  onDone: (updated: ExecutionPlan) => void;
}) {
  const token = localStorage.getItem('token') || '';
  const monitored = plan.monitored_queries || [];
  const [date, setDate] = useState(() => presetDate || todayISO());
  const [seed, setSeed] = useState('');
  const [templates, setTemplates] = useState<ContentTemplate[]>([]);
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [platform, setPlatform] = useState('');
  const [queries, setQueries] = useState<string[]>([]);
  const [dirs, setDirs] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<false | 'save' | 'gen'>(false);
  const [err, setErr] = useState<string | null>(null);

  // Full three-tier template set (system/tenant/topic), same source as the plan editor
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sys, tenant, topic] = await Promise.all([
          contentTemplatesApi.list(token, { scope: 'system' }),
          contentTemplatesApi.list(token, { scope: 'tenant' }),
          contentTemplatesApi.list(token, { scope: 'topic', topicId }),
        ]);
        if (cancelled) return;
        const all = [...sys, ...tenant, ...topic];
        setTemplates(all);
        const first = all.find(t => t.scope === 'system') || all[0];
        if (first) {
          setTemplateId(prev => prev ?? first.id);
          setPlatform(prev => prev || first.target_platforms?.[0] || '公众号');
        }
      } catch (e: unknown) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [token, topicId]);

  const tmpl = templateId != null ? templates.find(t => t.id === templateId) : undefined;
  const platOpts = [...new Set([
    ...(tmpl?.target_platforms?.length ? tmpl.target_platforms : ['公众号', '小红书', '抖音', '视频号']),
    '知乎', '官网博客', '软文匠',
  ])];
  const submit = async (generateNow: boolean) => {
    if (busy) return;
    if (!seed.trim() && queries.length === 0) {
      setErr('主题种子和提示词至少要填一个');
      return;
    }
    if (!templateId) {
      setErr('请选择内容模板');
      return;
    }
    if (!date) {
      setErr('请选择发布日期');
      return;
    }
    setBusy(generateNow ? 'gen' : 'save'); setErr(null);
    try {
      const item: PlanItemDraft = {
        id: null,
        seq: 0,
        publish_date: date,
        seed: seed.trim() || null,
        query: '',
        queries,
        template_id: templateId,
        platform,
        media: null,
        rwj_resource_id: null,
        note: note.trim() || null,
        creation_directions: dirs,
        copywriting_types: types,
      };
      const updated = await adminReviewApi.addPlanItem(topicId, {
        item, generate_now: generateNow,
      }, token);
      toast.success(generateNow
        ? `已加入 ${fmtDayLabel(date)} 并派发生成`
        : `已加入 ${fmtDayLabel(date)} 的计划`);
      onDone(updated);
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

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4"
         style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}>
      <div className="w-[600px] max-w-full rounded-md p-4 flex flex-col gap-3"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                    maxHeight: '85vh', overflowY: 'auto' }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-primary">新增一篇</h3>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </div>
        <p className="text-[11px] text-muted">
          选日期、自定主题和模板参数,直接加进发文计划;点「加入并立即生成」马上出稿,进入内容审核流程。
        </p>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <label className="block text-muted mb-1">发布日期</label>
            <input type="date" className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                   value={date}
                   onChange={e => setDate(e.target.value)} />
          </div>
          <div>
            <label className="block text-muted mb-1">主题 / 种子提示词(选问题时自动带出)</label>
            <input type="text" className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                   placeholder="想写什么主题…"
                   value={seed}
                   onChange={e => setSeed(e.target.value)} />
          </div>
          <div>
            <label className="block text-muted mb-1">内容模板</label>
            <UiSelect className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                      value={templateId != null ? String(templateId) : ''}
                      onChange={val => {
                        const id = Number(val) || null;
                        setTemplateId(id);
                        const nt = id != null ? templates.find(t => t.id === id) : undefined;
                        if (nt?.target_platforms?.length && !nt.target_platforms.includes(platform)) {
                          setPlatform(nt.target_platforms[0]);
                        }
                      }}
                      options={[
                        { value: '', label: '—' },
                        ...templates.map(t => ({
                          value: String(t.id),
                          label: t.kind ? `${t.name}(${t.kind})` : t.name,
                        })),
                      ]} />
          </div>
          <div>
            <label className="block text-muted mb-1">发布平台</label>
            <UiSelect className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                      value={platform}
                      onChange={val => setPlatform(val)}
                      options={platOpts.map(p => ({ value: p, label: platformLabel(p) }))} />
          </div>
        </div>

        <SeedQueryPicker
          seedQueries={plan.seed_queries}
          monitored={monitored}
          picked={queries}
          // Bring in the seed of the first checked prompt; don't overwrite if the user already typed a topic
          onChange={(qs, s) => {
            setQueries(qs);
            if (s) setSeed(prev => prev.trim() ? prev : s);
          }}
          maxHeight={140}
        />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <ChipMultiSelect
            label={`创作方向(${dirs.length},空=画像默认)`}
            options={COMBO_DIRECTIONS}
            value={dirs}
            onChange={setDirs}
          />
          <ChipMultiSelect
            label={`文案类型(${types.length},空=画像默认)`}
            options={COMBO_TYPES}
            value={types}
            onChange={setTypes}
          />
        </div>

        <div className="text-xs">
          <label className="block text-muted mb-1">备注(选填)</label>
          <input type="text" className="w-full rounded-md px-2 py-1.5" style={inputStyle}
                 value={note}
                 onChange={e => setNote(e.target.value)} />
        </div>

        {err && (
          <div className="rounded-md p-2 text-xs"
               style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
            {err}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            取消
          </button>
          <button type="button" onClick={() => submit(false)} disabled={!!busy}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)',
                           opacity: busy ? 0.5 : 1 }}>
            {busy === 'save' ? '保存中…' : '仅加入计划'}
          </button>
          <button type="button" onClick={() => submit(true)} disabled={!!busy}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
            {busy === 'gen' ? '派发中…' : '✍️ 加入并立即出稿'}
          </button>
        </div>
      </div>
    </div>
  );
}


// One-off batch generation modal — by default each day drafts its own articles; this stockpiles a batch by range.
// Range: next N days / all plan rows; count can be capped; by default only fills rows without drafts.


export function BatchGenerateModal({
  topicId, plan, onClose, onQueued,
}: {
  topicId: number;
  plan: ExecutionPlan;
  onClose: () => void;
  onQueued: () => void;
}) {
  const token = localStorage.getItem('token') || '';
  const [days, setDays] = useState<number | null>(7);   // null = all plan rows
  const [limit, setLimit] = useState<string>('');       // empty = unlimited
  const [includeExisting, setIncludeExisting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const items = plan.publishing_plan || [];
  // Rows whose generation failed are effectively undrafted -- they must count as pending, otherwise "fill-generate" skips them forever
  const pendingTotal = items.filter(it => !it.doc_id || it.doc_failed).length;
  // Published rows: with "also regenerate drafted" checked the backend blocks them all; tell the user honestly up front
  const publishedTotal = items.filter(it => it.doc_status === 'published').length;

  const RANGE_OPTS: { v: number | null; label: string }[] = [
    { v: 7, label: '未来 7 天' },
    { v: 14, label: '未来 14 天' },
    { v: 30, label: '未来 30 天' },
    { v: null, label: '全部计划行' },
  ];

  const handleSubmit = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await adminReviewApi.batchGenerateDocs(topicId, {
        days,
        limit: limit.trim() ? Math.max(1, Number(limit)) : null,
        include_existing: includeExisting,
      }, token);
      const skips = [
        r.skipped_existing ? `已出稿 ${r.skipped_existing} 行` : '',
        r.skipped_published ? `已发布 ${r.skipped_published} 行` : '',
      ].filter(Boolean).join('、');
      if (r.queued > 0) {
        toast.success(`已派发 ${r.queued} 篇生成任务${skips ? `(跳过${skips})` : ''}`);
        onQueued();
      } else if (r.matched === 0) {
        setErr('所选范围内没有计划行');
      } else if (r.skipped_published) {
        setErr(`所选范围内 ${r.skipped_published} 行都已发布,批量出稿不动已发布的稿 —— `
          + '确要重写请在月视图里点进那一天,在该行点 ↻ 单独重新出稿。');
      } else {
        setErr('所选范围内的计划行都已出稿(勾选「已出稿的行也重新出稿」可强制重写)');
      }
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

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4"
         style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}>
      <div className="w-[480px] max-w-full rounded-md p-4 flex flex-col gap-3"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-primary">批量出稿</h3>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </div>

        <p className="text-[11px] text-muted">
          默认节奏是每天生成当天的稿;这里可以按范围一次性先生成一批,生成后进入内容审核流程。
          当前计划共 {items.length} 行,未出稿 {pendingTotal} 行。
        </p>

        <div className="text-xs">
          <div className="text-muted mb-1.5">出稿范围</div>
          <div className="flex flex-wrap gap-1.5">
            {RANGE_OPTS.map(opt => (
              <button key={String(opt.v)} type="button" onClick={() => setDays(opt.v)}
                      className="px-2.5 py-1 rounded-full text-xs"
                      style={{
                        background: days === opt.v ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                        color: days === opt.v ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border-color)',
                      }}>
                {days === opt.v && '✓ '}{opt.label}
              </button>
            ))}
          </div>
        </div>

        <div className="text-xs">
          <label className="block text-muted mb-1">篇数上限(选填,按日期顺序取前 N 行)</label>
          <input type="number" min={1}
                 className="w-28 rounded-md px-2 py-1.5" style={inputStyle}
                 placeholder="不限"
                 value={limit}
                 onChange={e => setLimit(e.target.value)} />
        </div>

        <label className="flex items-center gap-2 text-xs cursor-pointer"
               style={{ color: 'var(--text-primary)' }}>
          <input type="checkbox" checked={includeExisting}
                 onChange={e => setIncludeExisting(e.target.checked)} />
          已出稿的行也重新出稿(会覆写正文并退回审核)
        </label>
        {includeExisting && (
          <div className="rounded-md p-2 text-[11px]"
               style={{ background: 'rgba(59,130,246,0.10)', color: 'var(--text-secondary)',
                        border: '1px solid rgba(59,130,246,0.3)' }}>
            只重写<b>未发布</b>的稿。已发布的稿会被跳过 —— 覆盖它只会改掉库里的正文,
            投放出去的那篇不会跟着变,线上内容和库里就对不上了,引用归因也会失真。
            {publishedTotal > 0 && `当前计划里有 ${publishedTotal} 行已发布。`}
            确要重写某篇已发布的稿,请在月视图里点进那一天,在该行点 ↻ 单独重新出稿。
          </div>
        )}

        {err && (
          <div className="rounded-md p-2 text-xs"
               style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
            {err}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            取消
          </button>
          <button type="button" onClick={handleSubmit} disabled={busy}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
            {busy ? '派发中…' : '⚡ 开始出稿'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Publish plan tab body ────────────────────────────────────────────────────────
// 2026-09-10 — "Calendar" and "Publish plan" used to be two sibling tabs that read like two features, but they are two layouts of the same
// publishing_plan: the month view shows density and drafting progress by day, the table view reads through by row and edits across days.
// Merged into one tab + view switcher, following the records+views pattern: the view only decides layout, not what you can do.
//
// "No lost functionality" hinges on two things:
//   1. The row edit modal (RowEditModal) is rendered by this component and shared by both views -- the month view can now edit every field;
//   2. The draft (usePlanDraft) is not reset on view switch -- unsaved changes aren't lost by a switch.
//      The draft itself lives further out on ExecutionPlanView, so even **switching tabs** doesn't unmount it.
// Save / confirm also moved up to the toolbar, so a draft can be confirmed directly from the month view without jumping to the table.
// Right side of row 1: this plan's **status** and **single primary action**.
//
// These three things used to be scattered in three places: plan status in the "Project overview" card, unsaved/needs-completion on the view toolbar,
// and "this plan is still a draft, go confirm" as a separate yellow bar. They were three facets of the same thing,
// and people had to scan three places to know "what state is it in, what should I click". Consolidated into one.
