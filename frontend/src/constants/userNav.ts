// The **single authoritative source** for customer-side navigation (user / agency_* / and admin·operator viewing as a customer).
//
// The reason for separating from constants/opsNav.ts is in that file's header. The shell is shared
// (components/nav/SidebarShell.tsx); the menu sources are not.
//
// The report center is **not in the sidebar**: it's a standalone module (with its own ReportsShell), entered from the top bar dropdown.
// A sidebar item that leaves the shell when clicked would only make people think they're still in the workbench.
//
// Grouping principle: every item = reachable in one click. **Don't pile several features into one menu item with tabs** --
// the previous version crammed 7 pages into one "Content" tab bar, so SMS records took two clicks plus horizontal scrolling,
// deeper than before splitting. Spec in docs/03-系统设计/前端UI规范.md "Workbench shell".
export interface UserNavItem {
  to: string;
  end?: boolean;
  icon: string;
  labelKey: string;
  roles: readonly string[];
  /** Points to a standalone module outside the workbench shell -- rendered with ↗; clicking leaves the shell. */
  external?: boolean;
  /** Child routes under this item (sharing the same role gate, not shown in the sidebar). */
  details?: readonly string[];
}

export interface UserNavGroup {
  key: string;
  labelKey: string;
  items: readonly UserNavItem[];
}

/** Customers + agencies + ops (viewing as a customer). tenant is kept out by NonTenantOnly. */
export const APP_ROLES = [
  'user', 'agency_admin', 'agency_operator', 'admin', 'operator',
] as const;

const ALL = APP_ROLES;
const AGENCY = ['agency_admin', 'agency_operator', 'admin'] as const;
// Organization member management is only for agency admins. Before the refactor it was /workbench/users with roles
// ['admin','operator','agency_admin'] -- agency_operator never had it, kept consistent here.
const AGENCY_ADMIN = ['agency_admin', 'admin'] as const;

export const userGroups: readonly UserNavGroup[] = [
  {
    key: 'project',
    labelKey: 'nav.appGroupProject',
    items: [
      // Landing page -- the bare /app path redirects here
      { to: '/app/topics', icon: 'accounts', labelKey: 'nav.appTopics', roles: ALL, details: ['/app/topics'] },
      { to: '/app/action-items', icon: 'actions', labelKey: 'nav.appActionItems', roles: ALL },
      { to: '/app/content', icon: 'docs', labelKey: 'nav.appContentLibrary', roles: ALL },
      { to: '/app/publishing', icon: 'publish', labelKey: 'nav.appPublishing', roles: ALL },
      { to: '/app/enterprises', icon: 'accounts', labelKey: 'nav.appEnterprises', roles: AGENCY },
      // Agencies manage their own organization's members. This page (UserOrganizationManagement) was mounted before the refactor at
      // /workbench/users, which agency_admin could enter; when splitting the shells I treated it as a pure platform feature and put it in
      // the admin workbench "Platform" group, but the admin workbench is only open to admin/operator/maintainer --
      // so agency_admin could no longer manage their own members. Now a copy is added in the customer shell, without letting them into the admin workbench.
      // The component narrows the data scope by role (canManage); the ops-side /ops/users is still the same component.
      { to: '/app/members', icon: 'users', labelKey: 'nav.appMembers', roles: AGENCY_ADMIN },
    ],
  },
  {
    key: 'check',
    labelKey: 'nav.appGroupCheck',
    items: [
      // "One-click full diagnosis" is an action page (form + history), on the same line as "Audit records".
      // The previous version merged audit records into "Reports" -- those are daily/weekly/monthly reports of monitoring batch runs, a different thing.
      { to: '/app/diagnosis', icon: 'check', labelKey: 'nav.appDiagnosis', roles: ALL },
      { to: '/app/history', icon: 'runs', labelKey: 'nav.appHistory', roles: ALL },
    ],
  },
  {
    key: 'monitor',
    labelKey: 'nav.appGroupMonitor',
    items: [
      { to: '/app/sentiment', end: true, icon: 'sentiment', labelKey: 'nav.appSentiment', roles: ALL },
      // This item is the old account center's "Brand & monitoring" (pages/Account/BrandSettingsTab).
      // The four things it configures -- brand name/aliases, monitoring focus and keyword groups, notification email, brand tone and
      // legal red lines -- all belong to sentiment monitoring, and it imports only useSentiment* / sentimentPlatforms.
      // Calling it "Brand settings" got it mistaken for a general brand config with its own group; renamed to follow sentiment monitoring.
      { to: '/app/sentiment/settings', icon: 'brand', labelKey: 'nav.appSentimentSettings', roles: ALL },
    ],
  },
  {
    key: 'outreach',
    labelKey: 'nav.appGroupOutreach',
    items: [
      { to: '/app/email-records', icon: 'mail', labelKey: 'nav.salesAgentEmailRecords', roles: ALL },
      { to: '/app/sms-records', icon: 'mail', labelKey: 'nav.salesAgentSmsRecords', roles: ALL },
    ],
  },
];

/** Groups + items this role can actually see. Empty groups are dropped; groups with only one item lose their header and are flattened. */
export function visibleUserGroups(role: string) {
  return userGroups
    .map(group => ({ ...group, items: group.items.filter(item => item.roles.includes(role)) }))
    .filter(group => group.items.length > 0)
    .map(group => ({ ...group, flatten: group.items.length === 1 }));
}

const underPrefix = (path: string, prefix: string) =>
  path === prefix || path.startsWith(`${prefix}/`);

/** Frontend direct-route gate -- derived from the menu source, no second hand-written copy. The backend is still the final permission boundary. */
export function canAccessAppPath(role: string, path: string): boolean {
  return userGroups.some(group =>
    group.items.some(item =>
      item.roles.includes(role) && !item.external
      && (underPrefix(path, item.to) || (item.details ?? []).some(d => underPrefix(path, d))),
    ),
  );
}

/** Bare /app path -> the role's first visible menu item (excluding items that leave the shell). */
export function firstAppPath(role: string): string | null {
  for (const group of visibleUserGroups(role)) {
    const item = group.items.find(i => !i.external);
    if (item) return item.to;
  }
  return null;
}

export function isAppRole(role: string): boolean {
  return (APP_ROLES as readonly string[]).includes(role);
}
