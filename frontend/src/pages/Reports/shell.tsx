// Standalone module shell for the report center — its own header and in-module nav, not reusing the brand growth page structure.
// Topic resolution logic reuses useShellState (a pure-logic hook, no UI), so brand growth pages are unaffected.

import { Link, NavLink } from 'react-router-dom';
import { PageHead } from '../../components/PageHead';
import { useShellState, type ShellState } from '../../hooks/useTopicScope';
import { useHintShift } from '../../hooks/useHintShift';
import { type BrandTier, type IndustryClass, type MetricKey } from '../../services/reportsApi';
import { useReportsLang } from './lang';
import { UiSelect } from '../../components/ui/Select';

export type { ShellState };

// ── Brand tags (benchmark) — read-only display. Tags are set in step one of "Topic settings", and the report center matches reference ranges by them ──
// The exclamation-mark hint lists target ranges for six metrics × three tiers (delivery metric definition display, read-only reference).

// Brand tier target ranges (display metric definition, consistent with the industry tier benchmarks): metric -> new brand / mid-sized brand / industry leader.
// All six metrics step up by tier; backend target thresholds are computed separately, this is for explanation only.
const TIER_BAND_ROWS: { key: MetricKey; new: string; mid: string; leader: string }[] = [
  { key: 'exposure', new: '5–12%',  mid: '18–30%', leader: '45–70%' },
  { key: 'top1',     new: '1–3%',   mid: '5–10%',  leader: '20–40%' },
  { key: 'top3',     new: '2–8%',   mid: '10–20%', leader: '30–55%' },
  { key: 'accuracy', new: '≥90%',   mid: '≥94%',   leader: '≥97%' },
  { key: 'citation', new: '5–12%',  mid: '15–30%', leader: '35–60%' },
  { key: 'mention',  new: '3–8%',   mid: '8–15%',  leader: '15–30%' },
];

// Brand tag i hint — target range table of three tiers × six metrics (expands on hover)
function BrandTagBandsHint({ activeTier }: { activeTier: BrandTier }) {
  const L = useReportsLang();
  const [shift, onShow] = useHintShift();
  const thBase = 'text-right font-medium py-1 px-1.5 tabular-nums';
  const colOn = (t: 'new' | 'mid' | 'leader') =>
    activeTier === t ? { color: 'var(--text-primary)', fontWeight: 700 } : undefined;
  return (
    <span className="relative inline-flex group" tabIndex={0} onMouseEnter={onShow} onFocus={onShow}>
      <span
        className="w-3.5 h-3.5 rounded-full text-[9px] font-serif italic font-semibold flex items-center justify-center cursor-help select-none"
        style={{ background: 'var(--bg-input)', color: 'var(--text-muted)', border: '1px solid var(--border-color)' }}
        aria-label={L.brandTagMetricsIntro}
      >
        i
      </span>
      <div
        className="hidden group-hover:block group-focus-within:block absolute left-1/2 top-full mt-1.5 z-50 w-[340px] p-3 rounded shadow-lg"
        style={{
          background: 'var(--bg-card)', border: '1px solid var(--border-color)',
          color: 'var(--text-secondary)', transform: `translateX(calc(-50% + ${shift}px))`,
        }}
      >
        <div className="text-[11px] mb-2">{L.brandTagMetricsIntro}</div>
        <table className="w-full text-[11px] border-collapse">
          <thead>
            <tr className="text-muted">
              <th className="text-left font-medium py-1 px-1.5">{L.brandTagMetricHeader}</th>
              <th className={thBase} style={colOn('new')}>{L.tierLabels.new}</th>
              <th className={thBase} style={colOn('mid')}>{L.tierLabels.mid}</th>
              <th className={thBase} style={colOn('leader')}>{L.tierLabels.leader}</th>
            </tr>
          </thead>
          <tbody>
            {TIER_BAND_ROWS.map(r => (
              <tr key={r.key} style={{ borderTop: '1px solid var(--border-color)' }}>
                <td className="text-left py-1 px-1.5" style={{ color: 'var(--text-primary)' }}>{L.metricLabels[r.key]}</td>
                <td className={thBase} style={colOn('new')}>{r.new}</td>
                <td className={thBase} style={colOn('mid')}>{r.mid}</td>
                <td className={thBase} style={colOn('leader')}>{r.leader}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="text-[11px] mt-2 text-muted">{L.brandTagMetricsOutro}</div>
      </div>
    </span>
  );
}

function BrandTagDisplay({ state }: { state: ShellState }) {
  const L = useReportsLang();
  if (!state.topic) return null;
  const tier = (state.topic.brand_tier || '') as BrandTier;
  const iclass = (state.topic.industry_class || '') as IndustryClass;

  const tierText = tier ? L.tierLabels[tier as 'new' | 'mid' | 'leader'] : '';
  const iclassText = iclass ? L.industryClassLabels[iclass as 'regular' | 'special'] : '';
  const label = [tierText, iclassText].filter(Boolean).join(' · ') || L.brandTagUnset;

  return (
    <span className="text-xs text-secondary flex items-center gap-1.5">
      <span className="text-muted">{L.brandTagLabel}</span>
      <span className="px-2 py-1 rounded font-medium leading-none"
        style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}>
        {label}
      </span>
      <BrandTagBandsHint activeTier={tier} />
    </span>
  );
}

function ModuleNav({ state }: { state: ShellState }) {
  const L = useReportsLang();
  const items = [
    { to: '/reports', end: true, label: L.navOverview },
    { to: '/reports/daily', end: false, label: L.tabDaily },
    { to: '/reports/weekly', end: false, label: L.tabWeekly },
    { to: '/reports/monthly', end: false, label: L.tabMonthly },
    // The metrics board has been merged into the report overview (rank distribution + source analysis) and its tab retired; /reports/metrics redirects to the overview
    { to: '/reports/compliance', end: false, label: L.navCompliance },
  ];
  const q = state.topicId ? `?topic=${state.topicId}` : '';
  // Direction A: underline tabs (active = 2px accent bottom border + bold), replacing pills
  return (
    <nav className="flex items-center gap-0.5">
      {items.map(it => (
        <NavLink key={it.to} to={`${it.to}${q}`} end={it.end}
          className="px-3.5 pt-1 pb-1.5 text-sm transition"
          style={({ isActive }: { isActive: boolean }) => ({
            borderBottom: `2px solid ${isActive ? 'var(--accent-primary)' : 'transparent'}`,
            color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
            fontWeight: isActive ? 700 : 450,
          })}>
          {it.label}
        </NavLink>
      ))}
    </nav>
  );
}

function TopicPicker({ state }: { state: ShellState }) {
  const L = useReportsLang();
  if (state.topics.length === 0) return null;
  return (
    <label className="text-xs text-secondary flex items-center gap-2">
      {L.topicLabel}
      <UiSelect
        value={String(state.topicId ?? '')}
        onChange={val => state.setTopicId(Number(val))}
        className="px-2 py-1 text-sm rounded"
        style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
        options={state.topics.map(t => ({ value: String(t.id), label: t.name }))}
      />
    </label>
  );
}

export function ReportsShell({ title, backTo, right, navRight, children }: {
  title?: string;                       // Detail page subtitle (not passed for top-level pages like the overview)
  backTo?: string;                      // Detail page back target (e.g. /reports)
  right?: React.ReactNode;              // Page-level action area (export button etc.)
  navRight?: (state: ShellState) => React.ReactNode;  // Action area on the right of the nav row (changes with topic)
  children: (state: ShellState) => React.ReactNode;
}) {
  const state = useShellState();
  const L = useReportsLang();
  const q = state.topicId ? `?topic=${state.topicId}` : '';

  return (
    <div className="reports-shell min-h-[calc(100vh-4rem)]" style={{ background: 'var(--bg-primary)' }}>
      <PageHead titleKey="pageMeta.dashboard.title" descriptionKey="pageMeta.dashboard.description" />

      <div className="px-4 sm:px-6 lg:px-8 pt-3.5 pb-2.5 border-b backdrop-blur sticky top-16 z-30"
        style={{ borderColor: 'var(--border-color)', background: 'color-mix(in srgb, var(--bg-card) 94%, transparent)' }}>
        <div className="max-w-[1400px] mx-auto grid gap-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <span className="w-[30px] h-[30px] rounded-[7px] flex items-center justify-center flex-shrink-0"
                style={{
                  background: 'var(--accent-primary)',
                  color: 'var(--accent-contrast)',
                }}>
                <svg className="w-[17px] h-[17px]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
                    d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </span>
              <div className="flex items-baseline gap-2 min-w-0">
                <h1 className="text-[15.5px] font-bold text-primary whitespace-nowrap">{L.moduleTitle}</h1>
                {title && (
                  <>
                    <span className="text-muted">/</span>
                    <span className="text-sm text-secondary truncate">{title}</span>
                  </>
                )}
                {state.topic && (
                  <span className="text-xs text-muted truncate">· {state.topic.name}</span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              {backTo ? (
                <Link to={`${backTo}${q}`}
                  className="px-3 py-1 text-xs rounded flex items-center gap-1"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}>
                  ‹ {L.backToList}
                </Link>
              ) : null}
              <TopicPicker state={state} />
              <BrandTagDisplay state={state} />
              {right}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <ModuleNav state={state} />
            {navRight?.(state)}
          </div>
        </div>
      </div>

      <main className="px-4 sm:px-6 lg:px-8 py-6">
        <div className="max-w-[1400px] mx-auto">
          {state.loading ? (
            <div className="text-center py-20 text-muted">{L.loading}</div>
          ) : state.topics.length === 0 ? (
            <div className="max-w-md mx-auto py-20 text-center">
              <div className="text-lg font-medium text-primary mb-2">{L.noTopicTitle}</div>
              <div className="text-sm text-muted">{L.noTopicHint}</div>
            </div>
          ) : (
            children(state)
          )}
        </div>
      </main>
    </div>
  );
}
