// "Integrations & notifications" -- the old account center's three items (integrations / email config / SMS config) combined into one page.
// They're all "how this account talks to external systems"; one place is clearer than three side-by-side top-level menu items.
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AgentIntegrationTab } from './AgentIntegrationTab';
import { SalesAgentEmailTab } from './SalesAgentEmailTab';
import { SalesAgentSmsTab } from './SalesAgentSmsTab';

const TABS = [
  { key: 'agent', labelKey: 'account.menu.integration', fallback: '对接集成' },
  { key: 'email', labelKey: 'account.menu.salesAgentEmail', fallback: '邮件配置' },
  { key: 'sms', labelKey: 'account.menu.salesAgentSms', fallback: '短信配置' },
] as const;

type TabKey = typeof TABS[number]['key'];

export function IntegrationsTab() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get('tab');
  const tab: TabKey = TABS.some(x => x.key === raw) ? (raw as TabKey) : 'agent';

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

      {tab === 'agent' && <AgentIntegrationTab />}
      {tab === 'email' && <SalesAgentEmailTab />}
      {tab === 'sms' && <SalesAgentSmsTab />}
    </div>
  );
}
