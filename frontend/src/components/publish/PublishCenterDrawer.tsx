// Publish center -- unified placement UI after a draft passes review (secondary right drawer, level=2).
//
// Three areas:
//   (1) Recommendations: source strategy placement suggestions (source_strategy, idle can generate in one click + poll)
//      + per-platform historical attribution badges (publish-attribution) + domains AI cites often (citation-sources)
//   (2) Platform list: platformCatalog grouped grid (owned channels/social content/account media/paid media),
//      manual -> create placement task; official blog (staff) -> one-click Mediumsly; Ruanwenjiang (staff) -> embedded media search
//   (3) Tasks: created task list + backfill URL + two-step confirmation for paid placement (single outlet / select several and confirm
//      the charge once, ordering serially one by one) / fetch receipts
// Four requests run in parallel, each area fault-tolerant on its own -- recommendations failing doesn't affect creating tasks.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { fmtTime } from '../../utils/datetime';
import { normalizePlatform } from '../../constants/platformCatalog';
import {
  contentApi, type PublishTask,
  type RwjBatchPreview, type RwjBatchPublishResult,
} from '../../services/contentApi';
import { aiTelemetryApi, type SourceStrategy } from '../../services/aiTelemetryApi';
import {
  reportsApi, type CitationSources, type PublishAttributionRow,
} from '../../services/reportsApi';
import { Drawer } from '../overlays/Drawer';
import { MediaLibrarySection } from './MediaLibrarySection';
import { UnifiedTargetList } from './UnifiedTargetList';
import { BackendError } from '../../services/apiError';
import { isPaidMediaTask, isAitoearnTask, taskStatusLabel, taskDisplayName,
         type PublishPanelDoc } from './publishShared';
import { uiConfirm } from '../ui/dialogs';
import { ctripVerificationOwnerKey } from '../../services/wechatSyncPlugin';
import { useRegisterAgentActions } from '../AgentChat/agentActions';

interface AttributionBadge {
  published: number;
  cited: number;
  rate: number;
  costCents: number | null;
}

function buildBadges(rollup: PublishAttributionRow[]): Map<string, AttributionBadge> {
  const map = new Map<string, AttributionBadge>();
  for (const row of rollup) {
    const entry = normalizePlatform(row.platform);
    if (!entry) continue;
    const b = map.get(entry.key) || { published: 0, cited: 0, rate: 0, costCents: null };
    b.published += row.published_docs;
    b.cited += row.cited_docs;
    if (row.total_cost_cents != null) b.costCents = (b.costCents || 0) + row.total_cost_cents;
    b.rate = b.published ? Math.round((b.cited / b.published) * 1000) / 10 : 0;
    map.set(entry.key, b);
  }
  return map;
}

function Badge({ b }: { b?: AttributionBadge }) {
  if (!b || !b.published) return null;
  const good = b.cited > 0;
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap"
          style={{
            background: good ? 'var(--success-bg)' : 'var(--bg-input)',
            color: good ? 'var(--success-fg)' : 'var(--text-secondary)',
          }}>
      历史被引 {b.cited}/{b.published} · {b.rate}%
      {b.costCents != null ? ` · 累计 ${b.costCents} 积分` : ''}
    </span>
  );
}

export function PublishCenterDrawer({
  open, onClose, doc, token, onChanged, onPublishMediumsly, asPage,
}: {
  open?: boolean;
  onClose: () => void;
  doc: PublishPanelDoc;
  token: string;
  onChanged: () => void;
  /** Passed only by the content review page: one-click publish to the official blog (Mediumsly); if not passed, that card offers no auto-publish */
  onPublishMediumsly?: () => Promise<boolean>;
  /** When true, renders as a full page (standalone publish page /content-review/:docId/publish) rather than a secondary drawer */
  asPage?: boolean;
}) {
  const { isStaff, isAgency, user } = useAuth();
  const canPublish = doc.status === 'approved' || doc.status === 'published';
  // The drawer loads based on open; the full-page layout has no notion of open and counts as active on entry.
  const active = asPage ? true : !!open;

  // ── Task area state ───────────────────────────────────────────
  const [tasks, setTasks] = useState<PublishTask[]>([]);
  const [urlDraft, setUrlDraft] = useState<Record<number, string>>({});
  const [customRefillName, setCustomRefillName] = useState('');
  const [customRefillUrl, setCustomRefillUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Batch paid placement: selected pending-payment tasks / batch price check results / batch order results
  const [batchPicked, setBatchPicked] = useState<Set<number>>(new Set());
  const [batchPreview, setBatchPreview] = useState<RwjBatchPreview | null>(null);
  const [batchResult, setBatchResult] = useState<RwjBatchPublishResult | null>(null);

  // ── Recommendation area state ───────────────────────────────────────────
  const [strategy, setStrategy] = useState<SourceStrategy | null>(null);
  const [strategyErr, setStrategyErr] = useState<string | null>(null);
  const [attribution, setAttribution] = useState<Map<string, AttributionBadge>>(new Map());
  const [attributionRaw, setAttributionRaw] = useState<PublishAttributionRow[]>([]);
  // Media-level attribution -- fed to the media library's "placed before and cited by AI" recommendation area.
  // Note the difference from attributionRaw: that one is aggregated by platform, while recommendations need specific media.
  const [mediaAttribution, setMediaAttribution] = useState<PublishAttributionRow[]>([]);
  const [sources, setSources] = useState<CitationSources | null>(null);
  const [showSources, setShowSources] = useState(false);
  const pollRef = useRef<number | null>(null);

  const loadTasks = useCallback(async () => {
    try {
      setTasks(await contentApi.listPublishTasks(doc.topic_id, { doc_id: doc.id }, token));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [doc.id, doc.topic_id, token]);

  const loadStrategy = useCallback(async () => {
    try {
      const s = await aiTelemetryApi.getSourceStrategy(doc.topic_id, token);
      setStrategy(s);
      if (s.status === 'generating') {
        pollRef.current = window.setTimeout(loadStrategy, 3000);
      }
    } catch (e) {
      setStrategyErr(e instanceof Error ? e.message : String(e));
    }
  }, [doc.topic_id, token]);

  useEffect(() => {
    if (!active) return;
    loadTasks();
    loadStrategy();
    reportsApi.publishAttribution(doc.topic_id, token)
      .then(a => {
        setAttribution(buildBadges(a.platform_rollup));
        setAttributionRaw(a.platform_rollup);
        setMediaAttribution(a.rows || []);
      })
      .catch(() => { /* attribution failure doesn't block the main flow */ });
    reportsApi.citationSources(doc.topic_id, token, 8)
      .then(setSources)
      .catch(() => { /* same as above */ });
    return () => { if (pollRef.current) window.clearTimeout(pollRef.current); };
  }, [active, doc.topic_id, token, loadTasks, loadStrategy]);

  useEffect(() => {
    if (!active) return;
    const refresh = () => { void loadTasks(); };
    window.addEventListener('vigilath:publish-verification-updated', refresh);
    return () => window.removeEventListener('vigilath:publish-verification-updated', refresh);
  }, [active, loadTasks]);

  const generateStrategy = async () => {
    setStrategyErr(null);
    try {
      const s = await aiTelemetryApi.generateSourceStrategy(doc.topic_id, token);
      setStrategy(s);
      pollRef.current = window.setTimeout(loadStrategy, 3000);
    } catch (e) {
      setStrategyErr(e instanceof Error ? e.message : String(e));
    }
  };

  // ── Publish actions ─────────────────────────────────────────────
  const publishMediumsly = async () => {
    if (!onPublishMediumsly) return;
    setBusy(true); setErr(null);
    try {
      const ok = await onPublishMediumsly();
      if (ok) onChanged();
    } finally { setBusy(false); }
  };

  const submitUrl = async (taskId: number) => {
    const url = (urlDraft[taskId] || '').trim();
    if (!url) return;
    setBusy(true); setErr(null);
    try {
      await contentApi.submitPublishUrl(taskId, { url }, token);
      setUrlDraft(d => ({ ...d, [taskId]: '' }));
      await loadTasks();
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const submitCustomRefill = async () => {
    const name = customRefillName.trim();
    const url = customRefillUrl.trim();
    if (!name || !url) return;
    setBusy(true); setErr(null);
    try {
      const created = await contentApi.createPublishTasks(doc.id, [{
        platform: name,
        media_name: '',
        channel_type: 'manual_account',
      }], token);
      const task = created[0];
      if (!task) throw new Error('创建回填任务失败');
      await contentApi.submitPublishUrl(task.id, { url }, token);
      setCustomRefillName('');
      setCustomRefillUrl('');
      await loadTasks();
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  // Paid ordering is **one step**, no more price-check confirmation layer. The retail quote the user sees on the page is sent as
  // expected_price_cents; the server checks the price in real time before ordering, and if the increase exceeds tolerance returns 409 with the new price
  // (that's one click on an abnormal path, not a routine second confirmation); within tolerance it orders directly, and price drops always pass.
  // On insufficient credits / over limit, give an actionable next step. Just saying "insufficient credits" leaves the user stuck --
  // the backend's 402 body carries topup.web, but that's for the agent; the web page gives a direct link.
  const [needTopup, setNeedTopup] = useState(false);

  const showApiError = (e: unknown) => {
    const code = e instanceof BackendError ? e.code : null;
    setNeedTopup(code === 'INSUFFICIENT_BALANCE');
    setErr(e instanceof Error ? e.message : String(e));
  };

  const retailCentsOf = (tk: PublishTask): number | null =>
    tk.retail_price_yuan ? Math.round(Number(tk.retail_price_yuan) * 100) : null;

  const payTask = async (tk: PublishTask) => {
    setBusy(true); setErr(null);
    try {
      setNeedTopup(false);
      await contentApi.rwjPublish(tk.id, retailCentsOf(tk), token);
      await loadTasks();
      onChanged();
    } catch (e) {
      showApiError(e);
    } finally { setBusy(false); }
  };

  // Withdraw: set tasks not yet sent to cancelled. Paid tasks can only be withdrawn while planned -- beyond that
  // the upstream order is submitted and money is spent, which needs a refund, not a withdrawal (the backend has the same gate; here we just don't
  // draw the button).
  const withdrawTask = async (tk: PublishTask) => {
    const paid = isPaidMediaTask(tk);
    const ok = await uiConfirm(
      paid
        ? `撤回「${taskDisplayName(tk)}」?这条还没下单,撤回不产生任何费用;之后想投可以重新加回来。`
        : `撤回「${taskDisplayName(tk)}」?撤回后它不再出现在待办里,之后想投可以重新加回来。`,
      { title: '撤回投放', okText: '撤回' },
    );
    if (!ok) return;
    setBusy(true); setErr(null);
    try {
      await contentApi.withdrawPublishTask(tk.id, '', token);
      await loadTasks();
      onChanged();
    } catch (e) {
      showApiError(e);
    } finally { setBusy(false); }
  };

  // ── Batch: select several pending-payment tasks -> one price check -> one charge confirmation ──────────────
  const toggleBatchPick = (taskId: number) => {
    setBatchPreview(null); setBatchResult(null);
    setBatchPicked(prev => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId); else next.add(taskId);
      return next;
    });
  };

  // Batch is also one step: first get real-time prices per outlet once (as expected prices), then order with them immediately.
  // Ordering is factored out by id because "publish directly after selecting in the media library" must take the same path --
  // tasks on that path were just created and aren't in batchPicked yet.
  const payTaskIds = async (ids: number[]) => {
    if (ids.length === 0) return;
    setBusy(true); setErr(null); setNeedTopup(false); setBatchResult(null);
    try {
      const quote = await contentApi.rwjBatchPublishPreview(ids, token);
      setBatchPreview(quote);
      if (!quote.publish_enabled) {
        setErr('发布闸门未开启,任务已留在待投放列表。请联系管理员开启后重试。');
        await loadTasks();
        onChanged();
        return;
      }
      if (quote.items.length === 0) {
        setErr('没有可下单的任务');
        await loadTasks();
        return;
      }
      const out = await contentApi.rwjBatchPublish(
        quote.items.map(i => ({
          task_id: i.task_id, expected_price_cents: i.retail_price_cents,
        })), token);
      setBatchResult(out);
      setBatchPreview(null);
      setBatchPicked(new Set());
      await loadTasks();
      onChanged();
    } catch (e) {
      showApiError(e);
      // When ordering fails (price increase / insufficient balance / gate) tasks stay planned; refresh the list so the user can retry
      await loadTasks();
    } finally { setBusy(false); }
  };

  const payBatch = () => payTaskIds([...batchPicked]);

  const rwjPull = async (taskId: number) => {
    setBusy(true); setErr(null);
    try {
      await contentApi.rwjReconcile(taskId, token);
      await loadTasks();
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const aitoearnPull = async (taskId: number) => {
    setBusy(true); setErr(null);
    try {
      await contentApi.aitoearnReconcile(taskId, token);
      await loadTasks();
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const confirmCandidateUrl = async (taskId: number) => {
    setBusy(true); setErr(null);
    try {
      await contentApi.confirmPublishCandidateUrl(taskId, token);
      await loadTasks();
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const refreshCtripVerification = async (taskId: number) => {
    setBusy(true); setErr(null);
    try {
      await contentApi.refreshPublishVerification(taskId, token);
      window.dispatchEvent(new Event('vigilath:ctrip-verification-sync-requested'));
      await loadTasks();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  // Manually trigger AI auto media selection: runs in the background, polling the task list until the Ruanwenjiang task appears (≤45 seconds)
  const [autoSelecting, setAutoSelecting] = useState(false);
  const [showManualPick, setShowManualPick] = useState(false);
  // Three destinations + body + suggestions, all expanded in place, no longer four peer tab cards
  const [showAccounts, setShowAccounts] = useState(false);
  const [showCustomRefill, setShowCustomRefill] = useState(false);
  const [showArticle, setShowArticle] = useState(false);
  const [showSuggest, setShowSuggest] = useState(false);
  // 2026-08-17 (step 8) -- accountChannel removed: multi-account distribution no longer has tabs per channel;
  // a unified list shows the three channels side by side, and the channel is reduced to a badge on each row.
  const autoPollRef = useRef<number | null>(null);

  // ── Agent page actions (batch 1: read-only) ────────────────────────────────────
  // **Narrowed by isStaff**: this component is also used on the customer side (Dashboard); don't carry ops actions over.
  // **Not registered**: runAutoSelect / paid ordering / one-click official blog publish -- these spend money or send real outbound content, so a human clicks.
  const publishSnapshot = useMemo(() => {
    const funnel: Record<string, number> = {};
    tasks.forEach((t2) => { funnel[t2.status] = (funnel[t2.status] || 0) + 1; });
    return {
      文章id: doc.id,
      稿件状态: doc.status,
      各状态任务数: funnel,
      已建任务: tasks.slice(0, 10).map((t2) => ({
        id: t2.id, 平台: t2.platform, 媒体: t2.media_name || '',
        状态: taskStatusLabel(t2), 有链接: !!t2.published_url,
      })),
    };
  }, [doc.id, doc.status, tasks]);

  useRegisterAgentActions(isStaff ? {
    page: 'publish_center',
    scope: 'publish',
    weight: 30,   // the drawer is the visually topmost layer, so it decides page/describe
    describe: '发布中心(在发哪一篇 / 投到哪儿 / 投放进度)',
    snapshot: publishSnapshot,
    actions: {
      // setTab retired (2026-09-04): the four tab cards were merged into one screen, so there are no tabs to switch.
      // setAccountChannel retired (step 8): in the unified list the channel is no longer a switchable view.
      openManualPick: () => setShowManualPick(true),
      closeManualPick: () => setShowManualPick(false),
      openAccounts: () => setShowAccounts(true),
      refresh: () => { void loadTasks(); },
    },
  } : null);

  const runAutoSelect = async () => {
    setErr(null); setAutoSelecting(true);
    try {
      await contentApi.rwjAutoSelect(doc.id, token);
      let elapsed = 0;
      const tick = async () => {
        elapsed += 3;
        const ts = await contentApi.listPublishTasks(doc.topic_id, { doc_id: doc.id }, token);
        setTasks(ts);
        if (ts.some(isPaidMediaTask) || elapsed >= 45) {
          setAutoSelecting(false);
          onChanged();
          if (!ts.some(isPaidMediaTask)) {
            setErr('自动选媒未产出任务(可能候选为空或合规拦截),请查看后台日志或稍后重试');
          }
          return;
        }
        autoPollRef.current = window.setTimeout(tick, 3000);
      };
      autoPollRef.current = window.setTimeout(tick, 3000);
    } catch (e) {
      setAutoSelecting(false);
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => () => { if (autoPollRef.current) window.clearTimeout(autoPollRef.current); }, []);


  const recommendations: { platform: string; priority: string; reason: string; content_type: string }[]
    = (strategy?.recommendations as never[]) || [];

  const externalSources = (sources?.top_sources || []).filter(s => !s.owned).slice(0, 8);
  const candidateUrlOf = (tk: PublishTask) =>
    tk.channel_meta?.url_verification?.candidate_url || tk.channel_meta?.candidate_url || '';
  const needsUrlRefill = (tk: PublishTask) =>
    tk.channel_meta?.url_verification?.verification_method !== 'platform_api'
    && (tk.status === 'pending_url' || tk.status === 'pending_publish' || tk.status === 'failed');
  const refillCount = tasks.filter(needsUrlRefill).length;
  const isHiddenPublishTask = (tk: PublishTask) => {
    const name = `${tk.platform} ${tk.media_name}`.toLowerCase();
    return name.includes('百家号') || name.includes('知乎') || name.includes('搜狐号')
      || name.includes('baijiahao') || name.includes('zhihu') || name.includes('sohu');
  };
  const publishTasks = tasks.filter(tk => !isHiddenPublishTask(tk));
  // Paid placement tasks eligible for batch charge confirmation (draft pushed, not yet ordered)
  const payableTasks = publishTasks.filter(
    tk => isPaidMediaTask(tk) && (tk.status === 'planned' || tk.status === 'failed'));
  // Total for the selected outlets -- written into the button text so the user sees how many credits it costs before clicking.
  // retail_price_yuan is a string in yuan; credits = yuan x 100.
  const pickedTotalPoints = (() => {
    const rows = payableTasks.filter(tk => batchPicked.has(tk.id));
    if (rows.length === 0 || rows.some(tk => !tk.retail_price_yuan)) return '';
    const yuanSum = rows.reduce((sum, tk) => sum + Number(tk.retail_price_yuan), 0);
    return String(Math.round(yuanSum * 100));
  })();
  const defaultTaskName = (tk: PublishTask) => `${tk.platform}${tk.media_name ? ` / ${tk.media_name}` : ''}`;
  // "Placement progress" = the union of the old "placement tasks" and "link backfill" tabs; the same tasks are rendered only once.
  // Manual platforms like Zhihu/Baijiahao are hidden in the placement list, but their backfill must be exposed, so they're let through
  // by "has link-related status".
  const progressTasks = tasks.filter(tk => !isHiddenPublishTask(tk)
    || needsUrlRefill(tk) || !!tk.published_url || !!candidateUrlOf(tk));
  const publishedCount = progressTasks.filter(tk => tk.status === 'published').length;
  const wordCount = doc.body_markdown ? doc.body_markdown.replace(/\s+/g, '').length : 0;
  const docStatusText = doc.status === 'published' ? '已发布'
    : doc.status === 'approved' ? '已过审' : '未过审';
  const suggestSummary = strategy?.status === 'ready' && recommendations.length > 0
    ? `投放建议 ${recommendations.length} 条`
    : strategy?.status === 'generating' ? '投放建议生成中…' : '投放建议';

  // One screen, three sections: which article is being published -> where to place it -> placement progress.
  // 2026-09-04 removed the original four tab cards (placement suggestions / placement tasks / link backfill / multi-account distribution):
  // those four weren't four parallel things -- suggestions are reference, backfill is a task state, account distribution is one of the channels;
  // laying them out as four peer entries forced users to guess which was the main line, and the same tasks were rendered in two tabs.
  const body = (
    <>
      {err && (
        <div className="text-xs space-y-1.5" style={{ color: 'var(--danger-fg)' }}>
          <div>{err}</div>
          {needTopup && (
            <a href="/account/balance" target="_blank" rel="noreferrer"
               className="inline-block px-3 py-1.5 rounded-md text-white no-underline"
               style={{ background: 'var(--accent-primary)' }}>
              去充值
            </a>
          )}
        </div>
      )}

      {/* ── (1) Which article is being published ─────────────────────────────────── */}
      <section className="rounded-lg p-3"
               style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-primary break-words">
              {doc.title || `文章 #${doc.id}`}
            </p>
            <p className="text-[11px] text-muted mt-0.5">
              {docStatusText}
              {doc.created_at ? ` · 生成于 ${fmtTime(doc.created_at, false)}` : ''}
              {wordCount ? ` · 约 ${wordCount} 字` : ''}
            </p>
          </div>
          {doc.body_markdown && (
            <button type="button" onClick={() => setShowArticle(v => !v)}
                    className="text-xs px-2.5 py-1 rounded-md whitespace-nowrap"
                    style={{ background: 'var(--bg-input)', color: 'var(--accent-primary)',
                             border: '1px solid var(--border-color)' }}>
              {showArticle ? '收起正文' : '查看正文'}
            </button>
          )}
        </div>
        {!showArticle && doc.summary && (
          <p className="text-[11px] text-secondary mt-1.5 line-clamp-2">{doc.summary}</p>
        )}
        {showArticle && doc.body_markdown && (
          <div className="mt-2 rounded-md p-2.5 overflow-y-auto text-xs"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                        maxHeight: 320, whiteSpace: 'pre-wrap', color: 'var(--text-secondary)' }}>
            {doc.body_markdown}
          </div>
        )}
      </section>

      {!canPublish && (
        <div className="rounded-md p-2.5 text-xs"
             style={{ background: 'rgba(245,158,11,0.08)', color: 'var(--warn-fg)',
                      border: '1px solid rgba(245,158,11,0.4)' }}>
          稿件尚未通过审核,暂不能新建投放;已有任务仍可查看与回填。
        </div>
      )}

      {/* ── (2) Where to place it ───────────────────────────────────── */}
      {canPublish && (
        <section className="rounded-lg p-3 space-y-2"
                 style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold text-primary">投到哪儿</p>
            {/* Placement suggestions downgraded to one line: it's a reference clue and shouldn't compete with "go place" as a peer entry */}
            <button type="button" onClick={() => setShowSuggest(v => !v)}
                    className="text-[11px] underline" style={{ color: 'var(--text-secondary)' }}>
              {showSuggest ? '收起投放建议' : suggestSummary}
            </button>
          </div>

          {showSuggest && (
            <div className="rounded-md p-2.5 space-y-2"
                 style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <p className="text-[11px] text-muted">
                  基于历史被引、媒体归因和 AI 常引用外部域名给出的投放线索。
                </p>
                {(!strategy || strategy.status === 'idle' || strategy.status === 'failed') && (
                  <button type="button" onClick={generateStrategy}
                          className="text-xs px-2.5 py-1 rounded-md whitespace-nowrap"
                          style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                                   border: '1px solid var(--border-color)' }}>
                    {strategy?.status === 'failed' ? '重新生成' : '生成投放建议'}
                  </button>
                )}
              </div>
              {strategyErr && <div className="text-[11px]" style={{ color: 'var(--danger-fg)' }}>{strategyErr}</div>}
              {strategy?.status === 'generating' && (
                <p className="text-[11px] text-muted">正在分析该主题的引用数据,约需 1-2 分钟…</p>
              )}
              {strategy?.status === 'failed' && strategy.error && (
                <p className="text-[11px]" style={{ color: 'var(--danger-fg)' }}>{strategy.error}</p>
              )}
              {strategy?.status === 'ready' && recommendations.map((r, i) => {
                const entry = normalizePlatform(r.platform);
                const badge = entry ? attribution.get(entry.key) : undefined;
                return (
                  <div key={i} className="rounded-md px-2.5 py-2 text-xs flex items-center gap-2 flex-wrap"
                       style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium text-white"
                          style={{ background: r.priority === 'P0' ? 'var(--danger-solid)' : r.priority === 'P1' ? 'var(--warn-solid)' : '#6b7280' }}>
                      {r.priority}
                    </span>
                    <span className="text-primary font-medium">{r.platform}</span>
                    {r.content_type && <span className="text-muted">{r.content_type}</span>}
                    <Badge b={badge} />
                    <p className="w-full text-muted leading-relaxed">{r.reason}</p>
                  </div>
                );
              })}
              {attributionRaw.length > 0 && (
                <p className="text-[11px] text-muted">
                  历史投放:{attributionRaw.slice(0, 5).map(r =>
                    `${r.platform} ${r.cited_docs}/${r.published_docs}篇被引`).join(' · ')}
                </p>
              )}
              {externalSources.length > 0 && (
                <div>
                  <button type="button" onClick={() => setShowSources(v => !v)}
                          className="text-[11px] text-muted underline">
                    {showSources ? '收起' : '查看'} AI 常引用的外部媒体域名({externalSources.length})
                  </button>
                  {showSources && (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {externalSources.map((s, i) => (
                        <span key={i} className="text-[11px] px-2 py-0.5 rounded-full"
                              style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}
                              title={s.title || s.url}>
                          {s.domain} ×{s.count}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Four destinations laid out flat; none hidden behind a tab */}
          <div className="space-y-1.5">
            {onPublishMediumsly && (
              <ChannelRow icon="🏠" accent="59,130,246"
                          title="官网博客" hint="发布到自有站点,上线后自动记录文章地址"
                          actionLabel={busy ? '发布中…' : '发布'}
                          disabled={busy} onAction={publishMediumsly} />
            )}
            {!tasks.some(isPaidMediaTask) && (
              <ChannelRow icon="✦" accent="139,92,246"
                          title="AI 自动选媒" hint="按归因表现、内容类型和预算线索自动匹配付费媒体"
                          actionLabel={autoSelecting ? '选媒中…' : '选媒'}
                          disabled={autoSelecting} onAction={runAutoSelect} />
            )}
            <ChannelRow icon="🎯" accent="245,158,11" open={showManualPick}
                        title="付费媒体" hint="按类型 / 价格 / 地区挑选媒体,可勾好直接发布"
                        actionLabel={showManualPick ? '收起' : '选择'}
                        onAction={() => setShowManualPick(v => !v)} />
            {showManualPick && (
              <div className="rounded-md p-2.5"
                   style={{ background: 'var(--bg-card)', border: '1px solid rgba(245,158,11,0.4)' }}>
                <MediaLibrarySection doc={doc} token={token}
                                     mediaAttribution={mediaAttribution}
                                     onPushed={async () => {
                                       setShowManualPick(false);
                                       await loadTasks();
                                       onChanged();
                                     }}
                                     onPushedAndPay={async ids => {
                                       // collapse the media selection panel; results (per-outlet success/failure) are shown in the progress area below
                                       setShowManualPick(false);
                                       await payTaskIds(ids);
                                     }} />
              </div>
            )}
            <ChannelRow icon="📣" accent="16,185,129" open={showAccounts}
                        title="自有账号" hint="自建账号池 / 浏览器插件,一次发多个平台"
                        actionLabel={showAccounts ? '收起' : '选择'}
                        onAction={() => setShowAccounts(v => !v)} />
            {showAccounts && (
              <div className="rounded-md p-2.5"
                   style={{ background: 'var(--bg-card)', border: '1px solid rgba(16,185,129,0.35)' }}>
                <UnifiedTargetList doc={doc} token={token}
                                   allowBackendChannels={isStaff || isAgency}
                                   verificationOwnerKey={user?.id
                                     ? ctripVerificationOwnerKey(user.id) : undefined}
                                   onPublished={async () => { await loadTasks(); onChanged(); }} />
              </div>
            )}
            <ChannelRow icon="🔗" accent="100,116,139" open={showCustomRefill}
                        title="已在别处发过" hint="手动登记一个已发布链接,纳入引用归因与复测"
                        actionLabel={showCustomRefill ? '收起' : '登记'}
                        onAction={() => setShowCustomRefill(v => !v)} />
            {showCustomRefill && (
              <div className="rounded-md p-2.5 grid grid-cols-1 md:grid-cols-[180px_1fr_auto] gap-1.5"
                   style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                <input type="text" value={customRefillName}
                       onChange={e => setCustomRefillName(e.target.value)}
                       placeholder="名称,如:行业媒体报道"
                       className="text-xs px-2.5 py-1.5 rounded-md"
                       style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }} />
                <input type="url" value={customRefillUrl}
                       onChange={e => setCustomRefillUrl(e.target.value)}
                       placeholder="文章地址 https://..."
                       className="text-xs px-2.5 py-1.5 rounded-md"
                       style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }} />
                <button type="button" disabled={busy || !customRefillName.trim() || !customRefillUrl.trim()}
                        onClick={submitCustomRefill}
                        className="text-xs px-3 py-1.5 rounded-md text-white whitespace-nowrap"
                        style={{ background: 'var(--success-solid)',
                                 opacity: (busy || !customRefillName.trim() || !customRefillUrl.trim()) ? 0.5 : 1 }}>
                  登记
                </button>
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── (3) Placement progress ───────────────────────────────────── */}
      <section className="rounded-lg p-3 space-y-2"
               style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <p className="text-xs font-semibold text-primary">
            投放进度
            {progressTasks.length > 0 && (
              <span className="ml-1.5 text-[11px] font-normal text-muted">
                {publishedCount}/{progressTasks.length} 已投放
                {refillCount > 0 ? ` · ${refillCount} 待回填` : ''}
              </span>
            )}
          </p>
          {/* Batch payment: when the same draft is placed with several outlets, select and charge once */}
          {payableTasks.length > 1 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] text-muted">
                {batchPicked.size > 0 ? `已勾 ${batchPicked.size} 家` : `${payableTasks.length} 家待付费`}
              </span>
              <button type="button"
                      onClick={() => {
                        setBatchPreview(null); setBatchResult(null);
                        setBatchPicked(batchPicked.size === payableTasks.length
                          ? new Set() : new Set(payableTasks.map(t => t.id)));
                      }}
                      className="text-[11px] px-2 py-1 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                {batchPicked.size === payableTasks.length ? '取消全选' : '全选'}
              </button>
              <button type="button" disabled={busy || batchPicked.size === 0} onClick={payBatch}
                      className="text-[11px] px-2.5 py-1 rounded-md text-white"
                      style={{ background: 'var(--warn-solid)',
                               opacity: (busy || batchPicked.size === 0) ? 0.5 : 1 }}>
                {busy ? '下单中…'
                  : `发布勾选${pickedTotalPoints ? ` · 扣 ${pickedTotalPoints} 积分` : ''}`}
              </button>
            </div>
          )}
        </div>

        {batchResult && (
          <div className="rounded-md px-2.5 py-2 text-[11px] space-y-0.5"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <div style={{ color: 'var(--success-fg)' }}>成功 {batchResult.succeeded.length} 家</div>
            {batchResult.failed.map((f, i) => (
              <div key={i} style={{ color: 'var(--danger-fg)' }}>
                失败:{f.media_name || `任务 ${f.task_id}`} —— {f.message}
              </div>
            ))}
            {batchResult.skipped.length > 0 && (
              <div style={{ color: 'var(--warn-fg)' }}>
                已中止,{batchResult.skipped.length} 家未执行(仍是待付费,可稍后重试)
              </div>
            )}
          </div>
        )}
        {batchPreview && batchPreview.price_changed_count > 0 && (
          <p className="text-[11px]" style={{ color: 'var(--warn-fg)' }}>
            下单时有 {batchPreview.price_changed_count} 家实时报价与页面价不同,已按实时价结算。
          </p>
        )}

        {progressTasks.length === 0 ? (
          <p className="text-[11px] text-muted">
            还没有投放任务。用上面的「投到哪儿」挑一个去处开始投放。
          </p>
        ) : progressTasks.map(tk => {
          const isRwj = isPaidMediaTask(tk);
          const isAe = isAitoearnTask(tk);
          // 2026-08-05 -- paid placement opened to users (backend validates draft ownership), no longer staff-only
          const rwjCanPay = isRwj && (tk.status === 'planned' || tk.status === 'failed');
          const rwjCanPull = isRwj && (tk.status === 'publishing' || tk.status === 'pending_url');
          const aeCanPull = isAe && (tk.status === 'publishing' || tk.status === 'pending_url');
          const needUrl = needsUrlRefill(tk);
          const candidateUrl = candidateUrlOf(tk);
          const checking = tk.status === 'url_submitted';
          // Can it be withdrawn: paid only while planned (after ordering the money is out; that's a refund, not a withdrawal);
          // manual tasks can be withdrawn any time before actually publishing. The backend blocks again when costs have been incurred.
          const canWithdraw = isRwj
            ? tk.status === 'planned'
            : ['planned', 'pending_publish', 'pending_url'].includes(tk.status);
          const verification = tk.channel_meta?.url_verification;
          const platformApiChecking = checking && verification?.verification_method === 'platform_api';
          const blocked = checking && (tk.last_error || '').includes('检测受阻');
          const done = tk.status === 'published';
          return (
            <div key={tk.id} className="rounded-md px-2.5 py-2 text-xs"
                 style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="text-primary font-medium">
                  {rwjCanPay && payableTasks.length > 1 && (
                    <input type="checkbox" className="mr-1.5 align-middle"
                           checked={batchPicked.has(tk.id)}
                           onChange={() => toggleBatchPick(tk.id)} />
                  )}
                  {defaultTaskName(tk)}
                </span>
                <span className="px-2 py-0.5 rounded-full text-[11px] whitespace-nowrap"
                      style={{
                        background: tk.status === 'failed' ? 'var(--danger-bg)'
                          : done ? 'var(--success-bg)' : 'var(--warn-bg)',
                        color: tk.status === 'failed' ? 'var(--danger-fg)'
                          : done ? 'var(--success-fg)' : 'var(--warn-fg)',
                      }}>
                  {platformApiChecking
                    ? verification?.status === 'published_pending_confirmation' ? '已上线，待最终确认'
                      : verification?.status === 'reviewing' ? '携程审核中'
                      : verification?.status === 'not_found' ? '等待携程同步'
                      : verification?.status === 'draft' ? '携程草稿状态，继续观察'
                      : verification?.status === 'query_error' ? '查询失败，等待重试'
                      : '等待携程首次审核'
                    : checking ? (blocked ? '检测受阻' : '待验证') : taskStatusLabel(tk)}
                </span>
              </div>
              {isRwj && tk.channel_meta && (
                <div className="text-muted mt-0.5">
                  {/* ★ This **must not** print channel_meta.quoted_price -- that's the **upstream cost price**
                      (the comment at rwj_channel.py:674 says "internal metric definition; don't display it or compare against it").
                      Retail quote = max(upstream x markup rate, floor), and the two can differ noticeably: media costing ¥3 upstream
                      is ¥4 retail. Printing the upstream price means the card says "quote ¥3" while the button says "deduct 400 credits",
                      and users clicking with a ¥3 expectation get charged 400. The single source of the markup rule is
                      services/rwj_pricing; anywhere a price is shown must read the retail_price_yuan it computes. */}
                  报价 ¥{tk.retail_price_yuan || '—'}
                  {tk.channel_meta.order_no ? ` · 订单号 ${tk.channel_meta.order_no}` : ''}
                  {tk.cost_cents != null ? ` · 实扣 ${tk.cost_cents} 积分` : ''}
                </div>
              )}
              {isRwj && !!tk.channel_meta?.dropped_images?.length && (
                <div className="mt-0.5" style={{ color: 'var(--warn-fg)' }}
                     title={tk.channel_meta.dropped_images.map(d => `${d.name || d.url}:${d.reason}`).join('\n')}>
                  ⚠ {tk.channel_meta.dropped_images.length} 张配图没带上(
                  {tk.channel_meta.dropped_images.map(d => d.reason).filter((r, i, a) => a.indexOf(r) === i).join('、')}
                  ),稿件已按无图推送
                </div>
              )}
              {platformApiChecking && verification?.next_at && (
                <div className="text-muted mt-0.5">下次检查：{fmtTime(verification.next_at)}</div>
              )}
              {tk.channel_meta?.auto_selected && tk.channel_meta.select_reason && (
                <div className="mt-0.5" style={{ color: 'var(--success-fg)' }}>
                  ✦ 系统自动选媒:{tk.channel_meta.select_reason}
                </div>
              )}
              {tk.published_url && (
                <a href={tk.published_url} target="_blank" rel="noreferrer"
                   className="underline opacity-80 hover:opacity-100 break-all"
                   style={{ color: 'var(--info-fg)' }}>{tk.published_url}</a>
              )}
              {!tk.published_url && candidateUrl && (
                <div className="mt-1">
                  <a href={candidateUrl} target="_blank" rel="noreferrer"
                     className="underline opacity-80 hover:opacity-100 break-all"
                     style={{ color: 'var(--info-fg)' }}>{candidateUrl}</a>
                  <span className="ml-1.5 text-[10px]" style={{ color: 'var(--warn-fg)' }}>
                    {verification?.status === 'rejected'
                      ? '审核失败 · 仅供审计，不计入发布'
                      : verification?.status === 'published_pending_confirmation'
                        ? '已上线，待最终确认' : '候选链接'}
                  </span>
                </div>
              )}
              {tk.last_error && <div style={{ color: 'var(--danger-fg)' }}>{tk.last_error}</div>}
              {/* Backfill happens right in the task row -- pending backfill is just one state of this task */}
              {needUrl && (
                <div className="flex items-center gap-1.5 mt-1.5">
                  <input type="url" value={urlDraft[tk.id] || ''}
                         onChange={e => setUrlDraft(d => ({ ...d, [tk.id]: e.target.value }))}
                         placeholder="回填已发布文章的完整地址 https://..."
                         className="text-xs px-2.5 py-1.5 rounded-md flex-1"
                         style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                                  border: '1px solid var(--border-color)' }} />
                  <button type="button" disabled={busy || !(urlDraft[tk.id] || '').trim()}
                          onClick={() => submitUrl(tk.id)}
                          className="text-xs px-3 py-1.5 rounded-md text-white whitespace-nowrap"
                          style={{ background: 'var(--success-solid)',
                                   opacity: (busy || !(urlDraft[tk.id] || '').trim()) ? 0.5 : 1 }}>
                    回填链接
                  </button>
                </div>
              )}
              {(rwjCanPay || rwjCanPull || aeCanPull || canWithdraw || (checking && candidateUrl)) && (
                <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                  {rwjCanPay && (
                    <button type="button" disabled={busy} onClick={() => payTask(tk)}
                            className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                            style={{ background: 'var(--warn-bg)', color: 'var(--warn-fg)',
                                     border: '1px solid rgba(245,158,11,0.4)', opacity: busy ? 0.5 : 1 }}>
                      {tk.retail_price_yuan
                        ? `发布 · 扣 ${Math.round(Number(tk.retail_price_yuan) * 100)} 积分`
                        : '发布(付费)'}
                    </button>
                  )}
                  {canWithdraw && (
                    <button type="button" disabled={busy} onClick={() => withdrawTask(tk)}
                            className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                            style={{ background: 'transparent', color: 'var(--text-secondary)',
                                     border: '1px solid var(--border-color)',
                                     opacity: busy ? 0.5 : 1 }}>
                      撤回
                    </button>
                  )}
                  {(rwjCanPull || aeCanPull) && (
                    <button type="button" disabled={busy}
                            onClick={() => (rwjCanPull ? rwjPull(tk.id) : aitoearnPull(tk.id))}
                            className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                            style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                                     border: '1px solid var(--border-color)', opacity: busy ? 0.5 : 1 }}>
                      拉取回执
                    </button>
                  )}
                  {checking && candidateUrl && !platformApiChecking && (
                    <button type="button" disabled={busy} onClick={() => confirmCandidateUrl(tk.id)}
                            className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                            style={{ background: 'var(--success-bg)', color: 'var(--success-fg)',
                                     border: '1px solid rgba(16,185,129,0.35)', opacity: busy ? 0.5 : 1 }}>
                      人工确认有效
                    </button>
                  )}
                  {platformApiChecking && (
                    <button type="button" disabled={busy}
                            onClick={() => refreshCtripVerification(tk.id)}
                            className="text-xs px-3 py-1.5 rounded-md whitespace-nowrap"
                            style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                                     border: '1px solid var(--border-color)', opacity: busy ? 0.5 : 1 }}>
                      刷新审核状态
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </section>
    </>
  );

  // Full-page layout: standalone publish page (four-level breadcrumbs), hosted in a bg-card shell, same source as the detail page asPage.
  if (asPage) {
    return (
      <div className="rounded-xl overflow-hidden"
           style={{ border: '1px solid var(--border-color)', background: 'var(--bg-card)' }}>
        <div className="p-4" style={{ borderBottom: '1px solid var(--border-color)' }}>
          <h2 className="text-base font-semibold text-primary">发布中心</h2>
          <p className="text-[11px] text-muted mt-0.5">
            挑去处 → 投出去 → 回填链接,回填后进入复测与引用归因
          </p>
        </div>
        <div className="p-4 space-y-3">{body}</div>
      </div>
    );
  }

  return (
    <Drawer
      open={active} onClose={onClose} level={2}
      widthClass="w-full md:w-[80%] max-w-5xl"
      header={
        <>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-primary">发布中心</h2>
            <p className="text-[11px] text-muted mt-0.5">
              挑去处 → 投出去 → 回填链接,回填后进入复测与引用归因
            </p>
          </div>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-xl leading-none">✕</button>
        </>
      }
    >
      {body}
    </Drawer>
  );
}

/** One destination row in "where to place it": icon + name + one-liner + one action button.
 *  All five destinations share one layout, so each channel doesn't write its own card style (which is how it used to be: four borders, four corner radii). */
function ChannelRow({ icon, accent, title, hint, actionLabel, onAction, disabled, open }: {
  icon: string;
  /** "R,G,B" -- the channel's accent color, used to tint the icon and the selected-state outline */
  accent: string;
  title: string;
  hint: string;
  actionLabel: string;
  onAction: () => void;
  disabled?: boolean;
  /** Expanded state: heavier outline, joining the panel expanded below into one piece */
  open?: boolean;
}) {
  return (
    <div className="rounded-md px-2.5 py-2 flex items-center justify-between gap-3"
         style={{ background: `rgba(${accent},0.06)`,
                  border: `1px solid rgba(${accent},${open ? 0.55 : 0.22})` }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="w-7 h-7 rounded-lg flex items-center justify-center text-sm shrink-0"
              style={{ background: `rgba(${accent},0.14)`, color: `rgb(${accent})` }}>{icon}</span>
        <div className="min-w-0">
          <p className="text-xs font-medium text-primary">{title}</p>
          <p className="text-[11px] text-muted truncate">{hint}</p>
        </div>
      </div>
      <button type="button" disabled={disabled} onClick={onAction}
              className="h-7 px-2.5 rounded-md text-xs font-medium whitespace-nowrap"
              style={{ background: open ? `rgb(${accent})` : `rgba(${accent},0.14)`,
                       color: open ? '#ffffff' : `rgb(${accent})`,
                       border: `1px solid rgba(${accent},0.38)`,
                       opacity: disabled ? 0.6 : 1 }}>
        {actionLabel}
      </button>
    </div>
  );
}
