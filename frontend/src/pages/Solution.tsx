import { useParams, Navigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../components/PageHead';
import { useContactModal } from '../components/ContactModalContext';
import { SolutionIcon } from '../components/SolutionIcon';
import { SOLUTIONS, SOLUTION_COLUMNS, solutionsByColumn, type Locale } from '../data/solutions';

export function Solution() {
  const { slug } = useParams<{ slug: string }>();
  const { t, i18n } = useTranslation();
  const { openContact } = useContactModal();

  const sol = slug ? SOLUTIONS[slug] : undefined;
  if (!sol) {
    // Unknown slug -> back to home (guards against old bookmarks / typos hitting 404)
    return <Navigate to="/" replace />;
  }

  const locale: Locale = i18n.language === 'en' ? 'en' : 'zh';
  const isZh = locale === 'zh';
  const column = SOLUTION_COLUMNS.find((c) => c.id === sol.column);
  const related = solutionsByColumn(sol.column).filter((s) => s.slug !== sol.slug).slice(0, 6);

  return (
    <div className="min-h-screen grid-background">
      <PageHead
        titleKey={`${sol.title[locale]} · Vigilath`}
        titleFallback={`${sol.title[locale]} · Vigilath`}
        descriptionKey={sol.subtitle[locale]}
        descriptionFallback={sol.subtitle[locale]}
      />
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 relative z-10">
        {/* ── Hero ───────────────────────────────────────── */}
        <section className="hero-gradient px-4 pt-16 sm:pt-24 pb-16">
          <div className="w-full max-w-5xl mx-auto animate-fade-in">
            {/* Breadcrumb */}
            <nav className="mb-8 text-sm flex items-center gap-2 flex-wrap" style={{ color: 'var(--text-muted)' }}>
              <Link to="/" className="hover:text-primary transition-colors">{t('nav.home')}</Link>
              <span>/</span>
              <span style={{ color: 'var(--text-secondary)' }}>{t('nav.solutions')}</span>
              {column && (
                <>
                  <span>/</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{column.title[locale]}</span>
                </>
              )}
            </nav>

            <div className="max-w-3xl">
              {/* Category chip */}
              {column && (
                <span
                  className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold mb-5 bg-accent-primary/10"
                  style={{ color: 'var(--accent-primary)' }}
                >
                  <SolutionIcon name={`col:${sol.column}`} className="w-3.5 h-3.5" strokeWidth={2} />
                  {column.title[locale]}
                </span>
              )}

              <div className="flex items-start gap-5 mb-6">
                <span className="hidden sm:flex w-16 h-16 rounded-2xl items-center justify-center flex-shrink-0 bg-accent-primary/10 ring-1 ring-accent-primary/20">
                  <SolutionIcon name={sol.icon} className="w-8 h-8 text-accent-primary" />
                </span>
                <h1 className="text-2xl sm:text-4xl lg:text-5xl font-bold leading-[1.05] tracking-tight animate-slide-up">
                  <span className="gradient-text">{sol.title[locale]}</span>
                </h1>
              </div>

              <p className="text-lg sm:text-xl text-secondary leading-relaxed mb-5 animate-slide-up" style={{ animationDelay: '0.1s' }}>
                {sol.subtitle[locale]}
              </p>
              <p className="text-base text-secondary leading-relaxed max-w-2xl animate-slide-up" style={{ animationDelay: '0.15s' }}>
                {sol.intro[locale]}
              </p>

              <div className="flex flex-col sm:flex-row gap-3 mt-8 animate-slide-up" style={{ animationDelay: '0.2s' }}>
                <button
                  onClick={() => openContact()}
                  className="btn-solid inline-flex items-center justify-center h-11 px-7 rounded-lg text-sm font-semibold transition-all duration-200"
                >
                  {isZh ? '联系我们' : 'Contact us'}
                </button>
                <Link
                  to="/"
                  className="btn-secondary inline-flex items-center justify-center h-11 px-7 rounded-lg text-sm font-semibold transition-all duration-200"
                >
                  {isZh ? '免费检测一次' : 'Run a free check'}
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* ── Sections ───────────────────────────────────────── */}
        <div className="px-4 pb-8">
          <div className="w-full max-w-5xl mx-auto">
            {sol.sections.map((section, si) => (
              <section key={si} className="mb-16 animate-fade-in" style={{ animationDelay: `${0.1 + si * 0.05}s` }}>
                <div className="flex items-center gap-3 mb-8">
                  <span className="w-1.5 h-7 rounded-full" style={{ background: 'var(--accent-primary)' }} />
                  <h2 className="text-2xl sm:text-3xl font-bold gradient-text">{section.heading[locale]}</h2>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                  {section.points.map((pt, pi) => (
                    <div
                      key={pi}
                      className="group relative bg-card border border-border rounded-2xl p-6 overflow-hidden transition-all duration-300 hover:-translate-y-1 hover:shadow-xl hover:shadow-accent-primary/10 hover:border-accent-primary/40"
                    >
                      {/* Top gradient bar revealed on hover */}
                      <span className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-accent-primary to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                      <span
                        className="inline-flex items-center justify-center w-10 h-10 rounded-xl mb-4 text-sm font-bold bg-accent-primary/10"
                        style={{ color: 'var(--accent-primary)' }}
                      >
                        {String(pi + 1).padStart(2, '0')}
                      </span>
                      <h3 className="text-lg font-bold mb-2.5 text-primary">{pt.title[locale]}</h3>
                      <p className="text-sm text-secondary leading-relaxed">{pt.desc[locale]}</p>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>

        {/* ── CTA ────────────────────────────────────────── */}
        <div className="px-4 py-8">
          <div className="w-full max-w-5xl mx-auto">
            <section
              className="relative overflow-hidden rounded-3xl p-10 sm:p-14 text-center border border-accent-primary/20"
              style={{ background: 'linear-gradient(135deg, color-mix(in srgb, var(--accent-primary) 12%, transparent), color-mix(in srgb, var(--accent-primary) 3%, transparent))' }}
            >
              <span className="inline-flex w-14 h-14 rounded-2xl items-center justify-center mb-6 bg-accent-primary/15">
                <SolutionIcon name={sol.icon} className="w-7 h-7 text-accent-primary" />
              </span>
              <h2 className="text-2xl sm:text-3xl font-bold mb-4 gradient-text">{sol.cta.title[locale]}</h2>
              <p className="text-base sm:text-lg text-secondary max-w-2xl mx-auto mb-8 leading-relaxed">
                {sol.cta.desc[locale]}
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                <button
                  onClick={() => openContact()}
                  className="btn-solid inline-flex items-center justify-center h-11 px-8 rounded-lg text-sm font-semibold transition-all duration-200"
                >
                  {isZh ? '联系我们' : 'Contact us'}
                </button>
                <Link
                  to="/"
                  className="btn-secondary inline-flex items-center justify-center h-11 px-8 rounded-lg text-sm font-semibold transition-all duration-200"
                >
                  {isZh ? '免费检测一次' : 'Run a free check'}
                </Link>
              </div>
            </section>
          </div>
        </div>

        {/* ── Related solutions ───────────────────────────────── */}
        {related.length > 0 && (
          <div className="px-4 pt-8 pb-20">
            <div className="w-full max-w-5xl mx-auto">
              <div className="flex items-center gap-3 mb-6">
                <span className="w-1.5 h-7 rounded-full" style={{ background: 'var(--accent-primary)' }} />
                <h2 className="text-xl sm:text-2xl font-bold text-primary">
                  {isZh ? '相关解决方案' : 'Related solutions'}
                </h2>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {related.map((r) => (
                  <Link
                    key={r.slug}
                    to={`/solutions/${r.slug}`}
                    className="group flex items-center gap-3 bg-card border border-border rounded-xl p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent-primary/40 hover:shadow-lg hover:shadow-accent-primary/10"
                  >
                    <span className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 bg-accent-primary/10 text-accent-primary">
                      <SolutionIcon name={r.icon} className="w-5 h-5" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-primary truncate group-hover:text-accent-primary transition-colors">
                        {r.navLabel[locale]}
                      </p>
                      <p className="text-xs text-secondary truncate">{r.subtitle[locale]}</p>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
