// Phase D.1 — Content (merges the former Compose + Posts).
// One screen showing all AI-generated / user-submitted drafts under the topic + review status + published outlets.
//
// Route: /dashboard/content
//
// Composition:
//   1. Top bar: topic switch + AI scheduling entry + "+ Submit article" entry
//   2. 5 stat cards: published / pending review / needs revision (rejected) / draft / AI auto status
//   3. Status + source filter bar
//   4. Article list (click one -> detail drawer)
//   5. SubmitDrawer / AutoScheduleDrawer / DocDetailDrawer

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useAgencyContext } from '../../contexts/AgencyContext';
import { Pagination } from '../../components/Pagination';
import { PageHead } from '../../components/PageHead';
import { ArticleBodyEditor } from '../../components/ArticleBodyEditor';
import { Drawer } from '../../components/overlays/Drawer';
import { PublishCenterDrawer } from '../../components/publish/PublishCenterDrawer';
import { aiTelemetryApi, type Topic } from '../../services/aiTelemetryApi';
import {
  contentApi,
  type ContentDoc, type ContentStats,
  type DocSource, type DocStatus,
  type AutoGenerateConfig, type PublishTask,
} from '../../services/contentApi';
import {
  isPaidMediaTask, taskDisplayName, taskStatusLabel,
} from '../../components/publish/publishShared';
import { MarkdownPreview } from '../../components/ui/MarkdownEditor';
import { DocRelationBlock } from '../../components/DocRelationBlock';
import { uiPrompt } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { UiSelect, UiMultiSelect } from '../../components/ui/Select';
import {
  FilterBar, FilterBarPrimary, FilterBarSecondary, FilterField, FilterSummary, DateRangeFilter,
  SearchInput,
} from '../../components/ui/FilterBar';
import {
  DATE_RANGE_ALL, inDateRange, isDateFilterActive, type DateRangeValue,
} from '../../components/ui/dateFilter';
import { FILTER_CONTROL_CLASS, FILTER_CONTROL_STYLE } from '../../components/ui/filterControls';
import { fmtTime } from '../../utils/datetime';
import { copyArticle } from '../../utils/copyDoc';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';

// Status / source / template are all multi-select: empty array = no filter. "All" no longer takes a mutually exclusive button slot;
// it's expressed by "nothing selected", matching the "Content publishing & review" side.
type ContentStatusFilter = DocStatus | 'gen_failed';

// generation_error is a separate user-visible status. To move failed drafts out of generating, the database
// still stores them as draft / pending_review; the filter layer must reclassify by generation_error first,
// otherwise clicking "Under review" would include failed placeholder drafts.
const STATUS_OPTIONS: ContentStatusFilter[] = [
  'draft', 'pending_review', 'rejected', 'approved', 'published', 'gen_failed',
];

// Auto-retry cap for failed generation -- aligned with the backend GEO_GEN_RETRY_MAX default
// (services/gen_retry.py MAX_ATTEMPTS); used only for display text.
const GEN_RETRY_MAX = 3;


/**
 * This draft is "generation failed", not "awaiting review".
 *
 * Rows that failed generation are also `pending_review` in the DB -- content_generator intentionally moves them
 * out of `generating` on failure, otherwise they'd be stuck in "Generating" forever. Looking only at status would show "Under review",
 * while the body was never generated. The criterion is `generation_error`, consistent with the "Content publishing & review" side.
 *
 * Reclassify only in states not yet handled by a human: drafts already reviewed / published follow the human verdict,
 * so a stale error doesn't drag them back into the failed state (same definition as backend ContentStats).
 */
function isGenFailed(d: ContentDoc): boolean {
  return !!(d.generation_error || '').trim()
    && (d.status === 'draft' || d.status === 'pending_review');
}

function failedDocTitle(d: ContentDoc): string {
  if (d.title) return d.title;
  if (isGenFailed(d) && d.source_query_text) return `[生成失败] ${d.source_query_text}`;
  return `#${d.id}`;
}

// generation_error is an internal failure string that may contain provider, HTTP response and token params.
// Clients only need an actionable reason category; the raw string stays in internal logs and admin troubleshooting.
function friendlyGenerationError(error: string): string {
  const safeMessages = new Set([
    '主题余额不足，充值后可重新生成。',
    '文章内容超过本次生成长度限制，请重新生成。',
    '生成服务暂时繁忙，请稍后重新生成。',
    '生成服务暂不可用，请联系平台支持。',
    '文章生成未完成，请稍后重新生成或联系平台支持。',
  ]);
  const original = (error || '').trim();
  if (safeMessages.has(original)) return original;

  const s = (error || '').toLowerCase();
  if (s.includes('余额不足') || s.includes('insufficient') || s.includes('balance')) {
    return '主题余额不足，充值后可重新生成。';
  }
  if (s.includes('finish_reason=length') || s.includes('输出被截断')) {
    return '文章内容超过本次生成长度限制，请重新生成。';
  }
  if (/\b(429|500|502|503|504|522|524)\b/.test(s)
      || s.includes('too busy') || s.includes('service unavailable')) {
    return '生成服务暂时繁忙，请稍后重新生成。';
  }
  if (s.includes('api_key') || s.includes('api key') || s.includes('unauthor')) {
    return '生成服务暂不可用，请联系平台支持。';
  }
  return '文章生成未完成，请稍后重新生成或联系平台支持。';
}

// The body was regenerated -- regeneration overwrites in place, created_at stays, only updated_at moves forward.
// Allow 60s tolerance: going from draft creation to a written body takes tens of seconds anyway, which doesn't count as a "rewrite".
function isRewritten(d: ContentDoc): boolean {
  if (!d.updated_at) return false;
  const c = new Date(d.created_at).getTime();
  const u = new Date(d.updated_at).getTime();
  return Number.isFinite(c) && Number.isFinite(u) && u - c > 60_000;
}
export function Content({ lockedTopicId }: { lockedTopicId?: number } = {}) {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const { isAgency } = useAuth();
  const { enterpriseUserId } = useAgencyContext();

  // Deep link: ?topic=<id>&doc=<id> -- the plan calendar's "View" lands directly in this draft's detail drawer.
  // Consumed only once on first page entry (stored in state); afterwards switching topics / closing the drawer isn't pulled back by the URL.
  const [searchParams] = useSearchParams();
  const [deepLink] = useState(() => ({
    topicId: Number(searchParams.get('topic')) || null,
    docId: Number(searchParams.get('doc')) || null,
  }));

  const [topics, setTopics] = useState<Topic[]>([]);
  // lockedTopicId: when embedded in step 6 of the "Topic info" stepper, lock to that topic and hide the topic dropdown.
  const [topicId, setTopicId] = useState<number | null>(
    lockedTopicId ?? deepLink.topicId ?? null);
  const [docs, setDocs] = useState<ContentDoc[]>([]);
  // doc_id -> placement tasks (list row badges)
  const [docTasks, setDocTasks] = useState<Map<number, PublishTask[]>>(new Map());
  const [stats, setStats] = useState<ContentStats | null>(null);
  const [statuses, setStatuses] = useState<ContentStatusFilter[]>([]);
  const [sources, setSources] = useState<DocSource[]>([]);
  const [searchText, setSearchText] = useState('');
  // Time filter by generation time (created_at), same definition as "Content & placement"
  const [dateRange, setDateRange] = useState<DateRangeValue>(DATE_RANGE_ALL);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openDocId, setOpenDocId] = useState<number | null>(deepLink.docId);
  const [showSubmit, setShowSubmit] = useState(false);
  const [showSchedule, setShowSchedule] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  // Template multi-select: values are content_templates.id strings, 'none' = no template (user-supplied drafts/legacy)
  const [templates, setTemplates] = useState<string[]>([]);
  // Sort by generation time: newest = newest first (default), oldest = oldest first
  // Generation time = created_at (moment of draft creation, unchanged by rewrites); update time = updated_at (last
  // write). Regeneration overwrites in place, so only sorting by update time brings just-rewritten drafts to the top.
  // Default is update time descending: regeneration overwrites in place, and sorting by generation time would sink just-rewritten drafts back to their creation day.
  const [sortOrder, setSortOrder] =
    useState<'newest' | 'oldest' | 'updated' | 'updated_oldest'>('updated');

  useEffect(() => {
    aiTelemetryApi.listTopics(token, isAgency ? enterpriseUserId : null)
      .then(list => {
        setTopics(list);
        setTopicId(prev => {
          const preferred = lockedTopicId ?? deepLink.topicId;
          if (preferred != null && list.some(topic => topic.id === preferred)) return preferred;
          return list.some(topic => topic.id === prev) ? prev : list[0]?.id ?? null;
        });
      })
      .catch(e => setErr(e instanceof Error ? e.message : String(e)));
  }, [token, isAgency, enterpriseUserId, lockedTopicId, deepLink.topicId]);

  const currentTopic = useMemo(
    () => topics.find(x => x.id === topicId) || null,
    [topics, topicId],
  );

  const refresh = useCallback(async () => {
    if (topicId == null) return;
    setLoading(true); setErr(null);
    try {
      // Fetch everything at once; status / source / template / search are all filtered on the frontend: multi-select can't be expressed with the backend's
      // single-value params, and changing filters no longer hits the API.
      const [docList, s] = await Promise.all([
        contentApi.listDocs(topicId, {}, token),
        contentApi.stats(topicId, token),
      ]);
      setDocs(docList);
      setStats(s);
      // Fetch all placement tasks at once -> list row task badges (paid vendor/manual platform status at a glance)
      contentApi.listPublishTasks(topicId, {}, token)
        .then(ts => {
          const m = new Map<number, PublishTask[]>();
          for (const tk of ts) {
            const arr = m.get(tk.doc_id) || [];
            arr.push(tk); m.set(tk.doc_id, arr);
          }
          setDocTasks(m);
        })
        .catch(() => { /* Badge failure doesn't affect the list */ });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [topicId, token]);

  useEffect(() => { refresh(); }, [refresh]);

  const reloadTopics = async () => {
    try {
      const list = await aiTelemetryApi.listTopics(token, isAgency ? enterpriseUserId : null);
      setTopics(list);
    } catch {/* ignore */}
  };

  const openDoc = docs.find(d => d.id === openDocId) || null;
  const topicApproved = (currentTopic?.submission_status || 'draft') === 'approved';

  // Template filter: options are the distinct values from loaded docs (template_id -> name); drafts without a template get their own option
  const templateOptions = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of docs) {
      if (d.template_id) m.set(String(d.template_id), d.template_name || `#${d.template_id}`);
    }
    const list = [...m.entries()].sort((a, b) => a[1].localeCompare(b[1], 'zh'))
      .map(([value, label]) => ({ value, label }));
    if (docs.some(d => !d.template_id)) {
      list.push({ value: 'none', label: t('dashboard.content.templateFilterNone', '无模板') });
    }
    return list;
  }, [docs, t]);

  const filteredDocs = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    const statusSet = new Set<ContentStatusFilter>(statuses);
    const sourceSet = new Set<string>(sources);
    const tplSet = new Set<string>(templates);
    const base = docs.filter(d => {
      if (statusSet.size) {
        const effectiveStatus: ContentStatusFilter = isGenFailed(d) ? 'gen_failed' : d.status;
        if (!statusSet.has(effectiveStatus)) return false;
      }
      if (sourceSet.size && !sourceSet.has(d.source)) return false;
      if (tplSet.size && !tplSet.has(d.template_id ? String(d.template_id) : 'none')) return false;
      if (!inDateRange(d.created_at, dateRange)) return false;
      if (!q) return true;
      const hay = [d.title, d.summary, d.source_query_text, d.template_name]
        .filter(Boolean).join(' ').toLowerCase();
      return hay.includes(q);
    });
    // Same time (generated in the same batch): fall back to id for a stable order
    const byUpdated = sortOrder === 'updated' || sortOrder === 'updated_oldest';
    const ts = (d: ContentDoc) => new Date(
      (byUpdated ? (d.updated_at || d.created_at) : d.created_at)).getTime() || 0;
    const desc = sortOrder === 'newest' || sortOrder === 'updated';
    return [...base].sort((a, b) => (
      desc ? ts(b) - ts(a) || b.id - a.id : ts(a) - ts(b) || a.id - b.id
    ));
  }, [docs, statuses, sources, templates, searchText, sortOrder, dateRange]);

  const pagedDocs = filteredDocs.slice((page - 1) * pageSize, page * pageSize);
  const filterActive = statuses.length > 0 || sources.length > 0
    || templates.length > 0 || searchText.trim() !== '' || isDateFilterActive(dateRange);
  const resetFilters = useCallback(() => {
    setStatuses([]); setSources([]); setTemplates([]); setSearchText('');
    setDateRange(DATE_RANGE_ALL);
  }, []);

  // Switching topic / status / source / template / search / time / sort / page size -> back to page one
  useEffect(() => {
    setPage(1);
  }, [topicId, statuses, sources, templates, searchText, dateRange, sortOrder, pageSize]);

  return (
    <div className="space-y-4">
      <PageHead titleKey="dashboard.nav.content" titleFallback="Content" />

      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-primary">
            {t('dashboard.nav.content')}
          </h1>
          <p className="text-xs text-secondary mt-0.5">
            {t('dashboard.content.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {lockedTopicId == null && (
            <UiSelect
              value={String(topicId ?? '')}
              onChange={(val) => { setTopicId(Number(val)); setOpenDocId(null); }}
              disabled={topics.length === 0}
              className="px-3 py-1.5 rounded-md text-sm min-w-[200px]"
              style={{
                background: 'var(--bg-input)',
                border: '1px solid var(--border-color)',
                color: 'var(--text-primary)',
              }}
              options={[
                ...(topics.length === 0 ? [{ value: '', label: t('dashboard.content.noTopic') }] : []),
                ...topics.map(x => ({ value: String(x.id), label: x.name })),
              ]}
            />
          )}
          <button
            type="button"
            disabled={!topicApproved}
            onClick={() => setShowSchedule(true)}
            className="text-xs px-3 py-1.5 rounded-md"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--accent-primary)',
              opacity: topicApproved ? 1 : 0.5,
            }}
          >
            ⏰ {t('dashboard.content.aiScheduleBtn')}
          </button>
          <button
            type="button"
            disabled={!topicApproved}
            onClick={() => setShowSubmit(true)}
            className="px-3 py-1.5 text-sm rounded-md text-white"
            style={{
              background: 'var(--accent-primary)',
              opacity: topicApproved ? 1 : 0.5,
            }}
          >
            + {t('dashboard.content.submitMyArticleBtn')}
          </button>
        </div>
      </header>

      {!topicApproved && currentTopic && (
        <div
          className="rounded-md p-2.5 text-xs"
          style={{
            background: 'rgba(234,179,8,0.1)',
            color: 'var(--warn-fg)',
            border: '1px solid rgba(234,179,8,0.3)',
          }}
        >
          {t('dashboard.content.topicNotApproved')}
        </div>
      )}

      {/* Stat cards */}
      <StatsRow stats={stats} topic={currentTopic} t={t}
                onScheduleClick={() => setShowSchedule(true)} />

      {/* Filters */}
      <FilterBar>
        <FilterBarPrimary
          right={
            <>
              <FilterField label={t('dashboard.content.filterDateLabel', '生成时间')}>
                <DateRangeFilter value={dateRange} onChange={setDateRange} />
              </FilterField>
              <UiSelect
                value={sortOrder}
                onChange={(val) => setSortOrder(
                  val === 'oldest' || val === 'newest' || val === 'updated_oldest'
                    ? val : 'updated')}
                title={t('dashboard.content.sortLabel', '排序')}
                className={`${FILTER_CONTROL_CLASS} min-w-[150px]`}
                style={FILTER_CONTROL_STYLE}
                options={[
                  { value: 'updated',
                    label: t('dashboard.content.sortUpdated', '更新时间 · 最新在前') },
                  { value: 'updated_oldest',
                    label: t('dashboard.content.sortUpdatedOldest', '更新时间 · 最早在前') },
                  { value: 'newest', label: t('dashboard.content.sortNewest', '生成时间 · 最新在前') },
                  { value: 'oldest', label: t('dashboard.content.sortOldest', '生成时间 · 最早在前') },
                ]}
              />
            </>
          }
        >
          <SearchInput
            value={searchText}
            onChange={setSearchText}
            placeholder={t('dashboard.content.searchPlaceholder', '搜标题 / 摘要 / 问题')}
            className="w-full sm:w-[240px]"
          />
        </FilterBarPrimary>

        <FilterBarSecondary
          right={
            <FilterSummary active={filterActive} matched={filteredDocs.length}
                           total={docs.length} onReset={resetFilters} />
          }
        >
          <FilterField label={t('dashboard.content.filterStatusLabel')}>
            <UiMultiSelect
              values={statuses}
              onChange={v => setStatuses(v as ContentStatusFilter[])}
              allLabel={t('dashboard.content.statusFilter.all')}
              className={`${FILTER_CONTROL_CLASS} min-w-[110px]`}
              style={FILTER_CONTROL_STYLE}
              options={STATUS_OPTIONS.map(k => ({
                value: k,
                label: t(`dashboard.content.statusFilter.${k}`),
              }))}
            />
          </FilterField>
          <FilterField label={t('dashboard.content.filterSourceLabel')}>
            <UiMultiSelect
              values={sources}
              onChange={v => setSources(v as DocSource[])}
              allLabel={t('dashboard.content.sourceFilter.all')}
              className={`${FILTER_CONTROL_CLASS} min-w-[100px]`}
              style={FILTER_CONTROL_STYLE}
              options={(['ai', 'user'] as DocSource[]).map(k => ({
                value: k,
                label: t(`dashboard.content.sourceFilter.${k}`),
              }))}
            />
          </FilterField>
          {templateOptions.length > 0 && (
            <FilterField label={t('dashboard.content.filterTemplateLabel', '模板')}>
              <UiMultiSelect
                values={templates}
                onChange={setTemplates}
                allLabel={t('dashboard.content.templateFilterAll', '全部模板')}
                className={`${FILTER_CONTROL_CLASS} min-w-[120px] max-w-[220px]`}
                style={FILTER_CONTROL_STYLE}
                options={templateOptions}
              />
            </FilterField>
          )}
        </FilterBarSecondary>
      </FilterBar>

      {err && (
        <div
          className="rounded-md p-3 text-sm"
          style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)', border: '1px solid rgba(239,68,68,0.3)' }}
        >
          {err}
        </div>
      )}

      {loading && <div className="py-12 text-center text-sm text-muted">…</div>}

      {!loading && filteredDocs.length === 0 && (
        <div className="py-12 text-center text-sm text-muted">
          {t('dashboard.content.empty')}
        </div>
      )}

      <div className="space-y-2">
        {pagedDocs.map(d => (
          <DocCard key={d.id} doc={d} tasks={docTasks.get(d.id)} onOpen={() => setOpenDocId(d.id)} />
        ))}
      </div>

      {filteredDocs.length > 0 && (
        <Pagination page={page} pageSize={pageSize} total={filteredDocs.length}
          onPageChange={setPage} onPageSizeChange={setPageSize} className="pt-1" />
      )}

      {openDoc && (
        <DocDetailDrawer
          doc={openDoc}
          onClose={() => setOpenDocId(null)}
          onChanged={() => { refresh(); }}
        />
      )}

      {showSubmit && currentTopic && (
        <SubmitDrawer
          topic={currentTopic}
          onClose={() => setShowSubmit(false)}
          onSubmitted={() => { setShowSubmit(false); refresh(); }}
        />
      )}

      {showSchedule && currentTopic && (
        <AutoScheduleDrawer
          topic={currentTopic}
          onClose={() => setShowSchedule(false)}
          onSaved={() => { setShowSchedule(false); reloadTopics(); refresh(); }}
        />
      )}
    </div>
  );
}

// ─────────────────────────── Stat cards ───────────────────────────

function StatsRow({
  stats, topic, t, onScheduleClick,
}: {
  stats: ContentStats | null; topic: Topic | null;
  t: (k: string, v?: Record<string, unknown>) => string;
  onScheduleClick: () => void;
}) {
  const cards = [
    { key: 'published', val: stats?.published ?? 0, color: 'var(--info-fg)' },
    { key: 'pending_review', val: stats?.pending_review ?? 0, color: 'var(--warn-fg)' },
    { key: 'rejected', val: stats?.rejected ?? 0, color: 'var(--danger-fg)' },
    { key: 'draft', val: stats?.draft ?? 0, color: '#94a3b8' },
    // Only take a slot when there are failed drafts -- normally 0, and a permanently empty red card just numbs people
    ...((stats?.gen_failed ?? 0) > 0
      ? [{ key: 'gen_failed' as const, val: stats?.gen_failed ?? 0, color: 'var(--danger-fg)' }]
      : []),
  ] as const;
  const aiOn = !!topic?.auto_generate_enabled;
  const aiTime = topic?.auto_generate_time || '09:00';
  // With the extra "Generation failed" card, add a column, otherwise on large screens one card drops to a lonely new row
  return (
    <div className={`grid grid-cols-2 sm:grid-cols-3 gap-2 ${
      cards.length > 4 ? 'lg:grid-cols-6' : 'lg:grid-cols-5'}`}>
      {cards.map(c => (
        <div
          key={c.key}
          className="rounded-md p-3"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
        >
          <div className="text-[11px] text-muted">
            {t(`dashboard.content.statsCard.${c.key}`)}
          </div>
          <div className="mt-1 text-xl font-semibold" style={{ color: c.color }}>
            {c.val}
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={onScheduleClick}
        className="rounded-md p-3 text-left"
        style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}
      >
        <div className="text-[11px] text-muted">
          {t('dashboard.content.statsCard.aiSchedule')}
        </div>
        <div className="mt-1 text-sm font-semibold text-primary">
          {aiOn
            ? t('dashboard.content.aiOnAtTime', { time: aiTime })
            : t('dashboard.content.aiOff')}
        </div>
      </button>
    </div>
  );
}

// ─────────────────────────── List cards ───────────────────────────

function DocCard({ doc, tasks, onOpen }: {
  doc: ContentDoc; tasks?: PublishTask[]; onOpen: () => void;
}) {
  const { t } = useTranslation();
  return (
    <article
      className="rounded-md p-3 cursor-pointer"
      onClick={onOpen}
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <div className="flex items-start gap-2 flex-wrap">
        <h3 className="text-sm font-semibold text-primary flex-1 min-w-[200px]">
          {failedDocTitle(doc)}
        </h3>
        <DocStatusChip status={doc.status} genFailed={isGenFailed(doc)} />
        <DocSourceChip source={doc.source} />
      </div>
      {(tasks || []).length > 0 && (
        <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
          {(tasks || []).map(tk => (
            <span key={tk.id} className="text-[10px] px-1.5 py-0.5 rounded-full"
                  title={tk.channel_meta?.select_reason || ''}
                  style={{
                    background: tk.status === 'published' ? 'var(--success-bg)'
                      : isPaidMediaTask(tk) ? 'var(--warn-bg)' : 'var(--bg-input)',
                    color: tk.status === 'published' ? 'var(--success-fg)'
                      : isPaidMediaTask(tk) ? '#f59e0b' : 'var(--text-secondary)',
                  }}>
              {taskDisplayName(tk)} {taskStatusLabel(tk)}
              {tk.channel_meta?.auto_selected ? ' ✦' : ''}
            </span>
          ))}
        </div>
      )}
      {doc.source_query_text && (
        <p className="text-[11px] text-muted mt-1">
          <span className="text-secondary">Q:</span> {doc.source_query_text}
        </p>
      )}
      {doc.summary && (
        <p className="text-xs text-secondary mt-1.5 line-clamp-2">{doc.summary}</p>
      )}
      {doc.reject_reason && (
        <p className="text-[11px] mt-1.5" style={{ color: 'var(--danger-fg)' }}>
          ⚠ {t('dashboard.content.rejectReasonLabel')}:{doc.reject_reason}
        </p>
      )}
      <div className="mt-2 flex items-center gap-3 text-[11px] text-muted flex-wrap">
        <span>
          🕒 {t('dashboard.content.generatedAt', '生成时间')}
          :{fmtTime(doc.created_at)}
        </span>
        {/* Regeneration overwrites in place: created_at stays at the creation day, only this shows the body was rewritten */}
        {isRewritten(doc) && (
          <span style={{ color: 'var(--accent-primary)' }}
                title={t('dashboard.content.rewrittenHint',
                  '这篇正文被重新生成过,创建时间仍是原来那天')}>
            ↻ {t('dashboard.content.updatedAt', '更新时间')}
            :{fmtTime(doc.updated_at as string)}
          </span>
        )}
        {/* 2026-07-31 — which publish plan row this draft corresponds to, matching #N in the plan table */}
        {doc.plan_seq != null && (
          <span title={doc.plan_publish_date
            ? `${t('dashboard.content.planRow', '发文计划')} #${doc.plan_seq} · ${t('dashboard.content.planDate', '计划发布')} ${doc.plan_publish_date.slice(0, 10)}`
            : undefined}>
            📋 {t('dashboard.content.planRow', '发文计划')} #{doc.plan_seq}
            {doc.plan_publish_date && ` · ${doc.plan_publish_date.slice(5, 10)}`}
          </span>
        )}
        {doc.publish_targets.length > 0 && (
          <span className="inline-flex items-center gap-1">
            🌐 {doc.publish_targets.map((p, i) => (
              <span key={i} className="px-1.5 py-0.5 rounded"
                    style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--info-fg)' }}>
                {p.platform}{p.media ? `·${p.media}` : ''}
              </span>
            ))}
          </span>
        )}
      </div>
    </article>
  );
}

/**
 * Status pill.
 *
 * `genFailed` is passed separately: drafts that failed generation are also `pending_review` in the DB
 * (content_generator intentionally moves them out of `generating` on failure, otherwise they'd be stuck in "Generating" forever),
 * so looking only at status would show users "Under review" -- while the body was never generated. The criterion is
 * `generation_error`, the same definition as the failure marker on the "Content publishing & review" side.
 */
function DocStatusChip({ status, genFailed }: { status: DocStatus; genFailed?: boolean }) {
  const { t } = useTranslation();
  if (genFailed) {
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold"
            style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
        {t('dashboard.content.generationErrorLabel')}
      </span>
    );
  }
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
    <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold"
          style={{ background: s.bg, color: s.c }}>
      {t(`dashboard.content.statusFilter.${status}`)}
    </span>
  );
}

function DocSourceChip({ source }: { source: DocSource }) {
  const { t } = useTranslation();
  const cm = source === 'ai'
    ? { c: '#8b5cf6', bg: 'rgba(139,92,246,0.15)' }
    : { c: '#06b6d4', bg: 'rgba(6,182,212,0.15)' };
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full"
          style={{ background: cm.bg, color: cm.c }}>
      {t(`dashboard.content.sourceFilter.${source}`)}
    </span>
  );
}

// md -> plain text (dependency-free): strip Markdown markup, for pasting into platforms that accept only plain text.
export function mdToPlainText(md: string): string {
  if (!md) return '';
  let s = md;
  s = s.replace(/^\s*```.*$/gm, '');              // Code fence lines (keep inner text)
  s = s.replace(/`([^`]+)`/g, '$1');              // Inline code
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1'); // Image -> alt
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');  // Link -> text
  s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '');       // Heading markers
  s = s.replace(/^\s{0,3}>\s?/gm, '');            // Blockquote markers
  s = s.replace(/^\s{0,3}([-*_] ?){3,}\s*$/gm, ''); // Horizontal rules (handled before list markers)
  s = s.replace(/^\s{0,3}[-*+]\s+/gm, '• ');      // Unordered list -> •
  s = s.replace(/(\*\*|__)(.*?)\1/g, '$2');       // Bold
  s = s.replace(/(\*|_)(.*?)\1/g, '$2');          // Italic
  s = s.replace(/~~(.*?)~~/g, '$1');              // Strikethrough
  s = s.replace(/\n{3,}/g, '\n\n');               // Collapse extra blank lines
  return s.trim();
}

// Copy button: instant "Copied" feedback on click (modeled on CopyBtn in Account/AgentIntegrationTab).
function CopyContentBtn({ label, getText }: { label: string; getText: () => string }) {
  const { t } = useTranslation();
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        const text = getText();
        if (!text) return;
        void navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
      className="text-xs px-2.5 py-1 rounded-md whitespace-nowrap"
      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
    >
      {done ? t('dashboard.content.copied') : label}
    </button>
  );
}

// ─────────────────────────── Detail drawer ─────────────────────────

// Exported for the plan calendar page's "View" -- it used to deep-link to a new page (?topic=&doc=),
// and one click lost the day selected on the calendar. The drawer only takes a doc + two callbacks, so it can be mounted in place.
export function DocDetailDrawer({
  doc, onClose, onChanged,
}: {
  doc: ContentDoc;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(doc.title);
  const [body, setBody] = useState(doc.body_markdown);
  const [summary, setSummary] = useState(doc.summary);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Publish center (secondary drawer) entry: the task count is always shown in the footer, no need to scroll to the bottom of the details
  const [showPublishCenter, setShowPublishCenter] = useState(false);
  const [taskCount, setTaskCount] = useState<number | null>(null);

  const refreshTaskCount = useCallback(() => {
    contentApi.listPublishTasks(doc.topic_id, { doc_id: doc.id }, token)
      .then(ts => setTaskCount(ts.length))
      .catch(() => { /* Count failure doesn't affect the details */ });
  }, [doc.id, doc.topic_id, token]);

  useEffect(() => { refreshTaskCount(); }, [refreshTaskCount]);

  const canEdit = doc.source === 'user' && (doc.status === 'draft' || doc.status === 'rejected');
  const canSubmit = !isGenFailed(doc) && (doc.status === 'draft' || doc.status === 'rejected');
  const canReview = !isGenFailed(doc)
    && (doc.status === 'draft' || doc.status === 'pending_review');   // User self-review

  const approve = async () => {
    setBusy(true); setErr(null);
    try {
      await contentApi.approveMyDoc(doc.id, token);
      onChanged(); onClose();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const reject = async () => {
    const reason = await uiPrompt('驳回原因(可留空):');
    if (reason === null) return;
    setBusy(true); setErr(null);
    try {
      await contentApi.rejectMyDoc(doc.id, reason, token);
      onChanged(); onClose();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const save = async () => {
    const wasRejected = doc.status === 'rejected';
    setBusy(true); setErr(null);
    try {
      // When a rejected draft is saved, the backend automatically rescans compliance: pass -> back to draft, fail -> refresh the rejection reason
      const fresh = await contentApi.updateMyDoc(
        doc.id, { title, body_markdown: body, summary }, token);
      setEditing(false);
      onChanged();
      if (wasRejected) {
        if (fresh.status === 'rejected') {
          toast.error(`合规复扫仍未通过:${fresh.reject_reason || '存在禁用表述'}`);
        } else {
          toast.success('合规复扫通过,稿件已恢复为草稿,可重新提交审核');
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async () => {
    setBusy(true); setErr(null);
    try {
      await contentApi.submitForReview(doc.id, token);
      onChanged();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const publishEntry = (
    <button type="button" onClick={() => setShowPublishCenter(true)}
            className="text-xs px-3 py-1.5 rounded-md"
            style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--info-fg)',
                     border: '1px solid rgba(59,130,246,0.4)' }}>
      发布 / 投放{taskCount ? `(${taskCount})` : ''}
    </button>
  );
  const showPublishEntry = doc.status === 'approved' || doc.status === 'published' || (taskCount ?? 0) > 0;

  return (
    <>
    <Drawer
      open onClose={onClose}
      header={<>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-lg font-semibold text-primary">
                {failedDocTitle(doc)}
              </h2>
              <DocStatusChip status={doc.status} genFailed={isGenFailed(doc)} />
              <DocSourceChip source={doc.source} />
            </div>
            <div className="text-xs text-muted mt-1">
              {doc.source_query_text && <span>Q: {doc.source_query_text} · </span>}
              <span>{fmtTime(doc.created_at)}</span>
              {isRewritten(doc) && (
                <span style={{ color: 'var(--accent-primary)' }}>
                  {' · ↻ '}{t('dashboard.content.updatedAt', '更新时间')}
                  :{fmtTime(doc.updated_at as string)}
                </span>
              )}
            </div>
          </div>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-xl leading-none">✕</button>
      </>}
      footer={
        <div className="p-3 flex items-center justify-between gap-2 flex-wrap">
          <div>{showPublishEntry && publishEntry}</div>
          <div className="flex justify-end gap-2 flex-wrap">{footerButtons()}</div>
        </div>
      }
    >
          {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

          {doc.reject_reason && (
            <div className="rounded-md p-2.5 text-xs"
                 style={{ background: 'rgba(239,68,68,0.05)', color: 'var(--danger-fg)',
                          border: '1px solid rgba(239,68,68,0.3)' }}>
              {t('dashboard.content.rejectReasonLabel')}:{doc.reject_reason}
            </div>
          )}
          {doc.generation_error && (
            <div className="rounded-md p-2.5 text-xs"
                 style={{ background: 'rgba(239,68,68,0.05)', color: 'var(--danger-fg)',
                          border: '1px solid rgba(239,68,68,0.3)' }}>
              {t('dashboard.content.generationErrorLabel')}:{friendlyGenerationError(doc.generation_error)}
              {/* Whether it will recover on its own -- without saying so, users can only keep clicking regenerate */}
              <div className="mt-1 opacity-80">
                {doc.gen_next_retry_at
                  ? t('dashboard.content.genRetryQueued',
                      { done: doc.gen_attempts || 0, max: GEN_RETRY_MAX })
                  : t('dashboard.content.genRetryStopped')}
              </div>
            </div>
          )}

          <DocRelationBlock doc={doc} />

          {(doc.body_markdown || editing) && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] text-muted mr-auto">{t('dashboard.content.copyHint')}</span>
              <CopyContentBtn
                label={t('dashboard.content.copyMarkdown')}
                getText={() => (editing ? body : doc.body_markdown)}
              />
              {/* 2026-07-31 — "Copy article" puts formatted content on the clipboard (paste directly into WeChat official accounts / Word / Feishu),
                  also writing text/plain so plain-text editors still get a clean body */}
              <button type="button"
                      onClick={() => copyArticle(doc.title, editing ? body : doc.body_markdown)}
                      className="text-xs px-2.5 py-1 rounded-md whitespace-nowrap"
                      title={t('dashboard.content.copyArticleHint', '带标题层级和加粗,可直接贴进公众号 / Word / 飞书文档')}
                      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                               border: '1px solid var(--border-color)' }}>
                {t('dashboard.content.copyArticle', '复制文章')}
              </button>
              <CopyContentBtn
                label={t('dashboard.content.copyText')}
                getText={() => mdToPlainText(editing ? body : doc.body_markdown)}
              />
            </div>
          )}

          {!editing && (
            <article
              className="text-sm text-primary leading-relaxed rounded-md p-3"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}
            >
              <MarkdownPreview
                value={doc.body_markdown}
                emptyText={t('dashboard.content.emptyBody')}
              />
            </article>
          )}

          {editing && (
            <div className="space-y-2">
              <input
                type="text"
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder={t('dashboard.content.submitTitlePh')}
                className="w-full text-sm px-3 py-2 rounded-md"
                style={{
                  background: 'var(--bg-input)', color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                }}
              />
              <textarea
                value={summary}
                onChange={e => setSummary(e.target.value)}
                rows={2}
                placeholder={t('dashboard.content.submitSummaryPh')}
                className="w-full text-sm px-3 py-2 rounded-md resize-none"
                style={{
                  background: 'var(--bg-input)', color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                }}
              />
              <ArticleBodyEditor
                topicId={doc.topic_id}
                token={token}
                value={body}
                onChange={setBody}
                placeholder={t('dashboard.content.submitBodyPh')}
                minHeight={360}
              />
            </div>
          )}

          {doc.publish_targets.length > 0 && (
            <section>
              <p className="text-xs text-muted mb-1.5">{t('dashboard.content.publishedToLabel')}:</p>
              <div className="flex flex-wrap gap-1.5">
                {doc.publish_targets.map((p, i) => (
                  <span key={i}
                        className="text-[11px] px-2.5 py-1 rounded-full inline-flex items-center gap-1.5"
                        style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--info-fg)' }}>
                    {p.platform}{p.media ? ` / ${p.media}` : ''}
                    {p.url && (
                      <a
                        href={p.url} target="_blank" rel="noreferrer"
                        onClick={e => e.stopPropagation()}
                        className="underline opacity-80 hover:opacity-100"
                      >🔗</a>
                    )}
                    <span className="text-muted opacity-80">
                      {p.marked_at ? fmtTime(p.marked_at, false).slice(0, 10) : ''}
                    </span>
                  </span>
                ))}
              </div>
            </section>
          )}

    </Drawer>
    <PublishCenterDrawer
      open={showPublishCenter}
      onClose={() => setShowPublishCenter(false)}
      doc={doc} token={token}
      onChanged={() => { refreshTaskCount(); onChanged(); }}
    />
    </>
  );

  function footerButtons() {
    return (
      <>
          {!editing && canEdit && (
            <button type="button" onClick={() => setEditing(true)}
                    className="text-xs px-3 py-1.5 rounded-md"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                             border: '1px solid var(--border-color)' }}>
              {t('dashboard.content.editBtn')}
            </button>
          )}
          {editing && (
            <>
              <button type="button" disabled={busy}
                      onClick={() => {
                        setEditing(false);
                        setTitle(doc.title); setBody(doc.body_markdown); setSummary(doc.summary);
                      }}
                      className="text-xs px-3 py-1.5 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                {t('dashboard.content.cancelBtn')}
              </button>
              <button type="button" disabled={busy} onClick={save}
                      className="text-xs px-3 py-1.5 rounded-md text-white"
                      style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
                {busy ? '...' : t('dashboard.content.saveBtn')}
              </button>
            </>
          )}
          {!editing && canSubmit && (
            <button type="button" disabled={busy} onClick={submitReview}
                    className="text-xs px-3 py-1.5 rounded-md"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)', opacity: busy ? 0.5 : 1 }}>
              {t('dashboard.content.submitForReviewBtn')}
            </button>
          )}
          {!editing && canReview && (
            <>
              <button type="button" disabled={busy} onClick={reject}
                      className="text-xs px-3 py-1.5 rounded-md"
                      style={{ background: 'transparent', color: 'var(--danger-fg)', border: '1px solid rgba(239,68,68,0.4)', opacity: busy ? 0.5 : 1 }}>
                驳回
              </button>
              <button type="button" disabled={busy} onClick={approve}
                      className="text-xs px-3 py-1.5 rounded-md text-white"
                      style={{ background: 'var(--success-solid)', opacity: busy ? 0.5 : 1 }}>
                {busy ? '...' : '审核通过'}
              </button>
            </>
          )}
      </>
    );
  }
}

// ─────────────────────────── Submit drawer ─────────────────────────

function SubmitDrawer({
  topic, onClose, onSubmitted,
}: {
  topic: Topic;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [summary, setSummary] = useState('');
  const [linkedQuery, setLinkedQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const selectedQueries = useMemo(() => {
    const out: string[] = [];
    (topic.queries || []).forEach((q, i) => {
      if ((topic.query_selected || [])[i] ?? true) out.push(q);
    });
    return out;
  }, [topic]);

  const submit = async (sendForReview: boolean) => {
    if (!title.trim() || !body.trim()) {
      setErr(t('dashboard.content.submitErrEmpty'));
      return;
    }
    setBusy(true); setErr(null);
    try {
      await contentApi.submitMyDoc(topic.id, {
        title: title.trim(),
        body_markdown: body,
        summary: summary.trim(),
        source_query_text: linkedQuery.trim(),
        submit_for_review: sendForReview,
      }, token);
      onSubmitted();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex"
      style={{ background: 'rgba(0,0,0,0.5)' }} {...overlayDismiss(onClose)}
    >
      <div className="flex-1" />
      <div
        className="w-full md:w-[60%] max-w-3xl h-full overflow-y-auto"
        style={{ background: 'var(--bg-card)', borderLeft: '1px solid var(--border-color)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <header
          className="sticky top-0 z-10 p-4 flex items-center justify-between border-b"
          style={{ borderColor: 'var(--border-color)', background: 'var(--bg-card)' }}
        >
          <h2 className="text-lg font-semibold text-primary">
            {t('dashboard.content.submitDrawerTitle')}
          </h2>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-xl leading-none">✕</button>
        </header>

        <div className="p-4 space-y-3">
          <p className="text-xs text-muted">{t('dashboard.content.submitDrawerHint')}</p>
          {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

          <div>
            <label className="text-xs text-muted block mb-1">
              {t('dashboard.content.submitTopicLabel')}
            </label>
            <input
              type="text" value={topic.name} disabled
              className="w-full text-sm px-3 py-2 rounded-md opacity-70"
              style={{
                background: 'var(--bg-input)', color: 'var(--text-primary)',
                border: '1px solid var(--border-color)',
              }}
            />
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">
              {t('dashboard.content.submitLinkedQueryLabel')}
            </label>
            <UiSelect
              value={linkedQuery}
              onChange={val => setLinkedQuery(val)}
              className="w-full text-sm px-3 py-2 rounded-md"
              style={{
                background: 'var(--bg-input)', color: 'var(--text-primary)',
                border: '1px solid var(--border-color)',
              }}
              options={[
                { value: '', label: t('dashboard.content.submitLinkedQueryNone') },
                ...selectedQueries.map(q => ({ value: q, label: q })),
              ]}
            />
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">
              {t('dashboard.content.submitTitleLabel')}
            </label>
            <input
              type="text" value={title} onChange={e => setTitle(e.target.value)}
              placeholder={t('dashboard.content.submitTitlePh')}
              className="w-full text-sm px-3 py-2 rounded-md"
              style={{
                background: 'var(--bg-input)', color: 'var(--text-primary)',
                border: '1px solid var(--border-color)',
              }}
            />
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">
              {t('dashboard.content.submitSummaryLabel')}
            </label>
            <textarea
              value={summary} onChange={e => setSummary(e.target.value)}
              rows={2} placeholder={t('dashboard.content.submitSummaryPh')}
              className="w-full text-sm px-3 py-2 rounded-md resize-none"
              style={{
                background: 'var(--bg-input)', color: 'var(--text-primary)',
                border: '1px solid var(--border-color)',
              }}
            />
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">
              {t('dashboard.content.submitBodyLabel')}
            </label>
            <ArticleBodyEditor
              topicId={topic.id}
              token={token}
              value={body}
              onChange={setBody}
              placeholder={t('dashboard.content.submitBodyPh')}
              minHeight={360}
            />
          </div>
        </div>

        <footer
          className="sticky bottom-0 p-3 border-t flex justify-end gap-2 flex-wrap"
          style={{ borderColor: 'var(--border-color)', background: 'var(--bg-card)' }}
        >
          <button type="button" onClick={onClose}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
            {t('dashboard.content.cancelBtn')}
          </button>
          <button type="button" disabled={busy} onClick={() => submit(false)}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                           opacity: busy ? 0.5 : 1 }}>
            {t('dashboard.content.saveDraftBtn')}
          </button>
          <button type="button" disabled={busy} onClick={() => submit(true)}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
            {busy ? '...' : t('dashboard.content.submitForReviewBtn')}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ─────────────────────────── AI scheduling drawer ──────────────────────

function AutoScheduleDrawer({
  topic, onClose, onSaved,
}: {
  topic: Topic;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [cfg, setCfg] = useState<AutoGenerateConfig>({
    enabled: !!topic.auto_generate_enabled,
    time: topic.auto_generate_time || '09:00',
    count: topic.auto_generate_count || 3,
    // On by default: if the topic hasn't explicitly turned it off (undefined), treat as on
    publish_enabled: topic.auto_publish_enabled !== false,
    // Auto-approve drafts — matches current backend behavior, on by default
    approve_enabled: topic.auto_approve_enabled !== false,
    // Auto-order paid media — spends money on its own, off by default, on only when explicitly true
    publish_paid_enabled: topic.auto_publish_paid_enabled === true,
    // What time date-only rows publish that day. Default 11:00 matches the backend fallback (existing topics' publish time unchanged)
    publish_time: topic.auto_publish_time || '11:00',
  });
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      await contentApi.configureAutoGenerate(topic.id, cfg, token);
      setMsg(t('dashboard.content.scheduleSaved'));
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    setRunning(true); setErr(null); setMsg(null);
    try {
      const r = await contentApi.runNow(topic.id, token);
      setMsg(t('dashboard.content.runNowQueued', { n: r.queued }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const seedCount = (topic.seed_prompts || []).length;
  const selectedCount = (topic.query_selected || []).filter(Boolean).length;
  const profile = topic.profile;
  const profileFilled = profile
    ? Object.values(profile).filter(v => (Array.isArray(v) ? v.length > 0 : !!v)).length
    : 0;

  return (
    <div
      className="fixed inset-0 z-50 flex"
      style={{ background: 'rgba(0,0,0,0.5)' }} {...overlayDismiss(onClose)}
    >
      <div className="flex-1" />
      <div
        className="w-full md:w-[50%] max-w-2xl h-full overflow-y-auto"
        style={{ background: 'var(--bg-card)', borderLeft: '1px solid var(--border-color)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <header
          className="sticky top-0 z-10 p-4 flex items-center justify-between border-b"
          style={{ borderColor: 'var(--border-color)', background: 'var(--bg-card)' }}
        >
          <h2 className="text-lg font-semibold text-primary">
            {t('dashboard.content.scheduleDrawerTitle')}
          </h2>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-xl leading-none">✕</button>
        </header>

        <div className="p-4 space-y-4">
          <p className="text-xs text-muted">{t('dashboard.content.scheduleDrawerHint')}</p>
          {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}
          {msg && <div className="text-xs" style={{ color: 'var(--success-fg)' }}>{msg}</div>}

          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox"
                   checked={cfg.enabled}
                   onChange={e => setCfg({ ...cfg, enabled: e.target.checked })}
                   style={{ accentColor: 'var(--accent-primary)' }} />
            <span className="text-sm text-primary">
              {t('dashboard.content.scheduleEnableLabel')}
            </span>
          </label>

          <div className="flex items-center gap-3 flex-wrap">
            <div>
              <label className="text-xs text-muted block mb-1">
                {t('dashboard.content.scheduleTimeLabel')}
              </label>
              <input type="time" value={cfg.time}
                     onChange={e => setCfg({ ...cfg, time: e.target.value })}
                     disabled={!cfg.enabled}
                     className="text-sm px-3 py-1.5 rounded-md"
                     style={{
                       background: 'var(--bg-input)', color: 'var(--text-primary)',
                       border: '1px solid var(--border-color)',
                       opacity: cfg.enabled ? 1 : 0.5,
                     }} />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">
                {t('dashboard.content.scheduleCountLabel')}
              </label>
              <input type="number" min={1} max={20} value={cfg.count}
                     onChange={e => setCfg({
                       ...cfg,
                       count: Math.max(1, Math.min(20, Number(e.target.value) || 1)),
                     })}
                     disabled={!cfg.enabled}
                     className="w-20 text-sm px-3 py-1.5 rounded-md"
                     style={{
                       background: 'var(--bg-input)', color: 'var(--text-primary)',
                       border: '1px solid var(--border-color)',
                       opacity: cfg.enabled ? 1 : 0.5,
                     }} />
            </div>
            <div className="text-xs text-muted self-end pb-1">
              {t('dashboard.content.scheduleTimeNote')}
            </div>
          </div>

          <div className="pt-1 border-t" style={{ borderColor: 'var(--border-color)' }}>
            <label className="flex items-center gap-2 cursor-pointer pt-3">
              <input type="checkbox"
                     checked={cfg.publish_enabled !== false}
                     onChange={e => setCfg({ ...cfg, publish_enabled: e.target.checked })}
                     style={{ accentColor: 'var(--accent-primary)' }} />
              <span className="text-sm text-primary">
                {t('dashboard.content.schedulePublishLabel')}
              </span>
            </label>
            <p className="text-xs text-muted mt-1 ml-6">
              {t('dashboard.content.schedulePublishHint')}
            </p>

            {/* Publish time. It used to be globally hardcoded to 11:00 with no user control, and asymmetric with the "generation time"
                above. This sets what time **plan rows without a specific time** publish that day; if a row's date in the publish plan
                includes a time, that row follows its own -- this must be stated clearly, otherwise users
                will think changing this changes every row. */}
            <div className="ml-6 mt-2 flex items-center gap-2 flex-wrap">
              <label className="text-xs text-muted">每天几点发:</label>
              <input type="time" value={cfg.publish_time || '11:00'}
                     onChange={e => setCfg({ ...cfg, publish_time: e.target.value })}
                     disabled={cfg.publish_enabled === false}
                     className="text-sm px-3 py-1.5 rounded-md"
                     style={{
                       background: 'var(--bg-input)', color: 'var(--text-primary)',
                       border: '1px solid var(--border-color)',
                       opacity: cfg.publish_enabled === false ? 0.5 : 1,
                     }} />
              <span className="text-xs text-muted">
                只对没指定具体时间的计划行生效;在发文计划里给某一行填了时分的,按那一行的时间发。
              </span>
            </div>

            <label className="flex items-center gap-2 cursor-pointer pt-3">
              <input type="checkbox"
                     checked={cfg.approve_enabled !== false}
                     onChange={e => setCfg({ ...cfg, approve_enabled: e.target.checked })}
                     style={{ accentColor: 'var(--accent-primary)' }} />
              <span className="text-sm text-primary">稿件自动过审</span>
            </label>
            <p className="text-xs text-muted mt-1 ml-6">
              开:生成成功的稿直接可发布。关:进「待审核」队列,要人工点通过之后才会发。
            </p>

            {/* The only switch that spends money on its own -- the text must fully say "whose money, when it's spent,
                and how much", so people don't discover credits being charged only after checking it. */}
            <label className="flex items-center gap-2 cursor-pointer pt-3">
              <input type="checkbox"
                     checked={cfg.publish_paid_enabled === true}
                     onChange={e => setCfg({ ...cfg, publish_paid_enabled: e.target.checked })}
                     style={{ accentColor: 'var(--warn-fg)' }} />
              <span className="text-sm" style={{ color: 'var(--warn-fg)' }}>
                付费媒体到排期日自动下单(会扣积分)
              </span>
            </label>
            <p className="text-xs text-muted mt-1 ml-6">
              开:计划里的付费媒体到了排期日,系统按当时报价直接下单,不再等你在发布中心
              逐条点「发布」。积分不足或超出单笔/单日上限会自动跳过,不会赊账发。
              关(默认):照旧每条都要你确认。
            </p>
          </div>

          <div className="rounded-md p-3 space-y-2"
               style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
            <p className="text-xs font-semibold text-secondary uppercase tracking-wide">
              {t('dashboard.content.scheduleSourcesTitle')}
            </p>
            <div className="text-xs text-secondary flex justify-between">
              <span>{t('dashboard.content.scheduleProfile')}</span>
              <span>{profileFilled} {t('dashboard.content.scheduleFields')}</span>
            </div>
            <div className="text-xs text-secondary flex justify-between">
              <span>{t('dashboard.content.scheduleSeeds')}</span>
              <span>{seedCount}</span>
            </div>
            <div className="text-xs text-secondary flex justify-between">
              <span>{t('dashboard.content.scheduleMonitored')}</span>
              <span>{selectedCount}</span>
            </div>
            {topic.auto_generate_last_run_at && (
              <div className="text-xs text-muted pt-1 border-t mt-2"
                   style={{ borderColor: 'var(--border-color)' }}>
                {t('dashboard.content.scheduleLastRunAt')}:
                {' '}
                {fmtTime(topic.auto_generate_last_run_at)}
              </div>
            )}
          </div>
        </div>

        <footer
          className="sticky bottom-0 p-3 border-t flex justify-end gap-2 flex-wrap"
          style={{ borderColor: 'var(--border-color)', background: 'var(--bg-card)' }}
        >
          <button type="button" onClick={onClose}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
            {t('dashboard.content.cancelBtn')}
          </button>
          <button type="button" disabled={running} onClick={runNow}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)',
                           opacity: running ? 0.5 : 1 }}>
            {running ? '...' : t('dashboard.content.runNowBtn')}
          </button>
          <button type="button" disabled={busy} onClick={save}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
            {busy ? '...' : t('dashboard.content.saveBtn')}
          </button>
        </footer>
      </div>
    </div>
  );
}
