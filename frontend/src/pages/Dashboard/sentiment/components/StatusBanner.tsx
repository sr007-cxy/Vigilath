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
  failed:  { bg: 'rgba(239,68,68,0.10)',  fg: '#b91c1c', border: 'rgba(239,68,68,0.35)' },
};

export function StatusBanner({ status, error, onRefresh, onRetry }: Props) {
  const { t } = useTranslation();

  // failed 也静默 — 用户要求:舆情监控任务异常不在前端暴露,继续展示历史数据即可。
  if (status === 'success' || status === null || status === 'failed') return null;

  const s = STYLE[status] || STYLE.pending;
  const message =
    status === 'failed'
      ? t('dashboard.sentiment.status.failed', { error: error ?? 'unknown' })
      : t(`dashboard.sentiment.status.${status}`);

  return (
    <div
      className="rounded-xl px-4 py-3 flex items-center justify-between gap-3 text-sm"
      style={{ background: s.bg, color: s.fg, border: `1px solid ${s.border}` }}
    >
      <span>{message}</span>
      <div className="flex items-center gap-2">
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
        {status === 'failed' && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="text-xs font-semibold px-2.5 py-1 rounded-md"
            style={{ background: 'rgba(255,255,255,0.4)', color: s.fg }}
          >
            {t('dashboard.sentiment.status.retry')}
          </button>
        )}
      </div>
    </div>
  );
}

/** 全屏的"首次运行中"占位(用 status === null 时)。 */
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
