import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useTranslation } from 'react-i18next';

import { mockPosts } from '../../../../mocks/sentiment';
import { usePosts, useGenerateDraft } from '../../../../hooks/useSentiment';
import type {
  SentimentAccount, SentimentPost, SentimentLabel, RiskLevel,
} from '../../../../types/sentiment';
import { SENTIMENT_PLATFORM_CODES } from '../../../../constants/sentimentPlatforms';

import { PostCard } from '../components/PostCard';
import { PostDetail } from '../components/PostDetail';

type SortKey = 'influence' | 'newest' | 'views';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

const ALL_SENTIMENTS: SentimentLabel[] = ['bullish', 'bearish', 'neutral', 'mixed', 'unknown'];
const ALL_RISKS: RiskLevel[] = ['none', 'low', 'medium', 'high'];
const FIXED_SOURCES = SENTIMENT_PLATFORM_CODES;

function formatCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

function noteworthy(p: SentimentPost): number {
  return (p.influence_potential ?? 0) * Math.abs(p.sentiment_score ?? 0);
}

interface Props {
  account: SentimentAccount;
  usingMock?: boolean;
}

export function ArticlesTab({ account, usingMock }: Props) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();

  const generateDraft = useGenerateDraft();
  const { data: postsResp, isLoading, error } = usePosts(
    usingMock ? null : account.id,
    usingMock ? null : account.ticker,
    200,
  );

  const posts: SentimentPost[] = useMemo(
    () => usingMock ? (mockPosts as SentimentPost[]) : (postsResp?.items ?? []),
    [usingMock, postsResp],
  );

  const [sentiments, setSentiments] = useState<Set<string>>(new Set());
  const [risks, setRisks] = useState<Set<string>>(new Set());
  const [sources, setSources] = useState<Set<string>>(new Set());
  const [topic, setTopic] = useState('');
  const [onlyRelevant, setOnlyRelevant] = useState(true);
  const [sortBy, setSortBy] = useState<SortKey>('influence');

  const sourceCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const src of FIXED_SOURCES) counts.set(src, 0);
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
      counts.set(p.source, (counts.get(p.source) ?? 0) + 1);
    }
    return counts;
  }, [posts, sentiments, risks, topic, onlyRelevant]);

  const sourceList = useMemo(() => {
    const fixed = FIXED_SOURCES as readonly string[];
    const extras = Array.from(sourceCounts.keys())
      .filter(s => !fixed.includes(s))
      .sort();
    return [...fixed, ...extras];
  }, [sourceCounts]);

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
  }, [posts, sentiments, risks, sources, topic, onlyRelevant, sortBy]);

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
    setSentiments(new Set()); setRisks(new Set()); setSources(new Set());
    setTopic(''); setOnlyRelevant(true); setSortBy('influence');
  };

  if (!usingMock && isLoading) {
    return <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
      {t('common.loading') || 'Loading...'}
    </div>;
  }
  if (!usingMock && error) {
    return <div className="rounded-xl py-6 px-4 text-sm" style={{ background: 'rgba(239,68,68,0.1)', color: '#dc2626' }}>
      ⚠ {error instanceof Error ? error.message : 'Failed to load posts'}
    </div>;
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[220px_minmax(0,1fr)_minmax(0,1.2fr)] gap-4">
      <aside className="rounded-xl p-4 space-y-4 self-start" style={cardStyle}>
        <header className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-primary">
            {t('dashboard.sentiment.articles.filters.title')}
          </h4>
          <button type="button" onClick={reset} className="text-xs text-muted hover:text-primary">
            {t('dashboard.sentiment.articles.filters.reset')}
          </button>
        </header>

        <FilterGroup label={t('dashboard.sentiment.articles.filters.sentiment')}>
          {ALL_SENTIMENTS.map(s => (
            <FilterChip key={s} label={t(`dashboard.sentiment.articles.labels.${s}`)}
              active={sentiments.has(s)} onClick={() => setSentiments(toggle(sentiments, s))} />
          ))}
        </FilterGroup>

        <FilterGroup label={t('dashboard.sentiment.articles.filters.risk')}>
          {ALL_RISKS.map(r => (
            <FilterChip key={r} label={t(`dashboard.sentiment.articles.risk.${r}`)}
              active={risks.has(r)} onClick={() => setRisks(toggle(risks, r))} />
          ))}
        </FilterGroup>

        <FilterGroup label={t('dashboard.sentiment.articles.filters.source')}>
          <div className="flex flex-col w-full gap-0.5">
            {sourceList.map(s => {
              const labelKey = `dashboard.sentiment.articles.sourceLabels.${s}`;
              const localized = t(labelKey);
              const display = localized === labelKey ? s : localized;
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
        </FilterGroup>

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
