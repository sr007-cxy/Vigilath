/**
 * Long-task status bar (taken from `@assistant-ui/elements-agent-status`, rewritten).
 *
 * Used for 8-20 minute tasks like report generation: previously there was only a static "auditing" text,
 * and users didn't know how long it had run or whether it was still running.
 *
 * Changes from the original:
 * - Tokens switched to `aui-*`, `dark:` variants removed (rationale in the header comment of `surfaces.ts`)
 * - Removed `animate-in` / `fade-in` / `blur-in` -- those are tailwindcss-animate plugin classes,
 *   which this repo doesn't install, so they'd be dead class names
 * - The original has a fixed pause/rerun button on the right; our report tasks can't be paused, so it's an optional `action`,
 *   not rendered if not passed
 */
import type { ComponentProps, ReactNode } from 'react';
import { CheckIcon } from 'lucide-react';
import { cn } from './cn';
import { mono, paper } from './surfaces';

export type AgentState = 'working' | 'waiting' | 'done' | 'failed';

export function AgentStatus({
  state,
  label,
  elapsed,
  action,
  truncateLabel = true,
  className,
  ...props
}: Omit<ComponentProps<'div'>, 'children' | 'state' | 'label' | 'elapsed' | 'action'> & {
  state: AgentState;
  label: string;
  elapsed?: string;
  action?: ReactNode;
  /** Width-limited truncation by default; pass false when placed in a narrow cell but the full sentence must be visible */
  truncateLabel?: boolean;
}) {
  return (
    <div
      data-slot="agent-status"
      className={cn(paper, 'inline-flex items-center gap-2.5 rounded-full py-1.5 ps-3.5 pe-3.5', className)}
      {...props}
    >
      {state === 'done' ? (
        <CheckIcon aria-hidden className="size-3 shrink-0 text-emerald-500" />
      ) : (
        <span
          aria-hidden
          className={cn(
            'size-1.5 shrink-0 rounded-full motion-reduce:animate-none',
            state === 'working' ? 'animate-pulse bg-blue-500'
              : state === 'failed' ? 'bg-aui-destructive'
              : 'animate-pulse bg-aui-muted-foreground',
          )}
        />
      )}
      {/* Width limit is decided by the outer layer: the group variant approach didn't take effect here, the label was still cut by max-w-44,
          and production only showed "Diagnosing...". Changed to an explicit prop -- one less layer of Tailwind magic, behavior visible at a glance. */}
      <span className={cn('text-xs text-aui-foreground', truncateLabel && 'max-w-44 truncate')}>{label}</span>
      {elapsed !== undefined && state !== 'done' && (
        <span className={cn(mono, 'tabular-nums text-aui-muted-foreground')}>{elapsed}</span>
      )}
      {action}
    </div>
  );
}
