import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { QRCodeSVG } from 'qrcode.react';
import { membershipApi, type Membership, formatTierPrice, grantPoints } from '../services/membershipApi';
import { paymentApi } from '../services/paymentApi';
import { useMembership } from '../hooks/useMembership';
import { useAuthModal } from '../contexts/AuthModalContext';
import { useTierModal } from '../components/TierModalContext';

type PayMethod = 'wechat';

// WeChat Pay branches by runtime environment: mobile stock browser -> H5 launches the WeChat app; WeChat in-app browser ->
// show a QR code for the user to long-press and recognize (WeChat blocks H5); desktop -> scan the code. UA detection is good enough;
// it only affects guidance text and the order scenario, and a wrong guess at worst falls back to the QR code.
const isMobileBrowser = /android|iphone|ipad|ipod|harmonyos/i.test(navigator.userAgent);
const isInWechat = /micromessenger/i.test(navigator.userAgent);

export function CheckoutPending() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { openAuthModal } = useAuthModal();
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
  // ICP filing remediation: this site keeps only the WeChat Pay channel; there is no payment method selector anymore.
  const payMethod: PayMethod = 'wechat';

  // WeChat Pay state
  const [wechatCodeUrl, setWechatCodeUrl] = useState<string>('');
  const [wechatPaymentId, setWechatPaymentId] = useState<number>(0);
  const [wechatAmount, setWechatAmount] = useState<number>(0);
  const [wechatStep, setWechatStep] = useState<'idle' | 'creating' | 'polling' | 'done'>('idle');
  // true = on mobile we wanted to jump to H5, but the merchant account hasn't enabled H5 payment yet, so we fell back to a QR code
  const [wechatFallback, setWechatFallback] = useState(false);
  // On mobile we tried launching WeChat via weixin:// but the page wasn't switched away -- most likely the browser blocks the scheme
  const [wechatWakeFailed, setWechatWakeFailed] = useState(false);
  const wechatPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!isLoggedIn) {
      openAuthModal('login');
      navigate('/', { replace: true });
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

  // Cleanup WeChat polling on unmount
  useEffect(() => {
    return () => {
      if (wechatPollRef.current) clearInterval(wechatPollRef.current);
    };
  }, []);

  /**
   * Launch the WeChat app's payment page directly from a mobile browser.
   *
   * The code_url returned by a Native order is `weixin://wxpay/bizpayurl?pr=xxx`; assigning it to
   * location.href can launch WeChat. This is an **unofficial** usage (never documented by WeChat, not guaranteed to keep
   * working), and success depends on the device and browser -- so it's only a best effort when H5 payment isn't enabled,
   * and the QR code always stays on the page as a fallback.
   *
   * Detecting success: on a successful launch the browser goes to the background (document.hidden=true). If still
   * in the foreground after 2.5s, assume it didn't switch and offer a manual retry.
   */
  const wakeWechatApp = (codeUrl: string) => {
    if (!codeUrl.startsWith('weixin://')) return;
    setWechatWakeFailed(false);
    let leftPage = false;
    const onHide = () => {
      if (document.hidden) leftPage = true;
    };
    document.addEventListener('visibilitychange', onHide);
    window.location.href = codeUrl;
    setTimeout(() => {
      document.removeEventListener('visibilitychange', onHide);
      if (!leftPage && !document.hidden) setWechatWakeFailed(true);
    }, 2500);
  };

  /** Check for payment every 3 seconds. The three channels (QR scan / H5 / JSAPI) share the same confirmation logic. */
  const startWechatPolling = (paymentId: number) => {
    if (!token) return;
    if (wechatPollRef.current) clearInterval(wechatPollRef.current);
    wechatPollRef.current = setInterval(async () => {
      try {
        const status = await paymentApi.getWechatPayStatus(token, paymentId);
        if (status.status === 'paid') {
          if (wechatPollRef.current) clearInterval(wechatPollRef.current);
          setWechatStep('done');
          navigate('/checkout/success?provider=wechat');
        }
      } catch {
        // Polling failures retry silently without interrupting payment
      }
    }, 3000);
  };

  /**
   * WeChat in-app browser: use JSAPI to pop WeChat's native payment sheet (no QR code, no redirect).
   *
   * Three steps -- (1) without a code, first send the user to official-account silent authorization (snsapi_base, invisible;
   * WeChat returns to this page with ?code=) (2) exchange the code for openid and place the order (3) launch
   * WeixinJSBridge with the signed params. Without an AppSecret configured, the backend falls back to a QR order and we render the QR code as usual.
   *
   * Returns true if this path has taken over; the caller should not run the QR logic.
   */
  const tryJsapiPay = async (): Promise<boolean> => {
    if (!tier || !token) return false;

    const code = params.get('code');
    if (!code) {
      // The return URL must keep the slug, otherwise after authorization we don't know which tier was purchased
      const back = `${window.location.origin}/checkout/pending?slug=${encodeURIComponent(tier.slug)}`;
      try {
        const cfg = await paymentApi.getWechatJsapiConfig(back, tier.slug);
        if (cfg.configured && cfg.oauth_url) {
          window.location.href = cfg.oauth_url;
          return true;
        }
      } catch {
        // If the config can't be fetched, treat JSAPI as unavailable and fall back to QR
      }
      return false;
    }

    const order = await paymentApi.createWechatJsapiSession(token, tier.slug, code);

    // No AppSecret configured -> the backend already fell back to a QR order; let the caller render the QR code
    if (!order.pay_params) {
      setWechatFallback(true);
      setWechatCodeUrl(order.code_url ?? '');
      setWechatPaymentId(order.payment_id);
      setWechatAmount(order.amount_cny);
      setWechatStep('polling');
      setSubmitting(false);
      startWechatPolling(order.payment_id);
      return true;
    }

    setWechatPaymentId(order.payment_id);
    setWechatAmount(order.amount_cny);
    setWechatStep('polling');
    setSubmitting(false);
    startWechatPolling(order.payment_id);

    const bridge = (window as any).WeixinJSBridge;
    const invoke = () => {
      (window as any).WeixinJSBridge.invoke(
        'getBrandWCPayRequest',
        order.pay_params,
        (res: { err_msg?: string }) => {
          // ok only means WeChat accepted the order; payment receipt is still determined by the backend callback -- polling is already running.
          if (res.err_msg !== 'get_brand_wcpay_request:ok') {
            setPayError(
              t('checkoutPending.wechatJsapiCancelled', '支付未完成,可以重新发起。'),
            );
          }
        },
      );
    };
    if (bridge) invoke();
    else document.addEventListener('WeixinJSBridgeReady', invoke, { once: true });
    return true;
  };

  const handleWechatPay = async () => {
    if (!tier || !token || submitting) return;
    setSubmitting(true);
    setPayError(null);
    setWechatStep('creating');
    try {
      // The WeChat in-app browser prefers JSAPI -- WeChat's native payment sheet, no QR code and no redirect.
      // If it takes over, we're done; if JSAPI is unavailable, continue to the QR flow.
      if (isInWechat && (await tryJsapiPay())) return;

      // Mobile stock browsers prefer H5, launching the WeChat app directly -- you can't scan a code on your own phone screen.
      // The WeChat in-app browser can't use H5 (WeChat blocks it with "please open in an external browser"), so it still shows a QR code
      // for the user to long-press and recognize. Desktop scans as before.
      const scene: 'native' | 'h5' = isMobileBrowser && !isInWechat ? 'h5' : 'native';
      const order = await paymentApi.createWechatPaySession(
        token,
        tier.slug,
        scene,
        scene === 'h5' ? `${window.location.origin}/checkout/success?provider=wechat` : undefined,
      );

      // H5 available: redirect to launch WeChat. The order is already persisted; on return, polling/callback recognize it as usual.
      if (order.h5_url) {
        window.location.href = order.h5_url;
        return;
      }

      // Reaching here means a QR order -- either it's desktop/in-WeChat to begin with,
      // or H5 was requested but the merchant hasn't enabled it (fallback); record it to change the guidance text.
      setWechatFallback(order.fallback === 'h5_not_enabled');
      setWechatCodeUrl(order.code_url ?? '');
      setWechatPaymentId(order.payment_id);
      setWechatAmount(order.amount_cny);
      setWechatStep('polling');
      setSubmitting(false);

      // H5 not enabled but the user is on mobile -- a QR code is useless to them, so first try our best to launch the WeChat app.
      // Not done in the WeChat in-app browser (already inside WeChat; long-press to recognize is enough).
      if (isMobileBrowser && !isInWechat && order.code_url) {
        wakeWechatApp(order.code_url);
      }

      startWechatPolling(order.payment_id);
    } catch (err) {
      setPayError(err instanceof Error ? err.message : t('checkoutPending.payError'));
      setWechatStep('idle');
      setSubmitting(false);
    }
  };

  // Official-account silent authorization return: coming back to this page with ?code= means the user was sent
  // to authorize mid-payment. Automatically finish placing the order and launch payment instead of making them tap again in WeChat.
  // A code can only be used once, so a ref ensures this fires only once.
  const jsapiResumedRef = useRef(false);
  useEffect(() => {
    if (!isInWechat || !tier || !token) return;
    if (!params.get('code') || jsapiResumedRef.current) return;
    jsapiResumedRef.current = true;
    void handleWechatPay();
  }, [tier, token]);

  const handlePay = handleWechatPay;

  // Cancel the current pending session and start a fresh one. Used when the
  // QR code expired, the wallet flow stalled, or the user just wants a do-over.
  const handleReinitiate = async () => {
    if (!token || submitting) return;
    setSubmitting(true);
    setPayError(null);
    try {
      if (payMethod === 'wechat' && wechatPaymentId) {
        if (wechatPollRef.current) {
          clearInterval(wechatPollRef.current);
          wechatPollRef.current = null;
        }
        await paymentApi.cancelWechatPaySession(token, wechatPaymentId);
        setWechatStep('idle');
        setWechatCodeUrl('');
        setWechatPaymentId(0);
        setSubmitting(false);
        await handleWechatPay();
        return;
      }
    } catch (err) {
      setPayError(err instanceof Error ? err.message : t('checkoutPending.payError'));
    } finally {
      setSubmitting(false);
    }
  };

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
              {/* Make it clear before scanning: this payment also grants an equal amount of credits */}
              {grantPoints(tier) !== null && (
                <p className="text-xs text-secondary text-right">
                  {t('checkoutPending.grantPoints', '支付后 {{n}} 积分同时到账', {
                    n: grantPoints(tier)!.toLocaleString('zh-CN'),
                  })}
                </p>
              )}
            </div>

            {/* WeChat Pay info -- pre-order guidance must follow the runtime environment:
                on mobile we should never say "generate a QR code and scan it" (you can't scan your own screen). */}
            {payMethod === 'wechat' && wechatStep === 'idle' && (
              <div className="rounded-lg bg-[rgba(255,255,255,0.02)] border border-border p-4 mb-5">
                <p className="text-xs text-secondary leading-relaxed">
                  {isInWechat
                    ? t(
                        'checkoutPending.methodWechatInApp',
                        '点击「立即支付」后将生成支付二维码,长按识别即可完成支付。',
                      )
                    : isMobileBrowser
                      ? t(
                          'checkoutPending.methodWechatMobile',
                          '点击「立即支付」后将打开微信 App 完成支付。若没能自动打开,页面会给出重试按钮和二维码备用。',
                        )
                      : t('checkoutPending.methodWechat')}
                </p>
              </div>
            )}

            {/* WeChat QR code panel */}
            {payMethod === 'wechat' && wechatStep === 'polling' && wechatCodeUrl && (
              <div className="rounded-lg bg-[#07C160]/5 border border-[#07C160]/30 p-5 mb-5">
                <div className="flex flex-col items-center gap-4">
                  <p className="text-sm font-medium text-primary">
                    {isMobileBrowser
                      ? t('checkoutPending.wechatLongPressQr', '长按二维码,用微信识别后付款')
                      : t('checkoutPending.wechatScanQr')}
                  </p>
                  <div className="bg-white p-3 rounded-lg">
                    <QRCodeSVG value={wechatCodeUrl} size={200} />
                  </div>

                  {/* Mobile + not in WeChat: make "Open WeChat Pay" the primary action.
                      Auto-launch may be blocked by the browser; a manual retry beats leaving only a QR code. */}
                  {isMobileBrowser && !isInWechat && wechatCodeUrl.startsWith('weixin://') && (
                    <button
                      type="button"
                      onClick={() => wakeWechatApp(wechatCodeUrl)}
                      className="w-full py-3 rounded-lg bg-[#07C160] hover:bg-[#06AD56] text-white font-semibold text-sm transition-colors"
                    >
                      {t('checkoutPending.wechatOpenApp', '打开微信完成支付')}
                    </button>
                  )}

                  {/* Showing a QR code on mobile = you can't scan it yourself, so an alternative path is required.
                      Distinguish two causes: the WeChat in-app browser (H5 blocked by WeChat, long-press to recognize)
                      and the merchant not having H5 enabled (fallback; suggest switching to desktop). */}
                  {isMobileBrowser && (
                    <div className="w-full rounded-lg bg-[rgba(255,255,255,0.03)] border border-border p-3">
                      <p className="text-xs text-secondary leading-relaxed">
                        {isInWechat
                          ? t(
                              'checkoutPending.wechatInAppHint',
                              '当前在微信内打开,请长按上方二维码并选择「识别图中二维码」完成支付。',
                            )
                          : wechatWakeFailed
                            ? t(
                                'checkoutPending.wechatWakeFailedHint',
                                '没能自动打开微信。可以再点一次上方按钮;若仍无反应,请长按二维码保存到相册,打开微信「扫一扫」右上角选择相册识别。',
                              )
                            : wechatFallback
                              ? t(
                                  'checkoutPending.wechatH5UnavailableHint',
                                  '手机浏览器直接跳转微信支付暂未开通。最省事的办法是复制本页链接、在微信里打开本页面,即可直接用微信支付付款;也可以长按二维码保存到相册,用微信「扫一扫」右上角的相册识别,或改用电脑端扫码。',
                                )
                              : t(
                                  'checkoutPending.wechatMobileHint',
                                  '请长按二维码保存到相册,再打开微信「扫一扫」从相册中选取识别。',
                                )}
                      </p>
                    </div>
                  )}
                  <div className="w-full space-y-2 text-xs">
                    <div className="flex justify-between">
                      <span className="text-secondary">{t('checkoutPending.wechatAmount')}</span>
                      <span className="font-mono text-primary font-semibold">¥{wechatAmount.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-secondary">{t('checkoutPending.wechatStatus')}</span>
                      <span className="text-[#07C160] font-medium flex items-center gap-1.5">
                        <svg className="animate-spin h-3 w-3" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        {t('checkoutPending.wechatWaiting')}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={handleReinitiate}
                    disabled={submitting}
                    className="mt-2 text-xs text-secondary hover:text-[#07C160] underline underline-offset-2 disabled:opacity-50"
                  >
                    {t('checkoutPending.reinitiate', '重新发起支付')}
                  </button>
                </div>
              </div>
            )}

            {/* WeChat creating spinner */}
            {payMethod === 'wechat' && wechatStep === 'creating' && (
              <div className="rounded-lg bg-[#07C160]/5 border border-[#07C160]/30 p-5 mb-5 text-center">
                <svg className="animate-spin h-6 w-6 mx-auto mb-2 text-[#07C160]" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <p className="text-xs text-secondary">{t('checkoutPending.submitting')}</p>
              </div>
            )}

            {payError && (
              <div className="mb-4 text-xs px-4 py-3 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/30">
                {payError}
              </div>
            )}

            <div className="flex flex-col sm:flex-row gap-3">
              {wechatStep !== 'polling' && (
                <button
                  type="button"
                  onClick={handlePay}
                  disabled={submitting}
                  className="flex-1 justify-center btn-primary !py-3 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {submitting
                    ? t('checkoutPending.submitting')
                    : t('checkoutPending.wechatPayNow')}
                </button>
              )}
              <button
                type="button"
                onClick={() => openTierModal()}
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
