// Admin content review -- pick account + topic -> view the document list -> check to submit for review -> approve + choose publish platform/media (or reject).
// Route: /ops/content-review

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { useAuth } from '../../contexts/AuthContext';
import { useRegisterAgentActions } from '../../components/AgentChat/agentActions';
import { Pagination } from '../../components/Pagination';
import { Drawer } from '../../components/overlays/Drawer';
import { PublishCenterDrawer } from '../../components/publish/PublishCenterDrawer';
import { contentApi, type PublishTask } from '../../services/contentApi';
import {
  isPaidMediaTask, taskDisplayName, taskStatusLabel,
} from '../../components/publish/publishShared';
import {
  adminContentReviewApi,
  type DocQuality, type DocSource, type DocStatus, type GeneratedDoc, type RetestProbe,
  type TopicWithDocs,
} from '../../services/adminContentReviewApi';
import { RichTextBodyEditor } from '../../components/RichTextBodyEditor';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { UiSelect } from '../../components/ui/Select';
import { TableShell, StickyHead } from '../../components/ui/DataTable';
import { fmtTime } from '../../utils/datetime';
import { UiMultiSelect } from '../../components/ui/Select';
import {
  FilterBar, FilterBarPrimary, FilterBarSecondary, FilterField, FilterSummary, DateRangeFilter,
  SearchInput, Segmented,
} from '../../components/ui/FilterBar';
import {
  DATE_RANGE_ALL, inDateRange, isDateFilterActive, type DateRangeValue,
} from '../../components/ui/dateFilter';
import { FILTER_CONTROL_CLASS, FILTER_CONTROL_STYLE } from '../../components/ui/filterControls';
import { MarkdownPreview } from '../../components/ui/MarkdownEditor';
import { DocRelationBlock } from '../../components/DocRelationBlock';
import { DocWhy } from '../../components/DocWhy';
import { copyArticle, copyMarkdown } from '../../utils/copyDoc';

// Status / source are both multi-select: empty array = no filter. Deep links and the agent still pass the old single-value form,
// translated into arrays in statusesFromParam (to_review = draft + pending_review).
type StatusFilter = DocStatus | 'to_review' | 'all';
type TemplateFilter = string;   // template id as a string; 'none' = drafts without a template

// Auto-retry cap for generation failures -- aligned with the backend GEO_GEN_RETRY_MAX default (services/gen_retry.py),
// only used for display copy like "2/3"; the decision is still made by the backend.
const GEN_RETRY_MAX = 3;

// The body has been regenerated -- regeneration overwrites in place, created_at stays, only updated_at moves forward.
// Leave a 60-second tolerance: writing the body after creating the draft takes tens of seconds anyway, which doesn't count as a "rewrite".
function isRewritten(d: GeneratedDoc): boolean {
  if (!d.updated_at) return false;
  const c = new Date(d.created_at).getTime();
  const u = new Date(d.updated_at).getTime();
  return Number.isFinite(c) && Number.isFinite(u) && u - c > 60_000;
}
// Refined-writing review pass line -- aligned with the backend GEO_CONTENT_QUALITY_MIN_SCORE default; only affects colors.
const QUALITY_PASS = 80;
const QUALITY_DIM_ORDER = ['coverage', 'quotability', 'evidence', 'brand', 'format'] as const;
const QUALITY_DIM_LABELS: Record<(typeof QUALITY_DIM_ORDER)[number], string> = {
  coverage: '问题覆盖', quotability: '可引用性', evidence: '事实与信息量',
  brand: '品牌一致', format: '排版篇幅',
};
// Next retry time: fmtTime already handles naive UTC -> Beijing time; this just drops the seconds.
const fmtRetryAt = (iso?: string | null): string => (iso ? fmtTime(iso, false) : '—');

// Status items in the multi-select dropdown. 'all' / 'to_review' are **entry-point forms** (deep links, to-do cards, agent),
// not in the dropdown -- the dropdown only has real statuses, and "all" is expressed by "none selected".
const STATUS_OPTIONS: DocStatus[] = [
  'draft', 'pending_review', 'approved', 'rejected', 'published',
];

/** Single-value form -> multi-select array. Both the deep link ?status= and the agent's setStatusFilter go through it. */
function statusesFromParam(v: string | null | undefined): DocStatus[] {
  if (!v || v === 'all') return [];
  if (v === 'to_review') return ['draft', 'pending_review'];
  return STATUS_OPTIONS.includes(v as DocStatus) ? [v as DocStatus] : [];
}

/** Multi-select array -> single-value form, written back into the detail page URL so "back to list" restores the same filters. */
function statusToParam(list: DocStatus[]): string {
  if (list.length === 0) return '';
  if (list.length === 1) return list[0];
  if (list.length === 2 && list.includes('draft') && list.includes('pending_review')) return 'to_review';
  return '';
}

// 2026-05-28 -- readable labels for multi-variant combos (synced with BrandProfileForm candidates)
const CREATION_DIRECTION_LABELS: Record<string, string> = {
  industry_insight: '行业洞察', case_story: '案例分享', how_to_guide: '实操指南',
  trend_forecast: '趋势预测', product_review: '产品评测', customer_story: '客户故事',
  faq: 'FAQ 答疑',
};
const COPYWRITING_TYPE_LABELS: Record<string, string> = {
  long_form: '深度长文', medium_post: '中等图文', short_social: '短社媒',
  video_script_long: '长视频脚本', video_script_short: '短视频文案', faq_list: 'FAQ 列表',
};
interface AdminContentReviewProps {
  // Lock to a given topic; if given, hide the topic selector (for embedding in the project detail page stepper)
  lockedTopicId?: number;
}

export function AdminContentReview({ lockedTopicId }: AdminContentReviewProps = {}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const inTopicStepper = lockedTopicId !== undefined;

  // Deep links: ?topic=X jumps to a given project, ?status=Y to a given status (used by stepper / cockpit to-do cards)
  // If lockedTopicId is given, it wins and the URL is ignored entirely.
  const initialUrlParams = useMemo(() => {
    const sp = new URLSearchParams(window.location.search);
    const topicParam = sp.get('topic');
    const statusParam = sp.get('status');
    return {
      topic: lockedTopicId ?? (topicParam ? Number(topicParam) : null),
      statuses: statusesFromParam(statusParam),
    };
  }, [lockedTopicId]);

  const [topics, setTopics] = useState<TopicWithDocs[]>([]);
  const [topicId, setTopicId] = useState<number | null>(initialUrlParams.topic);
  const [docs, setDocs] = useState<GeneratedDoc[]>([]);
  // doc_id -> placement tasks (for list row badges)
  const [docTasks, setDocTasks] = useState<Map<number, PublishTask[]>>(new Map());
  const [statuses, setStatuses] = useState<DocStatus[]>(initialUrlParams.statuses);
  const [sources, setSources] = useState<DocSource[]>([]);
  const [templates, setTemplates] = useState<TemplateFilter[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  // 2026-05-28 -- multiple drafts for the same query fold together; queries in the set are collapsed
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = useCallback((q: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(q)) next.delete(q); else next.add(q);
      return next;
    });
  }, []);
  const [searchText, setSearchText] = useState('');
  // Time filtering is by **generation time** (created_at) -- "which drafts came out today" is this page's main scenario
  const [dateRange, setDateRange] = useState<DateRangeValue>(DATE_RANGE_ALL);
  // Sort by generation time: newest = newest first (default), oldest = oldest first
  // Generation time = created_at (the moment the draft was created, unchanged by rewrites); update time = updated_at (the last write).
  // Regeneration overwrites in place, so only sorting by update time brings freshly rewritten drafts to the top.
  // Default is update time descending: regeneration overwrites in place, and sorting by generation time sinks freshly rewritten drafts back to their creation day.
  const [sortOrder, setSortOrder] =
    useState<'newest' | 'oldest' | 'updated' | 'updated_oldest'>('updated');
  // 2026-07-31 -- list view: time = flat by generation time (default), query = grouped and folded by prompt/seed
  const [viewMode, setViewMode] = useState<'time' | 'query'>('time');
  const [page, setPage] = useState(1);
  // Same metric definition as the customer-side content library (Dashboard/Content): 10 per page by default, adjustable.
  // This used to be a hardcoded PAGE_SIZE = 20, and the pagination UI was this page's own hand-written set --
  // the two pages list the same drafts but paged differently. Now both use the site-wide unified Pagination.
  const [pageSize, setPageSize] = useState(10);

  // Copy re-review is a workbench module (backend require_workbench): both admin and operator can enter
  const { isStaff } = useAuth();

  const refreshTopics = useCallback(async () => {
    try {
      const rs = await adminContentReviewApi.listTopics(token);
      setTopics(rs);
      // lockedTopicId mode isn't auto-overridden; the lock stays
      if (!inTopicStepper && rs.length > 0 && topicId === null) {
        setTopicId(rs[0].topic_id);
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [token, topicId, inTopicStepper]);

  const refreshDocs = useCallback(async () => {
    if (topicId === null) return;
    setLoading(true); setErr(null);
    try {
      // Fetch everything at once; status / source / template are all filtered on the frontend -- multi-select can't be expressed
      // with the backend's single-value params, and changing filters no longer hits the API, so switching is instant.
      const ds = await adminContentReviewApi.listDocs(topicId, undefined, token);
      setDocs(ds);
      // Fetch placement tasks for all topics at once, grouped by draft -> list rows show task badges directly (Ruanwenjiang etc.)
      contentApi.listPublishTasks(topicId, {}, token)
        .then(ts => {
          const m = new Map<number, PublishTask[]>();
          for (const t of ts) {
            const arr = m.get(t.doc_id) || [];
            arr.push(t); m.set(t.doc_id, arr);
          }
          setDocTasks(m);
        })
        .catch(() => { /* task badge failure doesn't affect the list */ });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [topicId, token]);

  useEffect(() => {
    if (!isStaff) { navigate('/app', { replace: true }); return; }
    refreshTopics();
  }, [isStaff, navigate, refreshTopics]);

  useEffect(() => { refreshDocs(); }, [refreshDocs]);

  const togglePick = (id: number) => {
    setPicked(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  // "View": go to the standalone detail page (direct URL / back navigation / shareable).
  // In embedded stepper mode, carry the topic-step6 context; the detail page uses it to render project breadcrumbs and return to step 6.
  const openDoc = (d: GeneratedDoc) => {
    const qs = new URLSearchParams();
    const statusParam = statusToParam(statuses);
    if (topicId != null) qs.set('topic', String(topicId));
    if (statusParam) qs.set('status', statusParam);
    if (inTopicStepper && topicId != null) {
      qs.set('context', 'topic-step6');
      const returnParams = new URLSearchParams({ step: '6' });
      if (statusParam) returnParams.set('status', statusParam);
      qs.set('returnTo', `/ops/topics/${topicId}/edit?${returnParams.toString()}`);
    }
    const suffix = qs.toString();
    navigate(`/ops/content-review/${d.id}${suffix ? `?${suffix}` : ''}`);
  };

  // 2026-07-31 -- jump from a draft back to the publish plan table (the "Plan #N" badge on the card).
  // In embedded stepper mode, return to step 5 (plan document); standalone mode goes to the full plan document page.
  const openPlan = () => {
    if (topicId == null) return;
    navigate(inTopicStepper
      ? `/ops/topics/${topicId}/edit?step=5`
      : `/ops/topics/${topicId}/execution-plan`);
  };

  const sendToReview = async () => {
    if (picked.size === 0) return;
    try {
      await adminContentReviewApi.selectForReview(Array.from(picked), token);
      setPicked(new Set());
      refreshDocs(); refreshTopics();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  // Template filter items: distinct values from loaded docs; drafts without a template get their own bucket
  const templateOptions = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of docs) {
      if (d.template_id) m.set(String(d.template_id), d.template_name || `#${d.template_id}`);
    }
    const list = [...m.entries()].sort((a, b) => a[1].localeCompare(b[1], 'zh'))
      .map(([value, label]) => ({ value, label }));
    if (docs.some(d => !d.template_id)) list.push({ value: 'none', label: '无模板' });
    return list;
  }, [docs]);

  // Status / source / template / search are all filtered + sorted + sliced on the client.
  const filteredDocs = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    const statusSet = new Set<string>(statuses);
    const sourceSet = new Set<string>(sources);
    const tplSet = new Set<string>(templates);
    const base = docs.filter(d => {
      if (statusSet.size && !statusSet.has(d.status)) return false;
      if (sourceSet.size && !sourceSet.has(d.source)) return false;
      if (tplSet.size && !tplSet.has(d.template_id ? String(d.template_id) : 'none')) return false;
      if (!inDateRange(d.created_at, dateRange)) return false;
      if (!q) return true;
      const hay = [d.title, d.summary, d.source_query_text, d.template_name]
        .filter(Boolean).join(' ').toLowerCase();
      return hay.includes(q);
    });
    // Use id as a tiebreaker for identical generation times within a batch, keeping order stable
    const byUpdated = sortOrder === 'updated' || sortOrder === 'updated_oldest';
    const ts = (d: GeneratedDoc) => new Date(
      (byUpdated ? (d.updated_at || d.created_at) : d.created_at)).getTime() || 0;
    const desc = sortOrder === 'newest' || sortOrder === 'updated';
    return [...base].sort((a, b) => (
      desc ? ts(b) - ts(a) || b.id - a.id : ts(a) - ts(b) || a.id - b.id
    ));
  }, [docs, searchText, sortOrder, statuses, sources, templates, dateRange]);
  const totalPages = Math.max(1, Math.ceil(filteredDocs.length / pageSize));
  const filterActive = statuses.length > 0 || sources.length > 0
    || templates.length > 0 || searchText.trim() !== '' || isDateFilterActive(dateRange);
  const resetFilters = useCallback(() => {
    setStatuses([]); setSources([]); setTemplates([]); setSearchText('');
    setDateRange(DATE_RANGE_ALL);
  }, []);
  useEffect(() => {
    setPage(1);
  }, [searchText, statuses, sources, templates, topicId, sortOrder, dateRange, pageSize]);
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  const pagedDocs = useMemo(
    () => filteredDocs.slice((page - 1) * pageSize, page * pageSize),
    [filteredDocs, page, pageSize],
  );

  // ── Agent page actions (batch 1: read-only) ────────────────────────────────────
  // **setTopic is not registered**: the page has a project selector, but "operate only on the current project" is the mechanism's bottom line for this integration,
  // and the agent can't touch it. To switch projects, the user picks on the page themselves.
  // The snapshot must **never** include the topics array -- each item carries a customer email, which would land in ops' own session history via the snapshot,
  // mixing data from N customers served by the same ops person into one session.
  const reviewSnapshot = useMemo(() => {
    const funnel: Record<string, number> = {};
    docs.forEach((d) => { funnel[d.status] = (funnel[d.status] || 0) + 1; });
    return {
      当前项目id: topicId,
      状态筛选: statuses.length ? statuses : '全部',
      来源筛选: sources.length ? sources : '全部',
      模板筛选: templates.length ? templates : '全部',
      视图: viewMode === 'time' ? '按时间' : '按提示词',
      排序: sortOrder,
      搜索词: searchText,
      第几页: `${page}/${totalPages}`,
      命中篇数: filteredDocs.length,
      已勾选: picked.size,
      各状态篇数: funnel,
      本页文章: pagedDocs.slice(0, 20).map((d) => ({
        id: d.id,
        标题: (d.title || '').slice(0, 30),
        状态: d.status,
        可勾选: d.status === 'draft',
        投放目标数: (d.publish_targets || []).length,
      })),
    };
  }, [docs, topicId, statuses, sources, templates, viewMode, sortOrder, searchText,
      page, totalPages, filteredDocs.length, picked.size, pagedDocs]);

  useRegisterAgentActions(isStaff ? {
    page: 'admin_content_review',
    scope: 'review',
    weight: 10,
    describe: '运营工作台 · 文章管理(稿件列表 / 筛选 / 勾选送审)',
    snapshot: reviewSnapshot,
    actions: {
      // The agent still calls with the single-value form (all / to_review / a specific status); translate it into a multi-select array here
      setStatusFilter: (a) => setStatuses(statusesFromParam(String(a.status || 'all') as StatusFilter)),
      setSourceFilter: (a) => {
        const v = String(a.source || 'all');
        setSources(v === 'ai' || v === 'user' ? [v] : []);
      },
      setViewMode: (a) => setViewMode(a.mode === 'query' ? 'query' : 'time'),
      setSort: (a) => {
        const v = String(a.order || '');
        if (!['newest', 'oldest', 'updated', 'updated_oldest'].includes(v)) throw new Error('排序值不对');
        setSortOrder(v as typeof sortOrder);
      },
      setSearch: (a) => setSearchText(String(a.text ?? '')),
      setPage: (a) => {
        const n = Number(a.page);
        if (!Number.isFinite(n) || n < 1) throw new Error('页码不对');
        setPage(Math.min(Math.max(1, Math.trunc(n)), totalPages));
      },
      // Only drafts can be checked (matching the page's checkable condition); skipped ones must be reported truthfully, not silently dropped
      selectDocs: (a) => {
        const want = (Array.isArray(a.docIds) ? a.docIds : []).map(Number).filter(Number.isFinite);
        const ok = new Set(docs.filter((d) => d.status === 'draft').map((d) => d.id));
        const kept = want.filter((i) => ok.has(i));
        const skipped = want.filter((i) => !ok.has(i));
        if (!kept.length) throw new Error('这些稿件都不是草稿,勾不了');
        setPicked(new Set(kept));
        if (skipped.length) throw new Error(`已勾选 ${kept.length} 篇;${skipped.length} 篇不是草稿被跳过`);
      },
      selectAllOnPage: () => setPicked(new Set(pagedDocs.filter((d) => d.status === 'draft').map((d) => d.id))),
      clearSelection: () => setPicked(new Set()),
      openDoc: (a) => {
        const id = Number(a.docId);
        const d = docs.find((x) => x.id === id);
        if (!d) throw new Error('当前列表里没有这篇稿件');
        openDoc(d);
      },
      openPlan: () => openPlan(),
      refresh: () => { void refreshDocs(); },
      // Persisting to DB: draft -> pending review. Reversible (can still be rejected), but still confirm once -- it pushes drafts into the re-review queue.
      sendToReview: async () => {
        if (picked.size === 0) throw new Error('还没勾选任何草稿');
        const ok = await uiConfirm(`把勾选的 ${picked.size} 篇草稿送去复审?`, {
          title: '批量送审', okText: '送审',
        });
        if (!ok) throw new Error('用户取消了送审');
        await sendToReview();
      },
    },
  } : null);

  // Non-workbench accounts: the effect above is already redirecting to /dashboard; this just avoids rendering.
  // Placed after all hooks -- an early return before hooks would make the hook call order vary with permissions.
  if (!isStaff) return null;

  return (
    <div className="space-y-4">
      {/* In locked mode the parent page owns the title (the stepper has taken over the context); don't render it twice */}
      {lockedTopicId === undefined && (
        <>
          <PageHead titleKey="admin.contentReview.title" titleFallback="内容审核" />
          <header className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <h1 className="text-xl font-semibold text-primary">{t('admin.contentReview.title')}</h1>
              <p className="text-xs text-secondary mt-0.5">{t('admin.contentReview.subtitle')}</p>
            </div>
            <button type="button" onClick={() => { refreshTopics(); refreshDocs(); }}
                    className="text-xs px-3 py-1.5 rounded-md"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}>
              ⟳ {t('admin.contentReview.refresh')}
            </button>
          </header>
        </>
      )}

      <FilterBar>
        <FilterBarPrimary
          right={
            <>
              <FilterField label={t('admin.contentReview.dateLabel', { defaultValue: '生成时间' })}>
                <DateRangeFilter value={dateRange} onChange={setDateRange} />
              </FilterField>
              <UiSelect value={sortOrder}
                        onChange={val => setSortOrder(
                          val === 'oldest' || val === 'newest' || val === 'updated_oldest'
                            ? val : 'updated')}
                        title={t('admin.contentReview.sortLabel', { defaultValue: '排序' })}
                        className={`${FILTER_CONTROL_CLASS} min-w-[150px]`}
                        style={FILTER_CONTROL_STYLE}
                        options={[
                          { value: 'updated',
                            label: t('admin.contentReview.sortUpdated', { defaultValue: '更新时间 · 最新在前' }) },
                          { value: 'updated_oldest',
                            label: t('admin.contentReview.sortUpdatedOldest', { defaultValue: '更新时间 · 最早在前' }) },
                          { value: 'newest',
                            label: t('admin.contentReview.sortNewest', { defaultValue: '生成时间 · 最新在前' }) },
                          { value: 'oldest',
                            label: t('admin.contentReview.sortOldest', { defaultValue: '生成时间 · 最早在前' }) },
                        ]} />
              {picked.size > 0 && (
                <button type="button" onClick={sendToReview}
                        className="h-8 px-3 text-xs rounded-md text-white whitespace-nowrap"
                        style={{ background: 'var(--accent-primary)' }}>
                  {t('admin.contentReview.sendToReview', { n: picked.size })}
                </button>
              )}
            </>
          }
        >
          <SearchInput
            value={searchText}
            onChange={setSearchText}
            placeholder={t('admin.contentReview.searchPlaceholder', { defaultValue: '搜标题 / 摘要 / 问题' })}
            className="w-full sm:w-[240px]"
          />
          {/* Locked mode doesn't show the topic selector (the stepper context has locked the project) */}
          {lockedTopicId === undefined && (
            <UiSelect value={String(topicId ?? '')}
                      onChange={val => { setTopicId(Number(val)); setPicked(new Set()); }}
                      title={t('admin.contentReview.topicLabel')}
                      searchable
                      searchPlaceholder={t('admin.contentReview.topicSearchPlaceholder', { defaultValue: '搜项目 / 客户…' })}
                      className={`${FILTER_CONTROL_CLASS} flex-1 min-w-[200px] max-w-[420px]`}
                      style={FILTER_CONTROL_STYLE}
                      options={topics.map(tp => ({
                        value: String(tp.topic_id),
                        label: `${tp.topic_name} · ${tp.user_email} · (${tp.draft_count}/${tp.doc_count})`,
                        keywords: `${tp.topic_name} ${tp.user_email}`,
                      }))} />
          )}
        </FilterBarPrimary>

        <FilterBarSecondary
          right={
            <>
              <Segmented
                value={viewMode}
                onChange={setViewMode}
                options={[
                  { value: 'time', label: t('admin.contentReview.viewTime', { defaultValue: '按时间' }),
                    title: '按生成时间平铺,不分组' },
                  { value: 'query', label: t('admin.contentReview.viewQuery', { defaultValue: '按提示词' }),
                    title: '同一提示词的多篇稿件折叠成一组' },
                ]}
              />
              <FilterSummary active={filterActive} matched={filteredDocs.length}
                             total={docs.length} onReset={resetFilters} />
            </>
          }
        >
          <FilterField label={t('admin.contentReview.statusLabel')}>
            <UiMultiSelect
              values={statuses}
              onChange={v => setStatuses(v as DocStatus[])}
              allLabel={t('admin.contentReview.statusFilter.all')}
              className={`${FILTER_CONTROL_CLASS} min-w-[110px]`}
              style={FILTER_CONTROL_STYLE}
              options={STATUS_OPTIONS.map(k => ({
                value: k,
                label: t(`admin.contentReview.statusFilter.${k}`),
              }))}
            />
          </FilterField>
          <FilterField label={t('admin.contentReview.sourceLabel')}>
            <UiMultiSelect
              values={sources}
              onChange={v => setSources(v as DocSource[])}
              allLabel={t('admin.contentReview.sourceFilter.all')}
              className={`${FILTER_CONTROL_CLASS} min-w-[100px]`}
              style={FILTER_CONTROL_STYLE}
              options={(['ai', 'user'] as DocSource[]).map(k => ({
                value: k,
                label: t(`admin.contentReview.sourceFilter.${k}`),
              }))}
            />
          </FilterField>
          {templateOptions.length > 0 && (
            <FilterField label={t('admin.contentReview.templateLabel', { defaultValue: '模板' })}>
              <UiMultiSelect
                values={templates}
                onChange={setTemplates}
                allLabel={t('admin.contentReview.templateAll', { defaultValue: '全部模板' })}
                className={`${FILTER_CONTROL_CLASS} min-w-[120px] max-w-[220px]`}
                style={FILTER_CONTROL_STYLE}
                options={templateOptions}
              />
            </FilterField>
          )}
        </FilterBarSecondary>
      </FilterBar>

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {loading && <div className="py-12 text-center text-sm text-muted">…</div>}

      {!loading && filteredDocs.length === 0 && (
        <div className="py-12 text-center text-sm text-muted">
          {searchText
            ? t('admin.contentReview.emptySearch', { defaultValue: '没有匹配的文档' })
            : t('admin.contentReview.empty')}
        </div>
      )}

      {!loading && filteredDocs.length > 0 && (
        <TableShell maxHeight={false}>
          <table className="w-full text-xs">
            <StickyHead>
              <th className="text-left px-2 py-2 w-8" />
              <th className="text-left px-3 py-2">标题</th>
              <th className="text-left px-3 py-2 w-[110px]">状态</th>
              <th className="text-left px-3 py-2 w-[64px]">质量</th>
              <th className="text-left px-3 py-2 w-[240px]">投放去向</th>
              <th className="text-left px-3 py-2 w-[140px]">生成时间</th>
              <th className="text-right px-3 py-2 w-[70px]" />
            </StickyHead>
            <tbody>
              {viewMode === 'time'
                // 2026-07-31 -- time view: strictly flat by generation time, not grouped by query/seed,
                // so "which drafts were generated today" is visible at a glance (default view).
                ? pagedDocs.map(d => (
                    <DocRow key={d.id} doc={d}
                            tasks={docTasks.get(d.id)}
                            pickable={d.status === 'draft'}
                            picked={picked.has(d.id)}
                            onPick={() => togglePick(d.id)}
                            onOpen={() => openDoc(d)}
                            onOpenPlan={() => openPlan()} />
                  ))
                : (() => {
                // 2026-05-28 -- when the same source_query_text has >=2 drafts, fold into a group showing a query header + children;
                // a single-draft query still renders as one row, keeping the old experience.
                const byQuery = new Map<string, typeof pagedDocs>();
                for (const d of pagedDocs) {
                  const q = (d.source_query_text || '').trim() || '(无 query)';
                  if (!byQuery.has(q)) byQuery.set(q, []);
                  byQuery.get(q)!.push(d);
                }
                const rows: ReactNode[] = [];
                for (const [query, group] of byQuery) {
                  if (group.length === 1) {
                    const d = group[0];
                    rows.push(
                      <DocRow key={d.id} doc={d}
                              tasks={docTasks.get(d.id)}
                              pickable={d.status === 'draft'}
                              picked={picked.has(d.id)}
                              onPick={() => togglePick(d.id)}
                              onOpen={() => openDoc(d)}
                              onOpenPlan={() => openPlan()} />,
                    );
                    continue;
                  }
                  const collapsed = collapsedGroups.has(query);
                  rows.push(
                    <tr key={`g:${query}`} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td colSpan={7} className="px-2 py-1.5"
                          style={{ background: 'var(--bg-secondary)' }}>
                        <button type="button" onClick={() => toggleGroup(query)}
                                className="w-full text-left flex items-center gap-2">
                          <span style={{ color: 'var(--text-secondary)' }}>{collapsed ? '▶' : '▼'}</span>
                          <span className="flex-1 truncate text-primary">提示词:{query}</span>
                          <span className="text-[10px] px-2 py-0.5 rounded-full"
                                style={{ background: 'var(--bg-card)', color: 'var(--text-muted)' }}>
                            {group.length} 篇
                          </span>
                        </button>
                      </td>
                    </tr>,
                  );
                  if (collapsed) continue;
                  for (const d of group) {
                    rows.push(
                      <DocRow key={d.id} doc={d} inGroup
                              tasks={docTasks.get(d.id)}
                              pickable={d.status === 'draft'}
                              picked={picked.has(d.id)}
                              onPick={() => togglePick(d.id)}
                              onOpen={() => openDoc(d)}
                              onOpenPlan={() => openPlan()} />,
                    );
                  }
                }
                return rows;
              })()}
            </tbody>
          </table>
        </TableShell>
      )}

      {/* Site-wide unified pagination -- the same component as the customer-side content library: range/total, page-size dropdown,
          numbered pages (with ellipsis), first/last. This used to be this page's hand-written "prev N/M next",
          so the same drafts paged differently on the two pages. */}
      <Pagination page={page} pageSize={pageSize} total={filteredDocs.length}
                  onPageChange={setPage} onPageSizeChange={setPageSize}
                  className="px-1 pt-1" />
    </div>
  );
}

// List row -- seven columns scannable on one screen: checkbox / title / status / quality / placement destination / generation time / actions.
// 2026-09-04 changed from cards to a table: cards spread out direction, style, summary, task badges and destinations,
// one item took three or four rows of height, and twenty drafts needed lots of scrolling. Secondary info (summary / direction / style / re-test) is on the detail page;
// only "which article, what status, placed or not, when generated" stays here.
function DocRow({ doc, tasks, pickable, picked, onPick, onOpen, onOpenPlan, inGroup }: {
  doc: GeneratedDoc;
  tasks?: PublishTask[]; pickable: boolean; picked: boolean;
  onPick: () => void; onOpen: () => void;
  onOpenPlan?: () => void;
  /** Child row when grouped by prompt; title indented one level */
  inGroup?: boolean;
}) {
  const { t } = useTranslation();
  // Multiple drafts under the same prompt are distinguished by (direction, style) -- in the grouped view titles alone can't tell them apart,
  // so these two fields stay, but downgraded to a line of gray text under the title instead of two colored pills.
  const variant = [
    doc.creation_direction && (CREATION_DIRECTION_LABELS[doc.creation_direction] || doc.creation_direction),
    doc.copywriting_type && (COPYWRITING_TYPE_LABELS[doc.copywriting_type] || doc.copywriting_type),
  ].filter(Boolean).join(' · ');
  // Destination = published links (publish_targets) + in-flight tasks (tasks), two sides of the same thing, merged into one column.
  // More than 2 fold into +N, fully visible on hover, so the column doesn't blow out.
  const targets = doc.publish_targets.map(p => ({
    key: `t:${p.platform}:${p.media || ''}:${p.url || ''}`,
    label: `${p.platform}${p.media ? ` / ${p.media}` : ''}`,
    url: p.url || '',
    tone: 'done' as const,
    title: '',
  }));
  const taskChips = (tasks || []).map(tk => ({
    key: `k:${tk.id}`,
    label: `${taskDisplayName(tk)} ${taskStatusLabel(tk)}`,
    url: '',
    tone: tk.status === 'published' ? ('done' as const)
      : isPaidMediaTask(tk) ? ('paid' as const) : ('pending' as const),
    title: tk.channel_meta?.select_reason || '',
  }));
  const chips = [...targets, ...taskChips];
  const shown = chips.slice(0, 2);
  const restTitle = chips.slice(2).map(c => c.label).join('、');
  const TONE = {
    done: { background: 'var(--success-bg)', color: 'var(--success-fg)' },
    paid: { background: 'var(--warn-bg)', color: '#f59e0b' },
    pending: { background: 'var(--bg-input)', color: 'var(--text-secondary)' },
  };
  return (
    <tr style={{ borderTop: '1px solid var(--border-color)',
                 background: picked ? 'var(--bg-tertiary)' : undefined }}>
      <td className="px-2 py-2 align-top">
        {pickable && (
          <input type="checkbox" checked={picked} onChange={onPick}
                 aria-label="选择本篇" style={{ accentColor: 'var(--accent-primary)' }} />
        )}
      </td>
      <td className={`px-3 py-2 align-top ${inGroup ? 'pl-6' : ''}`}>
        <button type="button" onClick={onOpen}
                className="text-left font-medium text-primary hover:underline block max-w-[520px] truncate">
          {doc.title || `#${doc.id}`}
        </button>
        <div className="flex items-center gap-2 mt-0.5 flex-wrap text-[11px] text-muted">
          {variant && (
            <span className="whitespace-nowrap" style={{ color: 'var(--text-secondary)' }}>{variant}</span>
          )}
          {doc.source_query_text && (
            <span className="truncate max-w-[380px]" title={doc.source_query_text}>
              {doc.source_query_text}
            </span>
          )}
          {doc.plan_seq != null && (
            <button type="button"
                    onClick={(e) => { e.stopPropagation(); onOpenPlan?.(); }}
                    className="px-1.5 rounded-full whitespace-nowrap"
                    title={`由发文计划第 ${doc.plan_seq} 行生成${
                      doc.plan_publish_date ? `,计划发布 ${doc.plan_publish_date.slice(0, 10)}` : ''}`}
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}>
              计划 #{doc.plan_seq}
            </button>
          )}
          {doc.generation_error && (
            <span style={{ color: 'var(--danger-fg)' }} title={doc.generation_error}>
              ⚠ {t('admin.contentReview.genError')}
              {doc.gen_next_retry_at
                ? ` · 待重试 ${doc.gen_attempts || 0}/${GEN_RETRY_MAX}`
                : (doc.gen_attempts ? ' · 已停止重试' : '')}
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-2 align-top whitespace-nowrap">
        <DocStatusChip status={doc.status} />
        <div className="mt-0.5"><DocSourceChip source={doc.source} /></div>
      </td>
      <td className="px-3 py-2 align-top whitespace-nowrap">
        <QualityChip score={doc.quality_score} quality={doc.quality} compact />
      </td>
      <td className="px-3 py-2 align-top">
        {chips.length === 0
          ? <span className="text-muted">—</span>
          : (
            <div className="flex flex-wrap items-center gap-1">
              {shown.map(c => (
                <span key={c.key} title={c.title}
                      className="text-[10px] px-1.5 py-0.5 rounded-full inline-flex items-center gap-1 max-w-[110px]"
                      style={TONE[c.tone]}>
                  <span className="truncate">{c.label}</span>
                  {c.url && (
                    <a href={c.url} target="_blank" rel="noreferrer"
                       onClick={e => e.stopPropagation()}
                       className="underline opacity-80 hover:opacity-100">🔗</a>
                  )}
                </span>
              ))}
              {chips.length > 2 && (
                <span className="text-[10px] text-muted" title={restTitle}>
                  +{chips.length - 2}
                </span>
              )}
            </div>
          )}
      </td>
      <td className="px-3 py-2 align-top whitespace-nowrap text-muted tabular-nums">
        {fmtTime(doc.created_at, false)}
        {/* Regeneration overwrites in place: created_at stays on the creation day, and only this shows the body was rewritten */}
        {isRewritten(doc) && (
          <span className="ml-1" style={{ color: 'var(--accent-primary)' }}
                title={`${t('admin.contentReview.rewrittenHint', {
                  defaultValue: '这篇正文被重新生成过,创建时间仍是原来那天' })}:${fmtTime(doc.updated_at as string)}`}>
            ↻
          </span>
        )}
      </td>
      <td className="px-3 py-2 align-top text-right">
        <button type="button" onClick={onOpen}
                className="text-xs px-2.5 py-1 rounded-md whitespace-nowrap"
                style={{ background: 'var(--bg-input)', color: 'var(--accent-primary)',
                         border: '1px solid var(--border-color)' }}>
          {t('admin.contentReview.view')}
        </button>
      </td>
    </tr>
  );
}

// Mediumsly push status display: success -> green link; failure -> red error. `compact` is the small size for list cards.
// Flywheel P1-C -- targeted re-test comparison card after placement: registers a probe on publish, runs a targeted audit 7 days later, shows before/after metric deltas.
// Exported: reused by the read-only detail page ContentReviewDetail (same source display as the dialog).
export function RetestCard({ docId }: { docId: number }) {
  const token = localStorage.getItem('token') || '';
  const [probes, setProbes] = useState<RetestProbe[] | null>(null);
  useEffect(() => {
    adminContentReviewApi.getRetest(docId, token).then(setProbes).catch(() => setProbes([]));
  }, [docId]);
  if (!probes || probes.length === 0) return null;
  const p = probes[0]; // the latest one
  const METRICS: { key: keyof import('../../services/adminContentReviewApi').RetestMetrics; label: string }[] = [
    { key: 'hit_pct', label: '露出率' },
    { key: 'top3_pct', label: 'TOP3率' },
    { key: 'cited_own_pct', label: '引用本稿率' },
  ];
  return (
    <div className="mt-2 p-2.5 rounded-md text-xs"
         style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="font-medium text-primary">投放后定向复测</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full"
              style={p.status === 'done'
                ? { background: 'var(--success-bg)', color: 'var(--success-fg)' }
                : { background: 'rgba(234,179,8,0.12)', color: '#ca8a04' }}>
          {p.status === 'scheduled' && `待复测 · ${(p.scheduled_for || '').slice(0, 10)}`}
          {p.status === 'enqueued' && '复测中(结果次日出)'}
          {p.status === 'done' && '已完成'}
          {p.status === 'cancelled' && '已取消'}
        </span>
        <span className="text-[10px] text-muted">
          {p.queries.length} 问题 × {p.engines.length} 引擎
        </span>
      </div>
      {p.status === 'done' && p.result.before && p.result.after && (
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-muted text-left">
              <th className="py-1 font-normal">指标</th>
              <th className="py-1 font-normal text-right">发布前</th>
              <th className="py-1 font-normal text-right">发布后</th>
              <th className="py-1 font-normal text-right">变化</th>
            </tr>
          </thead>
          <tbody>
            {METRICS.map(m => {
              const before = p.result.before?.[m.key];
              const after = p.result.after?.[m.key];
              const delta = p.result.delta?.[m.key as string];
              const up = typeof delta === 'number' && delta > 0;
              const down = typeof delta === 'number' && delta < 0;
              return (
                <tr key={m.key} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                  <td className="py-1 text-primary">{m.label}</td>
                  <td className="py-1 text-right text-secondary">{before ?? '—'}%</td>
                  <td className="py-1 text-right text-secondary">{after ?? '—'}%</td>
                  <td className="py-1 text-right font-medium"
                      style={{ color: up ? 'var(--success-fg)' : down ? 'var(--danger-fg)' : 'var(--text-muted)' }}>
                    {typeof delta === 'number' ? `${delta > 0 ? '+' : ''}${delta}pp` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function MediumslyStateChip({ doc, compact = false }: { doc: GeneratedDoc; compact?: boolean }) {
  const { t } = useTranslation();
  if (doc.mediumsly_url) {
    // The auto-publish chain writes the same Mediumsly URL into publish_targets (content_scheduler
    // ._mark_platform_target), and "publish destinations" already shows platform/link/date --
    // adding a green badge here would say the same thing twice. Only drafts whose destinations lack this URL
    // (old drafts with the wrong platform before the 2026-08-06 fix, manually pushed drafts) need it as a fallback.
    const inTargets = doc.publish_targets.some(
      p => (p.url || '').trim() === (doc.mediumsly_url || '').trim());
    if (inTargets) return null;
    const labelKey = compact ? 'admin.contentReview.mediumslyPushedShort'
                              : 'admin.contentReview.mediumslyPushed';
    return (
      <div className={compact ? 'mt-1' : 'mt-2'}>
        <a href={doc.mediumsly_url} target="_blank" rel="noreferrer"
           className={`${compact ? 'text-[10px]' : 'text-xs'} inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full underline`}
           style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
          ✓ {t(labelKey)}
          {!compact && doc.mediumsly_pushed_at && (
            <span className="text-muted">
              · {fmtTime(doc.mediumsly_pushed_at)}
            </span>
          )}
        </a>
      </div>
    );
  }
  if (doc.mediumsly_last_error) {
    return (
      <div className={compact ? 'mt-1' : 'mt-2'}>
        <span className={`${compact ? 'text-[10px]' : 'text-xs'} inline-block px-2 py-0.5 rounded-md`}
              style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                       border: '1px solid rgba(239,68,68,0.3)',
                       maxWidth: compact ? 360 : '100%' }}
              title={doc.mediumsly_last_error}>
          ⚠ {t('admin.contentReview.mediumslyFailed')}
          {!compact && `: ${doc.mediumsly_last_error}`}
        </span>
      </div>
    );
  }
  return null;
}

// Refined-writing review score badge -- green for pass, yellow for borderline, red for fail; hover shows per-dimension scores and uncovered questions.
// Without a score (pipeline disabled / review agent down / old draft) it isn't rendered and takes no space.
export function QualityChip({ score, quality, compact = false }: {
  score?: number | null; quality?: DocQuality | null;
  /** The column header already says "quality"; repeating the word in the badge says the same thing twice and wraps onto two lines */
  compact?: boolean;
}) {
  if (typeof score !== 'number') return null;
  const c = score >= QUALITY_PASS ? '#10b981' : score >= 60 ? '#eab308' : '#ef4444';
  const dims = quality?.dims || {};
  const tip = [
    ...QUALITY_DIM_ORDER
      .filter(k => typeof dims[k] === 'number')
      .map(k => `${QUALITY_DIM_LABELS[k]} ${dims[k]}`),
    quality?.uncovered?.length ? `未覆盖问题 ${quality.uncovered.length} 条` : '',
    quality?.rounds ? `已自动修订 ${quality.rounds} 轮` : '',
  ].filter(Boolean).join(' · ');
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap inline-block" title={tip}
          style={{ background: `${c}1f`, color: c, border: `1px solid ${c}55` }}>
      {compact ? score : `质量 ${score}`}
    </span>
  );
}

// Refined-writing review panel on the detail page: total score + five dimension bars + uncovered questions + hard flaws + revision list.
function QualityPanel({ score, quality }: { score: number; quality?: DocQuality | null }) {
  const pass = score >= QUALITY_PASS;
  const c = pass ? '#10b981' : score >= 60 ? '#eab308' : '#ef4444';
  const dims = quality?.dims || {};
  return (
    <div className="rounded-md p-2.5 text-xs space-y-2"
         style={{ background: `${c}0f`, border: `1px solid ${c}44` }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium" style={{ color: c }}>精写评审 {score} 分</span>
        <span className="text-muted">
          {pass ? '已达标' : `未达标(达标线 ${QUALITY_PASS}）`}
          {quality?.rounds ? ` · 自动修订 ${quality.rounds} 轮` : ' · 未修订'}
        </span>
      </div>
      {/* Five dimension bars -- table layout, no flex for PDF export (gap offsets under html2canvas) */}
      <table style={{ width: '100%', borderSpacing: '0 2px' }}>
        <tbody>
          {QUALITY_DIM_ORDER.filter(k => typeof dims[k] === 'number').map(k => {
            const v = dims[k] as number;
            return (
              <tr key={k}>
                <td className="text-muted whitespace-nowrap" style={{ width: 84 }}>
                  {QUALITY_DIM_LABELS[k]}
                </td>
                <td style={{ paddingRight: 8 }}>
                  <div style={{ height: 5, borderRadius: 3, background: 'var(--bg-input)' }}>
                    <div style={{
                      width: `${Math.max(0, Math.min(100, v))}%`, height: 5, borderRadius: 3,
                      background: v >= QUALITY_PASS ? 'var(--success-solid)' : v >= 60 ? 'var(--warn-solid)' : 'var(--danger-solid)',
                    }} />
                  </div>
                </td>
                <td className="tabular-nums text-secondary" style={{ width: 28 }}>{v}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!!quality?.uncovered?.length && (
        <div style={{ color: 'var(--danger-fg)' }}>
          这些提示词正文里没有明确答案:{quality.uncovered.join('、')}
        </div>
      )}
      {!!quality?.blocking?.length && (
        <div style={{ color: 'var(--danger-fg)' }}>
          硬伤:{quality.blocking.join(';')}
        </div>
      )}
      {!!quality?.fixes?.length && (
        <details>
          <summary className="text-muted cursor-pointer">
            评审开出的修订清单({quality.fixes.length} 条)
          </summary>
          <ol className="mt-1 ml-4 space-y-0.5 text-secondary" style={{ listStyle: 'decimal' }}>
            {quality.fixes.map((f, i) => <li key={i}>{f}</li>)}
          </ol>
        </details>
      )}
      {!!quality?.notes?.length && (
        <div className="text-muted">{quality.notes.join(' · ')}</div>
      )}
    </div>
  );
}

export function DocSourceChip({ source }: { source: DocSource }) {
  const { t } = useTranslation();
  const cm = source === 'ai'
    ? { c: '#8b5cf6', bg: 'rgba(139,92,246,0.15)' }
    : { c: '#06b6d4', bg: 'rgba(6,182,212,0.15)' };
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full"
          style={{ background: cm.bg, color: cm.c }}>
      {t(`admin.contentReview.sourceFilter.${source}`)}
    </span>
  );
}

export function DocStatusChip({ status }: { status: DocStatus }) {
  const { t } = useTranslation();
  const cm: Record<DocStatus, { c: string; bg: string }> = {
    generating:     { c: '#eab308', bg: 'rgba(234,179,8,0.15)' },
    draft:          { c: '#94a3b8', bg: 'rgba(148,163,184,0.15)' },
    pending_review: { c: '#eab308', bg: 'rgba(234,179,8,0.15)' },
    approved:       { c: '#10b981', bg: 'var(--success-bg)' },
    rejected:       { c: '#ef4444', bg: 'var(--danger-bg)' },
    published:      { c: '#3b82f6', bg: 'rgba(59,130,246,0.15)' },
  };
  const s = cm[status];
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full"
          style={{ background: s.bg, color: s.c }}>
      {t(`admin.contentReview.statusFilter.${status}`)}
    </span>
  );
}

// Explicit text label bar -- the "Measures for Labeling AI-Generated Synthetic Content" require generated content to carry a visible notice
// in the display interface. **This is the only copy**: since 2026-08-17 the backend no longer appends a label line to the body
// (drafts are auto-placed on external media, and a label in the body would be published by the platform verbatim), so the explicit label only appears
// in the product UI. The content number is issued by the backend gen_trace_no (not in the body);
// click "Provenance" to look it up in /ops/content-trace.
function AiDisclosureBar({ doc }: { doc: GeneratedDoc }) {
  if ((doc.source || 'ai') !== 'ai') return null;
  return (
    <div className="rounded-md px-3 py-2 text-xs flex items-center gap-2 flex-wrap"
         style={{ background: 'rgba(59,130,246,0.08)', color: 'var(--info-fg)',
                  border: '1px solid rgba(59,130,246,0.3)' }}>
      <span>⚠ 内容由AI生成,仅供参考</span>
      {doc.gen_trace_no && (
        <>
          <span style={{ color: 'var(--text-muted)' }}>·</span>
          <span style={{ color: 'var(--text-secondary)' }}>
            内容编号 <code>{doc.gen_trace_no}</code>
          </span>
          <a href={`/ops/content-trace?q=${encodeURIComponent(doc.gen_trace_no)}`}
             className="underline opacity-80 hover:opacity-100">溯源</a>
        </>
      )}
    </div>
  );
}

// Article detail view -- drawer (embedded stepper / list dialog) and page (standalone detail page) shells share the same
// state and actions (edit/regenerate/approve/reject/publish center/platform review backfill). asPage=true renders the full-page layout.
export function DocDetailView({ doc: initialDoc, token, onClose, onAnyChange, autoOpenPublish, asPage, onOpenPublish }:
  { doc: GeneratedDoc; token: string; onClose: () => void; onAnyChange: () => void;
    autoOpenPublish?: boolean; asPage?: boolean;
    /** When passed, "Choose publish platform" goes to the standalone publish page (full-page layout); otherwise opens the publish center drawer in place */
    onOpenPublish?: () => void }) {
  const { t } = useTranslation();
  // The modal maintains its own current doc; it doesn't depend on the parent's filtered docs[] (otherwise once the status changes and the doc
  // is filtered out of the list, the modal would vanish because selectedDoc becomes null)
  const [doc, setDoc] = useState<GeneratedDoc>(initialDoc);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showReject, setShowReject] = useState(false);
  // 2026-06-12 -- platform review result backfill (published drafts)
  const [showPlatformReject, setShowPlatformReject] = useState(false);
  const [platformRejectReason, setPlatformRejectReason] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  // Publish center (secondary drawer): choose media/platform + one-click official blog publish + task backfill, replacing the old publish picker
  const [showPublishCenter, setShowPublishCenter] = useState(false);
  const [taskCount, setTaskCount] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [editTitle, setEditTitle] = useState(doc.title);
  const [editBody, setEditBody] = useState(doc.body_markdown);
  const [editSummary, setEditSummary] = useState(doc.summary);
  const editorBodyMinHeight = asPage ? 420 : 320;

  const refreshTaskCount = useCallback(() => {
    contentApi.listPublishTasks(doc.topic_id, { doc_id: doc.id }, token)
      .then(ts => setTaskCount(ts.length))
      .catch(() => { /* count failure doesn't affect the details */ });
  }, [doc.id, doc.topic_id, token]);

  useEffect(() => { refreshTaskCount(); }, [refreshTaskCount]);

  // After inline approval, automatically open the publish center, once
  const autoOpenedRef = useRef(false);
  useEffect(() => {
    if (autoOpenPublish && doc.status === 'approved' && !autoOpenedRef.current) {
      autoOpenedRef.current = true;
      setShowPublishCenter(true);
    }
  }, [autoOpenPublish, doc.status]);

  // Call once after a mutation to get the latest doc -- replaces local state so the modal UI reflects the new state immediately
  const refetch = useCallback(async () => {
    try {
      const fresh = await adminContentReviewApi.getDoc(doc.id, token);
      setDoc(fresh);
    } catch { /* keep the old doc; onAnyChange makes the parent list refresh itself; the modal's degraded display is harmless */ }
  }, [doc.id, token]);

  // Returns a boolean indicating success -- callers decide the next step as needed (expand the publish picker, close the dialog, etc.)
  const wrap = async (fn: () => Promise<unknown>): Promise<boolean> => {
    if (busy) return false;
    setBusy(true); setErr(null);
    try {
      await fn();
      await refetch();
      onAnyChange();
      return true;
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleApprove = async () => {
    const ok = await wrap(() => adminContentReviewApi.approveDoc(doc.id, token));
    if (ok) setShowPublishCenter(true);   // open the publish center right after approval, no extra click
  };

  const handleReject = async () => {
    const ok = await wrap(() => adminContentReviewApi.rejectDoc(doc.id, rejectReason.trim(), token));
    if (ok) onClose();
  };

  // One-click Mediumsly for the publish center's "official blog" card (backend admin publish_doc unchanged, only the entry moved)
  const handlePublishMediumsly = async (): Promise<boolean> =>
    wrap(() => adminContentReviewApi.publishDoc(doc.id, [], token, true));

  const handleSaveEdit = async () => {
    if (busy) return;
    const wasRejected = doc.status === 'rejected';
    setBusy(true); setErr(null);
    try {
      // For returned drafts the backend rescans along the way: if it passes it goes back to draft automatically, otherwise the rejection reason is refreshed
      const fresh = await adminContentReviewApi.updateDoc(doc.id, {
        title: editTitle, body_markdown: editBody, summary: editSummary,
      }, token);
      setDoc(fresh);
      setEditing(false);
      onAnyChange();
      if (wasRejected) {
        if (fresh.status === 'rejected') {
          toast.error(`合规复扫仍未通过:${fresh.reject_reason || '存在禁用表述'}`);
        } else {
          toast.success('合规复扫通过,稿件已恢复为草稿,可继续送审 / 通过');
        }
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // Unlock entry for returned drafts: rerun the compliance scan after edits / word list adjustments
  const handleRescan = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const fresh = await adminContentReviewApi.rescanDoc(doc.id, token);
      setDoc(fresh);
      onAnyChange();
      if (fresh.status === 'rejected') {
        toast.error(`复扫仍未通过:${fresh.reject_reason || '存在禁用表述'}`);
      } else {
        toast.success('复扫通过,稿件已恢复为草稿');
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRegenerate = async () => {
    if (busy || regenerating) return;
    if (!(await uiConfirm('重新生成会用 AI 覆盖当前标题和正文(包括手动修改过的内容),确定继续?', { danger: true }))) return;
    setRegenerating(true); setErr(null);
    try {
      const fresh = await adminContentReviewApi.regenerateDoc(doc.id, token);
      setDoc(fresh);
      setEditing(false);
      onAnyChange();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRegenerating(false);
    }
  };

  const handleCancelRetry = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      setDoc(await adminContentReviewApi.cancelDocRetry(doc.id, token));
      onAnyChange();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const publishEntryLabel = t('admin.contentReview.choosePublish');
  // taskCount is no longer written into the button text (a status-agnostic total doesn't tell you what to do); it only decides whether the entry shows:
  // even when a draft is returned, it must be possible to go back to existing tasks and backfill links.
  const showPublishEntry = doc.status === 'approved' || doc.status === 'published' || (taskCount ?? 0) > 0;

  // Header body (title + status/source badges + meta info) -- shared by the drawer / page shells
  const headerMain = (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2 flex-wrap">
        <h2 className="text-lg font-semibold text-primary">{doc.title || `#${doc.id}`}</h2>
        <DocStatusChip status={doc.status} />
        <DocSourceChip source={doc.source} />
      </div>
      <div className="text-xs text-muted mt-1">
        {doc.source_query_text}
        {' · '}{fmtTime(doc.created_at)}
        {/* Which publish plan row it was scheduled from -- same source as the badge on list cards */}
        {doc.plan_seq != null && (
          <span className="ml-2" style={{ color: 'var(--accent-primary)' }}
                title={doc.plan_publish_date
                  ? `计划发布 ${doc.plan_publish_date.slice(0, 10)}`
                  : undefined}>
            📋 计划 #{doc.plan_seq}
            {doc.plan_publish_date && ` · ${doc.plan_publish_date.slice(5, 10)}`}
          </span>
        )}
      </div>
    </div>
  );

  // Edit / regenerate entries (only shown when not editing) -- shared by the drawer / page shells
  const editRegenButtons = !editing && (
    <>
      {/* 2026-07-31 -- copy out for placement: MD for editors / rich text for the official account backend etc. */}
      <button type="button"
              onClick={() => copyMarkdown(doc.title, doc.body_markdown)}
              className="text-xs px-2.5 py-1 rounded-md"
              title="复制 Markdown 源码"
              style={{ background: 'transparent', color: 'var(--text-secondary)',
                       border: '1px solid var(--border-color)' }}>
        ⧉ 复制 MD
      </button>
      <button type="button"
              onClick={() => copyArticle(doc.title, doc.body_markdown)}
              className="text-xs px-2.5 py-1 rounded-md"
              title="复制带排版的正文,可直接贴进公众号 / Word / 飞书文档"
              style={{ background: 'transparent', color: 'var(--text-secondary)',
                       border: '1px solid var(--border-color)' }}>
        ⧉ 复制文章
      </button>
      <button type="button"
              onClick={() => {
                setEditTitle(doc.title);
                setEditBody(doc.body_markdown);
                setEditSummary(doc.summary);
                setEditing(true);
              }}
              disabled={regenerating}
              className="text-xs px-2.5 py-1 rounded-md"
              style={{ background: 'transparent', color: 'var(--text-secondary)',
                       border: '1px solid var(--border-color)' }}>
        ✎ 编辑
      </button>
      {/* Way out for returned drafts: after edits click rescan; if it passes it goes back to draft, then it can be submitted / approved / published */}
      {doc.status === 'rejected' && (
        <button type="button"
                onClick={handleRescan}
                disabled={busy || regenerating}
                className="text-xs px-2.5 py-1 rounded-md"
                title="重跑合规扫描:通过则恢复为草稿,仍命中则刷新退回原因"
                style={{ background: 'transparent', color: 'var(--accent-primary)',
                         border: '1px solid var(--accent-primary)',
                         opacity: (busy || regenerating) ? 0.6 : 1 }}>
          ⟳ 合规复扫
        </button>
      )}
      {doc.status !== 'approved' && doc.status !== 'published' && (
        <button type="button"
                onClick={handleRegenerate}
                disabled={busy || regenerating}
                className="text-xs px-2.5 py-1 rounded-md"
                style={{ background: 'transparent', color: 'var(--text-secondary)',
                         border: '1px solid var(--border-color)',
                         opacity: (busy || regenerating) ? 0.6 : 1 }}>
          {regenerating ? '↻ 重新生成中…' : '↻ 重新生成'}
        </button>
      )}
    </>
  );

  // "Choose publish platform" entry: moved to the header in the full-page layout (left of the edit button); stays in the footer in the drawer layout.
  const publishEntryButton = showPublishEntry && (
    <button type="button" onClick={() => (onOpenPublish ? onOpenPublish() : setShowPublishCenter(true))} disabled={busy}
            className="text-xs px-4 py-1.5 rounded-md text-white"
            style={{ background: 'var(--info-solid)', opacity: busy ? 0.5 : 1 }}>
      {publishEntryLabel}
    </button>
  );

  // Body block (error/rejection hint + edit form or read-only body + publish records + Mediumsly status + re-test card) -- shared by both shells
  const body = (
    <div className="space-y-4">
          {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}
          {/* Which prompts and which template this draft used -- so the review can match it against the plan */}
          <DocRelationBlock doc={doc} />
          {doc.generation_error && (
            <div className="rounded-md p-2 text-xs space-y-1.5"
                 style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
              <div>{doc.generation_error}</div>
              {/* Requeue status after generation failure: when queued show the next time; not in the queue means a terminal error or given up */}
              <div className="flex items-center gap-2 flex-wrap"
                   style={{ color: 'var(--text-secondary)' }}>
                <span>
                  {doc.gen_next_retry_at
                    ? `自动重试 ${doc.gen_attempts || 0}/${GEN_RETRY_MAX} · 下次 ${fmtRetryAt(doc.gen_next_retry_at)}`
                    : (doc.gen_attempts
                        ? `已重试 ${doc.gen_attempts} 次,不再自动重试(错因重试也治不好,或已放弃)`
                        : '这个错因重试也治不好,没有排自动重试')}
                </span>
                <button type="button" onClick={handleRegenerate} disabled={busy || regenerating}
                        className="px-1.5 py-0.5 rounded-md"
                        style={{ border: '1px solid var(--border-color)',
                                 opacity: (busy || regenerating) ? 0.5 : 1 }}>
                  {regenerating ? '生成中…' : '立即重试'}
                </button>
                {doc.gen_next_retry_at && (
                  <button type="button" onClick={handleCancelRetry} disabled={busy}
                          className="px-1.5 py-0.5 rounded-md"
                          style={{ border: '1px solid var(--border-color)',
                                   opacity: busy ? 0.5 : 1 }}>
                    放弃自动重试
                  </button>
                )}
              </div>
            </div>
          )}
          {/* Refined-writing review panel -- the review agent's conclusions shown directly to ops: what's lacking, which question was missed,
              how many rounds of auto-revision. Drafts below the bar deserve a human look before deciding to approve or regenerate. */}
          {typeof doc.quality_score === 'number' && (
            <QualityPanel score={doc.quality_score} quality={doc.quality} />
          )}
          {doc.reject_reason && (
            <div className="rounded-md p-2 text-xs"
                 style={{ background: 'rgba(239,68,68,0.05)', color: 'var(--danger-fg)',
                          border: '1px solid rgba(239,68,68,0.3)' }}>
              {t('admin.contentReview.rejectReason')}:{doc.reject_reason}
              {doc.status === 'rejected' && (
                <div className="mt-1 text-muted">
                  改完正文保存即自动合规复扫;复扫通过会恢复成草稿,可继续送审 / 通过 / 发布。
                  也可以直接点上方「合规复扫」重跑一次。
                </div>
              )}
            </div>
          )}
          {/* Explicit text label -- mandatory under the "Measures for Labeling AI-Generated Synthetic Content".
              Pinned to the top of the body container, appearing in both edit and read-only states; customer-supplied drafts (source=user)
              aren't AI-generated, and labeling them would be a false label, so it's rendered only for AI drafts. */}
          <AiDisclosureBar doc={doc} />
          {editing ? (
            <div className="space-y-4 rounded-xl p-4"
                 style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
              <div>
                <label className="block text-xs text-muted mb-1.5">标题</label>
                <input className="w-full rounded-md px-3 py-2 text-sm"
                       style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                border: '1px solid var(--border-color)' }}
                       value={editTitle} onChange={e => setEditTitle(e.target.value)} />
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <label className="block text-xs text-muted">正文</label>
                  <span className="text-[11px] text-muted">支持导入文档、插入素材、插入视频链接</span>
                </div>
                <RichTextBodyEditor
                  topicId={doc.topic_id}
                  token={token}
                  value={editBody}
                  onChange={setEditBody}
                  minHeight={editorBodyMinHeight}
                  showMediaAction
                />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1.5">摘要</label>
                <textarea rows={3} className="w-full rounded-md px-3 py-2 text-sm"
                          style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                   border: '1px solid var(--border-color)' }}
                          value={editSummary} onChange={e => setEditSummary(e.target.value)} />
              </div>
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => setEditing(false)} disabled={busy}
                        className="text-xs px-3 py-1.5 rounded-md"
                        style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                                 border: '1px solid var(--border-color)' }}>
                  取消
                </button>
                <button type="button" onClick={handleSaveEdit} disabled={busy}
                        className="text-xs px-3 py-1.5 rounded-md text-white"
                        style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
                  {busy ? '保存中…' : '保存'}
                </button>
              </div>
            </div>
          ) : (
            <section className="space-y-2">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <label className="text-xs text-muted">正文</label>
                <span className="text-[11px] text-muted">详情按 Markdown 富文本样式展示</span>
              </div>
              <article
                className="text-sm text-primary leading-relaxed rounded-xl p-4"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}
              >
                <MarkdownPreview
                  value={doc.body_markdown}
                  emptyText={t('admin.contentReview.emptyBody')}
                  authToken={token}
                />
              </article>
            </section>
          )}
          {doc.publish_targets.length > 0 && (
            <div>
              <p className="text-xs text-muted mb-1">{t('admin.contentReview.publishedTo')}:</p>
              <div className="flex flex-wrap gap-1.5">
                {doc.publish_targets.map((p, i) => (
                  <span key={i}
                        className="text-[11px] px-2 py-0.5 rounded-full inline-flex items-center gap-1.5"
                        style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--info-fg)' }}>
                    {p.platform}{p.media ? ` / ${p.media}` : ''}
                    {p.url && (
                      <a href={p.url} target="_blank" rel="noreferrer"
                         className="underline opacity-80 hover:opacity-100">🔗</a>
                    )}
                    <span className="text-muted ml-1">
                      {p.marked_at && fmtTime(p.marked_at, false).slice(0, 10)}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}
          <MediumslyStateChip doc={doc} />
          {/* Strategy basis -- paired with the plan document's "placement strategy", answering how this article was written once scheduled */}
          <DocWhy docId={doc.id} />
          {doc.status === 'published' && <RetestCard docId={doc.id} />}
    </div>
  );

  // Publish center (secondary drawer) -- can be stacked on either shell
  const publishCenter = (
    <PublishCenterDrawer
      open={showPublishCenter}
      onClose={() => setShowPublishCenter(false)}
      doc={doc} token={token}
      onChanged={() => { refreshTaskCount(); refetch(); onAnyChange(); }}
      onPublishMediumsly={handlePublishMediumsly}
    />
  );

  // Full-page shell (standalone detail page): the whole block wrapped in a card background; the action bar (approve/reject/publish/platform review) sits above the body, visible on entry.
  if (asPage) {
    // The publish entry moved to the header; this bar only keeps review decisions (draft/pending: approve · reject; published: platform review backfill).
    const hasFooterActions = doc.status === 'draft' || doc.status === 'pending_review'
      || doc.status === 'published';
    return (
      <>
        <div className="rounded-xl overflow-hidden"
             style={{ border: '1px solid var(--border-color)', background: 'var(--bg-card)' }}>
          {/* Header: title / status · source badges / meta info + choose publish platform · edit · regenerate */}
          <div className="flex items-start justify-between gap-3 flex-wrap p-4"
               style={{ borderBottom: '1px solid var(--border-color)' }}>
            {headerMain}
            {(publishEntryButton || editRegenButtons) && (
              <div className="flex items-center gap-2 flex-wrap">
                {publishEntryButton}
                {editRegenButtons}
              </div>
            )}
          </div>
          {/* Action bar on top: actionable immediately on entering the detail page, no scrolling to the bottom */}
          {hasFooterActions && (
            <div style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}>
              {footerStack()}
            </div>
          )}
          {/* Body */}
          <div className="p-4 md:p-5 space-y-4">{body}</div>
        </div>
        {publishCenter}
      </>
    );
  }

  // Drawer shell (embedded stepper / list dialog) -- behavior kept the same as before the refactor
  return (
    <>
    <Drawer
      open onClose={onClose}
      header={<>
          {headerMain}
          {editRegenButtons}
          <button type="button" onClick={onClose} className="text-muted hover:text-primary">✕</button>
      </>}
      footer={<>
        {footerStack()}
      </>}
    >
          {body}
    </Drawer>
    {publishCenter}
    </>
  );

  // Footer stack: expanded panels render above the button row -- action buttons stay visible when expanded
  function footerStack() {
    return (
      <div style={asPage ? undefined : { maxHeight: '45vh', overflowY: 'auto' }}>
        {showPlatformRejectPanel()}
        {showRejectPanel()}
        <div className={`p-3 flex items-center gap-2 flex-wrap ${asPage ? 'justify-end' : 'justify-between'}`}>
          <div>
            {/* In the full-page layout the publish entry is in the header; the drawer layout keeps it here */}
            {!asPage && publishEntryButton}
          </div>
          <div className="flex justify-end gap-2 flex-wrap">
          {(doc.status === 'draft' || doc.status === 'pending_review') && (
            <>
              <button type="button" onClick={() => setShowReject(true)} disabled={busy}
                      className="text-xs px-4 py-1.5 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--danger-fg)',
                               border: '1px solid var(--border-color)' }}>
                {t('admin.contentReview.reject')}
              </button>
              <button type="button" disabled={busy}
                      onClick={handleApprove}
                      className="text-xs px-4 py-1.5 rounded-md text-white"
                      style={{ background: 'var(--accent-primary)' }}>
                {t('admin.contentReview.approve')}
              </button>
            </>
          )}
          {/* 2026-06-12 -- platform review result backfill: rejection reasons feed into platform rule learning */}
          {doc.status === 'published' && (
            <>
              {doc.platform_review_status === 'passed' && (
                <span className="text-xs px-2 py-1 rounded-md self-center"
                      style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
                  平台已过审
                </span>
              )}
              {doc.platform_review_status === 'rejected' && (
                <span className="text-xs px-2 py-1 rounded-md self-center"
                      title={doc.platform_reject_reason || ''}
                      style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
                  平台被拒:{(doc.platform_reject_reason || '').slice(0, 30)}
                </span>
              )}
              <button type="button" disabled={busy}
                      onClick={() => setShowPlatformReject(v => !v)}
                      className="text-xs px-4 py-1.5 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--danger-fg)',
                               border: '1px solid var(--border-color)' }}>
                平台被拒…
              </button>
              <button type="button" disabled={busy}
                      onClick={() => wrap(() =>
                        adminContentReviewApi.setPlatformReview(doc.id, 'passed', token))}
                      className="text-xs px-4 py-1.5 rounded-md text-white"
                      style={{ background: 'var(--success-solid)' }}>
                ✓ 平台已过审
              </button>
            </>
          )}
          </div>
        </div>
      </div>
    );
  }

  function showPlatformRejectPanel() {
    if (!showPlatformReject) return null;
    return (
          <div className="p-3 border-b"
               style={{ borderColor: 'var(--border-color)', background: 'var(--bg-tertiary)' }}>
            <p className="text-xs text-secondary mb-2">
              平台拒稿原因(必填 — 会被「平台规则·从拒稿学习」提炼成该平台审核规则)
            </p>
            <textarea value={platformRejectReason}
                      onChange={e => setPlatformRejectReason(e.target.value)} rows={2}
                      placeholder="例:含绝对化用语「全网第一」被拒 / 文末二维码导流被拒…"
                      className="w-full text-sm px-3 py-2 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                               border: '1px solid var(--border-color)' }} />
            <div className="flex justify-end gap-2 mt-2">
              <button type="button"
                      onClick={() => { setShowPlatformReject(false); setPlatformRejectReason(''); }}
                      className="text-xs px-3 py-1 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                {t('admin.contentReview.cancel')}
              </button>
              <button type="button" disabled={busy || !platformRejectReason.trim()}
                      onClick={async () => {
                        const ok = await wrap(() => adminContentReviewApi.setPlatformReview(
                          doc.id, 'rejected', token, platformRejectReason.trim()));
                        if (ok) { setShowPlatformReject(false); setPlatformRejectReason(''); }
                      }}
                      className="text-xs px-3 py-1 rounded-md text-white disabled:opacity-40"
                      style={{ background: 'var(--danger-solid)' }}>
                {busy ? '…' : '确认回填'}
              </button>
            </div>
          </div>
    );
  }

  function showRejectPanel() {
    if (!showReject) return null;
    return (
          <div className="p-3 border-b"
               style={{ borderColor: 'var(--border-color)', background: 'var(--bg-tertiary)' }}>
            <p className="text-xs text-secondary mb-2">{t('admin.contentReview.rejectReasonLabel')}</p>
            <textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)} rows={2}
                      className="w-full text-sm px-3 py-2 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                               border: '1px solid var(--border-color)' }} />
            <div className="flex justify-end gap-2 mt-2">
              <button type="button" onClick={() => { setShowReject(false); setRejectReason(''); }}
                      className="text-xs px-3 py-1 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                {t('admin.contentReview.cancel')}
              </button>
              <button type="button" disabled={busy}
                      onClick={handleReject}
                      className="text-xs px-3 py-1 rounded-md text-white"
                      style={{ background: 'var(--danger-solid)' }}>
                {t('admin.contentReview.confirmReject')}
              </button>
            </div>
          </div>
    );
  }
}
