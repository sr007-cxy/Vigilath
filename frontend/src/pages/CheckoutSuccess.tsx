import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { paymentApi } from '../services/paymentApi';
import { balanceApi, points } from '../services/balanceApi';
import { useTierModal } from '../components/TierModalContext';

type LoadState = 'loading' | 'paid' | 'pending' | 'error';

export function CheckoutSuccess() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { openTierModal } = useTierModal();
  const [params] = useSearchParams();
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState<string>('');
  // Buying a membership = tier + an equal amount of credits. Once paid it must be visible right away, otherwise users don't know they have credits.
  const [balanceCents, setBalanceCents] = useState<number | null>(null);

  const provider = params.get('provider');

  useEffect(() => {
    // WeChat H5 payment return: WeChat also sends the user to redirect_url on "cancel payment",
    // so anything carrying payment_id must check the real status once; we can't report success on arrival.
    // The callback may lag the user's return by a step; poll a few times before declaring failure.
    const wechatPaymentId = provider === 'wechat' ? params.get('payment_id') : null;
    if (wechatPaymentId) {
      const token = localStorage.getItem('token');
      if (!token) {
        setState('error');
        setError(t('checkout.mustLogin', '请先登录后再发起支付'));
        return;
      }
      let cancelled = false;
      let tries = 0;
      const pollWechat = async () => {
        try {
          const resp = await paymentApi.getWechatPayStatus(token, Number(wechatPaymentId));
          if (cancelled) return;
          if (resp.status === 'paid') {
            setState('paid');
            return;
          }
        } catch {
          // Network jitter, keep polling
        }
        if (cancelled) return;
        tries += 1;
        if (tries < 6) {
          setTimeout(pollWechat, 1500);
          return;
        }
        setState('error');
        setError(t('checkout.wechatNotCompleted', '未检测到支付完成,如已付款请稍候刷新'));
      };
      pollWechat();
      return () => {
        cancelled = true;
      };
    }

    // QR order: paid was already confirmed by polling before reaching this page
    if (provider === 'wechat') {
      setState('paid');
      return;
    }

    setState('error');
    setError(t('checkout.missingSession', '支付信息缺失,请从会员页重新发起支付。'));
  }, [provider, t]);

  // After a successful payment fetch credits once -- read the authoritative balance directly instead of computing from the tier price,
  // so cases like test-account discounts and currency conversion show the amount actually credited.
  useEffect(() => {
    if (state !== 'paid') return;
    const token = localStorage.getItem('token');
    if (!token) return;
    let cancelled = false;
    balanceApi.get(token)
      .then(b => { if (!cancelled) setBalanceCents(b.balance_cents); })
      .catch(() => { /* If unavailable, don't show this line; don't interrupt the success page */ });
    return () => { cancelled = true; };
  }, [state]);

  return (
    <div className="min-h-[60vh] flex items-center justify-center px-6">
      <div className="max-w-md w-full bg-card border border-border rounded-2xl p-8 text-center">
        {state === 'loading' && (
          <>
            <div className="mx-auto mb-4 w-12 h-12 rounded-full border-4 border-accent-primary border-t-transparent animate-spin" />
            <h1 className="text-xl font-bold text-primary mb-2">
              {t('checkout.verifying', '正在确认支付结果…')}
            </h1>
            <p className="text-sm text-secondary">
              {t('checkout.verifyingHint', '通常只需要几秒钟。')}
            </p>
          </>
        )}

        {state === 'paid' && (
          <>
            <div className="mx-auto mb-4 w-14 h-14 rounded-full bg-emerald-500/10 flex items-center justify-center">
              <svg
                className="w-8 h-8 text-emerald-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h1 className="text-2xl font-bold text-primary mb-2">
              {t('checkout.successTitle', '会员已开通')}
            </h1>
            <p className="text-sm text-secondary mb-4">
              {t('checkout.successBody', '支付成功,会员权益已生效,现在就可以开始跑完整检测。')}
            </p>
            {balanceCents !== null && (
              <p className="text-sm mb-6">
                <span className="text-secondary">
                  {t('checkout.pointsCredited', '等额积分已到账,当前')}{' '}
                </span>
                <b className="text-primary">{points(balanceCents)}</b>
              </p>
            )}
            <div className="flex flex-col sm:flex-row gap-3 justify-center">
              <button
                type="button"
                onClick={() => navigate('/account/balance')}
                className="px-5 py-2.5 rounded-lg bg-accent-primary text-white font-semibold hover:bg-accent-primary/80 transition-colors"
              >
                {t('checkout.viewPoints', '查看我的积分')}
              </button>
              <button
                type="button"
                onClick={() => navigate('/account/membership')}
                className="px-5 py-2.5 rounded-lg border border-border text-secondary hover:text-primary hover:bg-border/40 transition-colors"
              >
                {t('checkout.viewMembership', '查看会员')}
              </button>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="px-5 py-2.5 rounded-lg border border-border text-secondary hover:text-primary hover:bg-border/40 transition-colors"
              >
                {t('checkout.runCheck', '去跑一次检测')}
              </button>
            </div>
          </>
        )}

        {state === 'pending' && (
          <>
            <h1 className="text-xl font-bold text-primary mb-2">
              {t('checkout.pendingTitle', '已收到付款,正在开通…')}
            </h1>
            <p className="text-sm text-secondary mb-4">
              {t('checkout.pendingBody', '支付还在确认中,会员通常一分钟内开通。可以刷新本页,或到会员页查看。')}
            </p>
            <button
              type="button"
              onClick={() => navigate('/account/membership')}
              className="px-5 py-2.5 rounded-lg bg-accent-primary text-white font-semibold hover:bg-accent-primary/80 transition-colors"
            >
              {t('checkout.viewMembership', '查看会员')}
            </button>
          </>
        )}

        {state === 'error' && (
          <>
            <h1 className="text-xl font-bold text-rose-400 mb-2">
              {t('checkout.errorTitle', '支付校验失败')}
            </h1>
            <p className="text-sm text-secondary mb-4">{error}</p>
            <button
              type="button"
              onClick={openTierModal}
              className="px-5 py-2.5 rounded-lg border border-border text-secondary hover:text-primary hover:bg-border/40 transition-colors"
            >
              {t('checkout.backToPlans', '返回套餐列表')}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
