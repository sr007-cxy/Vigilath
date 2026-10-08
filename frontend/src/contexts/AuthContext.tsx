/**
 * AuthContext -- the single global source of JWT token + user profile state.
 *
 * Background (bug fix): previously each `useMembership()` consumer had its own React state,
 * read once from localStorage at init and never synced afterwards. The result:
 *   - <TierModal /> stays mounted at the App root from app startup
 *   - after registration, localStorage gets the new token
 *   - Register.tsx doesn't call refresh (doesn't even use useMembership)
 *   - TierModal's token state is stuck at null forever -> isLoggedIn=false
 *   - the user picks a plan -> the login dialog pops up again
 *
 * Second fix (2026-05-25): the Header's top-right login state didn't refresh after logging in via the dialog.
 * Cause: Header maintained `user` / `isAdmin` with its own useState, syncing only via
 * `useEffect([isLoggedIn])` and the `storage` event. But:
 *   - `localStorage.setItem` within the same tab doesn't fire that tab's `storage` event
 *   - if the token already existed at mount (refresh / leftover token), isLoggedIn doesn't change,
 *     so `useEffect([isLoggedIn])` doesn't rerun
 *   - when persistUser fails (/me throws), the catch falls back to writing {email}, but Header
 *     doesn't handle failure paths other than missing is_admin
 * Now user / isAdmin are moved into AuthContext too as the single source of truth; Header consumes it
 * directly, and AuthModal / AccountLayout / DashboardLayout / WorkbenchLayout write via
 * `setUser`, with the Context automatically updating all consumers.
 *
 * The new design:
 *   - token + user are maintained as React state only in AuthProvider
 *   - all useAuth() consume via Context, a single source of truth
 *   - writing the token always goes through `setToken(x)`, writing user through `setUser(u)`, and the Context
 *     handles syncing localStorage
 *   - AuthProvider listens to the `storage` event, so cross-tab login/logout also syncs
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { prewarmPluginAccounts } from '../services/wechatSyncPlugin';

export interface StoredUser {
  id?: number;
  email: string;
  name?: string | null;
  is_active?: boolean;
  is_admin?: boolean;
  /** System role; includes agency_admin / agency_operator; may be missing in old caches. */
  role?: string;
  /** Set for accounts registered via WeChat/phone. Displayed identity always goes through displayName(); don't use email directly. */
  phone?: string | null;
  wechat_nickname?: string | null;
}


/** role is the single source of truth for permissions; when old localStorage has no role, fall back to is_admin. */
export function resolveRole(u: StoredUser | null): string {
  if (!u) return 'user';
  if (u.role) return u.role;
  return u.is_admin ? 'admin' : 'user';
}

interface AuthContextValue {
  /** Current JWT; null when not logged in. */
  token: string | null;
  /** Convenience alias for !!token. */
  isLoggedIn: boolean;
  /** Current user profile; null when not logged in or profile missing. */
  user: StoredUser | null;
  /** Role convenience alias (falls back to is_admin when role is missing); 'user' when not logged in. */
  role: string;
  /** Convenience alias for role === 'admin' (old caches fall back to is_admin). */
  isAdmin: boolean;
  /** role === 'operator' (ops staff, the five workbench modules). */
  isOperator: boolean;
  /** Workbench staff: admin || operator. */
  isStaff: boolean;
  /** role === 'maintainer' (maintenance role: platform rules/crawlers/dispatch center/gateway). */
  isMaintainer: boolean;
  /** role === 'tenant' (gateway tenant, only has the tenant portal). */
  isTenant: boolean;
  isAgencyAdmin: boolean;
  isAgencyOperator: boolean;
  isAgency: boolean;
  /** Write the token after successful login/registration. Pass null to log out. Syncs localStorage. */
  setToken: (t: string | null) => void;
  /** Write/clear the user profile, syncing localStorage and immediately notifying all consumers. */
  setUser: (u: StoredUser | null) => void;
  /** Clear all login traces (token + user). */
  clearAuth: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

const TOKEN_KEY = 'token';
const USER_KEY = 'user';

function readStoredUser(): StoredUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return parsed as StoredUser;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  });

  const [user, setUserState] = useState<StoredUser | null>(() => readStoredUser());

  const setToken = useCallback((t: string | null) => {
    try {
      if (t) {
        localStorage.setItem(TOKEN_KEY, t);
      } else {
        localStorage.removeItem(TOKEN_KEY);
      }
    } catch {
      // A storage failure must not break the business flow; state is still updated
    }
    setTokenState(t);
  }, []);

  const setUser = useCallback((u: StoredUser | null) => {
    try {
      if (u) {
        localStorage.setItem(USER_KEY, JSON.stringify(u));
      } else {
        localStorage.removeItem(USER_KEY);
      }
    } catch {
      // ignore
    }
    setUserState(u);
  }, []);

  const clearAuth = useCallback(() => {
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    } catch {
      // ignore
    }
    setTokenState(null);
    setUserState(null);
  }, []);

  // Cross-tab sync: when the user logs in on tab A, tab B should see it too.
  // The browser's `storage` event only fires in **other tabs**, so it won't form a loop with this tab's
  // setToken/setUser.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === TOKEN_KEY) {
        setTokenState(e.newValue);
      } else if (e.key === USER_KEY) {
        setUserState(readStoredUser());
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  // Once a Vigilath login session appears, proactively notify the browser plugin to warm up the third-party platform account cache.
  // This way the publish center only reads the plugin cache and doesn't need to trigger account identification each time it opens.
  useEffect(() => {
    if (!token) return;
    let stopped = false;
    let attempts = 0;
    const run = () => {
      if (stopped) return;
      attempts += 1;
      prewarmPluginAccounts()
        .then(ok => {
          if (!ok && !stopped && attempts < 10) {
            window.setTimeout(run, 1000);
          }
        })
        .catch(() => {
          if (!stopped && attempts < 10) window.setTimeout(run, 1000);
        });
    };
    const timer = window.setTimeout(run, 500);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [token]);

  const value = useMemo<AuthContextValue>(() => {
    const role = resolveRole(user);
    return {
      token,
      isLoggedIn: !!token,
      user,
      role,
      isAdmin: role === 'admin',
      isOperator: role === 'operator',
      isStaff: role === 'admin' || role === 'operator',
      isMaintainer: role === 'maintainer',
      isTenant: role === 'tenant',
      isAgencyAdmin: role === 'agency_admin',
      isAgencyOperator: role === 'agency_operator',
      isAgency: role === 'agency_admin' || role === 'agency_operator',
      setToken,
      setUser,
      clearAuth,
    };
  }, [token, user, setToken, setUser, clearAuth]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within <AuthProvider>. Wrap your app in AuthProvider at the top level.');
  }
  return ctx;
}
