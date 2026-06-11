import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  aiTelemetryApi,
  type BrowserWorker,
  type WorkerQueueStats,
  type EngineSessionPage,
} from '../../services/aiTelemetryApi';

const REFRESH_MS = 10000;
const SESSION_PAGE_SIZE = 20;

export function AdminWorkers() {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [workers, setWorkers] = useState<BrowserWorker[]>([]);
  const [stats, setStats] = useState<WorkerQueueStats | null>(null);
  const [sessions, setSessions] = useState<EngineSessionPage | null>(null);
  const [sessPage, setSessPage] = useState(1);
  const [sessEngine, setSessEngine] = useState('');
  const [sessStatus, setSessStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busyUid, setBusyUid] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([
      aiTelemetryApi.adminListWorkers(token),
      aiTelemetryApi.adminWorkersQueueStats(token),
      aiTelemetryApi.adminListEngineSessions(token, sessPage, SESSION_PAGE_SIZE, sessEngine, sessStatus),
    ])
      .then(([ws, st, ss]) => {
        setWorkers(ws); setStats(st); setSessions(ss); setErr(null);
        // 行被清理后页码可能越界(空页),回第一页
        if (ss.items.length === 0 && ss.page > 1) setSessPage(1);
      })
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token, sessPage, sessEngine, sessStatus]);

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

  const engines = stats ? Object.keys(stats.queue).sort() : [];

  return (
    <div className="max-w-[1200px] mx-auto p-6">
      <div className="mb-4">
        <h1 className="text-xl font-semibold text-primary">
          {t('workbench.adminWorkers.heading', { defaultValue: 'Worker 管理' })}
        </h1>
        <p className="text-xs text-muted mt-1">
          {t('workbench.adminWorkers.subtitle', {
            defaultValue: '多机浏览器自动化 worker 自动注册到调度中心,领任务跑批。每 10 秒刷新。',
          })}
        </p>
      </div>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading')}</div>
      ) : err ? (
        <div className="text-xs text-red-500">{err}</div>
      ) : (
        <>
          {/* ── 任务队列概览 ── */}
          {stats && (
            <div className="rounded-lg overflow-hidden mb-5"
                 style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="px-3 py-2 text-xs text-secondary font-medium border-b"
                   style={{ borderColor: 'var(--border-color)' }}>
                {t('workbench.adminWorkers.queueTitle', { defaultValue: '任务队列(按引擎)' })}
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted">
                    <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colEngine', { defaultValue: '引擎' })}</th>
                    <th className="text-right px-3 py-2">queued</th>
                    <th className="text-right px-3 py-2">claimed</th>
                    <th className="text-right px-3 py-2">done</th>
                    <th className="text-right px-3 py-2">failed</th>
                    <th className="text-right px-3 py-2">{t('workbench.adminWorkers.colToday', { defaultValue: '今日 / 上限' })}</th>
                  </tr>
                </thead>
                <tbody>
                  {engines.map(e => {
                    const q = stats.queue[e] || {};
                    const cap = stats.daily_cap[e];
                    return (
                      <tr key={e} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                        <td className="px-3 py-2 text-primary">{e}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{q.queued || 0}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{q.claimed || 0}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-emerald-500">{q.done || 0}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-red-400">{q.failed || 0}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-secondary">
                          {stats.used_today[e] || 0}{cap == null ? '' : ` / ${cap}`}
                        </td>
                      </tr>
                    );
                  })}
                  {engines.length === 0 && (
                    <tr><td colSpan={6} className="px-3 py-4 text-center text-muted">
                      {t('workbench.adminWorkers.queueEmpty', { defaultValue: '队列为空' })}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* ── Worker 列表 ── */}
          <div className="rounded-lg overflow-hidden"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted">
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colHost', { defaultValue: '主机' })}</th>
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colExitIp', { defaultValue: '出口 IP' })}</th>
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colEngines', { defaultValue: '引擎' })}</th>
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colStatus', { defaultValue: '状态' })}</th>
                  <th className="text-right px-3 py-2">{t('workbench.adminWorkers.colInflight', { defaultValue: '在跑' })}</th>
                  <th className="text-right px-3 py-2">{t('workbench.adminWorkers.colDoneToday', { defaultValue: '今日完成' })}</th>
                  <th className="text-right px-3 py-2">{t('workbench.adminWorkers.colActions', { defaultValue: '操作' })}</th>
                </tr>
              </thead>
              <tbody>
                {workers.map(w => {
                  const online = w.status === 'online';
                  const brokenEngines = Object.keys(w.breaker || {});
                  return (
                    <tr key={w.worker_uid} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                      <td className="px-3 py-2 text-primary">
                        {w.hostname || w.label || w.worker_uid.slice(0, 8)}
                        {!w.enabled && (
                          <span className="ml-1 text-[10px] text-red-400">
                            {t('workbench.adminWorkers.disabledBadge', { defaultValue: '已禁用' })}
                          </span>
                        )}
                        {w.raw_status === 'draining' && (
                          <span className="ml-1 text-[10px] text-amber-500">
                            {t('workbench.adminWorkers.drainingBadge', { defaultValue: '排空中' })}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-secondary tabular-nums">{w.exit_ip || '—'}</td>
                      <td className="px-3 py-2 text-secondary">
                        {(w.engines || []).join(', ') || '—'}
                        {brokenEngines.length > 0 && (
                          <span className="ml-1 text-[10px] text-red-400">
                            ⚡{brokenEngines.join(',')}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <span className={online ? 'text-emerald-500' : 'text-muted'}>
                          ● {online
                            ? t('workbench.adminWorkers.online', { defaultValue: '在线' })
                            : t('workbench.adminWorkers.offline', { defaultValue: '离线' })}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{w.in_flight}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{w.done_today}</td>
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        {w.enabled ? (
                          <>
                            <button type="button" disabled={busyUid === w.worker_uid}
                                    onClick={() => act(w.worker_uid, 'drain')}
                                    className="px-2 py-1 rounded-md mr-1 disabled:opacity-40"
                                    style={{ background: 'var(--bg-tertiary)' }}>
                              {t('workbench.adminWorkers.drain', { defaultValue: '排空' })}
                            </button>
                            <button type="button" disabled={busyUid === w.worker_uid}
                                    onClick={() => act(w.worker_uid, 'disable')}
                                    className="px-2 py-1 rounded-md text-red-400 disabled:opacity-40"
                                    style={{ background: 'var(--bg-tertiary)' }}>
                              {t('workbench.adminWorkers.disable', { defaultValue: '禁用' })}
                            </button>
                          </>
                        ) : (
                          <button type="button" disabled={busyUid === w.worker_uid}
                                  onClick={() => act(w.worker_uid, 'enable')}
                                  className="px-2 py-1 rounded-md text-emerald-500 disabled:opacity-40"
                                  style={{ background: 'var(--bg-tertiary)' }}>
                            {t('workbench.adminWorkers.enable', { defaultValue: '启用' })}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {workers.length === 0 && (
                  <tr><td colSpan={7} className="px-3 py-6 text-center text-muted">
                    {t('workbench.adminWorkers.noWorkers', { defaultValue: '暂无 worker 接入' })}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* ── 账号池(登录态)── */}
          <div className="rounded-lg overflow-hidden mt-5"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <div className="flex items-center px-3 py-2 text-xs text-secondary font-medium border-b"
                 style={{ borderColor: 'var(--border-color)' }}>
              {t('workbench.adminWorkers.poolTitle', { defaultValue: '账号池(登录态)' })}
              <span className="text-muted ml-2">
                {t('workbench.adminWorkers.poolActive', {
                  defaultValue: '可用 {{n}} / 共 {{total}}',
                  n: sessions?.active_total ?? 0,
                  total: sessions?.grand_total ?? 0,
                })}
              </span>
              <span className="ml-auto flex items-center gap-2 font-normal">
                <select value={sessEngine}
                        onChange={e => { setSessEngine(e.target.value); setSessPage(1); }}
                        className="px-2 py-1 rounded-md text-xs"
                        style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
                  <option value="">{t('workbench.adminWorkers.filterAllEngines', { defaultValue: '全部引擎' })}</option>
                  {(sessions?.engines ?? []).map(e => <option key={e} value={e}>{e}</option>)}
                </select>
                <select value={sessStatus}
                        onChange={e => { setSessStatus(e.target.value); setSessPage(1); }}
                        className="px-2 py-1 rounded-md text-xs"
                        style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
                  <option value="">{t('workbench.adminWorkers.filterAllStatus', { defaultValue: '全部状态' })}</option>
                  <option value="active">{t('workbench.adminWorkers.statusActive', { defaultValue: '可用' })}</option>
                  <option value="expired">{t('workbench.adminWorkers.statusExpired', { defaultValue: '已过期' })}</option>
                  <option value="quarantined">{t('workbench.adminWorkers.statusQuarantined', { defaultValue: '已隔离' })}</option>
                </select>
              </span>
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted">
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colEngine', { defaultValue: '引擎' })}</th>
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colLabel', { defaultValue: '标识' })}</th>
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colStatus', { defaultValue: '状态' })}</th>
                  <th className="text-right px-3 py-2">{t('workbench.adminWorkers.colUseCount', { defaultValue: '用量' })}</th>
                  <th className="text-right px-3 py-2">{t('workbench.adminWorkers.colCaptcha', { defaultValue: '验证码' })}</th>
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colLastUsed', { defaultValue: '最后使用' })}</th>
                  <th className="text-left px-3 py-2">{t('workbench.adminWorkers.colExpires', { defaultValue: '过期' })}</th>
                </tr>
              </thead>
              <tbody>
                {(sessions?.items ?? []).map(s => {
                  const color = s.status === 'active' ? 'text-emerald-500'
                    : s.status === 'quarantined' ? 'text-red-400' : 'text-muted';
                  const statusText = s.status === 'active'
                    ? t('workbench.adminWorkers.statusActive', { defaultValue: '可用' })
                    : s.status === 'expired'
                      ? t('workbench.adminWorkers.statusExpired', { defaultValue: '已过期' })
                      : s.status === 'quarantined'
                        ? t('workbench.adminWorkers.statusQuarantined', { defaultValue: '已隔离' })
                        : s.status;
                  const fmt = (d: string | null) => d ? d.slice(0, 16).replace('T', ' ') : '—';
                  return (
                    <tr key={s.id} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                      <td className="px-3 py-2 text-primary">{s.engine}</td>
                      <td className="px-3 py-2 text-secondary">
                        {s.account_handle || s.label || `#${s.id}`}
                        {s.account_handle && s.label && (
                          <span className="text-[10px] text-muted ml-1">({s.label})</span>
                        )}
                      </td>
                      <td className={`px-3 py-2 ${color}`}>
                        {statusText}
                        {s.last_fail_type && s.status !== 'active' && (
                          <span className="text-[10px] text-muted ml-1">({s.last_fail_type})</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{s.use_count}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{s.captcha_count}</td>
                      <td className="px-3 py-2 text-secondary tabular-nums">{fmt(s.last_used_at)}</td>
                      <td className="px-3 py-2 text-secondary tabular-nums">{fmt(s.expires_at)}</td>
                    </tr>
                  );
                })}
                {(sessions?.total ?? 0) === 0 && (
                  <tr><td colSpan={7} className="px-3 py-6 text-center text-muted">
                    {(sessEngine || sessStatus)
                      ? t('workbench.adminWorkers.noFilterMatch', { defaultValue: '没有匹配当前筛选的账号' })
                      : t('workbench.adminWorkers.noSessions', { defaultValue: '账号池为空,用浏览器扩展上传登录态' })}
                  </td></tr>
                )}
              </tbody>
            </table>
            {(sessions?.total ?? 0) > SESSION_PAGE_SIZE && (
              <div className="flex items-center justify-end gap-2 px-3 py-2 text-xs border-t"
                   style={{ borderColor: 'var(--border-color)' }}>
                <button type="button" disabled={sessPage <= 1}
                        onClick={() => setSessPage(p => p - 1)}
                        className="px-2 py-1 rounded-md disabled:opacity-40"
                        style={{ background: 'var(--bg-tertiary)' }}>
                  {t('workbench.adminWorkers.prevPage', { defaultValue: '上一页' })}
                </button>
                <span className="text-muted tabular-nums">
                  {sessPage} / {Math.max(1, Math.ceil((sessions?.total ?? 0) / SESSION_PAGE_SIZE))}
                </span>
                <button type="button"
                        disabled={sessPage >= Math.ceil((sessions?.total ?? 0) / SESSION_PAGE_SIZE)}
                        onClick={() => setSessPage(p => p + 1)}
                        className="px-2 py-1 rounded-md disabled:opacity-40"
                        style={{ background: 'var(--bg-tertiary)' }}>
                  {t('workbench.adminWorkers.nextPage', { defaultValue: '下一页' })}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
