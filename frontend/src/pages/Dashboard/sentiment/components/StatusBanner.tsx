import { useTranslation } from 'react-i18next';
import type { RunStatus } from '../../../../mocks/sentiment';

interface Props {
  status: RunStatus;
  error?: string | null;
  onRefresh?: () => void;
  onRetry?: () => void;
}

const STYLE: Record<string, { bg: string; fg: string; border: string }> = {
  pending: { bg: 'rgba(251,191,36,0.10)', fg: '#b45309', border: 'rgba(251,191,36,0.35)' },
  running: { bg: 'rgba(251,191,36,0.10)', fg: '#b45309', border: 'rgba(251,191,36,0.35)' },
  failed:  { bg: 'var(--danger-bg)',  fg: '#b91c1c', border: 'var(--danger-border)' },
};

export function StatusBanner({ status, error, onRefresh, onRetry }: Props) {
  const { t } = useTranslation();

  // failed is silent too — per user request: sentiment monitoring task errors aren't exposed in the frontend; just keep showing historical data.
  // After this early return, the remaining status can only be 'pending' | 'running'.
  if (status === 'success' || status === null || status === 'failed') return null;

  // onRetry is no longer triggered (failed is now silent); the parameter is kept to avoid breaking caller signatures
  void onRetry;

  const s = STYLE[status] || STYLE.pending;
  const message = t(`dashboard.sentiment.status.${status}`);
  void error;  // Specific errors are no longer shown now that failed is silent

  return (
    <div
      className="rounded-xl px-4 py-3 flex items-center justify-between gap-3 text-sm"
      style={{ background: s.bg, color: s.fg, border: `1px solid ${s.border}` }}
    >
      <span>{message}</span>
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          className="text-xs font-semibold px-2.5 py-1 rounded-md"
          style={{ background: 'rgba(255,255,255,0.4)', color: s.fg }}
        >
          🔄 {t('dashboard.sentiment.refresh')}
        </button>
      )}
    </div>
  );
}

/** Full-screen "first run in progress" placeholder (used when status === null). */
export function FirstRunWaiting({ onRefresh }: { onRefresh?: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      className="rounded-xl py-12 px-6 text-center space-y-4"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <div className="text-4xl">⏳</div>
      <p className="text-sm text-secondary max-w-md mx-auto">
        {t('dashboard.sentiment.status.firstRun')}
      </p>
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          className="btn-solid rounded-full px-5 py-2 text-sm font-semibold"
        >
          🔄 {t('dashboard.sentiment.refresh')}
        </button>
      )}
    </div>
  );
}
