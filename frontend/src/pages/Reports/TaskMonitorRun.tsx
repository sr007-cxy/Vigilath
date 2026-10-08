// Task monitor · single batch run detail page (standalone route /task-monitor/runs/:runId).
// Reached via "View details" in the "Task monitor" list (no longer a modal). Lists each (model × question) answer of that run;
// clicking "View" on a row expands the full answer + citations inside the table, no longer a drawer from the right.
// token from localStorage (consistent with AdminRunDetail); run metadata prefers location.state (carried from the list),
// and on direct refresh/deep link without state, hits/answers/errors are computed from responses as a fallback.

import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import i18n from '../../i18n';
import { PageHead } from '../../components/PageHead';
import { engineLabel, canonicalEngines } from '../../constants/engines';
import { aiTelemetryApi, type AdminRun, type ResponseRow } from '../../services/aiTelemetryApi';
import { Pagination } from '../../components/Pagination';

function useZh(): boolean {
  return (i18n.language || 'zh').startsWith('zh');
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function StatusChip({ status, zh }: { status: string; zh: boolean }) {
  const map: Record<string, { bg: string; fg: string; zh: string; en: string }> = {
    success: { bg: 'var(--success-bg)', fg: '#15803d', zh: '成功', en: 'Success' },
    running: { bg: 'rgba(59,130,246,0.15)', fg: '#1d4ed8', zh: '运行中', en: 'Running' },
    failed: { bg: 'var(--danger-bg)', fg: '#b91c1c', zh: '失败', en: 'Failed' },
  };
  const m = map[status] || { bg: 'rgba(148,163,184,0.18)', fg: '#475569', zh: status, en: status };
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: m.bg, color: m.fg }}>
      {zh ? m.zh : m.en}
    </span>
  );
}

export function TaskMonitorRun() {
  const zh = useZh();
  const T = (z: string, e: string) => (zh ? z : e);
  const token = localStorage.getItem('token') || '';
  const { runId: runIdParam } = useParams();
  const runId = Number(runIdParam);
  const location = useLocation();
  const stateRun = (location.state as { run?: AdminRun } | null)?.run ?? null;

  const [rows, setRows] = useState<ResponseRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [engine, setEngine] = useState('');
  const [selectedRow, setSelectedRow] = useState<ResponseRow | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  useEffect(() => {
    if (!Number.isFinite(runId)) { setErr('bad run id'); setLoading(false); return; }
    setLoading(true);
    setErr(null);
    aiTelemetryApi.listResponses(runId, token)
      .then(setRows)
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [runId, token]);

  const engines = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) s.add(r.engine);
    return canonicalEngines(Array.from(s));
  }, [rows]);
  // Reverse order: latest answers on top (time descending, id descending for the same moment)
  const filtered = useMemo(() => {
    const base = engine ? rows.filter(r => r.engine === engine) : rows;
    return [...base].sort((a, b) => {
      const ta = new Date(a.created_at).getTime();
      const tb = new Date(b.created_at).getTime();
      return (tb - ta) || (b.id - a.id);
    });
  }, [rows, engine]);
  useEffect(() => {
    if (selectedRow && !filtered.some(r => r.id === selectedRow.id)) setSelectedRow(null);
  }, [filtered, selectedRow]);

  // Pagination (back to page one when filters/page size change)
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedRows = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);
  useEffect(() => { setPage(1); }, [engine, pageSize]);

  // Metadata: prefer stateRun carried from the list, otherwise compute from responses
  const derived = useMemo(() => {
    let hit = 0, errs = 0;
    for (const r of rows) { if (r.hit) hit++; if (r.error) errs++; }
    return { total: rows.length, hit, errs };
  }, [rows]);
  const hitCount = stateRun?.hit_count ?? derived.hit;
  const respCount = stateRun?.response_count ?? derived.total;
  const errCount = stateRun?.error_count ?? derived.errs;

  return (
    <div className="min-h-[calc(100vh-4rem)]" style={{ background: 'var(--bg-primary)' }}>
      <PageHead titleKey="pageMeta.dashboard.title" descriptionKey="pageMeta.dashboard.description" />

      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 py-6 grid gap-4">
        {/* Breadcrumb + batch run metadata */}
        <div>
          <Link to="/task-monitor" className="text-xs text-accent hover:underline">
            ← {T('返回任务监控', 'Back to Task monitoring')}
          </Link>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold text-primary">Run #{runId}</h1>
            {stateRun && <StatusChip status={stateRun.status} zh={zh} />}
          </div>
          <div className="mt-1 text-xs text-muted flex flex-wrap gap-x-4 gap-y-1">
            {stateRun && (
              <span>{T('开始', 'Started')}:<span className="text-secondary tabular-nums">{fmtDateTime(stateRun.started_at)}</span></span>
            )}
            {stateRun?.finished_at && (
              <span>{T('完成', 'Finished')}:<span className="text-secondary tabular-nums">{fmtDateTime(stateRun.finished_at)}</span></span>
            )}
            <span>{T('命中/答复', 'Hits/Answers')}:<span className="text-secondary tabular-nums">{hitCount}/{respCount}</span></span>
            {errCount > 0 && <span style={{ color: 'var(--danger-fg)' }}>{T('错误', 'Errors')} {errCount}</span>}
          </div>
        </div>

        {/* Engine filter */}
        {engines.length > 1 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <button type="button" onClick={() => setEngine('')} className="px-2 py-0.5 rounded-full"
              style={engine === ''
                ? { background: 'var(--bg-tertiary)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }
                : { color: 'var(--text-muted)', border: '1px solid transparent' }}>
              {T(`全部模型 (${rows.length})`, `All models (${rows.length})`)}
            </button>
            {engines.map(e => (
              <button key={e} type="button" onClick={() => setEngine(e)} className="px-2 py-0.5 rounded-full"
                style={engine === e
                  ? { background: 'var(--bg-tertiary)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }
                  : { color: 'var(--text-muted)', border: '1px solid transparent' }}>
                {engineLabel(e)} ({rows.filter(r => r.engine === e).length})
              </button>
            ))}
          </div>
        )}

        {selectedRow && <InlineAnswerPanel row={selectedRow} onClose={() => setSelectedRow(null)} zh={zh} />}

        {/* Answer list */}
        <div className="rounded-lg overflow-auto"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          {loading ? (
            <div className="text-xs text-muted py-12 text-center">{T('加载模型回复中…', 'Loading answers…')}</div>
          ) : err ? (
            <div className="text-xs py-12 text-center" style={{ color: 'var(--danger-fg)' }}>{err}</div>
          ) : filtered.length === 0 ? (
            <div className="text-xs text-muted py-12 text-center">{T('该跑批没有模型回复', 'No answers in this run')}</div>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10" style={{ background: 'var(--bg-card)' }}>
                <tr className="text-muted" style={{ boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
                  <th className="text-right px-3 py-2 w-14">#</th>
                  <th className="text-left px-3 py-2 w-32">{T('引擎', 'Engine')}</th>
                  <th className="text-left px-3 py-2">{T('问题', 'Query')}</th>
                  <th className="text-center px-3 py-2 w-20">{T('状态', 'Status')}</th>
                  <th className="text-left px-3 py-2 w-40">{T('时间', 'Time')}</th>
                  <th className="text-right px-3 py-2 w-16">{T('返回', 'Answer')}</th>
                </tr>
              </thead>
              <tbody>
                {pagedRows.map(r => (
                  <tr key={r.id} className="border-t hover:bg-black/[.02]" style={{ borderColor: 'var(--border-color)' }}>
                    <td className="px-3 py-2 text-right tabular-nums text-muted">{r.id}</td>
                    <td className="px-3 py-2 text-primary font-medium">{engineLabel(r.engine)}</td>
                    <td className="px-3 py-2 text-secondary truncate max-w-0" title={r.query}>{r.query}</td>
                    <td className="px-3 py-2 text-center">
                      {r.error ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: 'var(--danger-bg)', color: '#b91c1c' }}>{T('失败', 'Failed')}</span>
                      ) : r.hit ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: 'var(--success-bg)', color: '#15803d' }}>{T('命中', 'Hit')}</span>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: 'rgba(148,163,184,0.18)', color: '#475569' }}>{T('未命中', 'Miss')}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted tabular-nums">{fmtDateTime(r.created_at)}</td>
                    <td className="px-3 py-2 text-right">
                      <button type="button" onClick={() => setSelectedRow(r)} className="text-xs text-accent hover:underline">
                        {selectedRow?.id === r.id ? T('已展开', 'Open') : T('查看', 'View')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {!loading && !err && filtered.length > 0 && (
          <Pagination page={safePage} pageSize={pageSize} total={filtered.length}
            onPageChange={setPage} onPageSizeChange={setPageSize} />
        )}
      </div>

    </div>
  );
}

function InlineAnswerPanel({ row, onClose, zh }: { row: ResponseRow; onClose: () => void; zh: boolean }) {
  const T = (z: string, e: string) => (zh ? z : e);
  return (
    <section className="rounded-xl overflow-hidden" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="px-4 py-3 flex flex-wrap items-start justify-between gap-3" style={{ background: 'var(--bg-tertiary)', borderBottom: '1px solid var(--border-color)' }}>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-primary">{engineLabel(row.engine)} · {T('答复详情', 'Answer detail')}</div>
          <div className="mt-1 text-xs text-muted truncate max-w-[900px]">{row.query}</div>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="px-1.5 py-0.5 rounded" style={row.error ? { background: 'var(--danger-bg)', color: '#b91c1c' } : row.hit ? { background: 'var(--success-bg)', color: '#15803d' } : { background: 'rgba(148,163,184,0.18)', color: '#475569' }}>
            {row.error ? T('失败', 'Failed') : row.hit ? T('命中', 'Hit') : T('未命中', 'Miss')}
          </span>
          {row.brand_rank != null && <span className="text-secondary">{T('排名', 'Rank')} #{row.brand_rank}</span>}
          <button type="button" onClick={onClose} className="text-muted hover:text-primary px-1.5 py-0.5">✕</button>
        </div>
      </div>
      <div className="grid gap-3 p-4">
        {row.source_url || row.video_url ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted">{T('证据', 'Evidence')}</span>
            {row.source_url && <a href={row.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">{T('查看原始对话', 'Open original chat')} ↗</a>}
            {row.video_url && <a href={row.video_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">{T('查看录像', 'Watch recording')} ↗</a>}
          </div>
        ) : null}
        {row.error ? (
          <div className="rounded-lg px-3 py-3 text-xs whitespace-pre-wrap break-words" style={{ background: 'var(--danger-bg)', border: '1px solid rgba(239,68,68,0.35)', color: '#b91c1c' }}>{row.error}</div>
        ) : (
          <div className="rounded-lg px-4 py-3 text-sm leading-relaxed text-primary whitespace-pre-wrap break-words" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>{row.answer || T('(空答复)', '(empty answer)')}</div>
        )}
        {!!row.citations?.length && (
          <div>
            <div className="text-xs text-muted mb-2">{T('引用来源', 'Citations')} · {row.citations.length}</div>
            <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
              {row.citations.map((c, i) => (
                <a key={i} href={c.url} target="_blank" rel="noreferrer" className="rounded-lg px-3 py-2 text-xs hover:shadow-sm" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }} title={c.title || c.url}>
                  <div className="text-primary font-medium truncate">{c.title || c.url}</div>
                  <div className="text-muted truncate mt-1">{c.url}</div>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
