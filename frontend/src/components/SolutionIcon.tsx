// Icons shared across the "Solutions" system. The mega-menu and intro pages share the same set of paths,
// keeping the leaf icons in the menu consistent with the landing page hero icons.

// Icon for each solution (leaf), key = the icon field in solutions.ts.
const SOLUTION_ICON_PATHS: Record<string, string> = {
  globe: 'M21 12a9 9 0 11-18 0 9 9 0 0118 0zM3.6 9h16.8M3.6 15h16.8M12 3a15 15 0 010 18M12 3a15 15 0 000 18',
  building: 'M3 21h18M5 21V5a2 2 0 012-2h10a2 2 0 012 2v16M9 7h1m-1 4h1m-1 4h1m4-8h1m-1 4h1m-1 4h1',
  handshake: 'M7 11l3-3 4 4 3-3M3 12l4 4a2 2 0 002.8 0l.2-.2M21 12l-4 4a2 2 0 01-2.8 0',
  academic: 'M12 14l9-5-9-5-9 5 9 5zm0 0v6m0-6l6.16-3.42M6 12v4c0 1 2.7 2.5 6 2.5s6-1.5 6-2.5v-4',
  search: 'M21 21l-5.2-5.2M17 11a6 6 0 11-12 0 6 6 0 0112 0z',
  clipboard: 'M9 5h6a1 1 0 011 1v0a1 1 0 01-1 1H9a1 1 0 01-1-1v0a1 1 0 011-1zM9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 12l1.5 1.5L13 11m-4 5l1.5 1.5L13 15',
  edit: 'M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.5-9.5a2.121 2.121 0 013 3L12 19l-4 1 1-4 8.5-8.5z',
  radar: 'M12 12l5-3M12 3a9 9 0 109 9M12 3a9 9 0 019 9M12 7a5 5 0 105 5',
  pulse: 'M3 12h4l2 6 4-12 2 6h6',
  scale: 'M12 3v18M5 7h14M7 7l-3 6a3 3 0 006 0L7 7zm10 0l-3 6a3 3 0 006 0l-3-6z',
  link: 'M13.8 10.2a4 4 0 010 5.6l-2.5 2.5a4 4 0 01-5.6-5.6l1.3-1.3M10.2 13.8a4 4 0 010-5.6l2.5-2.5a4 4 0 015.6 5.6l-1.3 1.3',
  funnel: 'M3 4h18l-7 8v7l-4 2v-9L3 4z',
  loop: 'M4 12a8 8 0 0114-5.3L20 8M20 12a8 8 0 01-14 5.3L4 16m16-8V4m0 4h-4M4 16v4m0-4h4',
  trending: 'M3 17l6-6 4 4 8-8m0 0h-5m5 0v5',
  shield: 'M12 3l8 3v6c0 5-3.4 7.7-8 9-4.6-1.3-8-4-8-9V6l8-3zm-3 9l2 2 4-4',
  alert: 'M12 9v4m0 4h.01M10.29 3.86l-8.4 14.5A2 2 0 003.61 21h16.78a2 2 0 001.72-2.64l-8.4-14.5a2 2 0 00-3.42 0z',
  lock: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z',
  // System architecture -- layered stack
  architecture: 'M12 3l9 5-9 5-9-5 9-5zm9 9l-9 5-9-5m18 4l-9 5-9-5',
};

// Icons for each of the three column headers, key = SOLUTION_COLUMNS id.
const COLUMN_ICON_PATHS: Record<string, string> = {
  // Audience -- people
  audience:
    'M17 20h5v-1.5a3.5 3.5 0 00-5.1-3.1M17 20H7m10 0v-1.5c0-.7-.15-1.36-.42-1.96M7 20H2v-1.5a3.5 3.5 0 015.1-3.1M7 20v-1.5c0-.7.15-1.36.42-1.96m0 0a5 5 0 019.16 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3.5a2 2 0 11-4 0 2 2 0 014 0zM7 10.5a2 2 0 11-4 0 2 2 0 014 0z',
  // Service scope -- module grid
  scope:
    'M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z',
  // Solutions -- spark of inspiration
  solution:
    'M5 3v4M3 5h4M6 17v4m-2-2h4m6-15l2.4 6.6L21 12l-5.6 2.4L13 21l-2.4-6.6L5 12l5.6-2.4L13 4z',
};

type Props = {
  /** Leaf icon key (SOLUTION_ICON_PATHS), or a column icon (pass column-*, see below). */
  name: string;
  className?: string;
  strokeWidth?: number;
};

/** Render a solution icon. name matches SOLUTION_ICON_PATHS; the `col:audience` form takes a column icon. */
export function SolutionIcon({ name, className, strokeWidth = 1.8 }: Props) {
  const colMatch = name.startsWith('col:') ? name.slice(4) : null;
  const d = colMatch
    ? COLUMN_ICON_PATHS[colMatch] ?? COLUMN_ICON_PATHS.solution
    : SOLUTION_ICON_PATHS[name] ?? SOLUTION_ICON_PATHS.globe;
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={strokeWidth}>
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}
