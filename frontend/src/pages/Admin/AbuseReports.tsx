// Report handling -- admin ticket desk (the handling side of item 13 of the algorithm filing correction list).
// Route: /ops/abuse-reports   Backend: the /admin/reports series in geo/api/abuse_report.py
//
// The four-step closed loop maps one-to-one to the list: report (user submits) -> accept -> handle -> feedback.
// **Closing requires a reply** -- enforced by the backend, and the reply box is marked required here too; missing a step isn't a closed loop.
//
// Overdue metric definition: not closed and past the promised deadline (default 7 calendar days from acceptance), counted separately at the top of the list,
// marked in red -- during filing evidence collection the reviewers check for backlog.

import { useCallback, useEffect, useState } from 'react';
import { PageHead } from '../../components/PageHead';
import { fmtTime } from '../../utils/datetime';
import { abuseReportApi, type ReportAdmin } from '../../services/abuseReportApi';

const card: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border-color)',
};
const inputStyle: React.CSSProperties = {
  background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
};

const STATUS_TABS: { key: string; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'received', label: '待受理' },
  { key: 'processing', label: '处理中' },
  { key: 'resolved', label: '已处置' },
  { key: 'rejected', label: '不予受理' },
];

const STATUS_COLOR: Record<string, { c: string; bg: string }> = {
  received:   { c: '#eab308', bg: 'rgba(234,179,8,0.15)' },
  processing: { c: '#3b82f6', bg: 'rgba(59,130,246,0.15)' },
  resolved:   { c: '#10b981', bg: 'var(--success-bg)' },
  rejected:   { c: '#6b7280', bg: 'rgba(107,114,128,0.15)' },
};

function StatusPill({ r }: { r: ReportAdmin }) {
  const s = STATUS_COLOR[r.status] || STATUS_COLOR.received;
  return (
    <span className="text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap"
          style={{ background: s.bg, color: s.c }}>
      {r.status_label}{r.overdue ? ' · 已超时' : ''}
    </span>
  );
}

export function AdminAbuseReports() {
  const token = localStorage.getItem('token') || '';
  const [items, setItems] = useState<ReportAdmin[]>([]);
  const [openCount, setOpenCount] = useState(0);
  const [overdue, setOverdue] = useState(0);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // The expanded ticket + handling form (only one handled at a time, so replies don't get mixed up)
  const [active, setActive] = useState<ReportAdmin | null>(null);
  const [handling, setHandling] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const reload = useCallback(async (st: string) => {
    setLoading(true);
    try {
      const r = await abuseReportApi.list(token, st);
      setItems(r.items);
      setOpenCount(r.open_count);
      setOverdue(r.overdue_count);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { reload(''); }, [reload]);

  const openTicket = (r: ReportAdmin) => {
    setActive(r);
    setHandling(r.handling || '');
    setReply(r.reply || '');
  };

  const run = async (key: string, fn: () => Promise<ReportAdmin>) => {
    if (busy) return;
    setBusy(key); setErr(null);
    try {
      const updated = await fn();
      setActive(updated);
      setItems(list => list.map(x => (x.id === updated.id ? updated : x)));
      await reload(status);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const closed = active && ['resolved', 'rejected'].includes(active.status);

  return (
    <div className="space-y-4">
      <PageHead titleKey="admin.abuseReports.title" titleFallback="举报处置" />

      <div>
        <h1 className="text-xl font-semibold text-primary">举报处置</h1>
        <p className="text-xs text-muted mt-1">
          公众通过 <code>/report</code> 提交的举报工单。闭环四步:举报 → 受理 → 处置 → 反馈,
          <b className="text-primary">办结必须填写给举报人的答复</b>,否则不算办结。
        </p>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex rounded-md overflow-hidden"
             style={{ border: '1px solid var(--border-color)' }}>
          {STATUS_TABS.map(s => (
            <button key={s.key} type="button"
                    onClick={() => { setStatus(s.key); reload(s.key); }}
                    className="text-xs px-3 py-1.5"
                    style={status === s.key
                      ? { background: 'var(--accent-primary)', color: '#fff' }
                      : { color: 'var(--text-secondary)' }}>
              {s.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted">未办结 {openCount} 条</span>
        {overdue > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full"
                style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
            超时 {overdue} 条
          </span>
        )}
      </div>

      {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

      <div className="rounded-xl overflow-x-auto" style={card}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted" style={{ borderBottom: '1px solid var(--border-color)' }}>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">工单号</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">类型</th>
              <th className="text-left font-normal px-3 py-2">问题摘要</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">举报人</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">提交时间</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">承诺时限</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">状态</th>
            </tr>
          </thead>
          <tbody>
            {items.map(r => (
              <tr key={r.id} onClick={() => openTicket(r)}
                  className="cursor-pointer"
                  style={{ borderBottom: '1px solid var(--border-color)',
                           background: active?.id === r.id ? 'var(--bg-tertiary)' : undefined }}>
                <td className="px-3 py-2 font-mono text-secondary whitespace-nowrap">{r.report_no}</td>
                <td className="px-3 py-2 text-secondary whitespace-nowrap">{r.category_label}</td>
                <td className="px-3 py-2 text-secondary max-w-[420px] truncate">{r.description}</td>
                <td className="px-3 py-2 text-secondary whitespace-nowrap">
                  {r.contact_email || r.contact_name || <span className="text-muted">匿名</span>}
                </td>
                <td className="px-3 py-2 text-secondary whitespace-nowrap">
                  {fmtTime(r.created_at, false)}
                </td>
                <td className="px-3 py-2 whitespace-nowrap"
                    style={{ color: r.overdue ? 'var(--danger-fg)' : 'var(--text-secondary)' }}>
                  {fmtTime(r.due_at, false) || '—'}
                </td>
                <td className="px-3 py-2"><StatusPill r={r} /></td>
              </tr>
            ))}
            {!loading && items.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-8 text-center text-muted">没有工单</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {active && (
        <div className="rounded-xl p-5 space-y-4" style={card}>
          <div className="flex items-center gap-2 flex-wrap">
            <code className="text-primary">{active.report_no}</code>
            <StatusPill r={active} />
            <span className="text-xs text-muted">{active.category_label}</span>
            <button type="button" onClick={() => setActive(null)}
                    className="ml-auto text-xs text-muted hover:text-primary">关闭 ✕</button>
          </div>

          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs">
            {([
              ['举报人', active.contact_name || '匿名'],
              ['联系邮箱', active.contact_email || '未留'],
              ['联系电话', active.contact_phone || '未留'],
              ['来源 IP', active.client_ip || '—'],
              ['提交时间', fmtTime(active.created_at)],
              ['承诺时限', fmtTime(active.due_at)],
              ['受理时间', fmtTime(active.accepted_at) || '—'],
              ['经办人', active.handler_name || '—'],
            ] as [string, string][]).map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <dt className="text-muted shrink-0 w-[64px]">{k}</dt>
                <dd className="text-secondary break-all">{v}</dd>
              </div>
            ))}
          </dl>

          <div className="flex flex-wrap gap-3 text-xs">
            {active.target_url && (
              <a href={active.target_url} target="_blank" rel="noreferrer"
                 className="underline text-muted hover:text-primary">涉及链接 →</a>
            )}
            {active.target_trace_no && (
              // The reporter filled in a content number -> one click to the provenance page, locating directly who generated it and when
              <a href={`/ops/content-trace?q=${encodeURIComponent(active.target_trace_no)}`}
                 className="underline text-muted hover:text-primary">
                溯源 {active.target_trace_no} →
              </a>
            )}
            {active.has_attachment && (
              <a href={abuseReportApi.attachmentUrl(active.id)} target="_blank" rel="noreferrer"
                 className="underline text-muted hover:text-primary">
                附件 {active.attachment_name} →
              </a>
            )}
          </div>

          <div>
            <p className="text-xs text-muted mb-1">问题描述</p>
            <p className="text-sm text-secondary whitespace-pre-wrap rounded-md p-3"
               style={{ background: 'var(--bg-tertiary)' }}>{active.description}</p>
          </div>

          {closed ? (
            <div className="space-y-2 text-sm">
              <div>
                <p className="text-xs text-muted mb-1">内部处置记录</p>
                <p className="text-secondary whitespace-pre-wrap">{active.handling || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-muted mb-1">已反馈举报人</p>
                <p className="text-secondary whitespace-pre-wrap">{active.reply}</p>
              </div>
              <p className="text-xs text-muted">办结时间 {fmtTime(active.resolved_at)}</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-muted mb-1.5">
                  内部处置记录(不发给举报人)
                </label>
                <textarea rows={3} className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                          placeholder="例如:已下架该稿件、已把相关表述加入禁用词库"
                          value={handling} onChange={e => setHandling(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1.5">
                  给举报人的答复 <span style={{ color: 'var(--danger-fg)' }}>*</span>
                  <span className="ml-2 text-[11px]">
                    办结与驳回都必须填 —— 缺答复不构成「反馈」闭环
                  </span>
                </label>
                <textarea rows={4} className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                          placeholder="会随邮件发给举报人,并在其查询进度时显示"
                          value={reply} onChange={e => setReply(e.target.value)} />
              </div>
              <div className="flex justify-end gap-2">
                {active.status === 'received' && (
                  <button type="button" disabled={busy !== null}
                          onClick={() => run('accept', () => abuseReportApi.accept(active.id, token))}
                          className="text-xs px-3 py-1.5 rounded-md disabled:opacity-40"
                          style={{ color: 'var(--text-secondary)',
                                   border: '1px solid var(--border-color)' }}>
                    {busy === 'accept' ? '受理中…' : '受理'}
                  </button>
                )}
                <button type="button" disabled={busy !== null || !reply.trim()}
                        onClick={() => run('reject',
                          () => abuseReportApi.reject(active.id, token, handling, reply))}
                        className="text-xs px-3 py-1.5 rounded-md disabled:opacity-40"
                        style={{ color: 'var(--danger-fg)', border: '1px solid rgba(239,68,68,0.4)' }}>
                  {busy === 'reject' ? '处理中…' : '不予受理'}
                </button>
                <button type="button" disabled={busy !== null || !reply.trim()}
                        onClick={() => run('resolve',
                          () => abuseReportApi.resolve(active.id, token, handling, reply))}
                        className="text-xs px-3 py-1.5 rounded-md text-white disabled:opacity-40"
                        style={{ background: 'var(--accent-primary)' }}>
                  {busy === 'resolve' ? '办结中…' : '处置完成并反馈'}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
