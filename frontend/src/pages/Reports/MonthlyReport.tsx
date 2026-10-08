// P4 monthly / closing report detail — same six sections as the weekly report: KPI overview / per-platform matrix / competitors / trend and forecast / correction and breakthrough / deliverables + next-period plan.
// All block implementations use the shared pieces in components.tsx + chartkit.tsx; the two sides don't each write their own.

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ReportsShell, type ShellState } from './shell';
import { reportsApi, type MonthlyReport as MonthlyReportData } from '../../services/reportsApi';
import { useReportsLang } from './lang';
import {
  DeliverableGrid, KpiCardGrid, NextPlanList, PeriodNavButtons, PlatformMatrixTable, SectionCard,
  usePeriodKeys,
} from './components';
import { ExposureTrendChart, ExposureTrendLegend } from './chartkit';
import { CompetitiveCompact } from './Competitive';
import { CorrectionsTabs } from './CorrectionsTabs';
import { exportElementPdf, exportReportPdfServer } from './exportPdf';

export function MonthlyReport() {
  const L = useReportsLang();
  return (
    <ReportsShell title={L.monthlyTitle} backTo="/reports/monthly">
      {(state) => <Body state={state} />}
    </ReportsShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic } = state;
  const L = useReportsLang();
  const { monthKey = '' } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<MonthlyReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const pageRef = useRef<HTMLDivElement>(null);
  const periodKeys = usePeriodKeys(topic?.id, token, 'monthly');

  useEffect(() => {
    if (!topic || !monthKey) return;
    setLoading(true);
    setError('');
    reportsApi.monthlyReport(topic.id, token, monthKey)
      .then(setData)
      .catch(e => { setData(null); setError(e.message || 'error'); })
      .finally(() => setLoading(false));
  }, [token, topic?.id, monthKey, L]);   // L changes with the language -> refetch the bilingual text

  if (!topic) return null;
  if (loading) return <div className="text-xs text-muted text-center py-10">{L.loading}</div>;
  if (!data) return <div className="text-sm text-muted text-center py-16">{error || L.empty}</div>;

  const onExport = async () => {
    if (!pageRef.current || exporting) return;
    setExporting(true);
    try {
      const filename = `monthly-report-${data.month_key}.pdf`;
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
          {L.monthlyTitle} · {data.month_key}
          <span className="text-muted text-xs ml-2">{data.period_start} ~ {data.period_end}</span>
        </div>
        <div className="flex items-center gap-2">
          <PeriodNavButtons keys={periodKeys} current={monthKey}
            onGo={k => navigate(`/reports/monthly/${k}?topic=${topic.id}`)} />
          <button type="button" onClick={onExport} disabled={exporting}
            className="px-3 py-1 text-xs rounded disabled:opacity-60"
            style={{ background: 'var(--accent-primary)', color: 'white' }}>
            {exporting ? L.exporting : L.exportPdf}
          </button>
        </div>
      </div>

      <SectionCard title={L.secKpiCards} hint={L.hintBeforeAfter}>
        <KpiCardGrid cards={data.kpi_cards} />
      </SectionCard>

      <SectionCard title={L.secPlatformMatrix} hint={L.hintPlatformMatrix}>
        <PlatformMatrixTable rows={data.platform_matrix} />
      </SectionCard>

      <CompetitiveCompact topicId={topic.id} token={token}
        periodType="monthly" periodKey={monthKey} title={L.secCompetitive} />

      <SectionCard title={L.secTrend} hint={L.hintTrendForecast} right={<ExposureTrendLegend />}>
        <ExposureTrendChart trend={data.trend} basisChanges={data.basis_changes}
          exposureTarget={data.exposure_target} />
      </SectionCard>

      <SectionCard title={L.secCorrections} hint={L.hintCorrections}>
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
