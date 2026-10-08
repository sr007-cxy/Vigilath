import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useTierModal } from '../components/TierModalContext';

export function CheckoutCancel() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { openTierModal } = useTierModal();

  return (
    <div className="min-h-[60vh] flex items-center justify-center px-6">
      <div className="max-w-md w-full bg-card border border-border rounded-2xl p-8 text-center">
        <h1 className="text-2xl font-bold text-primary mb-2">
          {t('checkout.cancelTitle', '支付已取消')}
        </h1>
        <p className="text-sm text-secondary mb-6">
          {t('checkout.cancelBody', '本次没有扣款。可以重新选择套餐,随时再来。')}
        </p>
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <button
            type="button"
            onClick={openTierModal}
            className="px-5 py-2.5 rounded-lg bg-accent-primary text-white font-semibold hover:bg-accent-primary/80 transition-colors"
          >
            {t('checkout.backToPlans', '返回套餐列表')}
          </button>
          <button
            type="button"
            onClick={() => navigate('/')}
            className="px-5 py-2.5 rounded-lg border border-border text-secondary hover:text-primary hover:bg-border/40 transition-colors"
          >
            {t('checkout.goHome', '回到首页')}
          </button>
        </div>
      </div>
    </div>
  );
}
