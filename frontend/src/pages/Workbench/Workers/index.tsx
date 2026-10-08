// Worker management -- shell: data loading / state shared across tabs / four overlays; each tab body in its own file.
//
// Before splitting it was a 1440-line component: 23 useStates, 8 bare <table>s, 43 hardcoded color codes,
// with all four tabs inline. Splitting principle: **logic stays here (identical to before), presentation moves out.**
// The three authorization paths (password / SMS / QR scan) and self-heal are flows verified on physical devices; only the controls changed, not the logic.
//
//   index.tsx             here -- state, handlers, 10s refresh, overlays
//   OverviewTab.tsx       engine health + task queue
//   FleetTab.tsx          fleet (reworked: alerts consolidated into one column, version/hot sessions into the drawer, sorting and filtering)
//   AccountsTab.tsx       account pool (three authorization paths + server-paginated table)
//   ProxyTab.tsx          proxy library + egress IP distribution
//   WorkerDetailDrawer.tsx  machine details
//   fleet.ts              pure fleet logic (alert derivation / sorting / filtering)
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  aiTelemetryApi,
  type BrowserWorker, type WorkerQueueStats, type EngineSessionPage, type EngineSessionRow,
  type EngineProxy, type EngineIpGroup, type EngineHealth, type SelectorProposal,
} from '../../../services/aiTelemetryApi';
import { engineLabel, canonicalEngines } from '../../../constants/engines';
import { uiConfirm } from '../../../components/ui/dialogs';
import { toast } from '../../../components/ui/toast';
import { PageHeader, PillTabs, Button, Input, Modal } from '../../../components/ui';
import { OverviewTab } from './OverviewTab';
import { FleetTab } from './FleetTab';
import { AccountsTab, SESSION_PAGE_SIZE } from './AccountsTab';
import { ProxyTab } from './ProxyTab';
import { WorkerDetailDrawer, type DetailState } from './WorkerDetailDrawer';
import { openVnetConsole } from '../openVnetConsole';

const REFRESH_MS = 10000;

type WorkerTab = 'overview' | 'fleet' | 'accounts' | 'proxy';
type SessStatus = '' | 'active' | 'quarantined' | 'expired';

export function AdminWorkers() {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [tab, setTab] = useState<WorkerTab>('overview');
  const [workers, setWorkers] = useState<BrowserWorker[]>([]);
  const [stats, setStats] = useState<WorkerQueueStats | null>(null);
  const [sessions, setSessions] = useState<EngineSessionPage | null>(null);
  const [proxies, setProxies] = useState<EngineProxy[]>([]);
  const [proxyForm, setProxyForm] = useState({ label: '', proxy_url: '', note: '' });
  const [proxyBusy, setProxyBusy] = useState(false);
  const [ipGroups, setIpGroups] = useState<EngineIpGroup[]>([]);
  const [health, setHealth] = useState<{ checked_at: string | null; engines: EngineHealth[] }>({ checked_at: null, engines: [] });
  const [sessPage, setSessPage] = useState(1);
  const [sessEngine, setSessEngine] = useState('');
  const [sessStatus, setSessStatus] = useState<SessStatus>('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busyUid, setBusyUid] = useState<string | null>(null);
  const [authForm, setAuthForm] = useState({ engine: 'deepseek', identifier: '', password: '' });
  const [authBusy, setAuthBusy] = useState(false);
  const [authMsg, setAuthMsg] = useState<string | null>(null);
  const [qr, setQr] = useState<{ open: boolean; engine: string; sid: string | null; img: string | null; status: string }>(
    { open: false, engine: 'wenxin', sid: null, img: null, status: '' });
  const [heal, setHeal] = useState<{ open: boolean; engine: string; loading: boolean; proposal: SelectorProposal | null; raw: string; msg: string; validated: boolean; valText: string }>(
    { open: false, engine: '', loading: false, proposal: null, raw: '', msg: '', validated: false, valText: '' });
  // Machine detail drawer: event timeline + failure profile + runtime params (changes take effect on the next heartbeat)
  const [detail, setDetail] = useState<DetailState>(
    { open: false, uid: '', host: '', diag: null, policy: null, draft: {}, note: '', busy: false });
  // Phone verification code authorization (qwen has no password login; the other path besides QR scan): send code -> human types in the SMS code -> log in and join the pool
  const [sms, setSms] = useState<{ open: boolean; engine: string; cc: string; phone: string; code: string; sid: string | null;
                                   shot: string | null; status: string; msg: string }>(
    { open: false, engine: 'qwen', cc: '86', phone: '', code: '', sid: null, shot: null, status: '', msg: '' });

  const poolQuar = sessions?.quarantined_total ?? 0;
  const onlineCount = workers.filter(w => w.status === 'online').length;
  // How many online machines share the same egress. '-' is a placeholder left by early versions, not a real IP, and isn't grouped.
  const shareCount = useCallback((w: BrowserWorker) => {
    const ip = (w.exit_ip || '').trim();
    if (!ip || ip === '-') return 1;
    return workers.filter(x => x.status === 'online' && (x.exit_ip || '').trim() === ip).length;
  }, [workers]);
  const anyUnhealthy = health.engines.some(e => !e.ok) || poolQuar > 0;
  const alertCount = useMemo(() => workers.filter(w =>
    w.enabled && (w.idle_reason || Object.keys(w.breaker || {}).length
      || (w.hot_sessions || []).some(h => h.breaker_open || h.login_ok === false))).length, [workers]);

  const reload = useCallback(() => {
    Promise.all([
      aiTelemetryApi.adminListWorkers(token),
      aiTelemetryApi.adminWorkersQueueStats(token),
      aiTelemetryApi.adminListEngineSessions(token, sessPage, SESSION_PAGE_SIZE, sessEngine, sessStatus),
      aiTelemetryApi.adminEngineSessionsByIp(token).catch(() => ({ groups: [], ip_count: 0 })),
      aiTelemetryApi.adminEngineHealth(token).catch(() => ({ checked_at: null, engines: [] })),
      aiTelemetryApi.adminListProxies(token).catch(() => ({ items: [] })),
    ])
      .then(([ws, st, ss, byip, hl, px]) => {
        setWorkers(ws); setStats(st); setSessions(ss); setIpGroups(byip.groups || []); setHealth(hl);
        setProxies(px.items || []); setErr(null);
        // After rows are cleaned up the page number may overflow (empty page); go back to page one
        if (ss.items.length === 0 && ss.page > 1) setSessPage(1);
      })
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token, sessPage, sessEngine, sessStatus]);

  const closeSms = useCallback(() => {
    setSms(s => {
      if (s.sid && s.status !== 'done') void aiTelemetryApi.adminAuthorizeSmsPoll(token, s.sid, true).catch(() => undefined);
      return { ...s, open: false, sid: null };
    });
  }, [token]);
  const startSms = useCallback(async () => {
    const phone = sms.phone.replace(/\D/g, '');
    if (phone.length < 6) { setSms(s => ({ ...s, msg: '手机号不对' })); return; }
    setSms(s => ({ ...s, open: true, sid: null, shot: null, code: '', status: 'starting', msg: '' }));
    try {
      const r = await aiTelemetryApi.adminAuthorizeSmsStart(token, sms.engine, phone, sms.cc);
      if (r.session_id && r.status === 'code_sent') {
        // If the area code didn't switch, the code may have gone to a +86 number -- prompt the human to confirm against the screenshot rather than silently treating it as success
        const warn = (sms.cc !== '86' && r.country_ok === false) ? `区号可能没切到 +${sms.cc},请对着截图确认` : '';
        setSms(s => ({ ...s, sid: r.session_id!, shot: r.screenshot || null, status: 'code_sent', msg: warn }));
      } else if (r.session_id && r.status === 'captcha') {
        // Hit a slider: the page is still open on the server. kimi always shows a NetEase Yidun slider before sending the code (no automatic solving); a human drags it
        // on the server display via VNC and then the code is sent -- so this doesn't count as failure; keep the code input box.
        setSms(s => ({ ...s, sid: r.session_id!, shot: r.screenshot || null, status: 'captcha',
                        msg: r.captcha_manual
                          ? '发码前有滑块验证:请通过 VNC 在服务器浏览器里完成滑块,短信到达后在下面填码登录;可点「刷新截图」看进度'
                          : '撞了滑块/人机验证,看截图判断:换出口或稍后重试' }));
      } else {
        setSms(s => ({ ...s, sid: r.session_id || null, shot: r.screenshot || null,
                        status: 'error', msg: r.error || r.status || '发码失败' }));
      }
    } catch (e) { setSms(s => ({ ...s, status: 'error', msg: (e as Error)?.message || '失败' })); }
  }, [token, sms.engine, sms.phone, sms.cc]);
  // Refresh screenshot: the human drags the slider / completes the login directly in VNC, and progress is watched here; once logged in, join the pool and wrap up
  const refreshSms = useCallback(async () => {
    if (!sms.sid) return;
    try {
      const r = await aiTelemetryApi.adminAuthorizeSmsPoll(token, sms.sid, false);
      if (r.status === 'done') {
        setSms(s => ({ ...s, status: 'done', msg: r.uploaded ? '已入池' : `登录成功但入池失败:${r.error || ''}` }));
        void reload();
        setTimeout(() => setSms(s => ({ ...s, open: false, sid: null })), 1800);
      } else if (r.status === 'expired') {
        setSms(s => ({ ...s, status: 'error', msg: '会话已过期,请重新发码' }));
      } else {
        setSms(s => ({ ...s, shot: r.screenshot || s.shot,
                        status: r.captcha ? 'captcha' : 'code_sent',
                        msg: r.captcha ? s.msg : '滑块已过,填短信码登录' }));
      }
    } catch (e) { setSms(s => ({ ...s, msg: (e as Error)?.message || '刷新失败' })); }
  }, [token, sms.sid, reload]);
  const submitSms = useCallback(async () => {
    if (!sms.sid || !sms.code) return;
    setSms(s => ({ ...s, status: 'submitting', msg: '' }));
    try {
      const r = await aiTelemetryApi.adminAuthorizeSmsSubmit(token, sms.sid, sms.code);
      if (r.status === 'done') {
        setSms(s => ({ ...s, status: 'done', msg: r.uploaded ? `已入池:${r.account_handle || ''}` : `登录成功但入池失败:${r.error || ''}` }));
        void reload();
        setTimeout(() => setSms(s => ({ ...s, open: false, sid: null })), 1800);
      } else if (r.status === 'expired') {
        setSms(s => ({ ...s, status: 'error', msg: '会话已过期,请重新发码' }));
      } else {
        setSms(s => ({ ...s, status: 'code_sent', shot: r.screenshot || s.shot, msg: r.error || '没登上,看截图' }));
      }
    } catch (e) { setSms(s => ({ ...s, status: 'error', msg: (e as Error)?.message || '失败' })); }
  }, [token, sms.sid, sms.code, reload]);

  const startQr = useCallback(async () => {
    setQr(s => ({ ...s, open: true, sid: null, img: null, status: 'starting' }));
    try {
      const r = await aiTelemetryApi.adminAuthorizeQrStart(token, qr.engine);
      if (r.session_id && r.qr_image) setQr(s => ({ ...s, sid: r.session_id!, img: r.qr_image!, status: 'waiting' }));
      else setQr(s => ({ ...s, status: 'error:' + (r.error || '抓码失败') }));
    } catch (e) { setQr(s => ({ ...s, status: 'error:' + ((e as Error)?.message || '失败') })); }
  }, [token, qr.engine]);

  useEffect(() => {
    if (!qr.open || !qr.sid || qr.status === 'done') return;
    const id = setInterval(async () => {
      try {
        const r = await aiTelemetryApi.adminAuthorizeQrPoll(token, qr.sid!);
        if (r.status === 'done') {
          setQr(s => ({ ...s, status: 'done' }));
          reload();
          setTimeout(() => setQr(s => ({ ...s, open: false })), 1500);
        } else if (r.status === 'expired') {
          setQr(s => ({ ...s, status: 'expired' }));
        } else if (r.qr_image) {
          setQr(s => ({ ...s, img: r.qr_image! }));
        }
      } catch { /* Ignore polling failures, retry next time */ }
    }, 2500);
    return () => clearInterval(id);
  }, [qr.open, qr.sid, qr.status, token, reload]);

  const doAuthorize = useCallback(async () => {
    if (!authForm.identifier || !authForm.password) return;
    setAuthBusy(true); setAuthMsg(null);
    try {
      const r = await aiTelemetryApi.adminAuthorizeAccount(token, authForm.engine, authForm.identifier, authForm.password);
      setAuthMsg(r.ok
        ? `✅ 授权成功 ${r.account_handle || ''}${r.uploaded ? ' · 已入池' : ''}`
        : `❌ ${r.error || '失败'}`);
      if (r.ok) { setAuthForm(f => ({ ...f, identifier: '', password: '' })); reload(); }
    } catch (e) {
      setAuthMsg(`❌ ${(e as Error)?.message || '请求失败'}`);
    } finally { setAuthBusy(false); }
  }, [token, authForm, reload]);

  useEffect(() => {
    reload();
    const id = setInterval(reload, REFRESH_MS);
    return () => clearInterval(id);
  }, [reload]);

  const act = async (uid: string, action: 'enable' | 'disable' | 'drain') => {
    setBusyUid(uid);
    try {
      await aiTelemetryApi.adminWorkerAction(uid, action, token);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyUid(null);
    }
  };

  // Remove ghost rows: long-offline registration records (production has a row whose heartbeat stopped on 2026-08-13 with exit_ip '-',
  // developer-ThinkPad-L13, sharing a name with the live one). Historical tasks are untouched.
  const forget = async (w: BrowserWorker) => {
    const name = w.hostname || w.label || w.worker_uid.slice(0, 8);
    if (!await uiConfirm(`把「${name}」从注册表里移除?\n\n它跑过的历史任务会保留,` +
                         `只是不再出现在这个列表里。机器要是又上线,会自动重新注册。`)) return;
    setBusyUid(w.worker_uid);
    try {
      await aiTelemetryApi.adminForgetWorker(token, w.worker_uid);
      toast.success('已移除');
      reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally { setBusyUid(null); }
  };

  const openDetail = async (w: BrowserWorker) => {
    setDetail({
      open: true, uid: w.worker_uid,
      host: w.hostname || w.label || w.worker_uid.slice(0, 8),
      diag: null, policy: null, draft: {}, note: '', busy: false,
    });
    try {
      const [diag, policy] = await Promise.all([
        aiTelemetryApi.adminWorkerDiagnostics(token, w.worker_uid, 3),
        aiTelemetryApi.adminWorkerPolicy(token, w.worker_uid),
      ]);
      const draft: Record<string, string> = {};
      for (const k of Object.keys(policy.override || {})) draft[k] = String(policy.override[k]);
      setDetail(s => ({ ...s, diag, policy, draft, note: policy.note || '' }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      setDetail(s => ({ ...s, open: false }));
    }
  };

  const saveDetail = async () => {
    if (!detail.policy) return;
    // Empty = this item follows the global setting (remove it from the overrides), not "set to 0"
    const config: Record<string, number> = {};
    for (const [k, v] of Object.entries(detail.draft)) {
      const s = (v ?? '').trim();
      if (!s) continue;
      const n = Number(s);
      if (!Number.isFinite(n)) { toast.error(`${k} 必须是数字`); return; }
      config[k] = n;
    }
    setDetail(s => ({ ...s, busy: true }));
    try {
      const policy = await aiTelemetryApi.adminSaveWorkerPolicy(token, detail.uid, { config, note: detail.note });
      setDetail(s => ({ ...s, policy, busy: false }));
      toast.success('已保存,下一次心跳(最多 30 秒)生效');
      reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      setDetail(s => ({ ...s, busy: false }));
    }
  };

  const patchSession = async (
    id: number, patch: { disabled?: boolean; daily_cap?: number; priority?: number; proxy_override?: string; proxy_id?: number },
  ) => {
    try {
      await aiTelemetryApi.adminPatchEngineSession(token, id, patch);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  // Bind a proxy to an account: pick one from the proxy library (-1 = unbind); the backend resolves it into a string at check-out.
  const bindProxyId = (s: EngineSessionRow, proxyId: number) => { patchSession(s.id, { proxy_id: proxyId }); };

  // Proxy library: add (self-test first, then store)
  const createProxy = async () => {
    const label = proxyForm.label.trim();
    const url = proxyForm.proxy_url.trim();
    if (!label || !url) { setErr('代理名称和地址都要填'); return; }
    setProxyBusy(true);
    try {
      const r = await aiTelemetryApi.adminTestProxy(token, url);
      const warn = r.country && r.country.toUpperCase() !== 'CN' ? `\n⚠️ 非中国 IP(${r.country})。` : '';
      const msg = r.ok ? `出口 IP:${r.ip} (${r.country || '?'})${warn}\n\n确认入库?`
                       : `代理测试失败:${r.error || r.status}\n\n仍要入库?`;
      if (!(await uiConfirm(msg))) { setProxyBusy(false); return; }
      await aiTelemetryApi.adminCreateProxy(token, { label, proxy_url: url, note: proxyForm.note.trim() || undefined });
      setProxyForm({ label: '', proxy_url: '', note: '' });
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setProxyBusy(false); }
  };
  const testProxy = async (p: EngineProxy) => {
    try {
      const r = await aiTelemetryApi.adminTestProxyById(token, p.id);
      if (r.ok) toast.success(`${p.label}\n出口 IP:${r.ip} (${r.country || '?'})`);
      else toast.error(`${p.label}\n测试失败:${r.error || r.status}`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const deleteProxy = async (p: EngineProxy) => {
    if (!(await uiConfirm(`删除代理「${p.label}」?\n绑它的 ${p.bound_count} 个账号会被解绑(回退默认出口)。`, { danger: true }))) return;
    try { await aiTelemetryApi.adminDeleteProxy(token, p.id); reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  const doHeal = async (engine: string) => {
    setHeal({ open: true, engine, loading: true, proposal: null, raw: '', msg: '', validated: false, valText: '' });
    try {
      const r = await aiTelemetryApi.adminHealEngine(token, engine);
      const v = r.validation;
      setHeal(h => ({ ...h, loading: false, proposal: r.proposal, raw: r.llm_raw || '',
        validated: r.validated,
        valText: v ? (r.validated ? `✅ 验证通过 答${v.ans_len}/引${v.cites}` : `❌ 验证失败 ${v.error || '无答案'}`) : '',
        msg: r.probe_error ? `探针异常: ${r.probe_error}` : '' }));
    } catch (e) {
      setHeal(h => ({ ...h, loading: false, msg: `失败: ${(e as Error)?.message || ''}` }));
    }
  };
  const doHealApply = async () => {
    if (!heal.proposal) return;
    setHeal(h => ({ ...h, loading: true }));
    try {
      await aiTelemetryApi.adminHealEngineApply(token, heal.engine, heal.proposal);
      setHeal(h => ({ ...h, loading: false, msg: '✅ 已应用到中心配置,全队下次查询生效' }));
    } catch (e) {
      setHeal(h => ({ ...h, loading: false, msg: `应用失败: ${(e as Error)?.message || ''}` }));
    }
  };

  const engines = stats ? canonicalEngines(Object.keys(stats.queue)) : [];
  const detailWorker = detail.open ? workers.find(w => w.worker_uid === detail.uid) : undefined;

  const TABS = [
    { value: 'overview' as const, label: <>{t('workbench.adminWorkers.tabOverview', { defaultValue: '概览' })}{anyUnhealthy && <span className="ml-1 inline-block w-1.5 h-1.5 rounded-full align-middle" style={{ background: 'var(--danger-solid)' }} />}</> },
    { value: 'fleet' as const, label: t('workbench.adminWorkers.tabFleet', { defaultValue: 'Worker 机群' }), count: workers.length },
    { value: 'accounts' as const, label: t('workbench.adminWorkers.tabAccounts', { defaultValue: '账号池' }), count: sessions?.grand_total ?? 0 },
    { value: 'proxy' as const, label: t('workbench.adminWorkers.tabProxy', { defaultValue: '代理与出口 IP' }), count: proxies.length },
  ];

  return (
    // List page skeleton (convention 6): the shell holds h-full flex flex-col, the page header / tab bar are shrink-0,
    // the current tab gets the flex-1 min-h-0 area and fills it with its table, scrolling inside.
    // The previous root was a space-y-4 document flow -- the table grew without limit and the whole main scrolled, i.e. "the fleet keeps sliding down".
    <div className="h-full flex flex-col">
      <PageHeader
        className="shrink-0"
        title={t('workbench.adminWorkers.heading', { defaultValue: 'Worker 管理' })}
        hint={t('workbench.adminWorkers.subtitle', {
          defaultValue: '多机浏览器自动化 worker 自动注册到调度中心,领任务跑批。每 10 秒刷新。' })}
        actions={
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted tabular-nums whitespace-nowrap">
              {t('workbench.adminWorkers.summary', { defaultValue: '在线 {{on}}/{{all}} · 告警 {{alert}} · 掉线号 {{quar}}',
                on: onlineCount, all: workers.length, alert: alertCount, quar: poolQuar })}
            </span>
            {/* This page has moved wholesale to the collection network's own admin console (V-networks); this version awaits retirement in K29 */}
            <Button size="sm" variant="outline" onClick={() => void openVnetConsole(token)}>
              {t('workbench.adminWorkers.openVnetConsole', { defaultValue: '采集网络管理台 ↗' })}
            </Button>
          </div>
        }
      />

      <div className="shrink-0 mb-4"><PillTabs value={tab} onChange={setTab} items={TABS} /></div>

      <div className="flex-1 min-h-0">
      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading')}</div>
      ) : err ? (
        <div className="text-xs px-3 py-2 rounded-lg"
             style={{ color: 'var(--danger-fg)', background: 'var(--danger-bg)', border: '1px solid var(--danger-border)' }}>{err}</div>
      ) : (
        <>
          {tab === 'overview' && <OverviewTab health={health} stats={stats} engines={engines} doHeal={doHeal} />}
          {tab === 'fleet' && (
            <FleetTab workers={workers} busyUid={busyUid} shareCount={shareCount}
                      act={act} forget={forget} openDetail={openDetail} />
          )}
          {tab === 'accounts' && (
            <AccountsTab
              auth={{
                authForm, setAuthForm, authBusy, authMsg, doAuthorize,
                smsEngine: sms.engine, setSmsEngine: v => setSms(s => ({ ...s, engine: v })),
                smsCc: sms.cc, smsPhone: sms.phone,
                setSmsCc: v => setSms(s => ({ ...s, cc: v })),
                setSmsPhone: v => setSms(s => ({ ...s, phone: v })),
                startSms,
                qrEngine: qr.engine, setQrEngine: v => setQr(s => ({ ...s, engine: v })), startQr,
              }}
              pool={{
                sessions, page: sessPage, setPage: setSessPage,
                engine: sessEngine, setEngine: setSessEngine,
                status: sessStatus, setStatus: setSessStatus,
                proxies, patchSession, bindProxyId,
              }}
            />
          )}
          {tab === 'proxy' && (
            <ProxyTab proxies={proxies} ipGroups={ipGroups} proxyForm={proxyForm} setProxyForm={setProxyForm}
                      proxyBusy={proxyBusy} createProxy={createProxy} testProxy={testProxy} deleteProxy={deleteProxy} />
          )}
        </>
      )}
      </div>

      <WorkerDetailDrawer detail={detail} worker={detailWorker} setDetail={setDetail} onSave={saveDetail} />

      {/* ── SMS verification code login ── */}
      <Modal open={sms.open} onClose={closeSms} maxWidthClass="max-w-md"
             header={<span className="text-sm text-primary">{t('workbench.adminWorkers.smsTip', { defaultValue: '{{engine}} 手机验证码登录', engine: engineLabel(sms.engine) })}</span>}
             footer={<div className="flex justify-end"><Button size="sm" variant="ghost" onClick={closeSms}>{t('workbench.adminWorkers.close', { defaultValue: '关闭' })}</Button></div>}>
        <div className="text-center">
          {sms.status === 'starting' && <div className="text-xs text-muted py-10">{t('workbench.adminWorkers.smsSending', { defaultValue: '打开登录页、填手机号、点发码中…' })}</div>}
          {sms.shot && sms.status !== 'done' && (
            <img src={sms.shot} alt="login page" style={{ width: '100%', maxHeight: 260, objectFit: 'contain', background: '#fff', borderRadius: 8 }} />
          )}
          {(sms.status === 'code_sent' || sms.status === 'captcha' || sms.status === 'submitting') && (
            <div className="flex items-center justify-center gap-2 mt-3">
              <Input size="sm" className="!w-[140px]" value={sms.code} placeholder="短信验证码" autoFocus
                     onChange={v => setSms(s => ({ ...s, code: v }))}
                     onKeyDown={e => { if (e.key === 'Enter') void submitSms(); }} />
              <Button size="sm" variant="solid" loading={sms.status === 'submitting'} disabled={!sms.code} onClick={submitSms}>登录</Button>
              {sms.status === 'captcha' && (
                <Button size="sm" variant="outline" onClick={refreshSms}>
                  {t('workbench.adminWorkers.smsRefresh', { defaultValue: '刷新截图' })}
                </Button>
              )}
            </div>
          )}
          {sms.status === 'done' && <div className="py-6" style={{ color: 'var(--success-fg)' }}>✅ {sms.msg || '授权成功,已入池'}</div>}
          {sms.msg && sms.status !== 'done' && <div className="mt-3 text-xs" style={{ color: 'var(--danger-fg)' }}>{sms.msg}</div>}
        </div>
      </Modal>

      {/* ── QR scan authorization ── */}
      <Modal open={qr.open} onClose={() => setQr(s => ({ ...s, open: false }))} maxWidthClass="max-w-xs"
             header={<span className="text-sm text-primary">{t('workbench.adminWorkers.qrScanTip', { defaultValue: '{{engine}} 扫码登录', engine: engineLabel(qr.engine) })}</span>}
             footer={<div className="flex justify-end"><Button size="sm" variant="ghost" onClick={() => setQr(s => ({ ...s, open: false }))}>{t('workbench.adminWorkers.close', { defaultValue: '关闭' })}</Button></div>}>
        <div className="text-center">
          {qr.status === 'starting' && <div className="text-xs text-muted py-10">{t('workbench.adminWorkers.qrLoading', { defaultValue: '打开登录页、抓二维码中…' })}</div>}
          {qr.img && qr.status !== 'done' && qr.status !== 'expired' && (
            <img src={qr.img} alt="qr" style={{ width: 200, height: 200, objectFit: 'contain', margin: '0 auto', background: '#fff', borderRadius: 8 }} />
          )}
          {qr.status === 'waiting' && <div className="text-xs text-muted mt-3">{t('workbench.adminWorkers.qrWaiting', { defaultValue: '请用手机 App 扫码并确认登录…' })}</div>}
          {qr.status === 'done' && <div className="py-10" style={{ color: 'var(--success-fg)' }}>✅ {t('workbench.adminWorkers.qrDone', { defaultValue: '授权成功,已入池' })}</div>}
          {qr.status === 'expired' && <div className="py-10" style={{ color: 'var(--danger-fg)' }}>{t('workbench.adminWorkers.qrExpired', { defaultValue: '二维码已过期,请重新点击授权' })}</div>}
          {qr.status.startsWith('error') && <div className="py-10 text-xs" style={{ color: 'var(--danger-fg)' }}>{qr.status}</div>}
        </div>
      </Modal>

      {/* ── Engine self-heal proposal ── */}
      <Modal open={heal.open} onClose={() => setHeal(h => ({ ...h, open: false }))} maxWidthClass="max-w-xl"
             header={<span className="text-sm font-medium text-primary">{t('workbench.adminWorkers.healTitle', { defaultValue: '引擎自愈' })} — {engineLabel(heal.engine)}</span>}
             footer={
               <div className="flex justify-end gap-2">
                 <Button size="sm" variant="ghost" onClick={() => setHeal(h => ({ ...h, open: false }))}>{t('workbench.adminWorkers.close', { defaultValue: '关闭' })}</Button>
                 {heal.proposal && (
                   <Button size="sm" variant="solid" loading={heal.loading} disabled={!heal.validated} onClick={doHealApply}>
                     {t('workbench.adminWorkers.healApply', { defaultValue: '应用到中心配置' })}
                   </Button>
                 )}
               </div>
             }>
        {heal.loading && <div className="text-xs text-muted py-6 text-center">{t('workbench.adminWorkers.healRunning', { defaultValue: '探测页面 + LLM 提议选择器中(约 1 分钟)…' })}</div>}
        {heal.msg && <div className="text-xs mb-2" style={{ color: 'var(--warn-fg)' }}>{heal.msg}</div>}
        {heal.proposal && (
          <div className="text-xs">
            <div className="text-muted mb-2">{t('workbench.adminWorkers.healProposalHint', { defaultValue: 'LLM 提议的新选择器(审阅后应用,将写入中心配置、全队生效):' })}</div>
            <pre className="rounded-md p-3 overflow-auto max-h-[240px] text-secondary" style={{ background: 'var(--bg-tertiary)' }}>
              {JSON.stringify(heal.proposal, null, 2)}
            </pre>
            {heal.valText && <div className="mt-2" style={{ color: heal.validated ? 'var(--success-fg)' : 'var(--danger-fg)' }}>{heal.valText}</div>}
            {!heal.validated && (
              <div className="text-[10px] text-muted mt-1">
                {t('workbench.adminWorkers.healNotValidated', { defaultValue: '未通过验证(用提议选择器跑 canary 没出答案),不建议应用' })}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
