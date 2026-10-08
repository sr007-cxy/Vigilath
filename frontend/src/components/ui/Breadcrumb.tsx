// Breadcrumbs for in-page drill-down -- not routed, navigating back via callbacks (same-page hierarchies like calendar -> a specific day).
// Route-level breadcrumbs are built by each page with <Link>, not this.
//
// The last item is the current position: not clickable, emphasized; each earlier item with onClick is clickable to go back.

import type { ReactNode } from 'react';

export interface Crumb {
  label: string;
  /** Clickable only with onClick; the current position (last item) doesn't need it */
  onClick?: () => void;
}

export function Breadcrumb({ items, right }: { items: Crumb[]; right?: ReactNode }) {
  if (items.length === 0) return null;
  const back = [...items].reverse().find(c => c.onClick)?.onClick;
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {back && (
        <button type="button" onClick={back}
                className="w-6 h-6 rounded-md flex items-center justify-center shrink-0"
                style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                         border: '1px solid var(--border-color)' }}
                title="返回上一层">
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
          </svg>
        </button>
      )}
      <nav className="text-xs flex items-center gap-1 flex-wrap min-w-0">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <span key={i} className="flex items-center gap-1 min-w-0">
              {c.onClick && !last ? (
                <button type="button" onClick={c.onClick}
                        className="hover:underline truncate"
                        style={{ color: 'var(--accent-primary)' }}>
                  {c.label}
                </button>
              ) : (
                <span className="truncate font-semibold"
                      style={{ color: 'var(--text-primary)' }}>{c.label}</span>
              )}
              {!last && <span className="text-muted">›</span>}
            </span>
          );
        })}
      </nav>
      {right && <div className="flex-1 flex items-center justify-end gap-2">{right}</div>}
    </div>
  );
}
