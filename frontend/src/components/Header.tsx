import { useState, useEffect, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ThemeToggle } from './ThemeToggle';
import { useAuth } from '../contexts/AuthContext';
import { displayName } from '../utils/userDisplay';
import { useAuthModal } from '../contexts/AuthModalContext';
import { authApi } from '../services/authApi';
import { balanceApi, points } from '../services/balanceApi';
import { SOLUTION_COLUMNS, solutionsByColumn, type Locale } from '../data/solutions';
import { SolutionIcon } from './SolutionIcon';

export function Header() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const dropdownRef = useRef<HTMLDivElement>(null);
  const homeMenuRef = useRef<HTMLDivElement>(null);
  const solutionsMenuRef = useRef<HTMLDivElement>(null);
  // ICP filing remediation (D3-a): the site is Chinese-only, locale is always zh.
  const locale: Locale = 'zh';

  const navItems = [
    { to: '/', label: t('nav.home') },
    { to: '/geo-knowledge', label: t('nav.geoKnowledge') },
    { to: '/products-services', label: t('nav.productsServices') },
    { to: '/cases', label: t('nav.cases') },
    { to: '/blog', label: t('nav.blog') },
    { to: '/faq', label: t('nav.faq') },
    { to: '/about', label: t('nav.aboutUs') },
  ];

  // Anchor items in the "Home" hover dropdown jump to the matching section of the home page (Home.tsx listens to
  // location.hash and calls scrollIntoView).
  const homeAnchors = [
    { hash: '#pipeline', label: t('nav.overview') },
    { hash: '#brand-growth', label: t('nav.capBrandGrowth') },
    { hash: '#detection', label: t('nav.capDetection') },
    { hash: '#engines', label: t('nav.capEngines') },
  ];

  const isActive = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path);

  // Highlighting of the workbench entries ("Workbench" /app and "Admin workbench" /ops) -- **judged by where you currently are**.
  //
  // Both used to be hardcoded: /ops always accent-primary + 600, /app always text-secondary.
  // So while in "Workbench", the dropdown highlighted "Admin workbench", reading as if you were in the admin console.
  // The desktop dropdown and mobile menu each have their own rendering; the rule lives here, don't write separate colors again.
  const navEntryStyle = (active: boolean) => (active
    ? { color: 'var(--accent-primary)', fontWeight: 600 }
    : { color: 'var(--text-secondary)' });

  const { isLoggedIn, user, isStaff, isMaintainer, isTenant, setUser, clearAuth } = useAuth();
  // Credits in the top bar: with usage-based billing, "how much do I have left" is a number to check before every action;
  // hiding it two menu levels deep in the account center is as good as not having it. Throttled to 30s -- requesting on every route change is too noisy.
  const [balanceCents, setBalanceCents] = useState<number | null>(null);
  const balanceFetchedAt = useRef(0);
  // The admin workbench is only open to admin / operator / maintainer -- customers and agencies use /app,
  // no longer sharing a single "Workbench" entry with ops (exactly the root of the two sides' menus getting mixed).
  const canEnterOps = isStaff || isMaintainer;
  // Displayed identity uses displayName: WeChat nickname -> phone number (masked) -> real email.
  // Using email directly would expose placeholder addresses like 13800000000@wx.vigilath.com to users.
  const userLabel = displayName(user);
  const { openAuthModal } = useAuthModal();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isHomeMenuOpen, setIsHomeMenuOpen] = useState(false);
  const [isSolutionsMenuOpen, setIsSolutionsMenuOpen] = useState(false);
  const [isMobileSolutionsOpen, setIsMobileSolutionsOpen] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  useEffect(() => {
    if (!isLoggedIn) { setBalanceCents(null); return; }
    const token = localStorage.getItem('token');
    if (!token) return;
    // The throttle timestamp is set only **after success**. It used to be set before sending the request, while cancelled ignored
    // the result -- if the first-screen request was interrupted by an effect rerun (any route change does it), the response was dropped
    // but throttling had already kicked in, so no retry for 30 seconds, credits stayed null forever and the whole pill didn't render.
    if (Date.now() - balanceFetchedAt.current < 30_000) return;
    balanceApi.get(token)
      .then(b => { balanceFetchedAt.current = Date.now(); setBalanceCents(b.balance_cents); })
      .catch(() => { /* if unavailable, don't show it and don't interrupt navigation; retry on the next route change */ });
  }, [isLoggedIn, location.pathname]);

  const handleLogout = () => {
    clearAuth();
    setBalanceCents(null);
    setIsDropdownOpen(false);
    setIsMobileMenuOpen(false);
    navigate('/');
  };

  const toggleAuthModal = (tab: 'login' | 'register' = 'login') => {
    openAuthModal(tab);
    setIsMobileMenuOpen(false);
  };

  // Close mobile menu on route change
  useEffect(() => {
    setIsMobileMenuOpen(false);
  }, [location.pathname]);

  // Prevent body scroll when mobile menu is open
  useEffect(() => {
    if (isMobileMenuOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isMobileMenuOpen]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };

    if (isDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isDropdownOpen]);

  // Hover dropdowns mainly close via onMouseLeave, with clicking outside as a fallback (keyboard/trackpad edge cases).
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (homeMenuRef.current && !homeMenuRef.current.contains(event.target as Node)) {
        setIsHomeMenuOpen(false);
      }
    };
    if (isHomeMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isHomeMenuOpen]);

  // "Solutions" mega-menu: clicking outside closes it as a fallback (mainly closed on hover-out).
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (solutionsMenuRef.current && !solutionsMenuRef.current.contains(event.target as Node)) {
        setIsSolutionsMenuOpen(false);
      }
    };
    if (isSolutionsMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isSolutionsMenuOpen]);

  // Self-healing: old-version logins or the persistUser fallback wrote only email, without is_admin / role.
  // When the login session holds but the profile lacks permission fields, call /me once with the token to fill them in,
  // so admins / ops don't need to log in again to see the workbench entry.
  useEffect(() => {
    if (!isLoggedIn) return;
    if (!user) return;
    if (typeof user.is_admin !== 'undefined' && typeof user.role !== 'undefined') return;
    const token = localStorage.getItem('token');
    if (!token) return;
    authApi
      .getCurrentUser(token)
      .then((me) => setUser(me))
      .catch(() => {});
  }, [isLoggedIn, user, setUser]);

  return (
    <header
      className="fixed top-0 left-0 w-full px-4 sm:px-6 lg:px-8 py-3 md:py-4 backdrop-blur-xl border-b z-50 nav-surface"
    >
      <div className="max-w-[1400px] mx-auto flex justify-between items-center">
        <div className="flex items-center gap-3">
          <Link to="/" className="flex items-center gap-2" aria-label="Vigilath">
            <img
              src="/image/logo.svg"
              alt=""
              aria-hidden="true"
              className="brand-logo h-8 md:h-9 w-auto select-none"
              draggable={false}
            />
            <span className="text-xl md:text-2xl font-black tracking-tight text-primary select-none">
              Vigilath
            </span>
          </Link>
        </div>

        {/* Desktop navigation */}
        <nav className="hidden md:flex items-center gap-8">
          {/* Home: dropdown on hover with the home page section anchors inside; clicking "Home" itself goes back to / */}
          <div
            className="relative"
            ref={homeMenuRef}
            onMouseEnter={() => setIsHomeMenuOpen(true)}
            onMouseLeave={() => setIsHomeMenuOpen(false)}
          >
            <Link
              to="/"
              className="flex items-center gap-1 text-sm font-medium transition-colors duration-200"
              style={{ color: isActive('/') || isHomeMenuOpen ? 'var(--text-primary)' : 'var(--text-secondary)' }}
            >
              {t('nav.home')}
              <svg
                className="w-3.5 h-3.5 flex-shrink-0 transition-transform duration-200"
                style={{ transform: isHomeMenuOpen ? 'rotate(180deg)' : 'none' }}
                xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </Link>

            {isHomeMenuOpen && (
              <div className="absolute left-0 top-full pt-2 w-48 z-[61]">
                <div className="rounded-xl shadow-xl overflow-hidden animate-fade-in border bg-surface border-soft py-2">
                  {homeAnchors.map((a) => (
                    <Link
                      key={a.hash}
                      to={{ pathname: '/', hash: a.hash }}
                      onClick={() => setIsHomeMenuOpen(false)}
                      className="block w-full px-4 py-2 text-left text-sm transition-colors duration-200 bg-surface-hover"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {a.label}
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Solutions: full-width three-column mega-menu on hover */}
          <div
            className="relative"
            ref={solutionsMenuRef}
            onMouseEnter={() => setIsSolutionsMenuOpen(true)}
            onMouseLeave={() => setIsSolutionsMenuOpen(false)}
          >
            <button
              className="flex items-center gap-1 text-sm font-medium transition-colors duration-200"
              style={{ color: location.pathname.startsWith('/solutions') || isSolutionsMenuOpen ? 'var(--text-primary)' : 'var(--text-secondary)' }}
            >
              {t('nav.solutions')}
              <svg
                className="w-3.5 h-3.5 flex-shrink-0 transition-transform duration-200"
                style={{ transform: isSolutionsMenuOpen ? 'rotate(180deg)' : 'none' }}
                xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {isSolutionsMenuOpen && (
              <div className="absolute left-0 top-full pt-3 w-[min(92vw,940px)] z-[61]">
                <div className="rounded-2xl shadow-2xl overflow-hidden animate-fade-in border bg-surface border-soft">
                  <div className="grid grid-cols-3">
                    {SOLUTION_COLUMNS.map((col, ci) => {
                      const leaves = solutionsByColumn(col.id);
                      return (
                        <div
                          key={col.id}
                          className={`p-5 ${ci > 0 ? 'border-l border-soft' : ''}`}
                        >
                          {/* List header: dedicated icon + title + view all */}
                          <div className="flex items-center gap-3 mb-4 pb-3 border-b border-soft">
                            <span className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 bg-accent-primary/10">
                              <SolutionIcon name={`col:${col.id}`} className="w-5 h-5" strokeWidth={1.8} />
                            </span>
                            <div className="flex flex-col leading-tight min-w-0">
                              <span className="text-sm font-bold text-primary truncate">{col.title[locale]}</span>
                              <Link
                                to={`/solutions/${leaves[0]?.slug ?? ''}`}
                                onClick={() => setIsSolutionsMenuOpen(false)}
                                className="text-xs font-medium hover:underline"
                                style={{ color: 'var(--accent-primary)' }}
                              >
                                {t('nav.viewAll')} →
                              </Link>
                            </div>
                          </div>
                          {/* Leaf items: own icon + label, whole row highlighted on hover */}
                          <ul className="space-y-0.5">
                            {leaves.map((leaf) => (
                              <li key={leaf.slug}>
                                <Link
                                  to={`/solutions/${leaf.slug}`}
                                  onClick={() => setIsSolutionsMenuOpen(false)}
                                  className="group flex items-center gap-2.5 rounded-lg px-2 py-2 -mx-2 transition-colors duration-150 hover:bg-surface-hover"
                                >
                                  <span className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors duration-150 bg-surface-hover group-hover:bg-accent-primary/15 text-accent-primary">
                                    <SolutionIcon name={leaf.icon} className="w-4 h-4 transition-colors duration-150" />
                                  </span>
                                  <span
                                    className="text-sm transition-colors duration-150 group-hover:text-primary"
                                    style={{ color: 'var(--text-secondary)' }}
                                  >
                                    {leaf.navLabel[locale]}
                                  </span>
                                </Link>
                              </li>
                            ))}
                          </ul>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Other route items (skipping Home) */}
          {navItems.slice(1).map((item) => {
            const active = isActive(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className="text-sm font-medium transition-colors duration-200"
                style={{ color: active ? 'var(--text-primary)' : 'var(--text-secondary)' }}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Desktop right side */}
        <div className="hidden md:flex items-center gap-3">
          <ThemeToggle />

          {isLoggedIn ? (
            <div
              className="relative"
              ref={dropdownRef}
              onMouseEnter={() => setIsDropdownOpen(true)}
              onMouseLeave={() => setIsDropdownOpen(false)}
            >
              <button
                className="flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors duration-200 bg-surface border-soft border-soft-hover"
              >
                <span className="text-sm font-medium max-w-[120px] truncate" style={{ color: 'var(--text-primary)' }}>{userLabel}</span>
                <svg className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--text-secondary)' }} xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {isDropdownOpen && (
                <div className="absolute right-0 top-full pt-2 w-48 z-[61]">
                  <div className="rounded-xl shadow-xl overflow-hidden animate-fade-in border bg-surface border-soft">
                    <div className="px-4 py-3 border-b border-soft">
                      <p className="text-sm text-muted">{t('nav.signedInAs')}</p>
                      <p className="text-sm font-medium truncate text-primary">{userLabel}</p>
                      {/* The balance sits right below the identity -- where users recognize their own account, and where credits most need to be seen */}
                      {balanceCents !== null && (
                        <div className="mt-2 flex items-baseline justify-between gap-2">
                          <span className="text-sm font-semibold" style={{ color: 'var(--accent-primary)' }}>
                            {points(balanceCents)}
                          </span>
                          <Link
                            to="/account/billing?tab=balance"
                            onClick={() => setIsDropdownOpen(false)}
                            className="text-xs underline"
                            style={{ color: 'var(--text-secondary)' }}
                          >
                            {t('nav.pointsTopup', '明细 / 充值')}
                          </Link>
                        </div>
                      )}
                    </div>
                    <div className="py-2">
                      {canEnterOps && (
                        <Link
                          to="/ops"
                          onClick={() => setIsDropdownOpen(false)}
                          className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                          style={navEntryStyle(isActive('/ops'))}
                        >
                          <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                          </svg>
                          {t('nav.opsTitle')}
                        </Link>
                      )}
                      {isTenant && (
                        <Link
                          to="/gateway-portal"
                          onClick={() => setIsDropdownOpen(false)}
                          className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                          style={{ color: 'var(--accent-primary)', fontWeight: 600 }}
                        >
                          <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-4m3 4v-8m3 8v-2M5 21h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v14a2 2 0 002 2z" />
                          </svg>
                          {t('nav.gatewayPortal')}
                        </Link>
                      )}
                      {!isTenant && (<>
                      {/* Customer-side entries: the workbench is the main entry, the report center is a standalone module, and sentiment monitoring also appears in the workbench sidebar */}
                      <Link
                        to="/app"
                        onClick={() => setIsMobileMenuOpen(false)}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                        style={navEntryStyle(isActive('/app'))}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                        </svg>
                        {t('nav.appTitle')}
                      </Link>
                      <Link
                        to="/reports"
                        onClick={() => setIsMobileMenuOpen(false)}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-4m3 4v-8m3 8v-2M5 21h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v14a2 2 0 002 2z" />
                        </svg>
                        {t('nav.appReports')}
                      </Link>
                      <Link
                        to="/account"
                        onClick={() => setIsMobileMenuOpen(false)}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        {t('nav.account')}
                      </Link>
                      </>)}
                      <button
                        onClick={handleLogout}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                        </svg>
                        {t('nav.logout')}
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <button
                onClick={() => toggleAuthModal('login')}
                className="btn-secondary inline-flex items-center h-9 px-4 rounded-lg text-sm font-semibold transition-all duration-200"
              >
                {t('nav.login')}
              </button>
              <button
                onClick={() => toggleAuthModal('register')}
                className="btn-solid inline-flex items-center h-9 px-4 rounded-lg text-sm font-semibold transition-all duration-200"
              >
                {t('nav.register')}
              </button>
            </div>
          )}
        </div>

        {/* Mobile right side: compact controls + hamburger */}
        <div className="flex md:hidden items-center gap-2">
          <ThemeToggle />

          {/* Hamburger button */}
          <button
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            className="h-9 w-9 flex items-center justify-center rounded-lg border transition bg-surface border-soft"
            aria-label="Toggle menu"
          >
            {isMobileMenuOpen ? (
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" style={{ color: 'var(--text-primary)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" style={{ color: 'var(--text-primary)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Mobile menu drawer */}
      {isMobileMenuOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 top-0 bg-black/40 z-40 md:hidden"
            onClick={() => setIsMobileMenuOpen(false)}
          />
          {/* Menu panel */}
          <div
            className="fixed top-[57px] left-0 right-0 z-50 md:hidden animate-fade-in border-b"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border-color)' }}
          >
            <nav className="flex flex-col px-4 py-3 gap-1">
              {/* Home + anchor sublist (no hover on mobile, expanded directly) */}
              <Link
                to="/"
                onClick={() => setIsMobileMenuOpen(false)}
                className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200"
                style={{
                  color: isActive('/') ? 'var(--text-primary)' : 'var(--text-secondary)',
                  background: isActive('/') ? 'var(--bg-surface-hover)' : 'transparent',
                }}
              >
                {t('nav.home')}
              </Link>
              {homeAnchors.map((a) => (
                <Link
                  key={a.hash}
                  to={{ pathname: '/', hash: a.hash }}
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="text-sm font-medium py-2.5 pl-6 pr-3 rounded-lg transition-colors duration-200"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {a.label}
                </Link>
              ))}

              {/* Solutions: collapsible group */}
              <button
                onClick={() => setIsMobileSolutionsOpen((v) => !v)}
                className="flex items-center justify-between text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200"
                style={{ color: location.pathname.startsWith('/solutions') ? 'var(--text-primary)' : 'var(--text-secondary)' }}
              >
                {t('nav.solutions')}
                <svg
                  className="w-4 h-4 flex-shrink-0 transition-transform duration-200"
                  style={{ transform: isMobileSolutionsOpen ? 'rotate(180deg)' : 'none' }}
                  xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {isMobileSolutionsOpen && (
                <div className="pb-1">
                  {SOLUTION_COLUMNS.map((col) => (
                    <div key={col.id} className="mb-1">
                      <p className="text-xs font-semibold pl-6 pr-3 py-1.5" style={{ color: 'var(--text-muted)' }}>
                        {col.title[locale]}
                      </p>
                      {solutionsByColumn(col.id).map((leaf) => (
                        <Link
                          key={leaf.slug}
                          to={`/solutions/${leaf.slug}`}
                          onClick={() => setIsMobileMenuOpen(false)}
                          className="block text-sm py-2 pl-8 pr-3 rounded-lg transition-colors duration-200"
                          style={{ color: 'var(--text-secondary)' }}
                        >
                          {leaf.navLabel[locale]}
                        </Link>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {/* Other route items (skipping Home) */}
              {navItems.slice(1).map((item) => {
                const active = isActive(item.to);
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    onClick={() => setIsMobileMenuOpen(false)}
                    className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200"
                    style={{
                      color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                      background: active ? 'var(--bg-surface-hover)' : 'transparent',
                    }}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>

            <div className="px-4 py-3 border-t" style={{ borderColor: 'var(--border-color)' }}>
              {isLoggedIn ? (
                <div className="flex flex-col gap-1">
                  <div className="px-3 py-2">
                    <p className="text-xs text-muted">{t('nav.signedInAs')}</p>
                    <p className="text-sm font-medium truncate text-primary">{userLabel}</p>
                  </div>
                  {canEnterOps && (
                    <Link
                      to="/ops"
                      onClick={() => setIsMobileMenuOpen(false)}
                      className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2"
                      style={navEntryStyle(isActive('/ops'))}
                    >
                      <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                      </svg>
                      {t('nav.opsTitle')}
                    </Link>
                  )}
                  {isTenant && (
                    <Link
                      to="/gateway-portal"
                      onClick={() => setIsMobileMenuOpen(false)}
                      className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2"
                      style={{ color: 'var(--accent-primary)', fontWeight: 600 }}
                    >
                      <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-4m3 4v-8m3 8v-2M5 21h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v14a2 2 0 002 2z" />
                      </svg>
                      {t('nav.gatewayPortal')}
                    </Link>
                  )}
                  {!isTenant && (<>
                  <Link
                    to="/app"
                    onClick={() => setIsMobileMenuOpen(false)}
                    className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2"
                    style={navEntryStyle(isActive('/app'))}
                  >
                    <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                    </svg>
                    {t('nav.appTitle')}
                  </Link>
                  <Link
                    to="/reports"
                    onClick={() => setIsMobileMenuOpen(false)}
                    className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-4m3 4v-8m3 8v-2M5 21h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v14a2 2 0 002 2z" />
                    </svg>
                    {t('nav.appReports')}
                  </Link>
                  <Link
                    to="/account"
                    onClick={() => setIsMobileMenuOpen(false)}
                    className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    {t('nav.account')}
                  </Link>
                  </>)}
                  <button
                    onClick={handleLogout}
                    className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2 text-left"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                    </svg>
                    {t('nav.logout')}
                  </button>
                </div>
              ) : (
                <div className="flex gap-2">
                  <button
                    onClick={() => toggleAuthModal('login')}
                    className="btn-secondary flex-1 inline-flex items-center justify-center h-10 rounded-lg text-sm font-semibold transition-all duration-200"
                  >
                    {t('nav.login')}
                  </button>
                  <button
                    onClick={() => toggleAuthModal('register')}
                    className="btn-solid flex-1 inline-flex items-center justify-center h-10 rounded-lg text-sm font-semibold transition-all duration-200"
                  >
                    {t('nav.register')}
                  </button>
                </div>
              )}
            </div>
          </div>
        </>
      )}

    </header>
  );
}
