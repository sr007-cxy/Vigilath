// Model usage -- call detail table (backend paginated) + drill-down modal for a single business action.
// The repo has no generic Modal, so the modal is hand-written fixed inset-0 + useOverlayDismiss (consistent with AdminAllRuns).
import { useEffect, useRef, useState } from 'react';
import { Pagination } from '../../../components/Pagination';
import { useOverlayDismiss } from '../../../components/ui/useOverlayDismiss';
import {
  CALL_KIND_LABEL, SERVICE_LABEL, STATUS_LABEL, adminLlmUsageApi, fmtInt,
} from '../../../services/adminLlmUsageApi';
import type { UsageLogItem, UsageLogPage, UsageTraceDetail } from '../../../services/adminLlmUsageApi';
import { featureLabel } from './UsageFeatureRank';

function statusStyle(status: string) {
  if (status === 'ok') return { color: 'var(--success-fg)' };
  if (status === 'truncated' || status === 'parse_error') return { color: 'var(--warn-fg)' };
  return { color: 'var(--error-fg, #e06c6c)' };
}

function tokenCell(v: number | null) {
  // NULL = not obtained, 0 = the provider explicitly returned 0 -- different semantics, can't both display as 0
  return v === null || v === undefined
    ? <span className="text-muted" title="provider 未返回 token 用量">—</span>
    : <span className="tabular-nums">{fmtInt(v)}</span>;
}

function TraceModal({ traceId, token, onClose }: {
  traceId: string; token: string; onClose: () => void;
}) {
  const [data, setData] = useState<UsageTraceDetail | null>(null);
  const [err, setErr] = useState('');
  const boxRef = useRef<HTMLDivElement>(null);
  const dismiss = useOverlayDismiss(onClose);

  useEffect(() => {
    let alive = true;
    adminLlmUsageApi.trace(token, traceId)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setErr(String(e.message || e)); });
    return () => { alive = false; };
  }, [traceId, token]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.45)' }} {...dismiss}>
      <div ref={boxRef} className="w-full max-w-3xl max-h-[80vh] overflow-auto rounded-md p-4"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-semibold text-primary">一次业务动作的全部调用</h3>
          <button type="button" onClick={onClose}
                  className="ml-auto text-xs text-muted hover:text-primary">关闭</button>
        </div>
        <div className="text-[11px] text-muted mb-3 break-all">追踪号 {traceId}</div>
        {err && <div className="text-xs" style={{ color: 'var(--warn-fg)' }}>{err}</div>}
        {data && (
          <>
            <div className="flex gap-4 text-xs text-secondary mb-3">
              <span>共 <b className="text-primary tabular-nums">{fmtInt(data.calls)}</b> 次调用</span>
              <span>合计 <b className="text-primary tabular-nums">{fmtInt(data.total_tokens)}</b> token</span>
              <span>累计耗时 <b className="text-primary tabular-nums">
                {(data.latency_ms / 1000).toFixed(1)}</b> 秒</span>
            </div>
            <table className="w-full text-[11px]">
              <thead className="text-muted">
                <tr>
                  <th className="text-left py-1">时间</th>
                  <th className="text-left">场景</th>
                  <th className="text-left">模型</th>
                  <th className="text-left">状态</th>
                  <th className="text-right">输入</th>
                  <th className="text-right">输出</th>
                  <th className="text-right">耗时</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                    <td className="py-1 text-muted">{(r.created_at || '').slice(11, 19)}</td>
                    <td className="text-secondary">{featureLabel(r.feature)}</td>
                    <td className="text-muted">{r.model}</td>
                    <td style={statusStyle(r.status)}>
                      {STATUS_LABEL[r.status] || r.status}
                      {r.attempt_no > 0 && <span className="text-muted">(重试{r.attempt_no})</span>}
                    </td>
                    <td className="text-right">{tokenCell(r.prompt_tokens)}</td>
                    <td className="text-right">{tokenCell(r.completion_tokens)}</td>
                    <td className="text-right tabular-nums text-muted">
                      {(r.latency_ms / 1000).toFixed(1)}s
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

export function UsageLogTable({
  data, loading, token, page, pageSize, onPageChange, onPageSizeChange,
}: {
  data: UsageLogPage | null;
  loading: boolean;
  token: string;
  page: number;
  pageSize: number;
  onPageChange: (p: number) => void;
  onPageSizeChange: (s: number) => void;
}) {
  const [trace, setTrace] = useState<string>('');
  const items: UsageLogItem[] = data?.items || [];

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ minWidth: 900 }}>
          <thead className="text-muted">
            <tr>
              <th className="text-left py-1.5 px-2">时间</th>
              <th className="text-left px-2">来源</th>
              <th className="text-left px-2">场景</th>
              <th className="text-left px-2">模型</th>
              <th className="text-left px-2">通道</th>
              <th className="text-left px-2">状态</th>
              <th className="text-right px-2">输入</th>
              <th className="text-right px-2">输出</th>
              <th className="text-right px-2">缓存</th>
              <th className="text-right px-2">耗时</th>
              <th className="text-left px-2">归属</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id} style={{ borderTop: '1px solid var(--border-color)' }}
                  className="hover:bg-[var(--bg-tertiary)]">
                <td className="py-1.5 px-2 text-muted whitespace-nowrap">
                  {(r.created_at || '').replace('T', ' ').slice(5, 19)}
                </td>
                <td className="px-2 text-secondary whitespace-nowrap">
                  {SERVICE_LABEL[r.service] || r.service}
                  {r.call_kind !== 'chat' && (
                    <span className="text-muted"> · {CALL_KIND_LABEL[r.call_kind] || r.call_kind}</span>
                  )}
                </td>
                <td className="px-2 text-secondary">{featureLabel(r.feature)}</td>
                <td className="px-2 text-muted whitespace-nowrap">{r.model || '—'}</td>
                <td className="px-2 text-muted whitespace-nowrap" title="请求实际走的出口域名">
                  {r.endpoint_host || '—'}
                </td>
                <td className="px-2 whitespace-nowrap" style={statusStyle(r.status)}>
                  {STATUS_LABEL[r.status] || r.status}
                  {r.http_status && r.status !== 'ok' && (
                    <span className="text-muted"> {r.http_status}</span>
                  )}
                  {r.attempt_no > 0 && <span className="text-muted">·重试{r.attempt_no}</span>}
                </td>
                <td className="px-2 text-right">{tokenCell(r.prompt_tokens)}</td>
                <td className="px-2 text-right">{tokenCell(r.completion_tokens)}</td>
                <td className="px-2 text-right">{tokenCell(r.cached_tokens)}</td>
                <td className="px-2 text-right tabular-nums text-muted whitespace-nowrap">
                  {(r.latency_ms / 1000).toFixed(1)}s
                </td>
                <td className="px-2 text-muted whitespace-nowrap">
                  {r.topic_id ? `项目#${r.topic_id}` : ''}
                  {r.user_id ? ` 用户#${r.user_id}` : ''}
                  {r.trace_id && (
                    <button type="button" onClick={() => setTrace(r.trace_id)}
                            className="ml-1 text-accent hover:underline">下钻</button>
                  )}
                </td>
              </tr>
            ))}
            {!items.length && !loading && (
              <tr><td colSpan={11} className="py-8 text-center text-muted">该条件下没有调用记录</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {loading && <div className="text-xs text-muted py-2">加载中…</div>}

      <Pagination className="mt-3" page={page} pageSize={pageSize} total={data?.total || 0}
                  onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} />

      {trace && <TraceModal traceId={trace} token={token} onClose={() => setTrace('')} />}
    </div>
  );
}
