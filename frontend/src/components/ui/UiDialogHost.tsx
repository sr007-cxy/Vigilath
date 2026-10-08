// Rendering host for uiConfirm / uiAlert / uiPrompt, mounted at the App root. API in dialogs.ts.

import { useEffect, useRef, useState } from 'react';
import i18next from 'i18next';
import { DIALOG_Z } from '../overlays/zIndex';
import { registerDialogHost, type DialogRequest } from './dialogs';
import { useOverlayDismiss } from './useOverlayDismiss';

export function UiDialogHost() {
  const [queue, setQueue] = useState<DialogRequest[]>([]);
  const current = queue[0] ?? null;

  useEffect(() => {
    registerDialogHost(req => setQueue(q => [...q, req]));
    return () => registerDialogHost(null);
  }, []);

  const close = (value: boolean | string | null) => {
    current?.resolve(value);
    setQueue(q => q.slice(1));
  };

  if (!current) return null;
  return <DialogPanel key={queue.length} req={current} onClose={close} />;
}

function DialogPanel({ req, onClose }: { req: DialogRequest; onClose: (v: boolean | string | null) => void }) {
  const [input, setInput] = useState(req.defaultValue ?? '');
  const okRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);
  const zh = (i18next.language || '').startsWith('zh');

  const okText = req.okText ?? (zh ? '确定' : 'OK');
  const cancelText = req.cancelText ?? (zh ? '取消' : 'Cancel');
  const cancelValue = req.kind === 'confirm' ? false : req.kind === 'prompt' ? null : true;

  useEffect(() => {
    (req.kind === 'prompt' ? inputRef.current : okRef.current)?.focus();
    inputRef.current?.select();
  }, [req.kind]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(cancelValue); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  });

  const ok = () => onClose(req.kind === 'prompt' ? input : true);
  // Overlay closing checks both mousedown/mouseup, to avoid accidentally closing when selecting text in the uiPrompt input
  // and releasing after dragging outside the box
  const dismiss = useOverlayDismiss(() => onClose(cancelValue));

  return (
    <div className={`fixed inset-0 ${DIALOG_Z.panel} flex items-center justify-center p-4`}
         style={{ background: 'rgba(0,0,0,0.5)' }}
         {...dismiss}>
      <div className="w-full max-w-sm rounded-xl shadow-2xl overflow-hidden"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
           onClick={e => e.stopPropagation()}>
        <div className="px-5 pt-5 pb-4">
          {req.title != null && (
            <div className="text-base font-semibold mb-1.5" style={{ color: 'var(--text-primary)' }}>
              {req.title}
            </div>
          )}
          <div className="text-sm leading-relaxed whitespace-pre-wrap break-words"
               style={{ color: req.title != null ? 'var(--text-secondary)' : 'var(--text-primary)' }}>
            {req.message}
          </div>
          {req.kind === 'prompt' && (
            req.multiline ? (
              <textarea
                ref={inputRef as React.RefObject<HTMLTextAreaElement>}
                value={input} onChange={e => setInput(e.target.value)}
                placeholder={req.placeholder} rows={4}
                className="mt-3 w-full px-3 py-2 text-sm rounded-lg outline-none focus:ring-2"
                style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              />
            ) : (
              <input
                ref={inputRef as React.RefObject<HTMLInputElement>}
                value={input} onChange={e => setInput(e.target.value)}
                placeholder={req.placeholder}
                onKeyDown={e => { if (e.key === 'Enter') ok(); }}
                className="mt-3 w-full px-3 py-2 text-sm rounded-lg outline-none focus:ring-2"
                style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              />
            )
          )}
        </div>
        <div className="px-5 pb-4 flex justify-end gap-2">
          {req.kind !== 'alert' && (
            <button
              onClick={() => onClose(cancelValue)}
              className="px-4 py-1.5 text-sm rounded-lg transition-opacity hover:opacity-80"
              style={{ border: '1px solid var(--border-color)', color: 'var(--text-primary)', background: 'transparent' }}>
              {cancelText}
            </button>
          )}
          <button
            ref={okRef} onClick={ok}
            className="px-4 py-1.5 text-sm font-medium rounded-lg transition-opacity hover:opacity-85"
            style={req.danger
              ? { background: '#dc2626', color: '#ffffff' }
              : { background: 'var(--accent-primary)', color: 'var(--accent-contrast, #ffffff)' }}>
            {okText}
          </button>
        </div>
      </div>
    </div>
  );
}
