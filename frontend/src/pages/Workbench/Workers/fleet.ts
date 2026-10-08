// Pure logic for the fleet tab: alert consolidation, sorting, filtering. No JSX, for standalone verification.
//
// Why there's an "alert consolidation" layer:
// previously 9 kinds of alerts were scattered across 5 table columns (the egress IP cell held "shared by N" + blocked + idle,
// the version cell held "manual push needed" + upgrade_hint, the engine cell ⚡circuit breaker, the hot session cell logged-out/circuit breaker,
// and the quality cell was marked red); to answer "which machine has a problem" ops had to scan cell by cell. Here they're collected into one column of Badges,
// red first, then yellow.
//
// Two levels are enough:
//   danger  this machine **isn't working** or **is delivering garbage** -- needs immediate action
//   warn    needs manual intervention but the machine is still running -- schedule it
// Three levels (adding info) don't discriminate enough in an 8-machine table, and "hint" info fits better in the detail drawer.
import type { BrowserWorker } from '../../../services/aiTelemetryApi';
import { engineLabel } from '../../../constants/engines';

export type AlertLevel = 'danger' | 'warn';

export interface WorkerAlert {
  level: AlertLevel;
  /** Short label, goes into the Badge */
  label: string;
  /** Long explanation, goes into title */
  detail?: string;
}

/**
 * Derive all alerts for a worker row. Order is display order: red first.
 * `shareCount` is passed in from outside -- "egress shared by N" depends on the whole fleet and can't be computed from one row.
 */
export function deriveAlerts(w: BrowserWorker, shareCount: number): WorkerAlert[] {
  const out: WorkerAlert[] = [];

  // ── danger: not working / delivering garbage ──────────────────────────────────────
  // "The center lets it through but it doesn't pick up work" and "the center blocks it" are two different ailments with opposite fixes: the former means restart the machine,
  // the latter means adjust the gate. On 2026-09-09 five servers stopped for three and a half hours with the panel all green, just because only blocked was checked.
  if (w.idle_reason) {
    out.push({ level: 'danger', label: '不来领活', detail: w.idle_reason });
  }
  const broken = Object.keys(w.breaker || {});
  if (broken.length) {
    out.push({ level: 'danger', label: `熔断 ${broken.map(engineLabel).join('、')}` });
  }
  for (const h of w.hot_sessions || []) {
    if (h.breaker_open) {
      out.push({ level: 'danger', label: `${engineLabel(h.engine)} 熔断 ${Math.ceil(h.breaker_remain_sec / 60)} 分` });
    } else if (h.login_ok === false) {
      out.push({ level: 'danger', label: `${engineLabel(h.engine)} 掉登录` });
    }
  }
  // Healthy only if the success rate is over half and bodies exceed 500 characters -- a machine can steadily deliver 10 empty answers,
  // which "completed today" alone won't reveal
  if (w.quality) {
    const q = w.quality;
    const rate = q.done / Math.max(1, q.n);
    if (rate < 0.5 || q.avg_len < 500) {
      out.push({
        level: 'danger', label: '成色差',
        detail: `近 ${q.n} 条:成功 ${q.done},平均 ${q.avg_len} 字 / ${q.avg_cites} 源`,
      });
    }
  }

  // ── warn: needs manual action, but the machine is still running ─────────────────────────────────
  if (w.blocked_reason) {
    out.push({ level: 'warn', label: '被限流', detail: w.blocked_reason });
  }
  // A shared egress is the biggest risk for the Doubao channel: rate limiting is per egress IP, so several machines sharing one NAT egress
  // multiplies the density. To raise throughput add egresses, not machines.
  if (shareCount > 1) {
    out.push({ level: 'warn', label: `${shareCount} 台共用出口`, detail: '限流额度是它们合起来算的' });
  }
  // Empty bundle version = not yet on the self-update channel, needs one manual push. That's how four machines in production ran the 08-14 code
  // for 17 days: self-update was only added on 08-24, so they couldn't possibly have upgraded themselves.
  if (!w.bundle_version) {
    out.push({ level: 'warn', label: '待人工推包', detail: '按 V-networks/workers/desktop-cn/README.md 里那条命令推一次,之后就能自己升级' });
  }
  // version is a snapshot from the moment the process started and doesn't change when files on disk are replaced -- the backend directly says which step to take
  // based on "disk vs. process", and this just copies it.
  if (w.upgrade_hint?.level === 'action') {
    out.push({
      level: 'warn',
      label: w.upgrade_hint.code === 'process_stale' ? '需重启服务' : '需升级',
      detail: w.upgrade_hint.text,
    });
  }
  return out;
}

export type FleetFilter = 'all' | 'alert' | 'online' | 'offline' | 'disabled';

export interface FleetRow {
  w: BrowserWorker;
  alerts: WorkerAlert[];
  online: boolean;
  /** For sorting: 0 has red · 1 has yellow · 2 online and healthy · 3 offline · 4 disabled */
  rank: number;
}

export function buildRows(workers: BrowserWorker[], shareCount: (w: BrowserWorker) => number): FleetRow[] {
  return workers.map(w => {
    const alerts = deriveAlerts(w, shareCount(w));
    const online = w.status === 'online';
    let rank: number;
    if (!w.enabled) rank = 4;
    else if (!online) rank = 3;
    else if (alerts.some(a => a.level === 'danger')) rank = 0;
    else if (alerts.length) rank = 1;
    else rank = 2;
    return { w, alerts, online, rank };
  });
}

/** Default sort: has red > has yellow > online and healthy > offline > disabled; same tier by hostname. */
export function sortRows(rows: FleetRow[]): FleetRow[] {
  return [...rows].sort((a, b) =>
    a.rank - b.rank
    || hostOf(a.w).localeCompare(hostOf(b.w), 'zh-Hans-CN'));
}

export function filterRows(rows: FleetRow[], f: FleetFilter): FleetRow[] {
  switch (f) {
    case 'alert':    return rows.filter(r => r.alerts.length > 0 && r.w.enabled);
    case 'online':   return rows.filter(r => r.online && r.w.enabled);
    case 'offline':  return rows.filter(r => !r.online && r.w.enabled);
    case 'disabled': return rows.filter(r => !r.w.enabled);
    default:         return rows;
  }
}

export function countByFilter(rows: FleetRow[]): Record<FleetFilter, number> {
  return {
    all: rows.length,
    alert: filterRows(rows, 'alert').length,
    online: filterRows(rows, 'online').length,
    offline: filterRows(rows, 'offline').length,
    disabled: filterRows(rows, 'disabled').length,
  };
}

export function hostOf(w: BrowserWorker): string {
  return w.hostname || w.label || w.worker_uid.slice(0, 8);
}
