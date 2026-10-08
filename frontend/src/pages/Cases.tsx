import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { PageHead } from '../components/PageHead';
import { CaseStatRow } from '../components/CaseStatRow';
import { caseStudies } from '../data/caseStudies';
import { CaseCta } from './CaseStudy';

// Case list page /cases -- following OptimizeGEO's case-studies list:
//   intro paragraph -> one block per case: "Case N" eyebrow, title, three big numbers, three summaries (problem / approach / result), "Read the full case ->"
//   -> footer CTA. Details at /cases/:slug (CaseStudy.tsx).

export function Cases() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language === 'en' ? 'en' : 'zh';
  const nf = new Intl.NumberFormat(lang === 'en' ? 'en-US' : 'zh-CN');
  const list = [...caseStudies].sort((a, b) => a.order - b.order);

  return (
    <div className="min-h-screen grid-background">
      <PageHead titleKey="pageMeta.cases.title" descriptionKey="pageMeta.cases.description" />
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-5xl mx-auto animate-fade-in">
          <section className="hero mb-12 sm:mb-16">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-2xl sm:text-4xl lg:text-5xl font-bold mb-6 leading-[1.05] tracking-tight animate-slide-up">
                <span className="gradient-text">{t('cases.title')}</span>
              </h1>
              <p className="text-lg sm:text-xl text-secondary max-w-3xl mx-auto leading-relaxed animate-slide-up" style={{ animationDelay: '0.1s' }}>
                {t('cases.subtitle')}
              </p>
            </div>
          </section>

          <section className="flex flex-col gap-8 sm:gap-10">
            {list.map((c, i) => {
              const x = c[lang];
              const href = `/cases/${c.slug}`;
              return (
                <article key={c.slug} className="card px-6 py-10 sm:px-12 sm:py-12 lg:px-16">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted mb-4">
                    <span className="font-bold text-primary">{t('cases.caseN', { n: i + 1 })}</span>
                    <span>·</span>
                    <span className="font-semibold text-secondary">{t(`cases.verticals.${c.verticalKey}`)}</span>
                    <span>·</span>
                    <span>{x.brand}</span>
                    {c.periodLabel && lang === 'zh' && (<><span>·</span><span>{c.periodLabel}</span></>)}
                  </div>
                  <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-primary leading-[1.2] m-0 mb-2 max-w-3xl">
                    <Link to={href} className="hover:underline underline-offset-4">{x.headline}</Link>
                  </h2>
                  <p className="text-base sm:text-lg text-secondary m-0 mb-8 max-w-3xl">{x.tagline}</p>

                  <CaseStatRow c={c} t={t} nf={nf} />

                  <div className="max-w-3xl flex flex-col gap-4 mt-8">
                    {x.listSummary.map((para, k) => (
                      <p key={k} className="text-base text-secondary leading-relaxed m-0">{para}</p>
                    ))}
                  </div>

                  <Link to={href} className="inline-flex items-center gap-1.5 mt-8 text-sm font-semibold text-primary hover:underline underline-offset-4">
                    {t('cases.readFull')}
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                  </Link>
                </article>
              );
            })}
          </section>

          <CaseCta t={t} />

          <p className="text-center text-xs text-muted mt-10 max-w-2xl mx-auto leading-relaxed">{t('cases.disclaimer')}</p>
        </div>
      </main>
    </div>
  );
}
