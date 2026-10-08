// Chapter 03 User personas — model-inferred personas + coverage of their typical questions in actual measurement
import { useTranslation } from 'react-i18next';
import { subData, SourceBadge, NoNarrative } from '../reportKit';
import type { ChapterCtx } from './types';

/** After normalization, mutual containment counts as the same question (measured and persona questions often differ in punctuation/spaces) */
function norm(s: string): string {
  return (s || '').toLowerCase().replace(/[\s，。,.?？!！、:：]/g, '');
}

type Coverage = 'hit' | 'miss' | 'untested';

export function Ch03Personas({ n, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const vis = subData(sub, 'visibility');
  const personas = n.personas || [];

  // Measured question -> whether at least one engine hit it
  const hits = (vis?.query_hits || []) as { query: string; hits: Record<string, boolean> }[];
  const measured = hits.map((h) => ({
    key: norm(h.query),
    hit: Object.values(h.hits || {}).some(Boolean),
  }));

  const coverageOf = (q: string): Coverage => {
    const k = norm(q);
    const m = measured.find((x) => x.key.includes(k) || k.includes(x.key));
    if (!m) return 'untested';
    return m.hit ? 'hit' : 'miss';
  };

  if (personas.length === 0) return <NoNarrative />;

  const COVER_STYLE: Record<Coverage, { bg: string; c: string }> = {
    hit:      { bg: 'var(--success-bg)', c: 'var(--success-fg)' },
    miss:     { bg: 'var(--danger-bg)',  c: 'var(--danger-fg)' },
    untested: { bg: 'var(--neutral-bg)', c: 'var(--neutral-fg)' },
  };

  return (
    <>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {personas.map((p, i) => (
          <div key={i} className="p-4 rounded-xl border border-soft space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div className="text-sm font-bold text-primary">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full text-[11px] mr-2 text-white"
                      style={{ background: 'var(--accent-primary)' }}>{i + 1}</span>
                {p.name}
              </div>
              <SourceBadge kind="ai" />
            </div>
            {p.intent && (
              <p className="text-sm text-secondary leading-relaxed m-0">
                <span className="font-semibold text-primary">
                  {t('diagnosis.report.sections.personas.intent')}:
                </span>
                {p.intent}
              </p>
            )}
            {p.scenario && (
              <p className="text-sm text-secondary leading-relaxed m-0">{p.scenario}</p>
            )}
            {(p.typical_queries?.length ?? 0) > 0 && (
              <div>
                <div className="text-xs text-muted mb-1">
                  {t('diagnosis.report.sections.personas.typicalQueries')}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {p.typical_queries!.map((q, j) => {
                    const cov = coverageOf(q);
                    const st = COVER_STYLE[cov];
                    return (
                      <span key={j} className="px-2 py-0.5 rounded-md text-xs"
                            style={{ background: st.bg, color: st.c }}
                            title={t(`diagnosis.report.sections.personas.coverage.${cov}`)}>
                        {q}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
      {/* Legend -- the value of this block is exposing "questions inferred from personas were never measured at all" */}
      {measured.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
          <span>{t('diagnosis.report.sections.personas.coverageLegend')}</span>
          {(['hit', 'miss', 'untested'] as Coverage[]).map((c) => (
            <span key={c} className="inline-flex items-center gap-1">
              <span className="inline-block w-2.5 h-2.5 rounded"
                    style={{ background: COVER_STYLE[c].c }} />
              {t(`diagnosis.report.sections.personas.coverage.${c}`)}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
