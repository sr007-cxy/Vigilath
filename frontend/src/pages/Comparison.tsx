import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { PageHead } from '../components/PageHead';
import { useContactModal } from '../components/ContactModalContext';

type Row = { dim: string; vigilath: string; seo: string };
type WhenCol = { title: string; points: string[] };
type WhyCard = { title: string; desc: string };

export function Comparison() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { openContact } = useContactModal();

  const rows = t('comparison.table.rows', { returnObjects: true }) as Row[];
  const vigilathWhen = t('comparison.whenToUse.vigilath', { returnObjects: true }) as WhenCol;
  const seoWhen = t('comparison.whenToUse.seo', { returnObjects: true }) as WhenCol;
  const whyCards = t('comparison.why.cards', { returnObjects: true }) as WhyCard[];

  return (
    <div className="min-h-screen grid-background">
      <PageHead titleKey="pageMeta.comparison.title" descriptionKey="pageMeta.comparison.description" />
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          {/* Hero — H1 */}
          <section className="hero mb-16">
            <div className="max-w-3xl mx-auto text-center">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-surface border border-soft shadow-glow mb-4">
                <span className="text-xs font-semibold text-secondary">
                  {t('comparison.hero.badge')}
                </span>
              </div>
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight animate-slide-up">
                <span className="gradient-text">{t('comparison.hero.title')}</span>
              </h1>
              <p
                className="text-lg sm:text-xl text-secondary max-w-3xl mx-auto leading-relaxed animate-slide-up"
                style={{ animationDelay: '0.1s' }}
              >
                {t('comparison.hero.subtitle')}
              </p>
            </div>
          </section>

          {/* Intro — H2 */}
          <section className="mb-16 sm:mb-20 animate-fade-in">
            <h2 className="text-2xl sm:text-3xl font-bold mb-6 text-center gradient-text">
              {t('comparison.intro.title')}
            </h2>
            <p className="text-secondary leading-relaxed max-w-3xl mx-auto text-center">
              {t('comparison.intro.body')}
            </p>
          </section>

          {/* Comparison table — H2 */}
          <section className="mb-16 sm:mb-20 animate-fade-in">
            <h2 className="text-2xl sm:text-3xl font-bold mb-8 sm:mb-12 text-center gradient-text">
              {t('comparison.table.title')}
            </h2>
            <div className="card overflow-hidden">
              {/* Header row */}
              <div className="grid grid-cols-[1.1fr_2fr_2fr] sm:grid-cols-[1fr_2fr_2fr] border-b border-soft">
                <div className="px-3 sm:px-5 py-3 sm:py-4 text-xs sm:text-sm font-bold text-muted">
                  {t('comparison.table.colDimension')}
                </div>
                <div
                  className="px-3 sm:px-5 py-3 sm:py-4 text-sm sm:text-base font-bold text-primary"
                  style={{ background: 'var(--bg-surface-hover)' }}
                >
                  {t('comparison.table.colVigilath')}
                </div>
                <div className="px-3 sm:px-5 py-3 sm:py-4 text-sm sm:text-base font-bold text-secondary">
                  {t('comparison.table.colSeo')}
                </div>
              </div>
              {/* Body rows */}
              {rows.map((row, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[1.1fr_2fr_2fr] sm:grid-cols-[1fr_2fr_2fr] border-b border-soft last:border-b-0"
                >
                  <div className="px-3 sm:px-5 py-3 sm:py-4 text-xs sm:text-sm font-semibold text-primary">
                    {row.dim}
                  </div>
                  <div
                    className="px-3 sm:px-5 py-3 sm:py-4 text-xs sm:text-sm text-primary leading-relaxed"
                    style={{ background: 'var(--bg-surface-hover)' }}
                  >
                    <span className="flex items-start gap-2">
                      <svg
                        className="w-4 h-4 mt-0.5 flex-shrink-0 text-accent-primary"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span>{row.vigilath}</span>
                    </span>
                  </div>
                  <div className="px-3 sm:px-5 py-3 sm:py-4 text-xs sm:text-sm text-secondary leading-relaxed">
                    {row.seo}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* When to use — H2 + two H3 columns */}
          <section className="mb-16 sm:mb-20 animate-fade-in">
            <h2 className="text-2xl sm:text-3xl font-bold mb-8 sm:mb-12 text-center gradient-text">
              {t('comparison.whenToUse.title')}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 sm:gap-8">
              <div className="card p-6 sm:p-8" style={{ borderColor: 'var(--accent-primary)' }}>
                <h3 className="text-xl font-bold mb-5 gradient-text">{vigilathWhen.title}</h3>
                <ul className="space-y-3">
                  {vigilathWhen.points.map((p, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm text-secondary leading-relaxed">
                      <svg
                        className="w-5 h-5 mt-0.5 flex-shrink-0 text-accent-primary"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span>{p}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="card p-6 sm:p-8">
                <h3 className="text-xl font-bold mb-5 text-primary">{seoWhen.title}</h3>
                <ul className="space-y-3">
                  {seoWhen.points.map((p, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm text-secondary leading-relaxed">
                      <svg
                        className="w-5 h-5 mt-0.5 flex-shrink-0 text-muted"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6" />
                      </svg>
                      <span>{p}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <p className="text-sm text-secondary leading-relaxed max-w-3xl mx-auto text-center mt-8">
              {t('comparison.whenToUse.note')}
            </p>
          </section>

          {/* Why Vigilath — H2 + three H3 cards */}
          <section className="mb-16 sm:mb-20 animate-fade-in">
            <h2 className="text-2xl sm:text-3xl font-bold mb-8 sm:mb-12 text-center gradient-text">
              {t('comparison.why.title')}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 sm:gap-8">
              {whyCards.map((card, i) => (
                <div
                  key={i}
                  className="bg-card border border-border rounded-2xl p-6 sm:p-8 hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1"
                >
                  <h3 className="text-lg font-bold mb-3 gradient-text">{card.title}</h3>
                  <p className="text-sm text-secondary leading-relaxed">{card.desc}</p>
                </div>
              ))}
            </div>
          </section>

          {/* CTA */}
          <section className="animate-fade-in">
            <div className="card p-8 sm:p-12 text-center">
              <h2 className="text-2xl sm:text-3xl font-bold mb-4 gradient-text">
                {t('comparison.cta.title')}
              </h2>
              <p className="text-secondary leading-relaxed max-w-2xl mx-auto mb-8">
                {t('comparison.cta.subtitle')}
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="btn-solid px-6 py-3 rounded-full font-semibold text-sm sm:text-base"
                >
                  {t('comparison.cta.primary')}
                </button>
                <button
                  type="button"
                  onClick={openContact}
                  className="btn-secondary px-6 py-3 rounded-full font-semibold text-sm sm:text-base"
                >
                  {t('comparison.cta.secondary')}
                </button>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
