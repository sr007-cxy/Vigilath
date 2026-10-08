// P3 weekly report detail — same six sections as the monthly report: KPI overview / per-platform matrix / competitors / trend and forecast / correction and breakthrough / deliverables + next-period plan.
// All block implementations use the shared pieces in components.tsx + chartkit.tsx; the two sides don't each write their own.

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ReportsShell, type ShellState } from './shell';
import { reportsApi, type WeeklyReport as WeeklyReportData } from '../../services/reportsApi';
import { useReportsLang } from './lang';
import {
  DeliverableGrid, KpiCardGrid, NextPlanList, PeriodNavButtons, PlatformMatrixTable, SectionCard,
  usePeriodKeys,
} from './components';
import { ExposureTrendChart, ExposureTrendLegend } from './chartkit';
import { CompetitiveCompact } from './Competitive';
import { CorrectionsTabs } from './CorrectionsTabs';
import { exportElementPdf, exportReportPdfServer } from './exportPdf';

export function WeeklyReport() {
  const L = useReportsLang();
  return (
    <ReportsShell title={L.weeklyTitle} backTo="/reports/weekly">
      {(state) => <Body state={state} />}
    </ReportsShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic } = state;
  const L = useReportsLang();
  const { weekKey = '' } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<WeeklyReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const pageRef = useRef<HTMLDivElement>(null);
  const periodKeys = usePeriodKeys(topic?.id, token, 'weekly');

  useEffect(() => {
    if (!topic || !weekKey) return;
    setLoading(true);
    setError('');
    reportsApi.weeklyReport(topic.id, token, weekKey)
      .then(setData)
      .catch(e => { setData(null); setError(e.message || 'error'); })
      .finally(() => setLoading(false));
  }, [token, topic?.id, weekKey, L]);   // L changes with the language -> refetch the bilingual text

  if (!topic) return null;
  if (loading) return <div className="text-xs text-muted text-center py-10">{L.loading}</div>;
  if (!data) return <div className="text-sm text-muted text-center py-16">{error || L.empty}</div>;

  const onExport = async () => {
    if (!pageRef.current || exporting) return;
    setExporting(true);
    try {
      const filename = `weekly-report-${data.week_key}.pdf`;
      if (!(await exportReportPdfServer(token, topic.id, filename))) {
        await exportElementPdf(pageRef.current, filename);
      }
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="grid gap-4" ref={pageRef} data-export-root>
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-primary">
          {L.weeklyTitle} · {data.week_key}
          <span className="text-muted text-xs ml-2">{data.period_start} ~ {data.period_end}</span>
        </div>
        <div className="flex items-center gap-2">
          <PeriodNavButtons keys={periodKeys} current={weekKey}
            onGo={k => navigate(`/reports/weekly/${k}?topic=${topic.id}`)} />
          <button type="button" onClick={onExport} disabled={exporting}
            className="px-3 py-1 text-xs rounded disabled:opacity-60"
            style={{ background: 'var(--accent-primary)', color: 'white' }}>
            {exporting ? L.exporting : L.exportPdf}
          </button>
        </div>
      </div>

      <SectionCard title={L.secKpiCards} hint={L.hintWeeklyBeforeAfter}>
        <KpiCardGrid cards={data.kpi_cards} rankCoverage={data.rank_coverage} />
      </SectionCard>

      <SectionCard title={L.secPlatformMatrix} hint={L.hintWeeklyPlatformMatrix}>
        <PlatformMatrixTable rows={data.platform_matrix} />
      </SectionCard>

      <CompetitiveCompact topicId={topic.id} token={token}
        periodType="weekly" periodKey={weekKey} title={L.secCompetitive} />

      <SectionCard title={L.secTrend} hint={L.hintTrendForecast} right={<ExposureTrendLegend />}>
        <ExposureTrendChart trend={data.trend} basisChanges={data.basis_changes}
          exposureTarget={data.exposure_target} />
      </SectionCard>

      <SectionCard title={L.secCorrections} hint={L.hintWeeklyCorrections}>
        {data.corrections.length === 0 ? (
          <div className="text-xs text-muted py-2">{L.emptyCorrections}</div>
        ) : (
          <CorrectionsTabs rows={data.corrections} />
        )}
      </SectionCard>

      <div className="grid gap-4 md:grid-cols-2">
        <SectionCard title={L.secDeliverables}>
          <DeliverableGrid items={data.deliverables} />
        </SectionCard>

        <SectionCard title={L.secNextPlan}>
          <NextPlanList items={data.next_plan} />
        </SectionCard>
      </div>
    </div>
  );
}
