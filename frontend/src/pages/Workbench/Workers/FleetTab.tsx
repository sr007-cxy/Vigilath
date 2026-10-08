// Fleet tab -- one machine per row, **every cell strictly single-line**, alerts consolidated into one column.
//
// Before the rework, 4 of the 10 columns were "multi-line stacked cells": the egress IP cell held IP + sharing hint + blocked +
// idle, 4 lines in total; the version cell held 3 lines; the hot session cell was one long string of 3 engines. Row heights varied and baselines didn't align,
// plus 9 kinds of red/yellow alerts scattered across 5 columns -- to answer "which machine has a problem" ops had to scan cell by cell.
//
// Now:
//   · Alerts column: deriveAlerts consolidates the 9 kinds into Badges, red first then yellow; shows — when there are no alerts
//   · Version and hot sessions moved into the detail drawer -- they're troubleshooting info, not inspection info; look at them when something's wrong
//   · Default sort: has red > has yellow > online and healthy > offline > disabled
//   · Status filter uses PillTabs; actions go into a Menu (previously 2-3 buttons laid out per row)
//   · TableShell + StickyHead (convention 1), all colors via Badge tone / CSS variables
//
// Pagination: client-side (workers are fetched all at once), default 10/page, switchable to 20/50.
// Layout follows convention 6 "list page": the page doesn't scroll, only the table scrolls inside, header pinned, pagination pinned to the bottom.
// As machine count grows the long table still needs scrolling; pagination keeps "see it all on one screen"; when filtered results fit in one page the pagination bar hides automatically.
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { BrowserWorker } from '../../../services/aiTelemetryApi';
import { engineLabel, canonicalEngines } from '../../../constants/engines';
import {
  TableShell, StickyHead, EmptyRow, Badge, StatusDot, PillTabs, UiSelect,
} from '../../../components/ui';
import { Menu } from '../../../components/ui/Menu';
import { FilterBar, FilterBarPrimary, FilterBarSecondary, FilterField, SearchInput } from '../../../components/ui/FilterBar';
import { FILTER_CONTROL_CLASS, FILTER_CONTROL_STYLE } from '../../../components/ui/filterControls';
import { Pagination } from '../../../components/Pagination';
import {
  buildRows, sortRows, filterRows, countByFilter, hostOf,
  type FleetFilter, type WorkerAlert,
} from './fleet';

const COLS = 8;

export function FleetTab({
  workers, busyUid, shareCount, act, forget, openDetail,
}: {
  workers: BrowserWorker[];
  busyUid: string | null;
  shareCount: (w: BrowserWorker) => number;
  act: (uid: string, action: 'enable' | 'disable' | 'drain') => void;
  forget: (w: BrowserWorker) => void;
  openDetail: (w: BrowserWorker) => void;
}) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<FleetFilter>('all');
  const [q, setQ] = useState('');
  const [engine, setEngine] = useState('');

  const rows = useMemo(() => sortRows(buildRows(workers, shareCount)), [workers, shareCount]);
  // Status counts are computed on the set filtered by "search + engine" -- the numbers on the pills must match what the table shows
  const narrowed = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return rows.filter(r =>
      (!engine || (r.w.engines || []).includes(engine))
      && (!kw || hostOf(r.w).toLowerCase().includes(kw) || (r.w.exit_ip || '').includes(kw)
          || r.w.worker_uid.toLowerCase().includes(kw)));
  }, [rows, q, engine]);
  const counts = useMemo(() => countByFilter(narrowed), [narrowed]);
  const filtered = useMemo(() => filterRows(narrowed, filter), [narrowed, filter]);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  // The page number can't exceed the actual page count -- once a filter narrows results, staying on page 3 with only 5 results left gives an empty table.
  // Computed as a derived value rather than setState in an effect (that costs an extra render and is blocked by hooks rules).
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const shown = useMemo(
    () => filtered.slice((safePage - 1) * pageSize, safePage * pageSize), [filtered, safePage, pageSize]);
  // When any of the three filters changes, go back to page one
  const changeFilter = (v: FleetFilter) => { setFilter(v); setPage(1); };
  const changeQ = (v: string) => { setQ(v); setPage(1); };
  const changeEngine = (v: string) => { setEngine(v); setPage(1); };
  // Engine dropdown candidates = engines actually reported by the whole fleet, not hardcoded
  const engineOptions = useMemo(() => {
    const set = new Set<string>();
    workers.forEach(w => canonicalEngines(w.engines || []).forEach(e => set.add(e)));
    return [{ value: '', label: t('workbench.adminWorkers.allEngines', { defaultValue: '全部引擎' }) },
            ...[...set].map(e => ({ value: e, label: engineLabel(e) }))];
  }, [workers, t]);

  const FILTERS: { value: FleetFilter; label: string }[] = [
    { value: 'all', label: t('workbench.adminWorkers.filterAll', { defaultValue: '全部' }) },
    { value: 'alert', label: t('workbench.adminWorkers.filterAlert', { defaultValue: '有告警' }) },
    { value: 'online', label: t('workbench.adminWorkers.online', { defaultValue: '在线' }) },
    { value: 'offline', label: t('workbench.adminWorkers.offline', { defaultValue: '离线' }) },
    { value: 'disabled', label: t('workbench.adminWorkers.disabledBadge', { defaultValue: '已禁用' }) },
  ];

  return (
    // List page skeleton: filter bar shrink-0 · table flex-1 min-h-0 inner scrolling · pagination shrink-0 pinned to the bottom
    <div className="h-full flex flex-col gap-3">
      {/* The filter bar uses the unified skeleton: search on the first row, status pills + engine dropdown on the second.
          Same controls as content review / task monitor, no more hand-laid flex. */}
      <header className="shrink-0">
      <FilterBar>
        <FilterBarPrimary>
          <SearchInput value={q} onChange={changeQ} className="flex-1 min-w-[200px] max-w-md"
                       placeholder={t('workbench.adminWorkers.searchPlaceholder', { defaultValue: '搜主机名 / 出口 IP / uid' })} />
        </FilterBarPrimary>
        <FilterBarSecondary
          right={<FilterField label={t('workbench.adminWorkers.colEngines', { defaultValue: '引擎' })}>
            <UiSelect value={engine} onChange={v => changeEngine(String(v))} options={engineOptions}
                      className={FILTER_CONTROL_CLASS} style={FILTER_CONTROL_STYLE} />
          </FilterField>}>
          <PillTabs value={filter} onChange={changeFilter}
                    items={FILTERS.map(f => ({ ...f, count: counts[f.value] }))} />
        </FilterBarSecondary>
      </FilterBar>
      </header>

      {/* key={safePage}: remount the scroll container on page change so the scroll position returns to the top (convention 6) */}
      <div className="flex-1 min-h-0" key={safePage}>
      <TableShell maxHeight={false} className="h-full">
        <table className="w-full text-xs">
          <StickyHead>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colHost', { defaultValue: '主机' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colStatus', { defaultValue: '状态' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colAlerts', { defaultValue: '告警' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colEngines', { defaultValue: '引擎' })}</th>
            <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{t('workbench.adminWorkers.colExitIp', { defaultValue: '出口 IP' })}</th>
            <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminWorkers.colTodayInflight', { defaultValue: '今日 / 在跑' })}</th>
            <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{t('workbench.adminWorkers.colQuality', { defaultValue: '近十条成色' })}</th>
            <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colActions', { defaultValue: '操作' })}</th>
          </StickyHead>

          {shown.length === 0 ? (
            <EmptyRow
              colSpan={COLS}
              title={workers.length === 0
                ? t('workbench.adminWorkers.noWorkers', { defaultValue: '暂无 worker 接入' })
                : t('workbench.adminWorkers.noMatch', { defaultValue: '这个筛选下没有机器' })}
              hint={workers.length === 0
                ? t('workbench.adminWorkers.noWorkersHint', { defaultValue: '按 V-networks/workers/desktop-cn/README.md 一条命令装机,注册上来就会出现在这里。' })
                : undefined}
            />
          ) : (
            <tbody>
              {shown.map(({ w, alerts, online }) => (
                <tr key={w.worker_uid} style={{ borderTop: '1px solid var(--border-color)' }}>
                  {/* Host: click to open the detail drawer (version / hot sessions / params / event stream are all there) */}
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <button type="button" onClick={() => openDetail(w)}
                            className="text-primary underline decoration-dotted underline-offset-2 hover:opacity-80"
                            title={t('workbench.adminWorkers.detailTip', { defaultValue: '看这台机器的版本、热会话、运行参数和事件流' })}>
                      {hostOf(w)}
                    </button>
                  </td>

                  {/* Status: a dot + a word. Disabled / draining are modifiers of the running state, merged into this cell */}
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    {!w.enabled ? (
                      <StatusDot tone="neutral">{t('workbench.adminWorkers.disabledBadge', { defaultValue: '已禁用' })}</StatusDot>
                    ) : w.raw_status === 'draining' ? (
                      <StatusDot tone="warn">{t('workbench.adminWorkers.drainingBadge', { defaultValue: '排空中' })}</StatusDot>
                    ) : online ? (
                      <StatusDot tone="success">{t('workbench.adminWorkers.online', { defaultValue: '在线' })}</StatusDot>
                    ) : (
                      <StatusDot tone="neutral">{t('workbench.adminWorkers.offline', { defaultValue: '离线' })}</StatusDot>
                    )}
                  </td>

                  {/* Alerts: this column is everything inspection needs to see. Red first, then yellow */}
                  <td className="px-3 py-2.5">
                    <AlertBadges alerts={alerts} />
                  </td>

                  <td className="px-3 py-2.5 text-secondary whitespace-nowrap">
                    {canonicalEngines(w.engines || []).map(engineLabel).join('、') || '—'}
                  </td>

                  <td className="px-3 py-2.5 text-secondary tabular-nums whitespace-nowrap">
                    {w.exit_ip && w.exit_ip !== '-' ? w.exit_ip : '—'}
                  </td>

                  <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">
                    <span className="text-primary">{w.done_today}</span>
                    <span className="text-muted"> / {w.in_flight}</span>
                  </td>

                  <td className="px-3 py-2.5 text-secondary tabular-nums whitespace-nowrap">
                    {w.quality
                      ? `${w.quality.done}/${w.quality.n} · ${w.quality.avg_len}${t('workbench.adminWorkers.charUnit', { defaultValue: '字' })} · ${w.quality.avg_cites}${t('workbench.adminWorkers.citeUnit', { defaultValue: '源' })}`
                      : '—'}
                  </td>

                  {/* Actions go into a menu: previously 2-3 buttons laid out per row, all equally large and prominent */}
                  <td className="px-3 py-2.5 text-right">
                    <Menu
                      label="···"
                      title={t('workbench.adminWorkers.colActions', { defaultValue: '操作' })}
                      triggerClassName="px-2 py-1 rounded-md text-sm"
                      triggerStyle={{ background: 'var(--bg-tertiary)', opacity: busyUid === w.worker_uid ? 0.4 : 1 }}
                      items={[
                        ...(w.enabled ? [
                          { label: t('workbench.adminWorkers.drain', { defaultValue: '排空' }),
                            hint: t('workbench.adminWorkers.drainHint', { defaultValue: '不再领新活,跑完手上的再停' }),
                            onClick: () => act(w.worker_uid, 'drain'), disabled: busyUid === w.worker_uid },
                          { label: t('workbench.adminWorkers.disable', { defaultValue: '禁用' }),
                            hint: t('workbench.adminWorkers.disableHint', { defaultValue: '立刻停止派活' }),
                            onClick: () => act(w.worker_uid, 'disable'), disabled: busyUid === w.worker_uid, danger: true },
                        ] : [
                          { label: t('workbench.adminWorkers.enable', { defaultValue: '启用' }),
                            onClick: () => act(w.worker_uid, 'enable'), disabled: busyUid === w.worker_uid },
                        ]),
                        // Remove is offered only for offline ones -- the backend also rejects running ones (409).
                        // It fixes duplicate-name ghost rows: after the same machine changes uid, the old row stays forever and never disappears on its own.
                        ...(!online ? [
                          { label: t('workbench.adminWorkers.forget', { defaultValue: '移除' }),
                            hint: t('workbench.adminWorkers.forgetHint', { defaultValue: '从注册表移除,历史任务保留;机器再上线会自动重注册' }),
                            onClick: () => forget(w), disabled: busyUid === w.worker_uid },
                        ] : []),
                      ]}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </TableShell>
      </div>

      <Pagination className="shrink-0" page={safePage} pageSize={pageSize} total={filtered.length}
                  onPageChange={setPage} onPageSizeChange={s => { setPageSize(s); setPage(1); }}
                  pageSizeOptions={[10, 20, 50]} />
    </div>
  );
}

function AlertBadges({ alerts }: { alerts: WorkerAlert[] }) {
  if (alerts.length === 0) return <span className="text-muted">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {alerts.map((a, i) => (
        <Badge key={i} tone={a.level === 'danger' ? 'danger' : 'warn'} title={a.detail}>
          {a.label}
        </Badge>
      ))}
    </div>
  );
}
