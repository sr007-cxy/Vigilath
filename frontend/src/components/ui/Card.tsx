// Card container. The inline style set `background: var(--bg-card) + border` was hand-written 102 times site-wide,
// consolidated here. SectionCard (content card with a title) stays as is; it's one usage of Card.
//
// Usage:
//   <Card>…</Card>
//   <Card padded={false}><table>…</table></Card>
import type { ReactNode } from 'react';

export function Card({
  children, padded = true, className, style, as: Tag = 'div',
}: {
  children: ReactNode;
  /** Padding by default; pass false when holding a table so the table sits flush */
  padded?: boolean;
  className?: string;
  style?: React.CSSProperties;
  as?: 'div' | 'section' | 'aside';
}) {
  return (
    <Tag
      className={`rounded-xl ${padded ? 'p-4' : ''} ${className ?? ''}`}
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', ...style }}
    >
      {children}
    </Tag>
  );
}

/**
 * Page header: title + a one-line metric definition note + an action slot on the right.
 * A dozen-plus workbench pages each hand-wrote h1/h2 + description text, with different font sizes and spacing.
 */
/**
 * Page header for the workbench / admin workbench -- the **only** way to write it; pages must not hand-write h1 anymore.
 *
 * Before unification there were 5 title styles under the same sidebar: most pages text-xl font-semibold (the baseline, per "Optimization tasks"),
 * the cockpit and customer accounts used .wb-title (clamp 1.55~2rem, weight 680, negative letter spacing, one size larger),
 * model usage text-base, placement text-lg font-bold, sentiment monitoring text-2xl, one-click full diagnosis
 * text-2xl sm:text-3xl with a gradient. Another 5 pages had no h1 at all.
 *
 * This component used to be text-base -- one size below the baseline, and used by only 2 pages. This time it's calibrated to the baseline and rolled out,
 * rather than creating yet another component.
 */
export function PageHeader({
  title, hint, actions, className,
}: {
  title: ReactNode;
  /** One sentence on what this page is for. Optional. */
  hint?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-wrap items-end justify-between gap-3 mb-4 ${className ?? ''}`}>
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-primary truncate">{title}</h1>
        {hint && (
          <p className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{hint}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 flex-wrap shrink-0">{actions}</div>}
    </div>
  );
}
