import { useTranslation } from 'react-i18next';
import { PageHead } from '../components/PageHead';

export function About() {
  const { t } = useTranslation();

  return (
    <div className="min-h-screen grid-background">
      <PageHead titleKey="pageMeta.about.title" descriptionKey="pageMeta.about.description" />
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          {/* Hero Section */}
          <section className="hero mb-16">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-2xl sm:text-4xl lg:text-5xl font-bold mb-6 leading-[1.05] tracking-tight animate-slide-up">
                <span className="gradient-text">{t('aboutUs.title')}</span>
              </h1>
              <p className="text-lg sm:text-xl text-secondary max-w-3xl mx-auto leading-relaxed animate-slide-up" style={{ animationDelay: '0.1s' }}>
                {t('aboutUs.description')}
              </p>
            </div>
          </section>

          {/* Our Story Section */}
          <section className="mb-20 animate-fade-in" style={{ animationDelay: '0.2s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center gradient-text">
              {t('aboutUs.story.title')}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-12 items-center">
              <div>
                <h3 className="text-2xl font-bold mb-6 gradient-text">
                  {t('aboutUs.story.subtitle')}
                </h3>
                <p className="text-secondary mb-6 leading-relaxed">
                  {t('aboutUs.story.paragraph1')}
                </p>
                <p className="text-secondary mb-6 leading-relaxed">
                  {t('aboutUs.story.paragraph2')}
                </p>
                <p className="text-secondary leading-relaxed">
                  {t('aboutUs.story.paragraph3')}
                </p>
              </div>
              {/* This used to show an image straight from Trae's text_to_image API; that URL is dead
                  (returns 301 + a 2-byte JSON), so the page showed a broken image. Replaced with a brand visual block that depends on no
                  external service; to use real team photos, put the images in
                  frontend/public/image/ and replace this section. */}
              <div className="rounded-2xl overflow-hidden shadow-lg shadow-accent-primary/10 border border-border">
                <div className="relative aspect-[16/9] bg-tertiary flex items-center justify-center">
                  <div
                    className="absolute inset-0 opacity-70"
                    style={{
                      backgroundImage:
                        'radial-gradient(circle at 20% 20%, var(--accent-primary), transparent 55%), radial-gradient(circle at 80% 70%, var(--accent-secondary), transparent 55%)',
                      opacity: 0.18,
                    }}
                  />
                  <div
                    className="absolute inset-0"
                    style={{
                      backgroundImage:
                        'linear-gradient(var(--border-color) 1px, transparent 1px), linear-gradient(90deg, var(--border-color) 1px, transparent 1px)',
                      backgroundSize: '32px 32px',
                      opacity: 0.35,
                    }}
                  />
                  <img
                    src="/image/logo.webp"
                    alt="Vigilath"
                    className="relative w-28 md:w-36 opacity-80"
                    loading="lazy"
                  />
                </div>
              </div>
            </div>
          </section>

          {/* Our Mission Section */}
          <section className="mb-20 animate-fade-in" style={{ animationDelay: '0.3s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center gradient-text">
              {t('aboutUs.mission.title')}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <div className="bg-card border border-border rounded-2xl p-8 text-center hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1">
                <div className="w-16 h-16 bg-accent-primary/10 rounded-full flex items-center justify-center mx-auto mb-6">
                  <svg className="w-8 h-8 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z"></path>
                  </svg>
                </div>
                <h3 className="text-xl font-bold mb-4 gradient-text">{t('aboutUs.mission.innovation.title')}</h3>
                <p className="text-secondary">
                  {t('aboutUs.mission.innovation.description')}
                </p>
              </div>
              <div className="bg-card border border-border rounded-2xl p-8 text-center hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1">
                <div className="w-16 h-16 bg-accent-secondary/10 rounded-full flex items-center justify-center mx-auto mb-6">
                  <svg className="w-8 h-8 text-accent-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path>
                  </svg>
                </div>
                <h3 className="text-xl font-bold mb-4 gradient-text">{t('aboutUs.mission.value.title')}</h3>
                <p className="text-secondary">
                  {t('aboutUs.mission.value.description')}
                </p>
              </div>
              <div className="bg-card border border-border rounded-2xl p-8 text-center hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1">
                <div className="w-16 h-16 bg-purple-500/10 rounded-full flex items-center justify-center mx-auto mb-6">
                  <svg className="w-8 h-8 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path>
                  </svg>
                </div>
                <h3 className="text-xl font-bold mb-4 gradient-text">{t('aboutUs.mission.leadership.title')}</h3>
                <p className="text-secondary">
                  {t('aboutUs.mission.leadership.description')}
                </p>
              </div>
            </div>
          </section>

          {/* Contact Section */}
          <section id="contact-form" className="animate-fade-in" style={{ animationDelay: '0.5s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center gradient-text">
              {t('aboutUs.contact.title')}
            </h2>
            <div className="max-w-2xl mx-auto">
              <div className="bg-card border border-border rounded-2xl p-8">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                  <div className="flex items-start gap-4">
                    <svg className="w-6 h-6 text-accent-primary shrink-0 mt-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                    </svg>
                    <div>
                      <h4 className="text-lg font-semibold mb-2 gradient-text">联系邮箱</h4>
                      <p className="text-secondary">
                        <a href="mailto:<support-email>" className="text-accent-primary hover:underline">{'<support-email>'}</a>
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <svg className="w-6 h-6 text-accent-primary shrink-0 mt-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498A1 1 0 0121 15.72V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
                    </svg>
                    <div>
                      <h4 className="text-lg font-semibold mb-2 gradient-text">客服热线</h4>
                      <p className="text-secondary">
                        <a href="tel:<support-phone>" className="text-accent-primary hover:underline">{'<support-phone>'}</a>
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
