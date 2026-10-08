// Time display utilities - backend timestamps are UTC (naive, no timezone marker) and users are in Beijing.
// Always parse as UTC -> display in Beijing time (Asia/Shanghai) to avoid an 8-hour offset.

/**
 * Format a backend timestamp (ISO string, possibly naive UTC without Z) for display in Beijing time.
 * @param s time string (e.g. "2026-06-11T02:21:27" or with Z/+08:00)
 * @param withSeconds whether to show seconds (default true)
 * Returns "YYYY-MM-DD HH:mm[:ss]"; falls back to the original string if parsing fails.
 */
export function fmtTime(s?: string | null, withSeconds = true): string {
  return fmtTimeIn(s, 'Asia/Shanghai', withSeconds);
}

/** The browser's timezone (falls back to Beijing when unavailable). */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
  } catch {
    return 'Asia/Shanghai';
  }
}

/**
 * Same as fmtTime, but displays in the timezone of **the person reading the report**: omitting tz = browser timezone.
 * Used for customer deliverables (one-click diagnosis report) - overseas customers reading Beijing time would not match their own clocks.
 * Ops/admin pages still use fmtTime pinned to Beijing, since they need to line up with server logs.
 */
export function fmtTimeIn(s?: string | null, tz?: string | null, withSeconds = true): string {
  if (!s) return '';
  // The backend sends naive UTC (no timezone marker) -> append Z to mark it UTC; values that already carry a timezone (Z/+/-) are kept as-is
  const hasTz = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(s);
  const d = new Date(hasTz ? s : s + 'Z');
  if (isNaN(d.getTime())) return s.replace('T', ' ').slice(0, withSeconds ? 19 : 16);
  let timeZone = tz || browserTimeZone();
  try { new Intl.DateTimeFormat('zh-CN', { timeZone }); } catch { timeZone = 'Asia/Shanghai'; }
  const fmt = new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
    hour12: false,
  });
  // Intl outputs e.g. "2026/06/11 10:21:27" -> normalize to "2026-06-11 10:21:27"
  return fmt.format(d).replace(/\//g, '-');
}

/**
 * Backend timestamp -> calendar day in the Beijing timezone, "YYYY-MM-DD".
 * Time filters (today / last week) must split days on Beijing time, otherwise UTC 00:00-08:00 is counted as the previous day.
 */
export function bjDay(s?: string | null): string {
  if (!s) return '';
  const hasTz = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(s);
  const d = new Date(hasTz ? s : s + 'Z');
  if (isNaN(d.getTime())) return s.slice(0, 10);
  // en-CA's short date is already YYYY-MM-DD, no need to assemble it
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(d);
}

/** Today's (Beijing) "YYYY-MM-DD" */
export function bjToday(): string {
  return bjDay(new Date().toISOString());
}

/** Add/subtract days on a "YYYY-MM-DD", still returning "YYYY-MM-DD" */
export function shiftDay(day: string, delta: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  if (isNaN(d.getTime())) return day;
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}
