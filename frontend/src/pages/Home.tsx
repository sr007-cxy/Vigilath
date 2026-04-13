import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { geoApi } from '../services/geoApi';

interface User {
  email: string;
}

export function Home() {
  const { t } = useTranslation();
  const [url, setUrl] = useState('');
  const [keyword, setKeyword] = useState('');
  const [checkType, setCheckType] = useState('website');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [user, setUser] = useState<string | null>(null);
  const navigate = useNavigate();

  // 直接从本地存储中读取用户信息
  const getUserFromLocalStorage = (): string | null => {
    try {
      const storedUser = localStorage.getItem('user');
      if (storedUser) {
        const parsedUser = JSON.parse(storedUser);
        return parsedUser.email;
      }
    } catch (error) {
      console.error('Error parsing user from localStorage:', error);
      localStorage.removeItem('user');
    }
    return null;
  };

  // 从本地存储中读取用户信息
  useEffect(() => {
    // 从本地存储中读取用户信息
    const storedUser = localStorage.getItem('user');
    if (storedUser) {
      try {
        const parsedUser = JSON.parse(storedUser);
        setUser(parsedUser.email);
      } catch (error) {
        console.error('Error parsing user from localStorage:', error);
        localStorage.removeItem('user');
      }
    } else {
      setUser(null);
    }
    // 设置默认 URL 以便测试
    setUrl('https://example.com');
  }, []);

  // 监听本地存储变化
  useEffect(() => {
    const handleStorageChange = () => {
      const storedUser = localStorage.getItem('user');
      if (storedUser) {
        try {
          const parsedUser = JSON.parse(storedUser);
          setUser(parsedUser.email);
        } catch (error) {
          console.error('Error parsing user from localStorage:', error);
          localStorage.removeItem('user');
        }
      } else {
        setUser(null);
      }
    };

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, []);

  const validateUrl = (input: string): boolean => {
    try {
      new URL(input);
      return true;
    } catch {
      return false;
    }
  };

  const [progress, setProgress] = useState(0);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    console.log('handleSubmit called');
    console.log('checkType:', checkType);
    console.log('user:', user);
    console.log('url:', url);
    console.log('keyword:', keyword);

    if (checkType === 'website' && !url.trim()) {
      setError(t('home.error.empty'));
      return;
    }

    if (checkType === 'website') {
      console.log('url:', url);
      console.log('validateUrl(url):', validateUrl(url));
    }

    if (checkType === 'website' && !validateUrl(url)) {
      setError(t('home.error.invalid'));
      return;
    }

    if (checkType === 'keyword' && !keyword.trim()) {
      setError('请输入关键词');
      return;
    }

    // 检查用户是否登录
    let isLoggedIn = false;
    try {
      const storedUser = localStorage.getItem('user');
      if (storedUser) {
        const parsedUser = JSON.parse(storedUser);
        if (parsedUser.email) {
          isLoggedIn = true;
        }
      }
    } catch (error) {
      console.error('Error parsing user from localStorage:', error);
      localStorage.removeItem('user');
    }

    if (!isLoggedIn && checkType !== 'website') {
      console.log('User not logged in, can only use website check');
      alert('用户未登录，只能使用网站检测功能');
      setCheckType('website');
      return;
    }

    setIsLoading(true);
    setError('');
    setProgress(0);

    // 模拟GEO检查结果
    const mockResult = {
      url: checkType === 'website' ? url : keyword,
      score: 58,
      grade: 'D',
      checks: [
        {
          category: 'HTTPS',
          status: 'PASS',
          message: 'Site uses HTTPS',
          fix: null
        },
        {
          category: 'robots.txt',
          status: 'PASS',
          message: 'robots.txt found (171 bytes)',
          fix: null
        },
        {
          category: 'Sitemap',
          status: 'FAIL',
          message: 'No sitemap.xml found',
          fix: 'Create a sitemap.xml at your site root'
        },
        {
          category: 'URL Normalization',
          status: 'PASS',
          message: 'URL paths are consistent',
          fix: null
        },
        {
          category: 'llms.txt',
          status: 'FAIL',
          message: 'llms.txt not found',
          fix: '  - [API Docs](https://yoursite.com/api)'
        },
        {
          category: 'AI Crawl Readiness',
          status: 'PASS',
          message: 'Content is rendered server-side',
          fix: null
        },
        {
          category: 'AI Optimization',
          status: 'WARN',
          message: 'No llms.txt found',
          fix: 'Create an llms.txt file at your site root'
        },
        {
          category: 'AI Answer Formats',
          status: 'PASS',
          message: 'Definition sentences detected',
          fix: null
        },
        {
          category: 'Content Accessibility',
          status: 'PASS',
          message: 'Homepage has 1000 words in initial HTML',
          fix: null
        },
        {
          category: 'Content Quality',
          status: 'PASS',
          message: 'Readability: Flesch-Kincaid grade 8.5 (accessible)',
          fix: null
        },
        {
          category: 'Meta Tags',
          status: 'PASS',
          message: '<title> found: "Example Site"',
          fix: null
        },
        {
          category: 'Structured Data',
          status: 'FAIL',
          message: 'No JSON-LD structured data found',
          fix: 'Add JSON-LD structured data to your <head>'
        },
        {
          category: 'Technical Crawlability',
          status: 'PASS',
          message: 'Canonical URL set: https://example.com',
          fix: null
        },
        {
          category: 'Mobile & Weight',
          status: 'PASS',
          message: 'HTML page weight: 50 KB (lightweight)',
          fix: null
        },
        {
          category: '.well-known Discovery',
          status: 'INFO',
          message: 'No .well-known AI discovery files found',
          fix: 'Consider adding .well-known/security.txt'
        },
        {
          category: 'Authority & Trust',
          status: 'PASS',
          message: 'Strong security headers (3/4)',
          fix: null
        },
        {
          category: 'Social Signals',
          status: 'WARN',
          message: 'No Twitter/X card meta tags found',
          fix: 'Add Twitter card tags to your <head>'
        },
        {
          category: 'Cross-Platform',
          status: 'PASS',
          message: 'Strong cross-platform presence: 7/10 platforms',
          fix: null
        }
      ],
      summary: {
        pass_count: 44,
        warn_count: 20,
        fail_count: 6,
        info_count: 29,
        total_checks: 99
      }
    };
    // 输出 mockResult 变量的内容
    console.log('mockResult:', mockResult);
    setTimeout(() => {
      navigate('/result', { state: { result: mockResult } });
      setIsLoading(false);
      setProgress(0);
    }, 2000);
  };

  return (
    <div className="min-h-screen grid-background">
      {/* 背景发光效果 */}
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      {/* 主内容 */}
      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          <section className="hero">

            {/* 标题 */}
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight animate-slide-up text-center">
              <span className="gradient-text">{t('home.title')}</span>
            </h1>

            {/* 描述 */}
            <p className="text-lg sm:text-xl text-secondary max-w-3xl mx-auto leading-relaxed animate-slide-up text-center" style={{ animationDelay: '0.1s' }}>
              {t('home.description')}
            </p>

            {/* 表单 */}
            <div className="transition-all duration-300 animate-scale-in max-w-2xl mx-auto mt-10">
              {/* 检测类型选择 */}
              <div className="flex flex-wrap gap-2 mb-4">
                <button
                  onClick={() => setCheckType('website')}
                  className={`px-4 py-2 rounded-lg font-medium transition-all duration-300 ${checkType === 'website' ? 'bg-accent-primary text-white shadow-glow' : 'bg-card border border-border text-primary hover:bg-tertiary'}`}
                >
                  网站检测
                </button>
                <button
                  onClick={() => setCheckType('keyword')}
                  disabled={!(() => {
                    try {
                      const storedUser = localStorage.getItem('user');
                      if (storedUser) {
                        const parsedUser = JSON.parse(storedUser);
                        return !!parsedUser.email;
                      }
                    } catch (error) {
                      console.error('Error parsing user from localStorage:', error);
                      localStorage.removeItem('user');
                    }
                    return false;
                  })()}
                  className={`px-4 py-2 rounded-lg font-medium transition-all duration-300 ${checkType === 'keyword' ? 'bg-accent-primary text-white shadow-glow' : 'bg-card border border-border text-primary hover:bg-tertiary'} ${!(() => {
                    try {
                      const storedUser = localStorage.getItem('user');
                      if (storedUser) {
                        const parsedUser = JSON.parse(storedUser);
                        return !!parsedUser.email;
                      }
                    } catch (error) {
                      console.error('Error parsing user from localStorage:', error);
                      localStorage.removeItem('user');
                    }
                    return false;
                  })() ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  关键词检测
                </button>
                <button
                  onClick={() => setCheckType('competitor')}
                  disabled={!(() => {
                    try {
                      const storedUser = localStorage.getItem('user');
                      if (storedUser) {
                        const parsedUser = JSON.parse(storedUser);
                        return !!parsedUser.email;
                      }
                    } catch (error) {
                      console.error('Error parsing user from localStorage:', error);
                      localStorage.removeItem('user');
                    }
                    return false;
                  })()}
                  className={`px-4 py-2 rounded-lg font-medium transition-all duration-300 ${checkType === 'competitor' ? 'bg-accent-primary text-white shadow-glow' : 'bg-card border border-border text-primary hover:bg-tertiary'} ${!(() => {
                    try {
                      const storedUser = localStorage.getItem('user');
                      if (storedUser) {
                        const parsedUser = JSON.parse(storedUser);
                        return !!parsedUser.email;
                      }
                    } catch (error) {
                      console.error('Error parsing user from localStorage:', error);
                      localStorage.removeItem('user');
                    }
                    return false;
                  })() ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  竞争对手分析
                </button>
                <button
                  onClick={() => setCheckType('audit')}
                  disabled={!(() => {
                    try {
                      const storedUser = localStorage.getItem('user');
                      if (storedUser) {
                        const parsedUser = JSON.parse(storedUser);
                        return !!parsedUser.email;
                      }
                    } catch (error) {
                      console.error('Error parsing user from localStorage:', error);
                      localStorage.removeItem('user');
                    }
                    return false;
                  })()}
                  className={`px-4 py-2 rounded-lg font-medium transition-all duration-300 ${checkType === 'audit' ? 'bg-accent-primary text-white shadow-glow' : 'bg-card border border-border text-primary hover:bg-tertiary'} ${!(() => {
                    try {
                      const storedUser = localStorage.getItem('user');
                      if (storedUser) {
                        const parsedUser = JSON.parse(storedUser);
                        return !!parsedUser.email;
                      }
                    } catch (error) {
                      console.error('Error parsing user from localStorage:', error);
                      localStorage.removeItem('user');
                    }
                    return false;
                  })() ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  全站审计
                </button>
              </div>

              <form id="geo-form" onSubmit={handleSubmit}>
                {/* 输入框 */}
                <div className="flex items-center rounded-lg p-1">
                  {checkType === 'website' && (
                    <input
                      type="text"
                      id="url"
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      placeholder={t('home.placeholder')}
                      className="flex-1 py-3 px-3 text-base bg-transparent focus:outline-none"
                      disabled={isLoading}
                    />
                  )}
                  {checkType === 'keyword' && (
                    <input
                      type="text"
                      id="keyword"
                      value={keyword}
                      onChange={(e) => setKeyword(e.target.value)}
                      placeholder="请输入关键词"
                      className="flex-1 py-3 px-3 text-base bg-transparent focus:outline-none"
                      disabled={isLoading}
                    />
                  )}
                  {checkType === 'competitor' && (
                    <input
                      type="text"
                      id="competitor"
                      placeholder="请输入竞争对手网站"
                      className="flex-1 py-3 px-3 text-base bg-transparent focus:outline-none"
                      disabled={isLoading}
                    />
                  )}
                  {checkType === 'audit' && (
                    <input
                      type="text"
                      id="audit"
                      placeholder="请输入网站地址"
                      className="flex-1 py-3 px-3 text-base bg-transparent focus:outline-none"
                      disabled={isLoading}
                    />
                  )}
                  <button
                    type="submit"
                    disabled={isLoading}
                    className="bg-black text-white px-8 py-2 font-medium hover:bg-gray-800 transition-colors duration-300 flex items-center justify-center rounded-full ml-2"
                  >
                    {isLoading ? (
                      <div className="flex flex-col items-center">
                        <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth={4} />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                        </svg>
                        <span className="text-xs mt-1">{progress}%</span>
                      </div>
                    ) : (
                      t('home.button')
                    )}
                  </button>
                </div>

                {error && (
                  <div className="mt-4 bg-red-900 border border-red-800 rounded-xl p-5 flex items-center gap-3 animate-fade-in">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-red-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-.633-1.964-.633-2.732 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                    <span className="text-sm text-red-300 font-medium">{error}</span>
                  </div>
                )}
              </form>

              <div className="mt-8 text-center">
                <p className="text-sm text-muted font-medium">{t('home.poweredBy')}</p>
                <p className="mt-2">
                  <a href="/contact" className="text-sm text-accent-primary font-medium hover:text-primary transition-colors duration-300">
                    {t('home.contactLink')}
                  </a>
                </p>
              </div>
            </div>

            {/* 英雄按钮 */}
            <div className="hero-buttons flex justify-center mt-12">
              <a href="/geo-knowledge" className="btn-secondary">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.746 0 3.332.477 4.5 1.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
                {t('home.buttons.geoKnowledge')}
              </a>
              <a href="/products-services" className="btn-secondary">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z" />
                </svg>
                {t('home.buttons.services')}
              </a>
            </div>

          </section>
        </div>
      </main>
    </div>
  );
}
