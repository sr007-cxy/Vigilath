// Action menu -- tucks a set of low-frequency actions behind one button instead of laying them flat on the toolbar.
//
// Why it's needed: the plan toolbar once laid out 7 buttons flat (add one / batch generate drafts / rebuild publish table /
// candidate topics / template library / rebuild plan snapshot / rerun baseline audit), all the same size and prominence, while only one or two
// are actually clicked daily. Laying them flat isn't "everything is findable", it's "nothing stands out".
//
// Built on Radix Popover -- already used by the project (FilterBar's time filter); colors, shadows and layers
// follow the same rules, with no new dependency and no home-made onBlur closing.
//
// Usage:
//   <Menu label="⊕ Add" items={[
//     { label: 'Add one', hint: 'Pick date + template, can generate a draft directly', onClick: ... },
//     { label: 'Pick from candidate topics', onClick: ... },
//   ]} />

import { useState, type ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { POPUP_Z } from '../overlays/zIndex';

export interface MenuItem {
  label: ReactNode;
  /** Second line of small text: explain what this item actually does. Low-frequency actions especially need it -- the name can't carry all the information */
  hint?: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  /** Things that cost money / overwrite the body get a red warning */
  danger?: boolean;
}

export function Menu({
  label, items, align = 'end', title, triggerClassName, triggerStyle,
}: {
  label: ReactNode;
  items: readonly MenuItem[];
  align?: 'start' | 'center' | 'end';
  title?: string;
  triggerClassName?: string;
  triggerStyle?: React.CSSProperties;
}) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button type="button" title={title}
                className={triggerClassName
                  ?? 'text-xs px-2.5 py-1.5 rounded-md inline-flex items-center gap-1'}
                style={triggerStyle ?? {
                  background: 'transparent', color: 'var(--text-secondary)',
                  border: '1px solid var(--border-color)',
                }}>
          {label}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align={align} sideOffset={6}
                         className={`${POPUP_Z} rounded-lg p-1 min-w-[200px] max-w-[280px]`}
                         style={{ background: 'var(--bg-card)',
                                  border: '1px solid var(--border-color)',
                                  boxShadow: '0 8px 24px rgba(0,0,0,0.28)' }}>
          {items.map((it, i) => (
            <button key={i} type="button" disabled={it.disabled}
                    onClick={() => { setOpen(false); it.onClick(); }}
                    className="w-full text-left px-2.5 py-1.5 rounded-md text-xs
                               hover:bg-[var(--bg-tertiary)] disabled:opacity-40
                               disabled:cursor-not-allowed disabled:hover:bg-transparent"
                    style={{ color: it.danger ? 'var(--warn-fg)' : 'var(--text-primary)' }}>
              <div>{it.label}</div>
              {it.hint && (
                <div className="text-[11px] text-muted mt-0.5 leading-snug">{it.hint}</div>
              )}
            </button>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
