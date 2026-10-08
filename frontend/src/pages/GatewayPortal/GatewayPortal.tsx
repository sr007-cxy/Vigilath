// Tenant portal — self-service page for gateway tenants (role=tenant) after login:
// balance / per-engine usage today and remaining calls / call log (paged + filtered).
// Data is strictly isolated by the backend per the tenant bound to the logged-in account; the frontend passes no tenant param.
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  gatewayPortalApi,
  type PortalOverview,
  type PortalJob,
} from '../../services/gatewayPortalApi';
import { useAuthModal } from '../../contexts/AuthModalContext';
import { fmtTime } from '../../utils/datetime';
import { PageHead } from '../../components/PageHead';
import { UiSelect } from '../../components/ui/Select';
import { engineLabel, isCanonicalEngine, ENGINE_KEYS } from '../../constants/engines';
import { DateRangeFilter } from '../../components/ui/FilterBar';
import { fromQueryRange, toQueryRange } from '../../components/ui/dateFilter';

const REFRESH_MS = 15000;
const JOB_STATUSES = ['queued', 'claimed', 'done', 'failed'];

const card: React.CSSProperties = { background: 'var(--bg-card)', border: '1px solid var(--border-color)' };
const fld: React.CSSProperties = { ...card, fontSize: 12 };

export function GatewayPortal() {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { openAuthModal } = useAuthModal();

  const [overview, setOverview] = useState<PortalOverview | null>(null);
  const [jobs, setJobs] = useState<PortalJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [openJob, setOpenJob] = useState<number | null>(null);

  // Log filtering + pagination
  const [jEngine, setJEngine] = useState('');
  const [jStatus, setJStatus] = useState('');
  const [jSince, setJSince] = useState('');
  const [jUntil, setJUntil] = useState('');
  const [jPage, setJPage] = useState(0);
  const [jPageSize, setJPageSize] = useState(50);
  const [jTotal, setJTotal] = useState(0);
  const [jobsLoading, setJobsLoading] = useState(false);

  useEffect(() => {
    if (!token) {
      openAuthModal('login');
      navigate('/', { replace: true });
    }
  }, [token, openAuthModal, navigate]);

  const reload = useCallback(() => {
    if (!token) return;
    Promise.all([
      gatewayPortalApi.overview(token),
      gatewayPortalApi.jobs(token, {
        engine: jEngine || undefined,
        status: jStatus || undefined,
        since: jSince || undefined,
        until: jUntil || undefined,
        limit: jPageSize,
        offset: jPage * jPageSize,
      }),
    ])
      .then(([ov, js]) => { setOverview(ov); setJobs(js.jobs); setJTotal(js.total ?? 0); setErr(null); })
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => { setLoading(false); setJobsLoading(false); });
  }, [token, jEngine, jStatus, jSince, jUntil, jPage, jPageSize]);

  useEffect(() => {
    reload();
    const id = setInterval(reload, REFRESH_MS);
    return () => clearInterval(id);
  }, [reload]);

  const firstLoad = useRef(true);
  useEffect(() => {
    if (firstLoad.current) { firstLoad.current = false; return; }
    setJobsLoading(true);
    setJPage(0);
  }, [jEngine, jStatus, jSince, jUntil]);

  // Show only the unified 10 engines, in the unified order (filter out non-canonical collection keys)
  const engines = (overview?.engines || [])
    .filter(e => isCanonicalEngine(e.engine))
    .sort((a, b) => ENGINE_KEYS.indexOf(a.engine as never) - ENGINE_KEYS.indexOf(b.engine as never));

  return (
    <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <PageHead titleKey="portal.heading" descriptionKey="portal.subtitle" />
      <div className="mb-4">
        <h1 className="text-xl font-semibold text-primary">
          {t('portal.heading', { defaultValue: '租户门户' })}
          {overview ? <span className="text-sm text-muted font-normal ml-2">{overview.tenant.name}</span> : null}
        </h1>
        <p className="text-xs text-muted mt-1">
          {t('portal.subtitle', { defaultValue: '查看您的余额、各引擎今日用量与调用记录。每 15 秒自动刷新。' })}
        </p>
      </div>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading', { defaultValue: '加载中…' })}</div>
      ) : err ? (
        <div className="rounded-lg p-4 text-xs" style={card}>
          <span style={{ color: 'var(--danger-fg)' }}>{err}</span>
        </div>
      ) : overview && (
        <>
          {/* Balance and totals */}
          <div className="grid grid-cols-3 gap-3 mb-5">
            <div className="rounded-lg p-4" style={card}>
              <div className="text-2xl font-semibold" style={{ color: overview.tenant.credit_balance <= 0 ? 'var(--danger-fg)' : 'var(--text-primary)' }}>
                {overview.tenant.credit_balance}
              </div>
              <div className="text-xs text-muted mt-1">{t('portal.balance', { defaultValue: '剩余额度' })}</div>
            </div>
            <div className="rounded-lg p-4" style={card}>
              <div className="text-2xl font-semibold text-primary">{overview.billable_total}</div>
              <div className="text-xs text-muted mt-1">{t('portal.billableTotal', { defaultValue: '累计计费调用' })}</div>
            </div>
            <div className="rounded-lg p-4" style={card}>
              <div className="text-2xl font-semibold text-primary">{overview.credits_spent_total}</div>
              <div className="text-xs text-muted mt-1">{t('portal.creditsSpent', { defaultValue: '累计消耗额度' })}</div>
            </div>
          </div>

          {/* Engine usage */}
          <div className="rounded-lg overflow-hidden mb-5" style={card}>
            <div className="px-3 py-2 text-xs font-medium text-primary" style={{ borderBottom: '1px solid var(--border-color)' }}>
              {t('portal.engineUsage', { defaultValue: '各引擎今日用量' })}
            </div>
            <table className="w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="text-left px-3 py-2">{t('portal.colEngine', { defaultValue: '引擎' })}</th>
                  <th className="text-right px-3 py-2">{t('portal.colQuota', { defaultValue: '日配额' })}</th>
                  <th className="text-right px-3 py-2">{t('portal.colUsedToday', { defaultValue: '今日已用' })}</th>
                  <th className="text-right px-3 py-2">{t('portal.colRemaining', { defaultValue: '剩余次数' })}</th>
                </tr>
              </thead>
              <tbody>
                {engines.map(e => (
                  <tr key={e.engine} style={{ borderTop: '1px solid var(--border-color)' }}>
                    <td className="px-3 py-2 text-primary">{engineLabel(e.engine)}</td>
                    <td className="px-3 py-2 text-right text-muted">{e.daily_quota}</td>
                    <td className="px-3 py-2 text-right text-primary">{e.used_today}</td>
                    <td className="px-3 py-2 text-right" style={{ color: e.remaining <= 0 ? 'var(--danger-fg)' : 'var(--success-fg)' }}>{e.remaining}</td>
                  </tr>
                ))}
                {engines.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-6 text-center text-muted">{t('portal.noEngines', { defaultValue: '暂无可用引擎' })}</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Call log */}
          <div className="rounded-lg overflow-hidden" style={card}>
            <div className="px-3 py-2 flex items-center gap-2 flex-wrap" style={{ borderBottom: '1px solid var(--border-color)' }}>
              <span className="text-xs font-medium text-primary mr-1">{t('portal.records', { defaultValue: '调用记录' })}</span>
              {jobsLoading && <span className="text-xs text-muted">{t('common.loading', { defaultValue: '加载中…' })}</span>}
              <UiSelect className="px-2 py-1 rounded text-primary" style={fld} value={jEngine} onChange={val => setJEngine(val)}
                        options={[
                          { value: '', label: t('portal.engineAll', { defaultValue: '全部引擎' }) },
                          ...engines.map(e => ({ value: e.engine, label: engineLabel(e.engine) })),
                        ]} />
              <UiSelect className="px-2 py-1 rounded text-primary" style={fld} value={jStatus} onChange={val => setJStatus(val)}
                        options={[
                          { value: '', label: t('portal.statusAll', { defaultValue: '全部状态' }) },
                          ...JOB_STATUSES.map(st => ({ value: st, label: st })),
                        ]} />
              <DateRangeFilter
                value={fromQueryRange(jSince, jUntil)}
                onChange={v => { const q = toQueryRange(v); setJSince(q.since); setJUntil(q.until); }} />
            </div>
            <table className="w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="text-left px-3 py-2">#</th>
                  <th className="text-left px-3 py-2">{t('portal.colEngine', { defaultValue: '引擎' })}</th>
                  <th className="text-left px-3 py-2">{t('portal.colQuery', { defaultValue: '问题' })}</th>
                  <th className="text-left px-3 py-2">{t('portal.colStatus', { defaultValue: '状态' })}</th>
                  <th className="text-left px-3 py-2">{t('portal.colCreatedAt', { defaultValue: '发起时间' })}</th>
                  <th className="text-left px-3 py-2">{t('portal.colFinishedAt', { defaultValue: '完成时间' })}</th>
                  <th className="text-right px-3 py-2">{t('portal.colReturn', { defaultValue: '返回' })}</th>
                </tr>
              </thead>
              <tbody style={{ opacity: jobsLoading ? 0.45 : 1, transition: 'opacity .15s' }}>
                {jobs.map(j => (
                  <Fragment key={j.id}>
                    <tr style={{ borderTop: '1px solid var(--border-color)', cursor: 'pointer' }}
                        onClick={() => setOpenJob(openJob === j.id ? null : j.id)}>
                      <td className="px-3 py-2 text-muted">{j.id}</td>
                      <td className="px-3 py-2 text-primary">{engineLabel(j.engine)}</td>
                      <td className="px-3 py-2 text-muted max-w-[320px] truncate">{j.query}</td>
                      <td className="px-3 py-2">
                        <span style={{ color: j.status === 'done' ? 'var(--success-fg)' : j.status === 'failed' ? 'var(--danger-fg)' : 'var(--text-muted)' }}>{j.status}</span>
                      </td>
                      <td className="px-3 py-2 text-muted">{fmtTime(j.created_at)}</td>
                      <td className="px-3 py-2 text-muted">{fmtTime(j.finished_at)}</td>
                      <td className="px-3 py-2 text-right">
                        <button className="px-2 py-0.5 rounded text-xs" style={card}
                                onClick={(e) => { e.stopPropagation(); setOpenJob(openJob === j.id ? null : j.id); }}>
                          {openJob === j.id
                            ? t('portal.collapse', { defaultValue: '收起' })
                            : t('portal.view', { defaultValue: '查看' })}
                        </button>
                      </td>
                    </tr>
                    {openJob === j.id && (
                      <tr style={{ background: 'var(--bg-page, rgba(0,0,0,0.02))' }}>
                        <td colSpan={7} className="px-4 py-3">
                          {j.error
                            ? <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>
                                {t('portal.retError', { defaultValue: '错误' })}:{j.error}
                              </div>
                            : <>
                                <div className="text-xs text-muted mb-1">{t('portal.retAnswer', { defaultValue: '返回答案' })}:</div>
                                <div className="text-xs text-primary whitespace-pre-wrap mb-2" style={{ maxHeight: 320, overflow: 'auto' }}>
                                  {j.answer || t('portal.retEmpty', { defaultValue: '(空)' })}
                                </div>
                                <div className="text-xs text-muted mb-1">
                                  {t('portal.retCitations', { defaultValue: '引用来源' })}（{j.citations?.length || 0}）:
                                </div>
                                {(j.citations && j.citations.length > 0)
                                  ? <ul className="text-xs" style={{ listStyle: 'disc', paddingLeft: 18 }}>
                                      {j.citations.map((c, i) => (
                                        <li key={i}>
                                          <a href={c.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent, #2563eb)' }}>
                                            {c.title || c.domain || c.url}
                                          </a>
                                          {c.domain ? <span className="text-muted">（{c.domain}）</span> : null}
                                        </li>
                                      ))}
                                    </ul>
                                  : <div className="text-xs text-muted">{t('portal.retNoCite', { defaultValue: '(无引用)' })}</div>}
                              </>}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
                {jobs.length === 0 && (
                  <tr><td colSpan={7} className="px-3 py-6 text-center text-muted">{t('portal.noJobs', { defaultValue: '暂无调用记录' })}</td></tr>
                )}
              </tbody>
            </table>
            {/* Pagination bar */}
            <div className="px-3 py-2 flex items-center gap-3 flex-wrap text-xs text-muted"
                 style={{ borderTop: '1px solid var(--border-color)' }}>
              <span>{t('portal.total', { defaultValue: '共 {{n}} 条', n: jTotal })}</span>
              <span className="ml-auto flex items-center gap-2">
                <label className="flex items-center gap-1">
                  {t('portal.pageSize', { defaultValue: '每页' })}
                  <UiSelect className="px-1 py-0.5 rounded text-primary" style={fld} value={String(jPageSize)}
                            onChange={val => { setJPageSize(Number(val)); setJPage(0); }}
                            options={[20, 50, 100, 200].map(s => ({ value: String(s), label: s }))} />
                </label>
                <button className="px-2 py-0.5 rounded" style={{ ...fld, opacity: jPage <= 0 ? 0.4 : 1, cursor: jPage <= 0 ? 'not-allowed' : 'pointer' }}
                        disabled={jPage <= 0} onClick={() => setJPage(p => Math.max(0, p - 1))}>
                  {t('portal.prevPage', { defaultValue: '上一页' })}
                </button>
                <span>{t('portal.pageInfo', {
                  defaultValue: '第 {{c}} / {{n}} 页',
                  c: jPage + 1, n: Math.max(1, Math.ceil(jTotal / jPageSize)) })}</span>
                <button className="px-2 py-0.5 rounded"
                        style={{ ...fld, opacity: (jPage + 1) >= Math.ceil(jTotal / jPageSize) ? 0.4 : 1, cursor: (jPage + 1) >= Math.ceil(jTotal / jPageSize) ? 'not-allowed' : 'pointer' }}
                        disabled={(jPage + 1) >= Math.ceil(jTotal / jPageSize)} onClick={() => setJPage(p => p + 1)}>
                  {t('portal.nextPage', { defaultValue: '下一页' })}
                </button>
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
