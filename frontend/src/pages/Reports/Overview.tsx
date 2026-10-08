// Report overview — the module home's "summary report":
// (1) target progress banner (X/5 metrics met, contract metric definition +30%)
// (2) five metric cards (weekly metric definition, baseline -> current + target progress)
// (3) daily trend of overall exposure rate (last 30 periods)
// (4) summary cards for the latest daily / weekly / monthly reports, one click into details

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Area, CartesianGrid, ComposedChart, ReferenceArea, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { ReportsShell, type ShellState } from './shell';
import {
  reportsApi, type BasisChange, type CorrectionRow, type KpiMetric, type PeriodType,
  type ReportListItem,
} from '../../services/reportsApi';
import { useReportsLang } from './lang';
import { InfoHint, KpiCardRow, KpiRowSkeleton, METRIC_HUE, METRIC_TINT, SectionCard, SectionSkeleton } from './components';
import {
  AreaGradientDefs, BasisChangeNote, basisChangeLines, chartAxisProps, chartGridProps,
  chartTooltipLabelStyle, chartTooltipStyle,
} from './chartkit';
import { CompetitiveFull } from './Competitive';
import { CorrectionsTabs } from './CorrectionsTabs';
import { exportElementPdf, exportReportPdfServer } from './exportPdf';

export function ReportsOverview() {
  const L = useReportsLang();
  const [exporting, setExporting] = useState(false);
  const pageRef = useRef<HTMLDivElement>(null);

  const onExport = async (state: ShellState) => {
    if (!pageRef.current || exporting || !state.topic) return;
    setExporting(true);
    try {
      const today = new Date().toISOString().slice(0, 10);
      const filename = `结案报告-${state.topic.name}-${today}.pdf`;
      if (!(await exportReportPdfServer(state.token, state.topic.id, filename))) {
        await exportElementPdf(pageRef.current, filename);
      }
    } finally {
      setExporting(false);
    }
  };

  return (
    <ReportsShell navRight={(state) => state.topic ? (
      <button type="button" onClick={() => onExport(state)} disabled={exporting}
        className="px-3 py-1.5 text-xs rounded-lg hover:opacity-90 disabled:opacity-50"
        style={{ background: 'var(--accent-primary)', color: 'var(--accent-contrast)' }}>
        {exporting ? L.exporting : L.exportClosing}
      </button>
    ) : null}>
      {(state) => <Body state={state} pageRef={pageRef} />}
    </ReportsShell>
  );
}

interface LatestBundle {
  daily: ReportListItem | null;
  weekly: ReportListItem | null;
  monthly: ReportListItem | null;
}

interface TrendPoint { key: string; exposure: number; num: number; den: number }

// Trend chart tooltip: date + overall exposure rate + change vs. previous period (▲ green / ▼ red) + raw numerator/denominator.
// The numerator and denominator are direct evidence of metric definition changes -- seeing "valid responses 40 -> 62" explains why the curve jumped.
// active/payload/label are injected by recharts; series/metricLabel are business inputs
function TrendTooltip({ active, payload, label, series, metricLabel, numDen, changedLabel }: {
  active?: boolean; payload?: { value: number }[]; label?: string;
  series: TrendPoint[]; metricLabel: string;
  numDen: (num: number, den: number) => string;
  changedLabel: (key: string) => string | null;
}) {
  if (!active || !payload?.length || !label) return null;
  const idx = series.findIndex(p => p.key === label);
  const cur = idx >= 0 ? series[idx] : null;
  const prev = idx > 0 ? series[idx - 1].exposure : null;
  const v = payload[0].value;
  const dd = prev !== null ? Math.round((v - prev) * 10) / 10 : null;
  const changed = cur ? changedLabel(cur.key) : null;
  return (
    <div style={{ ...chartTooltipStyle, padding: '6px 10px' }}>
      <div style={chartTooltipLabelStyle}>{label}</div>
      <div className="tabular-nums text-xs" style={{ color: 'var(--text-primary)' }}>
        {metricLabel} <b>{v}%</b>
        {dd !== null && (
          <span style={{ color: dd >= 0 ? 'var(--success-fg)' : 'var(--danger-fg)', marginLeft: 6 }}>
            {dd >= 0 ? '▲' : '▼'} {Math.abs(dd)}
          </span>
        )}
      </div>
      {cur && cur.den > 0 && (
        <div className="tabular-nums text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {numDen(cur.num, cur.den)}
        </div>
      )}
      {changed && (
        <div className="text-[10px] mt-0.5" style={{ color: 'var(--warn-fg)' }}>⚑ {changed}</div>
      )}
    </div>
  );
}

function Body({ state, pageRef }: { state: ShellState; pageRef: React.RefObject<HTMLDivElement | null> }) {
  const { token, topic } = state;
  const L = useReportsLang();
  const trendGradientId = `rpt-trend-${useId().replace(/[^a-zA-Z0-9-]/g, '')}`;
  const [weeklyMetrics, setWeeklyMetrics] = useState<KpiMetric[]>([]);
  const [dailySeries, setDailySeries] = useState<TrendPoint[]>([]);
  const [basisChanges, setBasisChanges] = useState<BasisChange[]>([]);
  const [latest, setLatest] = useState<LatestBundle>({ daily: null, weekly: null, monthly: null });
  const [corrections, setCorrections] = useState<CorrectionRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!topic) return;
    setLoading(true);
    Promise.all([
      reportsApi.kpiSummary(topic.id, token, 'weekly'),
      reportsApi.listReports(topic.id, token, 'daily', 30),
      reportsApi.listReports(topic.id, token, 'weekly', 1),
      reportsApi.listReports(topic.id, token, 'monthly', 1),
    ]).then(([wk, dl, wl, ml]) => {
      setWeeklyMetrics(wk.metrics);
      // The x axis uses MM-DD (year dropped); metric definition change markers must be truncated the same way to line up
      setDailySeries([...dl.items].reverse().map(it => ({
        key: it.key.slice(5), exposure: it.exposure_pct,
        num: it.exposure_num, den: it.exposure_den,
      })));
      setBasisChanges(dl.basis_changes ?? []);
      setLatest({ daily: dl.items[0] ?? null, weekly: wl.items[0] ?? null, monthly: ml.items[0] ?? null });
      // Correction and breakthrough records: taken from the latest monthly report (same source and style as monthly report section 4)
      if (ml.items[0]) {
        reportsApi.monthlyReport(topic.id, token, ml.items[0].key)
          .then(m => setCorrections(m.corrections)).catch(() => setCorrections([]));
      } else {
        setCorrections([]);
      }
    }).catch(() => {
      setWeeklyMetrics([]); setDailySeries([]); setCorrections([]); setBasisChanges([]);
      setLatest({ daily: null, weekly: null, monthly: null });
    }).finally(() => setLoading(false));
  }, [token, topic?.id, L]);   // L changes with the language -> refetch bilingual text (correction records etc.)

  // Metric card mini-chart timeline: last point = start date of the latest weekly report, stepping back 7 days/point for three tick dates [start, middle, end].
  // Note: hooks must be called before all early returns
  const sparkLabels = useMemo(() => {
    const n = weeklyMetrics[0]?.sparkline.length ?? 0;
    const end = latest.weekly?.period_start;
    if (!end || n < 2) return null;
    const fmt = (backWeeks: number) => {
      const d = new Date(`${end}T00:00:00`);
      d.setDate(d.getDate() - backWeeks * 7);
      return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    };
    return [fmt(n - 1), fmt(Math.floor((n - 1) / 2)), fmt(0)];
  }, [weeklyMetrics, latest.weekly]);

  if (!topic) return null;
  if (loading) {
    return (
      <div className="grid gap-4">
        <KpiRowSkeleton n={6} />
        <SectionSkeleton rows={3} />
        <SectionSkeleton chart />
      </div>
    );
  }
  if (weeklyMetrics.length === 0 && !latest.daily) {
    return (
      <div className="rpt-card py-16 text-sm text-muted text-center">{L.emptyReports}</div>
    );
  }

  const withTarget = weeklyMetrics.filter(m => m.target !== null);
  const metCount = withTarget.filter(m => m.progress === 'met').length;
  const allMet = withTarget.length > 0 && metCount >= 3;
  // Option A: pale gradient background + 4px semantic-color left edge + large count + per-metric colored progress blocks
  const bannerConf = allMet
    ? { fg: 'var(--success-fg)', solid: 'var(--success-solid)' }
    : { fg: 'var(--warn-fg)', solid: 'var(--warn-solid)' };
  const progressConf = (p: string | null) => p === 'met'
    ? { color: 'var(--success-solid)', text: L.progressMet }
    : p === 'near'
      ? { color: 'var(--warn-solid)', text: L.progressNear }
      : p === 'missed'
        ? { color: 'var(--danger-solid)', text: L.progressMissed }
        : { color: 'var(--bg-input)', text: L.noTarget };

  // Target marker: the target is the exposure rate target from the weekly KPI (baseline × 1.3); the first day ≥ target = target-met day
  const exposureTarget = weeklyMetrics.find(m => m.key === 'exposure')?.target ?? null;
  const firstMetKey = exposureTarget !== null
    ? (dailySeries.find(p => p.exposure >= exposureTarget)?.key ?? null) : null;
  const lastKey = dailySeries.length ? dailySeries[dailySeries.length - 1].key : null;

  // Metric definition changes: keep only those within the current 30-period window (the backend already detects by window; this is defensive)
  const dayKeys = new Set(dailySeries.map(p => p.key));
  const visibleChanges = basisChanges.filter(c => dayKeys.has(c.key.slice(5)));
  const changedLabelOf = (k: string) => {
    const hit = visibleChanges.filter(c => c.key.slice(5) === k);
    return hit.length ? hit.map(c => L.basisChangeKinds[c.kind]).join(' · ') : null;
  };

  // Option A: daily/weekly/monthly report cards distinguished by three top-edge colors (blue/purple/green, from metric color variables, adapting to all three themes)
  const latestCards: { type: PeriodType; label: string; item: ReportListItem | null; hue: string; tint: string }[] = [
    { type: 'daily', label: L.dailyPrefix, item: latest.daily, hue: METRIC_HUE.exposure, tint: METRIC_TINT.exposure },
    { type: 'weekly', label: L.weeklyPrefix, item: latest.weekly, hue: METRIC_HUE.top1, tint: METRIC_TINT.top1 },
    { type: 'monthly', label: L.monthlyPrefix, item: latest.monthly, hue: METRIC_HUE.accuracy, tint: METRIC_TINT.accuracy },
  ];

  return (
    <div className="grid gap-4 animate-fade-in" ref={pageRef} data-export-root>
      {withTarget.length > 0 && (
        <div className="rpt-card grid items-center gap-3.5 px-4 py-3"
          style={{
            borderLeft: `4px solid ${bannerConf.fg}`,
            gridTemplateColumns: 'auto auto minmax(0, 1fr) auto',
            backgroundImage:
              'linear-gradient(100deg, rgba(37,99,235,0.06), rgba(124,58,237,0.05) 55%, rgba(219,39,119,0.04))',
          }}>
          <span className="rpt-num leading-none whitespace-nowrap" style={{ color: bannerConf.fg }}>
            <span className="text-[26px] font-bold">{metCount}</span>
            <span className="text-xs font-semibold opacity-60">/{withTarget.length}</span>
          </span>
          {/* Target progress blocks: one cell per metric, colored by its target status (green = met, yellow = close, red = not met) */}
          <span className="grid grid-flow-col gap-[4px]">
            {withTarget.map(m => {
              const conf = progressConf(m.progress);
              return (
                <span key={m.key} className="w-5 h-2.5 rounded-[3px]"
                  title={`${L.metricLabels[m.key]}:${conf.text}`}
                  style={{ background: conf.color }} />
              );
            })}
          </span>
          <span className="text-[12.5px] min-w-0" style={{ color: 'var(--text-secondary)' }}>
            <span className="font-bold" style={{ color: 'var(--text-primary)' }}>
              {L.overviewBanner(metCount, withTarget.length)}
            </span>
            <span className="ml-2 text-[11px] text-muted">{L.overviewBannerHint}</span>
          </span>
          <InfoHint text={L.hintOverviewBanner} />
        </div>
      )}

      <KpiCardRow metrics={weeklyMetrics} sparkLabels={sparkLabels} />

      {/* Option A: trend moved up -- narrative order metrics -> trend -> competitors */}
      <SectionCard title={L.overviewTrendTitle} hint={L.hintOverviewTrend}>
        <div style={{ height: 220 }}>
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <ComposedChart data={dailySeries} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
              <AreaGradientDefs id={trendGradientId} />
              <CartesianGrid {...chartGridProps} />
              <XAxis dataKey="key" {...chartAxisProps} minTickGap={28} />
              <YAxis {...chartAxisProps} unit="%" />
              <Tooltip content={
                <TrendTooltip series={dailySeries} metricLabel={L.metricLabels.exposure}
                  numDen={L.trendNumDen} changedLabel={changedLabelOf} />
              } />
              {/* Metric definition change vertical line: the numerator/denominator base or definition changed in that period, so the two sides shouldn't be compared directly */}
              {basisChangeLines(visibleChanges, k => k.slice(5), L.basisChangeFlag,
                dailySeries.map(p => p.key))}
              {/* Target marker: contract target line (amber dashed) + green vertical line on the first target-met day; the stretch after meeting the target is green */}
              {firstMetKey && lastKey && firstMetKey !== lastKey && (
                <ReferenceArea x1={firstMetKey} x2={lastKey} fill="var(--success-solid)" fillOpacity={0.06} />
              )}
              {exposureTarget !== null && (
                <ReferenceLine y={exposureTarget} stroke="var(--warn-solid)" strokeDasharray="6 4"
                  label={{ value: `${L.target} ${exposureTarget}%`, position: 'insideBottomRight',
                    fontSize: 10, fill: 'var(--warn-fg)' }} />
              )}
              {firstMetKey && (
                <ReferenceLine x={firstMetKey} stroke="var(--success-solid)" strokeDasharray="4 3"
                  label={{ value: `✓ ${L.progressMet} ${firstMetKey}`, position: 'insideTopLeft',
                    fontSize: 10, fill: 'var(--success-fg)' }} />
              )}
              <Area type="monotone" dataKey="exposure" name={L.metricLabels.exposure}
                stroke="var(--data-accent)" strokeWidth={2.5} fill={`url(#${trendGradientId})`}
                dot={false} activeDot={{ r: 4.5, strokeWidth: 2, stroke: 'var(--bg-card)' }} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <BasisChangeNote changes={visibleChanges} title={L.basisChangeTitle}
          hint={L.basisChangeHint} kindLabel={k => L.basisChangeKinds[k]}
          desc={L.basisChangeDesc} />
      </SectionCard>

      <CompetitiveFull topicId={topic.id} token={token} />

      {corrections.length > 0 && (
        <SectionCard title={L.secCorrections} hint={L.hintCorrections}>
          <CorrectionsTabs rows={corrections} />
        </SectionCard>
      )}

      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
        {latestCards.map(c => (
          <div key={c.type} className="rpt-card p-4 flex flex-col gap-2"
            style={{ borderTop: `3px solid ${c.hue}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-bold text-primary">{L.latestPrefix}{c.label}</span>
              <Link to={`/reports/${c.type}?topic=${topic.id}`}
                className="text-xs hover:underline font-semibold" style={{ color: 'var(--data-accent)' }}>
                {L.viewAllList} ›
              </Link>
            </div>
            {c.item ? (
              <>
                <div className="text-xs text-muted rpt-num">{c.item.key}
                  {c.item.period_start !== c.item.period_end && (
                    <span className="ml-1">({c.item.period_start} ~ {c.item.period_end})</span>
                  )}
                </div>
                {/* Two-column small definition table for metrics: muted label on the left / monospaced value on the right, dashed dividers between rows (grid layout, PDF-safe) */}
                <div className="grid gap-x-3 text-xs py-1"
                  style={{ gridTemplateColumns: 'auto minmax(0, 1fr)' }}>
                  {([
                    [L.metricLabels.exposure, c.item.exposure_pct],
                    [L.metricLabels.top1, c.item.top1_pct],
                    [L.metricLabels.top3, c.item.top3_pct],
                    [L.metricLabels.accuracy, c.item.accuracy_pct],
                    [L.metricLabels.citation, c.item.citation_pct],
                  ] as [string, number][]).map(([label, pct], i) => (
                    <div key={label} className="contents">
                      <span className="text-muted whitespace-nowrap py-[3px]"
                        style={i > 0 ? { borderTop: '1px dashed var(--rpt-hairline)' } : undefined}>{label}</span>
                      <span className="text-right rpt-num font-bold text-primary py-[3px]"
                        style={i > 0 ? { borderTop: '1px dashed var(--rpt-hairline)' } : undefined}>{pct}%</span>
                    </div>
                  ))}
                </div>
                <Link to={`/reports/${c.type}/${c.item.key}?topic=${topic.id}`}
                  className="mt-auto text-center px-3 py-1.5 text-xs rounded-md font-bold transition hover:opacity-80"
                  style={{ border: `1px solid ${c.hue}`, color: c.hue, background: c.tint }}>
                  {L.openReport}
                </Link>
              </>
            ) : (
              <div className="text-xs text-muted py-4 text-center">{L.empty}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
