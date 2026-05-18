// 实时热点面板 — 聚合自托管 newsnow 多站热榜,展示在舆情监控页顶部。
//
// 与 account.newsnow_sources 互补:
//   - 后者:配置在账号上,按 keywords 过滤入 posts 表 → 进入分析管线
//   - 本面板:全量裸热榜,不过滤、不入库,纯前端展示"全网在聊什么"
//
// 数据极薄:title + url,无时间戳/正文/作者(newsnow 接口天花板)。
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useNewsnowSources, useNewsnowHot } from '../../../../hooks/useSentiment';
import { timeAgo } from '../../../../mocks/sentiment';
import type { NewsnowItem, NewsnowSource } from '../../../../types/sentiment';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

interface Props {
  /** mock 模式下跳过真实拉取,展示一行占位 */
  usingMock?: boolean;
  /** 单源最多展示条数,默认 20 */
  limit?: number;
}

const FALLBACK_SOURCES: NewsnowSource[] = [
  { id: 'weibo', name_zh: '微博热搜', name_en: 'Weibo', category: 'social' },
  { id: 'zhihu', name_zh: '知乎热榜', name_en: 'Zhihu', category: 'social' },
  { id: 'toutiao', name_zh: '今日头条', name_en: 'Toutiao', category: 'news' },
  { id: '36kr', name_zh: '36 氪', name_en: '36Kr', category: 'tech' },
  { id: 'wallstreetcn', name_zh: '华尔街见闻', name_en: 'WallstreetCN', category: 'finance' },
];

export function HotTopicsPanel({ usingMock, limit = 20 }: Props) {
  const { t, i18n } = useTranslation();

  const { data: sourcesResp } = useNewsnowSources();
  const allSources: NewsnowSource[] = sourcesResp?.items ?? FALLBACK_SOURCES;

  // 把源按 category 分组渲染
  const grouped = useMemo(() => {
    const out: Record<string, NewsnowSource[]> = {};
    for (const s of allSources) {
      const k = s.category || 'other';
      (out[k] = out[k] || []).push(s);
    }
    return out;
  }, [allSources]);

  const [activeId, setActiveId] = useState<string>(() => allSources[0]?.id ?? 'weibo');

  // 切到推荐源后,如果异步加载的 sources 与当前 active 不一致也兼容
  const effectiveActive = allSources.some(s => s.id === activeId)
    ? activeId
    : (allSources[0]?.id ?? 'weibo');

  const hot = useNewsnowHot(usingMock ? null : effectiveActive, limit);
  const items = hot.data?.items ?? [];
  const fetchedAt = hot.data?.fetched_at;

  const labelOf = (s: NewsnowSource): string =>
    i18n.language?.startsWith('en') ? s.name_en : s.name_zh;

  const categoryOrder = ['social', 'news', 'tech', 'finance'];
  const sortedCategories = Object.keys(grouped).sort(
    (a, b) => categoryOrder.indexOf(a) - categoryOrder.indexOf(b),
  );

  return (
    <section className="rounded-xl p-5" style={cardStyle}>
      <header className="flex items-start justify-between gap-3 mb-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold text-primary uppercase tracking-wide">
            {t('dashboard.sentiment.today.hotTopics.title')}
          </h3>
          <p className="text-xs text-muted mt-0.5">
            {t('dashboard.sentiment.today.hotTopics.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-secondary">
          {fetchedAt && (
            <span>
              {t('dashboard.sentiment.today.hotTopics.fetchedAt', { ago: timeAgo(fetchedAt) })}
            </span>
          )}
          <button
            type="button"
            onClick={() => hot.refetch()}
            disabled={hot.isFetching || usingMock}
            className="font-semibold rounded-md px-2.5 py-1 disabled:opacity-50"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
          >
            {t('dashboard.sentiment.today.hotTopics.refresh')}
          </button>
        </div>
      </header>

      {/* 源 tab — 按 category 分组,每组横排 chip */}
      <div className="space-y-1.5 mb-3">
        {sortedCategories.map((cat) => (
          <div key={cat} className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[10px] uppercase tracking-wider text-muted min-w-[2.5rem]">
              {t(`dashboard.sentiment.today.hotTopics.categories.${cat}`, cat)}
            </span>
            {grouped[cat].map((s) => {
              const active = s.id === effectiveActive;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setActiveId(s.id)}
                  className="text-xs px-2 py-0.5 rounded-full transition-colors"
                  style={active
                    ? { background: 'var(--accent-primary)', color: '#fff' }
                    : { background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
                >
                  {labelOf(s)}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* 列表区 */}
      <div className="min-h-[180px]">
        {hot.isLoading && (
          <p className="text-sm text-secondary py-6 text-center">
            {t('dashboard.sentiment.today.hotTopics.loading')}
          </p>
        )}
        {hot.isError && !hot.isLoading && (
          <p className="text-sm py-6 text-center" style={{ color: '#dc2626' }}>
            ⚠ {t('dashboard.sentiment.today.hotTopics.error')}
          </p>
        )}
        {!hot.isLoading && !hot.isError && items.length === 0 && (
          <p className="text-sm text-secondary py-6 text-center">
            {t('dashboard.sentiment.today.hotTopics.empty')}
          </p>
        )}
        {!hot.isLoading && items.length > 0 && (
          <ol className="space-y-1">
            {items.map((it, idx) => (
              <HotItemRow key={`${effectiveActive}-${it.id ?? idx}`} item={it} rank={idx + 1} />
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

function HotItemRow({ item, rank }: { item: NewsnowItem; rank: number }) {
  const href = item.url || item.mobileUrl;
  const hotness = item.extra?.info;
  const rankColor =
    rank <= 3 ? '#dc2626' : rank <= 10 ? 'var(--accent-primary)' : 'var(--text-muted)';

  const content = (
    <span className="flex items-baseline gap-2 group min-w-0">
      <span
        className="text-xs font-bold tabular-nums w-6 shrink-0"
        style={{ color: rankColor }}
      >
        {rank}
      </span>
      <span className="text-sm text-primary truncate group-hover:underline">
        {item.title}
      </span>
      {hotness && (
        <span className="text-[11px] text-muted shrink-0 tabular-nums">{hotness}</span>
      )}
    </span>
  );

  if (!href) return <li className="py-1">{content}</li>;

  return (
    <li>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="block py-1 px-1 rounded -mx-1 hover:bg-[var(--bg-tertiary)] transition-colors"
      >
        {content}
      </a>
    </li>
  );
}
