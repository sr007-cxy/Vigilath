import { useState, useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LanguageSwitcher } from './LanguageSwitcher';
import { ThemeToggle } from './ThemeToggle';
import { useTranslation } from 'react-i18next';
import { AuthModal } from './AuthModal';

interface NavLink {
  href: string;
  label: string;
}

export function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const location = useLocation();
  const { t } = useTranslation();
  const dropdownRef = useRef<HTMLDivElement>(null);

  const [user, setUser] = useState<string | null>(() => {
    try {
      const storedUser = localStorage.getItem('user');
      console.log('Navbar initial user:', storedUser);
      if (storedUser) {
        const parsedUser = JSON.parse(storedUser);
        console.log('Navbar initial parsedUser:', parsedUser);
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

  useEffect(() => {
    const loadUserFromLocalStorage = () => {
      try {
        const storedUser = localStorage.getItem('user');
        console.log('Navbar loadUserFromLocalStorage:', storedUser);
        if (storedUser) {
          const parsedUser = JSON.parse(storedUser);
          console.log('Navbar parsed user:', parsedUser);
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

    loadUserFromLocalStorage();

    const intervalId = setInterval(loadUserFromLocalStorage, 1000);

    return () => {
      clearInterval(intervalId);
    };
  }, []);

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

  const navLinks: NavLink[] = [
    { href: '/', label: t('nav.home') },
    { href: '/geo-knowledge', label: t('nav.geoKnowledge') },
    { href: '/products-services', label: t('nav.productsServices') },
    { href: '/about', label: t('nav.aboutUs') },
  ];

  const isActive = (href: string) => {
    return location.pathname === href;
  };

  const handleLogout = () => {
    localStorage.removeItem('user');
    localStorage.removeItem('token');
    setUser(null);
    setIsMenuOpen(false);
    setIsDropdownOpen(false);
    console.log('User logged out');
  };

  const toggleAuthModal = (tab: 'login' | 'register' = 'login') => {
    setAuthModalTab(tab);
    setIsAuthModalOpen(!isAuthModalOpen);
  };

  return (
    <nav className="fixed top-0 left-0 w-full px-4 sm:px-6 lg:px-8 py-4 backdrop-blur-xl border-b z-50" style={{ background: 'var(--bg-nav)', borderColor: 'var(--border-color)' }}>
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

        <div className="hidden md:flex items-center gap-6">
          {navLinks.map((link) => (
            <Link
              key={link.href}
              to={link.href}
              className={`text-sm font-medium transition-colors duration-300 ${isActive(link.href)
                  ? 'text-white'
                  : 'hover:text-white'
                }`}
              style={{ color: isActive(link.href) ? 'var(--text-primary)' : 'var(--text-secondary)' }}
            >
              {link.label}
            </Link>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <ThemeToggle />
          <LanguageSwitcher />

          {user ? (
            <div className="hidden md:flex relative" ref={dropdownRef} onMouseEnter={() => setIsDropdownOpen(true)} onMouseLeave={() => setIsDropdownOpen(false)}>
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
                <div
                  className="absolute right-0 mt-2 w-48 rounded-xl shadow-2xl overflow-hidden z-[61] animate-fade-in"
                  style={{
                    background: 'var(--bg-card)',
                    borderColor: 'var(--border-color)'
                  }}
                >
                  <div className="px-4 py-3 border-b border-gray-700/50">
                    <p className="text-sm text-gray-400">{t('nav.signedInAs')}</p>
                    <p className="text-sm font-medium text-primary truncate">{user}</p>
                  </div>
                  <div className="py-2">
                    <button
                      onClick={handleLogout}
                      className="w-full px-4 py-2 text-left text-sm transition-colors duration-200 flex items-center gap-2"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                      </svg>
                      {t('common.cancel')}
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <button
              onClick={() => toggleAuthModal('login')}
              className="hidden md:block text-sm font-medium transition-colors duration-300 hover:text-white" style={{ color: 'var(--text-secondary)' }}
            >
              {t('nav.login')}
            </button>
          )}

          <div className="md:hidden">
            <button
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              className="toggle-btn"
            >
              <svg className="h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                {isMenuOpen ? (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16m-7 6h7" />
                )}
              </svg>
            </button>
          </div>
        </div>
      </div>

      {isMenuOpen && (
        <div className="md:hidden mt-4 px-4 pb-4">
          <div className="flex flex-col gap-2">
            {
              navLinks.map((link) => (
                <Link
                  key={link.href}
                  to={link.href}
                  onClick={() => setIsMenuOpen(false)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${isActive(link.href) ? 'bg-opacity-20' : ''}`}
                  style={{
                    background: isActive(link.href) ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                    color: isActive(link.href) ? 'var(--accent-primary)' : 'var(--text-secondary)'
                  }}
                >
                  {link.label}
                </Link>
              ))
            }

            {user ? (
              <div className="space-y-2">
                <div className="px-4 py-2 rounded-lg text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
                  {user}
                </div>
                <button
                  onClick={handleLogout}
                  className="w-full px-4 py-2 rounded-lg text-sm font-medium transition-colors text-left"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {t('common.cancel')}
                </button>
              </div>
            ) : (
              <button
                onClick={() => {
                  toggleAuthModal('login');
                  setIsMenuOpen(false);
                }}
                className="w-full px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                style={{ color: 'var(--text-secondary)' }}
              >
                {t('nav.login')}
              </button>
            )}
          </div>
        </div>
      )}

      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={toggleAuthModal}
        defaultTab={authModalTab}
      />
    </nav>
  );
}
