import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useTranslation } from 'react-i18next';

import { mockPosts } from '../../../../mocks/sentiment';
import { useInfinitePosts, useGenerateDraft, useSentimentPlatforms } from '../../../../hooks/useSentiment';
import type {
  SentimentAccount, SentimentPost, SentimentLabel, RiskLevel,
} from '../../../../types/sentiment';
import {
  MEDIA_TYPE_ORDER, INDUSTRY_ORDER,
  type MediaType, type Industry,
} from '../../../../constants/sentimentPlatforms';

import { PostCard } from '../components/PostCard';
import { PostDetail } from '../components/PostDetail';

type SortKey = 'influence' | 'newest' | 'views';
type TimePreset = 'today' | 'd7' | 'd30' | 'all' | 'custom';

const PAGE_SIZE = 50;

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

const ALL_SENTIMENTS: SentimentLabel[] = ['bullish', 'bearish', 'neutral', 'mixed', 'unknown'];
const ALL_RISKS: RiskLevel[] = ['none', 'low', 'medium', 'high'];

function formatCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

function noteworthy(p: SentimentPost): number {
  return (p.influence_potential ?? 0) * Math.abs(p.sentiment_score ?? 0);
}

function presetToDays(preset: TimePreset): number | undefined {
  switch (preset) {
    case 'today': return 1;
    case 'd7': return 7;
    case 'd30': return 30;
    case 'all': return 0;
    default: return undefined;
  }
}

interface Props {
  account: SentimentAccount;
  usingMock?: boolean;
}

export function ArticlesTab({ account, usingMock }: Props) {
  const { t, i18n } = useTranslation();
  const [params, setParams] = useSearchParams();

  const generateDraft = useGenerateDraft();
  const platforms = useSentimentPlatforms().data ?? [];
  const platformCodes = useMemo(() => platforms.map(p => p.code), [platforms]);
  const platformNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of platforms) {
      const n = i18n.language === 'zh' ? p.name_zh : p.name_en;
      if (n) m.set(p.code, n);
    }
    return m;
  }, [platforms, i18n.language]);

  // ── 时间筛选 + 分页 状态 ─────────────────────────────────
  const [timePreset, setTimePreset] = useState<TimePreset>('today');
  const [customStart, setCustomStart] = useState<string>('');
  const [customEnd, setCustomEnd] = useState<string>('');
  const [appliedStart, setAppliedStart] = useState<string>('');
  const [appliedEnd, setAppliedEnd] = useState<string>('');
  const [startOffset, setStartOffset] = useState<number>(0);
  const [jumpInput, setJumpInput] = useState<string>('');

  // 时间筛选改变 / preset 切换 → 重置分页
  useEffect(() => {
    setStartOffset(0);
    setJumpInput('');
  }, [timePreset, appliedStart, appliedEnd]);

  const customRangeError = customStart && customEnd && customStart > customEnd;

  const postsQuery = useInfinitePosts(
    usingMock ? null : account.id,
    usingMock ? null : account.ticker,
    {
      pageSize: PAGE_SIZE,
      days: timePreset === 'custom' ? undefined : presetToDays(timePreset),
      start: timePreset === 'custom' ? appliedStart || undefined : undefined,
      end: timePreset === 'custom' ? appliedEnd || undefined : undefined,
      startOffset,
    },
  );

  const posts: SentimentPost[] = useMemo(() => {
    if (usingMock) return mockPosts as SentimentPost[];
    return (postsQuery.data?.pages ?? []).flatMap(p => p.items ?? []);
  }, [usingMock, postsQuery.data]);

  const total = usingMock
    ? (mockPosts as SentimentPost[]).length
    : (postsQuery.data?.pages?.[0]?.total ?? posts.length);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(startOffset / PAGE_SIZE) + 1;
  const isLoading = !usingMock && postsQuery.isLoading;
  const error = !usingMock ? postsQuery.error : null;

  // ── 客户端筛选状态(在已加载页内做二次筛选)──────────────
  const [sentiments, setSentiments] = useState<Set<string>>(new Set());
  const [risks, setRisks] = useState<Set<string>>(new Set());
  const [mediaTypes, setMediaTypes] = useState<Set<string>>(new Set());
  const [industries, setIndustries] = useState<Set<string>>(new Set());
  const [sources, setSources] = useState<Set<string>>(new Set());
  const [topic, setTopic] = useState('');
  const [onlyRelevant, setOnlyRelevant] = useState(true);
  const [sortBy, setSortBy] = useState<SortKey>('influence');
  const [expandedMt, setExpandedMt] = useState<Set<string>>(new Set());

  // source code → (media_type, industry) 查找表 — 用于按双轴 chip 收窄 sources
  const platformAxes = useMemo(() => {
    const m = new Map<string, { media_type: string; industry: string }>();
    for (const p of platforms) m.set(p.code, { media_type: p.media_type, industry: p.industry });
    return m;
  }, [platforms]);

  // sources 经 mediaTypes/industries chip 收窄后的有效 source 白名单
  const allowedSources = useMemo(() => {
    if (!mediaTypes.size && !industries.size) return null;  // null = 不收窄
    const allowed = new Set<string>();
    for (const p of platforms) {
      if (mediaTypes.size && !mediaTypes.has(p.media_type)) continue;
      if (industries.size && !industries.has(p.industry)) continue;
      allowed.add(p.code);
    }
    return allowed;
  }, [mediaTypes, industries, platforms]);

  const sourceCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const src of platformCodes) counts.set(src, 0);
    for (const p of posts) {
      if (onlyRelevant && !p.is_relevant) continue;
      const sl = (p.sentiment_label as string) || 'unknown';
      if (sentiments.size && !sentiments.has(sl)) continue;
      const rl = (p.risk_level as string) || 'none';
      if (risks.size && !risks.has(rl)) continue;
      if (allowedSources && !allowedSources.has(p.source)) continue;
      if (topic.trim()) {
        const tt = topic.trim();
        const inTopics = (p.topics ?? []).some(x => x.includes(tt));
        const inTitle = (p.title ?? '').includes(tt);
        const inSummary = (p.summary ?? '').includes(tt);
        if (!inTopics && !inTitle && !inSummary) continue;
      }
      counts.set(p.source, (counts.get(p.source) ?? 0) + 1);
    }
    return counts;
  }, [posts, sentiments, risks, topic, onlyRelevant, platformCodes, allowedSources]);

  // 双轴 chip 计数
  const axisCounts = useMemo(() => {
    const mt = new Map<string, number>();
    const ind = new Map<string, number>();
    for (const p of posts) {
      if (onlyRelevant && !p.is_relevant) continue;
      const sl = (p.sentiment_label as string) || 'unknown';
      if (sentiments.size && !sentiments.has(sl)) continue;
      const rl = (p.risk_level as string) || 'none';
      if (risks.size && !risks.has(rl)) continue;
      if (topic.trim()) {
        const tt = topic.trim();
        const inTopics = (p.topics ?? []).some(x => x.includes(tt));
        const inTitle = (p.title ?? '').includes(tt);
        const inSummary = (p.summary ?? '').includes(tt);
        if (!inTopics && !inTitle && !inSummary) continue;
      }
      const ax = platformAxes.get(p.source);
      if (!ax) continue;
      mt.set(ax.media_type, (mt.get(ax.media_type) ?? 0) + 1);
      ind.set(ax.industry, (ind.get(ax.industry) ?? 0) + 1);
    }
    return { mediaType: mt, industry: ind };
  }, [posts, sentiments, risks, topic, onlyRelevant, platformAxes]);

  // 来源按 media_type 分组(竖列 + 折叠)
  const sourceGroups = useMemo(() => {
    const byMt = new Map<MediaType | 'other', string[]>();
    for (const p of platforms) {
      const mt = (p.media_type as MediaType) || 'other';
      const list = byMt.get(mt) ?? [];
      list.push(p.code);
      byMt.set(mt, list);
    }
    const known = new Set<string>(platformCodes);
    const extras = Array.from(sourceCounts.keys())
      .filter(s => !known.has(s))
      .sort();
    if (extras.length) byMt.set('other', extras);

    const order: (MediaType | 'other')[] = [...MEDIA_TYPE_ORDER, 'other'];
    return order
      .filter(m => byMt.has(m))
      .map(m => ({ mediaType: m, codes: byMt.get(m)! }));
  }, [sourceCounts, platforms, platformCodes]);

  const initialKey = params.get('post');
  const [selectedKey, setSelectedKey] = useState<string | null>(initialKey);

  useEffect(() => {
    if (selectedKey) {
      const next = new URLSearchParams(params);
      next.set('tab', 'articles');
      next.set('post', selectedKey);
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey]);

  const filtered = useMemo(() => {
    let list = posts.filter(p => {
      const isRel = !!p.is_relevant;
      if (onlyRelevant && !isRel) return false;
      const sl = (p.sentiment_label as string) || 'unknown';
      if (sentiments.size && !sentiments.has(sl)) return false;
      const rl = (p.risk_level as string) || 'none';
      if (risks.size && !risks.has(rl)) return false;
      if (allowedSources && !allowedSources.has(p.source)) return false;
      if (sources.size && !sources.has(p.source)) return false;
      if (topic.trim()) {
        const tt = topic.trim();
        const inTopics = (p.topics ?? []).some(x => x.includes(tt));
        const inTitle = (p.title ?? '').includes(tt);
        const inSummary = (p.summary ?? '').includes(tt);
        if (!inTopics && !inTitle && !inSummary) return false;
      }
      return true;
    });
    if (sortBy === 'influence') {
      list = list.sort((a, b) => noteworthy(b) - noteworthy(a));
    } else if (sortBy === 'newest') {
      list = list.sort((a, b) => (b.publish_time ?? '').localeCompare(a.publish_time ?? ''));
    } else if (sortBy === 'views') {
      list = list.sort((a, b) => (b.view_count ?? 0) - (a.view_count ?? 0));
    }
    return list;
  }, [posts, sentiments, risks, sources, topic, onlyRelevant, sortBy, allowedSources]);

  const selected: SentimentPost | null = useMemo(() => {
    if (!selectedKey) return filtered[0] ?? null;
    const [src, ...rest] = selectedKey.split('-');
    const pid = rest.join('-');
    return posts.find(p => p.source === src && p.post_id === pid) || filtered[0] || null;
  }, [selectedKey, filtered, posts]);

  const toggle = <T,>(s: Set<T>, v: T): Set<T> => {
    const n = new Set(s);
    if (n.has(v)) n.delete(v); else n.add(v);
    return n;
  };

  const reset = () => {
    setSentiments(new Set()); setRisks(new Set());
    setMediaTypes(new Set()); setIndustries(new Set());
    setSources(new Set());
    setTopic(''); setOnlyRelevant(true); setSortBy('influence');
  };

  const handleApplyCustom = () => {
    if (customRangeError) return;
    setAppliedStart(customStart);
    setAppliedEnd(customEnd);
  };

  const handleJumpPage = () => {
    const n = parseInt(jumpInput, 10);
    if (!Number.isFinite(n) || n < 1) return;
    const target = Math.min(n, totalPages);
    setStartOffset((target - 1) * PAGE_SIZE);
  };

  if (isLoading && !posts.length) {
    return <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
      {t('common.loading') || 'Loading...'}
    </div>;
  }
  if (error) {
    return <div className="rounded-xl py-6 px-4 text-sm" style={{ background: 'rgba(239,68,68,0.1)', color: '#dc2626' }}>
      ⚠ {error instanceof Error ? error.message : 'Failed to load posts'}
    </div>;
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[240px_minmax(0,1fr)_minmax(0,1.2fr)] gap-4">
      <aside className="rounded-xl p-4 space-y-4 self-start" style={cardStyle}>
        <header className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-primary">
            {t('dashboard.sentiment.articles.filters.title')}
          </h4>
          <button type="button" onClick={reset} className="text-xs text-muted hover:text-primary">
            {t('dashboard.sentiment.articles.filters.reset')}
          </button>
        </header>

        {/* 时间筛选已挪到中间列 header 右上角 — 见下方 <header> */}

        <FilterGroup label={t('dashboard.sentiment.articles.filters.risk')}>
          {ALL_RISKS.map(r => (
            <FilterChip key={r} label={t(`dashboard.sentiment.articles.risk.${r}`)}
              active={risks.has(r)} onClick={() => setRisks(toggle(risks, r))} />
          ))}
        </FilterGroup>

        <FilterGroup label={t('dashboard.sentiment.articles.filters.sentiment')}>
          {ALL_SENTIMENTS.map(s => (
            <FilterChip key={s} label={t(`dashboard.sentiment.articles.labels.${s}`)}
              active={sentiments.has(s)} onClick={() => setSentiments(toggle(sentiments, s))} />
          ))}
        </FilterGroup>

        <FilterGroup label={t('dashboard.sentiment.articles.filters.industry')}>
          {INDUSTRY_ORDER.map(i => {
            const c = axisCounts.industry.get(i) ?? 0;
            return (
              <FilterChip key={i}
                label={`${t(`dashboard.sentiment.articles.industries.${i}`)} ${c}`}
                active={industries.has(i)}
                onClick={() => setIndustries(toggle(industries, i as Industry))} />
            );
          })}
        </FilterGroup>

        {/* ── 媒体类型 + 平台 竖列折叠分组 ── */}
        <div>
          <p className="text-xs font-semibold text-secondary uppercase tracking-wider mb-1.5">
            {t('dashboard.sentiment.articles.filters.mediaType')}
          </p>
          <div className="flex flex-col w-full gap-1">
            {sourceGroups.map(({ mediaType, codes }) => {
              const mtLabelKey = `dashboard.sentiment.articles.mediaTypes.${mediaType}`;
              const mtLabel = t(mtLabelKey);
              const mtDisplay = mtLabel === mtLabelKey ? mediaType : mtLabel;
              const groupCount = axisCounts.mediaType.get(mediaType) ?? 0;
              const mtActive = mediaTypes.has(mediaType);
              const isOpen = expandedMt.has(mediaType) || mtActive;
              const expanded = expandedMt.has(mediaType);
              return (
                <div key={mediaType}>
                  <div className="flex items-center justify-between text-xs px-2 py-1 rounded"
                    style={{
                      background: mtActive ? 'rgba(99,102,241,0.10)' : 'transparent',
                    }}>
                    <button type="button"
                      onClick={() => setExpandedMt(toggle(expandedMt, mediaType as string))}
                      className="flex-1 text-left flex items-center gap-1"
                      style={{ color: 'var(--text-secondary)' }}>
                      <span style={{ display: 'inline-block', width: 10 }}>{expanded ? '▾' : '▸'}</span>
                      <span className="font-semibold" style={{ color: mtActive ? 'var(--accent-primary)' : 'var(--text-secondary)' }}>
                        {mtDisplay}
                      </span>
                      <span className="text-muted ml-1">{formatCount(groupCount)}</span>
                    </button>
                    {mediaType !== 'other' && (
                      <button type="button"
                        onClick={() => setMediaTypes(toggle(mediaTypes, mediaType as MediaType))}
                        className="text-[10px] px-1.5 py-0.5 rounded ml-1"
                        style={{
                          background: mtActive ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                          color: mtActive ? '#fff' : 'var(--text-muted)',
                        }}
                        title={mtActive ? '取消筛选该媒体类型' : '只看该媒体类型'}>
                        {mtActive ? '✓' : '+'}
                      </button>
                    )}
                  </div>
                  {isOpen && (
                    <div className="flex flex-col gap-0.5 pl-4 mt-0.5">
                      {codes.map(s => {
                        const fromDb = platformNames.get(s);
                        const labelKey = `dashboard.sentiment.articles.sourceLabels.${s}`;
                        const localized = fromDb || t(labelKey);
                        const display = (!fromDb && localized === labelKey) ? s : localized;
                        const count = sourceCounts.get(s) ?? 0;
                        const active = sources.has(s);
                        return (
                          <button key={s} type="button"
                            onClick={() => setSources(toggle(sources, s))}
                            className="flex items-center justify-between text-xs px-2 py-1 rounded transition-colors"
                            style={{
                              background: active ? 'var(--accent-primary)' : 'transparent',
                              color: active ? '#ffffff' : 'var(--text-secondary)',
                            }}>
                            <span>{display}</span>
                            <span style={{ color: active ? '#ffffff' : 'var(--text-muted)' }}>
                              {formatCount(count)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div>
          <label className="text-xs font-semibold text-secondary uppercase tracking-wider block mb-1">
            {t('dashboard.sentiment.articles.filters.topic')}
          </label>
          <input type="text" value={topic} onChange={(e) => setTopic(e.target.value)}
            placeholder={t('dashboard.sentiment.articles.filters.topicPlaceholder')}
            className="w-full px-2 py-1.5 text-sm rounded"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
        </div>

        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={onlyRelevant} onChange={(e) => setOnlyRelevant(e.target.checked)} />
          <span className="text-secondary">{t('dashboard.sentiment.articles.filters.onlyRelevant')}</span>
        </label>
      </aside>

      <section className="space-y-3 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto xl:scrollbar-hide">
        <header className="flex items-center justify-between flex-wrap gap-2 xl:sticky xl:top-0 xl:z-10" style={{ background: 'var(--bg-primary)' }}>
          <span className="text-xs text-muted">
            {t('dashboard.sentiment.articles.count', { count: filtered.length })}
            {!usingMock && total > posts.length && (
              <span className="ml-2">
                · {t('dashboard.sentiment.articles.page.totalCount', { loaded: posts.length, total })}
              </span>
            )}
          </span>
          <div className="flex items-center gap-3 flex-wrap">
            {/* 时间筛选(原在左侧栏,挪到右上角)*/}
            <div className="flex items-center gap-1">
              <span className="text-xs text-muted mr-0.5">{t('dashboard.sentiment.articles.filters.time')}:</span>
              {([
                ['today', 'timeToday'],
                ['d7', 'time7d'],
                ['d30', 'time30d'],
                ['all', 'timeAll'],
                ['custom', 'timeCustom'],
              ] as const).map(([key, label]) => (
                <FilterChip key={key} label={t(`dashboard.sentiment.articles.filters.${label}`)}
                  active={timePreset === key}
                  onClick={() => setTimePreset(key)} />
              ))}
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-muted">{t('dashboard.sentiment.articles.sort.label')}:</span>
              {(['influence', 'newest', 'views'] as SortKey[]).map(k => (
                <button key={k} type="button" onClick={() => setSortBy(k)}
                  className={`text-xs px-2 py-1 rounded ${sortBy === k ? 'font-bold' : ''}`}
                  style={{
                    background: sortBy === k ? 'var(--bg-tertiary)' : 'transparent',
                    color: sortBy === k ? 'var(--accent-primary)' : 'var(--text-secondary)',
                  }}>
                  {t(`dashboard.sentiment.articles.sort.${k}`)}
                </button>
              ))}
            </div>
          </div>
        </header>

        {/* 自定义时间区间 — 仅当 timePreset='custom' 时显示在 header 下方 */}
        {timePreset === 'custom' && (
          <div className="rounded-md p-2 flex items-center gap-2 flex-wrap" style={cardStyle}>
            <input type="date" value={customStart}
              onChange={(e) => setCustomStart(e.target.value)}
              aria-label={t('dashboard.sentiment.articles.filters.timeStart')}
              className="text-xs px-2 py-1 rounded"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
            <span className="text-xs text-muted">→</span>
            <input type="date" value={customEnd}
              onChange={(e) => setCustomEnd(e.target.value)}
              aria-label={t('dashboard.sentiment.articles.filters.timeEnd')}
              className="text-xs px-2 py-1 rounded"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
            <button type="button" onClick={handleApplyCustom}
              disabled={!!customRangeError || (!customStart && !customEnd)}
              className="text-xs px-3 py-1 rounded disabled:opacity-50"
              style={{ background: 'var(--accent-primary)', color: '#fff' }}>
              {t('dashboard.sentiment.articles.filters.timeApply')}
            </button>
            {customRangeError && (
              <span className="text-[11px]" style={{ color: '#dc2626' }}>
                {t('dashboard.sentiment.articles.filters.timeRangeError')}
              </span>
            )}
          </div>
        )}

        {filtered.length === 0 ? (
          <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
            {t('dashboard.sentiment.articles.empty')}
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map(p => {
              const key = `${p.source}-${p.post_id}`;
              return (
                <PostCard key={key} post={p}
                  selected={selected ? selected.source === p.source && selected.post_id === p.post_id : false}
                  onClick={() => setSelectedKey(key)} />
              );
            })}
          </div>
        )}

        {/* ── 分页控件:加载更多 + 跳到第 N 页 ── */}
        {!usingMock && (
          <div className="rounded-xl p-3 flex items-center justify-between flex-wrap gap-2"
            style={cardStyle}>
            <div className="flex items-center gap-2">
              {postsQuery.hasNextPage ? (
                <button type="button"
                  disabled={postsQuery.isFetchingNextPage}
                  onClick={() => postsQuery.fetchNextPage()}
                  className="btn-solid rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50">
                  {postsQuery.isFetchingNextPage
                    ? t('dashboard.sentiment.articles.page.loading')
                    : t('dashboard.sentiment.articles.page.loadMore')}
                </button>
              ) : (
                <span className="text-xs text-muted">
                  {t('dashboard.sentiment.articles.page.noMore')}
                </span>
              )}
              <span className="text-[11px] text-muted">
                {t('dashboard.sentiment.articles.page.jumpHint', { size: PAGE_SIZE, pages: totalPages })}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-muted">
                {t('dashboard.sentiment.articles.page.jumpTo')}
              </span>
              <input type="number" min={1} max={totalPages}
                value={jumpInput}
                onChange={(e) => setJumpInput(e.target.value)}
                placeholder={String(currentPage)}
                className="w-14 text-xs px-2 py-1 rounded text-center"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
              <span className="text-xs text-muted">
                {t('dashboard.sentiment.articles.page.jumpToUnit')} / {totalPages}
              </span>
              <button type="button" onClick={handleJumpPage}
                className="text-xs px-2 py-1 rounded"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}>
                {t('dashboard.sentiment.articles.page.jumpGo')}
              </button>
            </div>
          </div>
        )}
      </section>

      <aside className="self-start xl:sticky xl:top-20 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto xl:scrollbar-hide">
        <PostDetail post={selected} />
        {generateDraft.isPending && (
          <div className="mt-2 text-xs text-muted text-center">⏳ 正在生成草稿...</div>
        )}
      </aside>
    </div>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-semibold text-secondary uppercase tracking-wider mb-1.5">{label}</p>
      <div className="flex flex-wrap gap-1">{children}</div>
    </div>
  );
}

function FilterChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className="text-xs px-2 py-0.5 rounded transition-colors"
      style={{
        background: active ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
        color: active ? '#ffffff' : 'var(--text-secondary)',
        border: '1px solid transparent',
      }}>
      {label}
    </button>
  );
}
