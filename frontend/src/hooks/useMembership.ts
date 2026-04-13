import { useState, useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { membershipApi, ApiError } from '../services/membershipApi';

interface UserMembership {
  id: number;
  user_id: number;
  membership_id: number;
  start_date: string;
  end_date: string;
  is_active: boolean;
}

/**
 * Shared logout helper — wipes the auth footprint from localStorage and
 * returns true if anything was actually cleared. Exported in case other
 * screens need to force-logout on 401 from their own API calls.
 */
export function clearStoredAuth(): boolean {
  let cleared = false;
  if (localStorage.getItem('token')) {
    localStorage.removeItem('token');
    cleared = true;
  }
  if (localStorage.getItem('user')) {
    localStorage.removeItem('user');
    cleared = true;
  }
  return cleared;
}

export function useMembership() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'));
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery<UserMembership | null>({
    queryKey: ['user-membership', token],
    queryFn: async () => {
      if (!token) return null;
      try {
        return await membershipApi.getUserMembership(token);
      } catch (err) {
        // Zombie-login killer: if the token is rejected (401/403), purge
        // the stored auth so `isLoggedIn` flips to false and the UI stops
        // pretending the user is signed in. Any other error (network,
        // 5xx, parse) falls through as "unknown" — we keep the token so
        // the next refresh can retry without bouncing the user to login.
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          clearStoredAuth();
          setToken(null);
        }
        return null;
      }
    },
    enabled: !!token,
    staleTime: 60_000,
    retry: false,
  });

  const refresh = useCallback(() => {
    const latest = localStorage.getItem('token');
    setToken(latest);
    queryClient.invalidateQueries({ queryKey: ['user-membership'] });
  }, [queryClient]);

  return {
    token,
    isLoggedIn: !!token,
    isUnlocked: !!(data && data.is_active),
    isLoading,
    refresh,
  };
}
