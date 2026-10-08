// The **single authoritative source** for ops-side (admin / operator / maintainer) navigation.
//
// Strictly separated from constants/userNav.ts -- the two navs change at completely different rates:
// the ops menu grows with infrastructure (gateway / call-policies / probe were all added later),
// the customer menu grows with business value. Mixing them in one array filtered by roles meant every new ops page
// required rechecking whether customer roles were missed.
//
// Both "who can see it" and "who can enter it" derive from this one source; no second hand-written whitelist.
export interface OpsNavItem {
  to: string;
  end: boolean;
  icon: string;
  labelKey: string;
  roles: readonly string[];
  /** Detail routes under this menu item (not shown in the sidebar, but sharing the same role gate). */
  details?: readonly string[];
}

export interface OpsNavGroup {
  key: string;
  labelKey: string;
  items: readonly OpsNavItem[];
}

const OPS = ['admin', 'operator'] as const;
const ADMIN_MAINT = ['admin', 'maintainer'] as const;

export const opsGroups: readonly OpsNavGroup[] = [
  {
    // Delivery -- "where is this customer now"
    key: 'delivery',
    labelKey: 'nav.opsGroupDelivery',
    items: [
      { to: '/ops/cockpit', end: false, icon: 'cockpit', labelKey: 'nav.adminCockpit', roles: OPS },
      {
        to: '/ops/accounts', end: false, icon: 'accounts', labelKey: 'nav.adminAccounts', roles: OPS,
        // Customer topics: entered from customer accounts, not a separate top-level menu item
        details: ['/ops/topics'],
      },
      { to: '/ops/insights', end: false, icon: 'insights', labelKey: 'nav.adminInsights', roles: OPS },
      { to: '/ops/action-items', end: false, icon: 'actions', labelKey: 'nav.adminActionItems', roles: OPS },
    ],
  },
  {
    // Content governance -- the filing correction list items, plus the publish account pool
    key: 'content',
    labelKey: 'nav.opsGroupContent',
    items: [
      { to: '/ops/content-review', end: false, icon: 'docs', labelKey: 'nav.adminContentReview', roles: OPS },
      { to: '/ops/content-trace', end: false, icon: 'docs', labelKey: 'nav.adminContentTrace', roles: OPS },
      { to: '/ops/media', end: false, icon: 'docs', labelKey: 'nav.adminMediaLibrary', roles: OPS },
      { to: '/ops/abuse-reports', end: false, icon: 'rules', labelKey: 'nav.adminAbuseReports', roles: OPS },
      { to: '/ops/platform-rules', end: false, icon: 'rules', labelKey: 'nav.adminPlatformRules', roles: ADMIN_MAINT },
      // The old name "Account center" clashed in Chinese with "Customer accounts", renamed to publish account pool
      { to: '/ops/publish-accounts', end: false, icon: 'accounts', labelKey: 'nav.opsPublishAccounts', roles: ADMIN_MAINT },
    ],
  },
  {
    // Operations -- did batch runs actually run, and where are they stuck
    key: 'runtime',
    labelKey: 'nav.opsGroupRuntime',
    items: [
      { to: '/ops/runs', end: false, icon: 'runs', labelKey: 'nav.adminRuns', roles: OPS },
      { to: '/ops/schedule', end: false, icon: 'schedule', labelKey: 'nav.adminSchedule', roles: OPS },
      { to: '/ops/crawl', end: false, icon: 'crawl', labelKey: 'nav.adminCrawl', roles: ADMIN_MAINT },
      { to: '/ops/probe', end: false, icon: 'probe', labelKey: 'nav.adminProbe', roles: ['admin', 'operator', 'maintainer'] },
      { to: '/ops/workers', end: false, icon: 'workers', labelKey: 'nav.adminWorkers', roles: ADMIN_MAINT },
      { to: '/ops/gateway', end: false, icon: 'gateway', labelKey: 'nav.adminGateway', roles: ADMIN_MAINT },
    ],
  },
  {
    // Platform -- accounts, costs, external tiers
    key: 'platform',
    labelKey: 'nav.opsGroupPlatform',
    items: [
      { to: '/ops/users', end: false, icon: 'users', labelKey: 'nav.adminUsers', roles: OPS },
      { to: '/ops/llm-usage', end: false, icon: 'usage', labelKey: 'nav.adminLlmUsage', roles: ['admin'] },
      { to: '/ops/call-policies', end: false, icon: 'gateway', labelKey: 'nav.adminCallPolicy', roles: ['admin'] },
      {
        to: '/ops/sales-agent-email', end: false, icon: 'gateway', labelKey: 'nav.adminSalesAgentEmail', roles: ['admin'],
        details: ['/ops/sales-agent'],
      },
      { to: '/ops/sales-agent-sms', end: false, icon: 'gateway', labelKey: 'nav.adminSalesAgentSms', roles: ['admin'] },
    ],
  },
];

/** Groups + items this role can actually see. Empty groups are dropped; groups with only one item are marked flatten and render without a group header. */
export function visibleOpsGroups(role: string) {
  return opsGroups
    .map(group => ({ ...group, items: group.items.filter(item => item.roles.includes(role)) }))
    .filter(group => group.items.length > 0)
    .map(group => ({ ...group, flatten: group.items.length === 1 }));
}

const underPrefix = (path: string, prefix: string) =>
  path === prefix || path.startsWith(`${prefix}/`);

/**
 * Frontend direct-route gate -- **derived from the menu source**, no second hand-written whitelist.
 * The backend is still the final permission boundary.
 */
export function canAccessOpsPath(role: string, path: string): boolean {
  return opsGroups.some(group =>
    group.items.some(item =>
      item.roles.includes(role)
      && (underPrefix(path, item.to) || (item.details ?? []).some(d => underPrefix(path, d))),
    ),
  );
}

/** Landing page for the role -- the bare /ops path goes to the first visible menu item in group order. */
export function firstOpsPath(role: string): string | null {
  return visibleOpsGroups(role)[0]?.items[0]?.to ?? null;
}

export const OPS_ROLES = ['admin', 'operator', 'maintainer'] as const;

export function isOpsRole(role: string): boolean {
  return (OPS_ROLES as readonly string[]).includes(role);
}
