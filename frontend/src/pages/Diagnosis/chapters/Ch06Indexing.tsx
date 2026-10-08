// Chapter 06 Indexing results — six-metric overview / per platform / per query type + per-item hit details + metric definition notes
//
// The six metric names and definitions are taken verbatim from pages/Reports/lang.ts; never invent your own.
// In P0 the backend can only supply "overall exposure rate" (equivalent to per-engine mention rate); the other five columns take the "not collected" path;
// once backend full_diagnosis_metrics ships, metrics.by_engine / by_scene plug in directly.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { subData, StatBox, NoDataCard, ChapterDataStatus, SectionSub, isPdfExport } from '../reportKit';
import { MetricMatrix, METRIC_ORDER, type MetricKey, type MetricMatrixRow }
  from '../../../components/data/MetricMatrix';
import { engineLabel, sortEngines } from '../../../constants/engines';
import { SCENE_ORDER, sceneLabel } from '../sceneLabels';
import type { ChapterCtx } from './types';

export function Ch06Indexing({ report, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const v = subData(sub, 'visibility');
  const ent = subData(sub, 'entity');

  if (!v) {
    return (
      <>
        <ChapterDataStatus steps={report.progress?.steps} modes={['visibility']} sub={sub} />
        <NoDataCard />
      </>
    );
  }

  const metrics = (v.metrics || null) as any;
  const rates = (v.per_engine_rates || {}) as Record<string, number>;
  const hits = (v.query_hits || []) as
    { query: string; scene?: string; origin?: string;
      hits: Record<string, boolean>; ranks?: Record<string, number | null> }[];
  // Query pack source: model-planned or built-in template; the report must say so explicitly, no silent degradation
  const querySource = (v.query_source || '') as string;
  const engines = sortEngines((v.engines as string[]) || Object.keys(rates));

  const columnLabels = Object.fromEntries(
    METRIC_ORDER.map((k) => [k, t(`diagnosis.report.metricLabels.${k}`)]),
  ) as Record<MetricKey, string>;

  // ── Per-platform rows: prefer the backend's eleven counts, otherwise fill only the exposure rate ──
  const engineRows: MetricMatrixRow[] = metrics?.by_engine?.length
    ? metrics.by_engine.map((r: any) => ({
        key: r.engine, label: engineLabel(r.engine), values: r.metrics || {},
      }))
    : engines.map((e) => ({
        key: e, label: engineLabel(e),
        values: { exposure: rates[e] ?? null } as Partial<Record<MetricKey, number | null>>,
      }));

  // ── Per-query-type rows ──
  const sceneRows: MetricMatrixRow[] = metrics?.by_scene?.length
    ? SCENE_ORDER
        .map((s) => metrics.by_scene.find((r: any) => r.scene === s))
        .filter(Boolean)
        .map((r: any) => ({
          key: r.scene, label: sceneLabel(r.scene, t), values: r.metrics || {},
          note: r.exposure_excluded_from_global
            ? t('diagnosis.report.sections.indexing.excludedFromGlobal') : undefined,
        }))
    : [];

  const overall = metrics?.overall?.metrics as Partial<Record<MetricKey, number | null>> | undefined;

  // Sentiment distribution of AI answers. The verdicts have always been stored in metrics.rows[].stance, but before 2026-09-27
  // they were only used to compute the single "content neutral-positive rate" percentage, and the distribution itself was never shown -- report readers couldn't tell
  // whether "100% neutral-positive" meant truly no negatives or just very few hits. Here the denominator and all three tiers are laid out.
  // Note this is not the same as chapter 10's sentiment analysis: that measures web-wide evidence, this measures the wording of the AI answers themselves.
  const stanceRows = ((metrics?.rows || []) as {
    stance?: string | null; mentioned?: boolean; error?: string | null }[])
    .filter((r) => !r.error && r.mentioned);
  const stance = {
    positive: stanceRows.filter((r) => r.stance === 'positive').length,
    neutral: stanceRows.filter((r) => r.stance === 'neutral').length,
    negative: stanceRows.filter((r) => r.stance === 'negative').length,
  };
  const stanceTotal = stance.positive + stance.neutral + stance.negative;

  return (
    <>
      <ChapterDataStatus steps={report.progress?.steps} modes={['visibility']} sub={sub} />

      {/* Six-metric overview */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {METRIC_ORDER.map((k) => {
          const val = overall?.[k];
          return (
            <StatBox key={k}
                     label={t(`diagnosis.report.metricLabels.${k}`)}
                     accent={k === 'exposure'}
                     value={val == null
                       ? <span className="text-muted text-base">—</span>
                       : `${val}%`} />
          );
        })}
      </div>

      {stanceTotal > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.indexing.stanceTitle',
                               { defaultValue: 'AI 回答情感分布' })}
                      hint={t('diagnosis.report.sections.indexing.stanceHint', {
                        n: stanceTotal,
                        defaultValue: '仅统计提到本品牌的 {{n}} 条回答;「内容中正率」就是由它折算而来。与第 10 章的全网舆情不是同一口径。',
                      })} />
          <div className="grid grid-cols-3 gap-3">
            {([['positive', stance.positive, 'var(--success-fg)'],
               ['neutral', stance.neutral, 'var(--text-secondary)'],
               ['negative', stance.negative, 'var(--danger-fg)']] as const).map(
              ([k, v, color]) => (
                <div key={k} className="rounded-lg border border-soft px-3 py-2">
                  <div className="text-xs text-muted">
                    {t(`diagnosis.report.sections.indexing.stance.${k}`, {
                      defaultValue: k === 'positive' ? '正面' : k === 'neutral' ? '中性' : '负面',
                    })}
                  </div>
                  <div className="text-lg font-bold tabular-nums" style={{ color }}>
                    {Math.round((v / stanceTotal) * 100)}%
                    <span className="text-xs font-normal text-muted ml-1.5">{v} 条</span>
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {ent?.recognition_rate != null && (
        <p className="text-sm text-secondary m-0">
          {t('diagnosis.report.sections.indexing.recognitionRate')}:
          <span className="font-semibold text-primary tabular-nums"> {ent.recognition_rate}%</span>
        </p>
      )}

      {/* Per platform × six metrics */}
      <div>
        <SectionSub title={t('diagnosis.report.sections.indexing.byEngine')} />
        <MetricMatrix rows={engineRows}
                      columnLabels={columnLabels}
                      rowHeader={t('diagnosis.report.sections.indexing.colEngine')}
                      emptyText="—" />
      </div>

      {/* Per query type × six metrics */}
      {sceneRows.length > 0 && (
        <div>
          <SectionSub title={t('diagnosis.report.sections.indexing.byScene')} />
          <MetricMatrix rows={sceneRows}
                        columnLabels={columnLabels}
                        rowHeader={t('diagnosis.report.sections.indexing.colScene')}
                        emptyText="—" />
        </div>
      )}

      {/* Per prompt × engine */}
      {hits.length > 0 && <QueryDetail rows={hits} engines={engines} source={querySource} />}

      {/* Metric definition notes: hover tooltips don't make it into the PDF, so there must be a static place for them */}
      <div className="pt-2">
        <SectionSub title={t('diagnosis.report.sections.indexing.glossary')} />
        <div className="space-y-1">
          {METRIC_ORDER.map((k) => (
            <p key={k} className="text-xs text-muted m-0 leading-relaxed">
              <span className="font-semibold text-secondary">
                {t(`diagnosis.report.metricLabels.${k}`)}
              </span>
              {' — '}
              {t(`diagnosis.report.metricHints.${k}`)}
            </p>
          ))}
          {metrics?.metric_scope?.citation_scope === 'own_domain' && (
            <p className="text-xs text-muted m-0 leading-relaxed">
              {t('diagnosis.report.sections.indexing.citationScopeNote')}
            </p>
          )}
          {/* Mentioned ≠ recommended: unless stated clearly, readers think a low exposure rate means not being mentioned */}
          {metrics?.metric_scope?.hit_rule === 'recommended_only' && (
            <p className="text-xs text-muted m-0 leading-relaxed">
              {t('diagnosis.report.sections.indexing.hitRuleNote', {
                mentioned: Number(metrics.metric_scope.mentioned_count ?? 0),
                recommended: Number(metrics.metric_scope.recommended_count ?? 0),
                defaultValue: '露出口径 — 品牌名共在 {{mentioned}} 条回答里出现,其中 {{recommended}} 条是被推荐或列入名单;只有后者计入露出率。仅作背景举例、顺带带过的提及不算露出。',
              })}
            </p>
          )}
        </div>
      </div>
    </>
  );
}

/** Per-item details: collapsible groups by query type, question × engine within each group */
function QueryDetail({ rows, engines, source }: {
  rows: { query: string; scene?: string; origin?: string;
          hits: Record<string, boolean>; ranks?: Record<string, number | null> }[];
  engines: string[];
  source?: string;
}) {
  const { t } = useTranslation();
  const grouped = new Map<string, typeof rows>();
  for (const r of rows) {
    const s = r.scene || 'unknown';
    grouped.set(s, [...(grouped.get(s) || []), r]);
  }
  const scenes = SCENE_ORDER.filter((s) => grouped.has(s));
  // Ungrouped (backend doesn't supply scene yet): degrade to a single group
  const groups = scenes.length > 0
    ? scenes.map((s) => ({ scene: s, items: grouped.get(s)! }))
    : [{ scene: '', items: rows }];

  // On the page, only groups "with missed questions" are expanded by default; for PDF export everything is expanded --
  // a collapsed group is missing content in the PDF, and the PDF is a deliverable, so nothing can be left out
  const exporting = isPdfExport();
  const [open, setOpen] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(groups.map((g) => [
      g.scene,
      g.items.some((r) => !Object.values(r.hits || {}).some(Boolean)),
    ])),
  );

  return (
    <div>
      <SectionSub title={t('diagnosis.report.sections.indexing.queryDetail')} />
      {source && (
        <p className="text-xs text-muted m-0 mb-2 leading-relaxed">
          {source === 'manual'
            ? t('diagnosis.report.sections.indexing.querySourceManual', {
                n: rows.length,
                defaultValue: '本次 {{n}} 条提示词为方案人工指定,系统未做改写或扩展。',
              })
            : source === 'llm'
            ? t('diagnosis.report.sections.indexing.querySourceLlm', {
                n: rows.length,
                m: rows.filter((r) => r.origin).length,
                defaultValue: '本次 {{n}} 条提示词由 AI 规划,其中 {{m}} 条基于你填写的内容改写或拆分,原句标在问题下方。',
              })
            : t('diagnosis.report.sections.indexing.querySourceTemplate', {
                defaultValue: '本次提示词由内置模板生成。',
              })}
        </p>
      )}
      <div className="space-y-2">
        {groups.map((g) => {
          const isOpen = exporting || g.scene === '' || open[g.scene];
          return (
            <div key={g.scene} className="rounded-xl border border-soft overflow-hidden">
              {g.scene !== '' && (
                <button type="button"
                        onClick={() => setOpen((o) => ({ ...o, [g.scene]: !o[g.scene] }))}
                        className="w-full flex items-center gap-2 px-3 py-2 text-sm"
                        style={{ background: 'var(--bg-tertiary)' }}>
                  {!exporting && <span className="text-xs">{isOpen ? '▼' : '▶'}</span>}
                  <span className="font-semibold text-primary">{sceneLabel(g.scene, t)}</span>
                  <span className="text-xs text-muted">
                    {t('diagnosis.report.sections.scenes.wordCount', { n: g.items.length })}
                  </span>
                </button>
              )}
              {isOpen && (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ background: 'var(--bg-tertiary)' }}>
                        <th className="text-left px-3 py-2 font-medium text-secondary">
                          {t('diagnosis.report.sections.indexing.colQuery')}
                        </th>
                        {engines.map((e) => (
                          <th key={e} className="px-2 py-2 font-medium text-secondary text-center whitespace-nowrap">
                            {engineLabel(e)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {g.items.map((row, i) => (
                        <tr key={i} style={{ borderTop: '1px solid var(--grid-line)' }}>
                          <td className="px-3 py-2 text-primary">
                            <div>{row.query}</div>
                            {row.origin && (
                              <div className="text-xs text-muted mt-0.5">
                                {t('diagnosis.report.sections.indexing.rewrittenFrom',
                                   { origin: row.origin, defaultValue: '改写自:{{origin}}' })}
                              </div>
                            )}
                          </td>
                          {engines.map((e) => (
                            <td key={e} className="px-2 py-2 text-center">
                              <HitCell hit={row.hits?.[e]} rank={row.ranks?.[e]} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Cell: show the rank if available (colored by 1/3 tiers), otherwise just mark hit or not */
function HitCell({ hit, rank }: { hit?: boolean; rank?: number | null }) {
  if (hit === undefined) return <span className="text-muted text-xs">·</span>;
  if (!hit) {
    return (
      <span className="inline-flex items-center justify-center w-5 h-5 rounded text-xs"
            style={{ background: 'var(--danger-bg)', color: 'var(--text-muted)' }}>—</span>
    );
  }
  const style = rank === 1
    ? { background: 'rgba(16,185,129,0.22)', color: 'var(--success-fg)' }
    : rank != null && rank <= 3
      ? { background: 'var(--success-bg)', color: 'var(--success-fg)' }
      : { background: 'var(--success-bg)', color: 'var(--success-fg)' };
  return (
    <span className="inline-flex items-center justify-center w-5 h-5 rounded text-xs font-bold tabular-nums"
          style={style}>
      {rank != null ? rank : '✓'}
    </span>
  );
}
