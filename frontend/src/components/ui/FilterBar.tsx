// Unified skeleton for list page filter bars -- shared by "Content publishing & review" and "Content & placement".
//
// Why it exists: the two pages each stuffed a row of mutually exclusive pills + two dropdowns + a search box
// into the same flex-wrap, with different control heights/shapes/spacing, falling apart as soon as it wrapped on narrow screens.
// Here the layout is consolidated into two rows:
//   Row 1 = search (fills remaining width) + main selector + action buttons
//   Row 2 = several "label + control" filter items + view switch / clear on the right
// Each page only puts controls into slots, no longer writing its own flex layout.

import type { ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { POPUP_Z } from '../overlays/zIndex';
import { DayPicker, type DateRange } from 'react-day-picker';
import { zhCN } from 'react-day-picker/locale';
import 'react-day-picker/style.css';
import { FILTER_CONTROL_CLASS, FILTER_CONTROL_STYLE } from './filterControls';
import {
  DATE_PRESET_LABELS, dateRangeSummary, type DatePreset, type DateRangeValue,
  isDateFilterActive, resolveRange,
} from './dateFilter';

export function FilterBar({ children }: { children: ReactNode }) {
  return (
    <div
      className="rounded-lg p-2.5 flex flex-col gap-2.5"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      {children}
    </div>
  );
}

/**
 * Row 1 = "which set to find": search + context selector (project) on the left, time range / sort / primary action on the right.
 * The time range sits in the top right because it's a different kind of thing from the search on the left -- one defines the range,
 * the other searches within it; analytics admins (GA / Vercel / Linear) all put dates in this spot.
 */
export function FilterBarPrimary({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {children}
      {right != null && <div className="ml-auto flex items-center gap-2 flex-wrap">{right}</div>}
    </div>
  );
}

/** Row 2 = "what to filter within this set": status / source / template... content in right sits on the right (view switch, counts). */
export function FilterBarSecondary({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {children}
      {right != null && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}

/** One filter item = small gray label + control, on the same line, never wrapping separately */
export function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <span className="text-xs whitespace-nowrap" style={{ color: 'var(--text-secondary)' }}>{label}</span>
      {children}
    </div>
  );
}

/** Search box with a magnifier and clear button */
export function SearchInput({
  value, onChange, placeholder, className = '',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={`relative flex items-center ${className}`}>
      <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden
           className="absolute left-2.5 pointer-events-none"
           style={{ color: 'var(--text-secondary)' }}>
        <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <path d="M10.5 10.5 L14 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-8 w-full text-xs rounded-md pl-8 pr-7 outline-none"
        style={FILTER_CONTROL_STYLE}
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="清除搜索"
          className="absolute right-2 text-xs leading-none"
          style={{ color: 'var(--text-secondary)' }}
        >
          ×
        </button>
      )}
    </div>
  );
}

/**
 * Segmented control -- only for "two or three views of the same data" (like view switching);
 * real filters always use multi-select dropdowns, no more laying them out as a row of pills.
 */
export function Segmented<T extends string>({
  value, onChange, options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; title?: string }[];
}) {
  return (
    <div className="inline-flex rounded-md overflow-hidden"
         style={{ border: '1px solid var(--border-color)' }}>
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          title={o.title}
          onClick={() => onChange(o.value)}
          className="h-8 px-2.5 text-xs whitespace-nowrap"
          style={{
            background: value === o.value ? 'var(--accent-primary)' : 'var(--bg-input)',
            color: value === o.value ? '#fff' : 'var(--text-secondary)',
            borderLeft: i === 0 ? undefined : '1px solid var(--border-color)',
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** "N matches · Clear filters" -- appears only when a filter is active */
export function FilterSummary({
  active, matched, total, onReset, resetLabel = '清空筛选',
}: {
  active: boolean;
  matched: number;
  total: number;
  onReset: () => void;
  resetLabel?: string;
}) {
  return (
    <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
      <span className="whitespace-nowrap">
        {active ? `${matched} / ${total} 条` : `共 ${total} 条`}
      </span>
      {active && (
        <button type="button" onClick={onReset} className="underline whitespace-nowrap"
                style={{ color: 'var(--accent-primary)' }}>
          {resetLabel}
        </button>
      )}
    </div>
  );
}


/**
 * Time filter -- Radix Popover (the project already uses Radix) + react-day-picker.
 * A column of presets on the left (today / last week / last month), and a calendar on the right to drag a range directly:
 * the first click is the start, the second the end; clicking the same day twice means just that day.
 * Date metric definition (Beijing calendar day) lives in dateFilter.ts; this only handles interaction.
 */

// "YYYY-MM-DD" <-> Date conversions always use **local noon**, avoiding the classic pitfall of timezones shifting the date by a day
const toDate = (day?: string): Date | undefined => {
  if (!day) return undefined;
  const [y, m, d] = day.split('-').map(Number);
  if (!y || !m || !d) return undefined;
  return new Date(y, m - 1, d, 12);
};
const toDay = (d?: Date): string => {
  if (!d) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

const PRESET_ORDER: DatePreset[] = ['all', 'today', 'week', 'month'];

export function DateRangeFilter({
  value, onChange,
}: {
  value: DateRangeValue;
  onChange: (v: DateRangeValue) => void;
}) {
  const r = resolveRange(value);
  const active = isDateFilterActive(value);
  const selected: DateRange | undefined = value.preset === 'custom' && r
    ? { from: toDate(value.from), to: toDate(value.to || value.from) }
    : undefined;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button"
                className={`${FILTER_CONTROL_CLASS} inline-flex items-center gap-1.5 whitespace-nowrap`}
                style={{
                  ...FILTER_CONTROL_STYLE,
                  ...(active ? { borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' } : null),
                }}>
          <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="2" y="3" width="12" height="11" rx="2" />
            <path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" strokeLinecap="round" />
          </svg>
          {dateRangeSummary(value)}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={6} className={`${POPUP_Z} rounded-lg p-2 flex gap-2`}
                         style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                                  boxShadow: '0 8px 24px rgba(0,0,0,0.28)' }}>
          {/* Preset column -- 90% of choices are these four; users shouldn't be forced to count on the calendar */}
          <div className="flex flex-col gap-1 pr-2 min-w-[104px]"
               style={{ borderRight: '1px solid var(--border-color)' }}>
            {PRESET_ORDER.map(k => {
              const on = value.preset === k;
              return (
                <Popover.Close asChild key={k}>
                  <button type="button" onClick={() => onChange({ preset: k })}
                          className="text-xs text-left px-2 py-1.5 rounded-md whitespace-nowrap"
                          style={on
                            ? { background: 'var(--accent-primary)', color: '#fff' }
                            : { color: 'var(--text-secondary)' }}>
                    {DATE_PRESET_LABELS[k]}
                  </button>
                </Popover.Close>
              );
            })}
            {value.preset === 'custom' && (
              <span className="text-[11px] px-2 pt-1" style={{ color: 'var(--accent-primary)' }}>
                {dateRangeSummary(value)}
              </span>
            )}
          </div>
          <div className="vg-daypicker">
            <DayPicker
              mode="range"
              locale={zhCN}
              numberOfMonths={2}
              defaultMonth={toDate(r?.from) ?? new Date()}
              selected={selected}
              onSelect={(range: DateRange | undefined) => {
                if (!range?.from) { onChange({ preset: 'all' }); return; }
                onChange({ preset: 'custom', from: toDay(range.from), to: toDay(range.to ?? range.from) });
              }}
            />
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/**
 * Single-day picker -- the same shell as DateRangeFilter (Radix Popover + react-day-picker),
 * for lists that "look at one day only" (batch run monitoring, Run list). Empty value = all time.
 */
export function SingleDayPicker({
  value, onChange, placeholder = '全部日期',
}: {
  /** "YYYY-MM-DD", empty string = no limit */
  value: string;
  onChange: (day: string) => void;
  placeholder?: string;
}) {
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1, 12);
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button"
                className={`${FILTER_CONTROL_CLASS} inline-flex items-center gap-1.5 whitespace-nowrap`}
                style={{
                  ...FILTER_CONTROL_STYLE,
                  ...(value ? { borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' } : null),
                }}>
          <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="2" y="3" width="12" height="11" rx="2" />
            <path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" strokeLinecap="round" />
          </svg>
          {value || placeholder}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={6} className={`${POPUP_Z} rounded-lg p-2 flex gap-2`}
                         style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                                  boxShadow: '0 8px 24px rgba(0,0,0,0.28)' }}>
          <div className="flex flex-col gap-1 pr-2 min-w-[88px]"
               style={{ borderRight: '1px solid var(--border-color)' }}>
            {[
              { label: '全部日期', day: '' },
              { label: '今天', day: toDay(today) },
              { label: '昨天', day: toDay(yesterday) },
            ].map(o => (
              <Popover.Close asChild key={o.label}>
                <button type="button" onClick={() => onChange(o.day)}
                        className="text-xs text-left px-2 py-1.5 rounded-md whitespace-nowrap"
                        style={value === o.day
                          ? { background: 'var(--accent-primary)', color: '#fff' }
                          : { color: 'var(--text-secondary)' }}>
                  {o.label}
                </button>
              </Popover.Close>
            ))}
          </div>
          <div className="vg-daypicker">
            <DayPicker
              mode="single"
              locale={zhCN}
              defaultMonth={toDate(value) ?? new Date()}
              selected={toDate(value)}
              onSelect={(d: Date | undefined) => onChange(toDay(d))}
            />
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
