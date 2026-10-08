import { useMemo } from 'react';
import { renderCheckMessage } from '../utils/checkText';
import type { CheckResult } from '../types/geo';

/**
 * Derived view data for one dimension (GEO or SEO).
 *
 * Extracted from pages/Result.tsx: the result page, PDF exporter and PDF print page
 * (pages/ResultPrint.tsx) must run the same grouping / pass rate algorithm; three copies would drift sooner or later.
 */

const STATUS_ORDER: Record<string, number> = { FAIL: 0, WARN: 1, INFO: 2, PASS: 3 };

export interface GroupStat {
  tab: string;
  total: number;
  passed: number;
  failed: number;
  warned: number;
  info: number;
  passRate: number;
  lockedInTab: number;
  isPaid: boolean;
  isTabLocked: boolean;
  passedNames: string[];
  failedNames: string[];
  warnedNames: string[];
  infoNames: string[];
  allNames: string[];
}

export interface DimensionView {
  checksByCategory: Record<string, CheckResult[]>;
  otherCategories: string[];
  allTabs: string[];
  groupStats: GroupStat[];
  categoriesForTab: (tab: string) => string[];
  lockedCategorySet: Set<string>;
}

/**
 * Derive everything the tab UI needs from one report (GEO or SEO).
 *
 * Extracted so the GEO and SEO dimensions run through the *same* code rather
 * than two drifting copies — the SEO suite emits the identical line protocol,
 * so grouping, pass-rate math and paywall logic are genuinely shared. Called
 * once per dimension; the active one is picked by the `dimension` state.
 */
export function useDimensionView(
  checks: CheckResult[] | undefined,
  groups: Record<string, string[]>,
  freeGroups: Set<string>,
  lockedCategories: string[] | null | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
  language: string,
): DimensionView {
  const lockedCategorySet = useMemo(
    () => new Set(lockedCategories ?? []),
    [lockedCategories],
  );

  const checksByCategory = useMemo(() => {
    const map: Record<string, CheckResult[]> = {};
    for (const check of checks || []) {
      (map[check.category] ||= []).push(check);
    }
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9));
    }
    return map;
  }, [checks]);

  const groupKeys = useMemo(() => Object.keys(groups), [groups]);

  const knownCategories = useMemo(
    () => new Set(groupKeys.flatMap((g) => groups[g])),
    [groupKeys, groups],
  );

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
    tab === 'other' ? otherCategories : groups[tab] || [];

  const groupStats = useMemo(() => {
    return allTabs.map((tab) => {
      const cats = tab === 'other' ? otherCategories : groups[tab] || [];
      const tabChecks = cats.flatMap((c) => checksByCategory[c] || []);
      const total = tabChecks.length;
      const nameOf = (c: CheckResult) => renderCheckMessage(c, t);
      const namesByStatus = (status: string) =>
        tabChecks.filter((c) => c.status === status).map(nameOf);
      const passedNames = namesByStatus('PASS');
      const failedNames = namesByStatus('FAIL');
      const warnedNames = namesByStatus('WARN');
      const infoNames = namesByStatus('INFO');
      const allNames = tabChecks.map(nameOf);
      const passed = passedNames.length;
      const failed = failedNames.length;
      const warned = warnedNames.length;
      const info = infoNames.length;
      const passRate = total === 0 ? 0 : Math.round((passed / total) * 100);
      const lockedInTab = cats.filter((c) => lockedCategorySet.has(c)).length;
      // Paid tab is "locked" if not in freeGroups and any category is
      // in the backend-provided locked set. Free tabs are never locked.
      const isPaid = !freeGroups.has(tab) && tab !== 'other';
      const isTabLocked = isPaid && lockedInTab > 0;
      return {
        tab, total, passed, failed, warned, info, passRate, lockedInTab,
        isPaid, isTabLocked,
        passedNames, failedNames, warnedNames, infoNames, allNames,
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allTabs, checksByCategory, lockedCategorySet, otherCategories, groups, freeGroups, language]);

  return { checksByCategory, otherCategories, allTabs, groupStats, categoriesForTab, lockedCategorySet };
}
