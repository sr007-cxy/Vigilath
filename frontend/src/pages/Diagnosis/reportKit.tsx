// One-click full diagnosis report — shared chapter pieces.
//
// Typography rules (hard rules after the gray-text cleanup; new chapters must follow them):
//   · Body text = conclusions / reasons / narrative / metric definition notes -> text-sm + text-primary or text-secondary
//   · Metadata  = timestamps, units, weights, table headers, legends, empty states -> text-xs + text-muted
//   · Two red lines: (1) 10-11px font sizes must not be combined with muted to carry information (2) text longer than 20 characters must not use muted

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { DiagnosisSubResult, DiagnosisStep } from '../../services/fullDiagnosisApi';

/** Whether we're rendering for PDF export (the backend Playwright opens the page with ?export=1).
 *
 * Export screenshots the real page, and the backend can't change collapsed sections' React state -- collapsed content
 * is content missing from the PDF. Blocks that must be complete on export read this flag to expand themselves.
 * Note: not every collapse should expand; defaults like "show only items with issues" are deliberate trade-offs,
 * and expanding everything would bloat the PDF, so don't change them along the way. */
export function isPdfExport(): boolean {
  if (typeof window === 'undefined') return false;
  return new URLSearchParams(window.location.search).get('export') === '1';
}

/** Times in the report are shown in the viewer's timezone. For PDF export the page runs in the server's headless browser,
 *  whose timezone isn't the user's -- the export endpoint carries the user's browser timezone in ?tz=, read back here. */
export function reportTimeZone(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  return new URLSearchParams(window.location.search).get('tz') || undefined;
}

/** The single gateway for sub-audit data -- chapters always go through it; never read sub[mode].data directly */
export function subData(
  sub: Record<string, DiagnosisSubResult> | null | undefined,
  mode: string,
): Record<string, any> | null {
  const s = sub?.[mode];
  if (s && s.status === 'ok' && s.data && typeof s.data === 'object') {
    return s.data as Record<string, any>;
  }
  return null;
}

/** Reason a sub-audit got no data (skipped gives reason, failed gives error) */
export function subReason(
  sub: Record<string, DiagnosisSubResult> | null | undefined,
  mode: string,
): string {
  const s = sub?.[mode];
  if (!s) return '';
  return s.reason || s.error || '';
}

export function Chapter({ id, no, title, subtitle, children }: {
  id: string; no: string; title: string; subtitle: string; children: ReactNode;
}) {
  return (
    <section id={`diag-${id}`} className="card p-6 sm:p-8 scroll-mt-24">
      <div className="flex items-baseline gap-3 mb-1">
        <span className="text-xs font-bold px-1.5 py-0.5 rounded"
              style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>{no}</span>
        <h2 className="text-xl font-bold text-primary m-0">{title}</h2>
      </div>
      {/* The chapter lead is body text, no longer 11px gray text */}
      <p className="text-sm text-secondary mb-5 ml-10">{subtitle}</p>
      <div className="space-y-5">{children}</div>
    </section>
  );
}

/** Section heading within a chapter */
export function SectionSub({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-2">
      <h3 className="text-sm font-bold text-primary m-0">{title}</h3>
      {hint && <p className="text-xs text-muted mt-0.5 m-0">{hint}</p>}
    </div>
  );
}

/** Data source badge: saying "this is a model inference" outright is more honest than downplaying it with gray text */
export function SourceBadge({ kind }: { kind: 'ai' | 'measured' }) {
  const { t } = useTranslation();
  const isAi = kind === 'ai';
  return (
    <span className="inline-block px-1.5 py-0.5 rounded text-[11px] font-medium align-middle whitespace-nowrap"
          style={isAi
            ? { background: 'rgba(124,58,237,0.12)', color: 'var(--dg-5)' }
            : { background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
      {isAi ? t('diagnosis.report.sourceAi') : t('diagnosis.report.sourceMeasured')}
    </span>
  );
}

/**
 * Data collection status bar at the top of a chapter.
 * After splitting into 11 chapters, a single failed sub-audit leaves a whole chapter blank -- this says which item wasn't collected and why,
 * instead of just "insufficient data".
 */
export function ChapterDataStatus({ steps, modes, sub }: {
  steps?: DiagnosisStep[] | null;
  modes: string[];
  sub: Record<string, DiagnosisSubResult>;
}) {
  const { t } = useTranslation();
  const bad = modes
    .map((m) => ({ mode: m, step: steps?.find((s) => s.mode === m) }))
    .filter(({ mode, step }) => {
      const st = step?.status || sub[mode]?.status;
      return st === 'failed' || st === 'skipped';
    });
  if (bad.length === 0) return null;
  return (
    <div className="rounded-lg px-3 py-2 text-sm"
         style={{ background: 'var(--warn-bg)', color: 'var(--warn-fg)' }}>
      {bad.map(({ mode }) => {
        const reason = subReason(sub, mode);
        const name = t(`diagnosis.progress.steps.${mode}`, { defaultValue: mode });
        return (
          <div key={mode}>
            {t('diagnosis.report.stepMissing', { name })}
            {reason && <span className="opacity-80"> — {reason}</span>}
          </div>
        );
      })}
    </div>
  );
}

export function BarRow({ value, max = 100, muted = false, color, bareTrack = false }: {
  value: number; max?: number; muted?: boolean; color?: string; bareTrack?: boolean;
}) {
  const pct = Math.max(0, Math.min(100, (value / Math.max(max, 1)) * 100));
  const hue = color || 'var(--dg-1)';
  return (
    // bareTrack: for overlay layers; a transparent track won't cover the bars below
    <div className="h-2.5 rounded-full overflow-hidden"
         style={{ background: bareTrack ? 'transparent' : 'var(--dg-track)' }}>
      <div className="h-full rounded-full transition-all duration-500"
           style={{ width: `${pct}%`,
                    background: muted ? 'var(--text-muted)' : hue,
                    boxShadow: muted ? undefined : `0 0 8px -2px ${hue}`,
                    opacity: muted ? 0.5 : 1 }} />
    </div>
  );
}

/** Sub-item score bar: title on the left + got/max on the right + progress at the bottom */
export function ScoreItemBar({ label, got, max, color, note }: {
  label: string; got: number; max: number; color?: string; note?: string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <span className="text-sm text-primary">{label}</span>
        <span className="text-sm font-semibold text-primary tabular-nums">
          {got}<span className="text-xs text-muted font-normal">/{max}</span>
        </span>
      </div>
      <BarRow value={got} max={max} color={color} />
      {note && <p className="text-xs text-secondary mt-1 m-0">{note}</p>}
    </div>
  );
}

export function StatBox({ label, value, accent, hue }: {
  label: string; value: ReactNode; accent?: boolean; hue?: string;
}) {
  const color = hue || 'var(--dg-1)';
  return (
    <div className="p-4 rounded-xl border border-soft text-center"
         style={accent
           ? { borderColor: color, borderTopWidth: 3, borderTopColor: color }
           : { borderTopWidth: 3, borderTopColor: 'var(--dg-track)' }}>
      <div className="text-2xl font-bold tabular-nums"
           style={{ color: accent ? color : 'var(--text-primary)' }}>
        {value}
      </div>
      <div className="text-xs text-muted mt-0.5">{label}</div>
    </div>
  );
}

export function NoDataCard({ reason }: { reason?: string }) {
  const { t } = useTranslation();
  return (
    <div className="text-sm text-secondary">
      {t('diagnosis.report.noData')}
      {reason && <span className="text-muted"> — {reason}</span>}
    </div>
  );
}

/** Placeholder when model narrative is missing (hard-data chapters render as usual; only the narrative slot is empty) */
export function NoNarrative() {
  const { t } = useTranslation();
  return <p className="text-sm text-secondary m-0">{t('diagnosis.report.noNarrative')}</p>;
}

/** Long model-generated paragraphs: uniformly 14px + relaxed line height, no longer 12px gray text */
export function Prose({ children, tinted = false }: { children: ReactNode; tinted?: boolean }) {
  return (
    <p className={`text-sm text-primary leading-relaxed m-0${tinted ? ' p-4 rounded-xl' : ''}`}
       style={tinted ? { background: 'var(--bg-tertiary)' } : undefined}>
      {children}
    </p>
  );
}
