// Input controls + form fields. Pages used to assemble local constants like `style={{ ...card, fontSize: 12 }}`,
// giving 5 input heights within the same admin. This defines one set.
//
// Usage:
//   <Field label="Email" hint="Used for login" error={err}><Input value={v} onChange={setV} /></Field>
import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react';

const BASE: React.CSSProperties = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
};

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'size'> {
  /** Gives the value directly, no need to unpack e.target.value everywhere */
  onChange?: (value: string) => void;
  invalid?: boolean;
  size?: 'sm' | 'md';
}

export function Input({ onChange, invalid, size = 'md', className, style, ...rest }: InputProps) {
  return (
    <input
      onChange={(e) => onChange?.(e.target.value)}
      aria-invalid={invalid || undefined}
      className={`rounded-lg outline-none w-full ${size === 'sm' ? 'text-xs px-2.5 py-1.5' : 'text-sm px-3 py-2'} ${className ?? ''}`}
      style={{ ...BASE, ...(invalid ? { borderColor: 'var(--danger-border)' } : null), ...style }}
      {...rest}
    />
  );
}

export interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'onChange'> {
  onChange?: (value: string) => void;
  invalid?: boolean;
}

export function Textarea({ onChange, invalid, className, style, ...rest }: TextareaProps) {
  return (
    <textarea
      onChange={(e) => onChange?.(e.target.value)}
      aria-invalid={invalid || undefined}
      className={`rounded-lg outline-none w-full text-sm px-3 py-2 leading-relaxed ${className ?? ''}`}
      style={{ ...BASE, ...(invalid ? { borderColor: 'var(--danger-border)' } : null), ...style }}
      {...rest}
    />
  );
}

/**
 * Field wrapper: label on top, the control, then the hint or error.
 * Errors take precedence over hints -- showing both leaves people unsure which to read.
 */
export function Field({
  label, hint, error, required, children, className,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col gap-1.5 ${className ?? ''}`}>
      {label && (
        <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
          {label}
          {required && <span style={{ color: 'var(--danger-fg)' }}> *</span>}
        </label>
      )}
      {children}
      {error ? (
        <p className="text-[11px]" role="alert" style={{ color: 'var(--danger-fg)' }}>{error}</p>
      ) : hint ? (
        <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{hint}</p>
      ) : null}
    </div>
  );
}
