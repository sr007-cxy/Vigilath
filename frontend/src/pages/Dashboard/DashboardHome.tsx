import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function DashboardHome() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-primary">
        {t('dashboard.home.title')}
      </h1>

      {/* Needs attention */}
      <section
        className="rounded-xl p-5 space-y-3"
        style={cardStyle}
      >
        <h2 className="text-sm font-semibold text-primary uppercase tracking-wide">
          {t('dashboard.home.attention')}
        </h2>
        <div className="space-y-2">
          <AttentionRow
            label={t('dashboard.home.attentionReplies', { count: 7 })}
            to="/dashboard/inbox"
            cta={t('dashboard.home.review')}
          />
          <AttentionRow
            label={t('dashboard.home.attentionDrafts', { count: 2 })}
            to="/dashboard/posts"
            cta={t('dashboard.home.review')}
          />
          <AttentionRow
            label={t('dashboard.home.attentionToken')}
            to="/dashboard/accounts"
            cta={t('dashboard.home.reconnect')}
            warn
          />
        </div>
      </section>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label={t('dashboard.home.postsPublished')} value="12" />
        <StatCard label={t('dashboard.home.repliesSent')} value="38" />
        <StatCard label={t('dashboard.home.approvalRate')} value="92%" />
        <StatCard label={t('dashboard.home.agentCost')} value="$4.12" />
      </div>

      {/* Recent activity */}
      <section
        className="rounded-xl p-5 space-y-3"
        style={cardStyle}
      >
        <h2 className="text-sm font-semibold text-primary uppercase tracking-wide">
          {t('dashboard.home.recentActivity')}
        </h2>
        <div className="space-y-2">
          <ActivityRow time="14:22" text={t('dashboard.home.activityReply')} platform="Reddit" color="#FF4500" />
          <ActivityRow time="14:10" text={t('dashboard.home.activityPost')} platform="YouTube" color="#FF0000" />
          <ActivityRow time="13:45" text={t('dashboard.home.activityDraft')} platform="" color="" />
          <ActivityRow time="12:30" text={t('dashboard.home.activityPublish')} platform="dev.to" color="#08A6F3" />
        </div>
      </section>
    </div>
  );
}

function AttentionRow({
  label,
  to,
  cta,
  warn,
}: {
  label: string;
  to: string;
  cta: string;
  warn?: boolean;
}) {
  return (
    <div className="flex items-center justify-between py-2 px-3 rounded-lg" style={{ background: 'var(--bg-surface-hover)' }}>
      <span className="text-sm text-secondary flex items-center gap-2">
        {warn && (
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: 'var(--warning)' }} />
        )}
        {label}
      </span>
      <Link
        to={to}
        className="text-xs font-semibold px-3 py-1 rounded-md transition-colors"
        style={{ color: 'var(--accent-primary)', background: 'var(--bg-tertiary)' }}
      >
        {cta} &rarr;
      </Link>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="rounded-xl p-4 text-center"
      style={cardStyle}
    >
      <p className="text-2xl font-bold text-primary">{value}</p>
      <p className="text-xs text-secondary mt-1">{label}</p>
    </div>
  );
}

function ActivityRow({
  time,
  text,
  platform,
  color,
}: {
  time: string;
  text: string;
  platform: string;
  color: string;
}) {
  return (
    <div className="flex items-center gap-3 py-2">
      <span className="text-xs text-muted w-12 shrink-0">{time}</span>
      <span className="text-sm text-secondary flex-1">{text}</span>
      {platform && (
        <span
          className="text-xs font-medium px-2 py-0.5 rounded-full"
          style={{ color, background: `${color}15` }}
        >
          {platform}
        </span>
      )}
    </div>
  );
}
