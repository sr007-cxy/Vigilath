import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ThemeToggle } from './ThemeToggle';

export function Header() {
  const { t, i18n } = useTranslation();
  
  // 使用 useState 钩子的初始化函数来从本地存储中读取用户信息
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

  const toggleLanguage = () => {
    const newLang = i18n.language === 'en' ? 'zh' : 'en';
    i18n.changeLanguage(newLang);
  };

  // 从本地存储中读取用户信息
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
      setUser(null);
    }
  };

  // 使用 setInterval 来定期检查本地存储的变化，每1000毫秒检查一次
  useEffect(() => {
    const intervalId = setInterval(loadUserFromLocalStorage, 1000);

    return () => {
      // 清除定时器
      clearInterval(intervalId);
    };
  }, []);

  // 输出 user 状态的值
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
          <Link to="/" className="text-sm font-medium transition-colors duration-300 hover:text-white" style={{ color: 'var(--text-secondary)' }}>
            {t('nav.home')}
          </Link>
          <Link to="/geo-knowledge" className="text-sm font-medium transition-colors duration-300 hover:text-white" style={{ color: 'var(--text-secondary)' }}>
            {t('nav.geoKnowledge')}
          </Link>
          <Link to="/products-services" className="text-sm font-medium transition-colors duration-300 hover:text-white" style={{ color: 'var(--text-secondary)' }}>
            {t('nav.productsServices')}
          </Link>
          <Link to="/about" className="text-sm font-medium transition-colors duration-300 hover:text-white" style={{ color: 'var(--text-secondary)' }}>
            {t('nav.aboutUs')}
          </Link>
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
            <div className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
              {user}
            </div>
          ) : (
            <Link 
              to="/login" 
              className="btn-primary text-sm px-6 py-2"
            >
              {t('nav.login')}
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
