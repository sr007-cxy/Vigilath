// Site-wide unified button. Modeled on shadcn/ui's variant x size API, mapped onto this repo's CSS variables.
//
// Why it exists: the repo had 107 hand-written buttons with `px-3 py-1.5 rounded-md` + a chunk of inline style,
// each with its own height, radius, disabled opacity and hover; loading states each stuffed in their own animate-spin svg.
// Consolidated into one component, one change applies site-wide.
//
// Usage:
//   <Button onClick={save} loading={saving}>Save</Button>
//   <Button variant="ghost" size="sm">Details</Button>
//   <Button variant="danger" onClick={remove}>Delete</Button>
import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'solid' | 'outline' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

const SIZE: Record<ButtonSize, string> = {
  sm: 'text-xs px-2.5 py-1.5 rounded-md gap-1.5',
  md: 'text-sm px-3.5 py-2 rounded-lg gap-2',
  lg: 'text-sm px-5 py-2.5 rounded-lg gap-2 font-semibold',
};

/** Static styles per variant. Hover uses CSS variables, not Tailwind color scales -- otherwise it drifts under the dark theme. */
function variantStyle(variant: ButtonVariant): React.CSSProperties {
  switch (variant) {
    case 'solid':
      return { background: 'var(--solid-btn-bg)', color: 'var(--solid-btn-text)', border: '1px solid transparent' };
    case 'danger':
      return { background: 'var(--danger-bg)', color: 'var(--danger-fg)', border: '1px solid var(--danger-border)' };
    case 'ghost':
      return { background: 'transparent', color: 'var(--text-secondary)', border: '1px solid transparent' };
    case 'outline':
    default:
      return { background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' };
  }
}

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** In progress: auto-disabled + spinner in front. No more hand-written animate-spin svgs everywhere, and it also blocks duplicate submissions. */
  loading?: boolean;
  /** Leading icon (replaced by the spinner while loading) */
  icon?: ReactNode;
  /** Fill the container width */
  block?: boolean;
  type?: 'button' | 'submit' | 'reset';
}

export function Button({
  variant = 'outline', size = 'md', loading = false, icon, block = false,
  type = 'button', disabled, className, style, children, ...rest
}: ButtonProps) {
  const off = disabled || loading;
  return (
    <button
      type={type}
      disabled={off}
      aria-busy={loading || undefined}
      className={`ui-btn inline-flex items-center justify-center font-medium whitespace-nowrap transition-colors
                  ${SIZE[size]} ${block ? 'w-full' : ''} ${className ?? ''}`}
      style={{
        ...variantStyle(variant),
        // Disabled is always 0.5 + not-allowed: previously there were three variants, 0.5 / 0.6 / 0.45
        opacity: off ? 0.5 : 1,
        cursor: off ? 'not-allowed' : 'pointer',
        ...style,
      }}
      {...rest}
    >
      {loading ? (
        <svg className="animate-spin shrink-0" width="14" height="14" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth={4} />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      ) : icon ? (
        <span className="shrink-0 inline-flex">{icon}</span>
      ) : null}
      {children}
    </button>
  );
}
