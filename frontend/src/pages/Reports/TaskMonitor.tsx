// Task monitor — standalone page from the account dropdown. Lists every batch run (all time) of "the current account (current topic)",
// paginated; "View details" goes to the standalone page /task-monitor/runs/:runId to see each (model × question) answer of that run.
// Display matches the workbench "Batch run results" (AdminAllRuns), but drops the user / topic / brand term columns (redundant in one's own view).
// Data via /topics/{id}/runs + /runs/{id}/responses (backend owner check); you only see your own batch runs.

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import i18n from '../../i18n';
import { PageHead } from '../../components/PageHead';
import { useShellState } from '../../hooks/useTopicScope';
import { aiTelemetryApi, type AdminRun } from '../../services/aiTelemetryApi';
import { Pagination } from '../../components/Pagination';
import { UiSelect } from '../../components/ui/Select';
import { SingleDayPicker } from '../../components/ui/FilterBar';

function useZh(): boolean {
  return (i18n.language || 'zh').startsWith('zh');
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
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

export function TaskMonitor() {
  const state = useShellState();
  const zh = useZh();
  const T = (z: string, e: string) => (zh ? z : e);
  const navigate = useNavigate();
  const { token, topic, topics, topicId, setTopicId, loading: topicLoading } = state;

  const [day, setDay] = useState('');          // Default empty = all time
  const [status, setStatus] = useState('');
  const [runs, setRuns] = useState<AdminRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const load = useCallback(() => {
    if (!topic) return;
    setLoading(true);
    setErr(null);
    aiTelemetryApi.listTopicRuns(topic.id, token, {
      day: day || undefined, status: status || undefined, limit: 500,
    }).then(setRuns)
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [token, topic?.id, day, status]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [topicId, day, status, pageSize]);

  // "View details" goes to the standalone page, carrying run metadata (on a direct refresh that page falls back on its own)
  const openDetail = useCallback((r: AdminRun) => {
    navigate(`/task-monitor/runs/${r.run_id}`, { state: { run: r } });
  }, [navigate]);

  const totalPages = Math.max(1, Math.ceil(runs.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedRuns = runs.slice((safePage - 1) * pageSize, safePage * pageSize);
  const totals = useMemo(() => {
    let resp = 0, hit = 0;
    for (const r of runs) { resp += r.response_count; hit += r.hit_count; }
    return { runs: runs.length, resp, hit };
  }, [runs]);

  const inputStyle: CSSProperties = {
    background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)',
  };

  return (
    <div className="min-h-[calc(100vh-4rem)]" style={{ background: 'var(--bg-primary)' }}>
      <PageHead titleKey="pageMeta.dashboard.title" descriptionKey="pageMeta.dashboard.description" />

      {/* Header: title + topic (account) picker */}
      <div className="px-4 sm:px-6 lg:px-8 pt-3.5 pb-2.5 border-b backdrop-blur sticky top-16 z-30"
        style={{ borderColor: 'var(--border-color)', background: 'color-mix(in srgb, var(--bg-card) 94%, transparent)' }}>
        <div className="max-w-[1400px] mx-auto flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <span className="w-[30px] h-[30px] rounded-[7px] flex items-center justify-center flex-shrink-0"
              style={{ background: 'var(--accent-primary)', color: 'var(--accent-contrast)' }}>
              <svg className="w-[17px] h-[17px]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M22 12h-4l-3 9L9 3l-3 9H2" />
              </svg>
            </span>
            <h1 className="text-[15.5px] font-bold text-primary whitespace-nowrap">{T('任务监控', 'Task monitoring')}</h1>
            {topic && <span className="text-xs text-muted truncate">· {topic.name}</span>}
          </div>
          {topics.length > 1 && (
            <label className="text-xs text-secondary flex items-center gap-2">
              {T('主题', 'Topic')}
              <UiSelect value={String(topicId ?? '')} onChange={val => setTopicId(Number(val))}
                className="px-2 py-1 text-sm rounded" style={inputStyle}
                options={topics.map(t => ({ value: String(t.id), label: t.name }))} />
            </label>
          )}
        </div>
      </div>

      <main className="px-4 sm:px-6 lg:px-8 py-6">
        <div className="max-w-[1400px] mx-auto grid gap-4">
          {topicLoading ? (
            <div className="text-center py-20 text-muted">{T('加载中…', 'Loading…')}</div>
          ) : topics.length === 0 ? (
            <div className="max-w-md mx-auto py-20 text-center">
              <div className="text-lg font-medium text-primary mb-2">{T('还没有监测主题', 'No monitoring topics yet')}</div>
              <div className="text-sm text-muted">
                {T('监测主题由管理员配置,配置并跑批后这里会列出跑批结果。',
                  'Topics are set up by an admin; runs will appear here once configured.')}
              </div>
            </div>
          ) : (
            <>
              <p className="text-xs text-muted">
                {T('按次列出该账号的跑批;点「查看详情」看每条 (模型 × 问题) 答复。',
                  'Runs for this account; click Details to see each (model × query) answer.')}
              </p>

              {/* Filter row (date defaults to empty = all time) */}
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <label className="text-muted">{T('日期', 'Date')}</label>
                <SingleDayPicker value={day} onChange={setDay}
                  placeholder={T('全部日期', 'All dates')} />
                {day && (
                  <button type="button" onClick={() => setDay('')} className="text-xs text-accent hover:underline">
                    {T('清空日期', 'Clear date')}
                  </button>
                )}
                <span className="mx-1 text-muted">·</span>
                <label className="text-muted">{T('状态', 'Status')}</label>
                <UiSelect value={status} onChange={val => setStatus(val)}
                  className="px-2 py-1 rounded-md" style={{ ...inputStyle, minWidth: 100 }}
                  options={[
                    { value: '', label: T('全部', 'All') },
                    { value: 'success', label: T('成功', 'Success') },
                    { value: 'running', label: T('运行中', 'Running') },
                    { value: 'failed', label: T('失败', 'Failed') },
                  ]} />
                <button type="button" onClick={load} className="ml-2 px-2 py-1 rounded-md text-xs" style={inputStyle}>
                  {T('刷新', 'Refresh')}
                </button>
                <span className="ml-auto text-muted">
                  {T(`${totals.runs} 次跑批 · 共 ${totals.resp} 条答复 · 命中 ${totals.hit}`,
                    `${totals.runs} runs · ${totals.resp} answers · ${totals.hit} hits`)}
                </span>
              </div>

              {/* Batch run table */}
              <div className="rounded-lg overflow-auto"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', maxHeight: 'clamp(420px, 70vh, 760px)' }}>
                <table className="w-full text-xs">
                  <thead className="sticky top-0 z-10" style={{ background: 'var(--bg-card)' }}>
                    <tr className="text-muted" style={{ boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
                      <th className="text-right px-2 py-2 w-16">Run ID</th>
                      <th className="text-left px-2 py-2 w-44">{T('开始时间', 'Started')}</th>
                      <th className="text-left px-2 py-2 w-44">{T('完成时间', 'Finished')}</th>
                      <th className="text-center px-2 py-2 w-16">{T('状态', 'Status')}</th>
                      <th className="text-right px-2 py-2 w-24">{T('命中/答复', 'Hits/Answers')}</th>
                      <th className="text-right px-2 py-2 w-14">{T('错误', 'Errors')}</th>
                      <th className="text-right px-2 py-2 w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading ? (
                      <tr><td colSpan={7} className="px-3 py-10 text-center text-muted">{T('加载中…', 'Loading…')}</td></tr>
                    ) : err ? (
                      <tr><td colSpan={7} className="px-3 py-10 text-center" style={{ color: 'var(--danger-fg)' }}>{err}</td></tr>
                    ) : runs.length === 0 ? (
                      <tr><td colSpan={7} className="px-3 py-10 text-center text-muted">{T('没有符合条件的跑批', 'No runs match')}</td></tr>
                    ) : pagedRuns.map(r => (
                      <tr key={r.run_id} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                        <td className="px-2 py-2 text-right tabular-nums text-primary">#{r.run_id}</td>
                        <td className="px-2 py-2 text-secondary tabular-nums">{fmtDateTime(r.started_at)}</td>
                        <td className="px-2 py-2 text-secondary tabular-nums">{fmtDateTime(r.finished_at)}</td>
                        <td className="px-2 py-2 text-center"><StatusChip status={r.status} zh={zh} /></td>
                        <td className="px-2 py-2 text-right tabular-nums text-primary">{r.hit_count}/{r.response_count}</td>
                        <td className="px-2 py-2 text-right tabular-nums" style={r.error_count > 0 ? { color: 'var(--danger-fg)' } : undefined}>
                          {r.error_count}
                        </td>
                        <td className="px-2 py-2 text-right">
                          <button type="button" onClick={() => openDetail(r)} className="text-xs text-accent hover:underline">
                            {T('查看详情', 'Details')}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <Pagination page={safePage} pageSize={pageSize} total={runs.length}
                onPageChange={setPage} onPageSizeChange={setPageSize} />
            </>
          )}
        </div>
      </main>

    </div>
  );
}
