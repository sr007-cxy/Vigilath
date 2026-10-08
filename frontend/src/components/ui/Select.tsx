// Site-wide unified dropdown selector, replacing the native <select> (native styles are uncontrollable and turn white in the dark theme).
// The popover uses portal + fixed positioning, so it's not clipped by container overflow and flips upward when space runs out.
// Usage (migration comparison with native select):
//   <select value={v} onChange={e => setV(e.target.value)}><option .../></select>
//   → <UiSelect value={v} onChange={setV} options={[{ value, label }, ...]} />

import {
  useEffect, useLayoutEffect, useMemo, useRef, useState,
  type CSSProperties, type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { POPUP_Z } from '../overlays/zIndex';

export interface UiSelectOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
  /** For searchable matching; required when label isn't a plain string, otherwise the item can't be found */
  keywords?: string;
}

const POPUP_MAX_H = 280;
/** Above this many options a search box opens in the popover by default -- finding things in a long list by scrolling is the most off-putting interaction */
const SEARCHABLE_AUTO_AT = 12;

const optionText = (o: UiSelectOption): string =>
  o.keywords ?? (typeof o.label === 'string' ? o.label : '');

/**
 * Popover positioning -- shared by UiSelect / UiMultiSelect.
 * portal + fixed: not clipped by container overflow, flips upward when space runs out.
 */
function usePopupPosition(open: boolean, rowCount: number) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number; maxH: number } | null>(null);

  const reposition = () => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - 8;
    const above = r.top - 8;
    const openUp = below < Math.min(POPUP_MAX_H, rowCount * 32 + 8) && above > below;
    const maxH = Math.min(POPUP_MAX_H, Math.max(120, openUp ? above : below));
    setPos({
      top: openUp ? Math.max(8, r.top - 4 - maxH) : r.bottom + 4,
      left: Math.min(r.left, Math.max(8, window.innerWidth - r.width - 8)),
      width: r.width,
      maxH,
    });
  };

  useLayoutEffect(() => {
    if (open) reposition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onScroll = (e: Event) => {
      if (popupRef.current?.contains(e.target as Node)) return;
      reposition();
    };
    window.addEventListener('scroll', onScroll, true);
    return () => window.removeEventListener('scroll', onScroll, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return { triggerRef, popupRef, pos, reposition };
}

/** The small dropdown arrow on the trigger button */
function Caret({ open }: { open: boolean }) {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10"
         className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
         style={{ color: 'var(--text-secondary)' }} aria-hidden>
      <path d="M1.5 3.5 L5 7 L8.5 3.5" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function UiSelect({
  value, onChange, options,
  placeholder = '—',
  disabled = false,
  className,
  style,
  title,
  popupMatchWidth = true,
  searchable,
  searchPlaceholder = '搜索…',
}: {
  value: string;
  onChange: (value: string) => void;
  options: UiSelectOption[];
  placeholder?: string;
  disabled?: boolean;
  /** Appended to the trigger button; default padding is used if not passed */
  className?: string;
  style?: CSSProperties;
  title?: string;
  /** Whether the popover is at least as wide as the trigger button */
  popupMatchWidth?: boolean;
  /** Whether the popover has a search box. If not passed, it opens automatically above 12 options */
  searchable?: boolean;
  searchPlaceholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  const withSearch = searchable ?? options.length > SEARCHABLE_AUTO_AT;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!withSearch || !q) return options;
    return options.filter(o => optionText(o).toLowerCase().includes(q));
  }, [options, query, withSearch]);

  const { triggerRef, popupRef, pos } = usePopupPosition(open, shown.length);

  const selected = options.find(o => o.value === value) || null;
  const activeIdxOf = (v: string) => shown.findIndex(o => o.value === v);

  const openPopup = () => {
    if (disabled || options.length === 0) return;
    setQuery('');
    setActive(activeIdxOf(value) >= 0 ? activeIdxOf(value) : options.findIndex(o => !o.disabled));
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!popupRef.current?.contains(t) && !triggerRef.current?.contains(t)) close();
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('resize', close);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Auto-focus the search box: once the popover opens you can type directly without another click
  useEffect(() => {
    if (open && withSearch) searchRef.current?.focus();
  }, [open, withSearch]);

  useEffect(() => {
    if (!open || active < 0) return;
    popupRef.current
      ?.querySelector(`[data-idx="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, active]);

  const move = (dir: 1 | -1) => {
    if (shown.length === 0) return;
    let i = active;
    for (let step = 0; step < shown.length; step++) {
      i = (i + dir + shown.length) % shown.length;
      if (!shown[i].disabled) break;
    }
    setActive(i);
  };

  const commit = (idx: number) => {
    const opt = shown[idx];
    if (!opt || opt.disabled) return;
    setOpen(false);
    triggerRef.current?.focus();
    if (opt.value !== value) onChange(opt.value);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
        e.preventDefault();
        openPopup();
      }
      return;
    }
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); move(1); break;
      case 'ArrowUp': e.preventDefault(); move(-1); break;
      case 'Enter': e.preventDefault(); commit(active); break;
      case 'Escape': e.stopPropagation(); setOpen(false); break;
      case 'Tab': setOpen(false); break;
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        title={title}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openPopup())}
        onKeyDown={onKeyDown}
        className={`inline-flex items-center justify-between gap-1.5 select-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${className ?? 'px-2.5 py-1.5 text-sm rounded-lg'}`}
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          color: 'var(--text-primary)',
          ...style,
        }}
      >
        <span className="truncate text-left">
          {selected ? selected.label : <span style={{ color: 'var(--text-secondary)' }}>{placeholder}</span>}
        </span>
        <Caret open={open} />
      </button>
      {open && pos && createPortal(
        <div
          ref={popupRef}
          role="listbox"
          className={`fixed ${POPUP_Z} rounded-lg shadow-xl flex flex-col`}
          style={{
            top: pos.top, left: pos.left,
            minWidth: popupMatchWidth ? pos.width : undefined,
            maxWidth: 'min(90vw, 420px)',
            maxHeight: pos.maxH,
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
          }}
        >
          {withSearch && (
            <div className="p-1.5 shrink-0" style={{ borderBottom: '1px solid var(--border-color)' }}>
              <input
                ref={searchRef}
                value={query}
                onChange={e => { setQuery(e.target.value); setActive(0); }}
                onKeyDown={onKeyDown}
                placeholder={searchPlaceholder}
                className="w-full text-sm px-2 py-1 rounded-md outline-none"
                style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                         border: '1px solid var(--border-color)' }}
              />
            </div>
          )}
          <div className="overflow-y-auto py-1">
            {shown.length === 0 && (
              <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)' }}>无匹配项</div>
            )}
            {shown.map((opt, i) => {
              const isSel = opt.value === value;
              const isActive = i === active;
              return (
                <div
                  key={`${opt.value}-${i}`}
                  data-idx={i}
                  role="option"
                  aria-selected={isSel}
                  onMouseEnter={() => !opt.disabled && setActive(i)}
                  onClick={() => commit(i)}
                  className={`px-3 py-1.5 text-sm flex items-center justify-between gap-2 ${opt.disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
                  style={{
                    background: isActive && !opt.disabled ? 'var(--bg-primary)' : 'transparent',
                    color: 'var(--text-primary)',
                  }}
                >
                  <span className="truncate">{opt.label}</span>
                  {isSel && (
                    <svg width="12" height="12" viewBox="0 0 12 12" className="shrink-0" style={{ color: 'var(--accent-primary)' }} aria-hidden>
                      <path d="M2 6.5 L4.8 9.2 L10 3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
              );
            })}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

/**
 * Multi-select dropdown -- filters like status / source / template that "can show several at once" all use it,
 * no longer laid out as a row of mutually exclusive pills (which both takes width and can't express "show two at once").
 *
 * Convention: empty `values` = no filtering (shows allLabel), not "nothing selected".
 */
export function UiMultiSelect({
  values, onChange, options,
  allLabel = '全部',
  disabled = false,
  className,
  style,
  title,
  searchable,
  searchPlaceholder = '搜索…',
}: {
  values: string[];
  onChange: (values: string[]) => void;
  options: UiSelectOption[];
  /** Text shown on the trigger button when nothing is selected */
  allLabel?: string;
  disabled?: boolean;
  className?: string;
  style?: CSSProperties;
  title?: string;
  searchable?: boolean;
  searchPlaceholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  const withSearch = searchable ?? options.length > SEARCHABLE_AUTO_AT;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!withSearch || !q) return options;
    return options.filter(o => optionText(o).toLowerCase().includes(q));
  }, [options, query, withSearch]);

  const { triggerRef, popupRef, pos } = usePopupPosition(open, shown.length);

  const picked = useMemo(() => new Set(values), [values]);
  const firstLabel = options.find(o => picked.has(o.value))?.label;

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!popupRef.current?.contains(t) && !triggerRef.current?.contains(t)) close();
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('resize', close);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (open && withSearch) searchRef.current?.focus();
  }, [open, withSearch]);

  const toggle = (v: string) => {
    const next = new Set(picked);
    if (next.has(v)) next.delete(v); else next.add(v);
    // Keep the order of options, so callers don't get a jumbled list of values
    onChange(options.filter(o => next.has(o.value)).map(o => o.value));
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        title={title}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => { if (!disabled) { setQuery(''); setOpen(o => !o); } }}
        className={`inline-flex items-center justify-between gap-1.5 select-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${className ?? 'px-2.5 py-1.5 text-sm rounded-lg'}`}
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          color: 'var(--text-primary)',
          ...style,
        }}
      >
        <span className="truncate text-left">
          {values.length === 0
            ? <span style={{ color: 'var(--text-secondary)' }}>{allLabel}</span>
            : values.length === 1
              ? firstLabel
              : <>{firstLabel}<span style={{ color: 'var(--text-secondary)' }}> +{values.length - 1}</span></>}
        </span>
        <Caret open={open} />
      </button>
      {open && pos && createPortal(
        <div
          ref={popupRef}
          role="listbox"
          aria-multiselectable
          className={`fixed ${POPUP_Z} rounded-lg shadow-xl flex flex-col`}
          style={{
            top: pos.top, left: pos.left,
            minWidth: Math.max(pos.width, 180),
            maxWidth: 'min(90vw, 420px)',
            maxHeight: pos.maxH,
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
          }}
        >
          {withSearch && (
            <div className="p-1.5 shrink-0" style={{ borderBottom: '1px solid var(--border-color)' }}>
              <input
                ref={searchRef}
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                className="w-full text-sm px-2 py-1 rounded-md outline-none"
                style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                         border: '1px solid var(--border-color)' }}
              />
            </div>
          )}
          <div className="overflow-y-auto py-1">
            {shown.length === 0 && (
              <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)' }}>无匹配项</div>
            )}
            {shown.map((opt) => {
              const isSel = picked.has(opt.value);
              return (
                <div
                  key={opt.value}
                  role="option"
                  aria-selected={isSel}
                  onClick={() => !opt.disabled && toggle(opt.value)}
                  className={`px-3 py-1.5 text-sm flex items-center gap-2 ${opt.disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
                  style={{ color: 'var(--text-primary)' }}
                >
                  <span className="shrink-0 inline-flex items-center justify-center rounded"
                        style={{
                          width: 14, height: 14,
                          background: isSel ? 'var(--accent-primary)' : 'transparent',
                          border: `1px solid ${isSel ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                        }}>
                    {isSel && (
                      <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden>
                        <path d="M2 6.5 L4.8 9.2 L10 3.5" fill="none" stroke="#fff"
                              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </span>
                  <span className="truncate">{opt.label}</span>
                </div>
              );
            })}
          </div>
          {values.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="shrink-0 text-xs py-1.5 text-left px-3"
              style={{ borderTop: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}
            >
              清空({values.length})
            </button>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}
