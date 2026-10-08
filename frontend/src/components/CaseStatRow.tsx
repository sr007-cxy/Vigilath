import { useTranslation } from 'react-i18next';
import type { CaseStudy } from '../data/caseStudies';

type TFn = ReturnType<typeof useTranslation>['t'];
// Three big numbers side by side: customers/days (largest) · hit question count · cited article count. Thin-line separators, no box. Shared by list and detail pages.

export function CaseStatRow({ c, t, nf }: { c: CaseStudy; t: TFn; nf: Intl.NumberFormat }) {
  // Lead with customer count if available; otherwise (business didn't provide it) lead with hit count and end with engine count.
  const hit = { value: nf.format(c.hitQueries), label: t('cases.story.statHit'), note: t('cases.story.statHitNote', { max: c.engineMax }) };
  const cited = { value: nf.format(c.metrics.citedByAI), label: t('cases.story.statCited'), note: t('cases.story.statCitedNote', { n: c.metrics.indexedCount }) };
  const stats = c.dailyLeads
    ? [{ value: `${nf.format(c.dailyLeads.before)} → ${nf.format(c.dailyLeads.after)}`, label: t('cases.story.statLeads'), note: t('cases.story.statLeadsNote') }, hit, cited]
    : [hit, cited, { value: nf.format(c.engineHits.length), label: t('cases.story.statEngines'), note: t('cases.story.statEnginesNote') }];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 border-t border-b" style={{ borderColor: 'var(--border-color)' }}>
      {stats.map((st, i) => (
        <div key={i}
          className={`py-6 sm:py-8 ${i > 0 ? 'border-t sm:border-t-0 sm:border-l sm:pl-8' : ''} ${i < 2 ? 'sm:pr-8' : ''}`}
          style={{ borderColor: 'var(--border-color)' }}>
          {/* Number font size matches the home page h1 (2xl / 4xl / 5xl), no longer larger than the title */}
          <div className="font-black leading-none tabular-nums tracking-tight text-primary text-2xl sm:text-4xl lg:text-5xl">
            {st.value}
          </div>
          <div className="text-base font-bold text-primary mt-3 leading-tight">{st.label}</div>
          <div className="text-xs text-muted mt-1">{st.note}</div>
        </div>
      ))}
    </div>
  );
}
