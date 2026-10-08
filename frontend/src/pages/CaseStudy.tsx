import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams, Navigate } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import { CaseStatRow } from '../components/CaseStatRow';
import { caseStudies, type CaseStudy } from '../data/caseStudies';
import {
  ResponsiveContainer, BarChart, Bar, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Cell, LabelList, Tooltip as RTooltip,
} from 'recharts';

// Case detail page /cases/:slug -- one "story", following the skeleton of OptimizeGEO's case page:
//   one-line title -> three big numbers side by side -> about the client -> problem -> starting point -> what we did -> results -> one-liner.
// Numbers lead, text speaks plainly; monitoring charts (growth curve / funnel / per-engine hits) go into a collapsible area under "Results" as supporting evidence.
// This table only serves the domestic historical case data in caseStudies; it is not the engine registry --
// the overseas build's registry lacks these keys, and engineLabel() would just degrade to the raw key.
// Names follow constants/engines.ts.
const ENGINE_LABEL: Record<'zh' | 'en', Record<string, string>> = {
  zh: { deepseek: 'DeepSeek', doubao: '豆包', qwen: '通义千问', wenxin: '文心一言', yuanbao: '腾讯元宝' },
  en: { deepseek: 'DeepSeek', doubao: 'Doubao', qwen: 'Qwen', wenxin: 'ERNIE', yuanbao: 'Yuanbao' },
};

const SHADES = ['#141418', '#3a3a42', '#6b6b75', '#9a9aa3', '#c4c4cc'];

type TFn = ReturnType<typeof useTranslation>['t'];

export function CaseStudyPage() {
  const { slug } = useParams();
  const { t, i18n } = useTranslation();
  const lang = i18n.language === 'en' ? 'en' : 'zh';
  const nf = new Intl.NumberFormat(lang === 'en' ? 'en-US' : 'zh-CN');
  const c = caseStudies.find((x) => x.slug === slug);
  if (!c) return <Navigate to="/cases" replace />;
  const x = c[lang];
  const others = caseStudies.filter((o) => o.slug !== c.slug).sort((a, b) => a.order - b.order);

  return (
    <div className="min-h-screen grid-background">
      <Helmet>
        <title>{`${x.headline} · Vigilath`}</title>
        <meta property="og:title" content={x.headline} />
        <meta name="description" content={x.tagline} />
        <meta property="og:description" content={x.tagline} />
      </Helmet>
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>

      <main className="flex-1 px-4 py-12 sm:py-16 hero-gradient relative z-10">
        <div className="w-full max-w-5xl mx-auto animate-fade-in">
          <Link to="/cases" className="inline-flex items-center gap-1 text-sm font-medium text-secondary hover:text-primary transition-colors mb-6">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
            {t('cases.backToList')}
          </Link>

          <CaseStory c={c} lang={lang} t={t} nf={nf} />

          {others.length > 0 && (
            <section className="mt-12 sm:mt-16">
              <h3 className="text-lg font-bold text-primary mb-4">{t('cases.moreCases')}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {others.map((o) => (
                  <Link key={o.slug} to={`/cases/${o.slug}`} className="card p-6 block hover:border-strong transition-colors">
                    <div className="text-xs text-muted mb-2">{t(`cases.verticals.${o.verticalKey}`)} · {o[lang].brand}</div>
                    <div className="text-lg font-bold text-primary leading-snug">{o[lang].headline}</div>
                    <div className="text-sm text-secondary mt-2">{o[lang].tagline}</div>
                  </Link>
                ))}
              </div>
            </section>
          )}

          <CaseCta t={t} />

          <p className="text-center text-xs text-muted mt-10 max-w-2xl mx-auto leading-relaxed">{t('cases.disclaimer')}</p>
        </div>
      </main>
    </div>
  );
}
// Footer CTA: want to know what AI says about you now -> free audit
export function CaseCta({ t }: { t: TFn }) {
  return (
    <section className="mt-12 sm:mt-16 card px-6 py-10 sm:px-12 sm:py-12 text-center">
      <h3 className="text-2xl sm:text-3xl font-bold text-primary m-0 mb-3">{t('cases.cta.title')}</h3>
      <p className="text-base text-secondary max-w-xl mx-auto m-0 mb-6">{t('cases.cta.desc')}</p>
      <Link to="/" className="inline-flex items-center h-11 px-7 rounded-full text-sm font-semibold transition-all"
        style={{ background: 'var(--text-primary)', color: 'var(--bg-card)' }}>
        {t('cases.cta.button')}
      </Link>
    </section>
  );
}

function CaseStory({ c, lang, t, nf }: { c: CaseStudy; lang: 'zh' | 'en'; t: TFn; nf: Intl.NumberFormat }) {
  const x = c[lang];
  return (
    <article className="card px-6 py-10 sm:px-12 sm:py-14 lg:px-16">
      {/* Eyebrow + title */}
      <header className="max-w-3xl mb-10 sm:mb-12">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted mb-4">
          <span className="font-semibold text-secondary">{t(`cases.verticals.${c.verticalKey}`)}</span>
          <span>·</span>
          <span>{x.brand}</span>
          {c.periodLabel && lang === 'zh' && (<><span>·</span><span>{c.periodLabel}</span></>)}
        </div>
        <h2 className="text-3xl sm:text-4xl lg:text-[2.75rem] font-bold text-primary leading-[1.15] m-0 mb-3">{x.headline}</h2>
        <p className="text-lg sm:text-xl text-secondary m-0">{x.tagline}</p>
      </header>

      {/* Three big numbers */}
      <CaseStatRow c={c} t={t} nf={nf} />

      {/* Body */}
      <div className="max-w-3xl flex flex-col gap-10 sm:gap-12 mt-12 sm:mt-14">
        <Section title={t('cases.story.about')}><P>{x.about}</P></Section>
        <Section title={t('cases.story.problem')}><P>{x.problem}</P></Section>
        <Section title={t('cases.story.starting')}><P>{x.starting}</P></Section>

        <Section title={t('cases.story.fix')}>
          <ol className="m-0 p-0 list-none flex flex-col gap-3">
            {x.fix.map((line, i) => (
              <li key={i} className="flex items-start gap-3 text-base text-secondary leading-relaxed">
                <span className="flex-shrink-0 w-6 h-6 rounded-full text-xs font-bold flex items-center justify-center mt-0.5"
                  style={{ background: 'var(--text-primary)', color: 'var(--bg-card)' }}>{i + 1}</span>
                <span>{line}</span>
              </li>
            ))}
          </ol>
        </Section>

        {x.quote && (
          <blockquote className="m-0 pl-5 border-l-4" style={{ borderColor: 'var(--text-primary)' }}>
            <p className="text-lg sm:text-xl text-primary font-medium leading-relaxed m-0">“{x.quote.text}”</p>
            <footer className="text-sm text-muted mt-2">— {x.quote.who}</footer>
          </blockquote>
        )}
      </div>

      {/* Results: number-led items + collapsible monitoring charts */}
      <div className="mt-12 sm:mt-14">
        <Section title={t('cases.story.results')}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-8 mt-2">
            {x.results.map((r, i) => (
              <div key={i}>
                <div className={`font-black leading-none tabular-nums tracking-tight ${i === 0 ? 'text-5xl sm:text-6xl' : 'text-4xl sm:text-5xl'} text-primary`}>{r.value}</div>
                <div className="text-base sm:text-lg font-bold text-primary mt-2 leading-snug">{r.title}</div>
                <p className="text-sm text-secondary leading-relaxed m-0 mt-1.5">{r.desc}</p>
              </div>
            ))}
          </div>
        </Section>
        <TechCharts c={c} lang={lang} t={t} />
      </div>

      {/* One-liner */}
      <div className="mt-12 sm:mt-14 rounded-2xl px-6 py-7 sm:px-10 sm:py-9" style={{ background: 'var(--text-primary)', color: 'var(--bg-card)' }}>
        <div className="text-xs font-semibold tracking-wide mb-3" style={{ opacity: 0.6 }}>{t('cases.story.takeaway')}</div>
        <p className="text-xl sm:text-2xl font-bold leading-snug m-0">{x.takeaway}</p>
      </div>
    </article>
  );
}

// Three big numbers side by side: clients/day (largest) · questions hit · articles cited. Thin dividers, no boxes.
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-xl sm:text-2xl font-bold text-primary leading-tight m-0 mb-3">{title}</h3>
      {children}
    </section>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-base sm:text-[17px] text-secondary leading-relaxed m-0">{children}</p>;
}

// Monitoring charts: collapsed by default; those interested can expand.
function TechCharts({ c, lang, t }: { c: CaseStudy; lang: 'zh' | 'en'; t: TFn }) {
  const [open, setOpen] = useState(false);
  const funnelData = [
    { name: t('cases.metrics.seedKeywords'), value: c.metrics.seedKeywords },
    { name: t('cases.metrics.indexed'), value: c.metrics.indexedCount },
    { name: t('cases.metrics.cited'), value: c.metrics.citedByAI },
  ];
  const engineData = c.engineHits.map((e) => ({ name: ENGINE_LABEL[lang][e.engine] ?? e.engine, value: e.hits }));
  return (
    <div className="mt-8 flex flex-col gap-6">
      <div className="relative flex justify-center">
        <div className="absolute inset-x-0 top-1/2 h-px" style={{ background: 'var(--border-color)' }} />
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
          className="relative inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-semibold border transition-all duration-200"
          style={{ background: 'var(--bg-card)', color: 'var(--text-secondary)', borderColor: 'var(--border-color)' }}>
          {open ? t('cases.leads.hideTech') : t('cases.leads.showTech')}
          <svg className={`w-3.5 h-3.5 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>
      {open && (
      <div className="flex flex-col gap-7 rounded-2xl p-5 sm:p-6 border border-soft" style={{ background: 'var(--bg-surface)' }}>
    <p className="text-[11px] text-muted text-center m-0">{t('cases.leads.techHint')}</p>

      {/* ② AI visibility growth curve */}
    <div>
      <div className="mb-3">
        <h3 className="text-sm font-bold text-primary leading-tight">{t('cases.change.trendTitle')}</h3>
        <p className="text-[11px] text-muted mt-0.5 m-0">{t('cases.change.trendHint')}</p>
      </div>
      <ResponsiveContainer width="100%" height={210}>
        <AreaChart data={c.trend} margin={{ top: 8, right: 16, bottom: 0, left: -18 }}>
          <defs>
            <linearGradient id={`grad-${c.slug}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#141418" stopOpacity={0.22} />
              <stop offset="100%" stopColor="#141418" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} allowDecimals={false} />
          <RTooltip cursor={{ stroke: 'var(--border-strong)' }}
            formatter={(v) => [v as number, t('cases.change.trendTip')]} />
          <Area type="monotone" dataKey="value" stroke="#141418" strokeWidth={2.4}
            fill={`url(#grad-${c.slug})`} dot={{ r: 3, fill: '#141418' }} isAnimationActive />
        </AreaChart>
      </ResponsiveContainer>
      <div className="flex justify-between text-[11px] text-muted mt-1 px-1">
        <span>← {t('cases.change.trendStart')}</span>
        <span>{t('cases.change.trendEnd', { n: c.hitQueries })} →</span>
      </div>
    </div>

    {/* ③④ Supporting: optimization funnel + per-engine hits */}
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 sm:gap-8">
      <ChartBlock title={t('cases.chart.funnelTitle')} hint={t('cases.chart.funnelHint')}>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart layout="vertical" data={funnelData} margin={{ top: 4, right: 36, bottom: 4, left: 8 }} barCategoryGap={14}>
            <XAxis type="number" hide />
            <YAxis type="category" dataKey="name" width={92} axisLine={false} tickLine={false}
              tick={{ fontSize: 12, fill: 'var(--text-secondary)' }} />
            <Bar dataKey="value" radius={[0, 6, 6, 0]} isAnimationActive>
              {funnelData.map((_, i) => <Cell key={i} fill={SHADES[i]} />)}
              <LabelList dataKey="value" position="right" style={{ fill: 'var(--text-primary)', fontSize: 13, fontWeight: 700 }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ChartBlock>

      <ChartBlock title={t('cases.chart.engineTitle')} hint={t('cases.chart.engineHint', { max: c.engineMax })}>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart layout="vertical" data={engineData} margin={{ top: 4, right: 36, bottom: 4, left: 8 }} barCategoryGap={8}>
            <XAxis type="number" hide domain={[0, c.engineMax]} />
            <YAxis type="category" dataKey="name" width={72} axisLine={false} tickLine={false}
              tick={{ fontSize: 12, fill: 'var(--text-secondary)' }} />
            <RTooltip cursor={{ fill: 'rgba(0,0,0,0.04)' }}
              formatter={(v) => [`${v} / ${c.engineMax}`, t('cases.chart.engineTip')]} />
            <Bar dataKey="value" radius={[0, 6, 6, 0]} fill="#141418" isAnimationActive>
              <LabelList dataKey="value" position="right" style={{ fill: 'var(--text-primary)', fontSize: 13, fontWeight: 700 }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ChartBlock>
    </div>
      </div>
      )}
    </div>
  );
}

function ChartBlock({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-3">
        <h3 className="text-sm font-bold text-primary leading-tight">{title}</h3>
        <p className="text-[11px] text-muted mt-0.5 m-0">{hint}</p>
      </div>
      {children}
    </div>
  );
}
