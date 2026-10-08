// Overlay layer tokens -- consolidating the site's de facto convention: z-40 backdrop / z-50 level 1 / z-60 level 2 / z-70 level 3.
// New drawers/overlays always take their layer from here; no more hand-written special cases like z-[100].

export const OVERLAY_Z = {
  1: { mask: 'z-40', panel: 'z-50' },
  2: { mask: 'z-50', panel: 'z-[60]' },
  3: { mask: 'z-[60]', panel: 'z-[70]' },
} as const;

export type OverlayLevel = keyof typeof OVERLAY_Z;

// Global confirm/alert dialogs (ui/dialogs) and toasts must sit above all business overlays.
export const DIALOG_Z = { mask: 'z-[80]', panel: 'z-[90]' } as const;
export const TOAST_Z = 'z-[100]';
// Dropdown popovers (ui/Select) may appear inside overlays at any level, so they share the dialog level.
export const POPUP_Z = 'z-[90]';
