// Call log tab: server-side pagination + filters (tenant / engine / status / source / time range); rows expand to show the response.
//
// Filter and pagination state are held by the shell (reload needs them for requests); this only renders.
// Pagination used to be a hand-written 0-based previous/next + per-page dropdown; now it uses Pagination (1-based).
// The "Source" filter matters: since batch runs go through the gateway, both kinds share the table, batch runs produce hundreds per tick and external calls only a few --
// without the filter external calls would be drowned out.
import { Fragment, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { GatewayTenant, GatewayJob } from '../../../services/aiTelemetryApi';
import { fmtTime } from '../../../utils/datetime';
import { ENGINE_KEYS, engineLabel } from '../../../constants/engines';
import { TableShell, StickyHead, EmptyRow, Badge, Button, UiSelect, PillTabs } from '../../../components/ui';
import {
  FilterBar, FilterBarPrimary, FilterBarSecondary, FilterField, DateRangeFilter,
} from '../../../components/ui/FilterBar';
import { FILTER_CONTROL_CLASS, FILTER_CONTROL_STYLE } from '../../../components/ui/filterControls';
import { fromQueryRange, toQueryRange } from '../../../components/ui/dateFilter';
import { Pagination } from '../../../components/Pagination';
import { JOB_STATUSES, jobStatusLabel, jobStatusTone } from './labels';

export interface JobFilters {
  tenant: string;         // '' = all; otherwise the tenant id string
  engine: string;
  status: string;
  source: '' | 'api' | 'batch';
  since: string;
  until: string;
}

export interface JobsTabProps {
  tenants: GatewayTenant[];
  jobs: GatewayJob[];
  total: number;
  loading: boolean;       // Show loading only for filter-triggered refreshes, not silent polling
  filters: JobFilters;
  setFilters: (patch: Partial<JobFilters>) => void;
  resetFilters: () => void;
  page: number;           // 1-based
  pageSize: number;
  setPage: (p: number) => void;
  setPageSize: (s: number) => void;
}

export function JobsTab({
  tenants, jobs, total, loading, filters, setFilters, resetFilters, page, pageSize, setPage, setPageSize,
}: JobsTabProps) {
  const { t } = useTranslation();
  const [openJob, setOpenJob] = useState<number | null>(null);  // job id expanded to show the response
  const active = !!(filters.tenant || filters.engine || filters.status || filters.source || filters.since || filters.until);

  // Tenant column of the call log: show names instead of ids; historical empty values are labeled "Unassigned"
  const tenantName = (id: string | null) => {
    if (!id) return t('workbench.adminGateway.tenantUnassigned', { defaultValue: '未归属(历史)' });
    const hit = tenants.find(x => String(x.id) === String(id));
    // Matching no tenant = historical dirty value (callers once wrote made-up labels into this column); show as-is and flag it
    return hit ? hit.name : t('workbench.adminGateway.tenantUnknown', { defaultValue: '{{v}}(未登记)', v: id });
  };

  return (
    <div className="space-y-4">
      <FilterBar>
        <FilterBarPrimary right={active && (
          <Button size="sm" variant="ghost" onClick={resetFilters}>
            {t('workbench.adminGateway.clearFilters', { defaultValue: '清除筛选' })}
          </Button>
        )}>
          <FilterField label={t('workbench.adminGateway.colTenant', { defaultValue: '租户' })}>
            <UiSelect value={filters.tenant} onChange={v => setFilters({ tenant: v })} searchable
                      className={FILTER_CONTROL_CLASS} style={FILTER_CONTROL_STYLE}
                      options={[{ value: '', label: t('workbench.adminGateway.tenantAll', { defaultValue: '全部' }) },
                                ...tenants.map(tt => ({ value: String(tt.id), label: tt.name }))]} />
          </FilterField>
          <FilterField label={t('workbench.adminGateway.colEngine', { defaultValue: '引擎' })}>
            <UiSelect value={filters.engine} onChange={v => setFilters({ engine: v })}
                      className={FILTER_CONTROL_CLASS} style={FILTER_CONTROL_STYLE}
                      options={[{ value: '', label: t('workbench.adminGateway.engineAll', { defaultValue: '全部' }) },
                                ...ENGINE_KEYS.map(e => ({ value: e, label: engineLabel(e) }))]} />
          </FilterField>
          <FilterField label={t('workbench.adminGateway.colTime', { defaultValue: '时间' })}>
            <DateRangeFilter value={fromQueryRange(filters.since, filters.until)}
                             onChange={v => { const q = toQueryRange(v); setFilters({ since: q.since, until: q.until }); }} />
          </FilterField>
        </FilterBarPrimary>
        <FilterBarSecondary
          right={<FilterField label={t('workbench.adminGateway.colSource', { defaultValue: '来源' })}>
            <PillTabs value={filters.source} onChange={v => setFilters({ source: v })} items={[
              { value: '', label: t('workbench.adminGateway.sourceAll', { defaultValue: '全部' }) },
              { value: 'api', label: t('workbench.adminGateway.sourceApi', { defaultValue: '对外调用' }) },
              { value: 'batch', label: t('workbench.adminGateway.sourceBatch', { defaultValue: '内部跑批' }) },
            ]} />
          </FilterField>}>
          <PillTabs value={filters.status} onChange={v => setFilters({ status: v })} items={[
            { value: '', label: t('workbench.adminGateway.statusAll', { defaultValue: '全部状态' }) },
            ...JOB_STATUSES.map(s => ({ value: s, label: jobStatusLabel(t, s) })),
          ]} />
        </FilterBarSecondary>
      </FilterBar>

      <TableShell maxHeight={false}>
        <table className="w-full text-xs">
          <StickyHead>
            <th className="px-3 py-2.5 text-left font-medium">#</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colTenant', { defaultValue: '租户' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colSource', { defaultValue: '来源' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colEngine', { defaultValue: '引擎' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colQuery', { defaultValue: '问题' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colStatus', { defaultValue: '状态' })}</th>
            <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{t('workbench.adminGateway.colTime', { defaultValue: '时间' })}</th>
            <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminGateway.colReturn', { defaultValue: '返回' })}</th>
          </StickyHead>
          {jobs.length === 0 ? (
            <EmptyRow colSpan={8}
              title={active
                ? t('workbench.adminGateway.noMatch', { defaultValue: '这个筛选下没有调用' })
                : t('workbench.adminGateway.noJobs', { defaultValue: '暂无调用' })} />
          ) : (
            <tbody style={{ opacity: loading ? 0.45 : 1, transition: 'opacity .15s' }}>
              {jobs.map(j => {
                const isBatch = j.source ? j.source === 'batch' : j.topic_id != null;
                const open = openJob === j.id;
                return (
                  <Fragment key={j.id}>
                    <tr style={{ borderTop: '1px solid var(--border-color)', cursor: 'pointer' }}
                        onClick={() => setOpenJob(open ? null : j.id)}>
                      <td className="px-3 py-2.5 text-muted tabular-nums">{j.id}</td>
                      <td className="px-3 py-2.5 text-secondary whitespace-nowrap" title={j.tenant_id ? `tenant_id=${j.tenant_id}` : undefined}>
                        {tenantName(j.tenant_id)}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        {/* For the batch-run tier, bring out the topic: when ops see a batch run, the next question is always "which topic" */}
                        <Badge tone={isBatch ? 'neutral' : 'info'}
                               title={isBatch && j.topic_id != null ? `topic_id=${j.topic_id}${j.run_id != null ? ` run_id=${j.run_id}` : ''}` : undefined}>
                          {isBatch
                            ? t('workbench.adminGateway.sourceBatch', { defaultValue: '内部跑批' })
                            : t('workbench.adminGateway.sourceApi', { defaultValue: '对外调用' })}
                        </Badge>
                      </td>
                      <td className="px-3 py-2.5 text-primary whitespace-nowrap">{engineLabel(j.engine)}</td>
                      <td className="px-3 py-2.5 text-secondary truncate max-w-[22rem]" title={j.query}>{j.query}</td>
                      <td className="px-3 py-2.5 whitespace-nowrap"><Badge tone={jobStatusTone(j.status)}>{jobStatusLabel(t, j.status)}</Badge></td>
                      <td className="px-3 py-2.5 text-muted tabular-nums whitespace-nowrap">{fmtTime(j.created_at)}</td>
                      <td className="px-3 py-2.5 text-right">
                        <Button size="sm" variant="ghost" onClick={e => { e.stopPropagation(); setOpenJob(open ? null : j.id); }}>
                          {open ? t('workbench.adminGateway.collapse', { defaultValue: '收起' }) : t('workbench.adminGateway.view', { defaultValue: '查看' })}
                        </Button>
                      </td>
                    </tr>
                    {open && (
                      <tr style={{ background: 'var(--bg-tertiary)' }}>
                        <td colSpan={8} className="px-4 py-3">
                          {j.error ? (
                            <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>
                              {t('workbench.adminGateway.retError', { defaultValue: '错误' })}:{j.error}
                            </div>
                          ) : (
                            <>
                              <div className="text-xs text-muted mb-1">{t('workbench.adminGateway.retAnswer', { defaultValue: '返回答案' })}:</div>
                              <div className="text-xs text-primary whitespace-pre-wrap mb-2" style={{ maxHeight: 320, overflow: 'auto' }}>
                                {j.answer || t('workbench.adminGateway.retEmpty', { defaultValue: '(空)' })}
                              </div>
                              <div className="text-xs text-muted mb-1">
                                {t('workbench.adminGateway.retCitations', { defaultValue: '引用来源' })}({j.citations?.length || 0}):
                              </div>
                              {(j.citations && j.citations.length > 0) ? (
                                <ul className="text-xs" style={{ listStyle: 'disc', paddingLeft: 18 }}>
                                  {j.citations.map((c, i) => (
                                    <li key={i}>
                                      <a href={c.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>
                                        {c.title || c.domain || c.url}
                                      </a>
                                      {c.domain ? <span className="text-muted">({c.domain})</span> : null}
                                    </li>
                                  ))}
                                </ul>
                              ) : <div className="text-xs text-muted">{t('workbench.adminGateway.retNoCite', { defaultValue: '(无引用)' })}</div>}
                            </>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          )}
        </table>
      </TableShell>

      <Pagination page={page} pageSize={pageSize} total={total}
                  onPageChange={setPage} onPageSizeChange={setPageSize} pageSizeOptions={[20, 50, 100, 200]} />
    </div>
  );
}
