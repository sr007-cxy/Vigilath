// Correct detection of "click outside to close" on dialog overlays.
//
// Writing onClick={close} directly on the overlay misfires in two directions:
//  1. Press the mouse in an input inside the dialog to select text -> drag onto the overlay and release. The browser dispatches click to
//     the **common ancestor** of mousedown / mouseup (i.e. the overlay), and the inner stopPropagation
//     can't stop it -> the dialog vanishes halfway through copying.
//  2. Conversely, press on the overlay and release after dragging into the dialog; click's target is also the overlay -> also closes by mistake.
//
// So the decision must rest on the two real positions of mousedown / mouseup: only when both "press" and "release"
// happen on the overlay itself did the user really click outside the dialog.
//
// Two entry points, **the same detection**:
//   useOverlayDismiss(fn)  hook version, called at component top level (all existing callers use this)
//   overlayDismiss(fn)     inline version, `{...overlayDismiss(onClose)}` written directly in JSX
// The inline version records "was the press on the overlay" on the element itself (WeakSet), so no useRef is needed,
// and it's free of hook call-position restrictions -- a dozen-plus hand-written dialogs needn't restructure for this one flag.
import { useRef } from 'react';

/** Elements on which the press landed on the overlay itself. WeakSet: cleared automatically when the element unmounts, no leaks. */
const _downOnOverlay = new WeakSet<EventTarget>();

/**
 * Inline overlay-close detection. Passing undefined / null = clicking the overlay can't close right now (e.g. while submitting).
 *
 *     <div className="fixed inset-0 ..." {...overlayDismiss(onClose)}>
 *       <div onClick={e => e.stopPropagation()}> ...panel... </div>
 *     </div>
 */
export function overlayDismiss(onDismiss?: (() => void) | null) {
  if (!onDismiss) return {};
  return {
    onMouseDown: (e: React.MouseEvent) => {
      // Primary button only: context menus / middle-click paste shouldn't close the window
      if (e.button === 0 && e.target === e.currentTarget) _downOnOverlay.add(e.currentTarget);
      else _downOnOverlay.delete(e.currentTarget);
    },
    onMouseUp: (e: React.MouseEvent) => {
      const both = _downOnOverlay.has(e.currentTarget) && e.target === e.currentTarget;
      _downOnOverlay.delete(e.currentTarget);
      if (both) onDismiss();
    },
  };
}

export function useOverlayDismiss(onDismiss: () => void) {
  const downOnOverlay = useRef(false);

  return {
    onMouseDown: (e: React.MouseEvent) => {
      downOnOverlay.current = e.target === e.currentTarget;
    },
    onMouseUp: (e: React.MouseEvent) => {
      const bothOnOverlay = downOnOverlay.current && e.target === e.currentTarget;
      downOnOverlay.current = false;
      if (bothOnOverlay) onDismiss();
    },
  };
}
