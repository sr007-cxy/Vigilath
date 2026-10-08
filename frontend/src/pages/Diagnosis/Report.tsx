// One-click full diagnosis — report page shell: four-state polling / header / anchor nav / chapter layout.
// Chapter content lives in chapters/, shared pieces in reportKit.tsx, colors in palette.ts.
// Interaction matches the "strategy optimization plan": 3s polling across states (generating/ready/failed);
// the report root carries data-export-root, and the backend headless browser screenshots this page directly for PDF export.

import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { fmtTimeIn, browserTimeZone } from '../../utils/datetime';
import { fullDiagnosisApi, type FullDiagnosisReport } from '../../services/fullDiagnosisApi';
import { Chapter, StatBox, reportTimeZone } from './reportKit';
import { useDgPalette, gradeColorOf } from './palette';
import {
  Ch01Overview, Ch02Scores, Ch03Personas, Ch04Scenes, Ch05Visibility, Ch06Indexing, Ch10Sources,
  Ch07Website, Ch08Authority, Ch09Competitive, Ch10Sentiment, Ch11Outlook,
  type ChapterComp,
} from './chapters';

// Single source of truth: chapter order, anchor ids and copy keys are all derived from here.
// Previously chapter rendering and anchor nav were two hand-written arrays, and adding a chapter always missed one.
const CHAPTERS: { id: string; C: ChapterComp }[] = [
  { id: 'overview',    C: Ch01Overview },
  { id: 'scores',      C: Ch02Scores },
  { id: 'personas',    C: Ch03Personas },
  { id: 'scenes',      C: Ch04Scenes },
  { id: 'visibility',  C: Ch05Visibility },
  { id: 'indexing',    C: Ch06Indexing },
  { id: 'website',     C: Ch07Website },
  { id: 'authority',   C: Ch08Authority },
  { id: 'competitive', C: Ch09Competitive },
  { id: 'sources',     C: Ch10Sources },
  { id: 'sentiment',   C: Ch10Sentiment },
  { id: 'outlook',     C: Ch11Outlook },
];

const STEP_ORDER = ['base', 'aeo', 'crawlTest', 'authority', 'visibility',
                    'entity', 'competitiveIntel', 'sentiment'];

// Chapter-level fallback: if any of the 11 chapters throws while rendering, React unmounts the whole tree -- users see
// a completely blank page, without even the header or the other 10 chapters. This confines the blast to one chapter, leaving a
// visible notice in its slot, with the stack in the console as usual. PDF export also uses this page, so the notice shouldn't be too glaring.
class ChapterBoundary extends Component<{ id: string; children: ReactNode },
                                        { error: string }> {
  state = { error: '' };
  static getDerivedStateFromError(e: unknown) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  componentDidCatch(e: unknown) {
    console.error(`[diagnosis] chapter "${this.props.id}" render failed:`, e);
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <p className="text-sm rounded-md p-3 m-0"
         style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                  border: '1px solid var(--danger-border)' }}>
        本章渲染失败({this.state.error.slice(0, 120)}),其余章节不受影响。
      </p>
    );
  }
}

export function DiagnosisReport() {
  const { t, i18n } = useTranslation();
  const { id } = useParams();
  const navigate = useNavigate();
  const rid = Number(id);

  const [report, setReport] = useState<FullDiagnosisReport | null>(null);
  const [err, setErr] = useState('');
  const [exporting, setExporting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const pollRef = useRef<number | null>(null);
  const failStreakRef = useRef(0);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchOnce = useCallback(async () => {
    try {
      const r = await fullDiagnosisApi.get(rid);
      failStreakRef.current = 0;
      setReport(r);
      if (r.status !== 'generating') stopPoll();
    } catch (e: unknown) {
      // Tolerate 3 transient network failures
      failStreakRef.current += 1;
      if (failStreakRef.current >= 3) {
        setErr(e instanceof Error ? e.message : String(e));
        stopPoll();
      }
    }
  }, [rid, stopPoll]);

  useEffect(() => {
    fetchOnce();
    return stopPoll;
  }, [fetchOnce, stopPoll]);

  useEffect(() => {
    if (report?.status === 'generating' && !pollRef.current) {
      pollRef.current = window.setInterval(fetchOnce, 3000);
    } else if (report?.status !== 'generating') {
      stopPoll();
    }
  }, [report?.status, fetchOnce, stopPoll]);

  const handleExport = async () => {
    if (!report || report.status !== 'ready' || exporting) return;
    setExporting(true);
    try {
      const theme = localStorage.getItem('theme') || '';
      const blob = await fullDiagnosisApi.exportPdf(report.id, i18n.language || 'zh', theme, browserTimeZone());
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `diagnosis-${report.brand_name || report.id}.pdf`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const handleRetry = async () => {
    if (!report || retrying) return;
    setRetrying(true);
    setErr('');
    try {
      const res = await fullDiagnosisApi.generate(
        report.url, report.brand_name, report.competitor_domains || [],
        report.industry_terms || [], report.brand_terms || [],
      );
      if (res.report_id !== rid) {
        navigate(`/diagnosis/${res.report_id}`, { replace: true });
      } else {
        fetchOnce();
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRetrying(false);
    }
  };

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
              <h1 className="text-xl font-bold text-primary">{t('diagnosis.failed.title')}</h1>
              <p className="text-sm text-secondary">{report.error}</p>
              <button type="button" disabled={retrying} onClick={handleRetry}
                      className="btn-solid rounded-full py-2.5 px-8 text-sm font-semibold"
                      style={{ opacity: retrying ? 0.6 : 1 }}>
                {t('diagnosis.failed.retry')}
              </button>
            </div>
          )}

          {report?.status === 'ready' && (
            <ReportView report={report} exporting={exporting}
                        onExport={handleExport} onRegenerate={handleRetry} />
          )}
        </div>
      </main>
    </div>
  );
}

// ─────────────── Generating: eight-audit checklist ───────────────

export function ProgressView({ report }: { report: FullDiagnosisReport }) {
  const { t } = useTranslation();
  const progress = report.progress;
  const steps = progress?.steps?.length
    ? progress.steps
    : STEP_ORDER.map((m) => ({ mode: m, status: 'pending' as const }));
  const isLlm = progress?.stage === 'llm';

  return (
    <div className="card p-6 sm:p-8 max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-1">
        <div className="animate-spin rounded-full h-5 w-5 border-2 border-t-transparent flex-shrink-0"
             style={{ borderColor: 'var(--accent-primary)', borderTopColor: 'transparent' }} />
        <h1 className="text-xl font-bold text-primary m-0">{t('diagnosis.progress.title')}</h1>
      </div>
      <p className="text-sm text-secondary mb-1">{t('diagnosis.progress.subtitle')}</p>
      <p className="text-sm font-medium mb-5" style={{ color: 'var(--accent-primary)' }}>
        {isLlm
          ? t('diagnosis.progress.llmStage')
          : t('diagnosis.progress.done', {
              done: progress?.done ?? 0, total: progress?.total ?? steps.length,
            })}
      </p>
      <div className="space-y-2.5">
        {steps.map((s) => {
          const color = s.status === 'ok' ? 'var(--success-fg)'
            : s.status === 'failed' ? 'var(--danger-fg)'
            : s.status === 'skipped' ? 'var(--warn-fg)'
            : s.status === 'running' ? 'var(--accent-primary)'
            : 'var(--text-muted)';
          return (
            <div key={s.mode} className="flex items-start gap-3 text-sm">
              <span className="inline-flex items-center justify-center w-5 h-5 rounded-full flex-shrink-0 text-[11px] font-bold mt-0.5"
                    style={{ border: `2px solid ${color}`, color }}>
                {s.status === 'ok' ? '✓' : s.status === 'failed' ? '✕' : s.status === 'skipped' ? '—' : ''}
              </span>
              <div className="flex-1 min-w-0">
                <span className={s.status === 'pending' ? 'text-muted' : 'text-primary'}>
                  {t(`diagnosis.progress.steps.${s.mode}`, { defaultValue: s.mode })}
                </span>
                {/* Skip reason (e.g. 「未提供官网地址,站点侧检测已跳过」 ("no website provided, site-side audits skipped")) -- written by the backend in step.error;
                    previously not shown, so users only saw "skipped" without knowing why. */}
                {s.status === 'skipped' && s.error && (
                  <p className="text-xs mt-0.5 m-0" style={{ color: 'var(--text-muted)' }}>{s.error}</p>
                )}
              </div>
              <span className="ml-auto shrink-0 text-xs" style={{ color }}>
                {s.status === 'running' && (
                  <span className="inline-block animate-spin rounded-full h-3 w-3 border-2 border-t-transparent mr-1 align-middle"
                        style={{ borderColor: color, borderTopColor: 'transparent' }} />
                )}
                {t(`diagnosis.progress.stepStatus.${s.status}`, { defaultValue: s.status })}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────── Report body ───────────────

export function ReportView({ report, exporting = false, onExport, onRegenerate }: {
  report: FullDiagnosisReport;
  exporting?: boolean;
  onExport?: () => void;
  onRegenerate?: () => void;
}) {
  const { t } = useTranslation();
  const ctx = {
    report,
    scores: report.scores ?? null,
    n: report.narrative || {},
    sub: report.subresults || {},
  };
  return (
    <div data-export-root className="diagnosis-report space-y-6 relative">
      {/* Chapter anchors on the right (desktop). 11 items reach the bottom of the viewport, so give a max height + inner scrolling */}
      <nav className="hidden xl:block fixed right-6 top-32 w-36 space-y-1 print-hidden z-20
                      max-h-[62vh] overflow-y-auto">
        {CHAPTERS.map((c, i) => (
          <a key={c.id} href={`#diag-${c.id}`}
             className="block text-[11px] py-1 px-2 rounded text-secondary hover:text-primary truncate"
             style={{ borderLeft: '2px solid var(--grid-line)' }}>
            <span className="font-bold mr-1" style={{ color: 'var(--success-fg)' }}>
              {String(i + 1).padStart(2, '0')}
            </span>
            {t(`diagnosis.report.sections.${c.id}.title`)}
          </a>
        ))}
      </nav>

      <ReportHeader report={report} exporting={exporting}
                    onExport={onExport} onRegenerate={onRegenerate} />

      {CHAPTERS.map((c, i) => (
        <Chapter key={c.id}
                 id={c.id}
                 no={String(i + 1).padStart(2, '0')}
                 title={t(`diagnosis.report.sections.${c.id}.title`)}
                 subtitle={t(`diagnosis.report.sections.${c.id}.subtitle`)}>
          <ChapterBoundary id={c.id}>
            <c.C {...ctx} />
          </ChapterBoundary>
        </Chapter>
      ))}

      <p className="text-xs text-muted text-center pb-4">{t('diagnosis.report.cacheNote')}</p>
    </div>
  );
}

function ReportHeader({ report, exporting, onExport, onRegenerate }: {
  report: FullDiagnosisReport;
  exporting: boolean;
  onExport?: () => void;
  onRegenerate?: () => void;
}) {
  const { t } = useTranslation();
  const scores = report.scores;

  return (
    <section className="card p-6 sm:p-8"
             style={{ background: 'linear-gradient(135deg, rgba(99,102,241,0.10), rgba(16,185,129,0.06))' }}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <span className="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold mb-3"
                style={{ background: 'rgba(99,102,241,0.15)', color: 'var(--accent-primary)' }}>
            {t('diagnosis.report.badge')}
          </span>
          <h1 className="text-2xl sm:text-3xl font-bold text-primary mb-1">{report.brand_name}</h1>
          <p className="text-sm text-secondary m-0">
            {report.url || t('diagnosis.form.noUrl')}
          </p>
          {/* The header keeps only the generation time -- the report is a client deliverable, and the underlying model id (deepseek-chat etc.)
              is an implementation detail not exposed externally. llm_model is still in the API response, so internal dashboards can still see it. */}
          <p className="text-xs text-muted mt-2 m-0">
            {t('diagnosis.report.generatedAt')}:{fmtTimeIn(report.updated_at, reportTimeZone(), false)}
          </p>
        </div>
        <div className="flex items-center gap-4">
          {scores && <ScoreRing total={scores.total} grade={scores.grade} />}
          {(onExport || onRegenerate) && (
            <div className="flex flex-col gap-2 print-hidden">
              {onExport && (
                <button type="button" disabled={exporting} onClick={onExport}
                        className="btn-solid rounded-full py-2 px-5 text-xs font-semibold whitespace-nowrap"
                        style={{ opacity: exporting ? 0.6 : 1 }}>
                  {exporting ? t('diagnosis.report.exporting') : t('diagnosis.report.exportPdf')}
                </button>
              )}
              {onRegenerate && (
                <button type="button" onClick={onRegenerate}
                        className="rounded-full py-2 px-5 text-xs font-semibold whitespace-nowrap"
                        style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}>
                  {t('diagnosis.report.regenerate')}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
      {report.error && (
        <p className="text-sm mt-3 m-0" style={{ color: 'var(--warn-fg)' }}>
          {t('diagnosis.report.noNarrative')}
        </p>
      )}
    </section>
  );
}

function ScoreRing({ total, grade }: { total: number; grade: string }) {
  const dg = useDgPalette();
  const r = 34;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, total)) / 100;
  const color = gradeColorOf(total);
  const tail = total >= 70 ? '#34d399' : total >= 55 ? '#fbbf24' : '#fb923c';
  return (
    <div className="relative w-24 h-24 flex-shrink-0">
      <svg viewBox="0 0 84 84" className="w-full h-full -rotate-90">
        <defs>
          <linearGradient id="diagRingGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={color} />
            <stop offset="100%" stopColor={tail} />
          </linearGradient>
        </defs>
        <circle cx="42" cy="42" r={r} fill="none" stroke={dg.track} strokeWidth="7" />
        <circle cx="42" cy="42" r={r} fill="none" stroke="url(#diagRingGrad)" strokeWidth="7"
                strokeLinecap="round" strokeDasharray={`${c * pct} ${c}`} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold text-primary leading-none tabular-nums">{total}</span>
        <span className="text-[11px] font-semibold" style={{ color }}>{grade}</span>
      </div>
    </div>
  );
}

export { StatBox };
