// Shared "weekly pages + grouping by day" utilities for the publish plan — the view mode (ExecutionPlan) and edit mode
// (PublishingPlanEditor) both use this, replacing the old mixed seed-group × row-pagination structure.
// publish_date may be YYYY-MM-DD or YYYY-MM-DDTHH:MM; the date part is used for week/day grouping.

export interface WeekPage {
  start: string;   // Monday, ISO date (inclusive)
  end: string;     // Sunday, ISO date (inclusive)
}

const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

export const dayOf = (publishDate: string): string => (publishDate || '').slice(0, 10);

const parse = (iso: string): Date | null => {
  const d = new Date(iso + 'T00:00:00');
  return Number.isNaN(d.getTime()) ? null : d;
};

const toISO = (d: Date): string => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

export const mondayOf = (iso: string): string | null => {
  const d = parse(iso);
  if (!d) return null;
  const dow = d.getDay();               // 0=Sunday
  d.setDate(d.getDate() - ((dow + 6) % 7));
  return toISO(d);
};

/**
 * Add/subtract days to a date, **by local calendar day**.
 *
 * Don't change this back to `toISOString().slice(0,10)` -- that's UTC and is off by a whole day in UTC+8:
 * addDays('2026-09-11', 1) would return '2026-09-11' (stuck), and -1 would return '2026-09-09' (two days back).
 * planRow.ts once had such an implementation, so the month view's "next day" didn't move and "+ Add a row"
 * couldn't get the next day. Two implementations of the same utility, one wrong, was that bug's root cause; only this one remains.
 */
export const addDays = (iso: string, n: number): string => {
  const d = parse(iso)!;
  d.setDate(d.getDate() + n);
  return toISO(d);
};

/** Derive contiguous week pages from the set of plan row dates (Monday first, covering min..max; weeks without rows are kept for scroll awareness). */
export function buildWeeks(dayISOs: string[]): WeekPage[] {
  const mondays = dayISOs.map(mondayOf).filter((x): x is string => !!x);
  if (!mondays.length) return [];
  const first = mondays.reduce((a, b) => (a < b ? a : b));
  const last = mondays.reduce((a, b) => (a > b ? a : b));
  const weeks: WeekPage[] = [];
  for (let m = first; m <= last; m = addDays(m, 7)) {
    weeks.push({ start: m, end: addDays(m, 6) });
    if (weeks.length > 60) break;   // Fallback: don't lay out endlessly on abnormal data
  }
  return weeks;
}

/** Index of the week containing today; clamped to the nearest end if out of range. */
export function defaultWeekIdx(weeks: WeekPage[]): number {
  if (!weeks.length) return 0;
  const today = toISO(new Date());
  const idx = weeks.findIndex(w => w.start <= today && today <= w.end);
  if (idx >= 0) return idx;
  return today < weeks[0].start ? 0 : weeks.length - 1;
}

export const todayISO = (): string => toISO(new Date());

export function fmtDayLabel(iso: string): string {
  const d = parse(iso);
  if (!d) return iso || '未排期';
  return `${d.getMonth() + 1}月${d.getDate()}日 ${WEEKDAY[d.getDay()]}`;
}

export const fmtShort = (iso: string): string => {
  const d = parse(iso);
  return d ? `${d.getMonth() + 1}月${d.getDate()}日` : iso;
};

/** Status summary for the day group header: ✓drafted a · ⏳generating b · ○not generated c (zero segments hidden). */
export function daySummary(parts: { done: number; generating: number; pending: number }): string {
  const seg: string[] = [];
  if (parts.done) seg.push(`✓ 已出稿 ${parts.done}`);
  if (parts.generating) seg.push(`⏳ 生成中 ${parts.generating}`);
  if (parts.pending) seg.push(`○ 未生成 ${parts.pending}`);
  return seg.join(' · ');
}

/** Group the row list by day (preserving input order), returning [dayISO, rows][]; unscheduled (empty/bad date) goes under the '' key, last. */
export function groupByDay<T>(rows: T[], getDate: (r: T) => string): [string, T[]][] {
  const m = new Map<string, T[]>();
  for (const r of rows) {
    const key = dayOf(getDate(r)) || '';
    if (!m.has(key)) m.set(key, []);
    m.get(key)!.push(r);
  }
  const entries = [...m.entries()].sort((a, b) =>
    a[0] === '' ? 1 : b[0] === '' ? -1 : a[0].localeCompare(b[0]));
  return entries;
}
