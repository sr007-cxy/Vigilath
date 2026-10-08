/** Minimal className merge -- Elements source depends on `cn`, and we don't pull in clsx / tailwind-merge for it. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}
