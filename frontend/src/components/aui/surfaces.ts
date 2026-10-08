/**
 * Surface styles for assistant-ui Elements (taken from `@assistant-ui/elements-surfaces`, rewritten).
 *
 * Two rewrites, not a straight copy:
 * 1. **Tokens switched to aui-***: the original consumes shadcn's `bg-background` / `text-foreground`,
 *    and we don't take those names (see comments in tailwind.config.js), so a uniform prefix is used.
 * 2. **Removed `dark:` variants and opacity modifiers**: the site theme is `body[data-theme="dark"]`,
 *    while Tailwind's default darkMode is prefers-color-scheme, so `dark:` would follow the system instead of the site theme;
 *    and `--aui-*` already flips per theme, so no light/dark split is needed. Opacity modifiers (`/45`, `/[0.04]`)
 *    can't compute alpha on CSS variable colors, so semantic tokens (muted / secondary) are used instead.
 */
export const paper = 'bg-aui-card border border-aui-border';

export const floating = 'bg-aui-popover border border-aui-border';

export const field = 'bg-aui-muted';

export const fieldInteractive = 'bg-aui-muted transition-colors hover:bg-aui-accent';

export const pressable =
  'transition-transform duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.96] motion-reduce:transition-none';

export const ghostButton =
  'flex items-center justify-center rounded-full text-aui-muted-foreground outline-none ' +
  'transition-[background-color,color,scale] duration-150 hover:bg-aui-accent hover:text-aui-foreground ' +
  'active:scale-[0.96] focus-visible:ring-1 focus-visible:ring-aui-ring motion-reduce:transition-none';

export const inkButton =
  'bg-aui-primary text-aui-primary-foreground transition-[opacity,scale] duration-150 ' +
  'ease-[cubic-bezier(0.23,1,0.32,1)] hover:opacity-90 active:scale-[0.96] motion-reduce:transition-none';

export const live = 'text-aui-primary';

export const mono = 'font-mono text-[11px] tracking-tight';
