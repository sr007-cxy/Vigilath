// Rendering host for toast.success / error / info, mounted at the App root. API in toast.ts.

import { useEffect, useState } from 'react';
import { TOAST_Z } from '../overlays/zIndex';
import { registerToastHost, type ToastKind } from './toast';

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  leaving?: boolean;
}

let seq = 0;

const ACCENT: Record<ToastKind, string> = {
  success: '#16a34a',
  error: '#dc2626',
  info: '#2563eb',
};

const ICON: Record<ToastKind, string> = {
  success: '✓',
  error: '✕',
  info: 'ℹ',
};

export function ToastHost() {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    const timers = new Map<number, ReturnType<typeof setTimeout>>();
    const dismiss = (id: number) => {
      setItems(list => list.map(t => (t.id === id ? { ...t, leaving: true } : t)));
      timers.set(-id, setTimeout(() => setItems(list => list.filter(t => t.id !== id)), 200));
    };
    registerToastHost((kind, message) => {
      const id = ++seq;
      setItems(list => [...list.slice(-4), { id, kind, message }]);
      timers.set(id, setTimeout(() => dismiss(id), kind === 'error' ? 5000 : 3200));
    });
    return () => {
      registerToastHost(null);
      timers.forEach(clearTimeout);
    };
  }, []);

  if (items.length === 0) return null;
  return (
    <div className={`fixed top-4 right-4 ${TOAST_Z} flex flex-col gap-2 items-end pointer-events-none`}>
      {items.map(t => (
        <div key={t.id}
             onClick={() => setItems(list => list.filter(x => x.id !== t.id))}
             className="pointer-events-auto cursor-pointer max-w-sm flex items-start gap-2.5 px-4 py-3 rounded-lg shadow-lg text-sm transition-all duration-200"
             style={{
               background: 'var(--bg-card)',
               border: '1px solid var(--border-color)',
               borderLeft: `3px solid ${ACCENT[t.kind]}`,
               color: 'var(--text-primary)',
               opacity: t.leaving ? 0 : 1,
               transform: t.leaving ? 'translateX(12px)' : 'none',
             }}>
          <span className="shrink-0 w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold text-white mt-0.5"
                style={{ background: ACCENT[t.kind] }}>
            {ICON[t.kind]}
          </span>
          <span className="whitespace-pre-wrap break-words leading-snug">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
