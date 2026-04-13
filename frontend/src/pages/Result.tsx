import { useMemo, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../components/LanguageSwitcher';
import { PaymentModal } from '../components/PaymentModal';
import { useMembership } from '../hooks/useMembership';
import type { GeoTestResult, CheckResult } from '../types/geo';

const FREE_CHECKS_PER_TAB = 2;
const FREE_TOP_ISSUES = 2;

const categoryGroups: Record<string, string[]> = {
  websiteBasic: ['HTTPS', 'robots.txt', 'Sitemap', 'URL Normalization'],
  aiOptimization: ['llms.txt', 'AI Crawl Readiness', 'AI Optimization', 'AI Answer Formats'],
  contentQuality: ['Content Accessibility', 'Content Quality', 'Meta Tags', 'Structured Data'],
  technicalPerformance: ['Technical Crawlability', 'Mobile & Weight', '.well-known Discovery', '.well-known'],
  externalFactors: ['Authority & Trust', 'Social Signals', 'Cross-Platform', 'Platform Registration'],
};

const STATUS_ORDER: Record<string, number> = { FAIL: 0, WARN: 1, INFO: 2, PASS: 3 };

const statusTheme = (status: string) => {
  switch (status) {
    case 'PASS':
      return {
        dot: 'bg-emerald-400 shadow-[0_0_8px_rgba(0,255,157,0.7)]',
        text: 'text-emerald-400',
        badge: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
        bar: 'from-emerald-400 to-emerald-500',
      };
    case 'WARN':
      return {
        dot: 'bg-amber-400 shadow-[0_0_8px_rgba(255,184,0,0.7)]',
        text: 'text-amber-400',
        badge: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
        bar: 'from-amber-400 to-amber-500',
      };
    case 'FAIL':
      return {
        dot: 'bg-rose-500 shadow-[0_0_8px_rgba(255,0,110,0.8)]',
        text: 'text-rose-400',
        badge: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
        bar: 'from-rose-500 to-pink-500',
      };
    default:
      return {
        dot: 'bg-cyan-400 shadow-[0_0_8px_rgba(0,240,255,0.7)]',
        text: 'text-cyan-400',
        badge: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
        bar: 'from-cyan-400 to-cyan-500',
      };
  }
};

function ScoreRing({ score }: { score: number }) {
  const radius = 70;
  const stroke = 10;
  const normalized = Math.max(0, Math.min(100, score));
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (normalized / 100) * circumference;
  return (
    <div className="relative w-[180px] h-[180px] flex items-center justify-center shrink-0">
      <svg width="180" height="180" className="-rotate-90">
        <defs>
          <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#00f0ff" />
            <stop offset="50%" stopColor="#7b61ff" />
            <stop offset="100%" stopColor="#ff006e" />
          </linearGradient>
        </defs>
        <circle
          cx="90"
          cy="90"
          r={radius}
          fill="none"
          stroke="rgba(255,255,255,0.06)"
          strokeWidth={stroke}
        />
        <circle
          cx="90"
          cy="90"
          r={radius}
          fill="none"
          stroke="url(#ringGrad)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ filter: 'drop-shadow(0 0 8px rgba(0, 240, 255, 0.5))', transition: 'stroke-dashoffset 1s ease-out' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-4xl font-bold gradient-text leading-none">{normalized}</span>
        <span className="text-[11px] text-secondary uppercase tracking-[0.15em] mt-1">/ 100</span>
      </div>
    </div>
  );
}

export function Result() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const result = location.state?.result as GeoTestResult;
  const { token, isLoggedIn, isUnlocked, refresh } = useMembership();
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const [activeTab, setActiveTab] = useState<string>('websiteBasic');

  const handleUnlockClick = () => {
    if (!isLoggedIn) {
      navigate('/login');
      return;
    }
    setShowPaymentModal(true);
  };

  const handlePaymentSuccess = () => {
    setShowPaymentModal(false);
    refresh();
  };

  const checksByCategory = useMemo(() => {
    if (!result?.checks) return {} as Record<string, CheckResult[]>;
    const map: Record<string, CheckResult[]> = {};
    for (const check of result.checks) {
      (map[check.category] ||= []).push(check);
    }
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9));
    }
    return map;
  }, [result]);

  const groupKeys = useMemo(() => Object.keys(categoryGroups), []);

  const knownCategories = useMemo(() => new Set(groupKeys.flatMap((g) => categoryGroups[g])), [groupKeys]);

  const otherCategories = useMemo(
    () => Object.keys(checksByCategory).filter((c) => !knownCategories.has(c)),
    [checksByCategory, knownCategories],
  );

  const allTabs = useMemo(() => {
    const tabs = [...groupKeys];
    if (otherCategories.length > 0) tabs.push('other');
    return tabs;
  }, [groupKeys, otherCategories]);

  const categoriesForTab = (tab: string): string[] =>
    tab === 'other' ? otherCategories : categoryGroups[tab] || [];

  const groupStats = useMemo(() => {
    return allTabs.map((tab) => {
      const cats = categoriesForTab(tab);
      const checks = cats.flatMap((c) => checksByCategory[c] || []);
      const total = checks.length;
      const passed = checks.filter((c) => c.status === 'PASS').length;
      const failed = checks.filter((c) => c.status === 'FAIL').length;
      const warned = checks.filter((c) => c.status === 'WARN').length;
      const passRate = total === 0 ? 0 : Math.round((passed / total) * 100);
      return { tab, total, passed, failed, warned, passRate };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allTabs, checksByCategory]);

  const allTopIssues = useMemo(() => {
    if (!result?.checks) return [];
    return [...result.checks]
      .filter((c) => c.status === 'FAIL' || c.status === 'WARN')
      .sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9));
  }, [result]);

  const topIssues = isUnlocked ? allTopIssues : allTopIssues.slice(0, FREE_TOP_ISSUES);
  const lockedTopIssuesCount = isUnlocked ? 0 : Math.max(0, allTopIssues.length - FREE_TOP_ISSUES);

  if (!result) {
    return (
      <div className="min-h-screen grid-background flex items-center justify-center">
        <div className="bg-card border border-border rounded-2xl p-8 max-w-md w-full text-center">
          <div className="w-16 h-16 mx-auto rounded-full bg-rose-500/10 flex items-center justify-center mb-6">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-rose-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-.633-1.964-.633-2.732 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold mb-4">{t('common.error')}</h2>
          <p className="text-secondary mb-6">{t('result.error.noData')}</p>
          <button
            onClick={() => navigate('/')}
            className="w-full gradient-bg text-white rounded-xl py-3.5 font-semibold hover:opacity-90 transition-all duration-300 flex items-center justify-center gap-2 shadow-glow"
          >
            {t('result.buttons.checkAnother')}
          </button>
        </div>
      </div>
    );
  }

  const summary = result.summary || { pass_count: 0, warn_count: 0, fail_count: 0, info_count: 0, total_checks: 0 };
  const score = result.score || 0;
  const grade = result.grade || 'F';

  const handleCopyLink = () => {
    const url = `${window.location.origin}/result?url=${encodeURIComponent(result.url || '')}`;
    navigator.clipboard.writeText(url).then(() => alert(t('result.shareExport.copied')));
  };

  const handleExportCSV = () => {
    const csvContent = `data:text/csv;charset=utf-8,${encodeURIComponent(
      [
        ['Category', 'Status', 'Message', 'Fix'],
        ...result.checks.map((c) => [c.category, c.status, c.message, c.fix || '']),
      ]
        .map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(','))
        .join('\n'),
    )}`;
    const link = document.createElement('a');
    link.setAttribute('href', csvContent);
    link.setAttribute('download', `geo-result-${result.url.replace(/[^a-zA-Z0-9]/g, '-')}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleExportPDF = () => window.print();

  const tabLabel = (tab: string) => t(`result.categories.${tab}`);

  const activeCategories = categoriesForTab(activeTab);

  return (
    <div className="min-h-screen grid-background">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-5 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          {/* Top bar: title + URL + lang switch */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
            <div className="min-w-0">
              <h1 className="text-2xl sm:text-3xl font-bold gradient-text mb-1.5">
                {t('result.title')}
              </h1>
              <div className="flex items-center gap-2 text-xs sm:text-sm text-secondary min-w-0">
                <span className="uppercase tracking-[0.15em] text-[#d5d5dc] text-[10px]">{t('result.resultsFor')}</span>
                <a
                  href={result.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-accent-primary hover:text-primary transition-colors font-medium truncate"
                >
                  {result.url}
                </a>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={handleCopyLink}
                title={t('result.shareExport.copyLink')}
                className="w-9 h-9 rounded-lg bg-card border border-border text-secondary hover:text-accent-primary hover:border-accent-primary/40 transition-colors flex items-center justify-center"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              </button>
              <button
                onClick={handleExportCSV}
                title={t('result.shareExport.exportCSV')}
                className="w-9 h-9 rounded-lg bg-card border border-border text-secondary hover:text-accent-primary hover:border-accent-primary/40 transition-colors flex items-center justify-center"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </button>
              <button
                onClick={handleExportPDF}
                title={t('result.shareExport.exportPDF')}
                className="w-9 h-9 rounded-lg bg-card border border-border text-secondary hover:text-accent-primary hover:border-accent-primary/40 transition-colors flex items-center justify-center"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 17h2a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-10a2 2 0 00-2-2H9a2 2 0 00-2 2v10a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
              </button>
              {/* <LanguageSwitcher /> */}
            </div>
          </div>

          {/* Score + summary + group breakdown — single dense card */}
          <div className="bg-card border border-border rounded-2xl p-5 sm:p-6 mb-6 relative overflow-hidden">
            <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-cyan-400/40 to-transparent"></div>
            <div className="grid grid-cols-1 lg:grid-cols-[auto_1fr] gap-6 lg:gap-8 items-center">
              {/* Ring */}
              <div className="flex flex-col items-center gap-2">
                <ScoreRing score={score} />
                <div className="flex items-center gap-2">
                  <span className="text-[10px] uppercase tracking-[0.2em] text-[#d5d5dc]">{t('result.scoreCard.grade')}</span>
                  <span className="text-sm font-bold gradient-text px-2.5 py-0.5 rounded border border-cyan-500/30 bg-cyan-500/5">
                    {grade}
                  </span>
                </div>
              </div>

              {/* Stats + group bars */}
              <div className="flex flex-col gap-5 min-w-0">
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
                  <StatPill color="emerald" value={summary.pass_count} label={t('result.summary.passed')} />
                  <StatPill color="amber" value={summary.warn_count} label={t('result.summary.warnings')} />
                  <StatPill color="rose" value={summary.fail_count} label={t('result.summary.failed')} />
                  <StatPill color="cyan" value={summary.info_count} label={t('result.summary.info')} />
                  <StatPill color="muted" value={summary.total_checks} label={t('result.summary.totalChecks')} />
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2.5">
                    <span className="w-1 h-3.5 gradient-bg rounded-full"></span>
                    <h3 className="text-[11px] uppercase tracking-[0.18em] text-secondary font-semibold">
                      {t('result.groupProgress.title')}
                    </h3>
                  </div>
                  <div className="space-y-1.5">
                    {groupStats.filter((g) => g.total > 0).map((g) => (
                      <button
                        key={g.tab}
                        onClick={() => setActiveTab(g.tab)}
                        className={`w-full grid grid-cols-[110px_1fr_auto] sm:grid-cols-[140px_1fr_auto] items-center gap-3 py-1.5 px-2 -mx-2 rounded-md transition-colors ${activeTab === g.tab ? 'bg-cyan-500/5' : 'hover:bg-tertiary/30'
                          }`}
                      >
                        <span className={`text-xs font-medium truncate text-left ${activeTab === g.tab ? 'text-accent-primary' : 'text-secondary'}`}>
                          {tabLabel(g.tab)}
                        </span>
                        <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-cyan-400 via-purple-500 to-pink-500 transition-all duration-700"
                            style={{ width: `${g.passRate}%` }}
                          ></div>
                        </div>
                        <span className="text-[10px] font-mono text-[#d5d5dc] tabular-nums w-12 text-right">
                          {g.passed}/{g.total}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Top critical issues */}
          <div className="bg-card border border-border rounded-2xl p-5 sm:p-6 mb-6">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="w-1 h-4 bg-gradient-to-b from-rose-500 to-pink-500 rounded-full"></span>
                <h3 className="text-sm font-semibold text-primary uppercase tracking-wider">
                  {t('result.topIssues.title')}
                </h3>
              </div>
              {allTopIssues.length > 0 && (
                <span className="text-[10px] font-mono text-[#d5d5dc]">
                  {topIssues.length}/{allTopIssues.length}
                </span>
              )}
            </div>
            {topIssues.length === 0 ? (
              <p className="text-xs text-secondary py-2">{t('result.topIssues.empty')}</p>
            ) : (
              <div className="space-y-2">
                {topIssues.map((issue, i) => {
                  const theme = statusTheme(issue.status);
                  return (
                    <div
                      key={i}
                      className="flex items-start gap-3 p-3 rounded-lg bg-tertiary/30 border border-border hover:border-accent-primary/30 transition-colors"
                    >
                      <span className={`w-2 h-2 rounded-full shrink-0 mt-1.5 ${theme.dot}`}></span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[10px] font-mono text-[#d5d5dc] uppercase">{issue.category}</span>
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${theme.badge}`}>
                            {issue.status}
                          </span>
                        </div>
                        <p className="text-xs text-primary leading-relaxed">{issue.message}</p>
                      </div>
                    </div>
                  );
                })}
                {lockedTopIssuesCount > 0 && (
                  <button
                    onClick={handleUnlockClick}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-gradient-to-r from-cyan-500/5 via-purple-500/5 to-pink-500/5 hover:from-cyan-500/10 hover:via-purple-500/10 hover:to-pink-500/10 border border-cyan-500/15 transition-colors group"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                    </svg>
                    <span className="text-xs font-semibold text-accent-primary group-hover:text-primary transition-colors">
                      {t('result.paywall.lockedCount', { count: lockedTopIssuesCount })}
                    </span>
                    <span className="text-[10px] text-[#d5d5dc]">·</span>
                    <span className="text-[10px] text-[#d5d5dc] group-hover:text-accent-primary transition-colors">
                      {t('result.paywall.viewAll')} →
                    </span>
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Tab navigation — underline style */}
          <div className="border-b border-border mb-5">
            <div role="tablist" className="flex gap-1 sm:gap-2 overflow-x-auto -mb-px">
              {groupStats.filter((g) => g.total > 0).map((g) => {
                const isActive = activeTab === g.tab;
                return (
                  <button
                    key={g.tab}
                    role="tab"
                    aria-selected={isActive}
                    onClick={() => setActiveTab(g.tab)}
                    className={`px-4 py-3 text-xs sm:text-sm font-medium whitespace-nowrap border-b-2 transition-all flex items-center gap-2 ${isActive
                      ? 'border-cyan-400 text-accent-primary'
                      : 'border-transparent text-secondary hover:text-primary'
                      }`}
                  >
                    <span>{tabLabel(g.tab)}</span>
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${isActive ? 'bg-cyan-500/10 text-accent-primary' : 'bg-white/5 text-[#d5d5dc]'
                        }`}
                    >
                      {g.total}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Detailed table */}
          <div className="space-y-5 mb-8">
            {(() => {
              const renderRow = (check: CheckResult, rowKey: string) => {
                const theme = statusTheme(check.status);
                return (
                  <div
                    key={rowKey}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-tertiary/20 transition-colors"
                  >
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${theme.dot}`}></span>
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border shrink-0 w-12 text-center ${theme.badge}`}>
                      {check.status}
                    </span>
                    <p className="flex-1 min-w-0 text-xs text-primary truncate" title={check.message}>
                      {check.message}
                    </p>
                  </div>
                );
              };

              const renderCategoryBlock = (
                categoryKey: string,
                rows: ReactNode,
                visibleCount: number,
                totalCount: number,
              ) => {
                const lockedInCat = totalCount - visibleCount;
                return (
                  <div key={categoryKey}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="w-1 h-4 gradient-bg rounded-full"></span>
                        <h3 className="text-sm font-semibold text-primary">{categoryKey}</h3>
                      </div>
                      <div className="flex items-center gap-2">
                        {lockedInCat > 0 && (
                          <span className="flex items-center gap-1 text-[10px] font-mono text-accent-primary">
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                            </svg>
                            +{lockedInCat}
                          </span>
                        )}
                        <span className="text-[10px] font-mono text-[#d5d5dc]">
                          {visibleCount}/{totalCount}
                        </span>
                      </div>
                    </div>
                    <div className="bg-card border border-border rounded-xl overflow-hidden">
                      <div className="divide-y divide-border">{rows}</div>
                    </div>
                  </div>
                );
              };

              const lockedCta = (lockedCount: number) =>
                lockedCount > 0 ? (
                  <button
                    onClick={handleUnlockClick}
                    className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl border border-cyan-500/20 bg-gradient-to-r from-cyan-500/5 via-purple-500/5 to-pink-500/5 hover:from-cyan-500/10 hover:via-purple-500/10 hover:to-pink-500/10 transition-colors group"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                    </svg>
                    <span className="text-xs font-semibold text-accent-primary group-hover:text-primary transition-colors">
                      {t('result.paywall.lockedCount', { count: lockedCount })}
                    </span>
                    <span className="text-[10px] text-[#d5d5dc]">·</span>
                    <span className="text-[10px] text-[#d5d5dc] group-hover:text-accent-primary transition-colors">
                      {t('result.paywall.viewAll')} →
                    </span>
                  </button>
                ) : null;

              const presentCategories = activeCategories.filter(
                (c) => (checksByCategory[c]?.length ?? 0) > 0,
              );

              if (presentCategories.length === 0) {
                return (
                  <div className="bg-card border border-border rounded-xl p-8 text-center text-secondary text-xs">
                    {t('result.error.noData')}
                  </div>
                );
              }

              // UNLOCKED: full content, grouped by big category, no row expand
              if (isUnlocked) {
                return presentCategories.map((categoryKey) => {
                  const checks = checksByCategory[categoryKey];
                  return renderCategoryBlock(
                    categoryKey,
                    checks.map((check, idx) => renderRow(check, `${categoryKey}-${idx}`)),
                    checks.length,
                    checks.length,
                  );
                });
              }

              // FREE: 2 items total across the whole tab, but still grouped under big-category headings.
              // Distribute the budget by walking categories in order; categories that get 0 items are skipped
              // entirely (their counts roll up into the bottom unlock CTA so we don't clutter every block).
              let remaining = FREE_CHECKS_PER_TAB;
              const totalInTab = presentCategories.reduce(
                (sum, c) => sum + (checksByCategory[c]?.length ?? 0),
                0,
              );
              let totalShown = 0;

              const blocks: ReactNode[] = [];
              for (const categoryKey of presentCategories) {
                const checks = checksByCategory[categoryKey];
                const take = Math.min(remaining, checks.length);
                if (take === 0) continue;
                remaining -= take;
                totalShown += take;
                const rows = checks
                  .slice(0, take)
                  .map((check, idx) => renderRow(check, `${categoryKey}-${idx}`));
                blocks.push(renderCategoryBlock(categoryKey, rows, take, checks.length));
              }

              const lockedTotal = Math.max(0, totalInTab - totalShown);

              return (
                <>
                  {blocks}
                  {lockedCta(lockedTotal)}
                </>
              );
            })()}
          </div>

          {/* Footer actions */}
          <div className="flex flex-col sm:flex-row gap-3">
            <button
              onClick={() => navigate('/')}
              className="flex-1 bg-card border border-border rounded-xl py-3 px-5 text-sm font-semibold text-primary hover:bg-tertiary hover:border-accent-primary/40 transition-all flex items-center justify-center gap-2"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
              </svg>
              {t('result.buttons.checkAnother')}
            </button>
            {!isUnlocked && (
              <button
                onClick={handleUnlockClick}
                className="flex-1 gradient-bg text-white rounded-xl py-3 px-5 text-sm font-semibold hover:opacity-90 transition-all shadow-glow flex items-center justify-center gap-2"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                {t('result.paywall.viewAll')}
              </button>
            )}
            <a
              href="/contact"
              className="flex-1 bg-card border border-border rounded-xl py-3 px-5 text-sm font-semibold text-primary hover:bg-tertiary hover:border-accent-primary/40 transition-all flex items-center justify-center gap-2"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8h2a2 2 0 012 2v6a2 2 0 01-2 2h-2v4l-4-4H9a1.994 1.994 0 01-1.414-.586m0 0L11 14h4a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2v4l.586-.586z" />
              </svg>
              {t('result.buttons.getHelp')}
            </a>
          </div>
        </div>
      </main>

      {showPaymentModal && token && (
        <PaymentModal
          token={token}
          onClose={() => setShowPaymentModal(false)}
          onSuccess={handlePaymentSuccess}
        />
      )}
    </div>
  );
}

function StatPill({
  color,
  value,
  label,
}: {
  color: 'emerald' | 'amber' | 'rose' | 'cyan' | 'muted';
  value: number;
  label: string;
}) {
  const palette = {
    emerald: { text: 'text-emerald-400', dot: 'bg-emerald-400', border: 'border-emerald-500/20', bg: 'bg-emerald-500/5' },
    amber: { text: 'text-amber-400', dot: 'bg-amber-400', border: 'border-amber-500/20', bg: 'bg-amber-500/5' },
    rose: { text: 'text-rose-400', dot: 'bg-rose-500', border: 'border-rose-500/20', bg: 'bg-rose-500/5' },
    cyan: { text: 'text-cyan-400', dot: 'bg-cyan-400', border: 'border-cyan-500/20', bg: 'bg-cyan-500/5' },
    muted: { text: 'text-primary', dot: 'bg-white/40', border: 'border-border', bg: 'bg-white/5' },
  }[color];
  return (
    <div className={`flex items-center gap-2.5 px-3 py-2 rounded-lg border ${palette.border} ${palette.bg}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${palette.dot}`}></span>
      <div className="flex flex-col leading-tight min-w-0">
        <span className={`text-base font-bold tabular-nums ${palette.text}`}>{value}</span>
        <span className="text-[9px] uppercase tracking-wider text-[#d5d5dc] truncate">{label}</span>
      </div>
    </div>
  );
}
