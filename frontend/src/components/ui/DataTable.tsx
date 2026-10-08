// Table shell and empty/loading states -- the unified base for admin tables site-wide.
// Taken from two mature approaches, shadcn/ui's Table + Skeleton and GitHub Primer's Blankslate,
// mapped onto our own CSS variables.
//
// Three real problems it solves:
//   1. On narrow screens tables pushed the whole page into horizontal scrolling -- the **table itself** must scroll, not body.
//      This was missing on pages like AdminUsers / AdminAccounts / AdminGateway.
//   2. Once the header scrolls out of view the column names are lost, and long lists rely on memory -- sticky thead fixes it.
//   3. Loading used a spinner and empty data left a white blank -- replaced with skeleton rows + an empty state that says something.

import type { ReactNode } from 'react';

/**
 * Table container: rounded outline + its own horizontal scrolling + height-limited vertical scrolling (with sticky thead).
 * Passing `maxHeight` false means no height limit (for short tables, avoiding an unnecessary inner scrollbar).
 */
export function TableShell({
  children, maxHeight = 'clamp(420px, 70vh, 760px)', className,
}: {
  children: ReactNode;
  maxHeight?: string | false;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl overflow-auto ${className ?? ''}`}
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        ...(maxHeight ? { maxHeight } : null),
      }}
    >
      {children}
    </div>
  );
}

/**
 * Sticky header. The thead background must be an opaque solid color, otherwise rows show through under the text while scrolling.
 * The bottom border is drawn with an inset box-shadow -- border-bottom on sticky elements in some browsers
 * scrolls along with the content, while a shadow doesn't.
 */
export function StickyHead({ children }: { children: ReactNode }) {
  return (
    <thead className="sticky top-0 z-10" style={{ background: 'var(--bg-card)' }}>
      <tr className="text-muted" style={{ boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
        {children}
      </tr>
    </thead>
  );
}

/** Loading skeleton rows: say "a table will appear here" better than a spinner, and avoid jumps from collapsed height. */
export function SkeletonRows({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <tbody>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} style={{ borderTop: '1px solid var(--border-color)' }}>
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-3 py-2.5">
              <div
                className="h-3 rounded animate-pulse"
                style={{
                  // Use a semi-transparent foreground color over the card background, not --bg-tertiary --
                  // in the dark theme tertiary (#1a1a25) and card (#15151f) differ by only 5 levels,
                  // so the skeleton bars are nearly invisible, effectively back to a blank.
                  background: 'color-mix(in srgb, var(--text-primary) 12%, transparent)',
                  // Stagger the widths a bit; neat equal-width bars look like broken rendering
                  width: `${[70, 45, 85, 55, 60, 40][(r + c) % 6]}%`,
                }}
              />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

/**
 * Empty state. Rule: the title says "what the situation is now", the hint says "what can be done next",
 * and give a button when an action is possible -- writing only "No data" leaves people hanging.
 */
export function EmptyState({
  title, hint, action, icon = 'inbox', className,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  icon?: 'inbox' | 'search' | 'alert';
  className?: string;
}) {
  const paths: Record<string, string> = {
    inbox: 'M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0l-2.5 5A2 2 0 0115.7 20H8.3a2 2 0 01-1.8-1.1L4 13m16 0h-5l-1 2h-4l-1-2H4',
    search: 'M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z',
    alert: 'M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z',
  };
  return (
    <div className={`flex flex-col items-center justify-center text-center gap-2 px-6 py-14 ${className ?? ''}`}>
      <svg className="w-8 h-8 mb-1" style={{ color: 'var(--text-muted)', opacity: 0.5 }}
           fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={paths[icon]} />
      </svg>
      <p className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>{title}</p>
      {hint && <p className="text-xs max-w-sm" style={{ color: 'var(--text-muted)' }}>{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Empty state inside the table body: spans the whole row, so an empty table isn't just a header line. */
export function EmptyRow({
  colSpan, title, hint, action,
}: { colSpan: number; title: string; hint?: string; action?: ReactNode }) {
  return (
    <tbody>
      <tr>
        <td colSpan={colSpan}>
          <EmptyState title={title} hint={hint} action={action} />
        </td>
      </tr>
    </tbody>
  );
}
