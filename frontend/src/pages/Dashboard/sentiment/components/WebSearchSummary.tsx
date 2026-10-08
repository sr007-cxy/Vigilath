import { useTranslation } from 'react-i18next';

import type { SentimentWebSearchLatestResponse, SentimentWebSearchRisk } from '../../../../types/sentiment';
import { RiskBadge, SentimentBadge } from './badges';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

interface Props {
  data?: SentimentWebSearchLatestResponse | null;
  isLoading?: boolean;
  error?: unknown;
}

export function WebSearchSummary({ data, isLoading, error }: Props) {
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <section className="rounded-xl p-5" style={cardStyle}>
        <p className="text-sm text-secondary">{t('common.loading') || 'Loading...'}</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="rounded-xl p-5" style={cardStyle}>
        <Header />
        <p className="text-sm" style={{ color: '#dc2626' }}>
          ⚠ {error instanceof Error ? error.message : String(error)}
        </p>
      </section>
    );
  }

  if (!data?.available) {
    return (
      <section className="rounded-xl p-5" style={cardStyle}>
        <Header />
        <p className="text-sm text-secondary">
          {data?.error || t('dashboard.sentiment.today.webSearch.empty')}
        </p>
      </section>
    );
  }

  const generated = (data.generated_at || data.last_run_at || '').slice(0, 16).replace('T', ' ');
  const sources = (data.sources || []).slice(0, 5);

  return (
    <section className="rounded-xl p-5 space-y-4" style={cardStyle}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <Header />
        <div className="flex flex-wrap items-center gap-2">
          <RiskBadge level={data.risk_level} t={t} />
          <SentimentBadge label={data.sentiment_label} score={data.sentiment_score} t={t} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>{data.provider || 'yuanbao'}</span>
        {generated && <span>· {generated}</span>}
        {typeof data.valid_results === 'number' && typeof data.query_count === 'number' && (
          <span>· {data.valid_results}/{data.query_count}</span>
        )}
      </div>

      {data.summary && (
        <p className="text-sm text-primary leading-relaxed m-0">{data.summary}</p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <BulletBlock title={t('dashboard.sentiment.today.webSearch.positive')} items={data.positive_points} />
        <BulletBlock title={t('dashboard.sentiment.today.webSearch.negative')} items={data.negative_points} />
        <RiskBlock title={t('dashboard.sentiment.today.webSearch.risks')} items={data.risks} />
        <BulletBlock title={t('dashboard.sentiment.today.webSearch.suggestions')} items={data.suggestions} />
      </div>

      {sources.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-primary uppercase tracking-wide mb-2">
            {t('dashboard.sentiment.today.webSearch.sources')}
          </h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {sources.map((s, i) => (
              <a
                key={`${s.url || s.domain || s.title}-${i}`}
                href={s.url || undefined}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg p-3 text-left hover:opacity-90 transition-opacity"
                style={{ background: 'var(--bg-tertiary)', color: 'inherit' }}
              >
                <div className="text-sm font-semibold text-primary line-clamp-1">
                  {s.title || s.domain || s.url}
                </div>
                {s.domain && <div className="text-[11px] text-muted mt-0.5">{s.domain}</div>}
                {s.snippet && <p className="text-xs text-secondary leading-relaxed mt-1 m-0 line-clamp-2">{s.snippet}</p>}
              </a>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function Header() {
  const { t } = useTranslation();
  return (
    <div>
      <h3 className="text-sm font-semibold text-primary uppercase tracking-wide m-0">
        {t('dashboard.sentiment.today.webSearch.title')}
      </h3>
      <p className="text-xs text-secondary mt-1 m-0">
        {t('dashboard.sentiment.today.webSearch.subtitle')}
      </p>
    </div>
  );
}

function BulletBlock({ title, items }: { title: string; items?: string[] }) {
  const list = (items || []).filter(Boolean).slice(0, 5);
  if (list.length === 0) return null;
  return (
    <div className="rounded-lg p-3" style={{ background: 'var(--bg-tertiary)' }}>
      <h4 className="text-xs font-semibold text-primary mb-2 uppercase tracking-wide">{title}</h4>
      <ul className="space-y-1.5 m-0 pl-4 text-sm text-secondary leading-relaxed">
        {list.map((item, i) => <li key={i}>{item}</li>)}
      </ul>
    </div>
  );
}

function RiskBlock({ title, items }: { title: string; items?: SentimentWebSearchRisk[] }) {
  const list = (items || []).filter(Boolean).slice(0, 5);
  if (list.length === 0) return null;
  return (
    <div className="rounded-lg p-3" style={{ background: 'var(--bg-tertiary)' }}>
      <h4 className="text-xs font-semibold text-primary mb-2 uppercase tracking-wide">{title}</h4>
      <div className="space-y-2">
        {list.map((item, i) => (
          <div key={i} className="text-sm text-secondary leading-relaxed">
            <div className="font-semibold text-primary">{item.title}</div>
            {item.detail && <div>{item.detail}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
