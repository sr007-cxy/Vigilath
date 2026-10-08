// Rank distribution + source analysis — metrics board content merged into the overview (rendering logic taken from MetricsBoard.tsx).
// During the transition it hangs at the tail of CompetitiveFull (Overview.tsx is occupied by a parallel feature; after merging, move the mount point and retire the metrics board tab).

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts';
import {
  reportsApi, type CitationSources, type PositionDistribution,
} from '../../services/reportsApi';
import { factorColor, factorLabel } from '../../constants/factors';
import { useReportsLang } from './lang';
import { SectionCard } from './components';
import { platformOf } from './platforms';

// Ranking bar color: data blue (in line with the page-wide data blue)
const RANK_BAR = 'linear-gradient(90deg, #93b4f8, #2563eb)';

// Fixed colors for the four rank tiers (2026-07 option A tweak: not exposed changed from light red to light gray hatching -- it reads as "a gap" rather than "an alarm",
// distinguished by texture from the solid gray of ranks 4-10); count text on light segments uses a dark color for contrast.
// exposed = the "exposed" segment of the three-tier mode for brand/competitor comparison queries (hit but not #1), a lighter green to distinguish from TOP2-3
const DIST_COLORS = {
  top1: '#15803d', top2_3: '#4ade80', r4_10: '#94a3b8', exposed: '#86efac',
  miss: 'repeating-linear-gradient(45deg, #e3e8f2 0 4px, #f2f5fa 4px 8px)',
};
const DIST_TEXT = {
  top1: '#ffffff', top2_3: '#14532d', r4_10: '#1e293b', exposed: '#14532d', miss: '#68738a',
};

export function BoardSections({ topicId, token }: { topicId: number; token: string }) {
  const L = useReportsLang();
  const { i18n } = useTranslation();
  const zhLocale = !(i18n.language || 'zh').toLowerCase().startsWith('en');
  const [dist, setDist] = useState<PositionDistribution | null>(null);
  const [sources, setSources] = useState<CitationSources | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!topicId) return;
    Promise.all([
      reportsApi.positionDistribution(topicId, token),
  // The right side aggregates by platform; fetch more URL rows so TOP10 platform counts are more accurate
      reportsApi.citationSources(topicId, token, 200),
    ]).then(([d, c]) => { setDist(d); setSources(c); })
      .catch(() => { setDist(null); setSources(null); });
  }, [topicId, token]);

  return (
    <>
      <SectionCard title={L.secPositionDist} hint={L.hintPositionDist}
        right={
          <div className="flex items-center gap-3 text-[10px] text-muted">
            {([['top1', L.distTop1], ['top2_3', L.distTop23], ['r4_10', L.dist410],
               ['exposed', L.distExposed], ['miss', L.distMiss]] as const)
              .map(([k, label]) => (
                <span key={k} className="flex items-center gap-1">
                  <span className="w-2.5 h-2.5 rounded-sm inline-block"
                    style={{ background: DIST_COLORS[k] }} />
                  {label}
                </span>
              ))}
          </div>
        }>
        {!dist || dist.rows.length === 0 ? (
          <div className="text-xs text-muted py-4 text-center">{L.empty}</div>
        ) : (
          <div className="space-y-3">
            {dist.rows.map(r => {
              // Brand/competitor comparison queries show three tiers (a hit means exposed, rank is meaningless); other query types show four
              const exposureMode = r.bucket_mode === 'exposure';
              const exposed = r.exposed ?? 0;
              const total = (exposureMode
                ? r.top1 + exposed + r.miss
                : r.top1 + r.top2_3 + r.r4_10 + r.miss) || 1;
              const segs = exposureMode
                ? [
                  { k: 'top1' as const, v: r.top1 },
                  { k: 'exposed' as const, v: exposed },
                  { k: 'miss' as const, v: r.miss },
                ]
                : [
                  { k: 'top1' as const, v: r.top1 },
                  { k: 'top2_3' as const, v: r.top2_3 },
                  { k: 'r4_10' as const, v: r.r4_10 },
                  { k: 'miss' as const, v: r.miss },
                ];
              const open = expanded.has(r.scene_type);
              return (
                <div key={r.scene_type}>
                  <div className="flex items-center gap-3 text-xs cursor-pointer select-none"
                    onClick={() => setExpanded(prev => {
                      const next = new Set(prev);
                      if (next.has(r.scene_type)) next.delete(r.scene_type);
                      else next.add(r.scene_type);
                      return next;
                    })}>
                    <span className="w-24 text-secondary">
                      <span className="text-muted mr-1 text-[9px] inline-block">{open ? '▼' : '▶'}</span>
                      {L.sceneLabels[r.scene_type] || r.scene_type}
                      <span className="text-muted block text-[10px] pl-3.5">{L.queryCount(r.query_count)}</span>
                    </span>
                    <div className="flex-1 flex h-5 rounded overflow-hidden"
                      style={{ background: 'var(--bg-input)' }}>
                      {segs.map(s => s.v > 0 && (
                        <div key={s.k} title={`${s.v}`}
                          className="h-full flex items-center justify-center text-[9px] tabular-nums"
                          style={{ width: `${(s.v / total) * 100}%`, background: DIST_COLORS[s.k], color: DIST_TEXT[s.k] }}>
                          {(s.v / total) >= 0.08 ? s.v : ''}
                        </div>
                      ))}
                    </div>
                  </div>
                  {open && (
                    <div className="mt-2 ml-24 pl-3 py-2 rounded-lg space-y-1.5"
                      style={{ background: 'var(--bg-input)' }}>
                      {(exposureMode
                        ? [['top1', L.distTop1, r.top1_queries],
                           ['exposed', L.distExposed, r.exposed_queries ?? []],
                           ['miss', L.distMiss, r.miss_queries]] as const
                        : [['top1', L.distTop1, r.top1_queries], ['top2_3', L.distTop23, r.top2_3_queries],
                           ['r4_10', L.dist410, r.r4_10_queries], ['miss', L.distMiss, r.miss_queries]] as const)
                        .map(([k, label, qs]) => qs.length > 0 && (
                          <div key={k} className="text-[11px]">
                            <span className="inline-block w-2 h-2 rounded-sm mr-1.5 align-middle"
                              style={{ background: DIST_COLORS[k] }} />
                            <span className="text-secondary mr-2">{label}</span>
                            {qs.map(q => (
                              <span key={q.query}
                                className="inline-block mr-1.5 mb-1 px-1.5 py-0.5 rounded text-[10px]"
                                style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                                  color: 'var(--text-secondary)' }}>
                                {q.query}
                                <span className="text-muted"> ×{q.count}</span>
                              </span>
                            ))}
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      <SectionCard title={L.secCitationSources} hint={L.hintCitationSources}
        right={sources ? (
          <span className="text-[10px] text-muted">{L.totalCitations(sources.total_citations)}</span>
        ) : undefined}>
        {!sources || sources.top_sources.length === 0 ? (
          <div className="text-xs text-muted py-4 text-center">{L.empty}</div>
        ) : (() => {
          // Right: URL rows aggregated by platform (domains normalized to platforms), showing only the 10 most-cited platforms, without exposing specific links
          const platMap = new Map<string, { zh: string; en: string; logo: string; count: number }>();
          for (const s of sources.top_sources) {
            const host = s.domain || s.url.replace(/^https?:\/\//i, '').split('/')[0];
            if (!host) continue;
            const p = platformOf(host);
            const cur = platMap.get(p.key) ?? { zh: p.zh, en: p.en, logo: p.logo, count: 0 };
            cur.count += s.count;
            platMap.set(p.key, cur);
          }
          const platforms = [...platMap.values()].sort((a, b) => b.count - a.count).slice(0, 10);
          const maxCount = Math.max(...platforms.map(p => p.count), 1);
          return (
            <div className="grid gap-5 lg:grid-cols-[250px_1fr]">
              {/* Left: ring -- type composition, center = total citations */}
              <div>
                <div style={{ position: 'relative', height: 205 }}>
                  <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                    <PieChart>
                      <Pie data={sources.type_share.map(t => ({ name: factorLabel(t.source_type), value: t.count }))}
                        dataKey="value" cx="50%" cy="50%" innerRadius={67} outerRadius={92}
                        startAngle={90} endAngle={-270} stroke="var(--bg-card)" strokeWidth={2}
                        isAnimationActive={false}>
                        {sources.type_share.map(t => (
                          <Cell key={t.source_type} fill={factorColor(t.source_type)} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <div style={{
                    position: 'absolute', inset: 0, display: 'grid', placeContent: 'center',
                    textAlign: 'center', pointerEvents: 'none',
                  }}>
                    <div className="rpt-num text-[23px] font-bold text-primary leading-none">
                      {sources.total_citations}
                    </div>
                    <div className="text-[10px] text-muted mt-1">{L.totalCitationsShort}</div>
                  </div>
                </div>
                <div className="text-[11px] text-secondary" style={{ textAlign: 'center', lineHeight: 1.9 }}>
                  {sources.type_share.map(t => (
                    <span key={t.source_type} className="whitespace-nowrap"
                      style={{ display: 'inline-block', margin: '0 5px' }}>
                      <span style={{
                        display: 'inline-block', width: 9, height: 9, borderRadius: 2,
                        background: factorColor(t.source_type), marginRight: 4, verticalAlign: -1,
                      }} />
                      {factorLabel(t.source_type)} {t.pct}%
                    </span>
                  ))}
                </div>
              </div>

              {/* Right: TOP10 platform ranking -- aggregated by platform, no specific links, not clickable; logos use platform favicons,
                  falling back to a first-letter badge on load failure. Height aligned with the left ring column, scrolls on overflow */}
              <div>
                <div style={{ maxHeight: 248, overflowY: 'auto' }}>
                  {platforms.map((p, i) => {
                    const name = zhLocale ? p.zh : p.en;
                    return (
                      <div key={p.zh} className="grid items-center gap-2 py-[4px] text-xs"
                        style={{
                          gridTemplateColumns: '18px 20px minmax(0, 0.9fr) minmax(0, 1.4fr) 40px',
                          borderTop: i ? '1px dashed var(--rpt-hairline)' : undefined,
                        }}>
                        <span className="text-center rpt-num text-[10px] font-semibold"
                          style={{ color: i < 3 ? 'var(--data-accent)' : 'var(--text-muted)' }}>
                          {i + 1}
                        </span>
                        <span className="relative inline-flex w-[18px] h-[18px] rounded items-center justify-center text-[9px] font-bold overflow-hidden"
                          style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                          {name.charAt(0).toUpperCase()}
                          <img src={p.logo} alt="" width={18} height={18} loading="lazy"
                            className="absolute inset-0 w-full h-full rounded"
                            onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
                        </span>
                        <span className="min-w-0 flex items-center gap-1">
                          <span className="truncate font-semibold text-[11.5px] text-primary">{name}</span>
                        </span>
                        <span className="block h-[9px] rounded-full overflow-hidden"
                          style={{ background: 'var(--bg-input)' }}>
                          <span className="block h-full rounded-full"
                            style={{
                              width: `${Math.max(6, Math.round((p.count / maxCount) * 100))}%`,
                              background: RANK_BAR,
                            }} />
                        </span>
                        <span className="text-right rpt-num font-bold text-primary text-[11.5px]">{p.count}</span>
                      </div>
                    );
                  })}
                </div>
                <div className="text-[10px] text-muted mt-1.5 pt-1.5"
                  style={{ borderTop: '1px solid var(--rpt-hairline)' }}>
                  {L.srcBarLegend}
                </div>
              </div>
            </div>
          );
        })()}
      </SectionCard>
    </>
  );
}
