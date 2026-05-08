import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { SentimentPost } from '../../../../types/sentiment';
import { timeAgo } from '../../../../mocks/sentiment';
import { useSentimentPlatforms } from '../../../../hooks/useSentiment';
import { SentimentBadge, RiskBadge, SourceBadge, InfluenceBar } from './badges';

interface Props {
  post: SentimentPost;
  selected?: boolean;
  onClick?: () => void;
  compact?: boolean;
}

function countChars(s: string): number {
  const stripped = s.replace(/\s+/g, '');
  const wordCount = s.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(stripped.length, wordCount);
}

export function PostCard({ post, selected, onClick, compact }: Props) {
  const { t } = useTranslation();
  const platforms = useSentimentPlatforms().data ?? [];
  const sourceRegion = useMemo(
    () => new Map(platforms.map(p => [p.code, p.region] as const)),
    [platforms],
  );
  const topics = post.topics ?? [];
  return (
    <article
      onClick={onClick}
      className={`rounded-lg p-3 cursor-pointer transition-all ${selected ? 'shadow-glow' : 'hover:-translate-y-0.5'}`}
      style={{
        background: selected ? 'var(--bg-tertiary)' : 'var(--bg-card)',
        border: `1px solid ${selected ? 'var(--accent-primary)' : 'var(--border-color)'}`,
      }}
    >
      <header className="flex items-center gap-2 flex-wrap mb-1.5">
        <SourceBadge source={post.source} />
        <RegionTag source={post.source} sourceRegion={sourceRegion} t={t} />
        <RiskBadge level={post.risk_level} t={t} />
        {post.author && (
          <span className="text-[11px] text-muted truncate max-w-[8rem]" title={post.author}>
            @{post.author}
          </span>
        )}
        <span className="text-[11px] text-muted ml-auto">
          {post.publish_time ? timeAgo(post.publish_time) : '—'}
        </span>
      </header>

      <h4 className="font-semibold text-sm text-primary leading-snug line-clamp-2 mb-1">
        {post.title || '(无标题)'}
      </h4>

      {!compact && (
        <p className="text-xs text-secondary line-clamp-2 mb-2">
          {post.summary || ''}
        </p>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <SentimentBadge label={post.sentiment_label} score={post.sentiment_score} t={t} />
        <InfluenceBar value={post.influence_potential} />
        {post.content && (
          <span className="text-[11px] text-muted">📝 {countChars(post.content).toLocaleString()}</span>
        )}
        {post.view_count != null && (
          <span className="text-[11px] text-muted">👁 {post.view_count.toLocaleString()}</span>
        )}
      </div>

      {!compact && topics.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {topics.slice(0, 3).map(tag => (
            <span
              key={tag}
              className="text-[10px] px-1.5 py-0.5 rounded"
              style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
            >
              #{tag}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

function RegionTag({
  source, sourceRegion, t,
}: {
  source: string;
  sourceRegion: Map<string, string>;
  t: (k: string) => string;
}) {
  const region = sourceRegion.get(source);
  if (!region) return null;
  const key = `dashboard.sentiment.articles.regions.${region}`;
  const label = t(key);
  if (label === key) return null;
  return (
    <span
      className="text-[10px] px-1.5 py-0.5 rounded"
      style={{ background: 'var(--bg-tertiary)', color: 'var(--text-muted)' }}
    >
      {label}
    </span>
  );
}
