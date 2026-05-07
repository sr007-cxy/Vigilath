// 舆情主页 — 顶部账号选择器 + 状态横幅 + 4 tabs.
// 数据来源:
//   VITE_USE_MOCK_SENTIMENT=1  → 走 mocks/sentiment.ts(离线 demo)
//   否则                         → useSentimentAccounts + sentimentApi (真实 API)
import { useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { mockAccount, timeAgo } from '../../mocks/sentiment';
import { isMockMode } from '../../services/sentimentApi';
import { useSentimentAccounts, useRunNow, useRunStatus } from '../../hooks/useSentiment';
import type { SentimentAccount } from '../../types/sentiment';

import { OnboardingWizard } from './sentiment/OnboardingWizard';
import { TodayTab } from './sentiment/tabs/TodayTab';
import { ArticlesTab } from './sentiment/tabs/ArticlesTab';
import { BriefsTab } from './sentiment/tabs/BriefsTab';
import { DraftsTab } from './sentiment/tabs/DraftsTab';
import { StatusBanner, FirstRunWaiting } from './sentiment/components/StatusBanner';

type TabKey = 'today' | 'articles' | 'briefs' | 'drafts';
const TAB_KEYS: TabKey[] = ['today', 'articles', 'briefs', 'drafts'];

export function Sentiment() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const demo = params.get('demo'); // demo 模式覆盖,优先于 isMockMode
  const usingMock = isMockMode() || demo !== null;

  // ── 真实模式:拉账号列表 ──
  const { data: accounts = [], isLoading, refetch: refetchAccounts } = useSentimentAccounts();

  // demo=onboarding 强制 onboarding;否则 mock 用 mockAccount;否则用 accounts[0]
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // mock 模式下,onboarding 完成后切换到主视图(用 state flag 模拟创建)
  const [mockCreated, setMockCreated] = useState(false);

  // 决定当前显示的账号 — 使用派生值,避免 useEffect setState 级联渲染
  const account: SentimentAccount | null = usingMock
    ? (demo === 'onboarding' && !mockCreated ? null : (mockAccount as SentimentAccount))
    : (accounts.find(a => a.id === (selectedId ?? accounts[0]?.id)) ?? null);

  // run-status 仅真实模式查询
  const runStatusQuery = useRunStatus(usingMock ? null : (account?.id ?? null));
  const runNowMutation = useRunNow();

  // ── 加载中 ──
  if (!usingMock && isLoading) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold text-primary">{t('dashboard.sentiment.title')}</h1>
        <div className="rounded-xl py-12 text-center text-secondary text-sm"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          {t('common.loading') || 'Loading...'}
        </div>
      </div>
    );
  }

  // ── 无账号 → onboarding ──
  if (!account) {
    return (
      <OnboardingWizard
        onSubmit={() => {
          if (usingMock) setMockCreated(true);
          else refetchAccounts();
        }}
      />
    );
  }

  // ── 状态机覆盖(demo 模式 / 真实模式) ──
  const status = demo === 'running' ? 'running'
              : demo === 'failed' ? 'failed'
              : demo === 'firstrun' ? null
              : (usingMock ? account.last_run_status : (runStatusQuery.data?.status ?? account.last_run_status));
  const error = demo === 'failed' ? '调用 OpenAI 时出错(429 速率限制)'
              : (usingMock ? account.last_run_error : runStatusQuery.data?.error ?? account.last_run_error);
  const lastRunAt = usingMock ? account.last_run_at : (runStatusQuery.data?.last_run_at ?? account.last_run_at);

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
    else if (account.id) {
      runNowMutation.mutate(account.id, {
        onSuccess: () => runStatusQuery.refetch(),
      });
    }
  };

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-2xl font-bold text-primary">
            {t('dashboard.sentiment.title')}
          </h1>

          {/* Symbol selector(多账号时切换;单账号显示 chip) */}
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
              <span className="font-bold text-primary">{account.target}</span>
              {account.ticker && <span className="ml-2 font-mono text-xs">· {account.ticker}</span>}
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
            🔄 {t('dashboard.sentiment.refresh')}
          </button>
          <Link
            to="/sentiment/settings"
            className="text-xs font-semibold rounded-md px-3 py-1.5"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}
          >
            ⚙ {t('dashboard.sentiment.settingsLink')}
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
              : `🚀 ${t('dashboard.sentiment.status.runNow')}`}
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

          {tab === 'today' && <TodayTab account={account} usingMock={usingMock} />}
          {tab === 'articles' && <ArticlesTab account={account} usingMock={usingMock} />}
          {tab === 'briefs' && <BriefsTab account={account} usingMock={usingMock} />}
          {tab === 'drafts' && <DraftsTab account={account} usingMock={usingMock} />}
        </>
      )}
    </div>
  );
}
