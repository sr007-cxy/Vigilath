// Site-wide unified confirm/alert/prompt dialog API, replacing native window.confirm / alert / prompt.
// Usage (any module, including non-component code):
//   if (!(await uiConfirm('Delete this?'))) return;
//   await uiAlert('Operation failed', { title: 'Error' });
//   const name = await uiPrompt('Enter a name', { defaultValue: 'foo' });  // returns null on cancel
// Rendering is handled by <UiDialogHost /> (mounted at the App root); the component lives in UiDialogHost.tsx
// to satisfy react-refresh/only-export-components.

import type { ReactNode } from 'react';

export interface DialogOptions {
  title?: ReactNode;
  okText?: string;
  cancelText?: string;
  /** Dangerous operations (delete etc.): red confirm button */
  danger?: boolean;
  /** prompt only: initial input value */
  defaultValue?: string;
  /** prompt only: input placeholder */
  placeholder?: string;
  /** prompt only: multi-line input */
  multiline?: boolean;
}

export type DialogKind = 'confirm' | 'alert' | 'prompt';

export interface DialogRequest extends DialogOptions {
  kind: DialogKind;
  message: ReactNode;
  resolve: (value: boolean | string | null) => void;
}

let enqueue: ((req: DialogRequest) => void) | null = null;

/** Registered by UiDialogHost on mount as the rendering channel; business code must not call it */
export function registerDialogHost(fn: ((req: DialogRequest) => void) | null) {
  enqueue = fn;
}

function open(req: DialogRequest) {
  if (enqueue) {
    enqueue(req);
    return;
  }
  // When the Host isn't mounted, fall back to native, without blocking the caller
  if (req.kind === 'confirm') req.resolve(window.confirm(String(req.message)));
  else if (req.kind === 'alert') { window.alert(String(req.message)); req.resolve(true); }
  else req.resolve(window.prompt(String(req.message), req.defaultValue ?? '') ?? null);
}

export function uiConfirm(message: ReactNode, opts: DialogOptions = {}): Promise<boolean> {
  return new Promise(resolve => open({ kind: 'confirm', message, ...opts, resolve: v => resolve(Boolean(v)) }));
}

export function uiAlert(message: ReactNode, opts: DialogOptions = {}): Promise<void> {
  return new Promise(resolve => open({ kind: 'alert', message, ...opts, resolve: () => resolve() }));
}

export function uiPrompt(message: ReactNode, opts: DialogOptions = {}): Promise<string | null> {
  return new Promise(resolve => open({ kind: 'prompt', message, ...opts, resolve: v => resolve(typeof v === 'string' ? v : null) }));
}
