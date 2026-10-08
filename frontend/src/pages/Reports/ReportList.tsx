// Daily / weekly / monthly reports — each its own list section (periodType decided by the route).
// At the top, the five metric cards for that period's metric definition; below, the period list, click into details.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ReportsShell, type ShellState } from './shell';
import {
  reportsApi, type KpiMetric, type PeriodType, type ReportListItem,
} from '../../services/reportsApi';
import { useReportsLang } from './lang';
import { KpiCardRow, TrendBadge, TH, TD } from './components';

export function ReportList({ periodType }: { periodType: PeriodType }) {
  return (
    <ReportsShell>
      {(state) => <Body state={state} periodType={periodType} />}
    </ReportsShell>
  );
}

const PAGE_SIZE = 10;

function Body({ state, periodType }: { state: ShellState; periodType: PeriodType }) {
  const { token, topic } = state;
  const L = useReportsLang();
  const [metrics, setMetrics] = useState<KpiMetric[]>([]);
  const [items, setItems] = useState<ReportListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);

  useEffect(() => {
    if (!topic) return;
    setLoading(true);
    setPage(0);
    Promise.all([
      reportsApi.kpiSummary(topic.id, token, periodType),
      reportsApi.listReports(topic.id, token, periodType, 200),
    ]).then(([s, l]) => {
      setMetrics(s.metrics);
      setItems(l.items);
    }).catch(() => { setMetrics([]); setItems([]); })
      .finally(() => setLoading(false));
  }, [token, topic?.id, periodType]);

  if (!topic) return null;

  const prefix = { daily: L.dailyPrefix, weekly: L.weeklyPrefix, monthly: L.monthlyPrefix }[periodType];
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const pageItems = items.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div className="grid gap-4">
      {loading ? (
        <div className="text-xs text-muted text-center py-10">{L.loading}</div>
      ) : items.length === 0 ? (
        <div className="text-sm text-muted text-center py-16">{L.emptyReports}</div>
      ) : (
        <>
          <KpiCardRow metrics={metrics} />

          <div className="rounded-lg overflow-hidden"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <table className="w-full">
              <thead>
                <tr>
                  <TH>{L.colReport}</TH>
                  <TH>{L.colKpiSummary}</TH>
                  <TH right>{L.colResponses}</TH>
                  <TH right>{L.colOps}</TH>
                </tr>
              </thead>
              <tbody>
                {pageItems.map((it, idx) => {
                  const gi = page * PAGE_SIZE + idx;          // Global index: period-over-period uses the next period in the full list
                  const prev = items[gi + 1];
                  const delta = prev ? Math.round((it.exposure_pct - prev.exposure_pct) * 10) / 10 : null;
                  return (
                    <tr key={it.key}>
                      <TD>
                        <span className="text-primary font-medium">{prefix} · {it.key}</span>
                        {it.period_start !== it.period_end && (
                          <span className="text-muted ml-2 text-[10px]">
                            {it.period_start} ~ {it.period_end}
                          </span>
                        )}
                      </TD>
                      <TD>
                        <span className="text-secondary">
                          {L.metricLabels.exposure} {it.exposure_pct}%{' '}
                          <TrendBadge deltaPp={delta} />
                          <span className="text-muted"> · TOP1 {it.top1_pct}% · TOP3 {it.top3_pct}%</span>
                        </span>
                      </TD>
                      <TD right>{it.response_count}</TD>
                      <TD right>
                        <Link className="text-accent hover:underline"
                          to={`/reports/${periodType}/${it.key}?topic=${topic.id}`}>
                          {L.view}
                        </Link>
                      </TD>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {pageCount > 1 && (
              <div className="flex items-center justify-between px-3 py-2"
                style={{ borderTop: '1px solid var(--border-color)' }}>
                <span className="text-[11px] text-muted">{L.detailCount(items.length)}</span>
                <span className="flex items-center gap-2">
                  <button className="px-2.5 py-1 text-xs rounded disabled:opacity-40"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}
                    disabled={page === 0} onClick={() => setPage(p => p - 1)}>
                    {L.pagePrev}
                  </button>
                  <span className="text-[11px] text-muted tabular-nums">{page + 1} / {pageCount}</span>
                  <button className="px-2.5 py-1 text-xs rounded disabled:opacity-40"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}
                    disabled={page >= pageCount - 1} onClick={() => setPage(p => p + 1)}>
                    {L.pageNext}
                  </button>
                </span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
