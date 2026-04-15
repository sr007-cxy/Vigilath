import { useState, useEffect, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ThemeToggle } from './ThemeToggle';
import { AuthModal } from './AuthModal';

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
    navigate('/');
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
    <header
      className="fixed top-0 left-0 w-full px-4 sm:px-6 lg:px-8 py-4 backdrop-blur-xl border-b z-50 nav-surface"
    >
      <div className="max-w-7xl mx-auto flex justify-between items-center">
        <div className="flex items-center gap-3">
          <Link to="/" className="flex items-center gap-2.5">
            <svg className="h-8 w-8" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="50" cy="50" r="45" stroke="currentColor" strokeWidth="4" style={{ color: 'var(--accent-primary)' }} />
              <path d="M30 50 L45 65 L70 35" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--accent-primary)' }} />
            </svg>
            <span className="text-xl font-bold tracking-tight gradient-text">
              GApex
            </span>
          </Link>
        </div>

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

        <div className="flex items-center gap-3">
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
                <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{user}</span>
                <svg className="w-4 h-4" style={{ color: 'var(--text-secondary)' }} xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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
      </div>

      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={toggleAuthModal}
        defaultTab={authModalTab}
      />
    </header>
  );
}
