import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import {
  mockKpi, mockSentimentTrend, mockRiskDist, mockBriefs, mockPosts,
} from '../../../../mocks/sentiment';
import { useTodayData, useWebsearchLatest } from '../../../../hooks/useSentiment';
import type { SentimentAccount, SentimentPost } from '../../../../types/sentiment';

import { KpiCards } from '../components/KpiCards';
import { SentimentChart } from '../components/SentimentChart';
import { RiskPie } from '../components/RiskPie';
import { BriefRenderer } from '../components/BriefRenderer';
import { PostCard } from '../components/PostCard';
import { WebSearchSummary } from '../components/WebSearchSummary';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

interface Props {
  account: SentimentAccount;
  usingMock?: boolean;
}

/** Sort by influence × |sentiment| (same formula as the yuqin chat agent top_posts SQL) */
function noteworthy(p: SentimentPost): number {
  return (p.influence_potential ?? 0) * Math.abs(p.sentiment_score ?? 0);
}

export function TodayTab({ account, usingMock }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Use symbol as the fetch key -- the ticker is optional and may be an empty string
  const ticker = account.symbol || account.ticker || account.target;

  // Real mode fetches data; mock mode has enabled=false and sends no request
  const { data, isLoading, error } = useTodayData(
    usingMock ? null : account.id,
    usingMock ? null : ticker,
  );
  const {
    data: websearchLatest,
    isLoading: websearchLoading,
    error: websearchError,
  } = useWebsearchLatest(usingMock ? null : account.id);

  // mock mode: assemble a today payload from static fixtures
  const today = usingMock
    ? {
        kpi: mockKpi,
        sentiment_trend: mockSentimentTrend,
        risk_dist: mockRiskDist,
        latest_brief: mockBriefs[0],
        top_posts: [...(mockPosts as SentimentPost[])]
          .filter(p => p.is_relevant)
          .sort((a, b) => noteworthy(b) - noteworthy(a))
          .slice(0, 5),
      }
    : data;

  const websearchCard = (
    <WebSearchSummary
      data={websearchLatest}
      isLoading={websearchLoading}
      error={websearchError}
    />
  );

  if (!usingMock && isLoading) {
    return <div className="space-y-4">{websearchCard}<SkeletonCard label={t('common.loading') || 'Loading...'} /></div>;
  }
  if (!usingMock && error) {
    return (
      <div className="space-y-4">
        {websearchCard}
        <ErrorCard message={error instanceof Error ? error.message : 'Failed to load'} />
      </div>
    );
  }
  if (!today) {
    return <div className="space-y-4">{websearchCard}<SkeletonCard label="暂无数据" /></div>;
  }

  return (
    <div className="space-y-4">
      <KpiCards data={today.kpi} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-stretch">
        <div className="lg:col-span-2 flex">
          <SentimentChart data={today.sentiment_trend} />
        </div>
        <div className="flex">
          <RiskPie data={today.risk_dist} />
        </div>
      </div>

      {websearchCard}

      {/* Today's brief inline */}
      <section className="rounded-xl p-5" style={cardStyle}>
        <header className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-primary uppercase tracking-wide">
            {t('dashboard.sentiment.today.latestBrief')}
          </h3>
          <button
            type="button"
            onClick={() => navigate('/sentiment?tab=briefs')}
            className="text-xs font-semibold"
            style={{ color: 'var(--accent-primary)' }}
          >
            {t('dashboard.sentiment.today.viewFull')}
          </button>
        </header>
        {today.latest_brief ? (
          <BriefRenderer body={today.latest_brief.body} maxLines={30} />
        ) : (
          <p className="text-sm text-secondary">尚无简报。</p>
        )}
      </section>

      {/* High-risk posts Top 5 */}
      <section className="rounded-xl p-5" style={cardStyle}>
        <h3 className="text-sm font-semibold text-primary uppercase tracking-wide mb-3">
          {t('dashboard.sentiment.today.topRisk')}
        </h3>
        {today.top_posts.length === 0 ? (
          <p className="text-sm text-secondary">尚无高风险帖。</p>
        ) : (
          <div className="space-y-2">
            {today.top_posts.map((p) => (
              <button
                key={`${p.source}-${p.post_id}`}
                type="button"
                onClick={() => navigate(`/sentiment?tab=articles&post=${p.source}-${p.post_id}`)}
                className="w-full text-left"
              >
                <PostCard post={p} compact />
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function SkeletonCard({ label }: { label: string }) {
  return (
    <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
      {label}
    </div>
  );
}

function ErrorCard({ message }: { message: string }) {
  return (
    <div
      className="rounded-xl py-6 px-4 text-sm"
      style={{
        background: 'var(--danger-bg)',
        color: '#dc2626',
        border: '1px solid rgba(239,68,68,0.3)',
      }}
    >
      ⚠ {message}
    </div>
  );
}
