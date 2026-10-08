// Audit results overview card -- the six-metric card embedded in the GEO strategy optimization plan page (same source and metric definition as the report overview).
// 2026-07-30 replaces RunProgressCard: the plan page doesn't show the batch run process, only audit results;
// the metric cards reuse the report center's KpiCardRow (labels from the Reports/lang dictionary) + the kpi-summary data layer, no separate aggregation.
// While the first-round baseline audit is in progress, only a one-line text hint is shown with polling refresh.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { adminReviewApi } from '../services/adminReviewApi';
import { reportsApi, type KpiMetric } from '../services/reportsApi';
import { KpiCardRow, KpiRowSkeleton } from '../pages/Reports/components';

export function TopicKpiOverviewCard({ topicId }: { topicId: number }) {
  const token = localStorage.getItem('token') || '';
  const [metrics, setMetrics] = useState<KpiMetric[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<{ done: number; total: number } | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchOnce = useCallback(async () => {
    // Metrics: same metric definition as the report overview (weekly); failure is treated as no data
    try {
      const s = await reportsApi.kpiSummary(topicId, token, 'weekly');
      setMetrics(s.metrics);
    } catch {
      setMetrics([]);
    } finally {
      setLoading(false);
    }
    // Is the baseline audit running: silent probe, 404 (not started)/failure both count as not running
    try {
      const p = await adminReviewApi.getExecutionPlan(topicId, token);
      if (p.run_status === 'running') {
        setRunning({ done: p.progress_done, total: p.progress_total });
        return;   // keep polling
      }
    } catch { /* not started / fetch failed -> don't show the in-progress hint */ }
    setRunning(null);
    stopPoll();
  }, [topicId, token, stopPoll]);

  useEffect(() => {
    if (!topicId) return;
    fetchOnce();
    pollRef.current = window.setInterval(fetchOnce, 5000);
    return stopPoll;
  }, [topicId, fetchOnce, stopPoll]);

  // No data for any period at all (sparklines all empty) -> empty state
  const noData = metrics !== null && metrics.every(m => (m.sparkline || []).length === 0);

  return (
    <section className="rounded-md p-4"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold text-primary">检测结果总览</h2>
        <div className="flex-1" />
        <Link to={`/reports?topic=${topicId}`}
              className="text-xs hover:underline"
              style={{ color: 'var(--accent-primary)' }}>
          查看完整报告 →
        </Link>
      </div>

      {running && (
        <div className="rounded-md px-3 py-2 mb-3 text-xs flex items-center gap-2"
             style={{ background: 'rgba(234,179,8,0.08)', color: 'var(--warn-fg)',
                      border: '1px solid rgba(234,179,8,0.25)' }}>
          <div className="animate-spin rounded-full h-3 w-3 border-2 border-t-transparent shrink-0"
               style={{ borderColor: 'var(--warn-fg)', borderTopColor: 'transparent' }} />
          首轮基线检测进行中({running.done}/{running.total}),指标将随检测完成自动更新
        </div>
      )}

      {loading && metrics === null ? (
        <KpiRowSkeleton />
      ) : noData ? (
        <div className="rounded-md p-4 text-center text-xs text-muted"
             style={{ background: 'var(--bg-secondary)', border: '1px dashed var(--border-color)' }}>
          暂无检测数据 — 项目启动并完成首轮基线检测后,这里展示各项指标
        </div>
      ) : (
        <KpiCardRow metrics={metrics || []} />
      )}
    </section>
  );
}
