// "Membership & billing" -- the old account center's four items (membership info / usage / my credits / payment records)
// combined into one page with four tabs. They answer the same question: how much has this account spent, and how much is left.
//
// Splitting them into four top-level menu items was historical: the account center was once the only place with a sidebar, so everything went in.
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { MembershipTab } from './MembershipTab';
import { UsageTab } from './UsageTab';
import { BalanceTab } from './BalanceTab';
import { PaymentsTab } from './PaymentsTab';

const TABS = [
  { key: 'membership', labelKey: 'account.menu.membership', fallback: '会员信息' },
  { key: 'balance', labelKey: 'account.menu.balance', fallback: '我的积分' },
  { key: 'usage', labelKey: 'account.menu.usage', fallback: '使用情况' },
  { key: 'payments', labelKey: 'account.menu.payments', fallback: '支付记录' },
] as const;

type TabKey = typeof TABS[number]['key'];

export function BillingTab() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get('tab');
  const tab: TabKey = TABS.some(x => x.key === raw) ? (raw as TabKey) : 'membership';

  const select = (key: TabKey) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', key);
    setSearchParams(next, { replace: true });
  };

  return (
    <div>
      <div className="flex gap-1 mb-6 border-b overflow-x-auto scrollbar-hide"
           style={{ borderColor: 'var(--border-color)' }} role="tablist">
        {TABS.map(x => (
          <button
            key={x.key}
            type="button"
            role="tab"
            aria-selected={tab === x.key}
            onClick={() => select(x.key)}
            className="px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors"
            style={{
              color: tab === x.key ? 'var(--accent-primary)' : 'var(--text-secondary)',
              borderColor: tab === x.key ? 'var(--accent-primary)' : 'transparent',
            }}
          >
            {t(x.labelKey, x.fallback)}
          </button>
        ))}
      </div>

      {tab === 'membership' && <MembershipTab />}
      {tab === 'balance' && <BalanceTab />}
      {tab === 'usage' && <UsageTab />}
      {tab === 'payments' && <PaymentsTab />}
    </div>
  );
}
