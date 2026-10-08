import { useCallback, useEffect, useMemo, useState } from 'react';
import { LogIn, Plus, RefreshCw, Settings2, Trash2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  publishPoolApi,
  type PublishAccountPage,
  type PublishAccountRow,
  type PublishPlatformCapability,
  type PublishPoolStatus,
  type PublishProxyOption,
} from '../../services/publishPoolApi';
import { useAuth } from '../../contexts/AuthContext';
import { UiSelect } from '../../components/ui/Select';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { fmtTime } from '../../utils/datetime';
import {
  accountId as pluginAccountId,
  accountMedia,
  accountName,
  exportPluginSession,
  hasWechatSyncPlugin,
  isCurrentPluginAccount,
  readPluginAccounts,
  type SyncerAccount,
} from '../../services/wechatSyncPlugin';

const PAGE_SIZE = 10;
const REFRESH_MS = 10000;
const DEFAULT_CREDENTIAL_OWNER = '运营';
type AccountStatusFilter = '' | 'usable' | 'reauth' | 'manual';
type AddMode = 'plugin' | 'wxgzh_api';

const panelStyle = { background: 'var(--bg-card)', border: '1px solid var(--border-color)' };
const inputStyle = { background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' };
const ACCOUNT_STATUS_FILTERS: Record<Exclude<AccountStatusFilter, ''>, string[]> = {
  usable: ['active', 'warming', 'leased', 'rate_limited'],
  reauth: ['quarantined', 'expired', 'pending_auth'],
  manual: ['unschedulable'],
};
const REAUTH_STATUSES = new Set(ACCOUNT_STATUS_FILTERS.reauth);

function accountMatchesStatusFilter(row: PublishAccountRow, filter: AccountStatusFilter) {
  return !filter || ACCOUNT_STATUS_FILTERS[filter].includes(row.effective_status || row.status);
}

function needsReauth(row: PublishAccountRow) {
  return REAUTH_STATUSES.has(row.effective_status || row.status);
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    active: '可用', warming: '可用', quarantined: '掉线', unschedulable: '停止调度',
    expired: '已过期', rate_limited: '冷却中', leased: '使用中', disabled: '已停用',
    pending_auth: '待授权', retired: '已删除',
  };
  return labels[status] || status;
}

function statusColor(status: string) {
  if (status === 'active') return '#10b981';
  if (status === 'warming') return '#10b981';
  if (['quarantined', 'unschedulable', 'expired'].includes(status)) return '#f87171';
  return 'var(--text-secondary)';
}

function storageCookieSummary(storageState: { cookies?: unknown[] } | undefined) {
  return (Array.isArray(storageState?.cookies) ? storageState.cookies : []).flatMap(cookie => {
    if (!cookie || typeof cookie !== 'object' || Array.isArray(cookie)) return [];
    const row = cookie as Record<string, unknown>;
    return typeof row.name === 'string'
      ? [{ name: row.name, domain: typeof row.domain === 'string' ? row.domain : '' }]
      : [];
  });
}

function accountLabel(account: { id: number; account_handle?: string | null }) {
  return account.account_handle || `#${account.id}`;
}

function pluginAccountLabel(acc: SyncerAccount) {
  return accountMedia(acc) || acc.title || acc.uid || acc.type;
}

export function AccountCenter() {
  const token = localStorage.getItem('token') || '';
  const { isAdmin } = useAuth();
  const { t } = useTranslation();
  const [platforms, setPlatforms] = useState<PublishPlatformCapability[]>([]);
  const [poolStatus, setPoolStatus] = useState<PublishPoolStatus[]>([]);
  const [accounts, setAccounts] = useState<PublishAccountPage | null>(null);
  const [platform, setPlatform] = useState('');
  const [status, setStatus] = useState<AccountStatusFilter>('');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [rebindAccount, setRebindAccount] = useState<PublishAccountRow | null>(null);
  const [addPlatformHint, setAddPlatformHint] = useState('');
  const [form, setForm] = useState({
    appid: '',
    appsecret: '',
  });

  const pluginPlatformAliases = useMemo<Record<string, string[]>>(() => ({
    baijia: ['baijiahao'],
    wxgzh: ['weixin'],
    xhs: ['xiaohongshu'],
  }), []);
  const pluginExportPlatform = useCallback((platformCode: string) =>
    pluginPlatformAliases[platformCode]?.[0] || platformCode, [pluginPlatformAliases]);

  const reload = useCallback(async () => {
    try {
      const [pl, ps, ac] = await Promise.all([
        publishPoolApi.platforms(token),
        publishPoolApi.poolStatus(token),
        publishPoolApi.accounts(token, status
          ? { platform, pageSize: 200 }
          : { platform, page, pageSize: PAGE_SIZE }),
      ]);
      const filteredAccounts = status ? ac.items.filter(row => accountMatchesStatusFilter(row, status)) : ac.items;
      const pagedAccounts = status
        ? {
            ...ac,
            items: filteredAccounts.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
            total: filteredAccounts.length,
            page,
            page_size: PAGE_SIZE,
          }
        : ac;
      setPlatforms(pl.items);
      setPoolStatus(ps);
      setAccounts(pagedAccounts);
      setError(null);
      if (pagedAccounts.items.length === 0 && pagedAccounts.page > 1) setPage(1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token, platform, status, page]);

  useEffect(() => {
    void reload();
    const id = window.setInterval(() => void reload(), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  const platformMap = useMemo(() => new Map(platforms.map(p => [p.code, p])), [platforms]);
  const pluginToPoolPlatform = useMemo(() => {
    const out = new Map<string, string>();
    for (const p of platforms) {
      out.set(p.code, p.code);
      for (const alias of pluginPlatformAliases[p.code] || []) out.set(alias, p.code);
    }
    return out;
  }, [platforms, pluginPlatformAliases]);
  const visiblePool = platform ? poolStatus.filter(p => p.platform === platform) : poolStatus;
  const totals = visiblePool.reduce((a, p) => ({
    total: a.total + p.total, usable: a.usable + p.schedulable,
    abnormal: a.abnormal + p.quarantined + p.unschedulable,
  }), { total: 0, usable: 0, abnormal: 0 });
  const importPluginAccount = async (acc: SyncerAccount) => {
    const platformCode = pluginToPoolPlatform.get(acc.type) || acc.type;
    const pluginPlatform = pluginExportPlatform(platformCode);
    const session = await exportPluginSession(pluginPlatform, acc.accountKey);
    if (!session.storage_state?.cookies?.length) {
      throw new Error(`导入登录态失败：插件没有导出 cookies。平台=${platformCode}，请确认当前浏览器已登录该平台账号。`);
    }
    console.info('[publish-pool] plugin session exported', {
      platform: platformCode,
      pluginPlatform,
      cookieSummary: storageCookieSummary(session.storage_state),
    });
    const result = await publishPoolApi.importPluginSession(token, {
      platform: platformCode,
      storage_state: session.storage_state,
      credential_owner: DEFAULT_CREDENTIAL_OWNER,
      warmup_enabled: false,
      egress: 'host',
      account_handle: session.account_handle || pluginAccountLabel(acc) || undefined,
      account_key: session.account_key || acc.accountKey,
      user_agent: session.user_agent,
      ua_platform: session.ua_platform,
    });
    return result;
  };

  const importSelectedPluginAccounts = async (rows: SyncerAccount[]) => {
    if (rows.length === 0) {
      toast.error('请选择要导入的账号');
      return false;
    }
    setBusy(true);
    try {
      let renewed = 0;
      for (const row of rows) {
        const result = await importPluginAccount(row);
        if (result.renewed) renewed += 1;
      }
      toast.success(`已导入 ${rows.length} 个账号${renewed ? `，其中 ${renewed} 个为续期` : ''}；未绑定账号将在首次领取时自动绑定 worker`);
      await reload();
      return true;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const importWxgzhOfficialApi = async () => {
    const appid = form.appid.trim();
    const appsecret = form.appsecret.trim();
    if (!appid || !appsecret) {
      toast.error('请填写 AppID 和 AppSecret');
      return false;
    }
    setBusy(true);
    try {
      const result = await publishPoolApi.importWxgzhOfficialApi(token, {
        appid,
        appsecret,
        credential_owner: DEFAULT_CREDENTIAL_OWNER,
        warmup_enabled: false,
        egress: 'host',
      });
      toast.success(`公众号「${accountLabel(result.account)}」已${result.renewed ? '续期' : '加入发布账号池'}；未绑定账号将在首次领取时自动绑定 worker`);
      setForm({ appid: '', appsecret: '' });
      await reload();
      return true;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const deleteAccount = async (row: PublishAccountRow) => {
    if (!(await uiConfirm(`删除账号「${accountLabel(row)}」？\n账号将软下线，历史发布记录会保留。`, { danger: true }))) return;
    setBusy(true);
    try {
      const result = await publishPoolApi.retireAccount(token, row.id);
      toast.success(`账号已删除${result.cancelled ? `，同时取消 ${result.cancelled} 个排队任务` : ''}`);
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const openAddDialog = (platformHint = '') => {
    setAddPlatformHint(platformHint);
    setAddOpen(true);
  };

  const openReauthDialog = (row: PublishAccountRow) => {
    openAddDialog(row.platform);
  };

  const saveInlineSchedule = async (
    row: PublishAccountRow,
    field: 'daily_cap' | 'cooldown_minutes',
    value: number,
  ) => {
    setBusy(true);
    try {
      await publishPoolApi.updateAccountSchedule(token, row.id, {
        [field]: value,
        reason: field === 'daily_cap' ? '账号中心内联修改今日配额' : '账号中心内联修改冷却时间',
      });
      toast.success(field === 'daily_cap' ? '今日配额已更新' : (value === 0 ? '发布冷却已关闭' : '发布冷却时间已更新'));
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const rebind = async (
    row: PublishAccountRow,
    payload: { owner_worker_id: number; proxy_id?: number; auto_proxy?: boolean; egress: 'proxy' | 'host'; reason: string },
    reinstate: boolean,
  ) => {
    setBusy(true);
    let rebound = false;
    try {
      await publishPoolApi.rebindWorker(token, row.id, payload);
      rebound = true;
      if (reinstate) await publishPoolApi.reinstateAccount(token, row.id, payload.reason);
      toast.success(reinstate ? '账号已重新绑定并恢复调度' : '账号已重新绑定');
      setRebindAccount(null);
      await reload();
      return true;
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error(rebound ? `重新绑定已完成，但恢复调度失败：${message}` : message);
      await reload();
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-4">
        <div>
          <h1 className="text-xl font-semibold text-primary">{t('workbench.accountCenter.heading', { defaultValue: '账号中心' })}</h1>
          <p className="text-xs text-muted mt-1">管理多平台发布账号、登录状态与异常处置。每 10 秒自动刷新。</p>
        </div>
      </div>
      {error && <div className="mb-3 px-3 py-2 rounded text-xs text-red-400" style={panelStyle}>{error}</div>}
      <div className="flex items-center gap-2 border-b mb-5 pb-2" style={{ borderColor: 'var(--border-color)' }}>
        <button type="button" onClick={() => openAddDialog(platform)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs text-white"
          style={{ background: 'var(--accent-primary)' }}>
          <Plus size={14} />
          添加账号
        </button>
        <div className="ml-auto flex gap-2">
          <UiSelect value={platform} onChange={v => { setPlatform(v); setPage(1); }} className="px-2 py-1 rounded text-xs" style={inputStyle}
            options={[{ value: '', label: '全部平台' }, ...platforms.filter(p => p.enabled).map(p => ({ value: p.code, label: p.name }))]} />
          <UiSelect value={status} onChange={v => { setStatus(v as AccountStatusFilter); setPage(1); }} className="px-2 py-1 rounded text-xs" style={inputStyle}
            options={[{ value: '', label: '全部' }, { value: 'usable', label: '可用' }, { value: 'reauth', label: '需重新登录' }, { value: 'manual', label: '需人工处理' }]} />
        </div>
      </div>

      {loading ? <div className="py-16 text-center text-sm text-muted">加载中...</div> : <>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          {[['账号总数', totals.total, 'var(--text-primary)'], ['当前可用', totals.usable, '#10b981'], ['异常', totals.abnormal, '#f87171']].map(([label, value, color]) =>
            <div key={String(label)} className="rounded-lg px-4 py-3" style={panelStyle}><div className="text-xs text-muted">{label}</div><div className="text-2xl font-semibold mt-1" style={{ color: String(color) }}>{value}</div></div>)}
        </div>
        <AccountTable rows={accounts?.items || []} platforms={platformMap}
          onReauthAccount={openReauthDialog} onDeleteAccount={deleteAccount}
          onRebindAccount={setRebindAccount} onUpdateSchedule={saveInlineSchedule}
          canRebind={isAdmin} busy={busy} />
        <Pager page={page} total={accounts?.total || 0} onChange={setPage} />
      </>}

      {addOpen && <AddAccountDialog
        busy={busy}
        form={form}
        platformHint={addPlatformHint}
        onFormChange={setForm}
        pluginToPoolPlatform={pluginToPoolPlatform}
        platforms={platformMap}
        onClose={() => setAddOpen(false)}
        onImportPlugin={importSelectedPluginAccounts}
        onImportWxgzh={importWxgzhOfficialApi}
      />}
      {rebindAccount && <RebindAccountDialog
        account={rebindAccount}
        token={token}
        busy={busy}
        onClose={() => setRebindAccount(null)}
        onSubmit={(payload, reinstate) => rebind(rebindAccount, payload, reinstate)}
      />}
    </div>
  );
}

function RebindAccountDialog({ account, token, busy, onClose, onSubmit }: {
  account: PublishAccountRow;
  token: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (
    payload: { owner_worker_id: number; proxy_id?: number; auto_proxy?: boolean; egress: 'proxy' | 'host'; reason: string },
    reinstate: boolean,
  ) => Promise<boolean>;
}) {
  type EgressMode = 'host' | 'auto' | 'fixed';
  const [workerId, setWorkerId] = useState(account.owner_worker_id ? String(account.owner_worker_id) : '');
  const [mode, setMode] = useState<EgressMode>(account.egress === 'host' ? 'host' : account.proxy_id ? 'fixed' : 'auto');
  const [proxyId, setProxyId] = useState(account.proxy_id ? String(account.proxy_id) : '');
  const [reinstate, setReinstate] = useState(false);
  const [proxies, setProxies] = useState<PublishProxyOption[]>([]);

  useEffect(() => {
    void publishPoolApi.proxies(token)
      .then(result => setProxies(result.items.filter(proxy => !proxy.disabled)))
      .catch(e => toast.error(e instanceof Error ? e.message : String(e)));
  }, [token]);

  const confirm = async () => {
    const owner_worker_id = Number(workerId);
    if (!Number.isSafeInteger(owner_worker_id) || owner_worker_id <= 0) {
      toast.error('请输入有效的 Worker ID');
      return;
    }
    const selectedProxyId = Number(proxyId);
    if (mode === 'fixed' && (!Number.isSafeInteger(selectedProxyId) || selectedProxyId <= 0)) {
      toast.error('请选择固定代理');
      return;
    }
    await onSubmit({
      owner_worker_id,
      egress: mode === 'host' ? 'host' : 'proxy',
      ...(mode === 'auto' ? { auto_proxy: true } : {}),
      ...(mode === 'fixed' ? { proxy_id: selectedProxyId } : {}),
      reason: reinstate ? '账号中心重新绑定并恢复调度' : '账号中心重新绑定',
    }, reinstate);
  };

  return <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,.55)' }}>
    <div className="w-full max-w-lg rounded-xl p-5" style={panelStyle}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-primary">重新绑定账号</h2>
          <p className="text-xs text-muted mt-1">{accountLabel(account)} · {account.last_fail_reason || '调整执行 worker 或出口'}</p>
        </div>
        <button type="button" onClick={onClose} disabled={busy}
          className="inline-flex items-center justify-center w-8 h-8 rounded disabled:opacity-40"
          style={inputStyle} aria-label="关闭"><X size={16} /></button>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3">
        <label className="text-xs text-secondary">Worker ID
          <input value={workerId} onChange={e => setWorkerId(e.target.value.replace(/\D/g, ''))}
            inputMode="numeric" placeholder="请输入 Worker ID"
            className="mt-1 w-full px-3 py-2 rounded text-xs" style={inputStyle} />
        </label>
        <label className="text-xs text-secondary">出口方式
          <UiSelect value={mode} onChange={value => setMode(value as EgressMode)}
            className="mt-1 w-full px-3 py-2 rounded text-xs"
            options={[
              { value: 'host', label: '本机直连' },
              { value: 'auto', label: '自动分配代理' },
              { value: 'fixed', label: '固定代理' },
            ]} />
        </label>
        {mode === 'fixed' && <label className="text-xs text-secondary">代理
          <UiSelect value={proxyId} onChange={setProxyId} placeholder="请选择代理"
            className="mt-1 w-full px-3 py-2 rounded text-xs"
            options={proxies.map(proxy => ({
              value: String(proxy.id),
              label: `#${proxy.id} ${proxy.label}${proxy.last_exit_ip ? ` · ${proxy.last_exit_ip}` : ''}`,
            }))} />
        </label>}
        {account.status === 'unschedulable' && <label className="flex items-start gap-2 text-xs text-secondary">
          <input type="checkbox" checked={reinstate} onChange={e => setReinstate(e.target.checked)} />
          <span>已人工确认平台账号正常，重新绑定后恢复调度</span>
        </label>}
      </div>

      <div className="flex justify-end gap-2 mt-5">
        <button type="button" onClick={onClose} disabled={busy}
          className="px-3 py-1.5 rounded text-xs disabled:opacity-40" style={inputStyle}>取消</button>
        <button type="button" onClick={() => void confirm()} disabled={busy}
          className="px-3 py-1.5 rounded text-xs text-white disabled:opacity-40"
          style={{ background: 'var(--accent-primary)' }}>{busy ? '处理中...' : '确定'}</button>
      </div>
    </div>
  </div>;
}

function AddAccountDialog({ busy, form, platformHint, onFormChange, pluginToPoolPlatform, platforms, onClose, onImportPlugin, onImportWxgzh }: {
  busy: boolean;
  form: { appid: string; appsecret: string };
  platformHint: string;
  onFormChange: (value: { appid: string; appsecret: string }) => void;
  pluginToPoolPlatform: Map<string, string>;
  platforms: Map<string, PublishPlatformCapability>;
  onClose: () => void;
  onImportPlugin: (rows: SyncerAccount[]) => Promise<boolean>;
  onImportWxgzh: () => Promise<boolean>;
}) {
  const [mode, setMode] = useState<AddMode>('plugin');
  const [pluginRows, setPluginRows] = useState<SyncerAccount[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pluginPage, setPluginPage] = useState(1);
  const [loadingPlugin, setLoadingPlugin] = useState(false);
  const [pluginErr, setPluginErr] = useState<string | null>(null);

  const loadPlugin = useCallback(async (forceRefresh = false) => {
    setLoadingPlugin(true);
    setPluginErr(null);
    try {
      const rows = (await readPluginAccounts(forceRefresh))
        .filter(row => isCurrentPluginAccount(row) && row.draftOnly !== true);
      setPluginRows(rows);
      setSelected(prev => new Set([...prev].filter(key => rows.some(row => pluginAccountId(row) === key))));
      setPluginPage(1);
      if (rows.length === 0) {
        setPluginErr(hasWechatSyncPlugin()
          ? '插件已连接，但里面没有已登录的平台账号。'
          : '未检测到 WechatSync 插件。');
      }
    } catch (e) {
      setPluginRows([]);
      setPluginErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingPlugin(false);
    }
  }, []);

  useEffect(() => {
    void loadPlugin();
  }, [loadPlugin]);

  const pluginPages = Math.max(1, Math.ceil(pluginRows.length / PAGE_SIZE));
  const visiblePluginRows = pluginRows.slice((pluginPage - 1) * PAGE_SIZE, pluginPage * PAGE_SIZE);
  const picked = pluginRows.filter(row => selected.has(pluginAccountId(row)));
  const hasWxgzhPlugin = pluginRows.some(row => (pluginToPoolPlatform.get(row.type) || row.type) === 'wxgzh');
  const showWxgzhApi = platformHint === 'wxgzh' || hasWxgzhPlugin;
  useEffect(() => {
    if (!showWxgzhApi && mode === 'wxgzh_api') setMode('plugin');
  }, [showWxgzhApi, mode]);
  const toggle = (row: SyncerAccount) => {
    if (row.isAuthenticated === false) return;
    const key = pluginAccountId(row);
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };
  const confirm = async () => {
    const ok = mode === 'plugin' ? await onImportPlugin(picked) : await onImportWxgzh();
    if (ok) onClose();
  };

  return <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,.55)' }}>
    <div className="w-full max-w-3xl rounded-xl p-5" style={panelStyle}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-primary">添加账号</h2>
        <button type="button" onClick={onClose} disabled={busy}
          className="inline-flex items-center justify-center w-8 h-8 rounded disabled:opacity-40"
          style={inputStyle} aria-label="关闭">
          <X size={16} />
        </button>
      </div>

      <div className="inline-flex rounded-lg p-1 gap-1 mt-4"
           style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
        {[{ key: 'plugin', label: '插件导入' }, ...(showWxgzhApi ? [{ key: 'wxgzh_api', label: 'AppID/AppSecret 导入' }] : [])].map(item =>
          <button key={item.key} type="button" onClick={() => setMode(item.key as AddMode)}
            className="text-xs px-3 py-1.5 rounded-md transition-colors"
            style={{
              background: mode === item.key ? 'var(--bg-card)' : 'transparent',
              color: mode === item.key ? 'var(--text-primary)' : 'var(--text-muted)',
            }}>
            {item.label}
          </button>)}
      </div>

      {mode === 'plugin' ? <div className="mt-4 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] text-muted">仅显示插件支持直接发布的 10 个平台；导入时自动分配可用 IP。</p>
          <button type="button" disabled={busy || loadingPlugin} onClick={() => void loadPlugin(true)}
            className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded disabled:opacity-40"
            style={inputStyle}>
            <RefreshCw size={13} />
            {loadingPlugin ? '加载中...' : '刷新账号'}
          </button>
        </div>
        {pluginErr && <div className="rounded-md px-2.5 py-1.5 text-[11px]"
          style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.35)', color: '#f59e0b' }}>
          {pluginErr}
        </div>}
        <div className="space-y-2 min-h-[260px]">
          {visiblePluginRows.map(row => {
            const key = pluginAccountId(row);
            const active = selected.has(key);
            const platformCode = pluginToPoolPlatform.get(row.type) || row.type;
            const unsupported = !platforms.has(platformCode);
            const disabled = row.isAuthenticated === false || unsupported;
            const platformName = platforms.get(platformCode)?.name || accountName(row);
            const icon = row.icon || row.avatar || '';
            const loginUrl = row.loginUrl || row.home || '';
            return <label key={key}
              className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs"
              style={{
                background: disabled ? 'var(--bg-input)' : 'var(--bg-card)',
                border: `1px solid ${active ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                cursor: disabled ? 'not-allowed' : 'pointer',
                opacity: disabled ? 0.62 : 1,
              }}>
              <input type="checkbox" checked={active} disabled={busy || disabled} onChange={() => toggle(row)} />
              {icon && <img src={icon} alt="" className="w-4 h-4 rounded-sm" />}
              <span className="text-primary font-medium whitespace-nowrap">{platformName}</span>
              <span className="text-muted truncate">{pluginAccountLabel(row)}</span>
              <span className="ml-auto text-[11px] whitespace-nowrap" style={{ color: disabled ? '#ef4444' : 'var(--text-muted)' }}>
                {unsupported ? '平台未支持' : row.isAuthenticated === false && loginUrl
                  ? <a href={loginUrl} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} className="underline">未登录</a>
                  : row.isAuthenticated === false ? '未登录' : '已登录'}
              </span>
            </label>;
          })}
          {!loadingPlugin && visiblePluginRows.length === 0 && !pluginErr && (
            <div className="py-12 text-center text-xs text-muted">没有可导入的插件账号</div>
          )}
        </div>
        {pluginPages > 1 && <Pager page={pluginPage} total={pluginRows.length} onChange={setPluginPage} />}
      </div> : <div className="mt-4 grid grid-cols-1 gap-3">
        <input value={form.appid}
          onChange={e => onFormChange({ ...form, appid: e.target.value })}
          placeholder="公众号 AppID"
          className="px-3 py-2 rounded text-xs" style={inputStyle} />
        <input value={form.appsecret}
          onChange={e => onFormChange({ ...form, appsecret: e.target.value })}
          placeholder="公众号 AppSecret"
          type="password"
          autoComplete="off"
          className="px-3 py-2 rounded text-xs" style={inputStyle} />
        <p className="text-[11px] text-muted">凭据归属人默认保存为“运营”，并自动分配可用 IP。</p>
      </div>}

      <div className="flex justify-end gap-2 mt-5">
        <button type="button" onClick={onClose} disabled={busy} className="px-3 py-1.5 rounded text-xs disabled:opacity-40" style={inputStyle}>取消</button>
        <button type="button" onClick={() => void confirm()} disabled={busy || (mode === 'plugin' && picked.length === 0)}
          className="px-3 py-1.5 rounded text-xs text-white disabled:opacity-40" style={{ background: 'var(--accent-primary)' }}>
          {busy ? '导入中...' : '确定'}
        </button>
      </div>
    </div>
  </div>;
}

function InlineNumberInput({ value: initialValue, max, disabled, title, onCommit }: {
  value: number;
  max: number;
  disabled: boolean;
  title: string;
  onCommit: (value: number) => Promise<void>;
}) {
  const [value, setValue] = useState(String(initialValue));
  const [editing, setEditing] = useState(false);

  const commit = () => {
    const next = Number(value);
    if (!Number.isSafeInteger(next) || next < 0 || next > max) {
      setValue(String(initialValue));
      toast.error(`请输入 0-${max} 的整数`);
      return;
    }
    if (next !== initialValue) void onCommit(next);
  };

  return <input type="number" min={0} max={max} step={1} inputMode="numeric"
    value={editing ? value : String(initialValue)}
    disabled={disabled} title={title} aria-label={title}
    onFocus={e => { setValue(String(initialValue)); setEditing(true); e.currentTarget.select(); }}
    onChange={e => setValue(e.target.value.replace(/\D/g, ''))}
    onBlur={() => { setEditing(false); commit(); }}
    onKeyDown={e => {
      if (e.key === 'Enter') e.currentTarget.blur();
    }}
    className="w-16 px-2 py-1 rounded text-xs text-center tabular-nums disabled:opacity-50"
    style={inputStyle} />;
}

function AccountTable({ rows, platforms, onReauthAccount, onDeleteAccount, onRebindAccount, onUpdateSchedule, canRebind, busy }: {
  rows: PublishAccountRow[];
  platforms: Map<string, PublishPlatformCapability>;
  onReauthAccount: (row: PublishAccountRow) => void;
  onDeleteAccount: (row: PublishAccountRow) => void;
  onRebindAccount: (row: PublishAccountRow) => void;
  onUpdateSchedule: (row: PublishAccountRow, field: 'daily_cap' | 'cooldown_minutes', value: number) => Promise<void>;
  canRebind: boolean;
  busy: boolean;
}) {
  return <div className="rounded-lg overflow-x-auto" style={panelStyle}>
    <table className="w-full text-xs min-w-[1160px]"><thead><tr className="text-muted">
      {['平台', '账号', '状态', '今日配额', '发布冷却（分钟）', '最后使用', '过期时间', '出口 / 出口 IP', '执行 worker', '操作'].map(x => <th key={x} className="text-left px-3 py-2">{x}</th>)}
    </tr></thead><tbody>{rows.map(a => <tr key={a.id} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
      <td className="px-3 py-2 text-primary">{platforms.get(a.platform)?.name || a.platform}</td>
      <td className="px-3 py-2 text-secondary">{accountLabel(a)}</td>
      <td className="px-3 py-2" style={{ color: statusColor(a.effective_status) }}>{statusLabel(a.effective_status)}</td>
      <td className="px-3 py-2 text-secondary"><div className="flex items-center gap-1 tabular-nums">
        <span>{a.effective_cap === 0 ? '不限' : (a.remaining ?? 0)} /</span>
        <InlineNumberInput value={a.daily_cap ?? a.effective_cap} max={10000} disabled={busy}
          title="每日配额，0 表示不限；修改后按回车或移开焦点保存"
          onCommit={value => onUpdateSchedule(a, 'daily_cap', value)} />
      </div></td>
      <td className="px-3 py-2 text-secondary">
        <InlineNumberInput value={a.cooldown_minutes ?? a.effective_cooldown_minutes} max={10080} disabled={busy}
          title="每次发布后的最小间隔分钟数，0 表示不限制；修改后按回车或移开焦点保存"
          onCommit={value => onUpdateSchedule(a, 'cooldown_minutes', value)} />
      </td>
      <td className="px-3 py-2 text-secondary">{a.last_used_at ? fmtTime(a.last_used_at, false) : '-'}</td>
      <td className="px-3 py-2 text-secondary">{a.expires_at ? fmtTime(a.expires_at, false) : '-'}</td>
      <td className="px-3 py-2 text-secondary">{a.egress === 'host' ? '本机直连' : `代理 #${a.proxy_id || '-'}`}<span className="ml-1 text-muted">{a.exit_ip || '未探测'}</span></td>
      <td className="px-3 py-2 text-secondary tabular-nums">{a.owner_worker_id ?? <span className="text-amber-500">待首次领取</span>}</td>
      <td className="px-3 py-2"><div className="flex items-center gap-1 whitespace-nowrap">
        {canRebind && <button type="button" onClick={() => onRebindAccount(a)} disabled={busy}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs disabled:opacity-40"
          style={{ ...inputStyle, color: a.status === 'unschedulable' ? '#f59e0b' : 'var(--accent-primary)' }}>
          <Settings2 size={13} />
          {a.status === 'unschedulable' ? '处理异常' : '重新绑定'}
        </button>}
        {needsReauth(a) && <button type="button" onClick={() => onReauthAccount(a)} disabled={busy}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs disabled:opacity-40"
          style={{ ...inputStyle, color: 'var(--accent-primary)' }}>
          <LogIn size={13} />
          重新登录
        </button>}
        <button type="button" onClick={() => onDeleteAccount(a)} disabled={busy}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs disabled:opacity-40"
          style={{ ...inputStyle, color: '#dc2626' }}>
          <Trash2 size={13} />
          删除
        </button>
      </div></td>
    </tr>)}{rows.length === 0 && <tr><td colSpan={10} className="px-3 py-8 text-center text-muted">没有匹配的发布账号</td></tr>}</tbody></table>
  </div>;
}

function Pager({ page, total, onChange }: { page: number; total: number; onChange: (n: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return <div className="flex justify-end items-center gap-2 mt-3 text-xs text-muted">
    <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)} className="px-2 py-1 rounded disabled:opacity-40" style={inputStyle}>上一页</button>
    <span>{page} / {pages}</span>
    <button type="button" disabled={page >= pages} onClick={() => onChange(page + 1)} className="px-2 py-1 rounded disabled:opacity-40" style={inputStyle}>下一页</button>
  </div>;
}
