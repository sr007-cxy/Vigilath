import { useTranslation } from 'react-i18next';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function Stats() {
  const { t } = useTranslation();

  const metrics = [
    { label: t('dashboard.stats.totalPosts'), value: '48', change: '+12%' },
    { label: t('dashboard.stats.totalReplies'), value: '156', change: '+8%' },
    { label: t('dashboard.stats.avgConfidence'), value: '89%', change: '+3%' },
    { label: t('dashboard.stats.totalCost'), value: '$18.42', change: '-5%' },
  ];

  const platformStats = [
    { name: 'Reddit', color: '#FF4500', posts: 18, replies: 72, engagement: '4.2%' },
    { name: 'LinkedIn', color: '#0A66C2', posts: 12, replies: 34, engagement: '6.1%' },
    { name: 'YouTube', color: '#FF0000', posts: 8, replies: 28, engagement: '3.8%' },
    { name: 'dev.to', color: '#08A6F3', posts: 10, replies: 22, engagement: '5.4%' },
  ];

  const agentRuns = [
    { agent: t('dashboard.stats.contentAgent'), runs: 24, avgTime: '8.2s', cost: '$6.40' },
    { agent: t('dashboard.stats.publisherAgent'), runs: 48, avgTime: '2.1s', cost: '$3.20' },
    { agent: t('dashboard.stats.monitorAgent'), runs: 312, avgTime: '0.8s', cost: '$4.60' },
    { agent: t('dashboard.stats.replyAgent'), runs: 156, avgTime: '2.4s', cost: '$4.22' },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-primary">{t('dashboard.stats.title')}</h1>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {metrics.map((m) => (
          <div key={m.label} className="rounded-xl p-4" style={cardStyle}>
            <p className="text-2xl font-bold text-primary">{m.value}</p>
            <p className="text-xs text-secondary mt-1">{m.label}</p>
            <p
              className="text-xs font-medium mt-1"
              style={{ color: m.change.startsWith('+') ? 'var(--success)' : 'var(--accent-primary)' }}
            >
              {m.change} {t('dashboard.stats.vsLastWeek')}
            </p>
          </div>
        ))}
      </div>

      {/* Platform breakdown */}
      <section className="rounded-xl p-5" style={cardStyle}>
        <h2 className="text-sm font-semibold text-primary uppercase tracking-wide mb-4">
          {t('dashboard.stats.byPlatform')}
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="pb-3 font-medium">{t('dashboard.stats.platform')}</th>
                <th className="pb-3 font-medium text-right">{t('dashboard.stats.posts')}</th>
                <th className="pb-3 font-medium text-right">{t('dashboard.stats.replies')}</th>
                <th className="pb-3 font-medium text-right">{t('dashboard.stats.engagement')}</th>
              </tr>
            </thead>
            <tbody>
              {platformStats.map((p) => (
                <tr key={p.name} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                  <td className="py-3 flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: p.color }} />
                    <span className="text-primary font-medium">{p.name}</span>
                  </td>
                  <td className="py-3 text-right text-secondary">{p.posts}</td>
                  <td className="py-3 text-right text-secondary">{p.replies}</td>
                  <td className="py-3 text-right text-secondary">{p.engagement}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Agent performance */}
      <section className="rounded-xl p-5" style={cardStyle}>
        <h2 className="text-sm font-semibold text-primary uppercase tracking-wide mb-4">
          {t('dashboard.stats.agentPerformance')}
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="pb-3 font-medium">{t('dashboard.stats.agent')}</th>
                <th className="pb-3 font-medium text-right">{t('dashboard.stats.runs')}</th>
                <th className="pb-3 font-medium text-right">{t('dashboard.stats.avgTime')}</th>
                <th className="pb-3 font-medium text-right">{t('dashboard.stats.cost')}</th>
              </tr>
            </thead>
            <tbody>
              {agentRuns.map((a) => (
                <tr key={a.agent} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                  <td className="py-3 text-primary font-medium">{a.agent}</td>
                  <td className="py-3 text-right text-secondary">{a.runs}</td>
                  <td className="py-3 text-right text-secondary">{a.avgTime}</td>
                  <td className="py-3 text-right text-secondary">{a.cost}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
