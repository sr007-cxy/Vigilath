import { useTranslation } from 'react-i18next';

export function About() {
  const { t } = useTranslation();

  return (
    <div className="min-h-screen grid-background">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          {/* Hero Section */}
          <section className="hero mb-16">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight animate-slide-up">
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
              <div className="rounded-2xl overflow-hidden shadow-lg shadow-accent-primary/10">
                <img
                  src="https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=modern%20office%20space%20with%20tech%20team%20working%20on%20computers%2C%20bright%20and%20professional%20environment&image_size=landscape_16_9"
                  alt="Our Team"
                  className="w-full h-auto object-cover"
                />
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

          {/* Our Team Section */}
          <section className="mb-20 animate-fade-in" style={{ animationDelay: '0.4s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center gradient-text">
              {t('aboutUs.team.title')}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
              <div className="bg-card border border-border rounded-2xl overflow-hidden hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1">
                <img
                  src="https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=professional%20asian%20male%20CEO%20headshot%2C%20confident%20expression%2C%20business%20attire&image_size=square"
                  alt="CEO"
                  className="w-full h-64 object-cover"
                />
                <div className="p-6">
                  <h3 className="text-xl font-bold mb-2 gradient-text">{t('aboutUs.team.ceo.name')}</h3>
                  <p className="text-accent-primary font-semibold mb-4">{t('aboutUs.team.ceo.title')}</p>
                  <p className="text-secondary">
                    {t('aboutUs.team.ceo.description')}
                  </p>
                </div>
              </div>
              <div className="bg-card border border-border rounded-2xl overflow-hidden hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1">
                <img
                  src="https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=professional%20asian%20female%20CTO%20headshot%2C%20smart%20expression%2C%20business%20attire&image_size=square"
                  alt="CTO"
                  className="w-full h-64 object-cover"
                />
                <div className="p-6">
                  <h3 className="text-xl font-bold mb-2 gradient-text">{t('aboutUs.team.cto.name')}</h3>
                  <p className="text-accent-primary font-semibold mb-4">{t('aboutUs.team.cto.title')}</p>
                  <p className="text-secondary">
                    {t('aboutUs.team.cto.description')}
                  </p>
                </div>
              </div>
              <div className="bg-card border border-border rounded-2xl overflow-hidden hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1">
                <img
                  src="https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=professional%20asian%20male%20marketing%20director%20headshot%2C%20friendly%20expression%2C%20business%20attire&image_size=square"
                  alt="Marketing Director"
                  className="w-full h-64 object-cover"
                />
                <div className="p-6">
                  <h3 className="text-xl font-bold mb-2 gradient-text">{t('aboutUs.team.marketing.name')}</h3>
                  <p className="text-accent-primary font-semibold mb-4">{t('aboutUs.team.marketing.title')}</p>
                  <p className="text-secondary">
                    {t('aboutUs.team.marketing.description')}
                  </p>
                </div>
              </div>
              <div className="bg-card border border-border rounded-2xl overflow-hidden hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 hover:-translate-y-1">
                <img
                  src="https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=professional%20asian%20female%20designer%20headshot%2C%20creative%20expression%2C%20business%20attire&image_size=square"
                  alt="Design Director"
                  className="w-full h-64 object-cover"
                />
                <div className="p-6">
                  <h3 className="text-xl font-bold mb-2 gradient-text">{t('aboutUs.team.design.name')}</h3>
                  <p className="text-accent-primary font-semibold mb-4">{t('aboutUs.team.design.title')}</p>
                  <p className="text-secondary">
                    {t('aboutUs.team.design.description')}
                  </p>
                </div>
              </div>
            </div>
          </section>

          {/* Contact Section */}
          <section id="contact-form" className="animate-fade-in" style={{ animationDelay: '0.5s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center gradient-text">
              {t('aboutUs.contact.title')}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-10">
              <div className="bg-card border border-border rounded-2xl p-8">
                <h3 className="text-xl font-bold mb-6 gradient-text">
                  {t('aboutUs.contact.info.title')}
                </h3>
                <div className="space-y-6">
                  <div className="flex items-start gap-4">
                    <svg className="w-6 h-6 text-accent-primary shrink-0 mt-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"></path>
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"></path>
                    </svg>
                    <div>
                      <h4 className="text-lg font-semibold mb-2 gradient-text">{t('aboutUs.contact.info.address')}</h4>
                      <p className="text-secondary">
                        {t('aboutUs.contact.info.addressValue')}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <svg className="w-6 h-6 text-accent-primary shrink-0 mt-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
                    </svg>
                    <div>
                      <h4 className="text-lg font-semibold mb-2 gradient-text">{t('aboutUs.contact.info.email')}</h4>
                      <p className="text-secondary">
                        {t('aboutUs.contact.info.emailValue')}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <svg className="w-6 h-6 text-accent-primary shrink-0 mt-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"></path>
                    </svg>
                    <div>
                      <h4 className="text-lg font-semibold mb-2 gradient-text">{t('aboutUs.contact.info.phone')}</h4>
                      <p className="text-secondary">
                        {t('aboutUs.contact.info.phoneValue')}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
              <div className="bg-card border border-border rounded-2xl p-8">
                <h3 className="text-xl font-bold mb-6 gradient-text">
                  {t('aboutUs.contact.form.title')}
                </h3>
                <form className="space-y-6">
                  <div>
                    <label htmlFor="name" className="block text-sm font-semibold mb-2">
                      {t('aboutUs.contact.form.name')}
                    </label>
                    <input
                      type="text"
                      id="name"
                      className="w-full input-field"
                      placeholder={t('aboutUs.contact.form.namePlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="email" className="block text-sm font-semibold mb-2">
                      {t('aboutUs.contact.form.email')}
                    </label>
                    <input
                      type="email"
                      id="email"
                      className="w-full input-field"
                      placeholder={t('aboutUs.contact.form.emailPlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="message" className="block text-sm font-semibold mb-2">
                      {t('aboutUs.contact.form.message')}
                    </label>
                    <textarea
                      id="message"
                      rows={5}
                      className="w-full input-field resize-none"
                      placeholder={t('aboutUs.contact.form.messagePlaceholder')}
                    ></textarea>
                  </div>
                  <button
                    type="submit"
                    className="w-full btn-primary"
                  >
                    {t('aboutUs.contact.form.button')}
                  </button>
                </form>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
