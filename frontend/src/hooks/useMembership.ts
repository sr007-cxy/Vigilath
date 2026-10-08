import { useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { membershipApi, ApiError } from '../services/membershipApi';
import { useAuth } from '../contexts/AuthContext';

interface UserMembership {
  id: number;
  user_id: number;
  membership_id: number;
  start_date: string;
  end_date: string;
  is_active: boolean;
  /** Tier slug (free / pro / starter / growth / scale), filled by the backend API layer */
  slug?: string | null;
}

// Tier ranking, kept consistent with TIER_RANK in backend models/advanced.py.
const TIER_RANK: Record<string, number> = {
  free: 0,
  pro: 1,
  starter: 2,
  growth: 3,
  scale: 4,
};

/**
 * Shared logout helper — delegates to AuthContext.clearAuth via a
 * component-captured closure. Kept as a re-export convenience for code that
 * already imports from this module.
 *
 * NOTE: can only be called from within a React component (via the hook
 * returned value). If non-React code needs to clear auth, it should import
 * useAuth directly or the caller needs to propagate a callback.
 *
 * @deprecated Prefer `useAuth().clearAuth()` directly in new code.
 */
export function useClearStoredAuth(): () => boolean {
  const { token, clearAuth } = useAuth();
  return useCallback(() => {
    if (!token) return false;
    clearAuth();
    return true;
  }, [token, clearAuth]);
}

export function useMembership() {
  // The token now comes from AuthContext, the single global source of truth. Any setToken / clearAuth
  // call re-renders every component consuming useMembership. This fixes the bug of persistent components like TierModal
  // being "logged in but with out-of-sync state".
  const { token, isLoggedIn, clearAuth } = useAuth();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery<UserMembership | null>({
    queryKey: ['user-membership', token],
    queryFn: async () => {
      if (!token) return null;
      try {
        return await membershipApi.getUserMembership(token);
      } catch (err) {
        // Zombie-login killer: the token was rejected by the backend (401/403); clear storage so the UI
        // doesn't keep pretending to be "logged in". Other errors (network, 5xx, parsing) keep the token and retry
        // on the next refresh, without kicking the user out for no reason.
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          clearAuth();
        }
        return null;
      }
    },
    enabled: !!token,
    staleTime: 60_000,
    retry: false,
  });

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['user-membership'] });
  }, [queryClient]);

  return {
    token,
    isLoggedIn,
    // Gate for audit features (advanced audit modes + one-click full diagnosis) -- only the tier matters.
    // After "logging in grants audit membership", the backend gives pro to logged-in users without a subscription too, so this must
    // judge by slug rather than by whether there's a payment record, otherwise the backend allows it while the frontend still pops the upgrade dialog.
    // The metric definition must align with the backend `_ensure_tier` (audit features in MODE_MIN_TIER are all pro).
    isUnlocked: !!(
      data &&
      data.is_active &&
      TIER_RANK[data.slug ?? 'free'] >= TIER_RANK.pro
    ),
    isLoading,
    refresh,
  };
}
