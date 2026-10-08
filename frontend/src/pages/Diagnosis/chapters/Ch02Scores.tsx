// Chapter 02 Five-dimension scores — radar + total score + strongest/weakest + dimension breakdown cards + total score composition note
import { useTranslation } from 'react-i18next';
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer,
} from 'recharts';
import { BarRow, ScoreItemBar, NoDataCard } from '../reportKit';
import { DIM_COLOR, DIM_ORDER, useDgPalette, gradeColorOf } from '../palette';
import type { ChapterCtx } from './types';
import type { DiagnosisDimension } from '../../../services/fullDiagnosisApi';

export function Ch02Scores({ scores, n }: ChapterCtx) {
  const { t } = useTranslation();
  const dg = useDgPalette();
  if (!scores) return <NoDataCard />;

  const dims = scores.dimensions || [];
  const scored = dims.filter((d) => d.score != null);
  const missing = dims.filter((d) => d.score == null);
  const radarData = dims.map((d) => ({
    name: t(`diagnosis.report.dims.${d.key}`, { defaultValue: d.key }),
    score: d.score ?? 0,
  }));
  const gradeColor = gradeColorOf(scores.total);
  const sorted = [...scored].sort((a, b) => (b.score as number) - (a.score as number));
  const best = sorted[0];
  const worst = sorted.length > 1 ? sorted[sorted.length - 1] : null;

  return (
    <>
      {/* Top box: radar + total score + grade + strongest/weakest */}
      <div className="rounded-2xl border border-soft p-5 sm:p-6"
           style={{ background: 'var(--bg-tertiary)' }}>
        <div className="grid grid-cols-1 lg:grid-cols-[300px_auto_1fr] gap-5 items-center">
          <div style={{ width: '100%', height: 250 }}>
            <ResponsiveContainer minWidth={0}>
              <RadarChart data={radarData} outerRadius="68%">
                <defs>
                  <linearGradient id="diagRadarFill" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor={dg.hex[0]} stopOpacity={0.75} />
                    <stop offset="100%" stopColor={dg.hex[1]} stopOpacity={0.55} />
                  </linearGradient>
                </defs>
                <PolarGrid stroke={dg.hex[0]} strokeOpacity={0.28} />
                <PolarAngleAxis dataKey="name"
                                tick={{ fontSize: 11, fill: dg.textPrimary, fontWeight: 700 }} />
                <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
                <Radar dataKey="score" stroke={dg.hex[0]} strokeWidth={3}
                       fill="url(#diagRadarFill)" fillOpacity={1}
                       dot={{ r: 5, strokeWidth: 2, stroke: '#ffffff', fill: dg.hex[0] }} />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          <div className="text-center lg:px-4">
            <div className="text-5xl font-black leading-none tabular-nums" style={{ color: gradeColor }}>
              {scores.total}
            </div>
            <div className="text-xs text-muted mt-1">/100</div>
          </div>

          <div className="space-y-3">
            <span className="inline-block px-3 py-1 rounded-lg text-sm font-bold"
                  style={{ border: `2px solid ${gradeColor}`, color: gradeColor }}>
              {scores.grade}
            </span>
            {best && worst && (
              <div className="space-y-2">
                {[
                  { d: best, label: t('diagnosis.report.sections.scores.best'), c: 'var(--success-fg)' },
                  { d: worst, label: t('diagnosis.report.sections.scores.worst'), c: 'var(--danger-fg)' },
                ].map(({ d, label, c }) => (
                  <div key={label} className="flex items-center gap-2 text-sm">
                    <span className="text-xs px-2 py-0.5 rounded font-semibold whitespace-nowrap"
                          style={{ background: 'var(--neutral-bg)', color: c }}>{label}</span>
                    <span className="font-semibold text-primary">
                      {t(`diagnosis.report.dims.${d.key}`, { defaultValue: d.key })}
                    </span>
                    <span className="font-bold tabular-nums" style={{ color: dg.dim(d.key) }}>{d.score}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        {/* The radar always keeps all five axes; missing dimensions sit at 0 as placeholders, but the total is still renormalized over the dimensions the backend collected. */}
        {missing.length > 0 && (
          <p className="text-sm text-secondary mt-3 m-0">
            {t('diagnosis.report.sections.scores.missingDims', {
              names: missing.map((d) => t(`diagnosis.report.dims.${d.key}`, { defaultValue: d.key })).join('、'),
            })}
          </p>
        )}
      </div>

      {/* Dimension breakdown + the total score composition note in the last cell; a 3+2 layout fills the grid exactly */}
      <div>
        <h3 className="text-sm font-bold text-primary mb-3">
          {t('diagnosis.report.sections.scores.breakdown')}
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {dims.map((d) => (
            <DimensionCard key={d.key} dim={d} note={n.dimension_notes?.[d.key]} />
          ))}
          <FormulaCard scores={scores} />
        </div>
      </div>
    </>
  );
}

function DimensionCard({ dim: d, note }: { dim: DiagnosisDimension; note?: string }) {
  const { t } = useTranslation();
  const color = DIM_COLOR[d.key] || 'var(--dg-1)';
  const detail = (d as any).detail as
    | { formula?: string; groups?: { title: string; got: number; max: number; items?: any[] }[] }
    | undefined;

  return (
    <div className="p-4 rounded-xl border border-soft"
         style={{ borderLeftWidth: 4, borderLeftColor: color }}>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-3xl font-black leading-none tabular-nums" style={{ color }}>
          {d.score != null ? d.score : '—'}
        </span>
        <span className="text-xs text-muted">
          {t('diagnosis.report.sections.scores.weight')}{Math.round(d.weight * 100)}%
        </span>
      </div>
      <div className="text-sm font-bold text-primary mb-1.5">
        {t(`diagnosis.report.dims.${d.key}`, { defaultValue: d.key })}
        {d.estimated && (
          <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded font-normal align-middle"
                style={{ background: 'var(--warn-bg)', color: 'var(--warn-fg)' }}>
            {t('diagnosis.report.estimated')}
          </span>
        )}
      </div>
      {/* Composition metric definition: use the backend's when provided (on one-sided degradation it won't show fake weighting ratios) */}
      {detail?.formula && (
        <p className="text-xs text-secondary m-0 mb-1.5">{detail.formula}</p>
      )}
      {d.score != null && <BarRow value={d.score} color={color} />}
      {note && <p className="text-sm text-secondary leading-relaxed mt-2 m-0">{note}</p>}
    </div>
  );
}

/** Last cell: how the total score is computed -- so users seeing "one dimension is empty but the total isn't low" aren't confused */
function FormulaCard({ scores }: { scores: NonNullable<ChapterCtx['scores']> }) {
  const { t } = useTranslation();
  const s = scores as any;
  const reweighted = Boolean(s.reweighted);
  return (
    <div className="p-4 rounded-xl border border-dashed border-soft space-y-2">
      <div className="text-sm font-bold text-primary">
        {t('diagnosis.report.sections.scores.formulaTitle')}
      </div>
      <p className="text-sm text-secondary leading-relaxed m-0">
        {s.formula || t('diagnosis.report.sections.scores.totalFormula')}
      </p>
      {reweighted && (
        <p className="text-sm text-secondary leading-relaxed m-0">
          {t('diagnosis.report.sections.scores.weightRedistribute')}
        </p>
      )}
      <div>
        <div className="text-xs text-muted mb-1">
          {t('diagnosis.report.sections.scores.gradeScale')}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(s.grade_scale || [['A', 85], ['B', 70], ['C', 55], ['D', 40], ['F', 0]]).map(
            ([g, min]: [string, number]) => (
              <span key={g} className="px-2 py-0.5 rounded-md text-xs border border-soft text-secondary tabular-nums">
                {g} ≥ {min}
              </span>
            ),
          )}
        </div>
      </div>
    </div>
  );
}

/** Sub-item bar renderer needed by the authority endorsement chapter, exported here for reuse */
export { ScoreItemBar, DIM_ORDER };
