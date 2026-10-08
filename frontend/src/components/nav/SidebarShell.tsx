// Workbench sidebar shell -- **shared** by the customer side (/app) and the ops side (/ops).
//
// What's shared is "how it looks", not "who sees what": menu sources come from constants/userNav.ts
// and constants/opsNav.ts respectively, evolving independently. These two things were tied together once, and the customer side ended up
// with a horizontal bar nav -- the shell doesn't need to be split along with the menu sources.
//
// Spec in the "Layout patterns" and "Workbench shell" sections of docs/03-系统设计/前端UI规范.md.
// A few easy traps:
//   · The height chain `h-full` + `flex-1 min-h-0` must not break anywhere -- miss one min-h-0
//     and content pushes the parent open instead of showing a scrollbar, so "the page that wasn't supposed to scroll" scrolls again
//   · Collapse animation uses max-width + opacity, not display:none (the latter has no transition, so items flash)
//   · When collapsed, group title text is hidden but **its space is kept**, otherwise items jump up and down on expand/collapse
//   · The group matching the current route is forced open and can't be collapsed, or users can't find where they are after collapsing it
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { openCommandPalette } from '../ui/commandPaletteBus';
import { AgencyEnterpriseSwitcher } from '../AgencyEnterpriseSwitcher';
import '../../styles/workbench.css';

export interface ShellNavItem {
  to: string;
  end?: boolean;
  icon: string;
  labelKey: string;
  /** Points to a standalone module outside the shell -- rendered with ↗ to make clear it leaves. */
  external?: boolean;
}

export interface ShellNavGroup {
  key: string;
  labelKey: string;
  items: readonly ShellNavItem[];
  /** Groups with only one item get no group header and render directly as a top-level item. */
  flatten?: boolean;
}

export function SidebarIcon({ name }: { name: string }) {
  // Platform rules: shield + check (review red lines)
  if (name === 'rules') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    );
  }
  // Optimization tasks: checklist
  if (name === 'actions') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
      </svg>
    );
  }
  // Batch run scheduling: calendar (daily/weekly frequency tiers)
  if (name === 'schedule') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    );
  }
  // External gateway: cloud outbound
  if (name === 'gateway') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v9" />
      </svg>
    );
  }
  // User roles: single person + settings
  if (name === 'users') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
      </svg>
    );
  }
  // Project progress: dashboard
  if (name === 'cockpit') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
      </svg>
    );
  }
  // Profile: user group
  if (name === 'accounts') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
      </svg>
    );
  }
  // Engine live test: camera (execution recording)
  if (name === 'probe') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
      </svg>
    );
  }
  // Copy re-review: document
  if (name === 'docs') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    );
  }
  // Monitoring report: line chart
  if (name === 'insights') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
      </svg>
    );
  }
  // Batch run results: gear
  if (name === 'runs') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    );
  }
  // AI crawler analysis: robot/radar
  if (name === 'crawl') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h.01M15 9h.01M9.5 13a3.5 3.5 0 005 0" />
      </svg>
    );
  }
  // Dispatch center / Worker: server stack
  if (name === 'workers') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M5 4h14a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5a1 1 0 011-1zM5 14h14a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4a1 1 0 011-1zM8 7h.01M8 17h.01" />
      </svg>
    );
  }
  // Model usage: gauge
  if (name === 'usage') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M12 20a8 8 0 108-8 8 8 0 00-8 8zm0 0V12m0 0l4-3M4 12H2m2.9-5.1L3.5 5.5" />
      </svg>
    );
  }
  // Brand settings: palette
  if (name === 'brand') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828L11 19M7 17h.01" />
      </svg>
    );
  }
  // Sentiment monitoring: sound wave
  if (name === 'sentiment') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
      </svg>
    );
  }
  // Placement: send
  if (name === 'publish') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
      </svg>
    );
  }
  // Audit: magnifier + check
  if (name === 'check') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    );
  }
  // Outreach: envelope
  if (name === 'mail') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    );
  }
  return null;
}

function GroupHeader({
  label, open, active, collapsed, onToggle,
}: { label: string; open: boolean; active: boolean; collapsed: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      // Collapsed state: text hidden but **space kept** (min-h), otherwise items below jump up and down on expand/collapse
      className="workbench-nav-group w-full px-3 py-1.5 mt-2 first:mt-0 flex items-center justify-between gap-2 rounded-lg text-[11px] font-semibold tracking-wide min-h-[1.25rem]"
      style={{ color: active ? 'var(--accent-primary)' : 'var(--text-muted, var(--text-secondary))' }}
      aria-hidden={collapsed}
    >
      <span className="truncate transition-all duration-200"
            style={collapsed ? { maxWidth: 0, opacity: 0 } : { maxWidth: '12rem', opacity: 1 }}>
        {label}
      </span>
      <svg
        className="w-3 h-3 shrink-0 transition-transform"
        style={{ transform: open ? 'rotate(90deg)' : 'none', opacity: collapsed ? 0 : 1 }}
        fill="none" viewBox="0 0 24 24" stroke="currentColor"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
      </svg>
    </button>
  );
}

export interface SidebarShellProps {
  groups: readonly ShellNavGroup[];
  /** Module name at the top of the sidebar (i18n key). */
  titleKey: string;
  /** localStorage namespace -- collapse states of the customer side and ops side don't affect each other. */
  storageKey: string;
  /** Context passed to Outlet. */
  context?: unknown;
}

export function SidebarShell({ groups, titleKey, storageKey, context }: SidebarShellProps) {
  const location = useLocation();
  const { t } = useTranslation();
  const navRef = useRef<HTMLElement>(null);

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(`${storageKey}.collapsed`) === '1'; } catch { return false; }
  });
  const [closedGroups, setClosedGroups] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(`${storageKey}.groups`);
      return raw ? (JSON.parse(raw) as string[]) : [];
    } catch { return []; }
  });
  const [mobileOpen, setMobileOpen] = useState(false);

  // Remember the menu area's scroll position in storage -- with a long menu, coming back after switching pages shouldn't jump to the top
  useEffect(() => {
    const el = navRef.current;
    if (!el) return;
    try {
      const y = Number(sessionStorage.getItem(`${storageKey}.scroll`) || 0);
      if (y > 0) el.scrollTop = y;
    } catch { /* unavailable in private mode; just don't remember the scroll position */ }
    return () => {
      try { sessionStorage.setItem(`${storageKey}.scroll`, String(el.scrollTop)); } catch { /* noop */ }
    };
  }, [storageKey]);

  // Close the mobile drawer whenever the route changes, otherwise the drawer keeps covering the content after clicking a menu item
  useEffect(() => { setMobileOpen(false); }, [location.pathname]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed(prev => {
      const next = !prev;
      try { localStorage.setItem(`${storageKey}.collapsed`, next ? '1' : '0'); } catch { /* noop */ }
      return next;
    });
  }, [storageKey]);

  const toggleGroup = useCallback((key: string) => {
    setClosedGroups(prev => {
      const next = prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key];
      try { localStorage.setItem(`${storageKey}.groups`, JSON.stringify(next)); } catch { /* noop */ }
      return next;
    });
  }, [storageKey]);

  const isMac = useMemo(
    () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform), []);

  return (
    // Start of the height chain: the outer Chrome is already h-screen overflow-hidden (see App.tsx),
    // and every layer below must pass the height down; break one and a page-level scrollbar appears.
    <div className="workbench-shell flex flex-1 min-h-0">
      {/* Mobile drawer overlay */}
      {mobileOpen && (
        <button
          type="button"
          aria-label="close menu"
          onClick={() => setMobileOpen(false)}
          className="md:hidden fixed inset-0 z-40"
          style={{ background: 'rgba(0,0,0,.45)' }}
        />
      )}

      <aside
        className={`workbench-sidebar shrink-0 h-full flex flex-col border-r transition-transform duration-200
          max-md:fixed max-md:inset-y-16 max-md:left-0 max-md:z-50
          ${mobileOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full'}`}
        style={{ width: collapsed ? '4.5rem' : '15.5rem' }}
      >
        <div className="workbench-brand flex items-center gap-3 px-4 border-b shrink-0 overflow-hidden">
          <div className="workbench-brand-mark shrink-0" aria-hidden>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5h16M4 12h10M4 19h7" />
            </svg>
          </div>
          <div className="min-w-0 transition-all duration-200"
               style={collapsed ? { maxWidth: 0, opacity: 0 } : { maxWidth: '12rem', opacity: 1 }}>
            <p className="text-[10px] font-semibold tracking-[0.08em] text-muted">Vigilath</p>
            <p className="text-sm font-semibold truncate text-primary">{t(titleKey)}</p>
          </div>
        </div>

        {/* ⌘K needs a visible entry -- collapsed state keeps only the magnifier */}
        <div className="px-3 pt-3 shrink-0">
          <button
            type="button"
            onClick={openCommandPalette}
            title={collapsed ? t('nav.commandSearch', { defaultValue: '搜索页面' }) : undefined}
            className="workbench-search-trigger w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs"
          >
            <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
            </svg>
            {!collapsed && (
              <>
                <span className="flex-1 text-left">{t('nav.commandSearch', { defaultValue: '搜索页面' })}</span>
                <kbd className="text-[10px] px-1 py-0.5 rounded font-mono"
                     style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                  {isMac ? '⌘K' : 'Ctrl+K'}
                </kbd>
              </>
            )}
          </button>
        </div>

        {/* The menu area scrolls itself -- without min-h-0 it stretches the whole aside */}
        <nav ref={navRef} className="flex-1 min-h-0 overflow-y-auto scrollbar-hide p-2 md:p-3 flex flex-col gap-0.5">
          {groups.map(group => {
            const groupActive = group.items.some(item => location.pathname.startsWith(item.to));
            // The group matching the current route is forced open and can't be collapsed
            const open = groupActive || !closedGroups.includes(group.key);
            const links = group.items.map(item => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                title={collapsed ? t(item.labelKey) : undefined}
                className={({ isActive }) =>
                  `workbench-nav-link ${isActive ? 'is-active' : ''} px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap flex items-center gap-2 ${group.flatten || collapsed ? '' : 'md:pl-5'}`
                }
              >
                <SidebarIcon name={item.icon} />
                <span className="flex-1 min-w-0 truncate transition-all duration-200"
                      style={collapsed ? { maxWidth: 0, opacity: 0 } : { maxWidth: '12rem', opacity: 1 }}>
                  {t(item.labelKey)}
                </span>
                {/* Items that leave the shell are marked with ↗ */}
                {item.external && !collapsed && (
                  <span className="shrink-0 text-[11px]" style={{ color: 'var(--text-muted)' }}>↗</span>
                )}
              </NavLink>
            ));
            if (group.flatten) return links;
            return (
              <div key={group.key}>
                <GroupHeader
                  label={t(group.labelKey)}
                  open={open}
                  active={groupActive}
                  collapsed={collapsed}
                  onToggle={() => toggleGroup(group.key)}
                />
                <div className={`flex flex-col gap-0.5 ${open ? '' : 'hidden'}`}>{links}</div>
              </div>
            );
          })}
        </nav>

        <div className="p-2 border-t shrink-0" style={{ borderColor: 'var(--border-color)' }}>
          <button
            type="button"
            onClick={toggleCollapsed}
            title={collapsed ? '展开' : '收起'}
            className="workbench-nav-link w-full px-3 py-2 rounded-lg text-sm flex items-center gap-2"
          >
            <svg className="w-4 h-4 shrink-0 transition-transform"
                 style={{ transform: collapsed ? 'rotate(180deg)' : 'none' }}
                 fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
            {!collapsed && <span className="truncate">收起菜单</span>}
          </button>
        </div>
      </aside>

      <div className="flex-1 min-w-0 min-h-0 flex flex-col">
        {/* Mobile menu button -- the drawer sidebar is hidden on small screens, so there must be a way to bring it up */}
        <div className="md:hidden shrink-0 px-3 py-2 border-b" style={{ borderColor: 'var(--border-color)' }}>
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="workbench-nav-link px-2 py-1.5 rounded-lg text-sm flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
            {t(titleKey)}
          </button>
        </div>

        {/* The content area scrolls itself and **exclusively provides the gutter**.
            Pages never add their own padding or max-w + mx-auto --
            there used to be four layers (shell / Padded / module shell / page itself) that could each set it, so some had no margin,
            some floated centered, some hugged the menu: three behaviors under the same sidebar.
            **No width limit, no centering**: the left edge is already fixed by the sidebar, and another mx-auto would make
            "how far content sits from the menu" vary with window width, jumping on page switches.
            Pages that want "lists without page-level scrolling" use the h-full flex flex-col
            + flex-1 min-h-0 skeleton themselves (see the "List pages" section of the spec). */}
        <main className="workbench-main flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 lg:px-8 py-6">
          {/* Agency enterprise switcher -- must be **above the content, on every page**.
              It's the only entry for agencies to switch "which enterprise's data am I looking at" (selectEnterprise ->
              AgencyContext); for regular customers under an agency, it also shows the read-only notice
              "the current enterprise is managed by XX; enterprise business data is read-only".
              When neither role applies it renders null itself, so placing it here has zero impact on other roles.

              It used to be in WorkbenchLayout and was missed when this shell was extracted --
              removing the OpsLayout copy was intentional (agencies no longer enter the admin workbench), but adding one to the customer shell was forgotten,
              so nowhere on the site rendered it: agencies couldn't switch enterprises and managed customers couldn't see the read-only notice. */}
          <AgencyEnterpriseSwitcher />
          <Outlet context={context} />
        </main>
      </div>
    </div>
  );
}
