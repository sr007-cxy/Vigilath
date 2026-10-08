// Chapter 11 Overall assessment and outlook — current state and targets merged with explicit numbers
//
// The core is a reconciliation table: it converges the possibly contradictory numbers scattered across the total score forecast and dimension forecasts into one,
// and translates "expected +23 points" into "actual contribution of 8.1 points to the composite score".
import { useTranslation } from 'react-i18next';
import { StatBox, SectionSub, BarRow, Prose, NoNarrative } from '../reportKit';
import { DIM_COLOR, gradeColorOf } from '../palette';
import type { ChapterCtx } from './types';

export function Ch11Outlook({ scores, n }: ChapterCtx) {
  const { t } = useTranslation();
  const proj = n.score_projection;
  const total = scores?.total ?? 0;
  const dims = scores?.dimensions || [];
  const dimProj = n.dimension_projection || {};

  const hasContent = proj || (n.quick_wins?.length ?? 0) > 0 || (n.p1_actions?.length ?? 0) > 0
    || Boolean(n.overall_assessment?.verdict);
  if (!hasContent) return <NoNarrative />;

  // Reconciliation: per dimension current -> target, converted into its contribution to the total
  const rows = dims
    .filter((d) => d.score != null)
    .map((d) => {
      const p = (dimProj as any)[d.key] as { current: number; target: number } | undefined;
      const current = d.score as number;
      const target = p?.target != null ? Math.max(p.target, current) : current;
      const gain = target - current;
      return { key: d.key, weight: d.weight, current, target, gain };
    });
  const weightSum = rows.reduce((a, r) => a + r.weight, 0);
  // Contributions are normalized by the weights of dimensions with data, consistent with the backend total score definition (weights redistributed proportionally when dimensions are missing)
  const contribOf = (r: { gain: number; weight: number }) =>
    weightSum > 0 ? (r.gain * r.weight) / weightSum : 0;
  const totalGain = rows.reduce((a, r) => a + contribOf(r), 0);
  const computedTarget = Math.round(total + totalGain);
  // When the model's 90-day total doesn't match the per-dimension computation, the computed value wins and is flagged
  const mismatch = proj?.after_90d != null && Math.abs(proj.after_90d - computedTarget) > 3;

  const actionGain =
    [...(n.quick_wins || []), ...(n.p1_actions || []), ...(n.p2_actions || [])]
      .reduce((a, x: any) => a + (Number(x.expected_gain) || 0), 0);

  return (
    <>
      {n.overall_assessment?.verdict && <Prose tinted>{n.overall_assessment.verdict}</Prose>}

      {n.overall_assessment?.stage && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-secondary">
            {t('diagnosis.report.sections.outlook.stage')}
          </span>
          <span className="px-2.5 py-0.5 rounded-lg text-sm font-bold"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--data-accent)' }}>
            {n.overall_assessment.stage}
          </span>
        </div>
      )}

      {/* Three-point scale */}
      {proj && (
        <div className="grid grid-cols-3 gap-3">
          <StatBox label={t('diagnosis.report.sections.outlook.current')} value={total} />
          <StatBox label={t('diagnosis.report.sections.outlook.after30')} value={proj.after_30d} />
          <StatBox label={t('diagnosis.report.sections.outlook.after90')}
                   value={proj.after_90d} accent />
        </div>
      )}

      {/* Reconciliation table */}
      {rows.length > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.outlook.currentVsTarget')} />
          <div className="overflow-x-auto rounded-xl border border-soft">
            <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-tertiary)' }}>
                  <th className="text-left px-3 py-2 font-medium text-secondary">
                    {t('diagnosis.report.sections.scores.breakdown')}
                  </th>
                  <th className="px-2 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.colWeight')}
                  </th>
                  <th className="px-2 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.colCurrent')}
                  </th>
                  <th className="px-2 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.colTarget')}
                  </th>
                  <th className="px-2 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.colGain')}
                  </th>
                  <th className="px-3 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.colContribution')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const color = DIM_COLOR[r.key] || 'var(--dg-1)';
                  return (
                    <tr key={r.key} style={{ borderTop: '1px solid var(--grid-line)' }}>
                      <td className="px-3 py-2 text-primary whitespace-nowrap">
                        <span className="inline-block w-2 h-2 rounded-full mr-2 align-middle"
                              style={{ background: color }} />
                        {t(`diagnosis.report.dims.${r.key}`, { defaultValue: r.key })}
                      </td>
                      <td className="px-2 py-2 text-right text-muted tabular-nums">
                        {Math.round(r.weight * 100)}%
                      </td>
                      <td className="px-2 py-2 text-right text-primary tabular-nums">{r.current}</td>
                      <td className="px-2 py-2 text-right font-semibold tabular-nums"
                          style={{ color: 'var(--success-fg)' }}>{r.target}</td>
                      <td className="px-2 py-2 text-right tabular-nums"
                          style={{ color: r.gain > 0 ? 'var(--success-fg)' : 'var(--text-muted)' }}>
                        {r.gain > 0 ? `+${r.gain}` : '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-semibold text-primary tabular-nums">
                        {contribOf(r) > 0 ? `+${contribOf(r).toFixed(1)}` : '—'}
                      </td>
                    </tr>
                  );
                })}
                <tr style={{ borderTop: '2px solid var(--border-color)', background: 'var(--bg-tertiary)' }}>
                  <td className="px-3 py-2 font-bold text-primary">
                    {t('diagnosis.report.sections.outlook.projectedTotal')}
                  </td>
                  <td />
                  <td className="px-2 py-2 text-right font-bold text-primary tabular-nums">{total}</td>
                  <td className="px-2 py-2 text-right font-black tabular-nums"
                      style={{ color: gradeColorOf(computedTarget) }}>{computedTarget}</td>
                  <td />
                  <td className="px-3 py-2 text-right font-bold tabular-nums"
                      style={{ color: 'var(--success-fg)' }}>
                    {totalGain > 0 ? `+${totalGain.toFixed(1)}` : '—'}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Per-dimension progress bars: target underneath, current state on top */}
          <div className="mt-3 space-y-2">
            {rows.filter((r) => r.gain > 0).map((r) => (
              <div key={r.key} className="flex items-center gap-3 text-sm">
                <span className="w-28 flex-shrink-0 text-primary truncate">
                  {t(`diagnosis.report.dims.${r.key}`, { defaultValue: r.key })}
                </span>
                <div className="flex-1 relative">
                  <BarRow value={r.target} max={100} color="var(--success-solid)" />
                  <div className="absolute inset-0">
                    <BarRow value={r.current} max={100} bareTrack
                            color={DIM_COLOR[r.key] || 'var(--dg-1)'} />
                  </div>
                </div>
                <span className="w-20 text-right text-xs text-primary whitespace-nowrap tabular-nums">
                  {r.current} → <span className="font-bold" style={{ color: 'var(--success-fg)' }}>{r.target}</span>
                </span>
              </div>
            ))}
          </div>

          {mismatch && (
            <p className="text-sm mt-2 m-0" style={{ color: 'var(--warn-fg)' }}>
              {t('diagnosis.report.sections.outlook.mismatchNote', {
                model: proj!.after_90d, computed: computedTarget,
              })}
            </p>
          )}
          {actionGain > 0 && (
            <p className="text-sm text-secondary mt-1.5 m-0">
              {t('diagnosis.report.sections.outlook.actionGainSum', { n: actionGain })}
            </p>
          )}
          {/* The forecast premises are the conditions under which all numbers in the chapter hold -- the sentence that should least be downplayed */}
          {proj?.assumptions && (
            <p className="text-sm text-primary leading-relaxed mt-2 m-0">
              {t('diagnosis.report.sections.outlook.assumptions')}:{proj.assumptions}
            </p>
          )}
        </div>
      )}

      {/* Six-metric forecast */}
      {(n.overall_assessment?.metric_projection?.length ?? 0) > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.outlook.metricProjection')} />
          <div className="overflow-x-auto rounded-xl border border-soft">
            <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-tertiary)' }}>
                  <th className="text-left px-3 py-2 font-medium text-secondary">
                    {t('diagnosis.report.sections.indexing.glossary')}
                  </th>
                  <th className="px-2 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.current')}
                  </th>
                  <th className="px-2 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.after30')}
                  </th>
                  <th className="px-2 py-2 font-medium text-secondary text-right">
                    {t('diagnosis.report.sections.outlook.after90')}
                  </th>
                  <th className="text-left px-3 py-2 font-medium text-secondary">
                    {t('diagnosis.report.sections.outlook.driver')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {n.overall_assessment!.metric_projection!.map((m: any) => (
                  <tr key={m.key} style={{ borderTop: '1px solid var(--grid-line)' }}>
                    <td className="px-3 py-2 text-primary whitespace-nowrap">
                      {t(`diagnosis.report.metricLabels.${m.key}`, { defaultValue: m.key })}
                    </td>
                    <td className="px-2 py-2 text-right text-primary tabular-nums">{m.current}%</td>
                    <td className="px-2 py-2 text-right text-primary tabular-nums">{m.after_30d}%</td>
                    <td className="px-2 py-2 text-right font-semibold tabular-nums"
                        style={{ color: 'var(--success-fg)' }}>{m.after_90d}%</td>
                    <td className="px-3 py-2 text-secondary leading-relaxed">{m.driver}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Metric definition footnote: this table is most easily read as "the share of all questions in the top three";
              the fact that brand queries/competitor comparison queries are excluded must be stated right next to the numbers */}
          <p className="text-xs text-muted m-0 mt-1.5 leading-relaxed">
            {t('diagnosis.report.sections.outlook.metricProjectionNote')}
          </p>
        </div>
      )}

      {/* Quick wins */}
      {(n.quick_wins?.length ?? 0) > 0 && (
        <div>
          <SectionSub title={`⚡ ${t('diagnosis.report.sections.outlook.quickWins')}`} />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {n.quick_wins!.map((q, i) => (
              <div key={i} className="p-4 rounded-xl space-y-1.5" style={{ background: 'var(--warn-bg)' }}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-semibold text-primary">{q.action}</span>
                  {(q as any).expected_gain > 0 && (
                    <GainBadge n={(q as any).expected_gain} />
                  )}
                </div>
                <p className="text-sm text-secondary leading-relaxed m-0">
                  {t('diagnosis.report.sections.outlook.expectedImpact')}:{q.expected_impact}
                </p>
                <span className="inline-block text-xs px-1.5 py-0.5 rounded"
                      style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
                  {t(`diagnosis.report.sections.outlook.effort.${q.effort}`, { defaultValue: q.effort })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Priority actions */}
      {(['p1_actions', 'p2_actions'] as const).map((key) => {
        const items = n[key];
        if (!items?.length) return null;
        const label = key === 'p1_actions'
          ? t('diagnosis.report.sections.outlook.p1')
          : t('diagnosis.report.sections.outlook.p2');
        const color = key === 'p1_actions' ? 'var(--danger-solid)' : 'var(--warn-solid)';
        return (
          <div key={key}>
            <SectionSub title={label} />
            <div className="space-y-2">
              {items.map((a, i) => (
                <div key={i} className="p-3 rounded-lg"
                     style={{ borderLeft: `3px solid ${color}`, background: 'var(--bg-tertiary)' }}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm font-semibold text-primary">{i + 1}. {a.action}</span>
                    <span className="flex items-baseline gap-2 whitespace-nowrap">
                      {(a as any).expected_gain > 0 && <GainBadge n={(a as any).expected_gain} />}
                      {a.eta_weeks != null && (
                        <span className="text-xs text-muted">
                          {t('diagnosis.report.sections.outlook.etaWeeks', { weeks: a.eta_weeks })}
                        </span>
                      )}
                    </span>
                  </div>
                  <p className="text-sm text-secondary leading-relaxed mt-1 m-0">{a.reason}</p>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {/* Roadmap */}
      {(n.roadmap?.length ?? 0) > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.outlook.roadmap')} />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {n.roadmap!.map((r, i) => (
              <div key={i} className="p-4 rounded-xl border border-soft">
                <div className="flex items-center gap-2 mb-2">
                  <span className="inline-flex items-center justify-center w-5 h-5 rounded-full text-[11px] font-bold text-white"
                        style={{ background: ['var(--danger-solid)', 'var(--warn-solid)', 'var(--success-solid)'][i % 3] }}>
                    {i + 1}
                  </span>
                  <span className="text-sm font-semibold text-primary">{r.phase}</span>
                  <span className="text-xs text-muted ml-auto">{r.weeks}</span>
                </div>
                {(r.goals || []).map((g, j) => (
                  <p key={j} className="text-sm text-secondary m-0 leading-relaxed">· {g}</p>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function GainBadge({ n }: { n: number }) {
  const { t } = useTranslation();
  return (
    <span className="text-xs px-1.5 py-0.5 rounded font-bold whitespace-nowrap tabular-nums"
          style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
      +{n}{t('diagnosis.report.sections.outlook.gainUnit')}
    </span>
  );
}
