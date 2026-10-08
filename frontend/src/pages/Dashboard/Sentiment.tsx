// Sentiment monitoring landing page — access-control guard + data panel.
//
// Access-control flow:
//   1. Not logged in -> Navigate to / (home)
//   2. Logged in but no account -> Navigate to /account/brand (brand config page)
//   3. Logged in with an account -> show the data monitoring panel (4 tabs)
//
// Data source:
//   VITE_USE_MOCK_SENTIMENT=1  -> mocks/sentiment.ts (offline demo)
//   otherwise                    -> useSentimentAccounts + sentimentApi (real API)
import { useState } from 'react';
import { useSearchParams, Link, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../contexts/AuthContext';
import { mockAccount, timeAgo } from '../../mocks/sentiment';
import { isMockMode } from '../../services/sentimentApi';
import { useSentimentAccounts, useRunNow, useRunStatus } from '../../hooks/useSentiment';
import type { SentimentAccount } from '../../types/sentiment';
import { PageHead } from '../../components/PageHead';

import { TodayTab } from './sentiment/tabs/TodayTab';
import { ArticlesTab } from './sentiment/tabs/ArticlesTab';
import { BriefsTab } from './sentiment/tabs/BriefsTab';
import { StatusBanner, FirstRunWaiting } from './sentiment/components/StatusBanner';
import { AccountSidebar } from './sentiment/components/AccountSidebar';
import { HotTopicsPanel } from './sentiment/components/HotTopicsPanel';
import { QuickCheckPanel } from './sentiment/components/QuickCheckPanel';

type TabKey = 'today' | 'articles' | 'briefs';
const TAB_KEYS: TabKey[] = ['today', 'articles', 'briefs'];

export function Sentiment() {
  const { t } = useTranslation();
  const { isLoggedIn } = useAuth();
  const [params, setParams] = useSearchParams();
  const demo = params.get('demo');
  const usingMock = isMockMode() || demo !== null;

  const { data: accounts = [], isLoading, refetch: refetchAccounts } = useSentimentAccounts();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [mockCreated, _setMockCreated] = useState(false);

  const account: SentimentAccount | null = usingMock
    ? (demo === 'onboarding' && !mockCreated ? null : (mockAccount as SentimentAccount))
    : (accounts.find(a => a.id === (selectedId ?? accounts[0]?.id)) ?? null);

  // Hooks must be called before any early return — React Rules of Hooks
  const runStatusQuery = useRunStatus(usingMock ? null : (account?.id ?? null));
  const runNowMutation = useRunNow();

  // ── Guard 1: not logged in -> home ──
  if (!usingMock && !isLoggedIn) {
    return <Navigate to="/" replace />;
  }

  // 2026-08-25 — there used to be a guard here that "bounces back to home without a real paid subscription".
  // Since sentiment monitoring switched to **pay-per-use**, tiers no longer gate access (the backend pro hard gate was retired as well);
  // whether the balance suffices is checked by the backend when creating accounts/batch runs, and the frontend no longer blocks.

  // ── Loading ──
  if (!usingMock && isLoading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center space-y-3">
          <div
            className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 mx-auto"
            style={{ borderColor: 'var(--accent-primary)' }}
          />
          <p className="text-sm text-secondary">{t('common.loading') || 'Loading...'}</p>
        </div>
      </div>
    );
  }

  // ── No brand configured yet: offer a one-off quick check instead of turning people away ──
  // "Taking a look" shouldn't require creating an account first -- this used to Navigate straight to the brand settings page,
  // so users wanting a quick look for negative coverage were first asked to fill a full config, and the funnel broke here.
  if (!usingMock && !account) {
    return (
      <div>
        <div className="space-y-4">
          <PageHead titleKey="pageMeta.sentiment.title" descriptionKey="pageMeta.sentiment.description" />
          <QuickCheckPanel />
          <p className="text-sm text-secondary text-center">
            想要每天自动盯着 + 负面预警 + 历史回溯?
            <Link to="/account/brand" className="underline ml-1"
                  style={{ color: 'var(--accent-primary)' }}>
              开通日常监测
            </Link>
          </p>
        </div>
      </div>
    );
  }

  // guard 2 already handled the no-account case, so account must exist here
  const acct = account!;

  const status = demo === 'running' ? 'running'
              : demo === 'failed' ? 'failed'
              : demo === 'firstrun' ? null
              : (usingMock ? acct.last_run_status : (runStatusQuery.data?.status ?? acct.last_run_status));
  const error = demo === 'failed' ? '调用模型接口出错(429 速率限制)'
              : (usingMock ? acct.last_run_error : runStatusQuery.data?.error ?? acct.last_run_error);
  const lastRunAt = usingMock ? acct.last_run_at : (runStatusQuery.data?.last_run_at ?? acct.last_run_at);

  const tab = (params.get('tab') as TabKey) || 'today';
  const view: 'account' | 'newsnow' = params.get('view') === 'newsnow' ? 'newsnow' : 'account';
  const setTab = (k: TabKey) => {
    const next = new URLSearchParams(params);
    next.set('tab', k);
    next.delete('post');
    next.delete('view');
    setParams(next);
  };
  const selectNewsnow = () => {
    const next = new URLSearchParams(params);
    next.set('view', 'newsnow');
    next.delete('post');
    setParams(next);
  };
  const handleSelectAccount = (id: number) => {
    setSelectedId(id);
    if (params.get('view')) {
      const next = new URLSearchParams(params);
      next.delete('view');
      setParams(next);
    }
  };

  const ago = lastRunAt ? timeAgo(lastRunAt) : '';

  const handleRefresh = () => {
    runStatusQuery.refetch();
    refetchAccounts();
  };
  const handleRetry = () => {
    if (usingMock) handleRefresh();
    else if (acct.id) {
      runNowMutation.mutate(acct.id, {
        onSuccess: () => runStatusQuery.refetch(),
      });
    }
  };

  return (
    <div>
      <PageHead titleKey="pageMeta.sentiment.title" descriptionKey="pageMeta.sentiment.description" />
      <div>
        <header className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <h1 className="text-xl font-semibold text-primary">
            {t('dashboard.sentiment.title')}
          </h1>

          <div className="flex items-center gap-3">
            {status === 'success' && (
              <span className="text-xs text-muted">
                {t('dashboard.sentiment.status.success')} ·{' '}
                {t('dashboard.sentiment.lastUpdate', { ago })}
              </span>
            )}
            <button
              type="button"
              onClick={handleRefresh}
              className="text-xs font-semibold rounded-md px-2.5 py-1.5"
              style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
            >
              {t('dashboard.sentiment.refresh')}
            </button>
            <Link
              to="/account/brand"
              className="text-xs font-semibold rounded-md px-3 py-1.5"
              style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}
            >
              {t('dashboard.sentiment.settingsLink')}
            </Link>
          </div>
        </header>

        {/* Main 2-column layout: account tree on the left + tab area on the right. Single column stacked on mobile. */}
        <div className="grid grid-cols-1 xl:grid-cols-[220px_minmax(0,1fr)] gap-4">
          <AccountSidebar
            accounts={usingMock ? [acct] : accounts}
            selectedId={acct.id}
            onSelect={handleSelectAccount}
            view={view}
            onSelectNewsnow={selectNewsnow}
          />

          <div className="space-y-4 min-w-0">
            {view === 'newsnow' ? (
              <HotTopicsPanel usingMock={usingMock} account={acct} />
            ) : (
              <>
            <StatusBanner
              status={status as never}
              error={error}
              onRefresh={handleRefresh}
              onRetry={handleRetry}
            />

            {status === null && (
              <div
                className="rounded-xl px-4 py-3 flex items-center justify-between gap-3 text-sm"
                style={{
                  background: 'rgba(59,130,246,0.10)',
                  color: '#1d4ed8',
                  border: '1px solid rgba(59,130,246,0.35)',
                }}
              >
                <span>{t('dashboard.sentiment.status.neverRun')}</span>
                <button
                  type="button"
                  onClick={handleRetry}
                  disabled={runNowMutation.isPending}
                  className="text-xs font-semibold px-2.5 py-1 rounded-md disabled:opacity-50"
                  style={{ background: 'rgba(255,255,255,0.6)', color: '#1d4ed8' }}
                >
                  {runNowMutation.isPending
                    ? t('dashboard.sentiment.status.queueing')
                    : t('dashboard.sentiment.status.runNow')}
                </button>
              </div>
            )}

            {/* Full-screen placeholder for the first run — used only when there's no historical data */}
            {(status === 'pending' || status === 'running') && !lastRunAt ? (
              <FirstRunWaiting onRefresh={handleRefresh} />
            ) : (
              <>
                <nav
                  className="flex justify-between items-end gap-3 border-b overflow-x-auto flex-wrap"
                  style={{ borderColor: 'var(--border-color)' }}
                >
                  <div className="flex gap-1">
                    {TAB_KEYS.map(k => (
                      <button
                        key={k}
                        type="button"
                        onClick={() => setTab(k)}
                        className="px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors"
                        style={tab === k
                          ? { borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' }
                          : { borderColor: 'transparent', color: 'var(--text-secondary)' }}
                      >
                        {t(`dashboard.sentiment.tabs.${k}`)}
                      </button>
                    ))}
                  </div>
                  {/* Child tabs render their toolbar here via a portal — e.g. the articles tab's time filter chips */}
                  <div id="sentiment-tab-toolbar" className="flex items-center gap-2 pb-2" />
                </nav>

                {tab === 'today' && <TodayTab account={acct} usingMock={usingMock} />}
                {tab === 'articles' && <ArticlesTab account={acct} usingMock={usingMock} />}
                {tab === 'briefs' && <BriefsTab account={acct} usingMock={usingMock} />}
              </>
            )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
