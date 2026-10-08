// Chapter 08 Authority endorsement — composition formula + four off-site authority sub-items + eight entity recognition sub-items + signal details
//
// The composition formula bar must pick its copy by dimension.source: when only one side was collected it must not show fake weighting ratios.
import { useTranslation } from 'react-i18next';
import {
  subData, StatBox, SectionSub, ScoreItemBar, NoDataCard, ChapterDataStatus, Prose,
} from '../reportKit';
import { DIM_COLOR } from '../palette';
import type { ChapterCtx } from './types';

/** Eight entity recognition items: backend keys are English and are mapped to localized entries here (never print English keys directly) */
const ENTITY_ITEM_KEYS: Record<string, string> = {
  'Entity Recognition':   'recognition',
  'Entity Clarity':       'clarity',
  'Category Association': 'category',
  'Competitive Position': 'competitive',
  'Sentiment & Framing':  'sentiment',
  'Content Gap':          'gap',
  'Knowledge Graph':      'kg',
  'Platform Footprint':   'footprint',
};

/** Four off-site authority sections (5 points each) */
const OFFSITE_KEYS = ['reviews', 'awards', 'google', 'mentions'] as const;

export function Ch08Authority({ report, scores, n, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const auth = subData(sub, 'authority');
  const ent = subData(sub, 'entity');
  const dim = scores?.dimensions?.find((d) => d.key === 'authority');
  const detail = (dim as any)?.detail as
    | { formula?: string; groups?: any[]; notes?: string[] } | undefined;
  const color = DIM_COLOR.authority;

  if (!auth && !ent) {
    return (
      <>
        <ChapterDataStatus steps={report.progress?.steps} modes={['authority', 'entity']} sub={sub} />
        <NoDataCard />
      </>
    );
  }

  // Use real sub-item scores once backend section_scores is ready; otherwise render a two-state "signal present or not", never fabricating scores
  const sectionScores = (auth?.section_scores || null) as Record<string, number> | null;
  const offsiteSignals: Record<string, string[]> = {
    reviews:  ((auth?.reviews as any)?.platforms || []) as string[],
    awards:   ((auth?.awards as any)?.keywords || []) as string[],
    google:   [
      auth?.google?.indexed_pages != null
        ? t('diagnosis.report.sections.authority.indexedPages', { n: auth.google.indexed_pages }) : '',
      auth?.google?.kg_schema_fields != null
        ? t('diagnosis.report.sections.authority.kgFields', { n: auth.google.kg_schema_fields }) : '',
      (auth?.google as any)?.wikipedia_or_wikidata ? 'Wikipedia / Wikidata' : '',
    ].filter(Boolean) as string[],
    mentions: ((auth?.mentions as any)?.platforms || []) as string[],
  };

  const entScores = (ent?.scores || {}) as Record<string, number>;
  const footprint = ((ent?.platforms as any)?.found || []) as string[];
  const kg = (ent?.knowledge_graph || {}) as Record<string, any>;
  const kgHits = ['wikipedia', 'wikidata', 'google_kg', 'baidu_baike']
    .filter((k) => kg[k])
    .map((k) => t(`diagnosis.report.sections.authority.kg.${k}`, { defaultValue: k }));

  return (
    <>
      <ChapterDataStatus steps={report.progress?.steps} modes={['authority', 'entity']} sub={sub} />

      {n.authority_note?.summary && <Prose tinted>{n.authority_note.summary}</Prose>}

      {/* Composition formula bar */}
      <div className="rounded-xl border border-soft p-4" style={{ background: 'var(--bg-tertiary)' }}>
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <span className="text-sm font-bold text-primary">
            {t('diagnosis.report.sections.authority.formulaTitle')}
          </span>
          {dim?.score != null && (
            <span className="text-2xl font-black tabular-nums" style={{ color }}>{dim.score}</span>
          )}
        </div>
        <p className="text-sm text-secondary leading-relaxed m-0">
          {detail?.formula || formulaFallback(dim?.source, t)}
        </p>
        {/* 60 / 40 segmented bar: shown only when both sides were collected */}
        {dim?.source === 'authority+entity' && auth && ent && (
          <div className="flex mt-3 rounded-lg overflow-hidden h-8 text-xs font-semibold">
            <div className="flex items-center justify-center text-white"
                 style={{ width: '60%', background: color }}>
              {t('diagnosis.report.sections.authority.offsite')} {auth.percent}
            </div>
            <div className="flex items-center justify-center text-white"
                 style={{ width: '40%', background: 'var(--dg-5)' }}>
              {t('diagnosis.report.sections.authority.entity')} {ent.percent}
            </div>
          </div>
        )}
        {(detail?.notes?.length ?? 0) > 0 && (
          <div className="mt-2 space-y-1">
            {detail!.notes!.map((x, i) => (
              <p key={i} className="text-xs text-muted m-0 leading-relaxed">{x}</p>
            ))}
          </div>
        )}
      </div>

      {/* Off-site authority sub-items */}
      {auth && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.authority.offsiteTitle')}
                      hint={t('diagnosis.report.sections.authority.offsiteHint',
                              { got: auth.score, max: auth.max_score })} />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
            {OFFSITE_KEYS.map((k) => {
              const label = t(`diagnosis.report.sections.authority.offsiteItems.${k}`);
              const signals = offsiteSignals[k] || [];
              if (sectionScores && sectionScores[k] != null) {
                return (
                  <ScoreItemBar key={k} label={label} got={sectionScores[k]} max={5} color={color}
                                note={signals.slice(0, 4).join('、') || undefined} />
                );
              }
              // Without real sub-item scores only express signal presence; never fabricate a score
              return (
                <div key={k}>
                  <div className="flex items-baseline justify-between gap-2 mb-1">
                    <span className="text-sm text-primary">{label}</span>
                    <span className="text-xs font-semibold"
                          style={{ color: signals.length ? 'var(--success-fg)' : 'var(--text-muted)' }}>
                      {signals.length
                        ? t('diagnosis.report.sections.authority.hasSignal', { n: signals.length })
                        : t('diagnosis.report.sections.authority.noSignal')}
                    </span>
                  </div>
                  {signals.length > 0 && (
                    <p className="text-xs text-secondary m-0">{signals.slice(0, 4).join('、')}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Eight entity recognition sub-items -- real numbers available now */}
      {ent && Object.keys(entScores).length > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.authority.entityTitle')}
                      hint={t('diagnosis.report.sections.authority.entityHint',
                              { got: ent.total_score, max: ent.max_score })} />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
            {Object.entries(entScores).map(([k, v]) => {
              const short = ENTITY_ITEM_KEYS[k];
              return (
                <ScoreItemBar key={k}
                              label={short
                                ? t(`diagnosis.report.sections.authority.entityItems.${short}`)
                                : k}
                              got={Number(v)} max={20} color="var(--dg-5)" />
              );
            })}
          </div>
        </div>
      )}

      {/* Signal details */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {footprint.length > 0 && (
          <ChipGroup title={t('diagnosis.report.sections.authority.footprint')} items={footprint} />
        )}
        {kgHits.length > 0 && (
          <ChipGroup title={t('diagnosis.report.sections.authority.kgTitle')} items={kgHits} />
        )}
      </div>

      {/* Gaps and actions from the model */}
      {(n.authority_note?.missing_signals?.length ?? 0) > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.authority.missingSignals')} />
          <div className="space-y-1.5">
            {n.authority_note!.missing_signals!.map((s: string, i: number) => (
              <p key={i} className="text-sm text-secondary leading-relaxed m-0">· {s}</p>
            ))}
          </div>
        </div>
      )}
      {(n.authority_note?.actions?.length ?? 0) > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.authority.actions')} />
          <div className="space-y-2">
            {n.authority_note!.actions!.map((a: any, i: number) => (
              <div key={i} className="p-3 rounded-lg" style={{ background: 'var(--bg-tertiary)' }}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-semibold text-primary">{a.action}</span>
                  {a.eta_weeks != null && (
                    <span className="text-xs text-muted whitespace-nowrap">
                      {t('diagnosis.report.sections.outlook.etaWeeks', { weeks: a.eta_weeks })}
                    </span>
                  )}
                </div>
                {a.channel && <p className="text-sm text-secondary mt-0.5 m-0">{a.channel}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {auth && ent && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatBox label={t('diagnosis.report.sections.authority.offsite')}
                   value={`${auth.percent}/100`} accent hue={color} />
          <StatBox label={t('diagnosis.report.sections.authority.entity')}
                   value={`${ent.percent}/100`} hue="var(--dg-5)" />
        </div>
      )}
    </>
  );
}

function formulaFallback(source: string | undefined, t: (k: string) => string): string {
  if (source === 'authority') return t('diagnosis.report.sections.authority.formulaOffsiteOnly');
  if (source === 'entity')    return t('diagnosis.report.sections.authority.formulaEntityOnly');
  return t('diagnosis.report.sections.authority.formulaBoth');
}

function ChipGroup({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="text-sm font-bold text-primary mb-1.5">
        {title}
        <span className="ml-2 text-xs font-normal text-muted tabular-nums">{items.length}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.slice(0, 16).map((p, i) => (
          <span key={i} className="px-2 py-0.5 rounded-md text-xs border border-soft text-secondary">{p}</span>
        ))}
      </div>
    </div>
  );
}
