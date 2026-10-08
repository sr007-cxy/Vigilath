// Proxy and egress IP tab: proxy library (register / test / delete) + account distribution by egress IP.
//
// Both tables have search; the proxy library also has a "bound / unbound" filter, and the IP distribution a "anomalies only" filter --
// anomaly = cooling down >0 or ≥2 accounts rate-limited within 24h (the platform is judging by IP; change the IP instead of adding accounts).
// Controls use Input / Button / FilterBar / PillTabs / TableShell, no more hand-written styles.
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { EngineProxy, EngineIpGroup } from '../../../services/aiTelemetryApi';
import { engineLabel } from '../../../constants/engines';
import {
  TableShell, StickyHead, EmptyRow, Button, Input, PillTabs,
} from '../../../components/ui';
import { FilterBar, FilterBarPrimary, FilterBarSecondary, SearchInput } from '../../../components/ui/FilterBar';

type ProxyFilter = 'all' | 'bound' | 'unbound';
type IpFilter = 'all' | 'abnormal';

export function ProxyTab({
  proxies, ipGroups, proxyForm, setProxyForm, proxyBusy, createProxy, testProxy, deleteProxy,
}: {
  proxies: EngineProxy[];
  ipGroups: EngineIpGroup[];
  proxyForm: { label: string; proxy_url: string; note: string };
  setProxyForm: (fn: (f: { label: string; proxy_url: string; note: string }) => { label: string; proxy_url: string; note: string }) => void;
  proxyBusy: boolean;
  createProxy: () => void;
  testProxy: (p: EngineProxy) => void;
  deleteProxy: (p: EngineProxy) => void;
}) {
  const { t } = useTranslation();
  const [pq, setPq] = useState('');
  const [pf, setPf] = useState<ProxyFilter>('all');
  const [iq, setIq] = useState('');
  const [ipf, setIpf] = useState<IpFilter>('all');

  const shownProxies = useMemo(() => {
    const kw = pq.trim().toLowerCase();
    return proxies.filter(p =>
      (pf === 'all' || (pf === 'bound' ? p.bound_count > 0 : p.bound_count === 0))
      && (!kw || p.label.toLowerCase().includes(kw) || p.proxy_url.toLowerCase().includes(kw)
          || (p.last_exit_ip || '').includes(kw) || (p.note || '').toLowerCase().includes(kw)));
  }, [proxies, pq, pf]);

  const isAbnormal = (g: EngineIpGroup) => (g.cooling || 0) > 0 || (g.muted_24h || 0) >= 2;
  const shownIps = useMemo(() => {
    const kw = iq.trim().toLowerCase();
    return ipGroups.filter(g =>
      (ipf === 'all' || isAbnormal(g))
      && (!kw || g.exit_ip.includes(kw) || engineLabel(g.engine).toLowerCase().includes(kw)));
  }, [ipGroups, iq, ipf]);

  return (
    // The two tables share the vertical height: each block is flex-auto min-h-0 -- shown by content when it fits, shrinking proportionally by content when it doesn't,
    // with the bigger table taking more. Headers (title / add row / filters) are shrink-0, tables flex-1 min-h-0 with inner scrolling.
    <div className="h-full flex flex-col gap-4">
      {/* ── Proxy library ── */}
      <section className="flex-auto min-h-0 flex flex-col gap-3">
        <div className="shrink-0">
        <h2 className="text-sm font-medium text-primary">{t('workbench.adminWorkers.proxyPoolTitle', { defaultValue: '代理库' })}</h2>
        <p className="text-[11px] text-muted mb-2">{t('workbench.adminWorkers.proxyPoolHint', {
          defaultValue: '登记后在账号行下拉里选;改这里的地址,绑它的账号自动跟着变' })}</p>
        {/* Add one: self-test first, then store */}
        <div className="flex items-center gap-2 flex-wrap mb-3">
          <Input size="sm" value={proxyForm.label} className="!w-[200px]"
                 onChange={v => setProxyForm(f => ({ ...f, label: v }))}
                 placeholder={t('workbench.adminWorkers.proxyLabel', { defaultValue: '名称(如 越南-203.0.113.10)' })} />
          <Input size="sm" value={proxyForm.proxy_url} className="!w-[340px]"
                 onChange={v => setProxyForm(f => ({ ...f, proxy_url: v }))}
                 placeholder="http://user:pass@host:port" />
          <Input size="sm" value={proxyForm.note} className="!w-[160px]"
                 onChange={v => setProxyForm(f => ({ ...f, note: v }))}
                 placeholder={t('workbench.adminWorkers.proxyNote', { defaultValue: '备注(可空)' })} />
          <Button size="sm" variant="solid" loading={proxyBusy} onClick={createProxy}>
            {proxyBusy
              ? t('workbench.adminWorkers.proxyTesting', { defaultValue: '自检中…' })
              : t('workbench.adminWorkers.proxyAdd', { defaultValue: '自检并入库' })}
          </Button>
        </div>

        <FilterBar>
          <FilterBarPrimary>
            <SearchInput value={pq} onChange={setPq} className="flex-1 min-w-[200px] max-w-md"
                         placeholder={t('workbench.adminWorkers.proxySearch', { defaultValue: '搜名称 / 地址 / 出口 IP / 备注' })} />
          </FilterBarPrimary>
          <FilterBarSecondary>
            <PillTabs value={pf} onChange={setPf} items={[
              { value: 'all', label: t('workbench.adminWorkers.filterAll', { defaultValue: '全部' }), count: proxies.length },
              { value: 'bound', label: t('workbench.adminWorkers.proxyBound', { defaultValue: '有绑定' }), count: proxies.filter(p => p.bound_count > 0).length },
              { value: 'unbound', label: t('workbench.adminWorkers.proxyUnbound', { defaultValue: '未绑定' }), count: proxies.filter(p => p.bound_count === 0).length },
            ]} />
          </FilterBarSecondary>
        </FilterBar>
        </div>

        <div className="flex-1 min-h-0">
          <TableShell maxHeight={false} className="max-h-full">
            <table className="w-full text-xs">
              <StickyHead>
                <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.proxyColLabel', { defaultValue: '名称' })}</th>
                <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.proxyColUrl', { defaultValue: '地址(密码打码)' })}</th>
                <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{t('workbench.adminWorkers.proxyColExit', { defaultValue: '出口 IP / 国家' })}</th>
                <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.proxyColBound', { defaultValue: '绑定数' })}</th>
                <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.proxyColAct', { defaultValue: '操作' })}</th>
              </StickyHead>
              {shownProxies.length === 0 ? (
                <EmptyRow colSpan={5}
                  title={proxies.length === 0
                    ? t('workbench.adminWorkers.proxyEmpty', { defaultValue: '还没有代理' })
                    : t('workbench.adminWorkers.noMatch', { defaultValue: '这个筛选下没有结果' })}
                  hint={proxies.length === 0 ? t('workbench.adminWorkers.proxyEmptyHint', { defaultValue: '在上面登记一条,自检通过后入库。' }) : undefined} />
              ) : (
                <tbody>
                  {shownProxies.map(p => (
                    <tr key={p.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td className="px-3 py-2.5 text-primary whitespace-nowrap">{p.label}</td>
                      <td className="px-3 py-2.5 text-secondary tabular-nums truncate max-w-[24rem]" title={p.proxy_url}>{p.proxy_url}</td>
                      <td className="px-3 py-2.5 text-secondary whitespace-nowrap tabular-nums">
                        {p.last_exit_ip ? `${p.last_exit_ip} (${p.last_country || '?'})` : '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{p.bound_count}</td>
                      <td className="px-3 py-2.5 text-right whitespace-nowrap">
                        <div className="inline-flex gap-1">
                          <Button size="sm" variant="ghost" onClick={() => testProxy(p)}>
                            {t('workbench.adminWorkers.proxyTest', { defaultValue: '测试' })}
                          </Button>
                          <Button size="sm" variant="danger" onClick={() => deleteProxy(p)}>
                            {t('workbench.adminWorkers.proxyDelete', { defaultValue: '删除' })}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </TableShell>
        </div>
      </section>

      {/* ── Distribution by egress IP ── */}
      {ipGroups.length > 0 && (
        <section className="flex-auto min-h-0 flex flex-col gap-3">
          <div className="shrink-0">
          <h2 className="text-sm font-medium text-primary">{t('workbench.adminWorkers.byIpTitle', { defaultValue: '按出口 IP 分布' })}</h2>
          <p className="text-[11px] text-muted mb-2">{t('workbench.adminWorkers.byIpHint', { defaultValue: '密度越高越易被风控;同一出口 24h 内 ≥2 个号被限流 = 平台在按 IP 判,该换 IP 而不是补号' })}</p>
          <FilterBar>
            <FilterBarPrimary>
              <SearchInput value={iq} onChange={setIq} className="flex-1 min-w-[200px] max-w-md"
                           placeholder={t('workbench.adminWorkers.ipSearch', { defaultValue: '搜出口 IP / 引擎' })} />
            </FilterBarPrimary>
            <FilterBarSecondary>
              <PillTabs value={ipf} onChange={setIpf} items={[
                { value: 'all', label: t('workbench.adminWorkers.filterAll', { defaultValue: '全部' }), count: ipGroups.length },
                { value: 'abnormal', label: t('workbench.adminWorkers.ipAbnormal', { defaultValue: '有异常' }), count: ipGroups.filter(isAbnormal).length },
              ]} />
            </FilterBarSecondary>
          </FilterBar>
          </div>
          <div className="flex-1 min-h-0">
            <TableShell maxHeight={false} className="max-h-full">
              <table className="w-full text-xs">
                <StickyHead>
                  <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{t('workbench.adminWorkers.colExitIp', { defaultValue: '出口 IP' })}</th>
                  <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colEngine', { defaultValue: '引擎' })}</th>
                  <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminWorkers.colIpAccounts', { defaultValue: '该引擎账号数' })}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colUsedToday', { defaultValue: '今日' })}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colCooling', { defaultValue: '冷却中' })}</th>
                  <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminWorkers.colMuted24h', { defaultValue: '24h 被限流' })}</th>
                  <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminWorkers.colIpTotal', { defaultValue: '该 IP 总账号' })}</th>
                </StickyHead>
                {shownIps.length === 0 ? (
                  <EmptyRow colSpan={7} title={t('workbench.adminWorkers.noMatch', { defaultValue: '这个筛选下没有结果' })} />
                ) : (
                  <tbody>
                    {shownIps.map((g, i) => (
                      <tr key={`${g.exit_ip}-${g.engine}-${i}`} style={{ borderTop: '1px solid var(--border-color)' }}>
                        <td className="px-3 py-2.5 text-primary tabular-nums whitespace-nowrap">{g.exit_ip}</td>
                        <td className="px-3 py-2.5 text-secondary">{engineLabel(g.engine)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-secondary">{g.accounts}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-secondary">{g.used_today}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums"
                            style={{ color: (g.cooling || 0) > 0 ? 'var(--warn-fg)' : 'var(--text-secondary)' }}>
                          {g.cooling || 0}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums"
                            style={{ color: (g.muted_24h || 0) >= 2 ? 'var(--danger-fg)' : 'var(--text-secondary)',
                                     fontWeight: (g.muted_24h || 0) >= 2 ? 500 : undefined }}
                            title={(g.muted_24h || 0) >= 2
                              ? t('workbench.adminWorkers.mutedIpHint', { defaultValue: '该出口已被平台标记:限流按 IP 判定,再往上放号只会连环被封。建议更换这个 IP。' })
                              : undefined}>
                          {g.muted_24h || 0}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums"
                            style={{ color: g.ip_account_total >= 2 ? 'var(--warn-fg)' : 'var(--text-secondary)' }}>
                          {g.ip_account_total}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                )}
              </table>
            </TableShell>
          </div>
        </section>
      )}
    </div>
  );
}
