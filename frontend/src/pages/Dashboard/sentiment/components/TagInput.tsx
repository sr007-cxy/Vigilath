import { useState, type KeyboardEvent } from 'react';

interface Props {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  max?: number;
}

export function TagInput({ value, onChange, placeholder, max }: Props) {
  const [draft, setDraft] = useState('');

  const add = () => {
    const v = draft.trim().replace(/,$/, '');
    if (!v) return;
    if (value.includes(v)) {
      setDraft('');
      return;
    }
    if (max && value.length >= max) return;
    onChange([...value, v]);
    setDraft('');
  };

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      add();
    } else if (e.key === 'Backspace' && draft === '' && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  const remove = (i: number) => onChange(value.filter((_, idx) => idx !== i));

  return (
    <div
      className="rounded-md flex flex-wrap items-center gap-1.5 p-2 min-h-10"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}
      onClick={(e) => {
        const input = (e.currentTarget as HTMLDivElement).querySelector('input');
        input?.focus();
      }}
    >
      {value.map((tag, i) => (
        <span
          key={`${tag}-${i}`}
          className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs"
          style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}
        >
          {tag}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); remove(i); }}
            className="text-muted hover:text-primary"
            aria-label="remove"
          >
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKey}
        onBlur={add}
        placeholder={placeholder}
        className="flex-1 min-w-32 bg-transparent text-sm outline-none border-none"
        style={{ color: 'var(--text-primary)' }}
      />
    </div>
  );
}
