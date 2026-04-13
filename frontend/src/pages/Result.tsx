import { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../components/LanguageSwitcher';
import type { GeoTestResult, CheckResult } from '../types/geo';

interface User {
  email: string;
}

// 定义分类映射
const categoryGroups = {
  '网站基础': ['HTTPS', 'robots.txt', 'Sitemap', 'URL Normalization'],
  'AI优化': ['llms.txt', 'AI Crawl Readiness', 'AI Optimization', 'AI Answer Formats'],
  '内容质量': ['Content Accessibility', 'Content Quality', 'Meta Tags', 'Structured Data'],
  '技术性能': ['Technical Crawlability', 'Mobile & Weight', '.well-known Discovery'],
  '外部因素': ['Authority & Trust', 'Social Signals', 'Cross-Platform']
};

export function Result() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const result = location.state?.result as GeoTestResult;
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({});
  const [user, setUser] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>('网站基础');



  const toggleCategory = (category: string) => {
    setExpandedCategories((prev: Record<string, boolean>) => ({
      ...prev,
      [category]: !prev[category]
    }));
  };

  if (!result) {
    return (
      <div className="min-h-screen grid-background flex items-center justify-center">
        <div className="bg-card border border-border rounded-2xl p-8 max-w-md w-full text-center">
          <div className="w-16 h-16 mx-auto rounded-full bg-red-900/10 flex items-center justify-center mb-6">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-.633-1.964-.633-2.732 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold mb-4">{t('common.error')}</h2>
          <p className="text-secondary mb-6">{t('result.error.noData')}</p>
          <button
            onClick={() => navigate('/')}
            className="w-full gradient-bg text-white rounded-xl py-3.5 font-semibold hover:opacity-90 transition-all duration-300 flex items-center justify-center gap-2 shadow-glow"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
            {t('result.buttons.checkAnother')}
          </button>
        </div>
      </div>
    );
  }

  // Group checks by category
  const checksByCategory = result?.checks ? result.checks.reduce((acc: Record<string, CheckResult[]>, check: CheckResult) => {
    if (!acc[check.category]) {
      acc[check.category] = [];
    }
    acc[check.category].push(check);
    return acc;
  }, {} as Record<string, CheckResult[]>) : {};
  
  // 输出 checksByCategory 变量的内容
  console.log('checksByCategory:', checksByCategory);
  console.log('categoryGroups:', categoryGroups);
  console.log('activeTab:', activeTab);

  // Get checks for current tab
  const getChecksForTab = (tab: string) => {
    const categories = categoryGroups[tab as keyof typeof categoryGroups] || [];
    let tabChecks: CheckResult[] = [];
    categories.forEach(category => {
      if (checksByCategory[category]) {
        tabChecks = [...tabChecks, ...checksByCategory[category]];
      }
    });
    return tabChecks;
  };

  return (
    <div className="min-h-screen grid-background">
      {/* 背景发光效果 */}
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-5xl mx-auto animate-fade-in">
          <div className="bg-card border border-border rounded-2xl p-8">
            <div style={{ position: 'absolute', top: '24px', right: '24px', zIndex: 20 }}>
              <LanguageSwitcher />
            </div>

            <div className="text-center mb-10 sm:mb-12">
                <div className="inline-block mb-5 sm:mb-6">
                  <div className="w-16 sm:w-20 h-16 sm:h-20 mx-auto rounded-2xl flex items-center justify-center gradient-bg shadow-glow">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-7 sm:h-9 w-7 sm:w-9 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                </div>
                <h1 className="text-2xl sm:text-3xl md:text-4xl font-bold mb-4 gradient-text">
                  {t('result.title')}
                </h1>
                <p className="text-secondary mb-4 text-sm sm:text-base">
                  {t('result.resultsFor')} <a href={result?.url} target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:text-primary transition-colors duration-300 font-medium break-all">{result?.url || 'Unknown URL'}</a>
                </p>
              </div>

              {/* Score Card */}
              <div className="gradient-bg rounded-2xl p-6 sm:p-8 mb-10 text-white shadow-glow">
                <div className="flex flex-col md:flex-row items-center justify-between">
                  <div className="text-center md:text-left mb-4 md:mb-0">
                    <h2 className="text-xl sm:text-2xl font-bold mb-2">{t('result.scoreCard.title')}</h2>
                    <p className="text-white/90 text-sm sm:text-base">{t('result.scoreCard.description')}</p>
                  </div>
                  <div className="flex flex-col items-center">
                    <span className="text-3xl sm:text-4xl md:text-5xl font-bold">{result?.score || 0}/100</span>
                    <span className="text-sm sm:text-lg font-semibold bg-white/25 backdrop-blur-sm px-4 sm:px-5 py-1 sm:py-1.5 rounded-full mt-3">
                      {result?.grade || 'F'}
                    </span>
                  </div>
                </div>
              </div>

            {/* Summary */}
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-4 mb-10">
              <div className="bg-green-900/10 border border-green-800/20 rounded-xl p-4 sm:p-5 transition-all duration-300 hover:translate-y-[-4px]">
                <div className="text-green-400 font-bold text-lg sm:text-xl mb-1">{result?.summary?.pass_count || 0}</div>
                <div className="text-xs sm:text-sm text-secondary font-medium">{t('result.summary.passed')}</div>
              </div>
              <div className="bg-yellow-900/10 border border-yellow-800/20 rounded-xl p-4 sm:p-5 transition-all duration-300 hover:translate-y-[-4px]">
                <div className="text-yellow-400 font-bold text-lg sm:text-xl mb-1">{result?.summary?.warn_count || 0}</div>
                <div className="text-xs sm:text-sm text-secondary font-medium">{t('result.summary.warnings')}</div>
              </div>
              <div className="bg-red-900/10 border border-red-800/20 rounded-xl p-4 sm:p-5 transition-all duration-300 hover:translate-y-[-4px]">
                <div className="text-red-400 font-bold text-lg sm:text-xl mb-1">{result?.summary?.fail_count || 0}</div>
                <div className="text-xs sm:text-sm text-secondary font-medium">{t('result.summary.failed')}</div>
              </div>
              <div className="bg-blue-900/10 border border-blue-800/20 rounded-xl p-4 sm:p-5 transition-all duration-300 hover:translate-y-[-4px]">
                <div className="text-blue-400 font-bold text-lg sm:text-xl mb-1">{result?.summary?.info_count || 0}</div>
                <div className="text-xs sm:text-sm text-secondary font-medium">{t('result.summary.info')}</div>
              </div>
              <div className="bg-card border border-border rounded-xl p-4 sm:p-5 transition-all duration-300 hover:translate-y-[-4px]">
                <div className="text-primary font-bold text-lg sm:text-xl mb-1">{result?.summary?.total_checks || 0}</div>
                <div className="text-xs sm:text-sm text-secondary font-medium">{t('result.summary.totalChecks')}</div>
              </div>
            </div>

            {/* Tab Navigation */}
            <div className="mb-8">
              <div className="flex flex-wrap gap-2">
                {Object.keys(categoryGroups).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className={`px-4 py-2.5 rounded-lg font-medium transition-all duration-300 ${activeTab === tab ? 'bg-accent-primary text-white shadow-glow' : 'bg-card border border-border text-primary hover:bg-tertiary'}`}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </div>

            {/* Detailed Results */}
            <div className="mb-10">
              <h2 className="text-xl sm:text-2xl font-bold mb-6 gradient-text">
                {t('result.detailedResults')}
              </h2>

              {Object.keys(categoryGroups).map((tab) => {
                if (tab !== activeTab) return null;
                
                const categories = categoryGroups[tab as keyof typeof categoryGroups] || [];
                
                return (
                  <div key={tab}>
                    {categories.map((category) => {
                      const checks = checksByCategory[category];
                      if (!checks) return null;
                      
                      const isExpanded = expandedCategories[category] || false;
                      return (
                        <div key={category} className="mb-6">
                          <div 
                            className="flex items-center justify-between cursor-pointer p-4 bg-card border border-border rounded-xl mb-2 hover:border-accent-primary transition-all duration-300"
                            onClick={() => toggleCategory(category)}
                          >
                            <h3 className="text-lg sm:text-xl font-semibold text-primary flex items-center">
                              <span className="w-1.5 h-5 sm:h-7 gradient-bg rounded-full mr-3"></span>
                              <span className="truncate">{category}</span>
                            </h3>
                            <div className="flex items-center gap-2">
                              <span className="text-xs sm:text-sm text-secondary">{checks.length} {checks.length === 1 ? '项' : '项'}</span>
                              <svg 
                                xmlns="http://www.w3.org/2000/svg" 
                                className={`h-4 sm:h-5 w-4 sm:w-5 text-primary transition-transform duration-300 ${isExpanded ? 'transform rotate-180' : ''}`} 
                                fill="none" 
                                viewBox="0 0 24 24" 
                                stroke="currentColor"
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                              </svg>
                            </div>
                          </div>
                          {isExpanded && (
                            <div className="space-y-4 pl-6 sm:pl-8 pr-4">
                              {((() => {
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
              })() ? checks : checks.slice(0, 2)).map((check: CheckResult, index: number) => (
                                <div key={index} className="bg-card border border-border rounded-xl p-4 sm:p-6 transition-all duration-300 hover:border-accent-primary hover:translate-x-1">
                                  <div className="flex flex-col sm:flex-row justify-between items-start sm:items-start gap-4">
                                    <div className="flex-1">
                                      <p className="text-primary mb-3 sm:mb-4 font-medium text-sm sm:text-base">{check.message}</p>
                                      {check.fix && (
                                        <div className="mt-3 sm:mt-4 p-3 sm:p-4 bg-accent-primary/10 border border-accent-primary/20 rounded-lg">
                                          <p className="text-xs sm:text-sm text-accent-primary">
                                            <span className="font-semibold">{t('result.fix')}</span> {check.fix}
                                          </p>
                                        </div>
                                      )}
                                    </div>
                                    <span className={`px-3 py-1 sm:px-3.5 sm:py-1.5 rounded-full text-xs font-semibold whitespace-nowrap ${check.status === 'PASS' ? 'bg-green-900/10 text-green-400 border border-green-800/20' : check.status === 'WARN' ? 'bg-yellow-900/10 text-yellow-400 border border-yellow-800/20' : check.status === 'FAIL' ? 'bg-red-900/10 text-red-400 border border-red-800/20' : 'bg-blue-900/10 text-blue-400 border border-blue-800/20'}`}>
                                      {check.status}
                                    </span>
                                  </div>
                                </div>
                              ))}
                              {!user && checks.length > 2 && (
                                <div className="bg-gradient-to-r from-accent-primary/10 to-accent-primary/5 border border-accent-primary/20 rounded-xl p-4 sm:p-6">
                                  <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
                                    <div className="flex items-center gap-3">
                                      <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                                      </svg>
                                      <div>
                                        <h4 className="text-lg font-semibold text-accent-primary mb-2">{t('result.loginToView')}</h4>
                                        <p className="text-secondary text-sm sm:text-base">{t('result.loginToViewDesc')}</p>
                                      </div>
                                    </div>
                                    <button
                                      onClick={() => navigate('/login')}
                                      className="gradient-bg text-white rounded-xl py-2.5 px-4 font-semibold hover:opacity-90 transition-all duration-300 shadow-glow"
                                    >
                                      {t('result.loginButton')}
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>

            {/* Action Buttons */}
            <div className="flex flex-col gap-4">
              <button
                onClick={() => navigate('/')}
                className="w-full bg-card border border-border rounded-xl py-3 font-semibold text-primary hover:bg-tertiary hover:border-accent-primary transition-all duration-300 flex items-center justify-center gap-2"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 sm:h-5 w-4 sm:w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
                </svg>
                {t('result.buttons.checkAnother')}
              </button>
              <a
                href="/contact"
                className="w-full gradient-bg text-white rounded-xl py-3 font-semibold hover:opacity-90 transition-all duration-300 flex items-center justify-center gap-2 shadow-glow"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 sm:h-5 w-4 sm:w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8h2a2 2 0 012 2v6a2 2 0 01-2 2h-2v4l-4-4H9a1.994 1.994 0 01-1.414-.586m0 0L11 14h4a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2v4l.586-.586z" />
                </svg>
                {t('result.buttons.getHelp')}
              </a>
            </div>

            {/* Share and Export */}
            <div className="mt-8">
              <h3 className="text-lg font-semibold mb-4 text-primary">{t('result.shareExport.title')}</h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <button
                  onClick={() => {
                    // 复制结果链接到剪贴板
                    const url = `${window.location.origin}/result?url=${encodeURIComponent(result?.url || '')}`;
                    navigator.clipboard.writeText(url).then(() => {
                      alert(t('result.shareExport.copied'));
                    });
                  }}
                  className="bg-card border border-border rounded-xl py-3 font-semibold text-primary hover:bg-tertiary hover:border-accent-primary transition-all duration-300 flex flex-col items-center justify-center gap-2"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                  </svg>
                  <span className="text-xs sm:text-sm">{t('result.shareExport.copyLink')}</span>
                </button>
                <button
                  onClick={() => {
                    // 导出为PDF（简化版，实际项目中需要使用PDF库）
                    window.print();
                  }}
                  className="bg-card border border-border rounded-xl py-3 font-semibold text-primary hover:bg-tertiary hover:border-accent-primary transition-all duration-300 flex flex-col items-center justify-center gap-2"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 17h2a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-10a2 2 0 00-2-2H9a2 2 0 00-2 2v10a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z" />
                  </svg>
                  <span className="text-xs sm:text-sm">{t('result.shareExport.exportPDF')}</span>
                </button>
                <button
                  onClick={() => {
                    // 导出为CSV
                    const csvContent = `data:text/csv;charset=utf-8,${encodeURIComponent(
                      [
                        ['Category', 'Status', 'Message', 'Fix'],
                        ...result.checks.map((check: CheckResult) => [check.category, check.status, check.message, check.fix || ''])
                      ].map(row => row.join(',')).join('\n')
                    )}`;
                    const link = document.createElement('a');
                    link.setAttribute('href', csvContent);
                    link.setAttribute('download', `geo-result-${result.url.replace(/[^a-zA-Z0-9]/g, '-')}.csv`);
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                  }}
                  className="bg-card border border-border rounded-xl py-3 font-semibold text-primary hover:bg-tertiary hover:border-accent-primary transition-all duration-300 flex flex-col items-center justify-center gap-2"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <span className="text-xs sm:text-sm">{t('result.shareExport.exportCSV')}</span>
                </button>
                <button
                  onClick={() => {
                    // 分享到社交媒体（示例：分享到微信）
                    alert(t('result.shareExport.shareSocial'));
                  }}
                  className="bg-card border border-border rounded-xl py-3 font-semibold text-primary hover:bg-tertiary hover:border-accent-primary transition-all duration-300 flex flex-col items-center justify-center gap-2"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
                  </svg>
                  <span className="text-xs sm:text-sm">{t('result.shareExport.share')}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}