// Publish plan week switcher — pairs with the weekly pagination data in planWeeks.ts (split into its own file for react-refresh rules).
import { fmtShort, todayISO, type WeekPage } from './planWeeks';

/** Week switcher: ◀ Previous week | Aug 3 - Aug 9 (week 2 of 5) | Next week ▶ [Back to today] */
export function WeekPager({
  weeks, idx, onIdx, extraLabel,
}: {
  weeks: WeekPage[];
  idx: number;
  onIdx: (i: number) => void;
  extraLabel?: string | null;   // Appended after the title (e.g. "+ N unscheduled rows")
}) {
  if (weeks.length <= 1 && !extraLabel) return null;
  const w = weeks[idx];
  const today = todayISO();
  const todayIdx = weeks.findIndex(x => x.start <= today && today <= x.end);
  const btn = {
    background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
    border: '1px solid var(--border-color)',
  } as const;
  return (
    <div className="flex items-center justify-between gap-2 text-xs flex-wrap">
      <button type="button" disabled={idx <= 0}
              onClick={() => onIdx(Math.max(0, idx - 1))}
              className="px-2 py-1 rounded-md disabled:opacity-40" style={btn}>
        ◀ 上一周
      </button>
      <div className="text-secondary tabular-nums">
        {w ? (
          <>
            <span className="text-primary font-medium">{fmtShort(w.start)} - {fmtShort(w.end)}</span>
            <span className="text-muted ml-1.5">(第 {idx + 1} 周 / 共 {weeks.length} 周)</span>
          </>
        ) : '—'}
        {extraLabel && <span className="text-muted ml-1.5">{extraLabel}</span>}
      </div>
      <div className="flex items-center gap-2">
        {todayIdx >= 0 && todayIdx !== idx && (
          <button type="button" onClick={() => onIdx(todayIdx)}
                  className="px-2 py-1 rounded-md"
                  style={{ ...btn, color: 'var(--accent-primary)' }}>
            回到今天
          </button>
        )}
        <button type="button" disabled={idx >= weeks.length - 1}
                onClick={() => onIdx(Math.min(weeks.length - 1, idx + 1))}
                className="px-2 py-1 rounded-md disabled:opacity-40" style={btn}>
          下一周 ▶
        </button>
      </div>
    </div>
  );
}
