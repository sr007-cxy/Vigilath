import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { geoApi } from '../services/geoApi';
import { PaymentModal } from '../components/PaymentModal';
import { useMembership } from '../hooks/useMembership';

type AdvancedKey = 'compare' | 'crawlTest' | 'authority' | 'citation' | 'visibility' | 'entity';

const advancedCards: { key: AdvancedKey; icon: JSX.Element }[] = [
  {
    key: 'compare',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    ),
  },
  {
    key: 'crawlTest',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
    ),
  },
  {
    key: 'authority',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
    ),
  },
  {
    key: 'citation',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z" />
    ),
  },
  {
    key: 'visibility',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
    ),
  },
  {
    key: 'entity',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
    ),
  },
];

export function Home() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token, isLoggedIn, isUnlocked, refresh } = useMembership();
  const [url, setUrl] = useState('https://example.com');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const [comingSoonKey, setComingSoonKey] = useState<AdvancedKey | null>(null);

  const validateUrl = (input: string): boolean => {
    try {
      new URL(input);
      return true;
    } catch {
      return false;
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) {
      setError(t('home.error.empty'));
      return;
    }
    if (!validateUrl(url)) {
      setError(t('home.error.invalid'));
      return;
    }

    setIsLoading(true);
    setError('');

    geoApi
      .checkGeo({ url })
      .then((result) => {
        navigate('/result', { state: { result } });
      })
      .catch(() => {
        setError(t('home.error.failed'));
      })
      .finally(() => {
        setIsLoading(false);
      });
  };

  const handleAdvancedClick = (key: AdvancedKey) => {
    if (!isLoggedIn) {
      navigate('/login');
      return;
    }
    if (!isUnlocked) {
      setShowPaymentModal(true);
      return;
    }
    setComingSoonKey(key);
    window.setTimeout(() => setComingSoonKey(null), 2500);
  };

  return (
    <div className="min-h-screen grid-background">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          <section className="hero">
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight animate-slide-up text-center">
              <span className="gradient-text">{t('home.title')}</span>
            </h1>

            <p
              className="text-lg sm:text-xl text-secondary max-w-3xl mx-auto leading-relaxed animate-slide-up text-center"
              style={{ animationDelay: '0.1s' }}
            >
              {t('home.description')}
            </p>

            <div className="transition-all duration-300 animate-scale-in max-w-2xl mx-auto mt-10">
              <form id="geo-form" onSubmit={handleSubmit}>
                <div className="url-input-wrapper flex items-center bg-card border border-border rounded-full p-1.5 shadow-glow">
                  <input
                    type="text"
                    id="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder={t('home.placeholder')}
                    className="flex-1 py-3 px-4 text-base bg-transparent focus:outline-none text-primary placeholder-muted"
                    disabled={isLoading}
                  />
                  <button
                    type="submit"
                    disabled={isLoading}
                    className="gradient-bg text-white px-6 py-2.5 font-semibold hover:opacity-90 transition-all duration-300 flex items-center justify-center rounded-full disabled:opacity-60"
                  >
                    {isLoading ? (
                      <svg
                        className="animate-spin h-5 w-5"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth={4}
                        />
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                        />
                      </svg>
                    ) : (
                      t('home.button')
                    )}
                  </button>
                </div>

                {error && (
                  <div className="mt-4 bg-red-900/40 border border-red-800 rounded-xl p-4 flex items-center gap-3 animate-fade-in">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-5 w-5 text-red-400 flex-shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-.633-1.964-.633-2.732 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                      />
                    </svg>
                    <span className="text-sm text-red-300 font-medium">{error}</span>
                  </div>
                )}
              </form>

              <div className="mt-8 text-center">
                <p className="text-sm text-muted font-medium">{t('home.poweredBy')}</p>
                <p className="mt-2">
                  <a
                    href="/contact"
                    className="text-sm text-accent-primary font-medium hover:text-primary transition-colors duration-300"
                  >
                    {t('home.contactLink')}
                  </a>
                </p>
              </div>
            </div>
          </section>

          {/* Advanced Detection Section */}
          <section className="mt-24 sm:mt-32">
            <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-10">
              <div>
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent-primary/10 border border-accent-primary/20 mb-3">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-3.5 w-3.5 text-accent-primary"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                    />
                  </svg>
                  <span className="text-xs font-semibold text-accent-primary">
                    {t('home.advanced.badge')}
                  </span>
                </div>
                <h2 className="text-2xl sm:text-3xl font-bold gradient-text mb-2">
                  {t('home.advanced.title')}
                </h2>
                <p className="text-secondary text-sm sm:text-base max-w-2xl">
                  {t('home.advanced.subtitle')}
                </p>
              </div>
              {!isUnlocked && (
                <button
                  type="button"
                  onClick={() => (isLoggedIn ? setShowPaymentModal(true) : navigate('/login'))}
                  className="self-start sm:self-end gradient-bg text-white rounded-full py-2.5 px-5 text-sm font-semibold hover:opacity-90 transition-all duration-300 shadow-glow whitespace-nowrap"
                >
                  {t('home.advanced.upgrade')}
                </button>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
              {advancedCards.map(({ key, icon }) => (
                <div
                  type="button"
                  key={key}
                  onClick={() => handleAdvancedClick(key)}
                  className="group flex gap-4 relative text-left bg-gray-900 border border-gray-700 rounded-2xl p-6 transition-all duration-300 hover:border-primary/60 hover:-translate-y-1"
                >
                  {!isUnlocked && (
                    <div className="absolute top-4 right-4 w-8 h-8 rounded-full bg-accent-primary/10 border border-accent-primary/30 flex items-center justify-center">
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-4 w-4 text-accent-primary"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                        />
                      </svg>
                    </div>
                  )}
                  <div className="w-12 h-12 rounded-xl gradient-bg flex items-center justify-center mb-5 shadow-glow group-hover:scale-110 transition-transform duration-300">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-6 w-6 text-white"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      {icon}
                    </svg>
                  </div>
                  <div className='flex-1'>
                    <h3 className="text-lg font-bold text-primary mb-2">
                      {t(`home.advanced.cards.${key}.title`)}
                    </h3>
                    <p className="text-sm text-[#dedede] leading-relaxed m-0">
                      {t(`home.advanced.cards.${key}.desc`)}
                    </p>
                  </div>
                  {comingSoonKey === key && (
                    <div className="absolute inset-0 rounded-2xl bg-card/95 flex items-center justify-center animate-fade-in">
                      <span className="text-sm font-semibold text-accent-primary">
                        {t('home.advanced.comingSoon')}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>
      </main>

      {showPaymentModal && token && (
        <PaymentModal
          token={token}
          onClose={() => setShowPaymentModal(false)}
          onSuccess={() => {
            setShowPaymentModal(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}
