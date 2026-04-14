import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { membershipApi, type Membership } from '../services/membershipApi';
import { paymentApi, shouldUseStripe } from '../services/paymentApi';
import { useMembership } from '../hooks/useMembership';

function formatPrice(tier: Membership, useStripe: boolean): string {
  if (tier.tier_type === 'saas') {
    if (tier.price === 0) return useStripe ? '$0' : '¥0';
    return useStripe ? `$${tier.price}` : `¥${tier.price}`;
  }
  const range = tier.features_json?.price_range_usd;
  return range ?? `$${Math.round(tier.price).toLocaleString()}+`;
}

export function CheckoutPending() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { token, isLoggedIn } = useMembership();

  const slug = params.get('slug') ?? '';
  const useStripe = shouldUseStripe(i18n.language);

  const [tier, setTier] = useState<Membership | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [payError, setPayError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoggedIn) {
      navigate('/login', {
        state: { from: `/checkout/pending?slug=${encodeURIComponent(slug)}` },
      });
      return;
    }
    if (!slug) {
      setLoadError(t('checkoutPending.missingSlug', 'Missing plan identifier'));
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
          setLoadError(t('checkoutPending.notFound', 'Plan not found'));
        } else if (found.tier_type !== 'saas') {
          setLoadError(
            t(
              'checkoutPending.notSubscribable',
              'This plan is not directly subscribable. Please contact sales.',
            ),
          );
        } else {
          setTier(found);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : 'Failed to load plan');
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, isLoggedIn, navigate, t]);

  const features = useMemo(() => tier?.features.slice(0, 6) ?? [], [tier]);

  const handlePay = async () => {
    if (!tier || !token || submitting) return;
    setSubmitting(true);
    setPayError(null);
    try {
      if (useStripe) {
        const { checkout_url } = await paymentApi.createStripeCheckoutSession(
          token,
          tier.slug,
          i18n.language,
        );
        window.location.href = checkout_url;
        return;
      }
      const resp = await membershipApi.subscribe(token, tier.slug);
      setPayError(
        resp.message ||
          t(
            'checkoutPending.domesticPending',
            '微信 / 支付宝支付即将上线，请联系销售完成订阅。',
          ),
      );
    } catch (err) {
      setPayError(err instanceof Error ? err.message : '支付发起失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-[60vh] flex items-center justify-center px-6 py-16">
      <div className="max-w-xl w-full bg-card border border-border rounded-2xl p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-accent-primary/10 flex items-center justify-center">
            <svg
              className="w-5 h-5 text-accent-primary"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-bold text-primary">
              {t('checkoutPending.title', '待支付订单')}
            </h1>
            <p className="text-xs text-secondary">
              {t('checkoutPending.subtitle', '请确认订单信息后完成支付')}
            </p>
          </div>
        </div>

        {isLoading && (
          <div className="py-12 text-center text-sm text-secondary">
            {t('checkoutPending.loading', '加载订单中…')}
          </div>
        )}

        {!isLoading && loadError && (
          <div className="py-8">
            <p className="text-sm text-rose-400 mb-6">{loadError}</p>
            <button
              type="button"
              onClick={() => navigate('/products-services')}
              className="w-full justify-center btn-primary !py-3"
            >
              {t('checkoutPending.backToPlans', '返回套餐列表')}
            </button>
          </div>
        )}

        {!isLoading && tier && (
          <>
            <div className="rounded-xl border border-border p-5 mb-5 bg-[rgba(255,255,255,0.02)]">
              <div className="flex items-start justify-between gap-4 mb-3">
                <div>
                  <div className="text-sm text-secondary">
                    {t('checkoutPending.planLabel', '订阅套餐')}
                  </div>
                  <div className="text-lg font-bold text-primary mt-0.5">{tier.name}</div>
                </div>
                {tier.popular && (
                  <span className="px-2 py-0.5 rounded-full bg-accent-primary/10 text-accent-primary text-[10px] font-semibold uppercase tracking-wider">
                    {t('checkoutPending.popular', '热门')}
                  </span>
                )}
              </div>
              <p className="text-xs text-secondary leading-relaxed mb-4">{tier.description}</p>
              {features.length > 0 && (
                <ul className="space-y-2 mb-4">
                  {features.map((f, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-primary">
                      <svg
                        className="w-4 h-4 text-accent-primary shrink-0 mt-0.5"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="2.5"
                          d="M5 13l4 4L19 7"
                        />
                      </svg>
                      <span className="leading-snug">{f}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex items-end justify-between pt-4 border-t border-border">
                <span className="text-sm text-secondary">
                  {t('checkoutPending.totalLabel', '应付金额')}
                </span>
                <span className="text-2xl font-bold text-accent-primary">
                  {formatPrice(tier, useStripe)}
                  <span className="text-sm text-secondary font-medium ml-1">{tier.period}</span>
                </span>
              </div>
            </div>

            <div className="rounded-lg bg-[rgba(255,255,255,0.02)] border border-border p-4 mb-5">
              <div className="flex items-center gap-2 mb-1.5">
                <svg
                  className="w-4 h-4 text-accent-primary"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                  />
                </svg>
                <span className="text-xs font-semibold text-primary">
                  {t('checkoutPending.methodLabel', '支付方式')}
                </span>
              </div>
              <p className="text-xs text-secondary leading-relaxed">
                {useStripe
                  ? t(
                      'checkoutPending.methodStripe',
                      'You will be redirected to Stripe to complete the payment securely.',
                    )
                  : t(
                      'checkoutPending.methodDomestic',
                      '点击"立即支付"后将为您创建订单，微信 / 支付宝渠道即将上线。',
                    )}
              </p>
            </div>

            {payError && (
              <div className="mb-4 text-xs px-4 py-3 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/30">
                {payError}
              </div>
            )}

            <div className="flex flex-col sm:flex-row gap-3">
              <button
                type="button"
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
                onClick={() => navigate('/products-services')}
                disabled={submitting}
                className="px-5 py-3 rounded-lg border border-border text-secondary hover:text-primary hover:bg-border/40 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {t('checkoutPending.cancel', '取消')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
