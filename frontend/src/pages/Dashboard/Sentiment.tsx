// 舆情监控落地页 — 访问控制守卫 + 数据面板.
//
// 访问控制流程:
//   1. 未登录 → Navigate 到 / (首页)
//   2. 已登录但无账号 → Navigate 到 /account/brand (品牌配置页)
//   3. 已登录且有账号 → 展示数据监测面板 (4 Tabs)
//
// 数据来源:
//   VITE_USE_MOCK_SENTIMENT=1  → 走 mocks/sentiment.ts (离线 demo)
//   否则                         → useSentimentAccounts + sentimentApi (真实 API)
import { useState } from 'react';
import { useSearchParams, Link, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../contexts/AuthContext';
import { mockAccount, timeAgo } from '../../mocks/sentiment';
import { isMockMode } from '../../services/sentimentApi';
import { useSentimentAccounts, useRunNow, useRunStatus } from '../../hooks/useSentiment';
import type { SentimentAccount } from '../../types/sentiment';

import { TodayTab } from './sentiment/tabs/TodayTab';
import { ArticlesTab } from './sentiment/tabs/ArticlesTab';
import { BriefsTab } from './sentiment/tabs/BriefsTab';
import { DraftsTab } from './sentiment/tabs/DraftsTab';
import { StatusBanner, FirstRunWaiting } from './sentiment/components/StatusBanner';

type TabKey = 'today' | 'articles' | 'briefs' | 'drafts';
const TAB_KEYS: TabKey[] = ['today', 'articles', 'briefs', 'drafts'];

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

  // ── 守卫 1: 未登录 → 首页 ──
  if (!usingMock && !isLoggedIn) {
    return <Navigate to="/" replace />;
  }

  // ── 加载中 ──
  if (!usingMock && isLoading) {
    return (
      <div className="min-h-[calc(100vh-4rem)] grid-background flex items-center justify-center">
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

  // ── 守卫 2: 已登录但无配置的品牌 → 品牌设置页 ──
  if (!usingMock && !account) {
    return <Navigate to="/account/brand" replace />;
  }

  // guard 2 已经处理了无账号场景,这里 account 一定存在
  const acct = account!;

  const runStatusQuery = useRunStatus(usingMock ? null : acct.id);
  const runNowMutation = useRunNow();

  const status = demo === 'running' ? 'running'
              : demo === 'failed' ? 'failed'
              : demo === 'firstrun' ? null
              : (usingMock ? acct.last_run_status : (runStatusQuery.data?.status ?? acct.last_run_status));
  const error = demo === 'failed' ? '调用 OpenAI 时出错(429 速率限制)'
              : (usingMock ? acct.last_run_error : runStatusQuery.data?.error ?? acct.last_run_error);
  const lastRunAt = usingMock ? acct.last_run_at : (runStatusQuery.data?.last_run_at ?? acct.last_run_at);

  const tab = (params.get('tab') as TabKey) || 'today';
  const setTab = (k: TabKey) => {
    const next = new URLSearchParams(params);
    next.set('tab', k);
    next.delete('post');
    setParams(next);
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
    <div className="min-h-[calc(100vh-4rem)] grid-background">
      <div className="max-w-[1440px] mx-auto px-4 md:px-8 py-6 space-y-4">
        <header className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-bold text-primary">
              {t('dashboard.sentiment.title')}
            </h1>

            {!usingMock && accounts.length > 1 ? (
              <select
                value={selectedId ?? ''}
                onChange={(e) => setSelectedId(Number(e.target.value))}
                className="px-2.5 py-1 rounded-md text-sm font-medium"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                }}
              >
                {accounts.map(a => (
                  <option key={a.id} value={a.id}>{a.target} · {a.ticker}</option>
                ))}
              </select>
            ) : (
              <span
                className="px-2.5 py-1 rounded-md text-sm font-medium"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-color)',
                }}
              >
                <span className="font-bold text-primary">{acct.target}</span>
                {acct.ticker && <span className="ml-2 font-mono text-xs">· {acct.ticker}</span>}
              </span>
            )}
          </div>

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

        {status === 'pending' || status === 'running' ? (
          <FirstRunWaiting onRefresh={handleRefresh} />
        ) : (
          <>
            <nav
              className="flex gap-1 border-b overflow-x-auto"
              style={{ borderColor: 'var(--border-color)' }}
            >
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
            </nav>

            {tab === 'today' && <TodayTab account={acct} usingMock={usingMock} />}
            {tab === 'articles' && <ArticlesTab account={acct} usingMock={usingMock} />}
            {tab === 'briefs' && <BriefsTab account={acct} usingMock={usingMock} />}
            {tab === 'drafts' && <DraftsTab account={acct} usingMock={usingMock} />}
          </>
        )}
      </div>
    </div>
  );
}
