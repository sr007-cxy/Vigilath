// Complaints and reports — public-facing reporting entry (item 13 of the algorithm filing correction list).
// Route: /report   Backend: the /report endpoints in geo/api/abuse_report.py
//
// **Login not required**: reporters are mostly not our users, and a login wall would render this entry useless.
// When logged in the token is sent along so the backend links the account, but reports are accepted without it too.
//
// Two tabs:
//   Submit report -- type / content involved / description / contact / attachments
//   Check progress -- view handling status and reply by ticket number + the email used at submission (the "feedback" step of the loop)

import { useEffect, useState } from 'react';
import { PageHead } from '../components/PageHead';
import { fmtTime } from '../utils/datetime';
import {
  abuseReportApi, type ReportMeta, type ReportPublic, type ReportSubmitResult,
} from '../services/abuseReportApi';

const card: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border-color)',
};
const inputStyle: React.CSSProperties = {
  background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
};

export function Report({ embedded = false }: { embedded?: boolean } = {}) {
  const token = localStorage.getItem('token') || '';
  const [tab, setTab] = useState<'submit' | 'query'>('submit');
  const [meta, setMeta] = useState<ReportMeta | null>(null);

  // Submission form
  const [category, setCategory] = useState('content_illegal');
  const [targetUrl, setTargetUrl] = useState('');
  const [traceNo, setTraceNo] = useState('');
  const [description, setDescription] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<ReportSubmitResult | null>(null);

  // Check progress
  const [qNo, setQNo] = useState('');
  const [qEmail, setQEmail] = useState('');
  const [qBusy, setQBusy] = useState(false);
  const [qErr, setQErr] = useState<string | null>(null);
  const [qHit, setQHit] = useState<ReportPublic | null>(null);

  useEffect(() => {
    abuseReportApi.meta()
      .then(m => { setMeta(m); if (m.categories[0]) setCategory(m.categories[0].key); })
      .catch(() => setMeta(null));
  }, []);

  const submit = async () => {
    if (busy) return;
    setErr(null);
    if (description.trim().length < 5) { setErr('请简要描述问题(至少 5 个字)'); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('category', category);
      fd.append('description', description.trim());
      fd.append('target_url', targetUrl.trim());
      fd.append('target_trace_no', traceNo.trim());
      fd.append('contact_name', name.trim());
      fd.append('contact_email', email.trim());
      fd.append('contact_phone', phone.trim());
      if (file) fd.append('attachment', file);
      setDone(await abuseReportApi.submit(fd, token || undefined));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const query = async () => {
    if (qBusy) return;
    setQErr(null); setQHit(null); setQBusy(true);
    try {
      setQHit(await abuseReportApi.query(qNo.trim(), qEmail.trim()));
    } catch (e) {
      setQErr(e instanceof Error ? e.message : String(e));
    } finally {
      setQBusy(false);
    }
  };

  const slaDays = meta?.sla_days ?? 7;

  return (
    // embedded: when mounted in the account center sidebar, no full-page frame; padding is left to the shell
    <div className={embedded ? '' : 'min-h-screen grid-background relative'}>
      <PageHead titleKey="pageMeta.report.title" titleFallback="投诉与举报"
                descriptionKey="pageMeta.report.description" />
      <div className={embedded ? 'space-y-6' : 'max-w-3xl mx-auto px-6 py-16 space-y-6'}>
        <div>
          <h1 className="text-3xl font-bold text-primary mb-3">投诉与举报</h1>
          <p className="text-sm text-secondary leading-relaxed">
            如果你发现本平台生成或发布的内容存在违法违规、侵权、涉及个人信息或其他问题,
            可以通过本页面向我们举报。<b className="text-primary">无需注册或登录</b>,
            我们会在 <b className="text-primary">{slaDays} 个自然日</b>内完成核查并向你反馈处理结果。
          </p>
          {meta?.abuse_email && (
            <p className="text-sm text-secondary mt-2">
              你也可以直接发送邮件至{' '}
              <a href={`mailto:${meta.abuse_email}`}
                 className="text-accent-primary hover:underline">{meta.abuse_email}</a>。
            </p>
          )}
        </div>

        <div className="flex gap-2">
          {([['submit', '提交举报'], ['query', '查询进度']] as const).map(([k, label]) => (
            <button key={k} type="button" onClick={() => setTab(k)}
                    className="text-sm px-4 py-1.5 rounded-md"
                    style={tab === k
                      ? { background: 'var(--accent-primary)', color: '#fff' }
                      : { color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
              {label}
            </button>
          ))}
        </div>

        {tab === 'submit' && (
          done ? (
            // After a successful submission keep only the receipt -- the ticket number is the reporter's only credential for checking progress later; don't let the form crowd it out
            <div className="rounded-xl p-6 space-y-3" style={card}>
              <p className="text-lg font-semibold text-primary">✓ 举报已受理</p>
              <p className="text-sm text-secondary">{done.message}</p>
              <p className="text-sm">
                <span className="text-muted">工单号:</span>{' '}
                <code className="text-primary text-base">{done.report_no}</code>
              </p>
              {done.due_at && (
                <p className="text-xs text-muted">承诺反馈时限:{fmtTime(done.due_at, false)}</p>
              )}
              <div className="flex gap-2 pt-2">
                <button type="button"
                        onClick={() => { setQNo(done.report_no); setQEmail(email); setTab('query'); }}
                        className="text-xs px-3 py-1.5 rounded-md text-white"
                        style={{ background: 'var(--accent-primary)' }}>
                  查询进度
                </button>
                <button type="button"
                        onClick={() => { setDone(null); setDescription(''); setTargetUrl('');
                                         setTraceNo(''); setFile(null); }}
                        className="text-xs px-3 py-1.5 rounded-md"
                        style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
                  再提交一条
                </button>
              </div>
            </div>
          ) : (
            <div className="rounded-xl p-6 space-y-4" style={card}>
              <div>
                <label className="block text-xs text-muted mb-1.5">问题类型 *</label>
                <div className="flex flex-wrap gap-2">
                  {(meta?.categories || []).map(c => (
                    <button key={c.key} type="button" onClick={() => setCategory(c.key)}
                            className="text-xs px-3 py-1.5 rounded-md"
                            style={category === c.key
                              ? { background: 'var(--accent-primary)', color: '#fff' }
                              : { color: 'var(--text-secondary)',
                                  border: '1px solid var(--border-color)' }}>
                      {c.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-muted mb-1.5">涉及内容链接</label>
                  <input className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                         placeholder="https://…" value={targetUrl}
                         onChange={e => setTargetUrl(e.target.value)} />
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1.5">内容编号</label>
                  <input className="w-full rounded-md px-3 py-2 text-sm font-mono" style={inputStyle}
                         placeholder="VGL-… 或 VGI-…" value={traceNo}
                         onChange={e => setTraceNo(e.target.value)} />
                  <p className="text-[11px] text-muted mt-1">
                    我们生成的内容都带编号:文章在正文末尾标识行里,图片在文件属性中。
                    填上能帮我们更快定位。
                  </p>
                </div>
              </div>

              <div>
                <label className="block text-xs text-muted mb-1.5">问题描述 *</label>
                <textarea rows={5} className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                          placeholder="请说明具体问题,例如涉及哪段内容、为什么认为存在问题"
                          value={description} onChange={e => setDescription(e.target.value)} />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-muted mb-1.5">你的称呼</label>
                  <input className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                         value={name} onChange={e => setName(e.target.value)} />
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1.5">联系邮箱</label>
                  <input className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                         placeholder="用于接收处理结果" value={email}
                         onChange={e => setEmail(e.target.value)} />
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1.5">联系电话</label>
                  <input className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                         value={phone} onChange={e => setPhone(e.target.value)} />
                </div>
              </div>
              <p className="text-[11px] text-muted -mt-2">
                联系方式非必填,匿名举报同样受理;但未留邮箱时我们无法主动反馈,
                只能由你凭工单号自行查询。
              </p>

              <div>
                <label className="block text-xs text-muted mb-1.5">佐证附件</label>
                <input type="file" className="text-xs text-secondary"
                       accept={(meta?.allowed_ext || []).join(',')}
                       onChange={e => setFile(e.target.files?.[0] || null)} />
                <p className="text-[11px] text-muted mt-1">
                  支持 {(meta?.allowed_ext || ['.png', '.jpg', '.pdf'])
                        .map(x => x.replace('.', '')).join(' / ')},
                  单个文件不超过 {meta?.max_attach_mb ?? 5} MB。
                </p>
              </div>

              {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

              <div className="flex justify-end">
                <button type="button" onClick={submit} disabled={busy}
                        className="text-sm px-5 py-2 rounded-md text-white disabled:opacity-40"
                        style={{ background: 'var(--accent-primary)' }}>
                  {busy ? '提交中…' : '提交举报'}
                </button>
              </div>
            </div>
          )
        )}

        {tab === 'query' && (
          <div className="rounded-xl p-6 space-y-4" style={card}>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-muted mb-1.5">工单号 *</label>
                <input className="w-full rounded-md px-3 py-2 text-sm font-mono" style={inputStyle}
                       placeholder="VGR-…" value={qNo}
                       onChange={e => setQNo(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1.5">提交时填写的邮箱</label>
                <input className="w-full rounded-md px-3 py-2 text-sm" style={inputStyle}
                       value={qEmail} onChange={e => setQEmail(e.target.value)}
                       onKeyDown={e => { if (e.key === 'Enter') query(); }} />
              </div>
            </div>
            <div className="flex justify-end">
              <button type="button" onClick={query} disabled={qBusy || !qNo.trim()}
                      className="text-sm px-5 py-2 rounded-md text-white disabled:opacity-40"
                      style={{ background: 'var(--accent-primary)' }}>
                {qBusy ? '查询中…' : '查询'}
              </button>
            </div>

            {qErr && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{qErr}</div>}

            {qHit && (
              <div className="rounded-lg p-4 space-y-2 text-sm"
                   style={{ background: 'var(--bg-tertiary)' }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <code className="text-primary">{qHit.report_no}</code>
                  <span className="text-xs px-2 py-0.5 rounded-full"
                        style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--info-fg)' }}>
                    {qHit.status_label}
                  </span>
                  <span className="text-xs text-muted">{qHit.category_label}</span>
                </div>
                <p className="text-xs text-muted">
                  提交时间 {fmtTime(qHit.created_at, false)}
                  {qHit.due_at && ` · 承诺反馈 ${fmtTime(qHit.due_at, false)}`}
                  {qHit.resolved_at && ` · 办结 ${fmtTime(qHit.resolved_at, false)}`}
                </p>
                {qHit.reply ? (
                  <div>
                    <p className="text-xs text-muted mb-1">处理结论</p>
                    <p className="text-secondary whitespace-pre-wrap">{qHit.reply}</p>
                  </div>
                ) : (
                  <p className="text-xs text-muted">正在处理中,结论出来后会在这里显示。</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
