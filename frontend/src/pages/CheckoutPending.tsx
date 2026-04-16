import { useEffect, useMemo, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { membershipApi, type Membership, formatTierPrice } from '../services/membershipApi';
import { paymentApi } from '../services/paymentApi';
import { useMembership } from '../hooks/useMembership';
import { useTierModal } from '../components/TierModalContext';

type PayMethod = 'stripe' | 'usdc';

export function CheckoutPending() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { openTierModal } = useTierModal();
  const [params] = useSearchParams();
  const { token, isLoggedIn } = useMembership();

  const slug = params.get('slug') ?? '';

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

  const [tier, setTier] = useState<Membership | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [payError, setPayError] = useState<string | null>(null);
  const [payMethod, setPayMethod] = useState<PayMethod>('stripe');

  // USDC state
  const [usdcPaymentId, setUsdcPaymentId] = useState<number | null>(null);
  const [usdcServiceId, setUsdcServiceId] = useState<string>('');
  const [usdcAmount, setUsdcAmount] = useState<number>(0);
  const [usdcWallet, setUsdcWallet] = useState<string>('');
  const [usdcPolling, setUsdcPolling] = useState(false);
  const [usdcStatus, setUsdcStatus] = useState<string>('');

  useEffect(() => {
    if (!isLoggedIn) {
      navigate('/login', {
        state: { from: `/checkout/pending?slug=${encodeURIComponent(slug)}` },
      });
      return;
    }
    if (!slug) {
      setLoadError(t('checkoutPending.missingSlug'));
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    membershipApi
      .getMemberships()
      .then((list) => {
        if (cancelled) return;
        const found = list.find((m) => m.slug === slug);
        if (!found) {
          setLoadError(t('checkoutPending.notFound'));
        } else if (found.tier_type !== 'saas') {
          setLoadError(t('checkoutPending.notSubscribable'));
        } else {
          setTier(found);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : t('common.errors.loadFailed'));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => { cancelled = true; };
  }, [slug, isLoggedIn, navigate, t]);

  const tierInfo = useMemo(() => (tier ? tierText(tier) : null), [tier, i18n.language]);
  const features = useMemo(() => tierInfo?.features.slice(0, 6) ?? [], [tierInfo]);

  const handleStripePay = async () => {
    if (!tier || !token || submitting) return;
    setSubmitting(true);
    setPayError(null);
    try {
      const { checkout_url } = await paymentApi.createStripeCheckoutSession(token, tier.slug, i18n.language);
      window.location.href = checkout_url;
    } catch (err) {
      setPayError(err instanceof Error ? err.message : t('checkoutPending.payError'));
      setSubmitting(false);
    }
  };

  const handleUsdcPay = async () => {
    if (!tier || !token || submitting) return;
    setSubmitting(true);
    setPayError(null);
    try {
      const data = await paymentApi.createMoltsPaySession(token, tier.slug);
      setUsdcPaymentId(data.payment_id);
      setUsdcServiceId(data.service_id);
      setUsdcAmount(data.amount_usdc);
      setUsdcWallet(data.wallet_address);
      setUsdcStatus('pending');
      setUsdcPolling(true);
    } catch (err) {
      setPayError(err instanceof Error ? err.message : t('checkoutPending.payError'));
    } finally {
      setSubmitting(false);
    }
  };

  // Poll USDC payment status
  useEffect(() => {
    if (!usdcPolling || !usdcPaymentId || !token) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const data = await paymentApi.getMoltsPayStatus(token, usdcPaymentId);
        if (cancelled) return;
        setUsdcStatus(data.status);
        if (data.status === 'paid') {
          setUsdcPolling(false);
          navigate('/checkout/success?provider=moltspay');
        }
      } catch {
        // ignore polling errors
      }
    }, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [usdcPolling, usdcPaymentId, token, navigate]);

  const handlePay = payMethod === 'stripe' ? handleStripePay : handleUsdcPay;

  const copied = useCallback(() => {
    navigator.clipboard.writeText(usdcWallet);
  }, [usdcWallet]);

  return (
    <div className="min-h-[60vh] flex items-center justify-center px-6 py-16">
      <div className="max-w-xl w-full bg-card border border-border rounded-2xl p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-accent-primary/10 flex items-center justify-center">
            <svg className="w-5 h-5 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-bold text-primary">{t('checkoutPending.title')}</h1>
            <p className="text-xs text-secondary">{t('checkoutPending.subtitle')}</p>
          </div>
        </div>

        {isLoading && (
          <div className="py-12 text-center text-sm text-secondary">{t('checkoutPending.loading')}</div>
        )}

        {!isLoading && loadError && (
          <div className="py-8">
            <p className="text-sm text-rose-400 mb-6">{loadError}</p>
            <button
              type="button"
              onClick={openTierModal}
              className="w-full justify-center btn-primary !py-3"
            >
              {t('checkoutPending.backToPlans')}
            </button>
          </div>
        )}

        {!isLoading && tier && (
          <>
            {/* Plan info */}
            <div className="rounded-xl border border-border p-5 mb-5 bg-[rgba(255,255,255,0.02)]">
              <div className="flex items-start justify-between gap-4 mb-3">
                <div>
                  <div className="text-sm text-secondary">{t('checkoutPending.planLabel')}</div>
                  <div className="text-lg font-bold text-primary mt-0.5">{tierInfo?.name || tier.name}</div>
                </div>
                {tier.popular && (
                  <span className="px-2 py-0.5 rounded-full bg-accent-primary/10 text-accent-primary text-[10px] font-semibold uppercase tracking-wider">
                    {t('checkoutPending.popular')}
                  </span>
                )}
              </div>
              <p className="text-xs text-secondary leading-relaxed mb-4">{tierInfo?.description || tier.description}</p>
              {features.length > 0 && (
                <ul className="space-y-2 mb-4">
                  {features.map((f, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-primary">
                      <svg className="w-4 h-4 text-accent-primary shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="leading-snug">{f}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex items-end justify-between pt-4 border-t border-border">
                <span className="text-sm text-secondary">{t('checkoutPending.totalLabel')}</span>
                <span className="text-2xl font-bold text-accent-primary">
                  {formatTierPrice(tier)}
                  <span className="text-sm text-secondary font-medium ml-1">{tierInfo?.period || tier.period}</span>
                </span>
              </div>
            </div>

            {/* Payment method selector */}
            <div className="mb-5">
              <div className="flex items-center gap-2 mb-2">
                <svg className="w-4 h-4 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                <span className="text-xs font-semibold text-primary">{t('checkoutPending.methodLabel')}</span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setPayMethod('stripe')}
                  disabled={usdcPolling}
                  className={`rounded-lg border p-3 text-left transition-all ${
                    payMethod === 'stripe'
                      ? 'border-accent-primary bg-accent-primary/5'
                      : 'border-border hover:border-accent-primary/50'
                  }`}
                >
                  <div className="text-sm font-semibold text-primary">💳 {t('checkoutPending.methodStripeLabel', 'Credit Card')}</div>
                  <div className="text-[10px] text-secondary mt-0.5">Stripe</div>
                </button>
                <button
                  type="button"
                  onClick={() => setPayMethod('usdc')}
                  disabled={usdcPolling}
                  className={`rounded-lg border p-3 text-left transition-all ${
                    payMethod === 'usdc'
                      ? 'border-accent-primary bg-accent-primary/5'
                      : 'border-border hover:border-accent-primary/50'
                  }`}
                >
                  <div className="text-sm font-semibold text-primary">💰 USDC</div>
                  <div className="text-[10px] text-secondary mt-0.5">Base Chain</div>
                </button>
              </div>
            </div>

            {/* Stripe info */}
            {payMethod === 'stripe' && !usdcPolling && (
              <div className="rounded-lg bg-[rgba(255,255,255,0.02)] border border-border p-4 mb-5">
                <p className="text-xs text-secondary leading-relaxed">{t('checkoutPending.methodStripe')}</p>
              </div>
            )}

            {/* USDC info */}
            {payMethod === 'usdc' && !usdcPolling && !usdcPaymentId && (
              <div className="rounded-lg bg-[rgba(255,255,255,0.02)] border border-border p-4 mb-5">
                <p className="text-xs text-secondary leading-relaxed">
                  {t('checkoutPending.methodUsdc', 'Pay with USDC on Base chain. Click "Pay Now" to generate a payment session, then complete it with MoltsPay CLI or any x402-compatible wallet.')}
                </p>
              </div>
            )}

            {/* USDC waiting panel */}
            {usdcPolling && usdcPaymentId && (
              <div className="rounded-lg bg-accent-primary/5 border border-accent-primary/30 p-5 mb-5">
                <div className="text-sm font-semibold text-primary mb-3">
                  {t('checkoutPending.usdcWaiting', 'Waiting for USDC payment...')}
                </div>
                <div className="space-y-2 text-xs">
                  <div className="flex justify-between">
                    <span className="text-secondary">{t('checkoutPending.usdcAmount', 'Amount')}</span>
                    <span className="font-mono text-primary">{usdcAmount} USDC</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-secondary">{t('checkoutPending.usdcChain', 'Chain')}</span>
                    <span className="font-mono text-primary">Base</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-secondary">{t('checkoutPending.usdcWallet', 'Wallet')}</span>
                    <span className="font-mono text-primary text-[10px] flex items-center gap-1">
                      {usdcWallet.slice(0, 8)}...{usdcWallet.slice(-6)}
                      <button onClick={copied} className="text-accent-primary hover:underline text-[10px]">
                        {t('checkoutPending.usdcCopy', 'Copy')}
                      </button>
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-secondary">{t('checkoutPending.usdcStatus', 'Status')}</span>
                    <span className="text-yellow-400 font-medium">
                      {usdcStatus === 'paid' ? '✅ Paid' : '⏳ ' + t('checkoutPending.usdcPending', 'Pending')}
                    </span>
                  </div>
                </div>
                <div className="mt-4 p-3 rounded-lg bg-[rgba(0,0,0,0.2)] text-[10px] font-mono text-secondary break-all">
                  moltspay pay https://www.vigilath.com/pay {usdcServiceId} --chain base user_id={'{user_id}'} membership_slug={slug}
                </div>
                <p className="text-[10px] text-secondary mt-2">
                  {t('checkoutPending.usdcHint', 'Use MoltsPay CLI or any x402-compatible client to complete the payment.')}
                </p>
              </div>
            )}

            {payError && (
              <div className="mb-4 text-xs px-4 py-3 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/30">
                {payError}
              </div>
            )}

            <div className="flex flex-col sm:flex-row gap-3">
              {!usdcPolling && (
                <button
                  type="button"
                  onClick={handlePay}
                  disabled={submitting}
                  className="flex-1 justify-center btn-primary !py-3 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {submitting
                    ? t('checkoutPending.submitting')
                    : t('checkoutPending.payNow')}
                </button>
              )}
              <button
                type="button"
<<<<<<< HEAD
                onClick={handlePay}
                disabled={submitting}
                className="flex-1 justify-center btn-primary !py-3 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {submitting
                  ? t('checkoutPending.submitting', '正在跳转支付…')
                  : t('checkoutPending.payNow', '立即支付')}
              </button>
              <button
                type="button"
                onClick={openTierModal}
=======
                onClick={() => { setUsdcPolling(false); navigate('/products-services'); }}
>>>>>>> 72f4216 (feat(payment): 集成 MoltsPay USDC 支付（Base 链 x402 协议）)
                disabled={submitting}
                className="px-5 py-3 rounded-lg border border-border text-secondary hover:text-primary hover:bg-border/40 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {t('checkoutPending.cancel')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
