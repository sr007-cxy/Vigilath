/**
 * Tool call row (taken from `@assistant-ui/elements-tool-call`, rewritten).
 *
 * Previously the conversation only had coarse phrases like "filter and rank target companies", with no way to tell what was called, with what conditions,
 * and what came out. Here each tool call is one row; expand it to see the request and result.
 *
 * Changes from the original:
 * - Doesn't import `@/components/ui/collapsible` (radix): adding a dependency for one collapsible panel isn't worth it; uses local state
 * - Removed ShimmerLabel / SwapLabel: the former depends on the tw-shimmer plugin, the latter is a label fade; neither is necessary
 * - Removed `animate-in` / `fade-in` / `zoom-in` (tailwindcss-animate not installed)
 * - Tokens switched to aui-*, removed `dark:` and opacity modifiers (rationale in surfaces.ts)
 */
import { useState } from 'react';
import { CheckIcon, ChevronRightIcon, XIcon } from 'lucide-react';
import { cn } from './cn';
import { field, mono } from './surfaces';

export interface ToolCallProps {
  /** Wording when done, e.g. "Recommend target companies" */
  label: string;
  /** Wording while in progress, e.g. "Filtering target companies" */
  activeLabel?: string;
  /** Key parameters readable at a glance, e.g. "100 companies · industrial manufacturing · Yangtze River Delta" */
  query?: string;
  request?: string;
  result?: string;
  running?: boolean;
  failed?: boolean;
  defaultOpen?: boolean;
  className?: string;
}

export function ToolCall({
  label, activeLabel, query, request, result,
  running = false, failed = false, defaultOpen = false, className,
}: ToolCallProps) {
  const [open, setOpen] = useState(defaultOpen);
  const expandable = Boolean(request || result);
  return (
    <div data-slot="tool-call" className={cn('w-full', className)}>
      <button
        type="button"
        onClick={() => expandable && setOpen(v => !v)}
        className={cn(
          'flex w-full items-center gap-2 rounded-md py-1 text-left text-[13px] text-aui-muted-foreground outline-none transition-colors',
          expandable ? 'cursor-pointer hover:text-aui-foreground' : 'cursor-default',
        )}
      >
        {expandable && (
          <ChevronRightIcon
            className={cn('size-3.5 shrink-0 opacity-60 transition-transform duration-200', open && 'rotate-90')}
          />
        )}
        <span className={cn('truncate', running && 'animate-pulse')}>{running ? (activeLabel || label) : label}</span>
        {query && <span className={cn(mono, 'shrink-0 rounded-md bg-aui-muted px-1.5 py-0.5 text-aui-foreground')}>{query}</span>}
        <span className="ms-auto flex w-4 shrink-0 items-center justify-end">
          {failed ? <XIcon className="size-3.5 text-aui-destructive" />
            : !running && <CheckIcon className="size-3.5 text-emerald-500" />}
        </span>
      </button>
      {open && expandable && (
        <div className={cn(field, 'mt-2 overflow-hidden rounded-2xl text-xs')}>
          {request && (
            <div className="px-3.5 pb-2 pt-2.5">
              <p className={cn(mono, 'mb-1 text-aui-muted-foreground')}>请求</p>
              <p className="font-mono text-aui-muted-foreground">{request}</p>
            </div>
          )}
          {request && result && <div className="mx-3.5 h-px bg-aui-border" />}
          {result && (
            <div className="px-3.5 pb-2.5 pt-2">
              <p className={cn(mono, 'mb-1 text-aui-muted-foreground')}>结果</p>
              <p className="text-aui-foreground">{result}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
