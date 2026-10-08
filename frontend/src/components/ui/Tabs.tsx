// Pill-style filter tabs (optionally with counts). Several workbench list pages each wrote their own roleTab / statusTab,
// with different selected colors and radii. Consolidated into one here.
//
// Keyboard accessible: a group of tabs moves between options with ←→, following the WAI-ARIA tablist convention --
// the old hand-written button groups could only be tabbed through one by one.
//
// Usage:
//   <PillTabs value={f} onChange={setF} items={[
//     { value: 'all', label: 'All', count: 12 },
//     { value: 'active', label: 'Active', count: 9 },
//   ]} />
import type { ReactNode } from 'react';

export interface PillTabItem<T extends string = string> {
  value: T;
  label: ReactNode;
  count?: number;
}

export function PillTabs<T extends string>({
  value, onChange, items, className, right,
}: {
  value: T;
  onChange: (v: T) => void;
  items: readonly PillTabItem<T>[];
  className?: string;
  /** Extra controls on the right (status dropdown, clear, etc.) */
  right?: ReactNode;
}) {
  const move = (delta: number) => {
    const i = items.findIndex((x) => x.value === value);
    const next = items[(i + delta + items.length) % items.length];
    if (next) onChange(next.value);
  };

  return (
    <div className={`flex items-center gap-1.5 flex-wrap ${className ?? ''}`} role="tablist">
      {items.map((item) => {
        const on = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(item.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight') { e.preventDefault(); move(1); }
              if (e.key === 'ArrowLeft') { e.preventDefault(); move(-1); }
            }}
            className="text-xs px-2.5 py-1 rounded-md transition-colors"
            style={on
              ? { background: 'var(--accent-primary)', color: 'var(--accent-contrast, #fff)', border: '1px solid transparent' }
              : { background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
          >
            {item.label}
            {item.count != null && <span className="ml-1 tabular-nums opacity-70">{item.count}</span>}
          </button>
        );
      })}
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}
