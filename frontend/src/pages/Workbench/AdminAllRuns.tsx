// admin 跨用户 / 跨主题 / 每日跑批结果总览
// 路由:/workbench/runs
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { aiTelemetryApi, type AdminAccount, type AdminRun, type ResponseRow } from '../../services/aiTelemetryApi';

function today(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    success: { bg: 'rgba(34,197,94,0.15)', fg: '#15803d', label: '成功' },
    running: { bg: 'rgba(59,130,246,0.15)', fg: '#1d4ed8', label: '运行中' },
    failed: { bg: 'rgba(239,68,68,0.15)', fg: '#b91c1c', label: '失败' },
  };
  const m = map[status] || { bg: 'rgba(148,163,184,0.18)', fg: '#475569', label: status };
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ background: m.bg, color: m.fg }}>
      {m.label}
    </span>
  );
}

interface DetailState {
  loading: boolean;
  err: string | null;
  rows: ResponseRow[];
}

export function AdminAllRuns() {
  const token = localStorage.getItem('token') || '';
  const [day, setDay] = useState<string>(today());
  const [userId, setUserId] = useState<number | ''>('');
  const [status, setStatus] = useState<string>('');
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [runs, setRuns] = useState<AdminRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [details, setDetails] = useState<Map<number, DetailState>>(new Map());

  const toggleExpand = useCallback((runId: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
    setDetails(prev => {
      if (prev.has(runId)) return prev;
      const next = new Map(prev);
      next.set(runId, { loading: true, err: null, rows: [] });
      aiTelemetryApi.listResponses(runId, token)
        .then(rows => {
          setDetails(curr => {
            const m = new Map(curr);
            m.set(runId, { loading: false, err: null, rows });
            return m;
          });
        })
        .catch(e => {
          setDetails(curr => {
            const m = new Map(curr);
            m.set(runId, {
              loading: false,
              err: e instanceof Error ? e.message : String(e),
              rows: [],
            });
            return m;
          });
        });
      return next;
    });
  }, [token]);

  useEffect(() => {
    aiTelemetryApi.adminListAccounts(token)
      .then(setAccounts)
      .catch(() => setAccounts([]));
  }, [token]);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    aiTelemetryApi.adminListRuns(token, {
      day: day || undefined,
      userId: userId === '' ? undefined : userId,
      status: status || undefined,
      limit: 200,
    })
      .then(setRuns)
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [token, day, userId, status]);

  useEffect(() => { load(); }, [load]);

  const totals = useMemo(() => {
    let resp = 0, hit = 0;
    for (const r of runs) { resp += r.response_count; hit += r.hit_count; }
    return { runs: runs.length, resp, hit };
  }, [runs]);

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  };

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-xl font-semibold text-primary">跑批结果</h1>
        <p className="text-xs text-muted mt-1">
          跨用户 · 跨主题 · 按天列出每次跑批的命中统计;点 Run ID 跳到该主题的执行计划。
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="text-muted">日期</label>
        <input
          type="date"
          value={day}
          onChange={e => setDay(e.target.value)}
          className="px-2 py-1 rounded-md"
          style={{ ...inputStyle, minWidth: 140 }}
        />
        <button
          type="button"
          onClick={() => setDay('')}
          className="text-xs text-accent hover:underline"
          title="清空日期 = 列最近 200 条"
        >
          清空日期
        </button>

        <span className="mx-2 text-muted">·</span>

        <label className="text-muted">用户</label>
        <select
          value={userId === '' ? '' : String(userId)}
          onChange={e => setUserId(e.target.value === '' ? '' : Number(e.target.value))}
          className="px-2 py-1 rounded-md"
          style={{ ...inputStyle, minWidth: 200 }}
        >
          <option value="">全部用户</option>
          {accounts.map(a => (
            <option key={a.id} value={a.id}>
              #{a.id} {a.email}{a.is_admin ? ' (admin)' : ''}
            </option>
          ))}
        </select>

        <label className="text-muted">状态</label>
        <select
          value={status}
          onChange={e => setStatus(e.target.value)}
          className="px-2 py-1 rounded-md"
          style={{ ...inputStyle, minWidth: 100 }}
        >
          <option value="">全部</option>
          <option value="success">成功</option>
          <option value="running">运行中</option>
          <option value="failed">失败</option>
        </select>

        <button
          type="button"
          onClick={load}
          className="ml-2 px-2 py-1 rounded-md text-xs"
          style={{ ...inputStyle }}
        >
          刷新
        </button>

        <span className="ml-auto text-muted">
          {totals.runs} 次跑批 · 共 {totals.resp} 条答复 · 命中 {totals.hit}
        </span>
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
              <th className="w-6"></th>
              <th className="text-right px-2 py-2 w-16">Run ID</th>
              <th className="text-left px-2 py-2">用户</th>
              <th className="text-left px-2 py-2">主题</th>
              <th className="text-left px-2 py-2">品牌词</th>
              <th className="text-left px-2 py-2 w-40">开始时间</th>
              <th className="text-left px-2 py-2 w-40">完成时间</th>
              <th className="text-center px-2 py-2 w-16">状态</th>
              <th className="text-right px-2 py-2 w-20">命中/答复</th>
              <th className="text-right px-2 py-2 w-14">错误</th>
              <th className="text-right px-2 py-2 w-20"></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={11} className="px-3 py-10 text-center text-muted">加载中…</td></tr>
            ) : err ? (
              <tr><td colSpan={11} className="px-3 py-10 text-center" style={{ color: '#ef4444' }}>{err}</td></tr>
            ) : runs.length === 0 ? (
              <tr><td colSpan={11} className="px-3 py-10 text-center text-muted">没有符合条件的跑批</td></tr>
            ) : runs.map(r => {
              const isOpen = expanded.has(r.run_id);
              const detail = details.get(r.run_id);
              return (
                <Fragment key={r.run_id}>
                  <tr className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                    <td className="px-1 py-2 text-center">
                      <button
                        type="button"
                        onClick={() => toggleExpand(r.run_id)}
                        className="text-muted hover:text-primary text-xs"
                        aria-label={isOpen ? '收起' : '展开'}
                      >
                        {isOpen ? '▾' : '▸'}
                      </button>
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-primary">
                      <button
                        type="button"
                        onClick={() => toggleExpand(r.run_id)}
                        className="text-accent hover:underline"
                      >
                        #{r.run_id}
                      </button>
                    </td>
                    <td className="px-2 py-2 text-primary truncate max-w-[200px]" title={r.user_email}>
                      <span className="text-muted tabular-nums">#{r.user_id}</span> {r.user_email}
                    </td>
                    <td className="px-2 py-2 text-primary truncate max-w-[200px]" title={r.topic_name}>
                      {r.topic_name}
                    </td>
                    <td className="px-2 py-2 text-secondary truncate max-w-[160px]" title={r.topic_target}>
                      {r.topic_target || '—'}
                    </td>
                    <td className="px-2 py-2 text-secondary tabular-nums">{fmtDateTime(r.started_at)}</td>
                    <td className="px-2 py-2 text-secondary tabular-nums">{fmtDateTime(r.finished_at)}</td>
                    <td className="px-2 py-2 text-center"><StatusChip status={r.status} /></td>
                    <td className="px-2 py-2 text-right tabular-nums text-primary">
                      {r.hit_count}/{r.response_count}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums"
                        style={r.error_count > 0 ? { color: '#ef4444' } : undefined}>
                      {r.error_count}
                    </td>
                    <td className="px-2 py-2 text-right">
                      <Link to={`/workbench/topics/${r.topic_id}/execution-plan`} className="text-xs text-accent hover:underline">
                        执行计划
                      </Link>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr style={{ background: 'var(--bg-input)' }}>
                      <td></td>
                      <td colSpan={10} className="px-2 py-2">
                        <RunDetailTable detail={detail} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RunDetailTable({ detail }: { detail: DetailState | undefined }) {
  if (!detail || detail.loading) {
    return <div className="text-xs text-muted text-center py-4">加载详情中…</div>;
  }
  if (detail.err) {
    return <div className="text-xs text-center py-4" style={{ color: '#ef4444' }}>{detail.err}</div>;
  }
  if (detail.rows.length === 0) {
    return <div className="text-xs text-muted text-center py-4">该跑批没有 response 记录</div>;
  }
  return (
    <table className="w-full text-[11px]">
      <thead className="text-muted">
        <tr style={{ boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
          <th className="text-left px-2 py-1.5 w-24">模型</th>
          <th className="text-left px-2 py-1.5">问题</th>
          <th className="text-center px-2 py-1.5 w-14">命中</th>
          <th className="text-center px-2 py-1.5 w-12">排名</th>
          <th className="text-center px-2 py-1.5 w-14">段落</th>
          <th className="text-left px-2 py-1.5">答复 / 错误</th>
        </tr>
      </thead>
      <tbody>
        {detail.rows.map(r => (
          <tr key={r.id} className="border-t align-top" style={{ borderColor: 'var(--border-color)' }}>
            <td className="px-2 py-1.5 text-primary">{r.engine}</td>
            <td className="px-2 py-1.5 text-primary truncate max-w-[260px]" title={r.query}>{r.query}</td>
            <td className="px-2 py-1.5 text-center">
              {r.error ? (
                <span className="px-1 py-0.5 rounded text-[10px]"
                  style={{ background: 'rgba(239,68,68,0.15)', color: '#b91c1c' }}>错</span>
              ) : r.hit ? (
                <span className="px-1 py-0.5 rounded text-[10px]"
                  style={{ background: 'rgba(34,197,94,0.15)', color: '#15803d' }}>✓</span>
              ) : (
                <span className="text-muted">—</span>
              )}
            </td>
            <td className="px-2 py-1.5 text-center tabular-nums text-secondary">
              {r.brand_rank != null ? r.brand_rank : '—'}
            </td>
            <td className="px-2 py-1.5 text-center text-secondary">{r.mention_position || '—'}</td>
            <td className="px-2 py-1.5 text-secondary">
              {r.error ? (
                <span style={{ color: '#ef4444' }} title={r.error}>{r.error.slice(0, 200)}</span>
              ) : (
                <span title={r.answer}>{(r.answer || '').slice(0, 240) || '(空答复)'}{r.answer && r.answer.length > 240 ? '…' : ''}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
