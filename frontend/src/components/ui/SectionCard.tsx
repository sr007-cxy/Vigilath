// Content card with a title -- shared by the plan / source analysis / why-it's-scheduled-this-way pages.
// ExecutionPlan.tsx and SourceInsightsPanel.tsx each had a byte-identical private copy;
// consolidated here when a third page was added.

import { useState, type ReactNode } from 'react';

export function SectionCard({
  title, hint, right, collapsible = false, defaultCollapsed = false, children,
}: {
  title: string;
  /** One-line metric definition note under the title (optional) */
  hint?: ReactNode;
  /** Action slot on the right of the title row (optional) */
  right?: ReactNode;
  /**
   * The title can be clicked to expand/collapse. For blocks that are "a glance is enough, no need to always show" -- like overall project status,
   * topic logs, generalization logs: archives only flipped through when verifying, which would push the publish plan below the fold if always shown.
   */
  collapsible?: boolean;
  /** Collapsed by default (only meaningful when collapsible) */
  defaultCollapsed?: boolean;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(collapsible && defaultCollapsed);
  const folded = collapsible && collapsed;

  // In collapsed state mb-3 leaves a gap under the title that looks like content failed to load
  const headClass = `flex items-start gap-2${folded ? '' : ' mb-3'}`;

  return (
    <section className="rounded-md p-4"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className={headClass}>
        {collapsible ? (
          // The whole title is the toggle hot zone, not just the little triangle
          <button type="button" onClick={() => setCollapsed(c => !c)}
                  className="flex items-center gap-1.5 text-sm font-semibold text-primary
                             hover:opacity-80"
                  aria-expanded={!collapsed}
                  title={collapsed ? '展开' : '收起'}>
            <span className="text-[10px] text-muted w-2.5 inline-block">
              {collapsed ? '▶' : '▼'}
            </span>
            {title}
          </button>
        ) : (
          <h2 className="text-sm font-semibold text-primary">{title}</h2>
        )}
        {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
      </div>
      {/* hidden rather than not rendering: inner scroll position and expanded state aren't lost by collapsing */}
      <div hidden={folded}>
        {hint && <div className="text-[11px] text-muted -mt-2 mb-3">{hint}</div>}
        {children}
      </div>
    </section>
  );
}
