import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { SentimentPost } from '../../../../types/sentiment';
import { useSentimentPlatforms } from '../../../../hooks/useSentiment';
import { SentimentBadge, RiskBadge, SourceBadge, InfluenceBar } from './badges';

// 显示绝对日期 — 去掉 "X 天前" 相对时间。
// 今年:'5/8 14:30';跨年:'2025-12-31 14:30'。
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return sameYear
    ? `${m}/${day} ${hh}:${mm}`
    : `${d.getFullYear()}-${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')} ${hh}:${mm}`;
}

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
      className="px-3 py-2.5 cursor-pointer transition-colors hover:bg-tertiary/50"
      style={{
        background: selected ? 'var(--bg-tertiary)' : 'transparent',
        borderBottom: '1px solid var(--border-color)',
        borderLeft: selected ? '2px solid var(--accent-primary)' : '2px solid transparent',
      }}
    >
      {/* 头:风险 + 平台 + 区域 + 作者 ····· 日期 */}
      <header className="flex items-center gap-1.5 flex-wrap mb-1 text-[11px] text-muted">
        <RiskBadge level={post.risk_level} t={t} />
        <SourceBadge source={post.source} />
        <RegionTag source={post.source} sourceRegion={sourceRegion} t={t} />
        {post.author && (
          <span className="truncate max-w-[8rem]" title={post.author}>@{post.author}</span>
        )}
        <span className="ml-auto">{fmtDate(post.publish_time)}</span>
      </header>

      {/* 标题(粗体黑字) */}
      <h4 className="font-semibold text-sm text-primary leading-snug line-clamp-2 mb-0.5">
        {post.title || '(无标题)'}
      </h4>

      {/* 摘要 */}
      {!compact && post.summary && (
        <p className="text-xs text-secondary line-clamp-2 mb-1.5">
          {post.summary}
        </p>
      )}

      {/* 尾:情感 + 影响力 + 计数 + 话题 chip,紧凑一行 */}
      <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted">
        <SentimentBadge label={post.sentiment_label} score={post.sentiment_score} t={t} />
        <InfluenceBar value={post.influence_potential} />
        {post.content && <span>📝 {countChars(post.content).toLocaleString()}</span>}
        {post.view_count != null && <span>👁 {post.view_count.toLocaleString()}</span>}
        {!compact && topics.slice(0, 2).map(tag => (
          <span key={tag} className="px-1 rounded"
            style={{ background: 'rgba(251,191,36,0.15)', color: '#854d0e' }}>
            #{tag}
          </span>
        ))}
      </div>
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
