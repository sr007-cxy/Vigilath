// Single run detail (with header filters) — admins view all responses of one batch run
// Route: /ops/runs/:runId
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { aiTelemetryApi, type AdminRun, type ResponseRow } from '../../services/aiTelemetryApi';
import { engineLabel, canonicalEngines } from '../../constants/engines';
import { UiSelect } from '../../components/ui/Select';
import { useOverlayDismiss } from '../../components/ui/useOverlayDismiss';

type HitFilter = '' | 'hit' | 'miss' | 'error';
type RankFilter = '' | 'top1' | 'top3' | 'top5' | 'ranked' | 'no_rank';

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    success: { bg: 'var(--success-bg)', fg: '#15803d', label: '成功' },
    running: { bg: 'rgba(59,130,246,0.15)', fg: '#1d4ed8', label: '运行中' },
    failed: { bg: 'var(--danger-bg)', fg: '#b91c1c', label: '失败' },
  };
  const m = map[status] || { bg: 'rgba(148,163,184,0.18)', fg: '#475569', label: status };
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ background: m.bg, color: m.fg }}>
      {m.label}
    </span>
  );
}

const REASONING_PREFIX = '[思考过程] ';

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Brand terms: target split on / + aliases, length ≥2 (same metric definition as backend metric_brand_terms), longer terms matched first
function brandTerms(target: string, aliases: string[]): string[] {
  const set = new Set<string>();
  for (const t of [...target.split('/'), ...aliases]) {
    const v = t.trim();
    if (v.length >= 2) set.add(v);
  }
  return Array.from(set).sort((a, b) => b.length - a.length);
}

function Mark({ children }: { children: ReactNode }) {
  return (
    <mark
      className="rounded px-0.5 font-semibold"
      style={{ background: 'var(--success-bg)', color: '#15803d', boxShadow: 'inset 0 -2px 0 #15803d' }}
    >
      {children}
    </mark>
  );
}

// Wrap fragments of text matching terms in <mark>; if no term matches at all, fall back to highlighting the whole fallback passage (hit_excerpt)
function highlightTerms(text: string, terms: string[], fallback?: string | null): ReactNode {
  if (!text) return text;
  const tryTerms = (list: string[]): ReactNode[] | null => {
    if (list.length === 0) return null;
    const re = new RegExp(`(${list.map(escapeRegExp).join('|')})`, 'gi');
    const parts = text.split(re);
    if (parts.length === 1) return null;
    return parts.map((seg, i) => (i % 2 === 1 ? <Mark key={i}>{seg}</Mark> : seg));
  };
  const fb = (fallback || '').replace(REASONING_PREFIX, '').trim();
  return tryTerms(terms) ?? (fb ? tryTerms([fb]) : null) ?? text;
}

function HitChip({ row }: { row: ResponseRow }) {
  if (row.error) {
    return <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: 'var(--danger-bg)', color: '#b91c1c' }}>错误</span>;
  }
  if (row.hit) {
    return <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: 'var(--success-bg)', color: '#15803d' }}>命中</span>;
  }
  return <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: 'rgba(148,163,184,0.18)', color: '#475569' }}>未命中</span>;
}

export function AdminRunDetail() {
  const { runId: runIdParam } = useParams();
  const runId = Number(runIdParam);
  const token = localStorage.getItem('token') || '';

  const [run, setRun] = useState<AdminRun | null>(null);
  const [rows, setRows] = useState<ResponseRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // Filters
  const [engineFilter, setEngineFilter] = useState<string>('');
  const [queryFilter, setQueryFilter] = useState<string>('');
  const [hitFilter, setHitFilter] = useState<HitFilter>('');
  const [rankFilter, setRankFilter] = useState<RankFilter>('');
  const [positionFilter, setPositionFilter] = useState<string>('');

  // Modal: the full answer of a single (model × question)
  const [selected, setSelected] = useState<ResponseRow | null>(null);
  const closeDetail = useCallback(() => setSelected(null), []);
  // Overlay close checks both mousedown and mouseup, so selecting text in the details and releasing outside the box doesn't close it by mistake
  const dismissDetail = useOverlayDismiss(closeDetail);
  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelected(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected]);

  // Brand aliases are only for highlighting; failing to fetch them doesn't affect the page
  const [aliases, setAliases] = useState<string[]>([]);
  useEffect(() => {
    if (!run) return;
    aiTelemetryApi.getTopic(run.topic_id, token)
      .then(t => setAliases(t.target_aliases || []))
      .catch(() => setAliases([]));
  }, [run, token]);
  const terms = useMemo(() => brandTerms(run?.topic_target || '', aliases), [run, aliases]);

  useEffect(() => {
    if (!Number.isFinite(runId)) return;
    setLoading(true);
    setErr(null);
    Promise.all([
      aiTelemetryApi.adminGetRun(runId, token),
      aiTelemetryApi.listResponses(runId, token),
    ])
      .then(([r, rs]) => { setRun(r); setRows(rs); })
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [runId, token]);

  const engines = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) s.add(r.engine);
    return canonicalEngines(Array.from(s));
  }, [rows]);

  const positions = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) if (r.mention_position) s.add(r.mention_position);
    return Array.from(s).sort();
  }, [rows]);

  const filtered = useMemo(() => {
    const q = queryFilter.trim().toLowerCase();
    return rows.filter(r => {
      if (engineFilter && r.engine !== engineFilter) return false;
      if (q && !(r.query || '').toLowerCase().includes(q)) return false;
      if (hitFilter === 'hit' && !r.hit) return false;
      if (hitFilter === 'miss' && (r.hit || r.error)) return false;
      if (hitFilter === 'error' && !r.error) return false;
      if (positionFilter && r.mention_position !== positionFilter) return false;
      const rank = r.brand_rank;
      if (rankFilter === 'top1' && rank !== 1) return false;
      if (rankFilter === 'top3' && !(typeof rank === 'number' && rank <= 3)) return false;
      if (rankFilter === 'top5' && !(typeof rank === 'number' && rank <= 5)) return false;
      if (rankFilter === 'ranked' && typeof rank !== 'number') return false;
      if (rankFilter === 'no_rank' && typeof rank === 'number') return false;
      return true;
    });
  }, [rows, engineFilter, queryFilter, hitFilter, positionFilter, rankFilter]);

  const inputStyle: CSSProperties = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  };
  const headerFilterStyle: CSSProperties = {
    ...inputStyle,
    fontSize: '10px',
    padding: '2px 6px',
    width: '100%',
    minWidth: 0,
  };

  if (loading) return <div className="text-xs text-muted py-10 text-center">加载中…</div>;
  if (err) return <div className="text-xs py-10 text-center" style={{ color: 'var(--danger-fg)' }}>{err}</div>;
  if (!run) return <div className="text-xs text-muted py-10 text-center">未找到该跑批</div>;

  return (
    <div className="grid gap-4">
      <div>
        <div className="flex items-center gap-2 text-xs text-muted">
          <Link to="/ops/runs" className="hover:underline">← 跑批结果</Link>
          <span>·</span>
          <span>Run #{run.run_id}</span>
        </div>
        <h1 className="text-xl font-semibold text-primary mt-1 flex items-center gap-2">
          {run.topic_name}
          <StatusChip status={run.status} />
        </h1>
        <div className="text-xs text-muted mt-1 flex flex-wrap gap-x-4 gap-y-1">
          <span>用户:<span className="text-primary">#{run.user_id} {run.user_email}</span></span>
          <span>品牌词:<span className="text-primary">{run.topic_target || '—'}</span></span>
          <span>开始:<span className="text-primary tabular-nums">{fmtDateTime(run.started_at)}</span></span>
          <span>完成:<span className="text-primary tabular-nums">{fmtDateTime(run.finished_at)}</span></span>
          <span>命中/答复:<span className="text-primary">{run.hit_count}/{run.response_count}</span></span>
          {run.error_count > 0 && (
            <span style={{ color: 'var(--danger-fg)' }}>错误 {run.error_count}</span>
          )}
          <Link
            to={`/ops/topics/${run.topic_id}/edit?step=5`}
            className="ml-auto text-accent hover:underline"
          >
            执行策略与规划 →
          </Link>
        </div>
        {run.error && (
          <div className="mt-2 text-xs px-3 py-2 rounded"
            style={{ background: 'var(--danger-bg)', color: '#b91c1c' }}>
            跑批错误:{run.error}
          </div>
        )}
      </div>

      <div className="text-xs text-muted">
        {filtered.length} / {rows.length} 条
      </div>

      <div
        className="rounded-lg overflow-auto"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          maxHeight: 'clamp(420px, 70vh, 760px)',
        }}
      >
        <table className="w-full text-xs">
          <thead className="sticky top-0 z-10" style={{ background: 'var(--bg-card)' }}>
            <tr className="text-muted" style={{ boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
              <th className="text-right px-2 py-2 w-12">序号</th>
              <th className="text-left px-2 py-2 w-28">模型</th>
              <th className="text-left px-2 py-2">问题</th>
              <th className="text-center px-2 py-2 w-20">命中</th>
              <th className="text-center px-2 py-2 w-20">排名</th>
              <th className="text-center px-2 py-2 w-20">段落</th>
              <th className="text-left px-2 py-2">答复 / 错误</th>
              <th className="px-2 py-2 w-14" />
            </tr>
            <tr style={{ boxShadow: 'inset 0 -1px 0 var(--border-color)', background: 'var(--bg-card)' }}>
              <th className="px-2 py-1.5" />
              <th className="px-2 py-1.5">
                <UiSelect value={engineFilter} onChange={setEngineFilter} style={headerFilterStyle}
                          options={[
                            { value: '', label: '全部' },
                            ...engines.map(e => ({ value: e, label: engineLabel(e) })),
                          ]} />
              </th>
              <th className="px-2 py-1.5">
                <input
                  type="text"
                  value={queryFilter}
                  onChange={e => setQueryFilter(e.target.value)}
                  placeholder="搜索问题文本"
                  style={headerFilterStyle}
                />
              </th>
              <th className="px-2 py-1.5">
                <UiSelect value={hitFilter} onChange={val => setHitFilter(val as HitFilter)} style={headerFilterStyle}
                          options={[
                            { value: '', label: '全部' },
                            { value: 'hit', label: '命中' },
                            { value: 'miss', label: '未命中' },
                            { value: 'error', label: '错误' },
                          ]} />
              </th>
              <th className="px-2 py-1.5">
                <UiSelect value={rankFilter} onChange={val => setRankFilter(val as RankFilter)} style={headerFilterStyle}
                          options={[
                            { value: '', label: '全部' },
                            { value: 'top1', label: 'Top1' },
                            { value: 'top3', label: 'Top1-3' },
                            { value: 'top5', label: 'Top1-5' },
                            { value: 'ranked', label: '有排名' },
                            { value: 'no_rank', label: '未抽出' },
                          ]} />
              </th>
              <th className="px-2 py-1.5">
                <UiSelect value={positionFilter} onChange={setPositionFilter} style={headerFilterStyle}
                          options={[
                            { value: '', label: '全部' },
                            ...positions.map(p => ({ value: p, label: p })),
                          ]} />
              </th>
              <th className="px-2 py-1.5" />
              <th className="px-2 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-10 text-center text-muted">当前筛选无匹配 response</td></tr>
            ) : filtered.map((r, i) => (
              <tr key={r.id} className="border-t align-top" style={{ borderColor: 'var(--border-color)' }}>
                <td className="px-2 py-2 text-right tabular-nums text-muted">{i + 1}</td>
                <td className="px-2 py-2 text-primary">{engineLabel(r.engine)}</td>
                <td className="px-2 py-2 text-primary truncate max-w-[280px]" title={r.query}>{r.query}</td>
                <td className="px-2 py-2 text-center">
                  {r.error ? (
                    <span className="px-1 py-0.5 rounded text-[10px]"
                      style={{ background: 'var(--danger-bg)', color: '#b91c1c' }}>错</span>
                  ) : r.hit ? (
                    <span className="px-1 py-0.5 rounded text-[10px]"
                      style={{ background: 'var(--success-bg)', color: '#15803d' }}>✓</span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td className="px-2 py-2 text-center tabular-nums text-secondary">
                  {r.brand_rank != null ? r.brand_rank : '—'}
                </td>
                <td className="px-2 py-2 text-center text-secondary">{r.mention_position || '—'}</td>
                <td className="px-2 py-2 text-secondary">
                  {r.error ? (
                    <span style={{ color: 'var(--danger-fg)' }} title={r.error}>{r.error.slice(0, 240)}</span>
                  ) : (
                    <span title={r.answer}>
                      {(r.answer || '').slice(0, 280) || '(空答复)'}
                      {r.answer && r.answer.length > 280 ? '…' : ''}
                    </span>
                  )}
                </td>
                <td className="px-2 py-2 text-right">
                  <button type="button" onClick={() => setSelected(r)} className="text-xs text-accent hover:underline">
                    详情
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.5)' }}
          {...dismissDetail}
        >
          <div
            className="rounded-lg flex flex-col w-full mx-4"
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-color)',
              maxWidth: 960,
              maxHeight: '86vh',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div className="px-5 pt-4 pb-3" style={{ borderBottom: '1px solid var(--border-color)' }}>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-sm font-medium text-primary">{engineLabel(selected.engine)}</span>
                <HitChip row={selected} />
                {selected.brand_rank != null && <span className="text-secondary">排名 {selected.brand_rank}</span>}
                {selected.mention_position && <span className="text-secondary">段落 {selected.mention_position}</span>}
                <span className="ml-auto text-muted tabular-nums">{fmtDateTime(selected.created_at)}</span>
                <button type="button" onClick={closeDetail} className="text-muted hover:text-primary px-1">✕</button>
              </div>
              <div className="text-sm text-primary mt-2 font-medium whitespace-pre-wrap break-words">{selected.query}</div>
              {(selected.source_url || selected.video_url) && (
                <div className="flex flex-wrap items-center gap-3 mt-2 text-xs">
                  <span className="text-muted">证据</span>
                  {selected.source_url && (
                    <a href={selected.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">查看原始对话 ↗</a>
                  )}
                  {selected.video_url && (
                    <a href={selected.video_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">查看录像 ↗</a>
                  )}
                </div>
              )}
            </div>

            <div className="px-5 py-4 overflow-y-auto grid gap-4 text-xs">
              <section>
                <div className="text-muted mb-1.5">回答内容</div>
                {selected.error ? (
                  <div className="rounded-md px-3 py-2.5 whitespace-pre-wrap break-words"
                    style={{ background: 'var(--danger-bg)', border: '1px solid rgba(239,68,68,0.35)', color: '#b91c1c' }}>
                    {selected.error}
                  </div>
                ) : (
                  <>
                    {selected.hit_excerpt && !selected.hit_excerpt.startsWith(REASONING_PREFIX) && (
                      <div className="text-muted italic mb-1.5">命中片段:「{selected.hit_excerpt}」</div>
                    )}
                    <div className="rounded-md px-3 py-2.5 text-primary whitespace-pre-wrap break-words leading-relaxed"
                      style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
                      {selected.answer ? highlightTerms(selected.answer, terms, selected.hit_excerpt) : <span className="text-muted">(空答复)</span>}
                    </div>
                  </>
                )}
              </section>

              <section>
                <div className="text-muted mb-1.5">引用来源 · {selected.citations?.length || 0}</div>
                {!selected.citations?.length ? (
                  <div className="text-muted">无引用</div>
                ) : (
                  <ul className="grid gap-1">
                    {selected.citations.map((c, i) => (
                      <li key={i} className="flex items-baseline gap-2 min-w-0">
                        <span className="text-muted tabular-nums shrink-0">[{i + 1}]</span>
                        <a href={c.url} target="_blank" rel="noreferrer" className="text-accent hover:underline truncate" title={c.url}>
                          {c.title || c.url}
                        </a>
                        {c.domain && <span className="text-muted shrink-0">· {c.domain}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section>
                <div className="text-muted mb-1.5">思维链</div>
                {selected.reasoning == null ? (
                  <div className="text-muted">未抓到思考过程</div>
                ) : selected.reasoning === '' ? (
                  <div className="text-muted">该条无思考段</div>
                ) : (
                  // Shown collapsed, not mixed into the body; when the hit is only in the reasoning, hit_excerpt carries the 「[思考过程]」 ("[reasoning]") prefix -> expanded by default
                  <details className="rounded-md px-3 py-2" style={{ border: '1px dashed var(--border-color)' }}
                    open={!!selected.hit_excerpt?.startsWith(REASONING_PREFIX)}>
                    <summary className="text-muted cursor-pointer select-none">
                      思考过程 ({selected.reasoning.length} 字)
                      {selected.hit_excerpt?.startsWith(REASONING_PREFIX) && (
                        <span className="ml-2" style={{ color: '#15803d' }}>命中在思考里</span>
                      )}
                    </summary>
                    <div className="mt-2 text-secondary whitespace-pre-wrap break-words leading-relaxed">
                      {highlightTerms(selected.reasoning, terms, selected.hit_excerpt)}
                    </div>
                  </details>
                )}
              </section>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
