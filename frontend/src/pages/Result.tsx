import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PaymentModal } from '../components/PaymentModal';
import { useMembership } from '../hooks/useMembership';
import { geoApi } from '../services/geoApi';
import { resolveCategoryVisual } from '../components/result/CategoryVisual';
import type { GeoTestResult, CheckResult } from '../types/geo';

// 23 categories split into 7 tabs (2 free + 5 paid) aligned with
// docs/会员功能免费与付费功能项目列表.md §检测大项分组. Free and paid
// groups do not cross over, so a whole paid tab can show 🔒 for non-members
// while free tabs are always visible.
// Labels MUST match the `--- X ---` section headers emitted by geo_checker.py —
// the backend parser sets `checks[].category` from those headers.
const categoryGroups: Record<string, string[]> = {
  // 🆓 基础协议与可抓取性
  infraProtocols: [
    'HTTPS',
    'robots.txt',
    'sitemap.xml',
  ],
  // 🆓 页面基础与移动体验
  pageBasics: [
    'Meta Tags',
    'Mobile-Friendliness & Page Weight',
  ],
  // 💎 AI 专属协议与抓取
  aiProtocols: [
    'llms.txt',
    '.well-known Discovery',
    'AI Crawl Readiness',
  ],
  // 💎 结构化与语义
  structuredSemantic: [
    'Structured Data',
    'Schema Breadcrumbs & Knowledge Panel',
    'URL Normalization',
  ],
  // 💎 内容质量与可读性
  contentQuality: [
    'Content Accessibility',
    'Content Quality for AI',
    'AI-Specific Optimization',
    'AI Answer Format Optimization',
  ],
  // 💎 技术健壮性与媒体
  techRobustness: [
    'Technical Crawlability',
    'Outbound Links & Media',
    'Multilingual Content Depth',
    'Multi-Page Sampling',
  ],
  // 💎 权威与外部信号
  authorityExternal: [
    'Search Engine & AI Platform Registration',
    'Authority & Trust Signals',
    'Social Signals',
    'Cross-Platform Content Distribution',
  ],
};

// Tabs in FREE_GROUPS render for everyone; all others are paid and show 🔒
// on the tab for non-members (i.e. when any of their categories are in the
// backend-provided `locked_categories` set).
const FREE_GROUPS = new Set(['infraProtocols', 'pageBasics']);

// Advanced modes surfaced in the rerun dropdown for logged-in users.
// Each entry maps to a geo_checker CLI sub-mode; route target is /advanced/{key}.
// `minTier` is compared to result.tier to decide if a 🔒 icon shows.
type AdvancedMode = 'compare' | 'crawlTest' | 'authority' | 'citation' | 'visibility' | 'entity';
const ADVANCED_MODES: { key: AdvancedMode; minTier: 'pro' | 'starter' }[] = [
  { key: 'compare', minTier: 'pro' },
  { key: 'crawlTest', minTier: 'pro' },
  { key: 'authority', minTier: 'pro' },
  { key: 'citation', minTier: 'pro' },
  { key: 'visibility', minTier: 'starter' },
  { key: 'entity', minTier: 'starter' },
];

const TIER_RANK: Record<string, number> = {
  free: 0,
  pro: 1,
  starter: 2,
  growth: 3,
  scale: 4,
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

export function Result() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const result = location.state?.result as GeoTestResult;
  const { token, isLoggedIn, refresh } = useMembership();
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const [activeTab, setActiveTab] = useState<string>('infraProtocols');

  // Rerun bar state — title-row URL input + optional advanced mode dropdown.
  const [rerunUrl, setRerunUrl] = useState<string>(result?.url || '');
  const [rerunMode, setRerunMode] = useState<'default' | AdvancedMode>('default');
  const [rerunLoading, setRerunLoading] = useState(false);
  const [rerunError, setRerunError] = useState<string>('');

  const effectiveTier = result?.tier || 'free';
  const effectiveRank = TIER_RANK[effectiveTier] ?? 0;
  const isModeLocked = (mode: AdvancedMode): boolean => {
    const entry = ADVANCED_MODES.find((m) => m.key === mode);
    if (!entry) return true;
    return effectiveRank < TIER_RANK[entry.minTier];
  };

  // The backend tells us which categories were locked for this check run.
  // Deriving from the response (rather than from the membership hook) means
  // anonymous free users and logged-in free users behave identically, and a
  // tier change on the server is reflected without a client reload.
  const lockedCategorySet = useMemo(
    () => new Set(result?.locked_categories ?? []),
    [result],
  );
  const isUnlocked = lockedCategorySet.size === 0;

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
      const info = checks.filter((c) => c.status === 'INFO').length;
      const passRate = total === 0 ? 0 : Math.round((passed / total) * 100);
      const lockedInTab = cats.filter((c) => lockedCategorySet.has(c)).length;
      // Paid tab is "locked" if not in FREE_GROUPS and any category is
      // in the backend-provided locked set. Free tabs are never locked.
      const isPaid = !FREE_GROUPS.has(tab) && tab !== 'other';
      const isTabLocked = isPaid && lockedInTab > 0;
      return { tab, total, passed, failed, warned, info, passRate, lockedInTab, isPaid, isTabLocked };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allTabs, checksByCategory, lockedCategorySet]);

  const handleRerunSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = rerunUrl.trim();
    setRerunError('');
    if (!trimmed) {
      setRerunError(t('home.error.empty'));
      return;
    }
    try {
      new URL(trimmed);
    } catch {
      setRerunError(t('home.error.invalid'));
      return;
    }

    // Advanced mode → delegate to dedicated page (built in later phase).
    if (rerunMode !== 'default') {
      if (isModeLocked(rerunMode)) {
        if (!isLoggedIn) {
          navigate('/login');
          return;
        }
        setShowPaymentModal(true);
        return;
      }
      navigate(`/advanced/${rerunMode}`, { state: { url: trimmed } });
      return;
    }

    // Default mode → same call the homepage makes, then replace this page.
    setRerunLoading(true);
    geoApi
      .checkGeo({ url: trimmed })
      .then((freshResult) => {
        navigate('/result', { state: { result: freshResult }, replace: true });
      })
      .catch(() => {
        setRerunError(t('home.error.failed'));
      })
      .finally(() => {
        setRerunLoading(false);
      });
  };

  if (!result) {
    return (
      <div className="min-h-screen grid-background flex items-center justify-center">
        <div className="bg-card border border-[#3f4143] border-border rounded-2xl p-8 max-w-md w-full text-center">
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
          {/* Top bar: title + rerun input (center) + export buttons (right) */}
          <div className="flex flex-col gap-4 mb-6 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0 lg:shrink-0">
              <h1 className="text-2xl sm:text-3xl font-bold gradient-text mb-1.5">
                {t('result.title')}
              </h1>
              <div className="flex items-center gap-2 text-xs sm:text-sm text-secondary min-w-0 mb-2">
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
              {/* Compact overall score + grade chip — replaces the deleted top card. */}
              <div className="inline-flex items-center gap-2 pl-2 pr-1 py-1 rounded-full border border-[#3f4143] border-cyan-500/20 bg-gradient-to-r from-cyan-500/5 via-purple-500/5 to-pink-500/5">
                <span className="text-[10px] uppercase tracking-[0.18em] text-[#d5d5dc] font-semibold">
                  {t('result.scoreCard.title', { defaultValue: 'Score' })}
                </span>
                <span className="text-base font-bold gradient-text tabular-nums leading-none">{score}</span>
                <span className="text-[10px] text-[#8a8a94] font-mono">/100</span>
                <span className="text-[11px] font-bold px-1.5 py-0.5 rounded bg-cyan-500/10 text-accent-primary border border-[#3f4143] border-cyan-500/30">
                  {grade}
                </span>
              </div>
            </div>

            {/* Rerun bar — matches the homepage capsule but one size tighter. */}
            <form
              onSubmit={handleRerunSubmit}
              className="flex-1 min-w-0 lg:max-w-xl w-full"
            >
              <div className="flex items-center bg-card border border-[#3f4143] rounded-full p-1 shadow-glow">
                {isLoggedIn && (
                  <div className="relative shrink-0">
                    <select
                      value={rerunMode}
                      onChange={(e) => setRerunMode(e.target.value as 'default' | AdvancedMode)}
                      className="appearance-none bg-transparent text-xs text-primary pl-3 pr-7 py-2 rounded-full border-r border-[#3f4143] focus:outline-none cursor-pointer"
                      title={t('result.header.modeLabel')}
                    >
                      <option value="default" className="bg-card text-primary">
                        {t('result.header.modeDefault')}
                      </option>
                      {ADVANCED_MODES.map((m) => {
                        const locked = isModeLocked(m.key);
                        return (
                          <option key={m.key} value={m.key} className="bg-card text-primary">
                            {locked ? '🔒 ' : ''}
                            {t(`home.advanced.cards.${m.key}.title`)}
                          </option>
                        );
                      })}
                    </select>
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-secondary pointer-events-none"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M19 9l-7 7-7-7" />
                    </svg>
                  </div>
                )}
                <input
                  type="text"
                  value={rerunUrl}
                  onChange={(e) => setRerunUrl(e.target.value)}
                  placeholder={t('result.header.rerunPlaceholder')}
                  className="flex-1 min-w-0 py-2 px-3 text-xs sm:text-sm bg-transparent focus:outline-none text-primary placeholder-muted"
                  disabled={rerunLoading}
                />
                <button
                  type="submit"
                  disabled={rerunLoading}
                  title={t('result.header.rerun')}
                  className="gradient-bg text-white w-9 h-9 rounded-full flex items-center justify-center hover:opacity-90 transition-all shrink-0 disabled:opacity-60"
                >
                  {rerunLoading ? (
                    <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth={4} />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                  )}
                </button>
              </div>
              {rerunError && (
                <p className="mt-2 text-[11px] text-rose-400 px-3">{rerunError}</p>
              )}
            </form>

            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={handleCopyLink}
                title={t('result.shareExport.copyLink')}
                className="w-9 h-9 rounded-lg bg-card border border-[#3f4143] border-border text-secondary hover:text-accent-primary hover:border-accent-primary/40 transition-colors flex items-center justify-center"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              </button>
              <button
                onClick={handleExportCSV}
                title={t('result.shareExport.exportCSV')}
                className="w-9 h-9 rounded-lg bg-card border border-[#3f4143] border-border text-secondary hover:text-accent-primary hover:border-accent-primary/40 transition-colors flex items-center justify-center"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </button>
              <button
                onClick={handleExportPDF}
                title={t('result.shareExport.exportPDF')}
                className="w-9 h-9 rounded-lg bg-card border border-[#3f4143] border-border text-secondary hover:text-accent-primary hover:border-accent-primary/40 transition-colors flex items-center justify-center"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 17h2a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-10a2 2 0 00-2-2H9a2 2 0 00-2 2v10a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
              </button>
            </div>
          </div>

          {/* Tab navigation — card-style tabs. Inactive tabs carry their own
              border-b; the active tab drops its bottom border so it visually
              merges with the content panel below. Doing the bottom line
              per-tab (instead of a container border-b + cover hack) renders
              identically across Chrome / Safari / Firefox. */}
          <div
            role="tablist"
            className="flex flex-wrap items-end gap-1.5 mt-2"
          >
            {groupStats.map((g) => {
              const isActive = activeTab === g.tab;
              return (
                <button
                  key={g.tab}
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setActiveTab(g.tab)}
                  title={g.isTabLocked ? t('result.paywall.unlockCategory', { defaultValue: '升级检测会员解锁本项检测 →' }) : undefined}
                  className={`relative px-4 py-2.5 rounded-t-xl border text-left transition-all ${isActive
                    ? 'border-cyan-400/60 border-b-transparent bg-gradient-to-b from-cyan-500/15 via-purple-500/10 to-transparent shadow-[0_-1px_0_0_rgba(0,240,255,0.25),-1px_0_0_0_rgba(0,240,255,0.1),1px_0_0_0_rgba(0,240,255,0.1)] z-10'
                    : 'border-transparent border-b-[#3f4143] bg-card/40 hover:border-b-cyan-500/30 hover:bg-card/60'
                    }`}
                >
                  {isActive && (
                    <span className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-cyan-400/80 to-transparent"></span>
                  )}
                  <div className="flex items-center gap-2 min-w-0">
                    {g.isTabLocked && (
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-3 w-3 text-accent-primary shrink-0"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                      </svg>
                    )}
                    <span
                      className={`text-xs font-semibold truncate ${isActive
                        ? 'text-primary'
                        : g.isTabLocked
                          ? 'text-[#d5d5dc]'
                          : 'text-secondary hover:text-primary'
                        }`}
                    >
                      {tabLabel(g.tab)}
                    </span>
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.5 rounded shrink-0 ${isActive
                        ? 'bg-cyan-500/20 text-accent-primary border border-cyan-500/30'
                        : 'bg-white/5 text-[#d5d5dc]'
                        }`}
                    >
                      {g.total}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Per-tab header card — scoped stats, pass-rate bar, optional unlock CTA. */}
          {(() => {
            const stat = groupStats.find((g) => g.tab === activeTab);
            if (!stat) return null;
            // Locked (member) tabs: the entire body is replaced with a frosted
            // glass overlay. The user cannot preview any data — they must upgrade.
            if (stat.isTabLocked) {
              return (
                <div className="relative mt-5 mb-5 rounded-b-2xl rounded-tr-2xl border border-[#3f4143] border-t-0 bg-card overflow-hidden">
                  {/* Blurred skeleton placeholder so the glass effect has something to stand on */}
                  <div className="p-6 sm:p-8 blur-sm select-none pointer-events-none space-y-3">
                    {Array.from({ length: 5 }).map((_, i) => (
                      <div key={i} className="flex items-center gap-3">
                        <span className="w-1.5 h-1.5 rounded-full bg-cyan-400/40"></span>
                        <span className="w-12 h-4 rounded bg-white/10"></span>
                        <span className="flex-1 h-3 rounded bg-white/10"></span>
                      </div>
                    ))}
                  </div>
                  {/* Frosted glass overlay */}
                  <div className="absolute inset-0 flex items-center justify-center backdrop-blur-md bg-gradient-to-br from-cyan-500/5 via-purple-500/5 to-pink-500/5">
                    <div className="flex flex-col items-center gap-4 px-6 py-8 max-w-sm text-center">
                      <div className="w-14 h-14 rounded-full bg-gradient-to-br from-cyan-500/20 via-purple-500/20 to-pink-500/20 border border-cyan-500/40 flex items-center justify-center shadow-[0_0_24px_rgba(0,240,255,0.25)]">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                      </div>
                      <div className="space-y-1">
                        <p className="text-sm font-semibold text-primary">
                          {t('result.paywall.memberOnly', { defaultValue: '此检测项需要开通会员' })}
                        </p>
                        <p className="text-xs text-secondary">
                          {t('result.paywall.subtitle')}
                        </p>
                      </div>
                      <button
                        onClick={() => navigate('/products-services')}
                        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full gradient-bg text-white text-xs font-semibold hover:opacity-90 transition-all shadow-glow"
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
                        </svg>
                        {t('result.paywall.upgradePro', { defaultValue: '升级 Pro' })}
                      </button>
                    </div>
                  </div>
                </div>
              );
            }
            return (
              <div className="bg-card border border-[#3f4143] border-border rounded-2xl p-4 sm:p-5 mt-5 mb-5 relative overflow-hidden">
                <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-cyan-400/40 to-transparent"></div>
                <div className="flex items-center justify-between gap-3 mb-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="w-1 h-5 gradient-bg rounded-full shrink-0"></span>
                    <h2 className="text-sm sm:text-base font-bold text-primary truncate">
                      {tabLabel(activeTab)}
                    </h2>
                    {stat.isTabLocked && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-500/10 text-accent-primary border border-[#3f4143] border-cyan-500/30 shrink-0">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                        {t('result.paywall.locked', { defaultValue: 'Locked' })}
                      </span>
                    )}
                  </div>
                  <span className="text-[11px] font-mono text-[#d5d5dc] tabular-nums shrink-0">
                    {stat.passed}/{stat.total} · {stat.passRate}%
                  </span>
                </div>
                {/* Pass-rate bar */}
                <div className="h-1.5 rounded-full bg-white/5 overflow-hidden mb-4">
                  <div
                    className="h-full bg-gradient-to-r from-cyan-400 via-purple-500 to-pink-500 transition-all duration-700"
                    style={{ width: `${stat.passRate}%` }}
                  ></div>
                </div>
                {/* Group-scoped stat pills */}
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                  <StatPill color="emerald" value={stat.passed} label={t('result.summary.passed')} />
                  <StatPill color="amber" value={stat.warned} label={t('result.summary.warnings')} />
                  <StatPill color="rose" value={stat.failed} label={t('result.summary.failed')} />
                  <StatPill color="cyan" value={stat.info} label={t('result.summary.info')} />
                  <StatPill color="muted" value={stat.total} label={t('result.summary.totalChecks')} />
                </div>
                {stat.isTabLocked && (
                  <button
                    onClick={handleUnlockClick}
                    className="mt-4 w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-dashed border-cyan-500/30 bg-gradient-to-r from-cyan-500/10 via-purple-500/10 to-pink-500/10 hover:from-cyan-500/20 hover:via-purple-500/20 hover:to-pink-500/20 transition-colors"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                    </svg>
                    <span className="text-xs font-semibold text-accent-primary">
                      {t('result.paywall.unlockCategory', { defaultValue: '升级检测会员解锁本项检测 →' })}
                    </span>
                  </button>
                )}
              </div>
            );
          })()}

          {/* Detailed table — skipped for locked tabs (the frosted overlay above
              already covers the full body for member-only tabs). */}
          <div className="space-y-5 mb-8">
            {(() => {
              const activeStat = groupStats.find((g) => g.tab === activeTab);
              if (activeStat?.isTabLocked) return null;
              const renderRow = (check: CheckResult, rowKey: string) => {
                const theme = statusTheme(check.status);
                return (
                  <div
                    key={rowKey}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-tertiary/20 transition-colors"
                  >
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${theme.dot}`}></span>
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border border-[#3f4143] shrink-0 w-12 text-center ${theme.badge}`}>
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
                checksForCategory: CheckResult[],
                rows: ReactNode,
                visibleCount: number,
                totalCount: number,
              ) => {
                const lockedInCat = totalCount - visibleCount;
                // Try the rich visual first. resolveCategoryVisual is a plain
                // function — it returns a React element or null. If null, we
                // fall back to the plain row list so every category always
                // renders *something*. (This was previously a <Component/> ref
                // which made `visual` always truthy and silently broke the
                // fallback — the list never rendered for un-visualized
                // categories like HTTPS / sitemap.xml / Mobile & Weight.)
                const visual = resolveCategoryVisual(categoryKey, checksForCategory);
                return (
                  <div key={categoryKey}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="w-1 h-4 gradient-bg rounded-full"></span>
                        <h3 className="text-sm font-semibold text-primary">
                          {t(`result.categoryLabels.${categoryKey}`, { defaultValue: categoryKey })}
                        </h3>
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
                    {visual ? (
                      visual
                    ) : (
                      <div className="bg-card border border-[#3f4143] border-border rounded-xl overflow-hidden">
                        <div className="divide-y divide-border">{rows}</div>
                      </div>
                    )}
                  </div>
                );
              };

              const renderLockedPlaceholder = (categoryKey: string) => (
                <div key={`locked-${categoryKey}`}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="w-1 h-4 bg-gradient-to-b from-cyan-400/60 via-purple-500/60 to-pink-500/60 rounded-full"></span>
                      <h3 className="text-sm font-semibold text-secondary">
                        {t(`result.categoryLabels.${categoryKey}`, { defaultValue: categoryKey })}
                      </h3>
                    </div>
                    <span className="flex items-center gap-1 text-[10px] font-mono text-accent-primary">
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                      </svg>
                      {t('result.paywall.locked')}
                    </span>
                  </div>
                  <button
                    onClick={handleUnlockClick}
                    className="w-full flex items-center gap-3 px-4 py-3 rounded-xl border border-dashed border-cyan-500/20 bg-gradient-to-r from-cyan-500/5 via-purple-500/5 to-pink-500/5 hover:from-cyan-500/10 hover:via-purple-500/10 hover:to-pink-500/10 transition-colors group"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                    </svg>
                    <span className="text-xs text-secondary group-hover:text-primary transition-colors">
                      {t('result.paywall.unlockCategory', { defaultValue: '升级检测会员解锁本项检测 →' })}
                    </span>
                  </button>
                </div>
              );

              const presentCategories = activeCategories.filter(
                (c) => (checksByCategory[c]?.length ?? 0) > 0 || lockedCategorySet.has(c),
              );

              if (presentCategories.length === 0) {
                return (
                  <div className="bg-card border border-[#3f4143] border-border rounded-xl p-8 text-center text-secondary text-xs">
                    {t('result.error.noData')}
                  </div>
                );
              }

              // Render each category either as a full block (unlocked / has data)
              // or as a 🔒 placeholder card (belongs to the tab but is in the
              // locked set for this tier).
              return presentCategories.map((categoryKey) => {
                if (lockedCategorySet.has(categoryKey)) {
                  return renderLockedPlaceholder(categoryKey);
                }
                const checks = checksByCategory[categoryKey] || [];
                return renderCategoryBlock(
                  categoryKey,
                  checks,
                  checks.map((check, idx) => renderRow(check, `${categoryKey}-${idx}`)),
                  checks.length,
                  checks.length,
                );
              });
            })()}
          </div>

          {/* Footer actions */}
          <div className="flex flex-col sm:flex-row gap-3">
            <button
              onClick={() => navigate('/')}
              className="flex-1 bg-card border border-[#3f4143] border-border rounded-xl py-3 px-5 text-sm font-semibold text-primary hover:bg-tertiary hover:border-accent-primary/40 transition-all flex items-center justify-center gap-2"
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
              className="flex-1 bg-card border border-[#3f4143] border-border rounded-xl py-3 px-5 text-sm font-semibold text-primary hover:bg-tertiary hover:border-accent-primary/40 transition-all flex items-center justify-center gap-2"
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
    <div className={`flex items-center gap-2.5 px-3 py-2 rounded-lg border border-[#3f4143] ${palette.border} ${palette.bg}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${palette.dot}`}></span>
      <div className="flex flex-col leading-tight min-w-0">
        <span className={`text-base font-bold tabular-nums ${palette.text}`}>{value}</span>
        <span className="text-[9px] uppercase tracking-wider text-[#d5d5dc] truncate">{label}</span>
      </div>
    </div>
  );
}
