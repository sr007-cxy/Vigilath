// Unified sizes for filter controls -- shared by dropdowns, search boxes and segmented controls, keeping heights aligned in one row.
// A separate file for react-refresh: FilterBar.tsx only exports components, so constants live here.

import type { CSSProperties } from 'react';

export const FILTER_CONTROL_CLASS = 'h-8 px-2.5 text-xs rounded-md';

export const FILTER_CONTROL_STYLE: CSSProperties = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
};
