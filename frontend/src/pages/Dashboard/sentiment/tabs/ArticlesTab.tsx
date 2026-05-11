import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useSearchParams } from 'react-router-dom';

import { useTranslation } from 'react-i18next';

import { mockPosts } from '../../../../mocks/sentiment';
import { useInfinitePosts, useGenerateDraft, useSentimentPlatforms } from '../../../../hooks/useSentiment';
import type {
  SentimentAccount, SentimentPost, SentimentLabel, RiskLevel,
} from '../../../../types/sentiment';

import { PostCard } from '../components/PostCard';
import { PostDetail } from '../components/PostDetail';
import { FilterChip } from '../components/filterChips';
import {
  AdvancedFilterModal, type AdvancedFilterValue,
} from '../components/AdvancedFilterModal';

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

// ── 双月范围选择器辅助 ─────────────────────────────────────
// dateKey: 'YYYY-MM-DD'(只取年月日,不带时间)
function dateKey(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// 从 'YYYY-MM-DDTHH:MM[:SS]' 中拆 date / time
function splitDt(s: string): { date: string; time: string } {
  if (!s) return { date: '', time: '' };
  const m = s.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? { date: m[1], time: m[2] } : { date: '', time: '' };
}

// 构造某月的 42 cell 网格(6 周 × 7 天,周一开头),含上下月填充
function buildMonth(year: number, month: number): Date[] {
  const first = new Date(year, month, 1);
  const startWeekday = (first.getDay() + 6) % 7;  // Mon=0, Sun=6
  const cells: Date[] = [];
  for (let i = 0; i < 42; i++) {
    cells.push(new Date(year, month, i - startWeekday + 1));
  }
  return cells;
}

const WEEKDAYS_ZH = ['一', '二', '三', '四', '五', '六', '日'];

// 计算预设对应的 (start, end) 或 days。
// 'all' → days:0(后端约定:0 = 全部历史;不传 days 后端默认 1 = 仅今日,会显示空)
// 'custom' → {} 由调用方处理
function presetRange(preset: TimePreset): { start?: string; end?: string; days?: number } {
  if (preset === 'all') return { days: 0 };
  if (preset === 'custom') return {};
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

  // 时间触发器现在 inline 渲染到右栏 FilterRail 里(不再 portal 到 tab 工具条)

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
  const [timePreset, setTimePreset] = useState<TimePreset>('d90');
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

  // 双月日历的"左月"位置 — 默认聚焦到 customStart 那一月
  const [calNav, setCalNav] = useState<{ y: number; m: number }>(() => {
    const d = new Date();
    return { y: d.getFullYear(), m: d.getMonth() };
  });
  // 切到 custom 且已有起始日期 → 把日历滚到那一月
  useEffect(() => {
    if (timePreset !== 'custom') return;
    const { date } = splitDt(customStart);
    if (!date) return;
    const [y, m] = date.split('-').map(Number);
    setCalNav({ y, m: m - 1 });
  }, [timePreset]);  // 只在切到 custom 时跑;手动翻月不会被覆盖

  const startDt = splitDt(customStart);
  const endDt = splitDt(customEnd);

  // 点日历某天:依次选 start / end,选完两个再点重置 start
  const handlePickDay = (d: Date) => {
    const k = dateKey(d);
    const haveStart = !!customStart;
    const haveEnd = !!customEnd;
    if (!haveStart || (haveStart && haveEnd)) {
      // 重置:仅有 start
      setCustomStart(`${k}T00:00`);
      setCustomEnd('');
      return;
    }
    // haveStart && !haveEnd
    if (k < startDt.date) {
      // 倒选了 → 反过来:把原 start 当 end,新点的当 start
      setCustomEnd(`${startDt.date}T${startDt.time || '23:59'}`);
      setCustomStart(`${k}T00:00`);
    } else {
      setCustomEnd(`${k}T23:59`);
    }
  };

  const handleStartTime = (t: string) => {
    if (!startDt.date) return;
    setCustomStart(`${startDt.date}T${t}`);
  };
  const handleEndTime = (t: string) => {
    if (!endDt.date) return;
    setCustomEnd(`${endDt.date}T${t}`);
  };

  const calNext = (m: number) => (m === 11 ? { y: calNav.y + 1, m: 0 } : { y: calNav.y, m: m + 1 });

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
      days: effectiveRange.days,
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
  // topic 与 ?q URL 参数同步 — 侧栏 KeywordGroup 子项点击 → 写 ?q → 这里读出
  const [topic, setTopicState] = useState(() => params.get('q') ?? '');
  useEffect(() => {
    const q = params.get('q') ?? '';
    if (q !== topic) setTopicState(q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  const setTopic = (v: string) => {
    setTopicState(v);
    const next = new URLSearchParams(params);
    if (v) next.set('q', v);
    else next.delete('q');
    setParams(next, { replace: true });
  };
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

  // selected = null 时右栏显示筛选面板;明确点击文章后才显示详情
  const selected: SentimentPost | null = useMemo(() => {
    if (!selectedKey) return null;
    const [src, ...rest] = selectedKey.split('-');
    const pid = rest.join('-');
    return posts.find(p => p.source === src && p.post_id === pid) || null;
  }, [selectedKey, posts]);

  // 清空 selected — PostDetail 的「← 返回」按钮调用
  const clearSelected = () => {
    setSelectedKey(null);
    const next = new URLSearchParams(params);
    next.delete('post');
    setParams(next, { replace: true });
  };

  // 切换 Set 中某项 — 顶部 chip 行用
  const toggle = <T,>(s: Set<T>, v: T): Set<T> => {
    const n = new Set(s);
    if (n.has(v)) n.delete(v); else n.add(v);
    return n;
  };

  // 高级筛选 modal 开关 — FilterRail 顶部按钮触发
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // 高级筛选 modal 内 4 项的活跃数(媒体分类 + 媒体类型 + 平台 + 仅相关关闭 → 计 1)
  const advancedActive =
    mediaTypes.size + industries.size + sources.size + (onlyRelevant ? 0 : 1);

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

  const showCustomPanel = timePreset === 'custom';
  const popupWidth = showCustomPanel ? 660 : 200;
  const leftMonth = calNav;
  const rightMonth = calNext(calNav.m);

  const timePopup = pickerOpen && popupPos && (
    <div
      ref={popupContentRef}
      className="rounded-lg shadow-2xl"
      style={{
        position: 'fixed',
        top: popupPos.top,
        right: popupPos.right,
        zIndex: 1000,
        width: popupWidth,
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
      }}
    >
      <div className="flex">
        {/* 左:日历范围选择器(仅 custom 显示) */}
        {showCustomPanel && (
          <div className="p-3 flex-1" style={{ borderRight: '1px solid var(--border-color)' }}>
            {/* 头部:start/end 概览 + 翻月按钮 */}
            <div className="flex items-center justify-between mb-2 text-xs">
              <div className="flex items-center gap-1">
                <button type="button" onClick={() => setCalNav({ y: calNav.y - 1, m: calNav.m })}
                  className="px-1.5 py-0.5 rounded hover:bg-tertiary" style={{ color: 'var(--text-secondary)' }}>«</button>
                <button type="button" onClick={() => setCalNav(calNav.m === 0 ? { y: calNav.y - 1, m: 11 } : { y: calNav.y, m: calNav.m - 1 })}
                  className="px-1.5 py-0.5 rounded hover:bg-tertiary" style={{ color: 'var(--text-secondary)' }}>‹</button>
              </div>
              <div className="flex-1 text-center font-medium" style={{ color: 'var(--text-primary)' }}>
                {leftMonth.y}年{leftMonth.m + 1}月  ·  {rightMonth.y}年{rightMonth.m + 1}月
              </div>
              <div className="flex items-center gap-1">
                <button type="button" onClick={() => setCalNav(calNav.m === 11 ? { y: calNav.y + 1, m: 0 } : { y: calNav.y, m: calNav.m + 1 })}
                  className="px-1.5 py-0.5 rounded hover:bg-tertiary" style={{ color: 'var(--text-secondary)' }}>›</button>
                <button type="button" onClick={() => setCalNav({ y: calNav.y + 1, m: calNav.m })}
                  className="px-1.5 py-0.5 rounded hover:bg-tertiary" style={{ color: 'var(--text-secondary)' }}>»</button>
              </div>
            </div>

            {/* 双月日历 */}
            <div className="grid grid-cols-2 gap-3">
              {[leftMonth, rightMonth].map(({ y, m }) => (
                <CalendarMonth key={`${y}-${m}`}
                  year={y} month={m}
                  startKey={startDt.date} endKey={endDt.date}
                  onPick={handlePickDay} />
              ))}
            </div>

            {/* 时间输入 */}
            <div className="grid grid-cols-2 gap-3 mt-3 pt-3" style={{ borderTop: '1px solid var(--border-color)' }}>
              <div>
                <label className="text-[11px] text-muted block mb-1">
                  {t('dashboard.sentiment.articles.filters.timeStart')} {startDt.date && <span className="text-primary">{startDt.date}</span>}
                </label>
                <input type="time" value={startDt.time || '00:00'}
                  onChange={(e) => handleStartTime(e.target.value)}
                  disabled={!startDt.date}
                  className="w-full text-xs px-2 py-1.5 rounded disabled:opacity-50"
                  style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
              </div>
              <div>
                <label className="text-[11px] text-muted block mb-1">
                  {t('dashboard.sentiment.articles.filters.timeEnd')} {endDt.date && <span className="text-primary">{endDt.date}</span>}
                </label>
                <input type="time" value={endDt.time || '23:59'}
                  onChange={(e) => handleEndTime(e.target.value)}
                  disabled={!endDt.date}
                  className="w-full text-xs px-2 py-1.5 rounded disabled:opacity-50"
                  style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
              </div>
            </div>
            {customRangeError && (
              <p className="text-[11px] mt-2" style={{ color: '#dc2626' }}>
                {t('dashboard.sentiment.articles.filters.timeRangeError')}
              </p>
            )}
          </div>
        )}

        {/* 右:preset 列表 */}
        <div className="p-2 space-y-0.5 max-h-[360px] overflow-y-auto" style={{ width: 160, flexShrink: 0 }}>
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

  // 右栏:无文章选中 → 280-320px 窄筛选条;选中后 → 1.2fr 宽承载正文
  const gridCls = selected
    ? 'xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]'
    : 'xl:grid-cols-[minmax(0,1.5fr)_minmax(280px,340px)]';

  const platformLabel = (code: string): string => {
    const fromDb = platformNames.get(code);
    const labelKey = `dashboard.sentiment.articles.sourceLabels.${code}`;
    const localized = fromDb || t(labelKey);
    return (!fromDb && localized === labelKey) ? code : localized;
  };

  return (
    <div className={`grid grid-cols-1 ${gridCls} gap-3`}>
      {timePopup && createPortal(timePopup, document.body)}

      {advancedOpen && (
        <AdvancedFilterModal
          value={{ mediaTypes, industries, sources, onlyRelevant }}
          platforms={platforms}
          axisCounts={axisCounts}
          sourceCounts={sourceCounts}
          platformLabel={platformLabel}
          onCancel={() => setAdvancedOpen(false)}
          onSave={(v: AdvancedFilterValue) => {
            setMediaTypes(v.mediaTypes);
            setIndustries(v.industries);
            setSources(v.sources);
            setOnlyRelevant(v.onlyRelevant);
            setAdvancedOpen(false);
          }}
        />
      )}

      {/* ── 中:仅 排序/计数 header + 文章列表(筛选已搬到右栏)── */}
      <section className="space-y-2 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto xl:scrollbar-hide">
        <div className="flex items-center gap-2 py-2 px-1 xl:sticky xl:top-0 xl:z-10"
          style={{
            background: 'var(--bg-primary)',
            borderBottom: '1px solid var(--border-color)',
          }}>
          <span className="text-[11px] text-muted">
            {t('dashboard.sentiment.articles.count', { count: filtered.length })}
            {!usingMock && total > posts.length && (
              <span className="ml-1.5">· {posts.length}/{total}</span>
            )}
          </span>
          <div className="ml-auto flex items-center gap-0.5">
            {(['influence', 'newest', 'views'] as SortKey[]).map(k => (
              <button key={k} type="button" onClick={() => setSortBy(k)}
                className={`text-xs px-1.5 py-0.5 rounded ${sortBy === k ? 'font-bold' : ''}`}
                style={{
                  background: sortBy === k ? 'var(--bg-tertiary)' : 'transparent',
                  color: sortBy === k ? 'var(--accent-primary)' : 'var(--text-secondary)',
                }}>
                {t(`dashboard.sentiment.articles.sort.${k}`)}
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
            {t('dashboard.sentiment.articles.empty')}
          </div>
        ) : (
          <div style={{ borderTop: '1px solid var(--border-color)' }}>
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

      <aside className="self-start xl:sticky xl:top-20 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto xl:scrollbar-hide rounded-xl"
        style={cardStyle}>
        {selected ? (
          <PostDetail post={selected} onBack={clearSelected} />
        ) : (
          <FilterRail
            timeTrigger={timeTrigger}
            advancedActive={advancedActive}
            onOpenAdvanced={() => setAdvancedOpen(true)}
            t={t}
            risks={risks} setRisks={setRisks}
            sentiments={sentiments} setSentiments={setSentiments}
            sources={sources} setSources={setSources}
            posts={posts}
            platformCodes={platformCodes}
            sourceCounts={sourceCounts}
            platformLabel={platformLabel}
            topic={topic} setTopic={setTopic}
            onReset={reset}
            toggle={toggle}
          />
        )}
        {generateDraft.isPending && (
          <div className="mt-2 text-xs text-muted text-center">⏳ 正在生成草稿...</div>
        )}
      </aside>
    </div>
  );
}

/* ── 右栏筛选面板 ─────────────────────────────────────────
 * 顶行:时间 + 高级筛选(open modal)+ 重置
 * 内容:搜索 / 风险 / 情感 / 热门媒体
 * 媒体分类 / 媒体类型 / 仅相关 进 AdvancedFilterModal */
interface FilterRailProps {
  timeTrigger: React.ReactNode;
  advancedActive: number;
  onOpenAdvanced: () => void;
  t: (k: string, opts?: Record<string, unknown>) => string;
  risks: Set<string>; setRisks: (s: Set<string>) => void;
  sentiments: Set<string>; setSentiments: (s: Set<string>) => void;
  sources: Set<string>; setSources: (s: Set<string>) => void;
  posts: SentimentPost[];
  platformCodes: string[];
  sourceCounts: Map<string, number>;
  platformLabel: (code: string) => string;
  topic: string; setTopic: (v: string) => void;
  onReset: () => void;
  toggle: <T,>(s: Set<T>, v: T) => Set<T>;
}

function FilterRail({
  timeTrigger, advancedActive, onOpenAdvanced, t,
  risks, setRisks, sentiments, setSentiments,
  sources, setSources, posts, platformCodes, sourceCounts,
  platformLabel, topic, setTopic, onReset, toggle,
}: FilterRailProps) {
  return (
    <div className="p-3 space-y-3">
      {/* 顶行:时间 + 高级筛选 + 重置 */}
      <div className="flex items-center gap-2 flex-wrap">
        {timeTrigger}
        <button type="button" onClick={onOpenAdvanced}
          className="text-xs px-2 py-1 rounded inline-flex items-center gap-1"
          style={{
            background: 'var(--bg-tertiary)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)',
          }}>
          <span>⚙</span>
          <span>{t('dashboard.sentiment.articles.filters.advanced')}</span>
          {advancedActive > 0 && (
            <span className="text-[10px] px-1 rounded"
              style={{ background: 'var(--accent-primary)', color: '#fff' }}>
              {advancedActive}
            </span>
          )}
        </button>
        <button type="button" onClick={onReset}
          className="ml-auto text-xs text-muted hover:text-primary">
          {t('dashboard.sentiment.articles.filters.reset')}
        </button>
      </div>

      {/* 搜索 */}
      <input type="text" value={topic} onChange={(e) => setTopic(e.target.value)}
        placeholder={t('dashboard.sentiment.articles.filters.search')}
        className="w-full px-2 py-1.5 text-xs rounded"
        style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />

      {/* 风险 */}
      <RailSection title={t('dashboard.sentiment.articles.filters.risk')}>
        {ALL_RISKS.map(r => (
          <FilterChip key={r}
            label={t(`dashboard.sentiment.articles.risk.${r}`)}
            active={risks.has(r)}
            onClick={() => setRisks(toggle(risks, r))} />
        ))}
      </RailSection>

      {/* 情感 */}
      <RailSection title={t('dashboard.sentiment.articles.filters.sentiment')}>
        {ALL_SENTIMENTS.map(s => (
          <FilterChip key={s}
            label={t(`dashboard.sentiment.articles.labels.${s}`)}
            active={sentiments.has(s)}
            onClick={() => setSentiments(toggle(sentiments, s))} />
        ))}
      </RailSection>

      {/* 热门媒体 */}
      <RailSection title={t('dashboard.sentiment.articles.filters.popular')}>
        <FilterChip
          label={`${t('dashboard.sentiment.articles.filters.all')} ${posts.length}`}
          active={!sources.size}
          onClick={() => setSources(new Set())}
        />
        {POPULAR_PLATFORMS.filter(c => platformCodes.includes(c)).map(s => {
          const count = sourceCounts.get(s) ?? 0;
          const display = platformLabel(s);
          return (
            <FilterChip key={s}
              label={count > 0 ? `${display} ${count}` : display}
              active={sources.has(s)}
              onClick={() => setSources(toggle(sources, s))} />
          );
        })}
      </RailSection>
    </div>
  );
}

function RailSection({ title, children }:
  { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-1">
        {title}
      </p>
      <div className="flex flex-wrap gap-1">{children}</div>
    </div>
  );
}

// 单月日历(7×6 网格,周一开头)— 双月选择器中两次复用
function CalendarMonth({
  year, month, startKey, endKey, onPick,
}: {
  year: number; month: number;
  startKey: string; endKey: string;
  onPick: (d: Date) => void;
}) {
  const cells = buildMonth(year, month);
  const todayKey = dateKey(new Date());
  return (
    <div>
      <div className="text-[11px] text-center font-semibold mb-1.5" style={{ color: 'var(--text-secondary)' }}>
        {year}年{month + 1}月
      </div>
      <div className="grid grid-cols-7 gap-0.5 mb-1">
        {WEEKDAYS_ZH.map(w => (
          <div key={w} className="text-[10px] text-center" style={{ color: 'var(--text-muted)' }}>{w}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {cells.map((d, i) => {
          const k = dateKey(d);
          const inMonth = d.getMonth() === month;
          const isStart = !!startKey && k === startKey;
          const isEnd = !!endKey && k === endKey;
          const inRange = !!startKey && !!endKey && k > startKey && k < endKey;
          const isToday = k === todayKey;
          let bg = 'transparent';
          let color = inMonth ? 'var(--text-primary)' : 'var(--text-muted)';
          if (isStart || isEnd) {
            bg = '#0ea5e9'; color = '#ffffff';
          } else if (inRange) {
            bg = 'rgba(14,165,233,0.18)'; color = 'var(--text-primary)';
          }
          return (
            <button key={i} type="button" onClick={() => onPick(d)}
              className="text-[11px] py-1 rounded transition-colors hover:brightness-110"
              style={{
                background: bg,
                color,
                opacity: inMonth ? 1 : 0.4,
                border: isToday && !isStart && !isEnd ? '1px solid var(--accent-primary)' : '1px solid transparent',
              }}>
              {d.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}
