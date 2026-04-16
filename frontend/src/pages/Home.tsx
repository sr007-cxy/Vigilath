import { useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { geoApi, ApiError } from '../services/geoApi';
import { AuthModal } from '../components/AuthModal';
import { CheckProgress } from '../components/result/CheckProgress';
import { useMembership } from '../hooks/useMembership';
import {
  membershipApi,
  type Membership,
  formatTierPrice,
} from '../services/membershipApi';

type AdvancedKey = 'compare' | 'crawlTest' | 'authority' | 'citation' | 'visibility' | 'entity';

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
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { isLoggedIn, isUnlocked, refresh } = useMembership();
  const [url, setUrl] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [quotaExceeded, setQuotaExceeded] = useState(false);
  const [showTierModal, setShowTierModal] = useState(false);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [pendingTierSlug, setPendingTierSlug] = useState<string | null>(null);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [tiersLoading, setTiersLoading] = useState(false);
  const [tiersError, setTiersError] = useState<string | null>(null);

  const tierText = (tier: Membership) => {
    const slugToCardKey: Record<string, string> = { pro: 'detector' };
    const cardKey = slugToCardKey[tier.slug] ?? tier.slug;
    const base = `productsServices.cards.${cardKey}`;
    const name = i18n.exists(`${base}.name`) ? (t(`${base}.name`) as string) : tier.name;
    const description = i18n.exists(`${base}.description`)
      ? (t(`${base}.description`) as string)
      : tier.description;
    const period = i18n.exists(`${base}.period`)
      ? (t(`${base}.period`) as string)
      : tier.period;
    const translatedFeatures = i18n.exists(`${base}.features`)
      ? (t(`${base}.features`, { returnObjects: true }) as unknown)
      : null;
    const features = Array.isArray(translatedFeatures)
      ? (translatedFeatures as string[])
      : tier.features;
    return { name, description, period, features };
  };

  const ctaLabelFor = (tier: Membership) => {
    if (tier.slug === 'free') return t('productsServices.cta.tryNow');
    if (tier.tier_type === 'saas') return t('productsServices.cta.subscribeNow');
    return t('productsServices.cta.contactSales');
  };

  const openTierModal = () => {
    if (memberships.length === 0 && !tiersLoading) {
      setTiersLoading(true);
      setTiersError(null);
      membershipApi
        .getMemberships()
        .then((data) => setMemberships(data))
        .catch((err) =>
          setTiersError(err instanceof Error ? err.message : t('common.errors.loadFailed')),
        )
        .finally(() => setTiersLoading(false));
    }
    setShowTierModal(true);
  };

  const handleTierCTA = (tier: Membership) => {
    if (tier.slug === 'free') {
      setShowTierModal(false);
      return;
    }
    if (tier.tier_type === 'saas') {
      if (!isLoggedIn) {
        setPendingTierSlug(tier.slug);
        setShowTierModal(false);
        setAuthModalOpen(true);
        return;
      }
      navigate(`/checkout/pending?slug=${encodeURIComponent(tier.slug)}`);
      return;
    }
    // Service tier — redirect to the products-services page for contact sales flow.
    setShowTierModal(false);
    navigate('/products-services');
  };

  const handleAuthSuccess = () => {
    refresh();
    if (pendingTierSlug) {
      navigate(`/checkout/pending?slug=${encodeURIComponent(pendingTierSlug)}`);
      setPendingTierSlug(null);
    }
  };

  useEffect(() => {
    if (!showTierModal) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = 'unset';
    };
  }, [showTierModal]);

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

      <main className="flex-1 px-4 py-20 sm:py-28 relative z-10">
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

            <div className="transition-all duration-300 animate-scale-in max-w-2xl mx-auto mt-10">
              <form id="geo-form" onSubmit={handleSubmit}>
                <div className="flex items-center bg-surface border border-soft rounded-full p-1.5 shadow-glow transition-shadow">
                  <input
                    type="text"
                    id="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder={t('home.placeholder')}
                    className="flex-1 py-3 px-5 text-base bg-transparent focus:outline-none text-primary border-none"
                    style={{ color: 'var(--text-primary)' }}
                    disabled={isLoading}
                  />
                  <button
                    type="submit"
                    disabled={isLoading}
                    className="btn-solid px-6 py-3 font-semibold flex items-center justify-center rounded-full disabled:opacity-60"
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
                      <Link
                        to="/products-services"
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
                      </Link>
                    )}
                  </div>
                )}
              </form>

              <div className="mt-8 text-center">
                <p className="text-sm text-muted font-medium">{t('home.poweredBy')}</p>
                <p className="mt-2">
                  <Link
                    to="/products-services"
                    className="text-sm font-medium underline underline-offset-4 transition-colors duration-200"
                    style={{ color: 'var(--text-primary)', textDecorationColor: 'var(--border-strong)' }}
                  >
                    {t('home.contactLink')}
                  </Link>
                </p>
              </div>
            </div>
          </section>

          {/* Advanced Detection Section */}
          <section className="mt-24 sm:mt-32">
            <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-10">
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

      {showTierModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 backdrop-blur-sm animate-fade-in"
          style={{ background: 'rgba(0, 0, 0, 0.6)' }}
          onClick={() => setShowTierModal(false)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="relative w-full max-w-6xl max-h-[90vh] overflow-y-auto bg-surface border border-soft rounded-2xl p-6 sm:p-8 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => setShowTierModal(false)}
              className="absolute top-4 right-4 w-8 h-8 rounded-full flex items-center justify-center text-secondary hover:bg-surface-hover transition-colors"
              style={{ color: 'var(--text-secondary)' }}
              aria-label={t('productsServices.closeAria')}
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-5 w-5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
            <div className="text-center mb-8 pr-10">
              <h2 className="text-2xl md:text-3xl font-bold mb-2 tracking-tight">
                <span className="gradient-text">{t('home.advanced.tierModal.title')}</span>
              </h2>
              <p className="text-sm text-secondary">
                {t('home.advanced.tierModal.subtitle')}
              </p>
            </div>

            {tiersLoading && (
              <div className="py-12 text-center text-secondary">
                {t('productsServices.loadingMemberships')}
              </div>
            )}
            {tiersError && (
              <div className="py-12 text-center" style={{ color: '#ef4444' }}>{tiersError}</div>
            )}

            {!tiersLoading && !tiersError && memberships.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 items-stretch">
                {memberships.map((tier) => {
                  const text = tierText(tier);
                  return (
                    <div
                      key={tier.id}
                      className={`relative h-full card p-5 flex flex-col ${
                        tier.popular ? 'shadow-glow' : ''
                      }`}
                      style={
                        tier.popular
                          ? { borderColor: 'var(--accent-primary)' }
                          : undefined
                      }
                    >
                      {tier.popular && (
                        <div
                          className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full text-[10px] uppercase tracking-[0.2em] font-semibold whitespace-nowrap"
                          style={{
                            background: 'var(--solid-btn-bg)',
                            color: 'var(--solid-btn-text)',
                          }}
                        >
                          {t('productsServices.cta.popular')}
                        </div>
                      )}
                      <h3 className="text-lg font-bold mb-2 min-h-[1.75rem] text-primary">{text.name}</h3>
                      <div className="flex items-baseline gap-1 mb-3 min-h-[2.5rem]">
                        <span className="text-3xl font-bold text-primary">
                          {tier.slug === 'scale'
                            ? t('productsServices.cards.scale.getDemoPrice')
                            : formatTierPrice(tier)}
                        </span>
                        {tier.slug !== 'scale' && tier.tier_type === 'saas' && text.period && (
                          <span className="text-sm text-secondary font-medium">{text.period}</span>
                        )}
                        {tier.slug !== 'scale' && tier.tier_type === 'service' && (
                          <span className="text-xs text-secondary font-medium ml-1">
                            {t('productsServices.perProject')}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-secondary leading-relaxed mb-4 line-clamp-3 min-h-[3.4rem]">
                        {text.description}
                      </p>
                      <ul className="space-y-2 mb-6 flex-1">
                        {text.features.slice(0, 5).map((feature, idx) => (
                          <li key={idx} className="flex items-start gap-2 text-xs text-primary">
                            <svg
                              className="w-4 h-4 shrink-0 mt-0.5"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              style={{ color: 'var(--accent-primary)' }}
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth="2.5"
                                d="M5 13l4 4L19 7"
                              />
                            </svg>
                            <span className="leading-snug">{feature}</span>
                          </li>
                        ))}
                      </ul>
                      <div className="mt-auto">
                        <button
                          type="button"
                          onClick={() => handleTierCTA(tier)}
                          className={`w-full justify-center py-3 text-sm font-semibold rounded-lg transition-colors duration-200 ${
                            tier.popular ? 'btn-solid' : 'btn-secondary'
                          }`}
                        >
                          {ctaLabelFor(tier)}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      <AuthModal
        isOpen={authModalOpen}
        onClose={() => {
          setAuthModalOpen(false);
          setPendingTierSlug(null);
        }}
        defaultTab="login"
        onSuccess={handleAuthSuccess}
      />

      {isLoading && <CheckProgress mode="default" />}
    </div>
  );
}
