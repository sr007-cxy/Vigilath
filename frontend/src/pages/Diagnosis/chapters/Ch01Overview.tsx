// Chapter 01 Diagnosis overview — total score / key findings / risk notices / collection status of the eight audits
import { useTranslation } from 'react-i18next';
import { StatBox, Prose, NoNarrative } from '../reportKit';
import { severityStyle } from '../palette';
import type { ChapterCtx } from './types';

const ALL_MODES = ['base', 'aeo', 'crawlTest', 'authority', 'visibility',
                   'entity', 'competitiveIntel', 'sentiment'];

export function Ch01Overview({ report, scores, n }: ChapterCtx) {
  const { t } = useTranslation();
  const steps = report.progress?.steps || [];
  // Lite audit (scope=lite) runs only the four site-side items; the other four have no entries in progress.steps at all,
  // and not_selected can only be read from subresults -- without reading it they'd show as gray "not run yet",
  // which is different from "not run this time"
  const subStatus = (m: string) => (report.subresults as Record<string, { status?: string }> | undefined)?.[m]?.status;

  return (
    <>
      <div className="grid grid-cols-3 gap-3">
        <StatBox label={t('diagnosis.report.totalScore')} value={scores?.total ?? '—'} accent />
        <StatBox label={t('diagnosis.report.sections.overview.keyFindings')} value={n.key_findings?.length ?? 0} />
        <StatBox label={t('diagnosis.report.sections.overview.risks')} value={n.risks?.length ?? 0} />
      </div>

      {n.executive_summary && <Prose tinted>{n.executive_summary}</Prose>}

      {(n.key_findings?.length ?? 0) > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            ✦ {t('diagnosis.report.sections.overview.keyFindings')}
          </h3>
          <div className="space-y-2">
            {n.key_findings!.map((f, i) => {
              const sv = severityStyle(f.severity);
              return (
                <div key={i} className="p-3 rounded-lg" style={{ background: sv.bg }}>
                  <span className="text-sm font-semibold" style={{ color: sv.c }}>{f.title}</span>
                  <span className="text-sm text-primary leading-relaxed"> — {f.detail}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {(n.risks?.length ?? 0) > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            ⚠ {t('diagnosis.report.sections.overview.risks')}
          </h3>
          <div className="space-y-2">
            {n.risks!.map((r, i) => (
              <div key={i} className="p-3 rounded-lg text-sm text-primary leading-relaxed"
                   style={{ background: 'var(--warn-bg)', borderLeft: '3px solid var(--warn-solid)' }}>
                {r}
              </div>
            ))}
          </div>
        </div>
      )}

      {!n.executive_summary && !(n.key_findings?.length) && <NoNarrative />}

      {/* Collection status overview: which items got data and which didn't -- refer back here when later chapters are blank */}
      {steps.length > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            {t('diagnosis.report.sections.overview.collectStatus')}
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {ALL_MODES.map((m) => {
              const st = steps.find((s) => s.mode === m)?.status || subStatus(m);
              const ok = st === 'ok';
              const notSelected = st === 'not_selected';
              const bad = st === 'failed' || st === 'skipped';
              return (
                <span key={m} className="px-2 py-0.5 rounded-md text-xs"
                      style={ok
                        ? { background: 'var(--success-bg)', color: 'var(--success-fg)' }
                        : bad
                          ? { background: 'var(--warn-bg)', color: 'var(--warn-fg)' }
                          : { background: 'var(--neutral-bg)', color: 'var(--neutral-fg)' }}>
                  {ok ? '✓ ' : bad ? '— ' : ''}
                  {t(`diagnosis.progress.steps.${m}`, { defaultValue: m })}
                  {notSelected && ` · ${t('diagnosis.report.notSelected')}`}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
