// Chapter 10 Source analysis — who exactly the AI answers cite
//
// The data has always been collected (competitiveIntel.source_trace / source_preference), but before 2026-09-27
// no chapter rendered it -- the report never showed "which sites the AI learned these answers from",
// which is exactly the most actionable part of GEO: knowing who's cited tells you where content should go.
import { useTranslation } from 'react-i18next';
import { subData, StatBox, BarRow, SectionSub, ChapterDataStatus, NoDataCard } from '../reportKit';
import { useDgPalette } from '../palette';
import { engineLabel, sortEngines } from '../../../constants/engines';
import type { ChapterCtx } from './types';

type SourceRow = {
  platform: string; article_count: number; total_citations: number;
  engines?: string[]; queries?: string[]; source_type?: string;
  articles?: { url: string; title?: string; citations?: number }[];
};
type EnginePref = {
  engine: string; total_citations: number;
  type_distribution?: Record<string, number>;
  top_domains?: { domain: string; count: number }[];
};

export function Ch10Sources({ report, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const dg = useDgPalette();
  const comp = subData(sub, 'competitiveIntel');
  const trace = (comp?.source_trace || null) as Record<string, any> | null;
  const pref = (comp?.source_preference || null) as Record<string, any> | null;

  if (!trace && !pref) {
    return (
      <>
        <ChapterDataStatus steps={report.progress?.steps} modes={['competitiveIntel']} sub={sub} />
        <NoDataCard />
      </>
    );
  }

  const sources = ((trace?.sources || []) as SourceRow[])
    .slice().sort((a, b) => (b.total_citations || 0) - (a.total_citations || 0));
  const selfCites = (trace?.self_citations || []) as unknown[];
  const perEngine = ((pref?.per_engine || []) as EnginePref[])
    .slice().sort((a, b) => (b.total_citations || 0) - (a.total_citations || 0));
  const dist = (pref?.overall_distribution || {}) as Record<string, number>;
  const recs = (pref?.recommendations || []) as { engine?: string; type?: string; message: string }[];
  const maxCite = Math.max(...sources.map((s) => s.total_citations || 0), 1);
  const distRows = Object.entries(dist).sort((a, b) => b[1] - a[1]);
  const maxPct = Math.max(...distRows.map(([, v]) => v), 1);
  const T = (k: string, d: string) => t(`diagnosis.report.sections.sources.${k}`, { defaultValue: d });

  return (
    <>
      <ChapterDataStatus steps={report.progress?.steps} modes={['competitiveIntel']} sub={sub} />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatBox label={T('totalSources', '被引站点数')} value={trace?.total_sources ?? 0} accent />
        <StatBox label={T('totalCitations', '引用总次数')} value={trace?.total_citations ?? 0} />
        <StatBox label={T('selfCitations', '引用自有内容')} value={selfCites.length} />
        <StatBox label={T('engineCovered', '有引用的引擎')} value={perEngine.length} />
      </div>

      {sources.length > 0 && (
        <div>
          <SectionSub title={T('topPlatforms', '被引最多的站点')}
                      hint={T('topPlatformsHint', '按引用次数排序;「篇数」是同站被引到的不同文章数')} />
          <div className="space-y-2">
            {sources.slice(0, 10).map((s, i) => (
              <div key={s.platform} className="flex items-center gap-3 text-sm">
                <span className="w-44 flex-shrink-0 text-primary truncate" title={s.platform}>
                  <span className="inline-block w-1.5 h-1.5 rounded-full mr-2 align-middle"
                        style={{ background: dg.series(i) }} />
                  {s.platform}
                </span>
                <div className="flex-1">
                  <BarRow value={s.total_citations} max={maxCite} color={dg.series(i)} />
                </div>
                <span className="w-32 text-right text-xs text-secondary tabular-nums">
                  {s.total_citations} {T('cites', '次')} · {s.article_count} {T('articles', '篇')}
                </span>
                <span className="w-28 text-right text-xs text-muted truncate"
                      title={(s.engines || []).map(engineLabel).join('、')}>
                  {sortEngines(s.engines || []).map(engineLabel).join('·')}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {distRows.length > 0 && (
        <div>
          <SectionSub title={T('typeDist', '引用源类型分布')}
                      hint={T('typeDistHint', '这决定内容该往哪类渠道铺')} />
          <div className="space-y-2">
            {distRows.map(([k, v], i) => (
              <div key={k} className="flex items-center gap-3 text-sm">
                <span className="w-28 flex-shrink-0 text-primary">
                  {T(`type.${k}`, k)}
                </span>
                <div className="flex-1"><BarRow value={v} max={maxPct} color={dg.series(i)} /></div>
                <span className="w-14 text-right font-semibold tabular-nums">{v}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {perEngine.length > 0 && (
        <div>
          <SectionSub title={T('byEngine', '各引擎的引用偏好')} />
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="text-xs text-muted">
                  <th className="text-left font-normal py-1.5 pr-3">{T('colEngine', '引擎')}</th>
                  <th className="text-right font-normal py-1.5 pr-3">{T('colCites', '引用次数')}</th>
                  <th className="text-left font-normal py-1.5">{T('colTopDomains', '最常引用的站点')}</th>
                </tr>
              </thead>
              <tbody>
                {perEngine.map((p) => (
                  <tr key={p.engine} className="border-t border-soft align-top">
                    <td className="py-1.5 pr-3 text-primary whitespace-nowrap">{engineLabel(p.engine)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums font-semibold">{p.total_citations}</td>
                    <td className="py-1.5 text-secondary">
                      {(p.top_domains || []).slice(0, 5)
                        .map((d) => `${d.domain}(${d.count})`).join('、') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {recs.length > 0 && (
        <div>
          <SectionSub title={T('recs', '信源结论')} />
          <div className="space-y-1">
            {recs.map((r, i) => (
              <p key={i} className="text-sm text-secondary m-0">
                {r.engine && r.engine !== '全部' && (
                  <span className="font-semibold text-primary">{engineLabel(r.engine)}:</span>
                )}{' '}{r.message}
              </p>
            ))}
          </div>
        </div>
      )}

      {selfCites.length === 0 && (trace?.total_citations ?? 0) > 0 && (
        <p className="text-xs text-muted m-0">
          {T('noSelfCite', '本次 AI 回答中没有引用到任何自有内容 —— 站外内容资产是当前最大的缺口。')}
        </p>
      )}
    </>
  );
}
