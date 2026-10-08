// One-click full diagnosis report — single source of colors.
//
// 2026-07-31 — SVG stroke / fill / stop-color are presentation attributes,
// and var() is **not resolved** in Firefox and a batch of older Chromium engines, voiding the whole declaration -> radar strokes and
// gradient fills drop out and look gray (adding variables in index.css only helps inline styles).
// So chart colors are resolved to literal color values in JS before being passed in, kept consistent with .diagnosis-report in index.css.
// When changing colors, both places must be synced.

import { useEffect, useState } from 'react';

export const DG_HEX_LIGHT = ['#4f46e5', '#0d9488', '#ea580c', '#059669', '#7c3aed', '#db2777'];
export const DG_HEX_DARK  = ['#818cf8', '#2dd4bf', '#fb923c', '#34d399', '#a78bfa', '#f472b6'];
export const DG_TRACK_LIGHT = 'rgba(15, 23, 42, 0.09)';
export const DG_TRACK_DARK  = 'rgba(148, 163, 184, 0.18)';

// Order strictly matches DIMENSION_DEFS in backend full_diagnosis_generator.py -- adding a backend dimension must be synced here
export const DIM_ORDER = [
  'ai_visibility', 'infrastructure', 'authority', 'competitive', 'sentiment',
] as const;

// **Explicit** dimension -> color slot mapping.
// It used to be DG_HEX[DIM_ORDER.indexOf(key)], where an unregistered dimension silently fell to hex[0] with no error
// (the fifth dimension, sentiment, hit exactly this pitfall). Changed to an explicit table + dev-time warning.
// Sentiment uses dg-6 magenta instead of dg-5 purple: purple #7c3aed and dg-1 indigo #4f46e5 are too close in hue on the small radar markers.
const DIM_HEX_INDEX: Record<string, number> = {
  ai_visibility: 0,
  infrastructure: 1,
  authority: 2,
  competitive: 3,
  sentiment: 5,
};

// Fixed dimension colors (as CSS variables, for DOM inline styles; SVG should use useDgPalette().dim())
export const DIM_COLOR: Record<string, string> = {
  ai_visibility: 'var(--dg-1)',
  infrastructure: 'var(--dg-2)',
  authority: 'var(--dg-3)',
  competitive: 'var(--dg-4)',
  sentiment: 'var(--dg-6)',
};

// Categorical palette: engine bars / query type bars take colors in order
export const SERIES_COLORS = [
  'var(--dg-1)', 'var(--dg-2)', 'var(--dg-3)',
  'var(--dg-4)', 'var(--dg-5)', 'var(--dg-6)',
];

// Severity: text colors use semantic tokens (med used to be #eab308, only ~1.9:1 on light backgrounds); backgrounds stay pale
export const SEVERITY_STYLE: Record<string, { c: string; bg: string }> = {
  high: { c: 'var(--danger-fg)',  bg: 'rgba(239,68,68,0.10)' },
  med:  { c: 'var(--warn-fg)',    bg: 'rgba(234,179,8,0.10)' },
  low:  { c: 'var(--success-fg)', bg: 'rgba(16,185,129,0.10)' },
};

/** The LLM sometimes writes 'mid' and sometimes 'med'; normalize to SEVERITY_STYLE keys */
export function severityStyle(raw?: string) {
  const key = raw === 'mid' ? 'med' : (raw || 'low');
  return SEVERITY_STYLE[key] || SEVERITY_STYLE.low;
}

/** Tier colors for total score/grade: ≥70 green / ≥55 orange / otherwise red */
export function gradeColorOf(total: number): string {
  return total >= 70 ? '#059669' : total >= 55 ? '#ea580c' : '#dc2626';
}

export function useDgPalette() {
  const readDark = () =>
    typeof document !== 'undefined' && document.body.getAttribute('data-theme') === 'dark';
  // Text colors follow the theme, and likewise can't be passed into SVG via var(); use computed values
  const readText = () => {
    if (typeof document === 'undefined') return '#0f172a';
    const v = getComputedStyle(document.body).getPropertyValue('--text-primary').trim();
    return v || '#0f172a';
  };
  const [dark, setDark] = useState(readDark);
  const [textPrimary, setTextPrimary] = useState(readText);
  useEffect(() => {
    const sync = () => { setDark(readDark()); setTextPrimary(readText()); };
    sync();   // On the first frame, data-theme may not be on body yet
    const ob = new MutationObserver(sync);
    ob.observe(document.body, { attributes: true, attributeFilter: ['data-theme'] });
    return () => ob.disconnect();
  }, []);
  const hex = dark ? DG_HEX_DARK : DG_HEX_LIGHT;
  return {
    hex,
    dark,
    textPrimary,
    track: dark ? DG_TRACK_DARK : DG_TRACK_LIGHT,
    dim: (key: string) => {
      const i = DIM_HEX_INDEX[key];
      if (i === undefined) {
        if (import.meta.env.DEV) {
          console.warn('[diagnosis] Score dimension without a registered color:', key, '— please add it to DIM_HEX_INDEX in palette.ts');
        }
        return hex[hex.length - 1];   // Fallback to the last color, visibly "wrong", so it's noticed during development
      }
      return hex[i];
    },
    series: (i: number) => hex[i % hex.length],
  };
}
