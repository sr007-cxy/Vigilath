// Status tag. The site had 57 places with `px-2 py-0.5 rounded-full` + a chunk of inline colors,
// 70 of which hardcoded #ef4444 / #f59e0b directly -- bypassing the existing semantic color variables,
// so they didn't change with the theme. This consolidates them by semantics.
//
// Usage: <Badge tone="success">Active</Badge> / <Badge tone="danger">Disabled</Badge>
import type { ReactNode } from 'react';

export type BadgeTone = 'neutral' | 'success' | 'warn' | 'danger' | 'info';

const TONE: Record<BadgeTone, React.CSSProperties> = {
  neutral: { background: 'var(--neutral-bg)', color: 'var(--neutral-fg)' },
  success: { background: 'var(--success-bg)', color: 'var(--success-fg)', border: '1px solid var(--success-border)' },
  warn:    { background: 'var(--warn-bg)',    color: 'var(--warn-fg)',    border: '1px solid var(--warn-border)' },
  danger:  { background: 'var(--danger-bg)',  color: 'var(--danger-fg)',  border: '1px solid var(--danger-border)' },
  info:    { background: 'var(--info-bg)',    color: 'var(--info-fg)' },
};

export function Badge({
  tone = 'neutral', children, className, style, title,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium whitespace-nowrap ${className ?? ''}`}
      style={{ ...TONE[tone], ...style }}
    >
      {children}
    </span>
  );
}

/** Dot + text status indicator. Quieter than a full pill inside tables. */
export function StatusDot({ tone = 'neutral', children }: { tone?: BadgeTone; children: ReactNode }) {
  const solid: Record<BadgeTone, string> = {
    neutral: 'var(--neutral-fg)', success: 'var(--success-solid)',
    warn: 'var(--warn-solid)', danger: 'var(--danger-solid)', info: 'var(--info-solid)',
  };
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" style={{ color: 'var(--text-secondary)' }}>
      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: solid[tone] }} />
      {children}
    </span>
  );
}
