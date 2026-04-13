import { useState, useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { membershipApi } from '../services/membershipApi';

interface UserMembership {
  id: number;
  user_id: number;
  membership_id: number;
  start_date: string;
  end_date: string;
  is_active: boolean;
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
      } catch {
        return null;
      }
    },
    enabled: !!token,
    staleTime: 60_000,
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
