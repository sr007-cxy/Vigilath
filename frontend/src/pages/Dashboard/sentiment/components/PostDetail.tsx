import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SentimentPost } from '../../../../types/sentiment';
import { SentimentBadge, RiskBadge, SourceBadge } from './badges';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

// 中文按字符,英文按近似词(空白分割)。两者并存时取最大值,避免中英混排时低估。
function countChars(s: string): number {
  const stripped = s.replace(/\s+/g, '');
  const wordCount = s.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(stripped.length, wordCount);
}

interface Props {
  post: SentimentPost | null;
}

export function PostDetail({ post }: Props) {
  const { t } = useTranslation();
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

  // 容错:把可能为 null/undefined 的字段做默认值处理
  const content = post.content ?? '';
  const citations = post.citations ?? [];
  const topics = post.topics ?? [];
  const entities = post.entities ?? [];
  const emotions = post.emotions ?? [];
  const riskSignals = post.risk_signals ?? [];
  const influence = post.influence_potential ?? 0;

  const highlightedContent = renderHighlightedContent(content, citations);

  return (
    <article className="rounded-xl p-5 space-y-4" style={cardStyle}>
      <header className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <SourceBadge source={post.source} />
          <RiskBadge level={post.risk_level} t={t} />
          <SentimentBadge label={post.sentiment_label} score={post.sentiment_score} t={t} />
        </div>
        <h2 className="text-lg font-bold text-primary leading-snug">{post.title || '(无标题)'}</h2>
        <div className="text-xs text-muted flex items-center gap-3 flex-wrap">
          <span>{post.author || '—'}</span>
          <span>{post.publish_time ? new Date(post.publish_time).toLocaleString('zh-CN') : '—'}</span>
          {content && <span>📝 {countChars(content).toLocaleString()} 字</span>}
          {post.view_count != null && <span>👁 {post.view_count.toLocaleString()}</span>}
          {post.reply_count != null && <span>💬 {post.reply_count.toLocaleString()}</span>}
          {post.url && (
            <a
              href={post.url}
              target="_blank"
              rel="noreferrer"
              className="ml-auto"
              style={{ color: 'var(--accent-primary)' }}
            >
              {t('dashboard.sentiment.articles.detail.openOriginal')}
            </a>
          )}
        </div>
      </header>

      <section>
        <h4 className="text-xs font-semibold text-secondary uppercase tracking-wider mb-1.5">
          {t('dashboard.sentiment.articles.detail.originalText')}
        </h4>
        <p className="text-sm text-primary leading-relaxed whitespace-pre-wrap">
          {highlightedContent}
        </p>
      </section>

      <section className="pt-3" style={{ borderTop: '1px dashed var(--border-color)' }}>
        <h4 className="text-xs font-semibold text-secondary uppercase tracking-wider mb-3">
          {t('dashboard.sentiment.articles.detail.aiAnalysis')}
        </h4>

        <Field label={t('dashboard.sentiment.articles.detail.summary')}>
          <p className="text-sm text-primary">{post.summary || '—'}</p>
        </Field>

        <Field label={t('dashboard.sentiment.articles.detail.emotions')}>
          <ChipList items={emotions} />
        </Field>

        <Field label={t('dashboard.sentiment.articles.detail.topics')}>
          <ChipList items={topics.map(s => `#${s}`)} />
        </Field>

        <Field label={t('dashboard.sentiment.articles.detail.entities')}>
          <ChipList items={entities} />
        </Field>

        <div className="grid grid-cols-3 gap-3 my-2">
          <MicroField label={t('dashboard.sentiment.articles.detail.stance')} value={localizeEnum(t, 'stanceLabels', post.stance)} />
          <MicroField label={t('dashboard.sentiment.articles.detail.intent')} value={localizeEnum(t, 'intentLabels', post.intent)} />
          <MicroField label={t('dashboard.sentiment.articles.detail.factuality')} value={localizeEnum(t, 'factualityLabels', post.factuality)} />
        </div>

        <Field label={t('dashboard.sentiment.articles.detail.riskSignals')}>
          {riskSignals.length === 0 ? (
            <p className="text-xs text-muted">—</p>
          ) : (
            <ul className="text-xs space-y-1">
              {riskSignals.map((r, i) => (
                <li key={i}>
                  <span
                    className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold mr-1.5"
                    style={{ background: 'rgba(239,68,68,0.15)', color: '#b91c1c' }}
                  >
                    {r.type}
                  </span>
                  <span className="text-secondary">{r.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </Field>

        <Field label={t('dashboard.sentiment.articles.detail.influence')}>
          <div className="flex items-center gap-2">
            <div
              className="rounded-full overflow-hidden flex-1"
              style={{ height: 6, background: 'var(--bg-tertiary)' }}
            >
              <div
                style={{
                  width: `${influence * 100}%`,
                  height: '100%',
                  background: 'var(--accent-primary)',
                }}
              />
            </div>
            <span className="font-mono text-xs text-primary">
              {influence.toFixed(2)}
            </span>
          </div>
        </Field>

        {post.hidden_meaning && (
          <Field label={t('dashboard.sentiment.articles.detail.hidden')}>
            <p className="text-sm italic text-secondary">{post.hidden_meaning}</p>
          </Field>
        )}

        {citations.length > 0 && (
          <Field label={t('dashboard.sentiment.articles.detail.citations')}>
            <ul className="text-xs space-y-1">
              {citations.map((c, i) => (
                <li key={i}>
                  <span
                    className="inline-block px-1.5 py-0.5 rounded font-mono"
                    style={{ background: 'rgba(251,191,36,0.18)', color: '#854d0e' }}
                  >
                    "{c}"
                  </span>
                </li>
              ))}
            </ul>
          </Field>
        )}

        <details
          open={reasoningOpen}
          onToggle={(e) => setReasoningOpen((e.target as HTMLDetailsElement).open)}
          className="mt-3"
        >
          <summary className="text-xs text-muted cursor-pointer">
            {t('dashboard.sentiment.articles.detail.expandReasoning')}
          </summary>
          <p className="text-xs text-secondary mt-1.5 italic">{post.reasoning}</p>
        </details>
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

/** stance/intent/factuality 是 sentinel 上游的英文 enum,前端按 i18n 字典翻译;
 *  字典里没有该 key 时 i18next 会回显 key 本身,这里检测后 fallback 到 raw,
 *  防止后端新增 enum 时前端出现长串 key 路径。 */
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

/** 把 citations 在 content 里高亮(简单 split + replace)。 */
function renderHighlightedContent(content: string, citations: string[]): React.ReactNode {
  if (!citations.length) return content;

  // 用所有 citations 构造一个正则,split 出来的奇数索引就是命中片段
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
          style={{ background: 'rgba(251,191,36,0.25)', color: 'inherit', padding: '0 2px', borderRadius: 3 }}
        >
          {p}
        </mark>
      );
    }
    return p;
  });
}
