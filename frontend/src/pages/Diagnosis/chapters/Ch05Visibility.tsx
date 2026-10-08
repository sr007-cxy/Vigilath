// Chapter 05 Search visibility — overall mention rate and per-engine performance (per-item hit details are in chapter 06)
import { useTranslation } from 'react-i18next';
import { subData, StatBox, BarRow, NoDataCard, ChapterDataStatus } from '../reportKit';
import { useDgPalette } from '../palette';
import { engineLabel, sortEngines } from '../../../constants/engines';
import type { ChapterCtx } from './types';

export function Ch05Visibility({ report, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const dg = useDgPalette();
  const v = subData(sub, 'visibility');

  if (!v) {
    return (
      <>
        <ChapterDataStatus steps={report.progress?.steps} modes={['visibility']} sub={sub} />
        <NoDataCard />
      </>
    );
  }

  const rates = (v.per_engine_rates || {}) as Record<string, number>;
  const engines = sortEngines(Object.keys(rates));
  const maxRate = Math.max(...Object.values(rates), 1);
  const overallRate = v.overall_rate ?? v.total_score ?? 0;
  const tops = (v.top_competitors || []) as { domain: string; mentions: number }[];
  // Platforms with no data this time: either filtered out by the execution tier or the whole column failed. If not shown, the report just "has one column fewer",
  // and readers take it as "this platform didn't mention me" (2026-09-22 #138/#140 were misread exactly this way).
  const failed = (v.failed_engines || []) as
    { engine: string; attempted?: number; reason?: string; error?: string }[];

  return (
    <>
      <ChapterDataStatus steps={report.progress?.steps} modes={['visibility']} sub={sub} />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatBox label={t('diagnosis.report.sections.visibility.overallRate')}
                 value={`${overallRate}%`} accent />
        <StatBox label={t('diagnosis.report.sections.visibility.queryCount')} value={v.query_count ?? 0} />
        {v.mention_count != null && (
          <StatBox label={t('diagnosis.report.sections.visibility.mentionCount')} value={v.mention_count} />
        )}
        {v.total_answers != null && (
          <StatBox label={t('diagnosis.report.sections.visibility.totalAnswers')} value={v.total_answers} />
        )}
      </div>

      <div>
        <h3 className="text-sm font-bold text-primary mb-2">
          {t('diagnosis.report.sections.visibility.engineRates')}
        </h3>
        <div className="space-y-2">
          {engines.map((engine, i) => {
            const rate = rates[engine];
            const color = dg.series(i);
            return (
              <div key={engine} className="flex items-center gap-3 text-sm">
                <span className="w-28 flex-shrink-0 text-primary truncate">
                  <span className="inline-block w-1.5 h-1.5 rounded-full mr-2 align-middle"
                        style={{ background: color }} />
                  {engineLabel(engine)}
                </span>
                <div className="flex-1"><BarRow value={rate} max={maxRate} color={color} /></div>
                <span className="w-14 text-right font-semibold tabular-nums" style={{ color }}>{rate}%</span>
              </div>
            );
          })}
        </div>
      </div>

      {failed.length > 0 && (
        <div className="rounded-lg px-3 py-2 text-sm"
             style={{ background: 'var(--warn-bg)', color: 'var(--warn-fg)' }}>
          <div className="font-semibold mb-0.5">
            {t('diagnosis.report.sections.visibility.failedEngines', {
              defaultValue: '本次未取得数据的平台',
            })}
          </div>
          {failed.map((f) => (
            <div key={f.engine} className="opacity-90">
              {engineLabel(f.engine)}
              {' — '}
              {f.reason === 'filtered'
                ? t('diagnosis.report.sections.visibility.failedFiltered', {
                    defaultValue: '本次未调度该平台',
                  })
                : t('diagnosis.report.sections.visibility.failedAllErrors', {
                    n: f.attempted ?? 0,
                    defaultValue: '{{n}} 条问题全部未返回',
                  })}
              {f.error && <span className="text-xs opacity-75"> （{f.error}）</span>}
            </div>
          ))}
        </div>
      )}

      {tops.length > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            {t('diagnosis.report.sections.visibility.topCompetitors')}
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {tops.slice(0, 8).map((c) => (
              <span key={c.domain} className="px-2 py-0.5 rounded-md text-xs border border-soft text-secondary">
                {c.domain} · {c.mentions}
              </span>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
