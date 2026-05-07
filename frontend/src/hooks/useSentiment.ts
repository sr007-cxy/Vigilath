// React Query hooks 包装 sentimentApi.
// 自动注入 token,统一 staleTime,提供 invalidate 方法.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../contexts/AuthContext';
import { sentimentApi } from '../services/sentimentApi';
import type {
  AccountCreatePayload, AccountUpdatePayload,
} from '../types/sentiment';

const ACCOUNTS_KEY = ['sentiment', 'accounts'] as const;

export function useSentimentAccounts() {
  const { token } = useAuth();
  return useQuery({
    queryKey: ACCOUNTS_KEY,
    queryFn: () => sentimentApi.listAccounts(token!),
    enabled: !!token,
    staleTime: 60_000,
  });
}

export function useCreateAccount() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AccountCreatePayload) => sentimentApi.createAccount(payload, token!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  });
}

export function useUpdateAccount() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: AccountUpdatePayload }) =>
      sentimentApi.updateAccount(id, payload, token!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  });
}

export function useDeleteAccount() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => sentimentApi.deleteAccount(id, token!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  });
}

export function useRunStatus(accountId: number | null) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'run-status', accountId],
    queryFn: () => sentimentApi.getRunStatus(accountId!, token!),
    enabled: !!token && accountId !== null && accountId > 0,
    staleTime: 30_000,
  });
}

export function useRunNow() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: number) => sentimentApi.runNow(accountId, token!),
    onSuccess: (_data, accountId) => {
      qc.invalidateQueries({ queryKey: ['sentiment', 'run-status', accountId] });
      qc.invalidateQueries({ queryKey: ACCOUNTS_KEY });
    },
  });
}

export function useTodayData(accountId: number | null, ticker: string | null) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'today', accountId, ticker],
    queryFn: () => sentimentApi.getToday(accountId!, ticker!, token!),
    enabled: !!token && !!accountId && !!ticker,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function usePosts(accountId: number | null, ticker: string | null, limit = 200) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'posts', accountId, ticker, limit],
    queryFn: () => sentimentApi.listPosts(accountId!, ticker!, token!, limit),
    enabled: !!token && !!accountId && !!ticker,
    staleTime: 60_000,
  });
}

export function useBriefs(accountId: number | null, ticker: string | null) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'briefs', accountId, ticker],
    queryFn: () => sentimentApi.listBriefs(accountId!, ticker!, token!),
    enabled: !!token && !!accountId && !!ticker,
    staleTime: 60_000,
  });
}

export function useBriefDetail(accountId: number | null, briefId: number | null) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'brief', accountId, briefId],
    queryFn: () => sentimentApi.getBrief(accountId!, briefId!, token!),
    enabled: !!token && !!accountId && !!briefId,
    staleTime: 5 * 60_000,
  });
}

export function useDrafts(accountId: number | null, ticker: string | null) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'drafts', accountId, ticker],
    queryFn: () => sentimentApi.listDrafts(accountId!, ticker!, token!),
    enabled: !!token && !!accountId && !!ticker,
    staleTime: 60_000,
  });
}

export function useGenerateDraft() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: Parameters<typeof sentimentApi.generateDraft>[0]) =>
      sentimentApi.generateDraft(payload, token!),
    onSuccess: (_data, payload) => {
      qc.invalidateQueries({ queryKey: ['sentiment', 'drafts', payload.account_id] });
    },
  });
}

export function useKnowledge(accountId: number | null) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'knowledge', accountId],
    queryFn: () => sentimentApi.listKnowledge(accountId!, token!),
    enabled: !!token && !!accountId,
    staleTime: 5 * 60_000,
  });
}

export function useUpsertKnowledge() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, key, body }: { accountId: number; key: string; body: string }) =>
      sentimentApi.upsertKnowledge(accountId, key, body, token!),
    onSuccess: (_data, { accountId }) => {
      qc.invalidateQueries({ queryKey: ['sentiment', 'knowledge', accountId] });
    },
  });
}
