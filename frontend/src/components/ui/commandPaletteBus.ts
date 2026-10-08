// Imperative open/close for the command palette. Rendering lives in CommandPalette.tsx; only the call entry is here --
// the same split as toast.ts / ToastHost.tsx, satisfying react-refresh/only-export-components.
//
// With it, the "Search pages ⌘K" button on the sidebar can open the palette:
// a shortcut needs a visible entry, or only people who already know will use it.
let opener: (() => void) | null = null;

/** Registered by CommandPalette on mount; business code must not call it */
export function registerCommandPalette(fn: (() => void) | null) {
  opener = fn;
}

export function openCommandPalette() {
  opener?.();
}
