// External gateway operations -- shell: data loading / tenant actions / API key display; the two tabs each in their own file.
//
// Before splitting it was a 651-line component: 32 useStates, 14 bare <button>s, 9 bare <input>s,
// hardcoded #2563eb ×4, 6 buttons per tenant table row, two full-height tables stacked vertically with the whole page scrolling.
// The splitting principle is the same as Worker management: **logic stays here (identical to before), presentation moves out.**
//
//   index.tsx              here -- reload / tenant actions / issuedKey / 15s refresh
//   TenantsTab.tsx         three overview cards + tenant table (local filtering + client-side pagination)
//   JobsTab.tsx            call log (server-side pagination + filters + expand to see response)
//   CreateTenantModal.tsx  create tenant (former inline form card -> Modal, form state kept inside)
//   labels.ts              localized labels and palette for tiers / purposes / task statuses, one source for three places
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi, type GatewayTenant, type GatewayJob } from '../../../services/aiTelemetryApi';
import { uiConfirm, uiPrompt } from '../../../components/ui/dialogs';
import { toast } from '../../../components/ui/toast';
import { PageHeader, PillTabs, Button, Card } from '../../../components/ui';
import { TenantsTab } from './TenantsTab';
import { JobsTab, type JobFilters } from './JobsTab';
import { CreateTenantModal } from './CreateTenantModal';
import { kindLabel } from './labels';
import { openVnetConsole } from '../openVnetConsole';

const REFRESH_MS = 15000;
type Tab = 'tenants' | 'jobs';

const EMPTY_FILTERS: JobFilters = { tenant: '', engine: '', status: '', source: '', since: '', until: '' };

export function AdminGateway() {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('tenants');
  const [tenants, setTenants] = useState<GatewayTenant[]>([]);
  const [jobs, setJobs] = useState<GatewayJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [issuedKey, setIssuedKey] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  // Call log filters (backend-side, since only the latest N + time range are fetched) + pagination (1-based)
  const [jf, setJf] = useState<JobFilters>(EMPTY_FILTERS);
  const [jobsLoading, setJobsLoading] = useState(false);  // Show loading only for filter-triggered refreshes, not silent polling
  const [jPage, setJPage] = useState(1);
  const [jPageSize, setJPageSize] = useState(50);
  const [jTotal, setJTotal] = useState(0);

  const reload = useCallback(() => {
    Promise.all([
      aiTelemetryApi.adminGatewayTenants(token),
      aiTelemetryApi.adminGatewayJobs(token, {
        tenantId: jf.tenant === '' ? undefined : Number(jf.tenant),
        engine: jf.engine || undefined,
        status: jf.status || undefined,
        source: jf.source || undefined,
        since: jf.since || undefined,
        until: jf.until || undefined,
        limit: jPageSize,
        offset: (jPage - 1) * jPageSize,
      }),
    ])
      .then(([ts, js]) => { setTenants(ts.tenants); setJobs(js.jobs); setJTotal(js.total ?? 0); setErr(null); })
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => { setLoading(false); setJobsLoading(false); });
  }, [token, jf, jPage, jPageSize]);

  useEffect(() => {
    reload();
    const id = setInterval(reload, REFRESH_MS);
    return () => clearInterval(id);
  }, [reload]);

  // Filter conditions changed (not the first time) -> give the log table a loading state, avoiding the illusion of "selected but nothing happened"; and back to page one
  const firstLoad = useRef(true);
  const setFilters = (patch: Partial<JobFilters>) => {
    if (firstLoad.current) firstLoad.current = false;
    setJobsLoading(true);
    setJf(f => ({ ...f, ...patch }));
    setJPage(1);
  };
  const resetFilters = () => { setJobsLoading(true); setJf(EMPTY_FILTERS); setJPage(1); };

  const fail = (e: unknown) => setErr(e instanceof Error ? e.message : String(e));

  const unbindUser = async (tt: GatewayTenant) => {
    if (!(await uiConfirm(t('workbench.adminGateway.unbindConfirm', { defaultValue: '解绑该租户的平台账号?解绑后该账号无法再登录租户门户。' }), { danger: true }))) return;
    try { await aiTelemetryApi.adminGatewayUpdateTenant(tt.id, { unbind_user: true }, token); reload(); }
    catch (e) { fail(e); }
  };
  const topUp = async (tid: number) => {
    const v = await uiPrompt(t('workbench.adminGateway.topupPrompt', { defaultValue: '充值额度(credits),可填负数扣减:' }), { defaultValue: '100' });
    if (v == null) return;
    const amount = parseInt(v, 10);
    if (!Number.isFinite(amount) || amount === 0) return;
    try { await aiTelemetryApi.adminGatewayTopUp(tid, amount, token); reload(); }
    catch (e) { fail(e); }
  };
  const editQuota = async (tt: GatewayTenant) => {
    const v = await uiPrompt(t('workbench.adminGateway.quotaPrompt', { defaultValue: '每引擎默认日配额:' }), { defaultValue: String(tt.daily_quota_default) });
    if (v == null) return;
    const n = parseInt(v, 10);
    if (!Number.isFinite(n)) return;
    try { await aiTelemetryApi.adminGatewayUpdateTenant(tt.id, { daily_quota_default: n }, token); reload(); }
    catch (e) { fail(e); }
  };
  const toggleStatus = async (tt: GatewayTenant) => {
    const next = tt.status === 'active' ? 'suspended' : 'active';
    try { await aiTelemetryApi.adminGatewayUpdateTenant(tt.id, { status: next }, token); reload(); }
    catch (e) { fail(e); }
  };
  const reissue = async (tid: number) => {
    if (!(await uiConfirm(t('workbench.adminGateway.reissueConfirm', { defaultValue: '给该租户新发一把 API Key?旧 key 仍有效。' })))) return;
    try { const r = await aiTelemetryApi.adminGatewayReissueKey(tid, token); setIssuedKey(r.api_key); }
    catch (e) { fail(e); }
  };
  // Change purpose -- test tenants created by mistake go to "Integration testing", distinguishable at a glance in the list
  const editKind = async (tt: GatewayTenant) => {
    const cur = tt.kind || 'customer';
    const next = cur === 'customer' ? 'test' : cur === 'test' ? 'internal' : 'customer';
    if (!(await uiConfirm(t('workbench.adminGateway.kindConfirm', {
      defaultValue: '把「{{name}}」的用途改成「{{kind}}」?', name: tt.name, kind: kindLabel(t, next) })))) return;
    try { await aiTelemetryApi.adminGatewayUpdateTenant(tt.id, { kind: next }, token); reload(); }
    catch (e) { fail(e); }
  };

  const copyKey = () => {
    if (!issuedKey) return;
    navigator.clipboard?.writeText(issuedKey)
      .then(() => toast.success(t('common.copied', { defaultValue: '已复制' })))
      .catch(() => toast.error(t('common.copyFailed', { defaultValue: '复制失败,请手动选中' })));
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title={t('workbench.adminGateway.heading', { defaultValue: '对外网关运营' })}
        hint={t('workbench.adminGateway.subtitle', { defaultValue: '管理调用浏览器自动化能力的外部租户:配额、额度、调用流水。每 15 秒刷新。' })}
        actions={<div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => void openVnetConsole(token)}>
            {t('workbench.adminGateway.openVnetConsole', { defaultValue: '采集网络管理台 ↗' })}
          </Button>
          <Button size="sm" variant="solid" onClick={() => setShowCreate(true)}>
            {t('workbench.adminGateway.newTenant', { defaultValue: '新建租户' })}
          </Button>
        </div>}
      />

      {/* The API key is shown only once -- with a prominent border; gone once closed */}
      {issuedKey && (
        <Card className="text-xs" style={{ borderColor: 'var(--accent-primary)' }}>
          <div className="text-primary font-medium mb-1">
            {t('workbench.adminGateway.keyOnce', { defaultValue: 'API Key 已生成(仅此一次显示,请复制保存):' })}
          </div>
          <code className="text-primary break-all block mb-2">{issuedKey}</code>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={copyKey}>{t('common.copy', { defaultValue: '复制' })}</Button>
            <Button size="sm" variant="ghost" onClick={() => setIssuedKey(null)}>{t('common.close', { defaultValue: '关闭' })}</Button>
          </div>
        </Card>
      )}

      <PillTabs value={tab} onChange={setTab} items={[
        { value: 'tenants', label: t('workbench.adminGateway.tabTenants', { defaultValue: '租户' }), count: tenants.length },
        { value: 'jobs', label: t('workbench.adminGateway.tabJobs', { defaultValue: '调用流水' }), count: jTotal },
      ]} />

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading')}</div>
      ) : err ? (
        <div className="text-xs px-3 py-2 rounded-lg"
             style={{ color: 'var(--danger-fg)', background: 'var(--danger-bg)', border: '1px solid var(--danger-border)' }}>{err}</div>
      ) : tab === 'tenants' ? (
        <TenantsTab tenants={tenants} actions={{ topUp, editQuota, editKind, reissue, toggleStatus, unbindUser }} />
      ) : (
        <JobsTab tenants={tenants} jobs={jobs} total={jTotal} loading={jobsLoading}
                 filters={jf} setFilters={setFilters} resetFilters={resetFilters}
                 page={jPage} pageSize={jPageSize} setPage={setJPage}
                 setPageSize={s => { setJPageSize(s); setJPage(1); }} />
      )}

      <CreateTenantModal open={showCreate} onClose={() => setShowCreate(false)} token={token}
                         onCreated={key => { setIssuedKey(key); reload(); }} />
    </div>
  );
}
