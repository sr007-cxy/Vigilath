// Shared centered overlay -- three sections (header / scrolling middle / footer), for small selections/confirmations on top of drawers.

import { useEffect, type ReactNode } from 'react';
import { OVERLAY_Z, type OverlayLevel } from './zIndex';
import { overlayDismiss } from '../ui/useOverlayDismiss';

export function Modal({
  open, onClose, level = 2,
  maxWidthClass = 'max-w-2xl',
  header, footer, children,
}: {
  open: boolean;
  onClose: () => void;
  level?: OverlayLevel;
  maxWidthClass?: string;
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
    <div className={`fixed inset-0 ${z.panel} flex items-center justify-center p-4`}
         style={{ background: 'rgba(0,0,0,0.5)' }} {...overlayDismiss(onClose)}>
      <div className={`w-full ${maxWidthClass} max-h-[85vh] rounded-lg flex flex-col`}
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
           onClick={e => e.stopPropagation()}>
        <header className="p-3 border-b flex items-center justify-between gap-2 shrink-0"
                style={{ borderColor: 'var(--border-color)' }}>
          {header}
        </header>
        <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
          {children}
        </div>
        {footer != null && (
          <footer className="p-3 border-t shrink-0"
                  style={{ borderColor: 'var(--border-color)' }}>
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}
