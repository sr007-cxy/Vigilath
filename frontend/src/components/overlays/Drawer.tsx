// Shared right-side drawer -- the unified container for detail views site-wide (following TemplateDrawer's healthy pattern).
// Three sections: header fixed at top / children scroll independently in the middle / footer naturally at the bottom (last flex-col section,
// not relying on sticky, so it stays at the bottom even with short content). Closed by ESC and clicking the overlay.

import { useEffect, type ReactNode } from 'react';
import { OVERLAY_Z, type OverlayLevel } from './zIndex';
import { overlayDismiss } from '../ui/useOverlayDismiss';

export function Drawer({
  open, onClose, level = 1,
  widthClass = 'w-full md:w-[65%] max-w-4xl',
  header, footer, children,
}: {
  open: boolean;
  onClose: () => void;
  level?: OverlayLevel;
  widthClass?: string;
  header: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  const z = OVERLAY_Z[level];

  return (
    <>
      <div className={`fixed inset-0 ${z.mask}`}
           style={{ background: 'rgba(0,0,0,0.5)' }} {...overlayDismiss(onClose)} />
      <aside
        className={`fixed top-0 right-0 bottom-0 ${z.panel} ${widthClass} flex flex-col`}
        style={{
          background: 'var(--bg-card)',
          borderLeft: '1px solid var(--border-color)',
          boxShadow: '-8px 0 24px rgba(0,0,0,0.18)',
        }}
        onClick={e => e.stopPropagation()}
      >
        <header className="p-4 flex items-start justify-between gap-2 border-b shrink-0"
                style={{ borderColor: 'var(--border-color)' }}>
          {header}
        </header>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {children}
        </div>
        {footer != null && (
          <footer className="border-t shrink-0"
                  style={{ borderColor: 'var(--border-color)', background: 'var(--bg-card)' }}>
            {footer}
          </footer>
        )}
      </aside>
    </>
  );
}
