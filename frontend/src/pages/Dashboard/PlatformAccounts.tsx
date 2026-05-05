import { useTranslation } from 'react-i18next';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

interface PlatformAccount {
  id: string;
  name: string;
  handle: string;
  color: string;
  connected: boolean;
  scopes: string;
  expires?: string;
  note?: string;
}

const MOCK_ACCOUNTS: PlatformAccount[] = [
  { id: 'reddit', name: 'Reddit', handle: '@acme_dev', color: '#FF4500', connected: true, scopes: 'post, reply' },
  { id: 'linkedin', name: 'LinkedIn', handle: 'Acme Co.', color: '#0A66C2', connected: true, scopes: 'post', expires: '2026-04-23', note: 'Community Mgmt API: pending approval' },
  { id: 'youtube', name: 'YouTube', handle: 'Acme Channel', color: '#FF0000', connected: true, scopes: 'upload, reply' },
  { id: 'devto', name: 'dev.to', handle: '@acme', color: '#08A6F3', connected: true, scopes: 'API key' },
  { id: 'medium', name: 'Medium', handle: '', color: '#292929', connected: false, scopes: '' },
  { id: 'hashnode', name: 'Hashnode', handle: '', color: '#2962FF', connected: false, scopes: '' },
];

export function PlatformAccounts() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-primary">{t('dashboard.accounts.title')}</h1>
        <button className="btn-solid px-4 py-2 text-sm font-semibold rounded-lg transition-all">
          + {t('dashboard.accounts.addPlatform')}
        </button>
      </div>

      <div className="space-y-3">
        {MOCK_ACCOUNTS.map((acc) => (
          <div key={acc.id} className="rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-3" style={cardStyle}>
            {/* Platform info */}
            <div className="flex items-center gap-3 flex-1 min-w-0">
              <span
                className="w-10 h-10 rounded-lg flex items-center justify-center text-white text-sm font-bold shrink-0"
                style={{ background: acc.color }}
              >
                {acc.name.charAt(0)}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-primary">{acc.name}</p>
                {acc.handle && <p className="text-xs text-muted truncate">{acc.handle}</p>}
                {acc.note && (
                  <p className="text-xs mt-0.5" style={{ color: 'var(--warning)' }}>{acc.note}</p>
                )}
              </div>
            </div>

            {/* Status */}
            <div className="flex items-center gap-4 text-xs shrink-0">
              {acc.connected ? (
                <>
                  <span className="flex items-center gap-1.5" style={{ color: 'var(--success)' }}>
                    <span className="w-2 h-2 rounded-full" style={{ background: 'var(--success)' }} />
                    {t('dashboard.accounts.connected')}
                  </span>
                  <span className="text-muted">{acc.scopes}</span>
                  {acc.expires && (
                    <span style={{ color: 'var(--warning)' }}>
                      {t('dashboard.accounts.expires')}: {acc.expires}
                    </span>
                  )}
                </>
              ) : (
                <button
                  className="px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors"
                  style={{
                    color: 'var(--accent-primary)',
                    borderColor: 'var(--accent-primary)',
                    background: 'transparent',
                  }}
                >
                  {t('dashboard.accounts.connect')}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
