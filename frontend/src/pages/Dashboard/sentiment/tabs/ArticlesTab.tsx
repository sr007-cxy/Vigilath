import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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
// 时间筛选预设 — 与参考竞品对齐
type TimePreset = 'today' | 'h24' | 'd3' | 'd7' | 'd14' | 'd30' | 'd90' | 'all' | 'custom';

// preset → 显示文案的 i18n key 后缀
const TIME_PRESETS: { key: TimePreset; labelKey: string }[] = [
  { key: 'today', labelKey: 'timeToday' },
  { key: 'h24',   labelKey: 'timeH24' },
  { key: 'd3',    labelKey: 'time3d' },
  { key: 'd7',    labelKey: 'time7d' },
  { key: 'd14',   labelKey: 'time14d' },
  { key: 'd30',   labelKey: 'time30d' },
  { key: 'd90',   labelKey: 'time90d' },
  { key: 'all',   labelKey: 'timeAll' },
  { key: 'custom', labelKey: 'timeCustom' },
];

// 把本地 Date 格式化为 'YYYY-MM-DDTHH:MM:SS'(ISO 但不带时区)— 后端按 lex 比较
function fmtLocal(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// 计算预设对应的 (start, end);'all' 返回 {} 不过滤;'custom' 由调用方处理。
function presetRange(preset: TimePreset): { start?: string; end?: string } {
  if (preset === 'all' || preset === 'custom') return {};
  const now = new Date();
  if (preset === 'today') {
    const s = new Date(now); s.setHours(0, 0, 0, 0);
    const e = new Date(now); e.setHours(23, 59, 59, 999);
    return { start: fmtLocal(s), end: fmtLocal(e) };
  }
  const hoursMap: Record<string, number> = {
    h24: 24, d3: 24 * 3, d7: 24 * 7, d14: 24 * 14, d30: 24 * 30, d90: 24 * 90,
  };
  const h = hoursMap[preset];
  if (!h) return {};
  return {
    start: fmtLocal(new Date(now.getTime() - h * 3600 * 1000)),
    end: fmtLocal(now),
  };
}

const PAGE_SIZE = 50;

// 热门媒体段位的固定平台顺序(对标 WisersOne "热门媒体" 一栏的"高频露出"思路).
// 取这 9 个是因为 A 股散户/机构舆情场景里它们 90% 命中率最高;
// 全平台仍可从下方 媒体分类/媒体类型 段位下钻到。
const POPULAR_PLATFORMS: readonly string[] = [
  'weibo', 'weixin', 'xueqiu',
  'eastmoney', 'zhihu', 'toutiao',
  'bilibili', 'douyin', 'xiaohongshu',
];

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

const ALL_SENTIMENTS: SentimentLabel[] = ['bullish', 'bearish', 'neutral', 'mixed', 'unknown'];
const ALL_RISKS: RiskLevel[] = ['none', 'low', 'medium', 'high'];

function noteworthy(p: SentimentPost): number {
  return (p.influence_potential ?? 0) * Math.abs(p.sentiment_score ?? 0);
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

  // 时间筛选 chip 通过 portal 渲染到 Sentiment.tsx 顶部 Tab 栏右侧的 slot
  const [toolbarSlot, setToolbarSlot] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setToolbarSlot(document.getElementById('sentiment-tab-toolbar'));
  }, []);

  // 滚动到底部 sentinel — 用 IntersectionObserver 自动触发 fetchNextPage
  const loadMoreRef = useRef<HTMLDivElement | null>(null);

  // 时间选择器弹层 — 点外部关闭
  // 弹层 portal 到 document.body 避免被 <nav overflow-x-auto> 等祖先裁掉,
  // 用 position:fixed 跟随触发按钮 getBoundingClientRect 定位。
  const [pickerOpen, setPickerOpen] = useState(false);
  const triggerBtnRef = useRef<HTMLButtonElement | null>(null);
  const popupContentRef = useRef<HTMLDivElement | null>(null);
  const [popupPos, setPopupPos] = useState<{ top: number; right: number } | null>(null);

  useEffect(() => {
    if (!pickerOpen) return;
    const updatePos = () => {
      const el = triggerBtnRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      setPopupPos({
        top: rect.bottom + 4,
        right: Math.max(8, window.innerWidth - rect.right),
      });
    };
    updatePos();
    window.addEventListener('resize', updatePos);
    window.addEventListener('scroll', updatePos, true);  // 捕获嵌套滚动
    return () => {
      window.removeEventListener('resize', updatePos);
      window.removeEventListener('scroll', updatePos, true);
    };
  }, [pickerOpen]);

  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      const inTrigger = triggerBtnRef.current?.contains(target);
      const inPopup = popupContentRef.current?.contains(target);
      if (!inTrigger && !inPopup) setPickerOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [pickerOpen]);

  // ── 时间筛选 + 分页 状态 ─────────────────────────────────
  const [timePreset, setTimePreset] = useState<TimePreset>('today');
  // datetime-local 格式:YYYY-MM-DDTHH:MM(无秒)
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

  // 切到 custom 时自动填默认范围 = 今日 00:00 ~ 23:59
  // (用户明确没改 customStart/customEnd 时才填,避免覆盖之前输入的值)
  useEffect(() => {
    if (timePreset !== 'custom') return;
    if (customStart || customEnd) return;
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    setCustomStart(`${yyyy}-${mm}-${dd}T00:00`);
    setCustomEnd(`${yyyy}-${mm}-${dd}T23:59`);
  }, [timePreset, customStart, customEnd]);

  const customRangeError = customStart && customEnd && customStart > customEnd;

  // 把当前 preset / 自定义区间统一翻成 (start, end) 传给后端
  const effectiveRange = useMemo(() => {
    if (timePreset === 'custom') {
      return {
        start: appliedStart || undefined,
        end: appliedEnd || undefined,
      };
    }
    return presetRange(timePreset);
  }, [timePreset, appliedStart, appliedEnd]);

  const postsQuery = useInfinitePosts(
    usingMock ? null : account.id,
    usingMock ? null : account.ticker,
    {
      pageSize: PAGE_SIZE,
      start: effectiveRange.start,
      end: effectiveRange.end,
      startOffset,
    },
  );

  // 滚动到 sentinel 自动加载下一页 — 提前 200px 触发。mock 模式没分页,跳过。
  useEffect(() => {
    const el = loadMoreRef.current;
    if (!el || usingMock) return;
    if (!postsQuery.hasNextPage || postsQuery.isFetchingNextPage) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const isVisible = entries[0]?.isIntersecting;
        if (isVisible && postsQuery.hasNextPage && !postsQuery.isFetchingNextPage) {
          postsQuery.fetchNextPage();
        }
      },
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [usingMock, postsQuery.hasNextPage, postsQuery.isFetchingNextPage, postsQuery.fetchNextPage]);

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

  const handleJumpPage = () => {
    const n = parseInt(jumpInput, 10);
    if (!Number.isFinite(n) || n < 1) return;
    const target = Math.min(n, totalPages);
    setStartOffset((target - 1) * PAGE_SIZE);
  };

  // 把 ISO 'YYYY-MM-DDTHH:MM[:SS]' 缩成 'M/D HH:MM' 在按钮上展示。
  // 注意:必须在所有 hooks 之后、early return 之前的 hook 调用都已结束 ——
  // 这里是普通函数,不是 hook,可以在任意位置。
  const shortFmt = (s: string): string => {
    const m = s.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    return m ? `${parseInt(m[1])}/${parseInt(m[2])} ${m[3]}:${m[4]}` : s;
  };

  // triggerLabel 是 hook,必须在 isLoading/error 早 return *之前* 调用,
  // 否则不同渲染分支 hook 数量不一致 → React Rules of Hooks violation。
  const triggerLabel = useMemo(() => {
    if (timePreset === 'custom' && (appliedStart || appliedEnd)) {
      return `${shortFmt(appliedStart)} → ${shortFmt(appliedEnd)}`;
    }
    const found = TIME_PRESETS.find(p => p.key === timePreset);
    return t(`dashboard.sentiment.articles.filters.${found?.labelKey ?? 'timeToday'}`);
  }, [timePreset, appliedStart, appliedEnd, t]);

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

  const handleConfirmCustom = () => {
    if (customRangeError) return;
    setAppliedStart(customStart ? `${customStart}:00` : '');
    setAppliedEnd(customEnd ? `${customEnd}:59` : '');
    setTimePreset('custom');
    setPickerOpen(false);
  };

  const handlePickPreset = (key: TimePreset) => {
    setTimePreset(key);
    if (key !== 'custom') setPickerOpen(false);
  };

  // 时间筛选 — portal 到 Sentiment.tsx Tab 栏右侧
  // 触发按钮在 toolbar slot;弹出面板再 portal 到 document.body 避免被裁
  const timeTrigger = (
    <button
      ref={triggerBtnRef}
      type="button"
      onClick={() => setPickerOpen(v => !v)}
      className="text-xs px-3 py-1.5 rounded inline-flex items-center gap-1.5 transition-colors"
      style={{
        background: pickerOpen ? 'var(--accent-primary)' : 'var(--bg-surface)',
        color: pickerOpen ? '#ffffff' : 'var(--text-primary)',
        border: '1px solid var(--border-color)',
      }}
    >
      <span>📅</span>
      <span>{triggerLabel}</span>
      <span style={{ fontSize: 10 }}>{pickerOpen ? '▴' : '▾'}</span>
    </button>
  );

  const timePopup = pickerOpen && popupPos && (
    <div
      ref={popupContentRef}
      className="rounded-lg shadow-2xl"
      style={{
        position: 'fixed',
        top: popupPos.top,
        right: popupPos.right,
        zIndex: 1000,
        width: 520,
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
      }}
    >
          <div className="grid grid-cols-[1fr_160px]">
            {/* 左:datetime 输入 */}
            <div className="p-4 space-y-3" style={{ borderRight: '1px solid var(--border-color)' }}>
              <div className="text-[11px] font-semibold text-secondary uppercase tracking-wider">
                {t('dashboard.sentiment.articles.filters.timeCustom')}
              </div>
              <div>
                <label className="text-[11px] text-muted block mb-1">
                  {t('dashboard.sentiment.articles.filters.timeStart')}
                </label>
                <input type="datetime-local" value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  className="w-full text-xs px-2 py-1.5 rounded"
                  style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
              </div>
              <div>
                <label className="text-[11px] text-muted block mb-1">
                  {t('dashboard.sentiment.articles.filters.timeEnd')}
                </label>
                <input type="datetime-local" value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  className="w-full text-xs px-2 py-1.5 rounded"
                  style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
              </div>
              {customRangeError && (
                <p className="text-[11px]" style={{ color: '#dc2626' }}>
                  {t('dashboard.sentiment.articles.filters.timeRangeError')}
                </p>
              )}
            </div>

            {/* 右:preset 列表 */}
            <div className="p-2 space-y-0.5 max-h-[280px] overflow-y-auto">
              {TIME_PRESETS.map(({ key, labelKey }) => {
                const active = timePreset === key;
                return (
                  <button key={key} type="button"
                    onClick={() => handlePickPreset(key)}
                    className="w-full text-left text-xs px-2.5 py-1.5 rounded transition-colors"
                    style={{
                      background: active ? 'var(--accent-primary)' : 'transparent',
                      color: active ? '#ffffff' : 'var(--text-secondary)',
                    }}
                  >
                    {active ? '✓ ' : '  '}
                    {t(`dashboard.sentiment.articles.filters.${labelKey}`)}
                  </button>
                );
              })}
            </div>
          </div>

      {/* 底部 取消 / 确定 */}
      <div className="flex justify-end gap-2 px-3 py-2"
        style={{ borderTop: '1px solid var(--border-color)' }}>
        <button type="button" onClick={() => setPickerOpen(false)}
          className="text-xs px-3 py-1 rounded"
          style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
          {t('common.cancel') === 'common.cancel' ? '取消' : t('common.cancel')}
        </button>
        <button type="button" onClick={handleConfirmCustom}
          disabled={!!customRangeError || (!customStart && !customEnd)}
          className="text-xs px-3 py-1 rounded disabled:opacity-50"
          style={{ background: 'var(--accent-primary)', color: '#fff' }}>
          {t('common.confirm') === 'common.confirm' ? '确定' : t('common.confirm')}
        </button>
      </div>
    </div>
  );

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[240px_minmax(0,1fr)_minmax(0,1.2fr)] gap-4">
      {toolbarSlot && createPortal(timeTrigger, toolbarSlot)}
      {timePopup && createPortal(timePopup, document.body)}
      <aside className="rounded-xl p-4 space-y-4 self-start" style={cardStyle}>
        <header className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-primary">
            {t('dashboard.sentiment.articles.filters.title')}
          </h4>
          <button type="button" onClick={reset} className="text-xs text-muted hover:text-primary">
            {t('dashboard.sentiment.articles.filters.reset')}
          </button>
        </header>

        {/* 风险 / 情感 — 项数少,继续用 flex-wrap 小 chip,不强行套 3 列 grid */}
        <Section title={t('dashboard.sentiment.articles.filters.risk')}
          hint={t('dashboard.sentiment.articles.filters.multiSelectHint')}>
          <div className="flex flex-wrap gap-1">
            {ALL_RISKS.map(r => (
              <FilterChip key={r} label={t(`dashboard.sentiment.articles.risk.${r}`)}
                active={risks.has(r)} onClick={() => setRisks(toggle(risks, r))} />
            ))}
          </div>
        </Section>

        <Section title={t('dashboard.sentiment.articles.filters.sentiment')}
          hint={t('dashboard.sentiment.articles.filters.multiSelectHint')}>
          <div className="flex flex-wrap gap-1">
            {ALL_SENTIMENTS.map(s => (
              <FilterChip key={s} label={t(`dashboard.sentiment.articles.labels.${s}`)}
                active={sentiments.has(s)} onClick={() => setSentiments(toggle(sentiments, s))} />
            ))}
          </div>
        </Section>

        {/* 热门媒体 — 固定 9 个高频平台,3 列 grid,直接 toggle source */}
        <Section title={t('dashboard.sentiment.articles.filters.popular')}
          hint={t('dashboard.sentiment.articles.filters.multiSelectHint')}>
          <ChipGrid>
            <GridChip
              label={t('dashboard.sentiment.articles.filters.all')}
              count={posts.length}
              active={!sources.size}
              onClick={() => setSources(new Set())}
            />
            {POPULAR_PLATFORMS.filter(c => platformCodes.includes(c)).map(s => {
              const fromDb = platformNames.get(s);
              const labelKey = `dashboard.sentiment.articles.sourceLabels.${s}`;
              const localized = fromDb || t(labelKey);
              const display = (!fromDb && localized === labelKey) ? s : localized;
              return (
                <GridChip key={s} label={display}
                  count={sourceCounts.get(s) ?? 0}
                  active={sources.has(s)}
                  onClick={() => setSources(toggle(sources, s))} />
              );
            })}
          </ChipGrid>
        </Section>

        {/* 媒体分类 — 行业 chip,▾ 展开该行业下的全部平台供细选 */}
        <Section title={t('dashboard.sentiment.articles.filters.industry')}
          hint={t('dashboard.sentiment.articles.filters.multiSelectHint')}>
          <ChipGrid>
            <GridChip
              label={t('dashboard.sentiment.articles.filters.all')}
              count={posts.length}
              active={!industries.size}
              onClick={() => setIndustries(new Set())}
            />
            {INDUSTRY_ORDER.map(ind => {
              const c = axisCounts.industry.get(ind) ?? 0;
              const codes = platforms.filter(p => p.industry === ind).map(p => p.code);
              return (
                <ExpandableGridChip key={ind}
                  label={t(`dashboard.sentiment.articles.industries.${ind}`)}
                  count={c}
                  active={industries.has(ind)}
                  onToggleSelf={() => setIndustries(toggle(industries, ind as Industry))}
                  codes={codes}
                  platformLabel={(code) => {
                    const fromDb = platformNames.get(code);
                    const labelKey = `dashboard.sentiment.articles.sourceLabels.${code}`;
                    const localized = fromDb || t(labelKey);
                    return (!fromDb && localized === labelKey) ? code : localized;
                  }}
                  countOf={(code) => sourceCounts.get(code) ?? 0}
                  isSourceActive={(code) => sources.has(code)}
                  onToggleSource={(code) => setSources(toggle(sources, code))}
                />
              );
            })}
          </ChipGrid>
        </Section>

        {/* 媒体类型 — 全部媒体类型 chip,▾ 展开该类型下平台 */}
        <Section title={t('dashboard.sentiment.articles.filters.mediaType')}
          hint={t('dashboard.sentiment.articles.filters.multiSelectHint')}>
          <ChipGrid>
            <GridChip
              label={t('dashboard.sentiment.articles.filters.all')}
              count={posts.length}
              active={!mediaTypes.size}
              onClick={() => setMediaTypes(new Set())}
            />
            {sourceGroups.map(({ mediaType, codes }) => {
              const mtLabelKey = `dashboard.sentiment.articles.mediaTypes.${mediaType}`;
              const mtLabel = t(mtLabelKey);
              const mtDisplay = mtLabel === mtLabelKey ? mediaType : mtLabel;
              const groupCount = axisCounts.mediaType.get(mediaType) ?? 0;
              const isOther = mediaType === 'other';
              return (
                <ExpandableGridChip key={mediaType}
                  label={mtDisplay}
                  count={groupCount}
                  active={!isOther && mediaTypes.has(mediaType)}
                  onToggleSelf={isOther ? null : (() => setMediaTypes(toggle(mediaTypes, mediaType as MediaType)))}
                  codes={codes}
                  platformLabel={(code) => {
                    const fromDb = platformNames.get(code);
                    const labelKey = `dashboard.sentiment.articles.sourceLabels.${code}`;
                    const localized = fromDb || t(labelKey);
                    return (!fromDb && localized === labelKey) ? code : localized;
                  }}
                  countOf={(code) => sourceCounts.get(code) ?? 0}
                  isSourceActive={(code) => sources.has(code)}
                  onToggleSource={(code) => setSources(toggle(sources, code))}
                />
              );
            })}
          </ChipGrid>
        </Section>

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
        </header>

        {/* 自定义日期区间已并入 Tab 栏右侧 timeChips,这里不再重复 */}

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

        {/* ── 滚动到底自动加载的 sentinel + 状态指示 ── */}
        {!usingMock && filtered.length > 0 && (
          <div ref={loadMoreRef} className="py-3 text-center text-xs text-muted">
            {postsQuery.isFetchingNextPage
              ? `⏳ ${t('dashboard.sentiment.articles.page.loading')}`
              : postsQuery.hasNextPage
                ? `↓ ${t('dashboard.sentiment.articles.page.scrollHint')}`
                : `· ${t('dashboard.sentiment.articles.page.noMore')} ·`}
          </div>
        )}

        {/* ── 跳到第 N 页(可选,精确定位用)── */}
        {!usingMock && totalPages > 1 && (
          <div className="rounded-xl p-3 flex items-center justify-between flex-wrap gap-2"
            style={cardStyle}>
            <div className="flex items-center gap-2">
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

/* ── 侧栏 UI 原子(对标竞品 WisersOne 文章列表筛选区)───────────
 *
 * Section: 段标题 + "(可多选)" 灰提示 + 一条横分隔线;视觉层级清晰.
 * ChipGrid: 3 列等宽 grid + 行间小间距;chip 数量增长时只多几行,不会撑爆侧栏.
 * GridChip: 单 chip — 选中 = 浅紫底+紫字,未选 = 透明底+灰字 + 末尾灰色 count.
 * ExpandableGridChip: 在 GridChip 基础上加个 ▾ 触发器,点开内联展开下属平台
 *   list(每个 list 项也是可勾选 chip),关上又收回.UX 与竞品的"medical
 *   /3C/...下拉细分"等价 — 但实现成内联,避免 popover 在窄侧栏里的定位坑.
 */

/* 小 chip — 风险/情感 这种 4-5 项的 enum 不套 grid,走原有的 flex-wrap 风格,
 *  视觉上跟 grid chip 区分开:实色背景 = "硬筛子",grid chip = "媒体维度切片". */
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

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-baseline gap-1.5 pb-1.5 mb-2"
        style={{ borderBottom: '1px solid var(--border-color)' }}>
        <span className="text-xs font-semibold text-primary">{title}</span>
        {hint && <span className="text-[11px] text-muted">({hint})</span>}
      </div>
      {children}
    </div>
  );
}

function ChipGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-x-1 gap-y-0.5">
      {children}
    </div>
  );
}

const chipBase = "text-xs px-1.5 py-1 rounded transition-colors text-left flex items-center justify-between";
function activeChipStyle(active: boolean): React.CSSProperties {
  return {
    background: active ? 'rgba(99,102,241,0.12)' : 'transparent',
    color: active ? 'var(--accent-primary)' : 'var(--text-secondary)',
    fontWeight: active ? 600 : 400,
  };
}

function GridChip({ label, count, active, onClick }:
  { label: string; count?: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={chipBase}
      style={activeChipStyle(active)} title={label}>
      <span className="truncate">{label}</span>
      {count !== undefined && (
        <span className="ml-1 text-[10px]" style={{ color: active ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
          {count >= 1000 ? `${(count/1000).toFixed(1)}K` : count}
        </span>
      )}
    </button>
  );
}

function ExpandableGridChip({
  label, count, active, onToggleSelf,
  codes, platformLabel, countOf, isSourceActive, onToggleSource,
}: {
  label: string;
  count: number;
  active: boolean;
  onToggleSelf: (() => void) | null;
  codes: string[];
  platformLabel: (code: string) => string;
  countOf: (code: string) => number;
  isSourceActive: (code: string) => boolean;
  onToggleSource: (code: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const hasChildren = codes.length > 0;
  return (
    <>
      <div className="flex items-center" style={activeChipStyle(active)}>
        <button type="button"
          onClick={() => onToggleSelf ? onToggleSelf() : setOpen(v => !v)}
          className="text-xs pl-1.5 py-1 truncate flex-1 text-left"
          style={{ color: 'inherit' }}
          title={label}>
          {label}
        </button>
        <span className="text-[10px] mr-1" style={{ color: active ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
          {count >= 1000 ? `${(count/1000).toFixed(1)}K` : count}
        </span>
        {hasChildren && (
          <button type="button" onClick={() => setOpen(v => !v)}
            className="text-[10px] pr-1.5 py-1 hover:text-primary"
            style={{ color: 'var(--text-muted)' }}
            aria-label={open ? '收起' : '展开'}>
            {open ? '▴' : '▾'}
          </button>
        )}
      </div>
      {open && hasChildren && (
        <div className="col-span-3 ml-2 mb-1 mt-0.5 grid grid-cols-3 gap-x-1 gap-y-0.5"
          style={{ paddingLeft: 6, borderLeft: '2px solid var(--border-color)' }}>
          {codes.map(c => (
            <GridChip key={c}
              label={platformLabel(c)}
              count={countOf(c)}
              active={isSourceActive(c)}
              onClick={() => onToggleSource(c)} />
          ))}
        </div>
      )}
    </>
  );
}
