// Overview tab: engine health sentinel + task queue (per engine).
// Tables use TableShell + StickyHead, health chips use Badge, the self-heal button uses Button -- no more hand-written styles.
import { useTranslation } from 'react-i18next';
import type { WorkerQueueStats, EngineHealth } from '../../../services/aiTelemetryApi';
import { fmtTime } from '../../../utils/datetime';
import { engineLabel } from '../../../constants/engines';
import {
  TableShell, StickyHead, EmptyRow, Badge, Button, SectionCard,
} from '../../../components/ui';

// Only these engines scraped via DOM selectors support "self-heal" (fixing selectors after site redesigns via config);
// deepseek uses pure HTTP and doubao the official API, with no selectors to heal -> no self-heal button.
// yuanbao switched back to an account-pool browser engine on 2026-09-21 and **does have selectors**; it's not listed here only because
// the backend heal-probe hasn't produced a baseline for it yet (only qwen has been verified so far); add it once verified.
// Don't misread the order as "Yuanbao has no selectors" -- its selectors are in
// services/browser-service/app/engines/yuanbao_browser.py and dist-worker/yuanbao_collector.py.
const HEAL_SELECTOR_ENGINES = ['qwen'];

export function OverviewTab({
  health, stats, engines, doHeal,
}: {
  health: { checked_at: string | null; engines: EngineHealth[] };
  stats: WorkerQueueStats | null;
  engines: string[];
  doHeal: (engine: string) => void;
}) {
  const { t } = useTranslation();
  return (
    // List page skeleton: the health card is shrink-0, the queue table takes the remaining height. The queue has ≤10 rows, so use max-h-full rather than h-full --
    // sized by content, scrolling only when the window is short, instead of drawing a mostly empty box.
    <div className="h-full flex flex-col gap-4">
      {health.engines.length > 0 && (
        <div className="shrink-0">
        <SectionCard
          title={t('workbench.adminWorkers.healthTitle', { defaultValue: '引擎健康' })}
          right={health.checked_at && (
            <span className="text-[10px] text-muted">
              {t('workbench.adminWorkers.healthCheckedAt', { defaultValue: '检测于' })} {fmtTime(health.checked_at, true)}
            </span>
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            {health.engines.map(e => (
              <span key={e.engine} className="inline-flex items-center gap-1.5">
                <Badge tone={e.ok ? 'success' : 'danger'} title={e.error || ''}>
                  {engineLabel(e.engine)}
                  <span className="opacity-70 ml-1">
                    {e.ok
                      ? t('workbench.adminWorkers.healthOk', { defaultValue: '答{{a}}/引{{c}}', a: e.ans_len, c: e.cites })
                      : (e.error || t('workbench.adminWorkers.healthFail', { defaultValue: '异常' }))}
                  </span>
                </Badge>
                {!e.ok && HEAL_SELECTOR_ENGINES.includes(e.engine) && (
                  <Button size="sm" variant="outline" onClick={() => doHeal(e.engine)}>
                    {t('workbench.adminWorkers.heal', { defaultValue: '自愈' })}
                  </Button>
                )}
              </span>
            ))}
          </div>
        </SectionCard>
        </div>
      )}

      {stats && (
        <div className="flex-1 min-h-0">
        <TableShell maxHeight={false} className="max-h-full">
          <table className="w-full text-xs">
            <StickyHead>
              <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colEngine', { defaultValue: '引擎' })}</th>
              <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colQueued', { defaultValue: '排队' })}</th>
              <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colClaimed', { defaultValue: '已领' })}</th>
              <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colDone', { defaultValue: '完成' })}</th>
              <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colFailed', { defaultValue: '失败' })}</th>
              <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminWorkers.colToday', { defaultValue: '今日 / 上限' })}</th>
            </StickyHead>
            {engines.length === 0 ? (
              <EmptyRow colSpan={6} title={t('workbench.adminWorkers.queueEmpty', { defaultValue: '队列为空' })} />
            ) : (
              <tbody>
                {engines.map(e => {
                  const q = stats.queue[e] || {};
                  const cap = stats.daily_cap[e];
                  return (
                    <tr key={e} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td className="px-3 py-2.5 text-primary">{engineLabel(e)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{q.queued || 0}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{q.claimed || 0}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: 'var(--success-fg)' }}>{q.done || 0}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: (q.failed || 0) > 0 ? 'var(--danger-fg)' : 'var(--text-muted)' }}>{q.failed || 0}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-secondary">
                        {stats.used_today[e] || 0}{cap == null ? '' : ` / ${cap}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            )}
          </table>
        </TableShell>
        </div>
      )}
    </div>
  );
}
