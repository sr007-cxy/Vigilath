import { useTranslation } from 'react-i18next';

interface KpiData {
  total_today: number;
  high_risk: number;
  avg_sentiment: number;
  active_sources: number;
  trend_total_pct: number;
  trend_risk_pct: number;
  trend_sentiment_pct: number;
}

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function KpiCards({ data }: { data: KpiData }) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
      <Card
        label={t('dashboard.sentiment.today.kpi.total')}
        value={data.total_today.toLocaleString()}
        delta={data.trend_total_pct}
        deltaLabel={t('dashboard.sentiment.today.kpi.vsYesterday')}
      />
      <Card
        label={t('dashboard.sentiment.today.kpi.highRisk')}
        value={data.high_risk.toString()}
        delta={data.trend_risk_pct}
        deltaLabel={t('dashboard.sentiment.today.kpi.vsYesterday')}
        valueColor={data.high_risk > 0 ? '#dc2626' : undefined}
      />
      <Card
        label={t('dashboard.sentiment.today.kpi.avgSentiment')}
        value={(data.avg_sentiment >= 0 ? '+' : '') + data.avg_sentiment.toFixed(2)}
        delta={data.trend_sentiment_pct}
        deltaLabel={t('dashboard.sentiment.today.kpi.vsYesterday')}
        valueColor={data.avg_sentiment < 0 ? '#dc2626' : '#16a34a'}
      />
      <Card
        label={t('dashboard.sentiment.today.kpi.activeSources')}
        value={data.active_sources.toString()}
      />
    </div>
  );
}

function Card({ label, value, delta, deltaLabel, valueColor }: {
  label: string;
  value: string;
  delta?: number;
  deltaLabel?: string;
  valueColor?: string;
}) {
  const arrow = delta === undefined ? '' : delta > 0 ? '↑' : delta < 0 ? '↓' : '';
  const color = delta === undefined ? undefined : delta > 0 ? '#16a34a' : delta < 0 ? '#dc2626' : 'var(--text-muted)';
  return (
    <div className="rounded-xl p-4" style={cardStyle}>
      <p className="text-xs text-secondary mb-1">{label}</p>
      <p className="text-2xl font-bold tabular-nums" style={{ color: valueColor || 'var(--text-primary)' }}>
        {value}
      </p>
      {delta !== undefined && (
        <p className="text-[11px] mt-1" style={{ color }}>
          {arrow} {Math.abs(delta)}% <span className="opacity-70">{deltaLabel}</span>
        </p>
      )}
    </div>
  );
}
