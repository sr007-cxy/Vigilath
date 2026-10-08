// Chapter 04 Search scenes and intent
//   Top half "Search scenes" = what we measured (real numbers: structural distribution of five kinds of seed queries)
//   Bottom half "Search intent" = what users want (model interpretation + two real numbers from the entity audit's narrative framing)
import { useTranslation } from 'react-i18next';
import { subData, SectionSub, SourceBadge, Prose, NoNarrative } from '../reportKit';
import { useDgPalette } from '../palette';
import { SCENE_ORDER, sceneLabel } from '../sceneLabels';
import type { ChapterCtx } from './types';

export function Ch04Scenes({ n, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const dg = useDgPalette();
  const vis = subData(sub, 'visibility');
  const ent = subData(sub, 'entity');

  // Query type distribution: only available when the backend tagged each prompt with a scene (data supplied since P1)
  const rows = (vis?.query_hits || []) as { query: string; scene?: string; hits: Record<string, boolean> }[];
  const buckets = new Map<string, { count: number; hit: number; samples: string[] }>();
  for (const r of rows) {
    const s = r.scene || 'unknown';
    const b = buckets.get(s) || { count: 0, hit: 0, samples: [] };
    b.count += 1;
    if (Object.values(r.hits || {}).some(Boolean)) b.hit += 1;
    if (b.samples.length < 2) b.samples.push(r.query);
    buckets.set(s, b);
  }
  const scenes = SCENE_ORDER.filter((s) => buckets.has(s));
  const total = rows.length;

  return (
    <>
      {/* ── Search scenes ── */}
      <div>
        <SectionSub title={t('diagnosis.report.sections.scenes.sceneTitle')}
                    hint={t('diagnosis.report.sections.scenes.sceneHint')} />
        {scenes.length === 0 ? (
          <p className="text-sm text-secondary m-0">
            {t('diagnosis.report.sections.scenes.sceneUnavailable')}
          </p>
        ) : (
          <>
            {/* Share stacked bar */}
            <div className="flex h-3 rounded-full overflow-hidden mb-3" style={{ background: 'var(--dg-track)' }}>
              {scenes.map((s, i) => {
                const b = buckets.get(s)!;
                return (
                  <div key={s} style={{ width: `${(b.count / Math.max(total, 1)) * 100}%`,
                                        background: dg.series(i) }} />
                );
              })}
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              {scenes.map((s, i) => {
                const b = buckets.get(s)!;
                const rate = Math.round((b.hit / Math.max(b.count, 1)) * 1000) / 10;
                return (
                  <div key={s} className="p-3 rounded-xl border border-soft"
                       style={{ borderTopWidth: 3, borderTopColor: dg.series(i) }}>
                    <div className="text-sm font-bold text-primary mb-1">{sceneLabel(s, t)}</div>
                    <div className="text-xs text-secondary tabular-nums">
                      {t('diagnosis.report.sections.scenes.wordCount', { n: b.count })}
                    </div>
                    <div className="text-sm font-semibold tabular-nums" style={{ color: dg.series(i) }}>
                      {rate}%
                    </div>
                    <div className="text-xs text-muted">
                      {t('diagnosis.report.sections.scenes.hitRate')}
                    </div>
                    {b.samples.length > 0 && (
                      <p className="text-xs text-secondary mt-1.5 m-0 leading-relaxed">
                        {b.samples.join(' / ')}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* ── Search intent ── */}
      <div>
        <SectionSub title={t('diagnosis.report.sections.scenes.intentTitle')}
                    hint={t('diagnosis.report.sections.scenes.intentHint')} />
        {n.scene_intent?.summary && <Prose tinted>{n.scene_intent.summary}</Prose>}

        {(n.personas?.length ?? 0) > 0 ? (
          <div className="space-y-2 mt-3">
            {n.personas!.filter((p) => p.intent).map((p, i) => (
              <div key={i} className="flex items-start gap-3 p-3 rounded-lg"
                   style={{ background: 'var(--bg-tertiary)' }}>
                <SourceBadge kind="ai" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-primary">{p.intent}</div>
                  {p.scenario && (
                    <p className="text-sm text-secondary leading-relaxed mt-0.5 m-0">{p.scenario}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : !n.scene_intent?.summary ? <NoNarrative /> : null}

        {/* Two real numbers from the entity audit: sentiment and narrative framing when AI talks about this brand */}
        {ent && (ent.sentiment || ent.best_framing) && (
          <div className="flex flex-wrap items-center gap-2 mt-3">
            <SourceBadge kind="measured" />
            {ent.sentiment && (
              <span className="px-2 py-0.5 rounded-md text-xs border border-soft text-secondary">
                {t('diagnosis.report.sections.scenes.aiSentiment')}:{String(ent.sentiment)}
              </span>
            )}
            {ent.best_framing && (
              <span className="px-2 py-0.5 rounded-md text-xs border border-soft text-secondary">
                {t('diagnosis.report.sections.scenes.aiFraming')}:{String(ent.best_framing)}
              </span>
            )}
          </div>
        )}

        {(n.scene_intent?.gaps?.length ?? 0) > 0 && (
          <div className="mt-3">
            <div className="text-sm font-bold text-primary mb-1.5">
              {t('diagnosis.report.sections.scenes.gaps')}
            </div>
            <div className="space-y-1.5">
              {n.scene_intent!.gaps!.map((g, i) => (
                <p key={i} className="text-sm text-secondary leading-relaxed m-0">· {g}</p>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
