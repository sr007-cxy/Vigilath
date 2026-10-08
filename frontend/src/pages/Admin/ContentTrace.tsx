// Generation provenance -- reverse lookup page for implicit labels under the "Measures for Labeling AI-Generated Synthetic Content".
// Route: /ops/content-trace   Backend: geo/api/content_trace.py (require_workbench)
//
// Deliverable page for item 5 of the algorithm filing correction list: every AI-generated draft can be traced back by content number to
// "generator + IP + time + model + content number". Three ways to look up:
//   1. Ledger list -- recent generation records, directly showing whether collection is in place
//   2. Reverse lookup by content number -- with a number (in plaintext in the explicit label line), locate it directly
//   3. Paste body to decode -- after content is reposted off-site, paste the body back to read the zero-width watermark
// This page is read-only and changes no data.

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHead } from '../../components/PageHead';
import { fmtTime } from '../../utils/datetime';
import { contentTraceApi, type ContentTrace } from '../../services/contentTraceApi';

const card: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border-color)',
};
const inputStyle: React.CSSProperties = {
  background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
};

type TraceScope = 'text' | 'image' | 'all';
const SCOPES: { key: TraceScope; label: string }[] = [
  { key: 'text', label: '文本稿' },
  { key: 'image', label: 'AI配图' },
  { key: 'all', label: '全部' },
];

/** Label present or not is rendered with the same badge -- this column is what evidence screenshots look at */
function MarkBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded-md whitespace-nowrap"
          style={ok
            ? { background: 'var(--success-bg)', color: 'var(--success-fg)' }
            : { background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
      {ok ? '✓' : '✕'} {label}
    </span>
  );
}

/** Field table of a single provenance record -- shared by lookup results and expanded list rows; text drafts and AI images share one layout */
function TraceCard({ r }: { r: ContentTrace }) {
  const isImage = r.kind === 'image';
  // 'system' is a placeholder for automated batch runs, not a real IP; say so truthfully so reviewers don't think collection is missing
  const auto = r.gen_client_ip === 'system';
  const rows: [string, React.ReactNode][] = isImage ? [
    ['内容编号', <code className="text-primary">{r.trace_no || '—'}</code>],
    ['文件名', r.media_filename || r.title],
    ['内容来源', '本平台AI生成合成(配图)'],
    ['生成时间', fmtTime(r.generated_at)],
    ['出图模型', r.llm_model || '—'],
    ['归属客户', r.owner_email || '—'],
    ['所属项目', `${r.topic_name || '—'}(#${r.topic_id})`],
    ['文件大小', `${Math.round((r.body_chars || 0) / 1024)} KB`],
  ] : [
    ['内容编号', <code className="text-primary">{r.trace_no || '—'}</code>],
    ['文章标题', r.title || `#${r.doc_id}`],
    ['内容来源', r.source === 'user' ? '客户自带稿(非AI生成)' : '本平台AI生成合成'],
    ['生成时间', fmtTime(r.generated_at)],
    ['最后覆写', fmtTime(r.updated_at)],
    ['调用模型', r.llm_model || '—'],
    ['生成人', r.gen_user_email || (r.gen_user_id ? `用户 #${r.gen_user_id}` : '—')],
    ['生成IP', auto ? '自动跑批(无人工触发)' : (r.gen_client_ip || '未采集')],
    ['所属项目', `${r.topic_name || '—'}(#${r.topic_id})`],
    ['归属客户', r.owner_email || '—'],
    ['正文字数', `${r.body_chars} 字符`],
  ];
  const meta = r.media_meta || {};
  return (
    <div className="rounded-xl p-4 space-y-3" style={card}>
      <div className="flex items-center gap-2 flex-wrap">
        <MarkBadge ok={r.has_explicit_mark} label={isImage ? '图片编号已落' : '界面标识在位'} />
        <MarkBadge ok={r.has_implicit_mark} label={isImage ? '文件元数据在位' : '溯源台账在位'} />
        {isImage ? (
          r.media_url && (
            <a href={r.media_url} target="_blank" rel="noreferrer"
               className="text-[11px] underline text-muted hover:text-primary ml-auto">
              打开原图 →
            </a>
          )
        ) : (
          <a href={`/ops/content-review/${r.doc_id}`}
             className="text-[11px] underline text-muted hover:text-primary ml-auto">
            查看原稿 →
          </a>
        )}
      </div>

      {/* Images are shown as-is -- the bottom-right watermark is visible to the naked eye, more convincing than any text description */}
      {isImage && r.media_url && (
        <img src={r.media_url} alt={r.media_filename || ''}
             className="rounded-lg max-h-[280px] w-auto"
             style={{ border: '1px solid var(--border-color)' }} />
      )}

      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <dt className="text-muted shrink-0 w-[64px]">{k}</dt>
            <dd className="text-secondary break-all">{v}</dd>
          </div>
        ))}
      </dl>

      {/* Metadata actually read back from the image file -- exactly what item 7's evidence requires */}
      {isImage && Object.keys(meta).length > 0 && (
        <div className="rounded-md p-2 text-[11px] space-y-0.5"
             style={{ background: 'var(--bg-tertiary)' }}>
          <p className="text-muted mb-1">文件内嵌元数据(从图片本身读回,非数据库)</p>
          {Object.entries(meta)
            .filter(([k]) => !['Description', 'XPComment', 'Software'].includes(k))
            .map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <span className="text-muted shrink-0 w-[130px] font-mono">{k}</span>
                <span className="text-secondary break-all">{v}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

export function AdminContentTrace() {
  const token = localStorage.getItem('token') || '';
  // Entering with a number from the "Provenance" link on the article detail page (?q=VGL-…) lands it in the lookup box and runs it once automatically
  const [searchParams] = useSearchParams();
  const presetNo = searchParams.get('q') || '';

  const [items, setItems] = useState<ContentTrace[]>([]);
  const [total, setTotal] = useState(0);
  const [untraced, setUntraced] = useState(0);
  const [q, setQ] = useState('');
  // Text drafts and AI images are viewed separately: their label forms differ (body label line vs watermark on the image),
  // and mixing them in one column leaves reviewers unable to tell which corresponds to which list item
  const [kind, setKind] = useState<TraceScope>('text');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // The lookup area (number / paste body) is independent of the list, with results rendered separately, convenient for single-screen screenshots
  const [lookupNo, setLookupNo] = useState(presetNo);
  const [pasted, setPasted] = useState('');
  const [hit, setHit] = useState<ContentTrace | null>(null);
  const [hitMsg, setHitMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Closes over no state: keyword / kind are always passed explicitly by the call site. Default params combined with
  // useCallback would read the previous round's kind, so after switching tabs, clicking "Filter" would query the wrong table.
  const reload = useCallback(async (keyword: string, k: TraceScope) => {
    setLoading(true);
    try {
      const r = await contentTraceApi.list(token, { q: keyword || undefined, kind: k, limit: 50 });
      setItems(r.items);
      setTotal(r.total);
      setUntraced(r.untraced);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { reload('', 'text'); }, [reload]);

  // Auto-lookup once when entering with a number (only once; afterwards triggered manually by the user)
  useEffect(() => {
    if (!presetNo) return;
    contentTraceApi.get(presetNo, token)
      .then(setHit)
      .catch(e => setHitMsg(e instanceof Error ? e.message : String(e)));
  }, [presetNo, token]);

  const run = async (key: string, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(key); setHit(null); setHitMsg(null);
    try { await fn(); } catch (e) {
      setHitMsg(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  };

  const lookup = () => run('lookup', async () => {
    const no = lookupNo.trim();
    if (!no) return;
    setHit(await contentTraceApi.get(no, token));
  });

  const extract = () => run('extract', async () => {
    if (!pasted.trim()) return;
    const r = await contentTraceApi.extract(pasted, token);
    if (r.record) setHit(r.record);
    setHitMsg(r.message || null);
  });

  return (
    <div className="space-y-4">
      <PageHead titleKey="admin.contentTrace.title" titleFallback="生成溯源" />

      <div>
        <h1 className="text-xl font-semibold text-primary">生成溯源</h1>
        <p className="text-xs text-muted mt-1">
          每篇由本平台生成的内容都带一个内容编号:文本稿的编号记在后台溯源台账里(编号 VGL-…,
          正文本身不带标注,稿件要投到站外媒体),AI 配图以角标水印 + 文件元数据双重标注(编号 VGI-…)。
          按编号即可追回生成人、生成IP、生成时间与所调用的模型。
        </p>
      </div>

      {/* Lookup area -- number lookup + paste-body decoding, two paths side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl p-4 space-y-2" style={card}>
          <label className="text-xs text-muted">按内容编号反查</label>
          <div className="flex gap-2">
            <input className="flex-1 rounded-md px-3 py-2 text-sm font-mono"
                   style={inputStyle}
                   placeholder="VGL-CXSY-20260816-000123"
                   value={lookupNo}
                   onChange={e => setLookupNo(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter') lookup(); }} />
            <button type="button" onClick={lookup} disabled={busy !== null || !lookupNo.trim()}
                    className="text-xs px-3 py-1.5 rounded-md text-white disabled:opacity-40"
                    style={{ background: 'var(--accent-primary)' }}>
              {busy === 'lookup' ? '查询中…' : '反查'}
            </button>
          </div>
          <p className="text-[11px] text-muted">
            编号在文章详情页的标识条上,格式 VGL-主体码-日期-序号。
          </p>
        </div>

        <div className="rounded-xl p-4 space-y-2" style={card}>
          <label className="text-xs text-muted">粘贴正文反解(内容被转载后核验)</label>
          <textarea rows={3} className="w-full rounded-md px-3 py-2 text-sm"
                    style={inputStyle}
                    placeholder="把从站外复制回来的正文整段粘进来"
                    value={pasted} onChange={e => setPasted(e.target.value)} />
          <div className="flex items-center gap-2">
            <button type="button" onClick={extract} disabled={busy !== null || !pasted.trim()}
                    className="text-xs px-3 py-1.5 rounded-md text-white disabled:opacity-40"
                    style={{ background: 'var(--accent-primary)' }}>
              {busy === 'extract' ? '解析中…' : '解析标识'}
            </button>
            <span className="text-[11px] text-muted">
              零宽水印随复制粘贴一起走;对方清洗过零宽字符时退回读明文编号。
            </span>
          </div>
        </div>
      </div>

      {hitMsg && (
        <div className="rounded-md p-2 text-xs"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {hitMsg}
        </div>
      )}
      {hit && (
        <div className="space-y-2">
          <p className="text-xs text-muted">反查结果</p>
          <TraceCard r={hit} />
        </div>
      )}

      {/* Ledger list */}
      <div className="flex items-center gap-2 flex-wrap pt-2">
        <h2 className="text-sm font-semibold text-primary">生成台账</h2>
        <div className="flex rounded-md overflow-hidden"
             style={{ border: '1px solid var(--border-color)' }}>
          {SCOPES.map(s => (
            <button key={s.key} type="button"
                    onClick={() => { setKind(s.key); reload(q, s.key); }}
                    className="text-xs px-2.5 py-1"
                    style={kind === s.key
                      ? { background: 'var(--accent-primary)', color: '#fff' }
                      : { color: 'var(--text-secondary)' }}>
              {s.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted">共 {total} 条</span>
        <input className="rounded-md px-3 py-1.5 text-xs w-[280px]"
               style={inputStyle}
               placeholder="按内容编号或标题筛选"
               value={q}
               onChange={e => setQ(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') reload(q, kind); }} />
        <button type="button" onClick={() => reload(q, kind)} disabled={loading}
                className="text-xs px-3 py-1.5 rounded-md disabled:opacity-40"
                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
          {loading ? '加载中…' : '筛选'}
        </button>
      </div>

      {untraced > 0 && (
        <div className="rounded-md p-2 text-xs"
             style={{ background: 'rgba(234,179,8,0.10)', color: 'var(--warn-fg)',
                      border: '1px solid rgba(234,179,8,0.3)' }}>
          其中 {untraced} 条是内容标识功能上线前生成的存量稿,当时未采集溯源信息,故编号为空。
          不做回填 —— 补录一个当时并不存在的 IP 就是伪造台账。
        </div>
      )}

      {err && (
        <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>
      )}

      <div className="rounded-xl overflow-x-auto" style={card}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted" style={{ borderBottom: '1px solid var(--border-color)' }}>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">内容编号</th>
              <th className="text-left font-normal px-3 py-2">标题</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">生成人</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">生成IP</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">生成时间</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">模型</th>
              <th className="text-left font-normal px-3 py-2 whitespace-nowrap">标识</th>
            </tr>
          </thead>
          <tbody>
            {items.map(r => (
              <tr key={`${r.kind}-${r.kind === 'image' ? r.media_id : r.doc_id}`}
                  style={{ borderBottom: '1px solid var(--border-color)' }}>
                <td className="px-3 py-2 font-mono text-secondary whitespace-nowrap">
                  {r.trace_no || <span className="text-muted">未采集</span>}
                </td>
                <td className="px-3 py-2 text-secondary">
                  <a href={r.kind === 'image'
                            ? (r.media_url || '#')
                            : `/ops/content-review/${r.doc_id}`}
                     {...(r.kind === 'image' ? { target: '_blank', rel: 'noreferrer' } : {})}
                     className="hover:underline">
                    {r.kind === 'image' ? '🖼 ' : ''}{r.title || `#${r.doc_id}`}
                  </a>
                </td>
                <td className="px-3 py-2 text-secondary whitespace-nowrap">
                  {r.gen_user_email || (r.gen_user_id ? `#${r.gen_user_id}` : '—')}
                </td>
                <td className="px-3 py-2 text-secondary whitespace-nowrap">
                  {r.kind === 'image'
                    ? <span className="text-muted">随稿件生成</span>
                    : r.gen_client_ip === 'system'
                      ? <span className="text-muted">自动跑批</span>
                      : (r.gen_client_ip || <span className="text-muted">—</span>)}
                </td>
                <td className="px-3 py-2 text-secondary whitespace-nowrap">
                  {fmtTime(r.generated_at, false)}
                </td>
                <td className="px-3 py-2 text-secondary whitespace-nowrap">{r.llm_model || '—'}</td>
                <td className="px-3 py-2">
                  <div className="flex gap-1">
                    <MarkBadge ok={r.has_explicit_mark} label="显式" />
                    <MarkBadge ok={r.has_implicit_mark} label="隐式" />
                  </div>
                </td>
              </tr>
            ))}
            {!loading && items.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-8 text-center text-muted">没有记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
