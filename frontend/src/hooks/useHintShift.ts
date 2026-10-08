// Horizontal avoidance for hint bubbles -- the bubble is 16rem wide (w-64), centered below the icon;
// when the icon is near the viewport's left/right edge it shifts as needed, so the bubble doesn't stick out and cause horizontal page scrolling.
//
// Used to live in pages/BrandGrowth/charts.tsx, and the report center (Reports/shell.tsx,
// Reports/components.tsx) has always imported it. Moved permanently to hooks/ after the brand growth module was retired.

import { useState } from 'react';
import type React from 'react';

const HINT_W = 256;
const HINT_MARGIN = 8;

export function useHintShift(): [number, (e: React.SyntheticEvent<HTMLElement>) => void] {
  const [shift, setShift] = useState(0);
  const onShow = (e: React.SyntheticEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const center = r.left + r.width / 2;
    const vw = document.documentElement.clientWidth;
    let s = 0;
    const overRight = center + HINT_W / 2 - (vw - HINT_MARGIN);
    if (overRight > 0) s = -overRight;
    const overLeft = HINT_MARGIN - (center - HINT_W / 2);
    if (overLeft > 0) s = overLeft;
    setShift(s);
  };
  return [shift, onShow];
}
