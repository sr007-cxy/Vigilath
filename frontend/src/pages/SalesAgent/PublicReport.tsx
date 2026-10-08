import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { PageHead } from '../../components/PageHead';
import { fetchPublicSalesAgentReport, type SalesAgentPublicReportResponse } from '../../services/salesAgentApi';
import type { FullDiagnosisReport } from '../../services/fullDiagnosisApi';
import { ProgressView, ReportView } from '../Diagnosis/Report';

function normalizeReportStatus(status: string): FullDiagnosisReport['status'] {
  if (status === 'ready' || status === 'failed') return status;
  return 'generating';
}

function toFullDiagnosisReport(data: SalesAgentPublicReportResponse): FullDiagnosisReport {
  const report = data.report;
  return {
    id: report.id,
    status: normalizeReportStatus(report.status),
    url: report.url || data.company?.website || '',
    brand_name: report.brand_name || data.company?.name || 'Sales Agent 目标企业诊断报告',
    competitor_domains: report.competitor_domains || [],
    industry_terms: report.industry_terms || [],
    brand_terms: report.brand_terms || [],
    progress: report.progress || null,
    scores: report.scores || null,
    narrative: report.narrative || null,
    subresults: report.subresults || null,
    llm_model: '',
    error: report.error || null,
    created_at: report.created_at || null,
    updated_at: report.updated_at || null,
  };
}

export function SalesAgentPublicReport() {
  const { token = '' } = useParams();
  const [data, setData] = useState<SalesAgentPublicReportResponse | null>(null);
  const [err, setErr] = useState('');
  const pollRef = useRef<number | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchOnce = useCallback(async () => {
    if (!token) {
      setErr('公开报告链接不存在或已失效。');
      return;
    }
    try {
      const next = await fetchPublicSalesAgentReport(token);
      setData(next);
      setErr('');
      if (next.report.status !== 'generating') stopPoll();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '公开报告读取失败');
      stopPoll();
    }
  }, [token, stopPoll]);

  useEffect(() => {
    void fetchOnce();
    return stopPoll;
  }, [fetchOnce, stopPoll]);

  useEffect(() => {
    if (data?.report.status === 'generating' && !pollRef.current) {
      pollRef.current = window.setInterval(() => { void fetchOnce(); }, 3000);
    }
    if (data?.report.status !== 'generating') stopPoll();
  }, [data?.report.status, fetchOnce, stopPoll]);

  const report = data ? toFullDiagnosisReport(data) : null;

  return (
    <div className="min-h-screen grid-background">
      <PageHead titleKey="diagnosis.pageTitle" titleFallback="一键全面诊断" />
      <main className="flex-1 px-4 pt-8 pb-16 relative z-10">
        <div className="w-full max-w-5xl mx-auto animate-fade-in">
          {err && (
            <div className="rounded-md p-3 mb-4 text-sm"
                 style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                          border: '1px solid var(--danger-border)' }}>
              {err}
            </div>
          )}

          {!report && !err && <div className="py-24 text-center text-sm text-muted">…</div>}

          {report?.status === 'generating' && <ProgressView report={report} />}

          {report?.status === 'failed' && (
            <div className="card p-8 text-center space-y-4">
              <h1 className="text-xl font-bold text-primary">诊断失败</h1>
              <p className="text-sm text-secondary">{report.error || '本次诊断未完成,请联系报告分享方重新生成。'}</p>
            </div>
          )}

          {report?.status === 'ready' && <ReportView report={report} />}

          {data?.privacy && <p className="text-xs text-muted text-center mt-6 pb-4">{data.privacy}</p>}
        </div>
      </main>
    </div>
  );
}

export default SalesAgentPublicReport;
