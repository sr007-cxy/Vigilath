// "Current topic awareness": lets the global floating Agent know which topic the user is looking at right now.
// Site-wide topic state is URL-driven (no global store), so parse it from the current route:
//   - /dashboard/topics/:topicId/profile        -> path param
//   - /app/reports?topic=123 (and subpages)     -> query param
//   - /ops/topics/:topicId/edit|...       -> ops workbench project page (path param)
//   - /ops/content-review?topic=123       -> ops workbench article management (query param)
//
// Before 2026-08, /ops/ always returned null, because "admins edit other people's topics, and the backend would
// ModelRetry on failed ownership checks". Now the backend has an ops console lane (scope="admin": checks role + locks project,
// no ownership check, see backend/agents/geo/scope.py), so the project id must be fed in -- without it the ops console
// lane can never activate (resolve_scope always falls back to self without topic_id).
// ★ These two changes must ship in the same batch; changing either alone is a functional regression.
import { useLocation } from 'react-router-dom';

/** Ops workbench project page route: /ops/topics/:topicId/... (aligned with backend _ADMIN_ROUTE_RE). */
export function isAdminTopicRoute(pathname: string): boolean {
  return /^\/workbench\/topics\/\d+(?:\/|$)/.test(pathname);
}

/** Parse the topic id the user is viewing from the current location; null when not on a topic page. */
export function topicIdFromLocation(pathname: string, search: string): number | null {
  // Topic pages (customer-side /dashboard/topics/:id and ops-side /ops/topics/:id have the same shape)
  const m = pathname.match(/\/topics\/(\d+)(?:\/|$)/);
  if (m) {
    const id = Number(m[1]);
    return id > 0 ? id : null;
  }

  // Brand growth pages use ?topic=123; ops-side article management /ops/content-review?topic=123 has the same shape
  const q = Number(new URLSearchParams(search).get('topic') || '0');
  return q > 0 ? q : null;
}

/** Hook version: follows route changes, reflecting the current topic in real time. */
export function useCurrentTopicId(): number | null {
  const { pathname, search } = useLocation();
  return topicIdFromLocation(pathname, search);
}

/**
 * Whether this turn requests the "ops workbench lane".
 * Criterion = under /workbench + a resolvable project id; real access is also hard-gated by backend role
 * (non admin/operator is always self, whatever the frontend sends).
 */
export function useAgentScope(): 'admin' | undefined {
  const { pathname, search } = useLocation();
  if (!pathname.startsWith('/ops/')) return undefined;
  return topicIdFromLocation(pathname, search) ? 'admin' : undefined;
}
