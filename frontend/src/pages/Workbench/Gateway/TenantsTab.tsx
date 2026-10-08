// Tenants tab: three overview cards + tenant table (local frontend filtering + client-side pagination).
//
// Before the rework there were 11 columns, and the action column had **6 buttons per row** (top up / quota / change purpose / issue new key / disable / unbind)
// all equally large and prominent. Now:
//   · 11 -> 9 columns: "Key count" merged as small text next to "Balance"; "Bound account" merged as small text in the name column
//   · The six actions go into a Menu, with the frequent top up and disable placed first
//   · Purpose / status use Badge (purpose used to be three hardcoded hex values)
//   · Filtering uses FilterBar: search + status pills (with counts) + purpose / engine dropdowns
//   · Client-side pagination, default 10/page; filter changes go back to page one, page number derived to prevent overflow
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { GatewayTenant } from '../../../services/aiTelemetryApi';
import { ENGINE_KEYS, engineLabel, canonicalEngines } from '../../../constants/engines';
import {
  TableShell, StickyHead, EmptyRow, Badge, StatusDot, UiSelect, PillTabs, Card,
} from '../../../components/ui';
import { Menu } from '../../../components/ui/Menu';
import { FilterBar, FilterBarPrimary, FilterBarSecondary, FilterField, SearchInput } from '../../../components/ui/FilterBar';
import { FILTER_CONTROL_CLASS, FILTER_CONTROL_STYLE } from '../../../components/ui/filterControls';
import { Pagination } from '../../../components/Pagination';
import { KIND_OPTIONS, kindLabel, kindTone, tierLabel } from './labels';

type StatusFilter = '' | 'active' | 'suspended';

export interface TenantActions {
  topUp: (id: number) => void;
  editQuota: (tt: GatewayTenant) => void;
  editKind: (tt: GatewayTenant) => void;
  reissue: (id: number) => void;
  toggleStatus: (tt: GatewayTenant) => void;
  unbindUser: (tt: GatewayTenant) => void;
}

export function TenantsTab({ tenants, actions }: { tenants: GatewayTenant[]; actions: TenantActions }) {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<StatusFilter>('');
  const [kind, setKind] = useState('');
  const [engine, setEngine] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Narrow by search + purpose + engine first; status pill counts are computed on this set -- the numbers must match what the table shows
  const narrowed = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return tenants.filter(tt =>
      (!kw || tt.name.toLowerCase().includes(kw) || (tt.bound_email || '').toLowerCase().includes(kw))
      && (!kind || (tt.kind || 'customer') === kind)
      // The allowlist includes the engine, or the tenant is fully open (engines empty)
      && (!engine || !tt.engines.length || tt.engines.includes(engine)));
  }, [tenants, q, kind, engine]);
  const filtered = useMemo(() => narrowed.filter(tt => !status || tt.status === status), [narrowed, status]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const shown = useMemo(() => filtered.slice((safePage - 1) * pageSize, safePage * pageSize), [filtered, safePage, pageSize]);

  const reset1 = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(1); };

  const totalCallsToday = tenants.reduce((s, x) => s + x.calls_today, 0);
  const totalCredits = tenants.reduce((s, x) => s + x.credit_balance, 0);

  return (
    <div className="space-y-4">
      {/* Three overview cards */}
      <div className="grid grid-cols-3 gap-3">
        <Stat label={t('workbench.adminGateway.statTenants', { defaultValue: '租户数' })} value={tenants.length} />
        <Stat label={t('workbench.adminGateway.statCallsToday', { defaultValue: '今日调用' })} value={totalCallsToday} />
        <Stat label={t('workbench.adminGateway.statCredits', { defaultValue: '余额合计(credits)' })} value={totalCredits} />
      </div>

      <FilterBar>
        <FilterBarPrimary>
          <SearchInput value={q} onChange={reset1(setQ)} className="flex-1 min-w-[200px] max-w-md"
                       placeholder={t('workbench.adminGateway.searchTenant', { defaultValue: '搜租户名称 / 绑定邮箱' })} />
        </FilterBarPrimary>
        <FilterBarSecondary
          right={<>
            <FilterField label={t('workbench.adminGateway.colKind', { defaultValue: '用途' })}>
              <UiSelect value={kind} onChange={reset1(setKind)} className={FILTER_CONTROL_CLASS} style={FILTER_CONTROL_STYLE}
                        options={[{ value: '', label: t('workbench.adminGateway.kindAll', { defaultValue: '全部' }) }, ...KIND_OPTIONS(t)]} />
            </FilterField>
            <FilterField label={t('workbench.adminGateway.colEngine', { defaultValue: '引擎' })}>
              <UiSelect value={engine} onChange={reset1(setEngine)} className={FILTER_CONTROL_CLASS} style={FILTER_CONTROL_STYLE}
                        options={[{ value: '', label: t('workbench.adminGateway.engineAll', { defaultValue: '全部' }) },
                                  ...ENGINE_KEYS.map(e => ({ value: e, label: engineLabel(e) }))]} />
            </FilterField>
          </>}>
          <PillTabs value={status} onChange={reset1(setStatus)} items={[
            { value: '', label: t('workbench.adminGateway.statusAll', { defaultValue: '全部' }), count: narrowed.length },
            { value: 'active', label: t('workbench.adminGateway.active', { defaultValue: '正常' }), count: narrowed.filter(x => x.status === 'active').length },
            { value: 'suspended', label: t('workbench.adminGateway.suspended', { defaultValue: '已停用' }), count: narrowed.filter(x => x.status !== 'active').length },
          ]} />
        </FilterBarSecondary>
      </FilterBar>

      <TableShell maxHeight={false}>
        <table className="w-full text-xs">
          <StickyHead>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colName', { defaultValue: '名称' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colKind', { defaultValue: '用途' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colTier', { defaultValue: '档位' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colEngines', { defaultValue: '引擎' })}</th>
            <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminGateway.colQuota', { defaultValue: '日配额' })}</th>
            <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminGateway.colBalanceKeys', { defaultValue: '余额 · 密钥' })}</th>
            <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminGateway.colCallsToday', { defaultValue: '今日调用' })}</th>
            <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminGateway.colStatus', { defaultValue: '状态' })}</th>
            <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminGateway.colActions', { defaultValue: '操作' })}</th>
          </StickyHead>
          {shown.length === 0 ? (
            <EmptyRow colSpan={9}
              title={tenants.length === 0
                ? t('workbench.adminGateway.noTenants', { defaultValue: '暂无租户' })
                : t('workbench.adminGateway.noMatch', { defaultValue: '这个筛选下没有租户' })}
              hint={tenants.length === 0 ? t('workbench.adminGateway.noTenantsHint', { defaultValue: '点右上角「新建租户」发第一把 API Key。' }) : undefined} />
          ) : (
            <tbody>
              {shown.map(tt => (
                <tr key={tt.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <span className="text-primary">{tt.name}</span>
                    {tt.bound_email && (
                      <span className="text-[10px] text-muted ml-1.5" title={t('workbench.adminGateway.colBoundAccount', { defaultValue: '绑定账号' })}>
                        {tt.bound_email}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <Badge tone={kindTone(tt.kind)}
                           title={[tt.source_key ? `source_key=${tt.source_key}` : '',
                                   tt.created_by ? t('workbench.adminGateway.createdBy', { defaultValue: '建于 {{by}}', by: tt.created_by }) : '']
                                   .filter(Boolean).join(' · ') || undefined}>
                      {kindLabel(t, tt.kind)}{tt.source_key ? ` · ${t('workbench.adminGateway.autoCreated', { defaultValue: '自动' })}` : ''}
                    </Badge>
                  </td>
                  <td className="px-3 py-2.5 text-secondary whitespace-nowrap">{tierLabel(t, tt.tier)}</td>
                  <td className="px-3 py-2.5 text-secondary truncate max-w-[16rem]"
                      title={tt.engines.length ? canonicalEngines(tt.engines).map(engineLabel).join('、') : undefined}>
                    {tt.engines.length ? canonicalEngines(tt.engines).map(engineLabel).join('、') : t('workbench.adminGateway.allEngines', { defaultValue: '全部' })}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-primary">{tt.daily_quota_default}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">
                    <span style={{ color: tt.credit_balance <= 0 ? 'var(--danger-fg)' : 'var(--text-primary)' }}>{tt.credit_balance}</span>
                    <span className="text-muted"> · {tt.active_keys}</span>
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-primary">{tt.calls_today}</td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    {tt.status === 'active'
                      ? <StatusDot tone="success">{t('workbench.adminGateway.active', { defaultValue: '正常' })}</StatusDot>
                      : <StatusDot tone="danger">{t('workbench.adminGateway.suspended', { defaultValue: '已停用' })}</StatusDot>}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <Menu label="···" triggerClassName="px-2 py-1 rounded-md text-sm" triggerStyle={{ background: 'var(--bg-tertiary)' }}
                      items={[
                        { label: t('workbench.adminGateway.topup', { defaultValue: '充值' }),
                          hint: t('workbench.adminGateway.topupHint', { defaultValue: '加减 credits,可填负数扣减' }),
                          onClick: () => actions.topUp(tt.id) },
                        { label: tt.status === 'active'
                            ? t('workbench.adminGateway.suspend', { defaultValue: '停用' })
                            : t('workbench.adminGateway.enable', { defaultValue: '启用' }),
                          onClick: () => actions.toggleStatus(tt), danger: tt.status === 'active' },
                        { label: t('workbench.adminGateway.editQuota', { defaultValue: '改日配额' }),
                          hint: t('workbench.adminGateway.editQuotaHint', { defaultValue: `当前 ${tt.daily_quota_default}` }),
                          onClick: () => actions.editQuota(tt) },
                        { label: t('workbench.adminGateway.editKind', { defaultValue: '改用途' }),
                          hint: t('workbench.adminGateway.editKindHint', { defaultValue: '对外客户 → 联调测试 → 自有系统 轮换' }),
                          onClick: () => actions.editKind(tt) },
                        { label: t('workbench.adminGateway.reissue', { defaultValue: '发新 Key' }),
                          hint: t('workbench.adminGateway.reissueHint', { defaultValue: '旧 key 仍有效' }),
                          onClick: () => actions.reissue(tt.id) },
                        ...(tt.user_id ? [{
                          label: t('workbench.adminGateway.unbind', { defaultValue: '解绑账号' }),
                          hint: t('workbench.adminGateway.unbindHint', { defaultValue: '解绑后该账号无法再登录租户门户' }),
                          onClick: () => actions.unbindUser(tt), danger: true,
                        }] : []),
                      ]} />
                  </td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </TableShell>

      <Pagination page={safePage} pageSize={pageSize} total={filtered.length}
                  onPageChange={setPage} onPageSizeChange={s => { setPageSize(s); setPage(1); }}
                  pageSizeOptions={[10, 20, 50]} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <div className="text-2xl font-semibold text-primary tabular-nums">{value}</div>
      <div className="text-xs text-muted mt-1">{label}</div>
    </Card>
  );
}
