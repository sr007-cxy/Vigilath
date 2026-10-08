// Chapter 09 Competitor analysis — mixed mention-rate leaderboard + advantage logic comparison cards
import { useTranslation } from 'react-i18next';
import { subData, BarRow, Prose, ChapterDataStatus } from '../reportKit';
import type { ChapterCtx } from './types';

const THREAT_COLOR = {
  high: 'var(--danger-fg)', mid: 'var(--warn-fg)', low: 'var(--success-fg)',
} as const;

export function Ch09Competitive({ report, n, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const comp = subData(sub, 'competitiveIntel');
  const vis = subData(sub, 'visibility');

  const insight = comp?.competitor_insight as Record<string, any> | undefined;
  const competitors = (insight?.competitors || []) as {
    name: string; rate: number; mention_count: number; engines?: string[];
    user_specified?: boolean;
  }[];
  const yourRate = Number(insight?.your_rate ?? 0);

  // Degradation: when competitive intel has no data, do a lightweight comparison using competitor domains auto-detected in visibility
  const fallbackTops = !comp && vis
    ? ((vis.top_competitors || []) as { domain: string; mentions: number }[])
    : [];

  if (!comp && fallbackTops.length === 0) {
    return (
      <>
        <ChapterDataStatus steps={report.progress?.steps} modes={['competitiveIntel']} sub={sub} />
        <p className="text-sm text-secondary m-0">
          {t('diagnosis.report.sections.competitive.insufficient')}
        </p>
      </>
    );
  }

  // Tiers relative to own mention rate: ≥100% high threat, ≥50% medium threat; when own rate is 0 use absolute tiers
  const threatOf = (rate: number): 'high' | 'mid' | 'low' => {
    if (yourRate <= 0) return rate >= 30 ? 'high' : rate >= 10 ? 'mid' : 'low';
    if (rate >= yourRate) return 'high';
    if (rate >= yourRate * 0.5) return 'mid';
    return 'low';
  };

  const brandName = (comp?.brand as string) || t('diagnosis.report.sections.competitive.yourBrand');
  // Competitors named by the user in the form must keep a slot -- their mention rate is often 0 (the rivals the client cares about don't appear
  // in AI answers at all), and truncating by mention rate would cut that conclusion, leaving only a bunch of unfamiliar names
  const namedComps = competitors.filter((c) => c.user_specified);
  const autoComps = competitors.filter((c) => !c.user_specified);
  // Keep at least 5 slots for auto-detected ones: with 6 named competitors, counting "8 rows total" would leave just 1 slot,
  // and the companies AI actually recommends would vanish from the board -- both the competitive landscape and the benchmark list must be shown.
  const picked = [...namedComps, ...autoComps.slice(0, Math.max(5, 7 - namedComps.length))];
  const rows = comp
    ? [
        { name: brandName, rate: yourRate, self: true, named: false },
        ...picked.map((c) => ({
          name: c.name, rate: c.rate || 0, self: false, named: !!c.user_specified,
        })),
      ].sort((a, b) => b.rate - a.rate)
    : [];
  const maxRate = Math.max(...rows.map((r) => r.rate), 1);

  return (
    <>
      <ChapterDataStatus steps={report.progress?.steps} modes={['competitiveIntel']} sub={sub} />

      {n.competitive_summary && <Prose tinted>{n.competitive_summary}</Prose>}

      {/* Metric definition note: without it, "this brand 21.4% vs competitor 7.1%" would be read as leading */}
      {rows.length > 0 && insight?.rate_scope && (
        <p className="text-xs text-secondary m-0">
          {insight.rate_scope === 'category'
            ? t('diagnosis.report.sections.competitive.scopeNote', {
                valid: insight.rate_scope_valid, total: insight.rate_scope_total,
              })
            : insight.rate_scope === 'legacy'
              ? t('diagnosis.report.sections.competitive.scopeNoteLegacy')
              : t('diagnosis.report.sections.competitive.scopeNoteAll', {
                  total: insight.rate_scope_total,
                })}
        </p>
      )}

      {rows.length > 0 && (
        <div className="space-y-2">
          {rows.map((r, i) => {
            const th = threatOf(r.rate);
            return (
              <div key={`${r.name}-${i}`}
                   className="flex items-center gap-3 text-sm px-2 py-1.5 rounded-lg"
                   style={r.self ? { background: 'rgba(99,102,241,0.08)' } : undefined}>
                <span className="w-6 flex-shrink-0 text-center text-xs font-bold tabular-nums"
                      style={{ color: r.self ? 'var(--data-accent)' : 'var(--text-muted)' }}>
                  {i + 1}
                </span>
                <span className={`w-32 flex-shrink-0 truncate ${r.self ? 'font-semibold' : 'text-primary'}`}
                      style={r.self ? { color: 'var(--data-accent)' } : undefined}>
                  {r.name}
                  {r.self && (
                    <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded align-middle"
                          style={{ background: 'rgba(99,102,241,0.15)', color: 'var(--data-accent)' }}>
                      {t('diagnosis.report.sections.competitive.yourBrand')}
                    </span>
                  )}
                  {r.named && (
                    <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded align-middle"
                          style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
                      {t('diagnosis.report.sections.competitive.yourPick')}
                    </span>
                  )}
                </span>
                {/* This brand uses the primary color, competitors are colored by threat level -- see at a glance who's ahead of you */}
                <div className="flex-1">
                  <BarRow value={r.rate} max={maxRate}
                          color={r.self ? 'var(--dg-1)' : THREAT_COLOR[th]} />
                </div>
                <span className={`w-14 text-right tabular-nums ${r.self ? 'font-bold' : 'text-primary'}`}
                      style={r.self ? { color: 'var(--data-accent)' } : undefined}>{r.rate}%</span>
                <span className="w-16 text-right text-xs font-semibold"
                      style={{ color: r.self ? 'transparent' : THREAT_COLOR[th] }}>
                  {r.self ? '—' : t(`diagnosis.report.sections.competitive.threat.${th}`)}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {!comp && fallbackTops.length > 0 && (
        <div>
          <p className="text-sm text-secondary mb-2 m-0">
            {t('diagnosis.report.sections.competitive.autoDetected')}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {fallbackTops.slice(0, 8).map((c) => (
              <span key={c.domain} className="px-2 py-0.5 rounded-md text-xs border border-soft text-secondary">
                {c.domain} · {c.mentions}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Advantage logic comparison */}
      {(n.brand_strengths?.length || n.competitor_profiles?.length) ? (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            {t('diagnosis.report.sections.competitive.advantageLogic')}
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {(n.brand_strengths?.length || n.brand_weaknesses?.length) ? (
              <div className="p-4 rounded-xl"
                   style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.35)' }}>
                <div className="flex items-baseline justify-between mb-2">
                  <span className="text-sm font-bold" style={{ color: 'var(--data-accent)' }}>
                    {brandName}
                  </span>
                  <span className="text-sm font-bold text-primary tabular-nums">{yourRate}%</span>
                </div>
                {(n.brand_strengths || []).map((s, i) => (
                  <p key={`s${i}`} className="text-sm m-0 leading-relaxed" style={{ color: 'var(--success-fg)' }}>▲ {s}</p>
                ))}
                {(n.brand_weaknesses || []).map((w, i) => (
                  <p key={`w${i}`} className="text-sm m-0 leading-relaxed" style={{ color: 'var(--danger-fg)' }}>▼ {w}</p>
                ))}
              </div>
            ) : null}
            {(n.competitor_profiles || []).map((p) => {
              const match = competitors.find((c) => c.name === p.name);
              const gap = match ? Math.round((match.rate - yourRate) * 10) / 10 : null;
              return (
                <div key={p.name} className="p-4 rounded-xl border border-soft">
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="text-sm font-bold text-primary">{p.name}</span>
                    {match && <span className="text-sm text-primary tabular-nums">{match.rate}%</span>}
                  </div>
                  {(p.strengths || []).map((s, i) => (
                    <p key={`s${i}`} className="text-sm m-0 leading-relaxed" style={{ color: 'var(--success-fg)' }}>▲ {s}</p>
                  ))}
                  {(p.weaknesses || []).map((w, i) => (
                    <p key={`w${i}`} className="text-sm m-0 leading-relaxed" style={{ color: 'var(--danger-fg)' }}>▼ {w}</p>
                  ))}
                  {gap != null && (
                    <p className="text-xs mt-2 mb-0 font-semibold"
                       style={{ color: gap > 0 ? 'var(--danger-fg)' : 'var(--success-fg)' }}>
                      {gap > 0
                        ? t('diagnosis.report.sections.competitive.aheadBy', { gap: Math.abs(gap) })
                        : t('diagnosis.report.sections.competitive.behindBy', { gap: Math.abs(gap) })}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </>
  );
}
