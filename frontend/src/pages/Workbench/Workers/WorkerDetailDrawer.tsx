// Machine details: runtime (version / bundle / hot sessions) + recent status + runtime params + event timeline.
//
// "Version" and "Hot sessions" used to sit permanently in two columns of the fleet table, stretching each row to 3-4 lines. They're **troubleshooting**
// info -- needed only when something's wrong, not during inspection -- so they moved here, and the main table only shows an alert Badge when there's a problem.
import { useTranslation } from 'react-i18next';
import type { BrowserWorker, WorkerDiagnostics, WorkerPolicy } from '../../../services/aiTelemetryApi';
import { fmtTime } from '../../../utils/datetime';
import { engineLabel } from '../../../constants/engines';
import { Badge, StatusDot, Button, Input, Modal } from '../../../components/ui';

// Runtime params adjustable per physical device. Key names correspond one-to-one with load_config() in V-networks/workers/desktop-cn/worker.py
// -- on every heartbeat the worker merges the config pushed by the center and hot-applies it key by key, so changes here take effect within 30 seconds,
// without logging into the machine to edit config.json or restarting the center.
// Before adding a new key, confirm worker.py recognizes it, otherwise it'll be pushed and ignored.
const POLICY_FIELDS: { key: string; label: string; hint: string }[] = [
  { key: 'daily_task_limit', label: '每日任务上限', hint: '这一台每天最多跑多少条' },
  { key: 'hourly_task_limit', label: '每小时任务上限', hint: '控速率;共用出口时应压低' },
  { key: 'chats_per_window', label: '单窗口连问题数', hint: '一次领几条在同一个浏览器窗口里顺序跑,别超过 5' },
  { key: 'task_gap_min_sec', label: '两题最小间隔(秒)', hint: '' },
  { key: 'task_gap_max_sec', label: '两题最大间隔(秒)', hint: '' },
  { key: 'profile_recycle_every', label: '跑满几单换会话', hint: '实测连出 5 单必撞墙;0=不主动换' },
  { key: 'profile_recycle_cooldown_sec', label: '换会话后冷却(秒)', hint: '撞墙后约 18 分钟自行恢复,别调太短' },
];

const EVENT_LABEL: Record<string, string> = {
  registered: '首次注册', restarted: '进程重启', upgraded: '版本更新',
  online: '恢复心跳', paused: '暂停领活', resumed: '恢复领活',
  drained: '被排空', disabled: '被禁用', config_changed: '改了参数',
  forgotten: '移出注册表', stopped: '退出',
};

/** Compress event details into one localized sentence. The fields are few and fixed, so no generic JSON rendering -- something like
 *  {"after_offline_sec":7200} on the panel conveys no information. */
function eventDetailText(d: Record<string, unknown>): string {
  if (!d || Object.keys(d).length === 0) return '';
  const parts: string[] = [];
  if (d.reason) parts.push(String(d.reason));
  if (d.by) parts.push(`由 ${d.by}`);
  if (d.from && d.to) parts.push(`${d.from} → ${d.to}`);
  if (d.after_offline_sec) parts.push(`离线 ${Math.round(Number(d.after_offline_sec) / 60)} 分后`);
  if (d.changed && typeof d.changed === 'object') parts.push(Object.keys(d.changed as object).join('、'));
  return parts.join(' · ');
}

export interface DetailState {
  open: boolean; uid: string; host: string;
  diag: WorkerDiagnostics | null; policy: WorkerPolicy | null;
  draft: Record<string, string>; note: string; busy: boolean;
}

export function WorkerDetailDrawer({
  detail, worker, setDetail, onSave,
}: {
  detail: DetailState;
  /** Live data of this row in the main table (version / hot sessions read from here, no separate request) */
  worker: BrowserWorker | undefined;
  setDetail: (fn: (s: DetailState) => DetailState) => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  const close = () => setDetail(s => ({ ...s, open: false }));

  return (
    <Modal open={detail.open} onClose={close} maxWidthClass="max-w-3xl"
           header={
             <div>
               <div className="text-sm font-semibold text-primary">{detail.host}</div>
               <div className="text-[11px] text-muted tabular-nums">{detail.uid}</div>
             </div>
           }
           footer={
             <div className="flex justify-end gap-2">
               <Button size="sm" variant="ghost" onClick={close}>
                 {t('workbench.adminWorkers.close', { defaultValue: '关闭' })}
               </Button>
               <Button size="sm" variant="solid" onClick={onSave} loading={detail.busy} disabled={!detail.policy}>
                 {t('workbench.adminWorkers.save', { defaultValue: '保存' })}
               </Button>
             </div>
           }>
      <div>

        {/* (1) Runtime -- the two columns moved from the main table. Version must be split into two metric definitions, "disk" and "process":
            version is a snapshot from the moment the process started, and doesn't change when files on disk are replaced. On 2026-09-09 two physical devices
            had the new files pulled, yet this column didn't budge, so it was investigated as "files not pulled" for a whole day. */}
        {worker && (
          <Section title={t('workbench.adminWorkers.runtimeTitle', { defaultValue: '运行时' })}>
            <table className="w-full text-[11px]">
              <tbody>
                <Row label={t('workbench.adminWorkers.colVersion', { defaultValue: '进程版本' })}>
                  <span className="tabular-nums text-primary">{worker.version || '—'}</span>
                  {worker.disk_version && worker.disk_version !== worker.version && (
                    <span className="text-muted ml-2 tabular-nums">
                      {t('workbench.adminWorkers.diskVersion', { defaultValue: '磁盘' })} {worker.disk_version}
                    </span>
                  )}
                </Row>
                <Row label={t('workbench.adminWorkers.bundleVersion', { defaultValue: '整包版本' })}>
                  {worker.bundle_version
                    ? <span className="tabular-nums text-primary">{worker.bundle_version}</span>
                    : <Badge tone="warn" title={t('workbench.adminWorkers.needManualPushTip', {
                        defaultValue: '这台还没接上整包自更新。按 V-networks/workers/desktop-cn/README.md 里那条命令人工推一次,之后就能自己升级了。' })}>
                        {t('workbench.adminWorkers.needManualPush', { defaultValue: '待人工推一次' })}
                      </Badge>}
                </Row>
                {worker.upgrade_hint && (
                  <Row label={t('workbench.adminWorkers.upgradeHint', { defaultValue: '升级提示' })}>
                    <span style={{ color: worker.upgrade_hint.level === 'action' ? 'var(--warn-fg)' : 'var(--text-muted)' }}>
                      {worker.upgrade_hint.code === 'process_stale'
                        ? t('workbench.adminWorkers.processStale', {
                            defaultValue: '磁盘 {{disk}} ≠ 进程 —— 重启服务', disk: worker.disk_version })
                        : worker.upgrade_hint.text}
                    </span>
                  </Row>
                )}
                <Row label={t('workbench.adminWorkers.colHotSession', { defaultValue: '热会话' })}>
                  {(worker.hot_sessions || []).length === 0 ? <span className="text-muted">—</span> : (
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      {(worker.hot_sessions || []).map(h => (
                        <span key={h.engine} className="whitespace-nowrap">
                          <span className="text-secondary">{engineLabel(h.engine)}</span>{' '}
                          {h.breaker_open ? (
                            <StatusDot tone="danger">{t('workbench.adminWorkers.hotBreaker', { defaultValue: '熔断' })} {Math.ceil(h.breaker_remain_sec / 60)}{t('workbench.adminWorkers.minute', { defaultValue: '分' })}</StatusDot>
                          ) : h.login_ok === false ? (
                            <StatusDot tone="danger">{t('workbench.adminWorkers.hotLoginLost', { defaultValue: '掉登录' })}</StatusDot>
                          ) : h.login_ok ? (
                            <StatusDot tone="success">{t('workbench.adminWorkers.hotOk', { defaultValue: '正常' })}</StatusDot>
                          ) : (
                            <StatusDot tone="neutral">{t('workbench.adminWorkers.hotUnknown', { defaultValue: '未验' })}</StatusDot>
                          )}
                          {h.rotate_count > 0 && (
                            <span className="text-muted ml-1">
                              {t('workbench.adminWorkers.hotRebuilt', { defaultValue: '重建' })} {h.rotate_count}
                            </span>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </Row>
                <Row label={t('workbench.adminWorkers.lastHeartbeat', { defaultValue: '最近心跳' })}>
                  <span className="tabular-nums text-secondary">{worker.last_heartbeat_at ? fmtTime(worker.last_heartbeat_at) : '—'}</span>
                </Row>
              </tbody>
            </table>
          </Section>
        )}

        {!detail.diag ? (
          <div className="text-xs text-muted py-10 text-center">
            {t('workbench.adminWorkers.loading', { defaultValue: '读取中…' })}
          </div>
        ) : (
          <>
            {/* (2) Recent status: quality + failures grouped by type. The key is separating "never ran" from "ran and got blocked"
                -- of 522 failures in production, sixty percent were the former (too many queued with no takers, aged out and marked dead),
                and investigating along the lines of "Doubao got anti-abuse blocked again" would lead you astray. */}
            <Section title={t('workbench.adminWorkers.recentTitle', { defaultValue: '近 3 天' })}>
              <div className="text-xs text-secondary mb-2">
                <span className="text-primary tabular-nums">
                  {detail.diag.done} / {detail.diag.done + detail.diag.failed}
                </span>
                {detail.diag.ok_rate !== null && (
                  <span className="ml-1 tabular-nums"
                        style={{ color: detail.diag.ok_rate < 0.2 ? 'var(--danger-fg)' : 'var(--text-muted)' }}>
                    ({Math.round(detail.diag.ok_rate * 100)}%)
                  </span>
                )}
                {detail.diag.blocked_reason && (
                  <span className="ml-3" style={{ color: 'var(--warn-fg)' }}>
                    {t('workbench.adminWorkers.blockedNow', { defaultValue: '此刻被限流' })}:{detail.diag.blocked_reason}
                  </span>
                )}
              </div>
              {detail.diag.failures.length > 0 && (
                <table className="w-full text-[11px]">
                  <tbody>
                    {detail.diag.failures.map(f => (
                      <tr key={f.label} style={{ borderTop: '1px solid var(--border-color)' }}>
                        <td className="py-1 text-secondary" style={{ width: 160 }}>{f.label}</td>
                        <td className="py-1 text-primary tabular-nums" style={{ width: 50 }}>{f.n}</td>
                        <td className="py-1 text-muted">{f.sample}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>

            {/* (3) Runtime params. Empty = this item follows the global setting, not set to 0.
                Changes take effect on the next heartbeat (≤30s) -- the worker already merges the center-pushed config on every heartbeat. */}
            <Section title={t('workbench.adminWorkers.policyTitle', { defaultValue: '运行参数(留空=跟随全局)' })}>
              <table className="w-full text-[11px]">
                <tbody>
                  {POLICY_FIELDS.map(f => (
                    <tr key={f.key}>
                      <td className="py-1 text-secondary" style={{ width: 190 }}>{f.label}</td>
                      <td className="py-1" style={{ width: 110 }}>
                        <Input size="sm" value={detail.draft[f.key] ?? ''} inputMode="numeric"
                               placeholder={String(detail.policy?.global_config?.[f.key] ?? '—')}
                               onChange={v => setDetail(s => ({ ...s, draft: { ...s.draft, [f.key]: v } }))} />
                      </td>
                      <td className="py-1 text-muted pl-2">{f.hint}</td>
                    </tr>
                  ))}
                  <tr>
                    <td className="py-1 text-secondary">{t('workbench.adminWorkers.policyNote', { defaultValue: '备注' })}</td>
                    <td className="py-1" colSpan={2}>
                      <Input size="sm" value={detail.note} onChange={v => setDetail(s => ({ ...s, note: v }))} />
                    </td>
                  </tr>
                </tbody>
              </table>
              {detail.policy?.updated_at && (
                <div className="text-[10px] text-muted mt-2">
                  {t('workbench.adminWorkers.policyLastEdit', { defaultValue: '上次修改' })}:
                  {fmtTime(detail.policy.updated_at)} · {detail.policy.updated_by}
                </div>
              )}
            </Section>

            {/* (4) Event timeline. It fixes "no idea why it stopped" -- an offline machine used to
                just "not appear in the online list", looking exactly like one that was never installed. */}
            <Section title={t('workbench.adminWorkers.timelineTitle', { defaultValue: '发生过什么' })}>
              {detail.diag.timeline.length === 0 ? (
                <div className="text-[11px] text-muted">{t('workbench.adminWorkers.timelineEmpty', { defaultValue: '暂无记录' })}</div>
              ) : (
                <table className="w-full text-[11px]">
                  <tbody>
                    {detail.diag.timeline.map((e, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border-color)' }}>
                        <td className="py-1 text-muted tabular-nums" style={{ width: 130 }}>{fmtTime(e.at)}</td>
                        <td className="py-1 text-secondary" style={{ width: 90 }}>{EVENT_LABEL[e.kind] || e.kind}</td>
                        <td className="py-1 text-muted">{eventDetailText(e.detail)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>
          </>
        )}

      </div>
    </Modal>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="text-xs font-medium text-secondary mb-2 pb-1"
           style={{ borderBottom: '1px solid var(--border-color)' }}>{title}</div>
      {children}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <tr>
      <td className="py-1 text-secondary align-top whitespace-nowrap" style={{ width: 110 }}>{label}</td>
      <td className="py-1">{children}</td>
    </tr>
  );
}
