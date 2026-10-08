// Time filter metric definitions for list pages -- the component is in FilterBar.tsx; only types and pure functions live here
// (react-refresh requires component files not to export non-components).
//
// Always compare by **Beijing calendar day**: the backend stores naive UTC, and slicing the first 10 characters of the ISO string would count
// drafts from 00:00-08:00 Beijing time as the previous day.

import { bjDay, bjToday, shiftDay } from '../../utils/datetime';

export type DatePreset = 'all' | 'today' | 'week' | 'month' | 'custom';

export type DateRangeValue = {
  preset: DatePreset;
  /** Effective when preset='custom', "YYYY-MM-DD"; only from filled with the same to = a single day */
  from?: string;
  to?: string;
};

export const DATE_RANGE_ALL: DateRangeValue = { preset: 'all' };

export const DATE_PRESET_LABELS: Record<DatePreset, string> = {
  all: '不限时间',
  today: '今日',
  week: '最近一周',
  month: '最近一个月',
  custom: '自定义…',
};

/** Resolve a preset into a concrete closed interval [from, to] (Beijing day); all -> null */
export function resolveRange(v: DateRangeValue): { from: string; to: string } | null {
  const today = bjToday();
  switch (v.preset) {
    case 'today': return { from: today, to: today };
    case 'week': return { from: shiftDay(today, -6), to: today };   // 7 days including today
    case 'month': return { from: shiftDay(today, -29), to: today };
    case 'custom': {
      const from = v.from || '';
      const to = v.to || v.from || '';
      if (!from && !to) return null;
      // Filling only one end means an open interval with the other end at the extreme, so users don't see nothing after filling half
      return { from: from || '0000-01-01', to: to || '9999-12-31' };
    }
    default: return null;
  }
}

/** Whether a timestamp falls within the filter interval */
export function inDateRange(ts: string | null | undefined, v: DateRangeValue): boolean {
  const r = resolveRange(v);
  if (!r) return true;
  const day = bjDay(ts);
  if (!day) return false;
  return day >= r.from && day <= r.to;
}

export function isDateFilterActive(v: DateRangeValue): boolean {
  return resolveRange(v) !== null;
}

/** One-line summary on the filter bar, e.g. "Today" "2026-09-01 ~ 2026-09-04" */
export function dateRangeSummary(v: DateRangeValue): string {
  if (v.preset !== 'custom') return DATE_PRESET_LABELS[v.preset];
  const r = resolveRange(v);
  if (!r) return '自定义';
  if (r.from === r.to) return r.from;
  return `${r.from === '0000-01-01' ? '最早' : r.from} ~ ${r.to === '9999-12-31' ? '至今' : r.to}`;
}

/**
 * Filter value -> backend query params (since/until, "YYYY-MM-DD", empty string means no limit).
 * Presets compute real values from today; custom with only one end really sends only that end -- don't leak internal sentinel dates.
 */
export function toQueryRange(v: DateRangeValue): { since: string; until: string } {
  if (v.preset === 'custom') return { since: v.from || '', until: v.to || '' };
  const r = resolveRange(v);
  return r ? { since: r.from, until: r.to } : { since: '', until: '' };
}

/** Backend query params -> filter value (used when page state is still two strings, since/until) */
export function fromQueryRange(since: string, until: string): DateRangeValue {
  if (!since && !until) return DATE_RANGE_ALL;
  return { preset: 'custom', from: since, to: until };
}
