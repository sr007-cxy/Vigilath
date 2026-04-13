import { useState, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LanguageSwitcher } from './LanguageSwitcher';
import { ThemeToggle } from './ThemeToggle';
import { useTranslation } from 'react-i18next';

interface NavLink {
  href: string;
  label: string;
}

export function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const location = useLocation();
  const { t } = useTranslation();

  // 使用 useState 钩子的初始化函数来从本地存储中读取用户信息
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

  // 在组件挂载时，使用 setInterval 来定期检查本地存储的变化
  useEffect(() => {
    // 从本地存储中读取用户信息
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
        setUser(null);
      }
    };

    // 初始加载用户信息
    loadUserFromLocalStorage();

    // 使用 setInterval 来定期检查本地存储的变化，每1000毫秒检查一次
    const intervalId = setInterval(loadUserFromLocalStorage, 1000);

    return () => {
      // 清除定时器
      clearInterval(intervalId);
    };
  }, []);



  const navLinks: NavLink[] = [
    { href: '/', label: t('nav.home') },
    { href: '/geo-knowledge', label: t('nav.geoKnowledge') },
    { href: '/products-services', label: t('nav.productsServices') },
    { href: '/about', label: t('nav.aboutUs') },
  ];

  const isActive = (href: string) => {
    return location.pathname === href;
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
              className={`text-sm font-medium transition-colors duration-300 ${
                isActive(link.href)
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
            <div className="hidden md:block text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
              {user}
            </div>
          ) : (
            <Link to="/login" className="hidden md:block text-sm font-medium transition-colors duration-300 hover:text-white" style={{ color: 'var(--text-secondary)' }}>
              {t('nav.login')}
            </Link>
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
              <div className="px-4 py-2 rounded-lg text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
                {user}
              </div>
            ) : (
              <Link
                to="/login"
                onClick={() => setIsMenuOpen(false)}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                style={{ color: 'var(--text-secondary)' }}
              >
                {t('nav.login')}
              </Link>
            )}
          </div>
        </div>
      )}
    </nav>
  );
}
