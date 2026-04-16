import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { geoApi, ApiError } from '../services/geoApi';
import { useContactModal } from '../components/ContactModalContext';
import { useTierModal } from '../components/TierModalContext';
import { CheckProgress } from '../components/result/CheckProgress';
import { useMembership } from '../hooks/useMembership';
import { useLoadNs } from '../i18n/useLoadNs';

type AdvancedKey = 'aeo' | 'compare' | 'crawlTest' | 'authority' | 'citation' | 'visibility' | 'entity';

const advancedCards: { key: AdvancedKey; icon: ReactNode }[] = [
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
  useLoadNs('result');
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isLoggedIn, isUnlocked } = useMembership();
  const { openContact } = useContactModal();
  const { openTierModal } = useTierModal();
  const [url, setUrl] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [quotaExceeded, setQuotaExceeded] = useState(false);

  const validateUrl = (input: string): boolean => {
    try {
      // 如果输入没有http/https前缀，自动添加https://
      const url = input.startsWith('http://') || input.startsWith('https://') ? input : `https://${input}`;
      new URL(url);
      return true;
    } catch {
      return false;
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setQuotaExceeded(false);
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

    // 处理URL前缀，确保API调用时使用正确的URL格式
    const formattedUrl = url.startsWith('http://') || url.startsWith('https://') ? url : `https://${url}`;

    geoApi
      .checkGeo({ url: formattedUrl })
      .then((result) => {
        navigate('/result', { state: { result } });
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 429) {
          setQuotaExceeded(true);
          setError(err.message || t('home.error.quotaExceeded'));
        } else {
          setError(err instanceof Error ? err.message : t('home.error.failed'));
        }
      })
      .finally(() => {
        setIsLoading(false);
      });
  };

  const handleAdvancedClick = (key: AdvancedKey) => {
    if (!isLoggedIn || !isUnlocked) {
      openTierModal();
      return;
    }
    // Member — jump straight to the Result page in "empty advanced" mode:
    // the rerun bar is centered, the dropdown is pre-selected to `key`, and
    // running it renders the result inline (no /advanced/{mode} hop).
    let initialUrl: string | undefined;
    if (url && validateUrl(url)) {
      initialUrl = url.startsWith('http://') || url.startsWith('https://') ? url : `https://${url}`;
    }
    navigate('/result', { state: { initialMode: key, initialUrl } });
  };

  return (
    <div className="min-h-screen grid-background">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 pt-24 pb-12 sm:py-28 relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          <section className="hero text-center">


            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-[1.05] tracking-tight animate-slide-up text-center">
              <span className="gradient-text">{t('home.title')}</span>
            </h1>

            <p
              className="text-base sm:text-lg text-secondary max-w-2xl mx-auto leading-relaxed animate-slide-up text-center"
              style={{ animationDelay: '0.1s' }}
            >
              {t('home.description')}
            </p>

            <div className="transition-all duration-300 animate-scale-in max-w-2xl mx-auto mt-8 sm:mt-10">
              <form id="geo-form" onSubmit={handleSubmit}>
                <div className="flex flex-col sm:flex-row sm:items-center bg-surface border border-soft rounded-2xl sm:rounded-full p-1.5 shadow-glow transition-shadow gap-1.5 sm:gap-0">
                  <input
                    type="text"
                    id="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder={t('home.placeholder')}
                    className="flex-1 py-3 px-4 sm:px-5 text-sm sm:text-base bg-transparent focus:outline-none text-primary border-none min-w-0"
                    style={{ color: 'var(--text-primary)' }}
                    disabled={isLoading}
                  />
                  <button
                    type="submit"
                    disabled={isLoading}
                    className="btn-solid px-6 py-3 font-semibold flex items-center justify-center rounded-xl sm:rounded-full disabled:opacity-60 text-sm sm:text-base"
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
                  <div
                    className="mt-4 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-3 animate-fade-in"
                    style={{
                      background: 'rgba(239, 68, 68, 0.08)',
                      border: '1px solid rgba(239, 68, 68, 0.25)',
                    }}
                  >
                    <div className="flex items-start gap-3 flex-1">
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-5 w-5 text-rose-500 flex-shrink-0 mt-0.5"
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
                      <span className="text-sm font-medium" style={{ color: '#ef4444' }}>{error}</span>
                    </div>
                    {quotaExceeded && (
                      <button
                        type="button"
                        onClick={openTierModal}
                        className="btn-solid inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-full text-sm font-semibold transition-colors duration-200 flex-shrink-0"
                      >
                        {t('home.error.quotaCta')}
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          className="h-4 w-4"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M17 8l4 4m0 0l-4 4m4-4H3"
                          />
                        </svg>
                      </button>
                    )}
                  </div>
                )}
              </form>

              <div className="mt-8 text-center">
                <p className="text-sm text-muted font-medium">{t('home.poweredBy')}</p>
                <p className="mt-2">
                  <button
                    type="button"
                    onClick={openContact}
                    className="text-sm font-medium underline underline-offset-4 transition-colors duration-200 bg-transparent border-none cursor-pointer"
                    style={{ color: 'var(--text-primary)', textDecorationColor: 'var(--border-strong)' }}
                  >
                    {t('home.contactLink')}
                  </button>
                </p>
              </div>
            </div>
          </section>

          {/* Advanced Detection Section */}
          <section className="mt-12 sm:mt-32">
            <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-6 sm:mb-10">
              <div>
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-surface border border-soft shadow-glow mb-3">
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
                      d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                    />
                  </svg>
                  <span className="text-xs font-semibold text-secondary">
                    {t('home.advanced.badge')}
                  </span>
                </div>
                <h2 className="text-2xl sm:text-3xl font-bold mb-2 tracking-tight">
                  <span className="gradient-text">{t('home.advanced.title')}</span>
                </h2>
                <p className="text-secondary text-sm sm:text-base max-w-2xl">
                  {t('home.advanced.subtitle')}
                </p>
              </div>
              {!isUnlocked && (
                <button
                  type="button"
                  onClick={openTierModal}
                  className="btn-solid self-start sm:self-end rounded-full py-2.5 px-5 text-sm font-semibold transition-all duration-200 whitespace-nowrap"
                >
                  {t('home.advanced.upgrade')}
                </button>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
              {advancedCards.map(({ key, icon }) => (
                <div
                  role="button"
                  key={key}
                  onClick={() => handleAdvancedClick(key)}
                  className="group flex gap-4 relative text-left card p-6 hover:-translate-y-0.5"
                >
                  {!isUnlocked && (
                    <div className="absolute top-4 right-4 w-8 h-8 rounded-full flex items-center justify-center bg-surface border border-soft">
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-4 w-4 text-secondary"
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
                  <div className="icon-tile w-12 h-12 rounded-xl flex items-center justify-center mb-5 group-hover:scale-105 transition-transform duration-200 flex-shrink-0">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-6 w-6"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      {icon}
                    </svg>
                  </div>
                  <div className='flex-1'>
                    <h3 className="text-lg font-bold mb-2 text-primary">
                      {t(`home.advanced.cards.${key}.title`)}
                    </h3>
                    <p className="text-sm text-secondary leading-relaxed m-0">
                      {t(`home.advanced.cards.${key}.desc`)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </main>

      {/* Slogan Banner */}
      <section
        className="relative z-10 px-4 py-16 sm:py-24"
        style={{ background: '#141418' }}
      >
        <div className="w-full max-w-5xl mx-auto">
          {/* Logo */}
          <img
            src="/image/logo.png"
            alt="GApex"
            className="h-12 sm:h-14 w-auto mb-10 select-none brightness-0 invert"
            draggable={false}
          />

          {/* Heading */}
          <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold mb-3 text-white">
            {t('home.slogan.sectionTitle')}
          </h2>

          {/* Subtitle */}
          <p className="text-base sm:text-lg italic mb-10" style={{ color: 'rgba(255,255,255,0.55)' }}>
            {t('home.slogan.title')}
          </p>

          {/* 2x2 Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-12">
            {(t('home.slogan.points', { returnObjects: true }) as string[]).map((point, idx) => {
              const sep = point.indexOf(' — ');
              const title = sep !== -1 ? point.slice(0, sep) : point;
              const desc = sep !== -1 ? point.slice(sep + 3) : '';
              return (
                <div
                  key={idx}
                  className="rounded-xl p-5 sm:p-6"
                  style={{ background: 'rgba(255,255,255,0.95)', color: '#141418' }}
                >
                  <h3 className="font-bold text-sm sm:text-base mb-1.5">{title}</h3>
                  {desc && <p className="text-xs sm:text-sm leading-relaxed" style={{ color: '#555' }}>{desc}</p>}
                </div>
              );
            })}
          </div>

          {/* CTA Row */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6">
            <p className="text-xl sm:text-2xl lg:text-3xl font-semibold italic gradient-text">
              {t('home.slogan.cta')}
            </p>
            <button
              type="button"
              onClick={openContact}
              className="inline-flex items-center gap-3 px-6 py-3 rounded-full border-2 border-white text-white font-semibold text-sm sm:text-base transition-colors duration-200 hover:bg-white hover:text-black flex-shrink-0"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <circle cx="12" cy="12" r="10" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 8l4 4m0 0l-4 4m4-4H8" />
              </svg>
              {t('home.slogan.contactSales')}
            </button>
          </div>
        </div>
      </section>

      {isLoading && <CheckProgress mode="default" />}
    </div>
  );
}
