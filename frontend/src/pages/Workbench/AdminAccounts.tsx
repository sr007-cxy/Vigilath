import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi, type AdminAccount } from '../../services/aiTelemetryApi';

export function AdminAccounts() {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    aiTelemetryApi.adminListAccounts(token)
      .then(setAccounts)
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token]);

  return (
    <div className="max-w-[1100px] mx-auto p-6">
      <h1 className="text-xl font-semibold text-primary mb-4">
        {t('workbench.adminAccounts.heading')}
      </h1>
      <p className="text-xs text-muted mb-4">
        {t('workbench.adminAccounts.subtitle')}
      </p>
      {loading ? (
        <div className="text-xs text-muted text-center py-10">
          {t('common.loading')}
        </div>
      ) : err ? (
        <div className="text-xs text-red-500">{err}</div>
      ) : (
        <div className="rounded-lg overflow-hidden" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted">
                <th className="text-left px-3 py-2">{t('workbench.adminAccounts.colId')}</th>
                <th className="text-left px-3 py-2">{t('workbench.adminAccounts.colEmail')}</th>
                <th className="text-left px-3 py-2">{t('workbench.adminAccounts.colName')}</th>
                <th className="text-right px-3 py-2">{t('workbench.adminAccounts.colTopicCount')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {accounts.map(a => (
                <tr key={a.id} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                  <td className="px-3 py-2 text-muted tabular-nums">{a.id}</td>
                  <td className="px-3 py-2 text-primary">
                    {a.email}
                    {a.is_admin && (
                      <span className="ml-1 text-[10px] text-amber-500">
                        {t('workbench.adminAccounts.adminBadge')}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-secondary">{a.name || '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.topic_count}</td>
                  <td className="px-3 py-2">
                    <Link to={`/workbench/accounts/${a.id}/topics`} className="text-accent hover:underline">
                      {t('workbench.adminAccounts.configureTopics')} →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
