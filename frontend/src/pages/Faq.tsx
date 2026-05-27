import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../components/PageHead';

export function Faq() {
  const { t } = useTranslation();
  const [openFaq, setOpenFaq] = useState<Set<number>>(new Set());
  const toggleFaq = (idx: number) =>
    setOpenFaq((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });

  return (
    <div className="min-h-screen grid-background">
      <PageHead titleKey="pageMeta.faq.title" descriptionKey="pageMeta.faq.description" />
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-3xl mx-auto animate-fade-in">
          <section className="hero mb-10">
            <div className="text-center">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-surface border border-soft shadow-glow mb-4">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-3.5 w-3.5 text-secondary"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093M12 17h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                  />
                </svg>
                <span className="text-xs font-semibold text-secondary">
                  {t('home.faq.badge')}
                </span>
              </div>
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight animate-slide-up">
                <span className="gradient-text">{t('home.faq.title')}</span>
              </h1>
              <p className="text-lg sm:text-xl text-secondary leading-relaxed animate-slide-up" style={{ animationDelay: '0.1s' }}>
                {t('home.faq.subtitle')}
              </p>
            </div>
          </section>

          <section className="space-y-3">
            {(t('home.faq.items', { returnObjects: true }) as { q: string; a: string }[]).map(
              (item, idx) => {
                const isOpen = openFaq.has(idx);
                return (
                  <div
                    key={idx}
                    className="card overflow-hidden"
                  >
                    <button
                      type="button"
                      onClick={() => toggleFaq(idx)}
                      aria-expanded={isOpen}
                      className="w-full flex items-center justify-between gap-4 px-5 sm:px-6 py-4 sm:py-5 text-left transition-colors hover:bg-surface-hover"
                    >
                      <span className="text-sm sm:text-base font-semibold text-primary leading-snug">
                        {item.q}
                      </span>
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-4 w-4 flex-shrink-0 transition-transform duration-200 text-secondary"
                        style={{ transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </button>
                    <div
                      className="grid transition-[grid-template-rows] duration-300 ease-out"
                      style={{ gridTemplateRows: isOpen ? '1fr' : '0fr' }}
                    >
                      <div className="overflow-hidden">
                        <p className="px-5 sm:px-6 pb-4 sm:pb-5 text-sm sm:text-base text-secondary leading-relaxed">
                          {item.a}
                        </p>
                      </div>
                    </div>
                  </div>
                );
              },
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
