// Site-wide unified lightweight toast notification API, replacing alert()-style operation result messages.
// Usage: toast.success('Saved') / toast.error('Delete failed') / toast.info('...')
// Rendering is handled by <ToastHost /> (mounted at the App root); the component lives in ToastHost.tsx
// to satisfy react-refresh/only-export-components.

export type ToastKind = 'success' | 'error' | 'info';

let push: ((kind: ToastKind, message: string) => void) | null = null;

/** Registered by ToastHost on mount as the rendering channel; business code must not call it */
export function registerToastHost(fn: ((kind: ToastKind, message: string) => void) | null) {
  push = fn;
}

function emit(kind: ToastKind, message: string) {
  if (push) push(kind, message);
  else window.alert(message); // fallback when the Host isn't mounted
}

export const toast = {
  success: (message: string) => emit('success', message),
  error: (message: string) => emit('error', message),
  info: (message: string) => emit('info', message),
};
