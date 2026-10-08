import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SentimentPost } from '../../../../types/sentiment';
import { useSentimentPlatforms } from '../../../../hooks/useSentiment';
import { SentimentBadge, RiskBadge, SourceBadge } from './badges';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

// Chinese counts by character, English by approximate words (whitespace split). When both are present take the max, to avoid underestimating mixed text.
function countChars(s: string): number {
  const stripped = s.replace(/\s+/g, '');
  const wordCount = s.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(stripped.length, wordCount);
}

interface Props {
  post: SentimentPost | null;
  /** Back button — clicking clears selectedKey and the right column swaps back to FilterRail */
  onBack?: () => void;
}

export function PostDetail({ post, onBack }: Props) {
  const { t } = useTranslation();
  const platforms = useSentimentPlatforms().data ?? [];
  const sourceRegion = useMemo(
    () => new Map(platforms.map(p => [p.code, p.region] as const)),
    [platforms],
  );
  const [reasoningOpen, setReasoningOpen] = useState(false);

  if (!post) {
    return (
      <div
        className="rounded-xl py-12 px-6 text-center text-secondary text-sm"
        style={cardStyle}
      >
        {t('dashboard.sentiment.articles.detail.selectPost')}
      </div>
    );
  }

  // Tolerance: default values for fields that may be null/undefined
  const content = post.content ?? '';
  const citations = post.citations ?? [];
  const topics = post.topics ?? [];
  const entities = post.entities ?? [];
  const emotions = post.emotions ?? [];
  const riskSignals = post.risk_signals ?? [];
  const influence = post.influence_potential ?? 0;

  const highlightedContent = renderHighlightedContent(content, citations);

  const region = sourceRegion.get(post.source);
  const regionKey = region && `dashboard.sentiment.articles.regions.${region}`;
  const regionLabel = regionKey ? t(regionKey) : '';
  const showRegion = regionKey && regionLabel !== regionKey;

  return (
    <article className="p-5 space-y-3">
      {/* row 0: back + source/region/risk badges + open original (right-aligned) */}
      <div className="flex items-center gap-2 flex-wrap text-[11px]">
        {onBack && (
          <button type="button" onClick={onBack}
            className="text-xs px-2 py-0.5 rounded hover:brightness-110"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
            ← {t('dashboard.sentiment.articles.detail.backToFilters')}
          </button>
        )}
        <SourceBadge source={post.source} />
        {showRegion && (
          <span className="px-1.5 py-0.5 rounded text-muted"
            style={{ background: 'var(--bg-tertiary)' }}>{regionLabel}</span>
        )}
        <RiskBadge level={post.risk_level} t={t} />
        {post.url && (
          <a href={post.url} target="_blank" rel="noreferrer"
            className="ml-auto text-xs px-2 py-0.5 rounded inline-flex items-center gap-0.5"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}>
            {t('dashboard.sentiment.articles.detail.openOriginal')}
          </a>
        )}
      </div>

      {/* row 1: title (large) */}
      <h2 className="text-xl font-bold text-primary leading-snug">
        {post.title || '(无标题)'}
      </h2>

      {/* row 2: @account · publish time · counts */}
      <div className="text-xs text-muted flex items-center gap-3 flex-wrap">
        {post.author && <span>@{post.author}</span>}
        <span>{(post.publish_time ?? post.ingested_at) ? new Date((post.publish_time ?? post.ingested_at)!).toLocaleString('zh-CN') : '—'}</span>
        {content && <span>📝 {countChars(content).toLocaleString()}</span>}
        {post.view_count != null && <span>👁 {post.view_count.toLocaleString()}</span>}
        {post.reply_count != null && <span>💬 {post.reply_count.toLocaleString()}</span>}
      </div>

      {/* row 3: summary + full body */}
      {post.summary && (
        <p className="text-sm text-secondary italic leading-relaxed border-l-2 pl-3"
          style={{ borderColor: 'var(--border-color)' }}>
          {post.summary}
        </p>
      )}
      {content && (
        <p className="text-sm text-primary leading-relaxed whitespace-pre-wrap">
          {highlightedContent}
        </p>
      )}

      {/* Bottom metadata — risk / sentiment / stance / influence / topics / entities / reasoning */}
      <section className="pt-3 space-y-3" style={{ borderTop: '1px solid var(--border-color)' }}>
        <h4 className="text-[11px] font-semibold text-muted uppercase tracking-wider">
          {t('dashboard.sentiment.articles.detail.aiAnalysis')}
        </h4>

        {/* Sentiment chip (risk is already in the header) */}
        <div className="flex items-center gap-2 flex-wrap">
          <SentimentBadge label={post.sentiment_label} score={post.sentiment_score} t={t} />
        </div>

        {/* Stance / intent / factuality */}
        <div className="grid grid-cols-3 gap-3">
          <MicroField label={t('dashboard.sentiment.articles.detail.stance')}
            value={localizeEnum(t, 'stanceLabels', post.stance)} />
          <MicroField label={t('dashboard.sentiment.articles.detail.intent')}
            value={localizeEnum(t, 'intentLabels', post.intent)} />
          <MicroField label={t('dashboard.sentiment.articles.detail.factuality')}
            value={localizeEnum(t, 'factualityLabels', post.factuality)} />
        </div>

        {/* Influence */}
        <Field label={t('dashboard.sentiment.articles.detail.influence')}>
          <div className="flex items-center gap-2">
            <div className="rounded-full overflow-hidden flex-1"
              style={{ height: 6, background: 'var(--bg-tertiary)' }}>
              <div style={{
                width: `${influence * 100}%`,
                height: '100%',
                background: 'var(--accent-primary)',
              }} />
            </div>
            <span className="font-mono text-xs text-primary">{influence.toFixed(2)}</span>
          </div>
        </Field>

        {/* Topics / entities / emotions */}
        {topics.length > 0 && (
          <Field label={t('dashboard.sentiment.articles.detail.topics')}>
            <ChipList items={topics.map(s => `#${s}`)} />
          </Field>
        )}
        {entities.length > 0 && (
          <Field label={t('dashboard.sentiment.articles.detail.entities')}>
            <ChipList items={entities} />
          </Field>
        )}
        {emotions.length > 0 && (
          <Field label={t('dashboard.sentiment.articles.detail.emotions')}>
            <ChipList items={emotions} />
          </Field>
        )}

        {/* Risk signals */}
        {riskSignals.length > 0 && (
          <Field label={t('dashboard.sentiment.articles.detail.riskSignals')}>
            <ul className="text-xs space-y-1">
              {riskSignals.map((r, i) => (
                <li key={i}>
                  <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold mr-1.5"
                    style={{ background: 'var(--danger-bg)', color: '#b91c1c' }}>
                    {r.type}
                  </span>
                  <span className="text-secondary">{r.reason}</span>
                </li>
              ))}
            </ul>
          </Field>
        )}

        {/* Implied meaning */}
        {post.hidden_meaning && (
          <Field label={t('dashboard.sentiment.articles.detail.hidden')}>
            <p className="text-sm italic text-secondary">{post.hidden_meaning}</p>
          </Field>
        )}

        {/* AI citations */}
        {citations.length > 0 && (
          <Field label={t('dashboard.sentiment.articles.detail.citations')}>
            <ul className="text-xs space-y-1">
              {citations.map((c, i) => (
                <li key={i}>
                  <span className="inline-block px-1.5 py-0.5 rounded font-mono"
                    style={{ background: 'rgba(168,85,247,0.15)', color: '#6b21a8' }}>
                    "{c}"
                  </span>
                </li>
              ))}
            </ul>
          </Field>
        )}

        {/* AI reasoning (collapsed by default) */}
        {post.reasoning && (
          <details open={reasoningOpen}
            onToggle={(e) => setReasoningOpen((e.target as HTMLDetailsElement).open)}>
            <summary className="text-xs text-muted cursor-pointer">
              {t('dashboard.sentiment.articles.detail.expandReasoning')}
            </summary>
            <p className="text-xs text-secondary mt-1.5 italic">{post.reasoning}</p>
          </details>
        )}
      </section>
    </article>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="my-2">
      <p className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-1">
        {label}
      </p>
      {children}
    </div>
  );
}

function MicroField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] text-muted uppercase tracking-wider">{label}</p>
      <p className="text-xs font-medium text-primary mt-0.5">{value || '—'}</p>
    </div>
  );
}

function ChipList({ items }: { items: string[] }) {
  if (!items?.length) return <span className="text-xs text-muted">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((it, i) => (
        <span
          key={i}
          className="text-[11px] px-1.5 py-0.5 rounded"
          style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
        >
          {it}
        </span>
      ))}
    </div>
  );
}

/** stance/intent/factuality are English enums from upstream sentinel, translated on the frontend via the i18n dictionary;
 *  when the dictionary lacks the key, i18next echoes the key itself, so we detect that and fall back to raw,
 *  preventing long key paths from appearing in the frontend when the backend adds new enums. */
function localizeEnum(
  t: (k: string) => string,
  group: 'stanceLabels' | 'intentLabels' | 'factualityLabels',
  raw: string | null | undefined,
): string {
  if (!raw) return '—';
  const key = `dashboard.sentiment.articles.detail.${group}.${raw}`;
  const translated = t(key);
  return translated === key ? raw : translated;
}

/** Highlight citations in content (simple split + replace). */
function renderHighlightedContent(content: string, citations: string[]): React.ReactNode {
  if (!citations.length) return content;

  // Build one regex from all citations; the odd indexes after split are the matched fragments
  const escaped = citations
    .filter(c => c && c.length >= 2)
    .map(c => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!escaped.length) return content;

  const re = new RegExp(`(${escaped.join('|')})`, 'g');
  const parts = content.split(re);
  return parts.map((p, i) => {
    if (i % 2 === 1) {
      return (
        <mark
          key={i}
          style={{ background: 'rgba(168,85,247,0.22)', color: 'inherit', padding: '0 2px', borderRadius: 3 }}
        >
          {p}
        </mark>
      );
    }
    return p;
  });
}
