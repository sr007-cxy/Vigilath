import { useState, useEffect, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ThemeToggle } from './ThemeToggle';
import { useAuth } from '../contexts/AuthContext';
import { useAuthModal } from '../contexts/AuthModalContext';
import { switchLanguage } from '../i18n';

export function Header() {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const dropdownRef = useRef<HTMLDivElement>(null);

  const navItems = [
    { to: '/', label: t('nav.home') },
    { to: '/geo-knowledge', label: t('nav.geoKnowledge') },
    { to: '/products-services', label: t('nav.productsServices') },
    { to: '/about', label: t('nav.aboutUs') },
  ];

  const isActive = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path);

  const [user, setUser] = useState<string | null>(() => {
    try {
      const storedUser = localStorage.getItem('user');
      console.log('Header initial user:', storedUser);
      if (storedUser) {
        const parsedUser = JSON.parse(storedUser);
        console.log('Header initial parsedUser:', parsedUser);
        return parsedUser.email;
      }
    } catch (error) {
      console.error('Error parsing user from localStorage:', error);
      localStorage.removeItem('user');
    }
    return null;
  });

  const { isLoggedIn } = useAuth();
  const { openAuthModal } = useAuthModal();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const toggleLanguage = () => {
    const newLang = i18n.language === 'en' ? 'zh' : 'en';
    // Must route through switchLanguage() so the target-language pack is
    // loaded BEFORE changeLanguage flips the active lng. Calling
    // i18n.changeLanguage() directly leaves the new lng with no resources
    // attached → react-i18next suspends forever → Suspense fallback looks
    // like a white screen to the user.
    switchLanguage(newLang);
  };

  const loadUserFromLocalStorage = () => {
    try {
      const storedUser = localStorage.getItem('user');
      if (storedUser) {
        const parsedUser = JSON.parse(storedUser);
        setUser(parsedUser.email);
      } else {
        setUser(null);
      }
    } catch (error) {
      console.error('Error parsing user from localStorage:', error);
      localStorage.removeItem('user');
      localStorage.removeItem('token');
      setUser(null);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('user');
    localStorage.removeItem('token');
    setUser(null);
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

  useEffect(() => {
    loadUserFromLocalStorage();

    const handleStorage = () => loadUserFromLocalStorage();
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  // Reactive: when login state changes (same tab), refresh user display immediately
  useEffect(() => {
    loadUserFromLocalStorage();
  }, [isLoggedIn]);

  return (
    <header
      className="fixed top-0 left-0 w-full px-4 sm:px-6 lg:px-8 py-3 md:py-4 backdrop-blur-xl border-b z-50 nav-surface"
    >
      <div className="max-w-7xl mx-auto flex justify-between items-center">
        <div className="flex items-center gap-3">
          <Link to="/" className="flex items-center" aria-label="GApex">
            <picture>
              <source srcSet="/image/logo.webp" type="image/webp" />
              <img
                src="/image/logo.png"
                alt="GApex"
                className="brand-logo h-8 md:h-9 w-auto select-none"
                draggable={false}
              />
            </picture>
          </Link>
        </div>

        {/* Desktop navigation */}
        <nav className="hidden md:flex items-center gap-8">
          {navItems.map((item) => {
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

          <button
            onClick={toggleLanguage}
            className="h-9 px-3 flex items-center gap-1 rounded-full border text-sm font-semibold transition bg-surface border-soft border-soft-hover"
            style={{ color: 'var(--text-primary)' }}
          >
            <span style={{ color: i18n.language === 'en' ? 'var(--accent-primary)' : 'var(--text-muted)' }}>EN</span>
            <span style={{ color: 'var(--text-muted)' }}>/</span>
            <span style={{ color: i18n.language === 'zh' ? 'var(--accent-primary)' : 'var(--text-muted)' }}>中文</span>
          </button>

          {user ? (
            <div
              className="relative"
              ref={dropdownRef}
              onMouseEnter={() => setIsDropdownOpen(true)}
              onMouseLeave={() => setIsDropdownOpen(false)}
            >
              <button
                className="flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors duration-200 bg-surface border-soft border-soft-hover"
              >
                <span className="text-sm font-medium max-w-[120px] truncate" style={{ color: 'var(--text-primary)' }}>{user}</span>
                <svg className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--text-secondary)' }} xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {isDropdownOpen && (
                <div className="absolute right-0 top-full pt-2 w-48 z-[61]">
                  <div className="rounded-xl shadow-xl overflow-hidden animate-fade-in border bg-surface border-soft">
                    <div className="px-4 py-3 border-b border-soft">
                      <p className="text-sm text-muted">{t('nav.signedInAs')}</p>
                      <p className="text-sm font-medium truncate text-primary">{user}</p>
                    </div>
                    <div className="py-2">
                      <Link
                        to="/dashboard"
                        onClick={() => setIsDropdownOpen(false)}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm0 8a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zm12 0a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
                        </svg>
                        {t('nav.dashboard')}
                      </Link>
                      <Link
                        to="/sentiment"
                        onClick={() => setIsDropdownOpen(false)}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                        </svg>
                        {t('nav.sentiment')}
                      </Link>
                      <Link
                        to="/account"
                        onClick={() => setIsDropdownOpen(false)}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 bg-surface-hover"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        {t('nav.account')}
                      </Link>
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

          <button
            onClick={toggleLanguage}
            className="h-8 px-2 flex items-center gap-0.5 rounded-full border text-xs font-semibold transition bg-surface border-soft"
            style={{ color: 'var(--text-primary)' }}
          >
            <span style={{ color: i18n.language === 'en' ? 'var(--accent-primary)' : 'var(--text-muted)' }}>EN</span>
            <span style={{ color: 'var(--text-muted)' }}>/</span>
            <span style={{ color: i18n.language === 'zh' ? 'var(--accent-primary)' : 'var(--text-muted)' }}>中文</span>
          </button>

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
              {navItems.map((item) => {
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
              {user ? (
                <div className="flex flex-col gap-1">
                  <div className="px-3 py-2">
                    <p className="text-xs text-muted">{t('nav.signedInAs')}</p>
                    <p className="text-sm font-medium truncate text-primary">{user}</p>
                  </div>
                  <Link
                    to="/dashboard"
                    onClick={() => setIsMobileMenuOpen(false)}
                    className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm0 8a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zm12 0a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
                    </svg>
                    {t('nav.dashboard')}
                  </Link>
                  <Link
                    to="/sentiment"
                    onClick={() => setIsMobileMenuOpen(false)}
                    className="text-sm font-medium py-2.5 px-3 rounded-lg transition-colors duration-200 flex items-center gap-2"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                    </svg>
                    {t('nav.sentiment')}
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
