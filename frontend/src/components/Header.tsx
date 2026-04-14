import { useState, useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ThemeToggle } from './ThemeToggle';
import { AuthModal } from './AuthModal';

export function Header() {
  const { t, i18n } = useTranslation();
  const location = useLocation();
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

  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);
  const [authModalTab, setAuthModalTab] = useState<'login' | 'register'>('login');
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  const toggleLanguage = () => {
    const newLang = i18n.language === 'en' ? 'zh' : 'en';
    i18n.changeLanguage(newLang);
  };

  const loadUserFromLocalStorage = () => {
    try {
      const storedUser = localStorage.getItem('user');
      console.log('Header loadUserFromLocalStorage:', storedUser);
      if (storedUser) {
        const parsedUser = JSON.parse(storedUser);
        console.log('Header parsed user:', parsedUser);
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
    console.log('User logged out');
  };

  const toggleAuthModal = (tab: 'login' | 'register' = 'login') => {
    setAuthModalTab(tab);
    setIsAuthModalOpen(!isAuthModalOpen);
  };

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
    const intervalId = setInterval(loadUserFromLocalStorage, 1000);

    return () => {
      clearInterval(intervalId);
    };
  }, []);

  console.log('Header user state:', user);

  return (
    <header className="fixed top-0 left-0 w-full px-4 sm:px-6 lg:px-8 py-4 backdrop-blur-xl border-b z-50" style={{ background: 'var(--bg-nav)', borderColor: 'var(--border-color)' }}>
      <div className="max-w-7xl mx-auto flex justify-between items-center">
        <div className="flex items-center gap-3">
          <Link to="/" className="flex items-center gap-3">
            <svg className="h-8 w-8" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="50" cy="50" r="45" stroke="url(#gradient)" strokeWidth="4" />
              <path d="M30 50 L45 65 L70 35" stroke="url(#gradient)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
              <defs>
                <linearGradient id="gradient" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#00f0ff" />
                  <stop offset="50%" stopColor="#7b61ff" />
                  <stop offset="100%" stopColor="#ff006e" />
                </linearGradient>
              </defs>
            </svg>
            <span className="text-xl font-bold" style={{ background: 'var(--accent-gradient)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              GEO Checker
            </span>
          </Link>
        </div>

        <nav className="hidden md:flex items-center gap-6">
          {navItems.map((item) => {
            const active = isActive(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className="text-sm font-medium transition-colors duration-300 hover:text-white"
                style={{ color: active ? '#ffffff' : 'var(--text-secondary)' }}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-3">
          <ThemeToggle />

          <button
            onClick={toggleLanguage}
            className="h-10 px-3 flex items-center gap-1 rounded-full border text-sm font-semibold transition"
            style={{
              background: 'var(--bg-tertiary)',
              borderColor: 'var(--border-color)',
              color: 'var(--text-secondary)'
            }}
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
                className="flex items-center gap-2 px-3 py-2 rounded-lg transition-colors duration-200"
                style={{
                  background: 'var(--bg-tertiary)',
                  borderColor: 'var(--border-color)',
                  color: 'var(--text-secondary)'
                }}
              >
                <span className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
                  {user}
                </span>
                <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {isDropdownOpen && (
                <div className="absolute right-0 top-full pt-2 w-48 z-[61]">
                  <div
                    className="rounded-xl shadow-2xl overflow-hidden animate-fade-in border"
                    style={{
                      background: 'var(--bg-card)',
                      borderColor: 'var(--border-color)'
                    }}
                  >
                    <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border-color)' }}>
                      <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('nav.signedInAs')}</p>
                      <p className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)' }}>{user}</p>
                    </div>
                    <div className="py-2">
                      <button
                        onClick={handleLogout}
                        className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2 hover:bg-white/5"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                        </svg>
                        {t('nav.logout', '退出登录')}
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
                className="inline-flex items-center gap-1.5 h-10 px-4 rounded-lg text-sm font-semibold transition-all duration-300 hover:-translate-y-0.5"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)'
                }}
              >
                <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
                {t('nav.login')}
              </button>
              {/* <button
                onClick={() => toggleAuthModal('register')}
                className="inline-flex items-center gap-1.5 h-10 px-4 rounded-lg text-sm font-semibold text-white transition-all duration-300 hover:-translate-y-0.5"
                style={{
                  background: 'var(--accent-gradient)',
                  boxShadow: 'var(--glow-primary)',
                  border: 'none'
                }}
              >
                <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
                </svg>
                {t('nav.register')}
              </button> */}
            </div>
          )}
        </div>
      </div>

      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={toggleAuthModal}
        defaultTab={authModalTab}
      />
    </header>
  );
}
