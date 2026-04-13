import { useTranslation } from 'react-i18next';

interface Service {
  title: string;
  price: string;
  description: string;
  features: string[];
  button: string;
}

export function ProductsServices() {
  const { t } = useTranslation();
  const services = t('productsServices.services', { returnObjects: true }) as Service[];

  return (
    <div className="min-h-screen grid-background">
      {/* 背景发光效果 */}
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      {/* 主内容 */}
      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          {/* Hero Section */}
          <section className="hero mb-20">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight animate-slide-up">
                <span className="gradient-text">{t('productsServices.title')}</span>
              </h1>
              <p className="text-lg sm:text-xl text-secondary max-w-3xl mx-auto leading-relaxed animate-slide-up" style={{ animationDelay: '0.1s' }}>
                {t('productsServices.description')}
              </p>
            </div>
          </section>

          {/* Services Section */}
          <section className="mb-20 animate-fade-in" style={{ animationDelay: '0.2s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center">
              <span className="gradient-text">{t('productsServices.sections.ourServices')}</span>
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                {services.map((service: Service, index: number) => (
                  <div key={index} className="bg-card border border-border rounded-2xl p-8 hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 flex flex-col">
                    <div className="flex-grow">
                      <h3 className="text-xl font-bold mb-4">{service.title}</h3>
                      <div className="text-2xl font-bold text-accent-primary mb-6">{service.price}</div>
                      <p className="text-secondary mb-6 leading-relaxed">{service.description}</p>
                      <ul className="space-y-3 mb-8">
                        {service.features.map((feature: string, i: number) => (
                          <li key={i} className="flex items-start gap-3">
                            <svg className="w-5 h-5 text-accent-primary shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"></path>
                            </svg>
                            <span className="text-secondary">{feature}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                    <button className="w-full py-3 rounded-lg bg-accent-primary text-white font-semibold hover:bg-accent-primary/80 transition-colors duration-300">
                      {service.button}
                    </button>
                  </div>
                ))}
              </div>
          </section>

          {/* Contact Section */}
          <section className="animate-fade-in" style={{ animationDelay: '0.3s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center">
              <span className="gradient-text">{t('productsServices.sections.contactConsultation')}</span>
            </h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-10">
              <div className="bg-card border border-border rounded-2xl p-8">
                <h3 className="text-xl font-bold mb-6">{t('productsServices.contact.getCustomPlan')}</h3>
                <form className="space-y-6">
                  <div>
                    <label htmlFor="name" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.name')}
                    </label>
                    <input
                      type="text"
                      id="name"
                      name="name"
                      required
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200"
                      placeholder={t('productsServices.contact.namePlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="email" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.email')}
                    </label>
                    <input
                      type="email"
                      id="email"
                      name="email"
                      required
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200"
                      placeholder={t('productsServices.contact.emailPlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="website" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.website')}
                    </label>
                    <input
                      type="url"
                      id="website"
                      name="website"
                      required
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200"
                      placeholder={t('productsServices.contact.websitePlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="service" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.service')}
                    </label>
                    <select
                      id="service"
                      name="service"
                      className="w-full px-4 py-3 rounded-lg bg-card text-white focus:outline-none transition-colors duration-200"
                    >
                      <option value="basic">{t('productsServices.services.0.title')}</option>
                      <option value="advanced">{t('productsServices.services.1.title')}</option>
                      <option value="custom">{t('productsServices.services.2.title')}</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="message" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.message')}
                    </label>
                    <textarea
                      id="message"
                      name="message"
                      rows={5}
                      required
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200 resize-none"
                      placeholder={t('productsServices.contact.messagePlaceholder')}
                    ></textarea>
                  </div>
                  <button
                    type="submit"
                    className="w-full py-3.5 rounded-lg bg-accent-primary text-white font-semibold shadow-lg shadow-accent-primary/25 hover:shadow-xl hover:shadow-accent-primary/30 hover:-translate-y-0.5 transition-all duration-300"
                  >
                    {t('productsServices.contact.submit')}
                  </button>
                </form>
              </div>
              <div className="flex flex-col justify-center">
                <h3 className="text-xl font-bold mb-6">{t('productsServices.contact.contactUs')}</h3>
                <p className="text-secondary mb-8 leading-relaxed">
                  {t('productsServices.contact.contactText')}
                </p>
                <div className="space-y-4">
                  <div className="flex items-start gap-4">
                    <div className="w-12 h-12 rounded-xl bg-accent-primary/20 flex items-center justify-center shrink-0">
                      <svg className="w-5 h-5 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
                      </svg>
                    </div>
                    <div>
                      <p className="text-sm text-muted mb-1">{t('productsServices.contact.emailLabel')}</p>
                      <a href="mailto:contact@zen7geo.com" className="text-primary font-semibold hover:text-accent-primary transition-colors duration-200">
                        contact@zen7geo.com
                      </a>
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <div className="w-12 h-12 rounded-xl bg-accent-primary/20 flex items-center justify-center shrink-0">
                      <svg className="w-5 h-5 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"></path>
                      </svg>
                    </div>
                    <div>
                      <p className="text-sm text-muted mb-1">{t('productsServices.contact.phoneLabel')}</p>
                      <a href="tel:+8610123456789" className="text-primary font-semibold hover:text-accent-primary transition-colors duration-200">
                        +86 10 12345 6789
                      </a>
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
