// Industry library · industry detail page (public, no login) — industry report framing, no "selected brand" perspective:
//   three industry overview cards (share of voice/TOP1 brand/concentration trend) / leading brand comparison (vs. industry average) / market competition insights
//   / citation source analysis (source type share + TOP sources) / keyword heat map (table + bubble cloud) / per-engine brand leaderboard.
// Data from /api/industry/{key}/data (category query metric definition, brand list extracted from responses), accessible anonymously.
// Action buttons = acquisition CTA: logged out opens sign-up, logged in goes to the report center.

import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Area, AreaChart, Bar, BarChart, Line, LineChart, ResponsiveContainer,
} from 'recharts';
import { PageHead } from '../../components/PageHead';
import { useAuth } from '../../contexts/AuthContext';
import { useAuthModal } from '../../contexts/AuthModalContext';
import cloud, { type CloudWord } from 'd3-cloud';
import { industryApi, type IndustryData, type IndustryHeatWord, type IndustryVisMetric } from '../../services/industryApi';
import type { CompetitiveAnalysis } from '../../services/reportsApi';
import { engineLabel } from '../../constants/engines';
import { factorColor, factorLabel } from '../../constants/factors';
import { platformOf } from '../Reports/platforms';
import { useIndustryLang, type IndustryLang } from './lang';

// ── Visual constants (light palette mimicking the reference design; the whole page is fixed light, text fixed dark ink) ──
const INK = '#0f172a';
const INK_SUB = '#64748b';
const CARD_GRADS = [
  'linear-gradient(135deg,#e6fbf4 0%,#f2fdfa 55%,#ffffff 100%)',   // Industry share of voice - teal
  'linear-gradient(135deg,#e8efff 0%,#f3f6ff 55%,#ffffff 100%)',   // TOP1 brand - blue
  'linear-gradient(135deg,#f3ecff 0%,#faf6ff 55%,#ffffff 100%)',   // Brand concentration - purple
];
const CARD_ACCENTS = ['#10b981', '#3b82f6', '#8b5cf6'];
// Engine bar colors (Wenxin blue / Tongyi teal / Yuanbao orange / Doubao purple / DeepSeek gray)
const ENGINE_COLORS: Record<string, string> = {
  wenxin: '#2563eb', qwen: '#14b8a6', yuanbao: '#f59e0b',
  doubao: '#8b5cf6', deepseek: '#94a3b8',
};
// Word cloud palette (final version tuned in the playwright visual lab cloud_lab):
// lead words converge on a blue -> indigo -> purple -> teal family, the visually top word alone gets an amber accent, the long tail recedes in gray-blue
const CLOUD_ACCENT = '#d97706';
const CLOUD_CORE = ['#1d4ed8', '#4f46e5', '#7c3aed', '#0e7490', '#2563eb', '#6d28d9', '#0891b2'];
const CLOUD_TAIL = '#94a3b8';

function medal(i: number) {
  return i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : null;
}

function fmtDelta(v: number | null, unit: string): { text: string; up: boolean } | null {
  if (v === null) return null;
  if (v === 0) return { text: `0${unit}`, up: true };
  return { text: `${v > 0 ? '+' : ''}${Math.round(v * 100) / 100}${unit}`, up: v > 0 };
}

// ── Industry overview metric card: big number + mini trend + period-over-period badge ──
function VisCard({ idx, title, sub, big, metric, deltaUnit, chart }: {
  idx: number; title: string; sub: string; big: string;
  metric: IndustryVisMetric; deltaUnit: string;
  chart: 'area' | 'line' | 'bar';
}) {
  const accent = CARD_ACCENTS[idx];
  const keys = metric.series_keys;
  const data = metric.series.map((v, i) => ({ k: keys[i] ?? '', v }));
  const delta = fmtDelta(metric.delta, deltaUnit);
  return (
    <div className="rounded-2xl p-5 shadow-sm" style={{ background: CARD_GRADS[idx], border: '1px solid rgba(15,23,42,0.06)' }}>
      <div className="text-[13px] font-semibold" style={{ color: INK }}>{title}</div>
      <div className="mt-2 text-[30px] font-bold leading-none tabular-nums" style={{ color: INK }}>{big}</div>
      <div className="mt-1 text-[11px]" style={{ color: INK_SUB }}>{sub}</div>
      <div className="mt-3" style={{ height: 84 }}>
        <ResponsiveContainer width="100%" height="100%">
          {chart === 'area' ? (
            <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={`vis-grad-${idx}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={accent} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={accent} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <Area dataKey="v" stroke={accent} strokeWidth={2} fill={`url(#vis-grad-${idx})`} isAnimationActive={false} />
            </AreaChart>
          ) : chart === 'line' ? (
            <LineChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
              <Line dataKey="v" stroke={accent} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap="35%">
              <Bar dataKey="v" fill={accent} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex items-center justify-between text-[10px]" style={{ color: INK_SUB }}>
        <span>{keys[0] ?? ''}</span>
        <span>{keys[keys.length - 1] ?? ''}</span>
      </div>
      {delta && (
        <div className="mt-2 text-[12px] font-semibold tabular-nums" style={{ color: delta.up ? '#059669' : '#dc2626' }}>
          {delta.text}
        </div>
      )}
    </div>
  );
}

// ── Leading brand comparison: metric rows (diff>0 = above industry average, green ▲) ──
function ContrastRow({ label, value, diff }: { label: string; value: string; diff?: number }) {
  return (
    <div className="flex items-center justify-between rounded-lg px-3 py-2" style={{ background: 'rgba(15,23,42,0.03)' }}>
      <span className="text-[12px]" style={{ color: INK_SUB }}>{label}</span>
      <span className="text-[13px] font-bold tabular-nums" style={{ color: INK }}>
        {value}
        {diff !== undefined && diff !== 0 && (
          <span className="ml-1 text-[11px]" style={{ color: diff > 0 ? '#059669' : '#dc2626' }}>
            {diff > 0 ? '▲' : '▼'}
          </span>
        )}
      </span>
    </div>
  );
}

// Source logo: if the favicon fails to load, degrade to a colored first-letter circle (external favicons on public pages have a limited success rate, so a fallback is required)
const AVATAR_COLORS = ['#3b82f6', '#8b5cf6', '#14b8a6', '#f59e0b', '#ec4899', '#6366f1', '#0891b2', '#d97706'];

function SourceLogo({ domain, name }: { domain: string; name: string }) {
  const [broken, setBroken] = useState(false);
  const ch = (name || domain).trim().charAt(0).toUpperCase();
  let hash = 0;
  for (const c of domain) hash = (hash * 31 + c.charCodeAt(0)) % 997;
  if (broken || !/^[a-z0-9.-]+$/i.test(domain)) {
    return (
      <span className="inline-flex items-center justify-center rounded-md shrink-0 text-white font-bold"
        style={{ width: 22, height: 22, fontSize: 11, background: AVATAR_COLORS[hash % AVATAR_COLORS.length] }}>
        {ch}
      </span>
    );
  }
  return (
    <img src={`https://${domain}/favicon.ico`} alt="" width={22} height={22}
      className="rounded-md shrink-0" style={{ objectFit: 'contain' }}
      onError={() => setBroken(true)} />
  );
}

// ── Citation source analysis: left source type share / right TOP cited sources ──
function SourcesSection({ d, L }: { d: IndustryData; L: IndustryLang }) {
  const s = d.sources;
  if (s.total_citations === 0) return null;
  const maxPct = Math.max(...s.type_share.map(t => t.pct), 1);
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(240px, 340px) 1fr' }}>
      {/* Left: type share */}
      <div className="rounded-xl p-4" style={{ border: '1px solid rgba(15,23,42,0.07)' }}>
        <div className="flex items-center justify-between mb-3">
          <span className="text-[12px] font-bold" style={{ color: INK }}>{L.sourceTypeShare}</span>
          <span className="text-[10.5px] tabular-nums" style={{ color: INK_SUB }}>{L.totalCitations(s.total_citations)}</span>
        </div>
        <div className="grid gap-2.5">
          {s.type_share.map(t => (
            <div key={t.source_type}>
              <div className="flex items-center justify-between text-[11.5px] mb-1">
                <span style={{ color: INK }}>{factorLabel(t.source_type)}</span>
                <span className="tabular-nums font-semibold" style={{ color: INK }}>{t.pct}%</span>
              </div>
              <div className="h-[7px] rounded-full overflow-hidden" style={{ background: 'rgba(15,23,42,0.06)' }}>
                <div className="h-full rounded-full" style={{ width: `${(t.pct / maxPct) * 100}%`, background: factorColor(t.source_type) }} />
              </div>
            </div>
          ))}
        </div>
      </div>
      {/* Right: TOP sources table */}
      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid rgba(15,23,42,0.07)' }}>
        <div className="grid px-4 py-2.5 text-[11px] font-semibold" style={{ gridTemplateColumns: '1fr 84px minmax(120px, 200px)', background: 'rgba(15,23,42,0.035)', color: INK_SUB }}>
          <span>{L.topSources}</span>
          <span className="text-right">{L.sourceCountCol}</span>
          <span className="text-right">{L.sourceEnginesCol}</span>
        </div>
        {s.top_sources.map((row, i) => {
          const p = platformOf(row.domain);
          return (
            <div key={row.domain} className="grid items-center px-4 py-2" style={{ gridTemplateColumns: '1fr 84px minmax(120px, 200px)', borderTop: i ? '1px solid rgba(15,23,42,0.05)' : undefined }}>
              <span className="flex items-center gap-2.5 min-w-0">
                <SourceLogo domain={row.domain} name={p.zh} />
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-semibold truncate" style={{ color: INK }}>{p.zh}</span>
                  <span className="block text-[10px] truncate" style={{ color: INK_SUB }}>{row.domain}</span>
                </span>
              </span>
              <span className="text-right text-[13px] font-bold tabular-nums" style={{ color: INK }}>{row.count}</span>
              <span className="text-right">
                {row.engines.map(e => (
                  <span key={e} className="inline-block ml-1 text-[10px] rounded px-1.5 py-0.5" style={{ background: 'rgba(15,23,42,0.045)', color: INK_SUB }}>
                    {engineLabel(e)}
                  </span>
                ))}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── d3-cloud word cloud: Archimedean spiral layout, font size scaled by heat; prompts colored, related search terms gray-blue ──
interface PlacedWord extends CloudWord {
  text: string;
  full: string;                                              // Full term text (for the hover tooltip)
  heat: number;
  source: string;
  idx: number;
}

// Canvas 900×460 + padding 3: measured (playwright parameter matrix) to fit all 22 long Chinese terms;
// 760×380 + padding 6 fits only 9 (padding inflates long words all around, at a huge cost)
// Aspect ratio matches the right card's actual inner area (height set by the 10-row table on the left), so the cloud fills it without a big blank bottom
const CLOUD_W = 900;
const CLOUD_H = 560;

// Word cloud display text: related terms have the industry-name prefix stripped (「商用清洁机器人十大品牌」 ("top 10 commercial cleaning robot brands") -> 「十大品牌」 ("top 10 brands")),
// since only short words look like a word cloud; question-style prompts are truncated to 12 characters. Hover/click use the full text.
function cloudText(query: string, industryName: string): string {
  let t = query;
  if (industryName && t.startsWith(industryName) && t.length - industryName.length >= 2) {
    t = t.slice(industryName.length).replace(/^[的·\s,,]+/, '');
  }
  return t.length > 12 ? `${t.slice(0, 11)}…` : t;
}

function WordCloud({ words, highlight, industryName, onPick }: {
  words: IndustryHeatWord[]; highlight: string; industryName: string;
  onPick: (word: string) => void;
}) {
  const [placed, setPlaced] = useState<PlacedWord[]>([]);
  useEffect(() => {
    const max = Math.max(...words.map(w => w.heat), 1);
    // Fixed random seed (LCG): stable layout for the same data, no jumping on refresh
    let seed = 42;
    const rand = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;
    const layout = cloud<PlacedWord>()
      .size([CLOUD_W, CLOUD_H])
      .words(words.reduce<PlacedWord[]>((acc, w, i) => {
        const text = cloudText(w.query, industryName);
        if (acc.some(x => x.text === text)) return acc;   // Dedup identical display text after truncation/prefix stripping
        // Font size = square-root heat scaling × long-word penalty: short words take the lead, long sentences recede into small background text -- key to the word cloud look;
        // the penalty also keeps long words from overflowing (overflowing words are silently dropped by d3-cloud)
        const byHeat = 15 + Math.sqrt(w.heat / max) * 36;
        const penalty = Math.max(0.36, Math.min(1, 7 / text.length));
        acc.push({ text, full: w.query, heat: w.heat, source: w.source, idx: i,
                   size: Math.max(13, byHeat * penalty) });
        return acc;
      // Place big words first for a higher placement success rate (d3-cloud places in array order, later collisions are dropped)
      }, []).sort((a, b) => ((b.size ?? 0) - (a.size ?? 0))))
      .padding(3)
      .rotate(0)                                             // Chinese is horizontal, no rotation
      .font('"PingFang SC","Microsoft YaHei",sans-serif')
      .fontSize(d => d.size ?? 13)
      .spiral('archimedean')
      .random(rand)
      .on('end', setPlaced);
    layout.start();
    return () => { layout.stop(); };
  }, [words, industryName]);

  return (
    <svg viewBox={`0 0 ${CLOUD_W} ${CLOUD_H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      {/* Animation: per-word fade-in on entry + continuous gentle float (period/phase staggered per word), enlarge and brighten on hover */}
      <style>{`
        @keyframes wcIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes wcFloat { from { transform: translateY(-3.5px); } to { transform: translateY(3.5px); } }
        .wc-in { animation: wcIn .55s ease both; }
        .wc-float { animation: wcFloat 3.2s ease-in-out infinite alternate; }
        .wc-word { cursor: pointer; transition: transform .18s ease, filter .18s ease;
                   transform-box: fill-box; transform-origin: center; }
        .wc-word:hover { transform: scale(1.14); filter: brightness(.8) saturate(1.35); }
        @media (prefers-reduced-motion: reduce) { .wc-float { animation: none; } }
      `}</style>
      <g transform={`translate(${CLOUD_W / 2},${CLOUD_H / 2})`}>
        {placed.map((w, k) => {
          const hl = highlight && w.full.includes(highlight);
          // placed is already sorted by font size descending: the top word gets an amber accent, lead words the blue-purple-teal family, the long tail gray-blue
          const color = k === 0 ? CLOUD_ACCENT : k < 9 ? CLOUD_CORE[w.idx % CLOUD_CORE.length] : CLOUD_TAIL;
          return (
            <g key={w.full} transform={`translate(${w.x ?? 0},${w.y ?? 0})`}
              className="wc-in" style={{ animationDelay: `${k * 45}ms` }}>
              <g className="wc-float"
                style={{ animationDuration: `${2.6 + (w.idx % 5) * 0.5}s`, animationDelay: `${-(w.idx % 7) * 0.45}s` }}>
                <text className="wc-word"
                  textAnchor="middle"
                  fontSize={w.size}
                  fontWeight={k < 9 ? 700 : 500}
                  fill={color}
                  opacity={highlight ? (hl ? 1 : 0.15) : 1}
                  onClick={() => onPick(w.full)}
                  style={{ fontFamily: '"PingFang SC","Microsoft YaHei",sans-serif' }}>
                  <title>{`${w.full} · ${w.heat}`}</title>
                  {w.text}
                </text>
              </g>
            </g>
          );
        })}
      </g>
    </svg>
  );
}

// ── Keyword heat map: left table (real prompt heat) + right d3-cloud word cloud (prompts + related search terms) ──
function HeatSection({ words, L, highlight, industryName, onPick }: {
  words: IndustryHeatWord[]; L: IndustryLang; highlight: string;
  industryName: string; onPick: (word: string) => void;
}) {
  if (words.length === 0) return null;
  const tableWords = words.filter(w => w.source === 'monitor').slice(0, 10);
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(240px, 340px) 1fr' }}>
      {/* Left: ranking table */}
      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid rgba(15,23,42,0.07)' }}>
        <div className="grid px-4 py-2.5 text-[11px] font-semibold" style={{ gridTemplateColumns: '52px 1fr 72px', background: 'rgba(15,23,42,0.035)', color: INK_SUB }}>
          <span>{L.heatRankCol}</span><span>{L.heatWordCol}</span><span className="text-right">{L.heatValueCol}</span>
        </div>
        {tableWords.map((w, i) => (
          <div key={w.query} className="grid items-center px-4 py-2.5 text-[12.5px]"
            style={{ gridTemplateColumns: '52px 1fr 72px', borderTop: '1px solid rgba(15,23,42,0.05)', background: i < 3 ? 'rgba(79,124,247,0.04)' : undefined }}>
            <span className="tabular-nums font-semibold" style={{ color: INK_SUB }}>{medal(i) ?? i + 1}</span>
            <span className="truncate" style={{ color: INK, fontWeight: i < 3 ? 650 : 450 }}>{w.query}</span>
            <span className="text-right font-bold tabular-nums" style={{ color: INK }}>{w.heat.toLocaleString()}</span>
          </div>
        ))}
      </div>
      {/* Right: word cloud (colored by heat tier, no longer by source; clicking a word links to search) */}
      <div className="rounded-xl p-3 flex items-center h-full" style={{
        background: 'radial-gradient(ellipse at center,#ffffff 25%,#eef2fb 100%)',
        border: '1px solid rgba(15,23,42,0.05)',
      }}>
        <WordCloud words={words} highlight={highlight} industryName={industryName} onPick={onPick} />
      </div>
    </div>
  );
}

// ── Brand leaderboard: per-engine columns (progress bar + score) + composite score + action CTA ──
function RankingSection({ d, L, highlight, onCta }: {
  d: CompetitiveAnalysis; L: IndustryLang; highlight: string; onCta: () => void;
}) {
  const engines = d.engines;
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="w-full tabular-nums" style={{ borderCollapse: 'separate', borderSpacing: 0, minWidth: 720 }}>
        <thead>
          <tr>
            <th style={{ ...TH, width: 48, textAlign: 'left' }}>{L.rankCol}</th>
            <th style={{ ...TH, textAlign: 'left' }}>{L.rankingProductCol}</th>
            {engines.map(e => <th key={e} style={TH}>{engineLabel(e)}</th>)}
            <th style={{ ...TH, width: 76 }}>{L.compositeCol}</th>
            <th style={{ ...TH, width: 90 }}>{L.actionCol}</th>
          </tr>
        </thead>
        <tbody>
          {d.ranking.map((row, i) => {
            const hl = highlight && row.name.toLowerCase().includes(highlight.toLowerCase());
            return (
              <tr key={row.name} style={{ background: hl ? 'rgba(79,124,247,0.08)' : undefined }}>
                <td style={{ ...TD(i), fontWeight: 650, color: INK_SUB }}>{medal(i) ?? i + 1}</td>
                <td style={{ ...TD(i) }}>
                  <div className="font-semibold text-[13px]" style={{ color: INK }}>{row.name}</div>
                  <div className="text-[10.5px]" style={{ color: INK_SUB }}>{L.relatedBrand}</div>
                </td>
                {engines.map(e => {
                  const s = row.scores[e] ?? 0;
                  const c = ENGINE_COLORS[e] || '#94a3b8';
                  return (
                    <td key={e} style={{ ...TD(i), minWidth: 96 }}>
                      <div className="flex items-center gap-2 justify-center">
                        <span className="h-[7px] rounded-full overflow-hidden" style={{ width: 56, background: 'rgba(15,23,42,0.07)', display: 'inline-block' }}>
                          <span className="block h-full rounded-full" style={{ width: `${Math.min(100, s)}%`, background: c }} />
                        </span>
                        <span className="text-[11.5px]" style={{ color: INK_SUB }}>{s.toFixed(s >= 100 ? 0 : 1)}</span>
                      </div>
                    </td>
                  );
                })}
                <td style={{ ...TD(i), textAlign: 'center' }}>
                  <span className="text-[14px] font-bold" style={{ color: '#2563eb' }}>{row.composite.toFixed(1)}</span>
                </td>
                <td style={{ ...TD(i), textAlign: 'center' }}>
                  <button onClick={onCta} className="text-[11.5px] font-semibold rounded-md px-3 py-1.5 transition-opacity hover:opacity-85"
                    style={i === 0
                      ? { background: 'var(--danger-solid)', color: '#fff' }
                      : { background: '#2563eb', color: '#fff' }}>
                    {L.improveRankBtn}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const TH: CSSProperties = {
  fontSize: 11, fontWeight: 600, color: INK_SUB, textAlign: 'center',
  padding: '0 6px 10px', letterSpacing: '0.03em', whiteSpace: 'nowrap',
};
const TD = (i: number): CSSProperties => ({
  padding: '9px 6px', borderTop: i >= 0 ? '1px solid rgba(15,23,42,0.06)' : undefined,
  textAlign: 'left',
});

// The whole page is fixed light (matching the reference design, not following the site theme, to avoid dark text on dark backgrounds)
function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="text-[15px] font-bold mb-3" style={{ color: INK }}>{children}</h2>;
}

function WhiteCard({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl p-5 shadow-sm ${className ?? ''}`}
      style={{ background: '#fff', border: '1px solid rgba(15,23,42,0.07)' }}>
      {children}
    </div>
  );
}

export function IndustryDetail() {
  const { key = '' } = useParams();
  const L = useIndustryLang();
  const navigate = useNavigate();
  const { isLoggedIn } = useAuth();
  const { openAuthModal } = useAuthModal();
  const [data, setData] = useState<IndustryData | null>(null);
  const [failed, setFailed] = useState(false);
  const [q, setQ] = useState('');

  useEffect(() => {
    setData(null); setFailed(false);
    industryApi.data(key).then(setData).catch(() => setFailed(true));
  }, [key]);

  // CTA: the public page's conversion slot — logged out opens sign-up, logged in goes to the report center
  const cta = () => (isLoggedIn ? navigate('/reports') : openAuthModal('register'));

  const comp = data?.competitive;

  return (
    <div className="min-h-screen" style={{ background: '#f6f7fb' }}>
      <PageHead titleKey="pageMeta.industry.title" titleFallback={data ? `${data.name} · ${L.detailTitlePrefix}` : L.libTitle} />
      {/* hero: light gradient + title + brand search (highlights the leaderboard/word cloud) */}
      <div style={{ background: 'linear-gradient(160deg,#dfe9ff 0%,#ece5ff 45%,#f6f4ff 100%)' }}>
        <div className="max-w-6xl mx-auto px-4 pt-12 pb-10 text-center">
          <Link to="/industry" className="text-[12px] font-medium" style={{ color: '#4f6cd4' }}>← {L.backToLib}</Link>
          <h1 className="mt-3 text-3xl sm:text-4xl font-bold" style={{ color: INK }}>
            {data?.name ?? key}<span style={{ color: '#4f6cd4' }}> · {L.detailTitlePrefix}</span>
          </h1>
          {data && <p className="mt-2 text-[12.5px]" style={{ color: INK_SUB }}>{L.updatedAt(data.updated)}</p>}
          <div className="mt-6 max-w-xl mx-auto relative">
            <input value={q} onChange={e => setQ(e.target.value)} placeholder={L.searchPlaceholder}
              className="w-full rounded-full px-5 py-3 text-[13px] outline-none shadow-sm"
              style={{ background: '#fff', color: INK, border: '1px solid rgba(15,23,42,0.08)' }} />
            <span className="absolute right-4 top-1/2 -translate-y-1/2 text-[15px]" style={{ color: '#4f6cd4' }}>🔍</span>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-10 grid gap-10">
        {failed && <div className="text-center text-sm py-16" style={{ color: INK_SUB }}>{L.loadFailed}</div>}
        {!failed && !data && <div className="text-center text-sm py-16" style={{ color: INK_SUB }}>{L.loading}</div>}
        {data && comp && (
          <>
            {/* ── Three industry overview cards ── */}
            <section>
              <SectionTitle>{L.secVisibility}</SectionTitle>
              <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
                <VisCard idx={0} title={L.heatCard} sub={L.heatCardSub}
                  big={Math.round(data.heat.current).toLocaleString()} metric={data.heat} deltaUnit="" chart="area" />
                <VisCard idx={1} title={L.top1Card} sub={L.top1CardSub(data.top1.coverage.current)}
                  big={data.top1.name} metric={data.top1.coverage} deltaUnit="%" chart="line" />
                <VisCard idx={2} title={L.cr3Card} sub={L.cr3CardSub}
                  big={`${data.cr3.current}%`} metric={data.cr3} deltaUnit="%" chart="bar" />
              </div>
            </section>

            {/* ── Leading brand comparison + market competition insights ── */}
            <section className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(260px, 1fr)' }}>
              <div>
                <SectionTitle>{L.secContrast}</SectionTitle>
                <WhiteCard>
                  <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
                    {/* Industry average */}
                    <div className="rounded-xl p-3.5" style={{ background: 'rgba(15,23,42,0.025)', border: '1px solid rgba(15,23,42,0.06)' }}>
                      <div className="text-center text-[13px] font-bold mb-3" style={{ color: INK }}>{L.industryAvg}</div>
                      <div className="grid gap-2">
                        <ContrastRow label={L.kwCoverage} value={`${data.industry_avg_coverage}%`} />
                        <ContrastRow label={L.prodRank} value="—" />
                        <ContrastRow label={L.prodMention} value={`${data.industry_avg_mention}%`} />
                      </div>
                    </div>
                    {/* Top3 brand cards: compared to the industry average (▲ = above industry average) */}
                    {data.brands.map(b => (
                      <div key={b.name} className="rounded-xl p-3.5" style={{ background: '#fff', border: '1px solid rgba(15,23,42,0.08)', boxShadow: '0 1px 4px rgba(15,23,42,0.04)' }}>
                        <div className="text-center text-[13px] font-bold mb-3 truncate" style={{ color: INK }}>
                          {medal(b.rank - 1)} {b.name}
                        </div>
                        <div className="grid gap-2">
                          <ContrastRow label={L.kwCoverage} value={`${b.coverage}%`} diff={b.coverage_vs_avg} />
                          <ContrastRow label={L.prodRank} value={L.rankValue(b.rank)} />
                          <ContrastRow label={L.prodMention} value={`${b.mention}%`} diff={b.mention_vs_avg} />
                        </div>
                        <div className="mt-2.5 text-center text-[10.5px]" style={{ color: b.coverage_vs_avg >= 0 ? '#059669' : '#dc2626' }}>
                          {b.coverage_vs_avg >= 0 ? L.vsAvgAbove(Math.abs(b.coverage_vs_avg)) : L.vsAvgBelow(Math.abs(b.coverage_vs_avg))}
                        </div>
                      </div>
                    ))}
                  </div>
                </WhiteCard>
              </div>
              <div>
                <SectionTitle>{L.secInsight}</SectionTitle>
                <WhiteCard>
                  <div className="grid gap-3">
                    <div className="rounded-xl p-4 flex items-center justify-between gap-3" style={{ background: 'linear-gradient(135deg,#eef3ff,#f7f9ff)', border: '1px solid rgba(37,99,235,0.12)' }}>
                      <div>
                        <div className="text-[12px] font-semibold" style={{ color: INK }}>{L.top1Lead}</div>
                        <div className="text-[22px] font-bold tabular-nums" style={{ color: '#2563eb' }}>
                          +{data.top1_gap}%
                        </div>
                        <div className="text-[10.5px]" style={{ color: INK_SUB }}>
                          {data.brands.length >= 2 ? L.top1LeadSub(data.brands[0].name, data.brands[1].name) : ''}
                        </div>
                      </div>
                      <button onClick={cta} className="shrink-0 text-[12px] font-semibold rounded-lg px-4 py-2 text-white transition-opacity hover:opacity-85"
                        style={{ background: 'linear-gradient(135deg,#3b82f6,#6366f1)' }}>
                        {L.improveBtn}
                      </button>
                    </div>
                    <div className="rounded-xl p-4 flex items-center justify-between gap-3" style={{ background: 'linear-gradient(135deg,#f5efff,#faf7ff)', border: '1px solid rgba(139,92,246,0.14)' }}>
                      <div>
                        <div className="text-[12px] font-semibold" style={{ color: INK }}>{L.optimizeKw}</div>
                        <div className="text-[22px] font-bold tabular-nums" style={{ color: INK }}>{data.opportunity_keywords}</div>
                        <div className="text-[10.5px]" style={{ color: INK_SUB }}>{L.optimizeKwSub}</div>
                      </div>
                      <button onClick={cta} className="shrink-0 text-[12px] font-semibold rounded-lg px-4 py-2 text-white transition-opacity hover:opacity-85"
                        style={{ background: 'linear-gradient(135deg,#8b5cf6,#a855f7)' }}>
                        {L.getBtn}
                      </button>
                    </div>
                  </div>
                </WhiteCard>
              </div>
            </section>

            {/* ── Keyword heat map ── */}
            <section>
              <SectionTitle>{L.secHeat}</SectionTitle>
              <WhiteCard>
                <HeatSection words={data.heat_words} L={L} highlight={q.trim()}
                  industryName={data.name} onPick={w => setQ(q.trim() === w ? '' : w)} />
              </WhiteCard>
            </section>

            {/* ── Brand leaderboard ── */}
            <section>
              <SectionTitle>{L.secRanking}</SectionTitle>
              <WhiteCard>
                <RankingSection d={comp} L={L} highlight={q.trim()} onCta={cta} />
              </WhiteCard>
            </section>

            {/* ── Citation source analysis (finale) ── */}
            {data.sources.total_citations > 0 && (
              <section>
                <SectionTitle>{L.secSources}</SectionTitle>
                <WhiteCard>
                  <SourcesSection d={data} L={L} />
                </WhiteCard>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
