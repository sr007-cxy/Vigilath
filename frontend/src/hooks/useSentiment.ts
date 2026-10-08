// React Query hooks wrapping sentimentApi.
// Auto-injects the token, unifies staleTime, and provides invalidate methods.

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../contexts/AuthContext';
import { useAgencyContext } from '../contexts/AgencyContext';
import { sentimentApi, isMockMode } from '../services/sentimentApi';
import { SENTIMENT_PLATFORMS as PLATFORM_FALLBACK } from '../constants/sentimentPlatforms';
import type {
  AccountCreatePayload, AccountUpdatePayload, PostsQuery, PostsResponse,
  SentimentPlatform,
} from '../types/sentiment';

const ACCOUNTS_KEY = ['sentiment', 'accounts'] as const;
const PLATFORMS_KEY = ['sentiment', 'platforms'] as const;

/** "Upgrade" the local static entries in constants/sentimentPlatforms.ts into the full SentimentPlatform shape.
 *  Only used for (a) mock mode (b) fallback when the backend /platforms fetch fails, so the page isn't blank. */
function fallbackPlatforms(): SentimentPlatform[] {
  return PLATFORM_FALLBACK.map(p => ({
    code: p.code,
    domain: p.domain,
    category: p.category,
    media_type: p.media_type,
    industry: p.industry,
    region: p.region,
    name_zh: '', name_en: '',  // let consumers fall back to the i18n sourceLabels translation
  }));
}

// ICP filing remediation: the backend sentiment_platforms table may still have historical overseas site rows,
// never shown in the UI -- filtered out uniformly here, so each consumer doesn't filter on its own.
function dropOverseas(list: SentimentPlatform[]): SentimentPlatform[] {
  return list.filter((p) => p.region !== 'overseas');
}

/** Fetch the media platform catalog (global read-only, almost never changes).
 *  - mock mode: use constants directly
 *  - real mode: GET /api/sentiment/platforms, falling back to constants on failure
 *  - staleTime: 1h (catalog changes take effect within 1 hour at most; usually users get the new version on re-login) */
export function useSentimentPlatforms() {
  const { token } = useAuth();
  return useQuery<SentimentPlatform[]>({
    queryKey: PLATFORMS_KEY,
    queryFn: async () => {
      if (isMockMode() || !token) return fallbackPlatforms();
      try {
        const data = await sentimentApi.listPlatforms(token);
        return data.length > 0 ? dropOverseas(data) : fallbackPlatforms();
      } catch {
        return fallbackPlatforms();
      }
    },
    staleTime: 60 * 60_000,
    initialData: fallbackPlatforms,  // data available on first render, avoiding a blank UI flash
  });
}

export function useSentimentAccounts() {
  const { token, isAgency } = useAuth();
  const { enterpriseUserId } = useAgencyContext();
  return useQuery({
    queryKey: [...ACCOUNTS_KEY, isAgency ? enterpriseUserId : null],
    queryFn: async () => {
      const rows = await sentimentApi.listAccounts(token!);
      return isAgency && enterpriseUserId
        ? rows.filter(row => row.user_id === enterpriseUserId) : rows;
    },
    enabled: !!token,
    staleTime: 60_000,
  });
}

export function useCreateAccount() {
  const { token, isAgency } = useAuth();
  const { enterpriseUserId, canWrite } = useAgencyContext();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AccountCreatePayload) => {
      if (!canWrite) throw new Error('当前企业业务处于只读状态');
      return sentimentApi.createAccount({
        ...payload,
        ...(isAgency && enterpriseUserId ? { enterprise_user_id: enterpriseUserId } : {}),
      }, token!);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  });
}

export function useUpdateAccount() {
  const { token } = useAuth();
  const { canWrite } = useAgencyContext();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: AccountUpdatePayload }) => {
      if (!canWrite) throw new Error('当前企业业务处于只读状态');
      return sentimentApi.updateAccount(id, payload, token!);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  });
}

export function useDeleteAccount() {
  const { token } = useAuth();
  const { canWrite } = useAgencyContext();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => {
      if (!canWrite) throw new Error('当前企业业务处于只读状态');
      return sentimentApi.deleteAccount(id, token!);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  });
}

export function useRunStatus(accountId: number | null) {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useQuery({
    queryKey: ['sentiment', 'run-status', accountId],
    queryFn: async () => {
      const data = await sentimentApi.getRunStatus(accountId!, token!);
      // When the status flips from running/pending to terminal (success/failed),
      // proactively invalidate data queries -- users see new results without manually refreshing.
      const prev = qc.getQueryData<{ status?: string }>(['sentiment', 'run-status', accountId]);
      const wasRunning = prev?.status === 'running' || prev?.status === 'pending';
      const nowDone = data?.status === 'success' || data?.status === 'failed';
      if (wasRunning && nowDone) {
        qc.invalidateQueries({ queryKey: ['sentiment', 'today', accountId] });
        qc.invalidateQueries({ queryKey: ['sentiment', 'posts', accountId] });
        qc.invalidateQueries({ queryKey: ['sentiment', 'briefs', accountId] });
        qc.invalidateQueries({ queryKey: ['sentiment', 'websearch-latest', accountId] });
        qc.invalidateQueries({ queryKey: ACCOUNTS_KEY });
      }
      return data;
    },
    enabled: !!token && accountId !== null && accountId > 0,
    staleTime: 5_000,
    // Poll every 15s while the task runs; stop polling in a terminal state (return false)
    refetchInterval: (query) => {
      const s = (query.state.data as { status?: string } | undefined)?.status;
      return s === 'running' || s === 'pending' ? 15_000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useRunNow() {
  const { token } = useAuth();
  const { canWrite } = useAgencyContext();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: number) => {
      if (!canWrite) throw new Error('当前企业业务处于只读状态');
      return sentimentApi.runNow(accountId, token!);
    },
    onSuccess: (_data, accountId) => {
      qc.invalidateQueries({ queryKey: ['sentiment', 'run-status', accountId] });
      qc.invalidateQueries({ queryKey: ['sentiment', 'websearch-latest', accountId] });
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

export function useWebsearchLatest(accountId: number | null) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'websearch-latest', accountId],
    queryFn: () => sentimentApi.getWebsearchLatest(accountId!, token!),
    enabled: !!token && !!accountId,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
}

/** One-shot fetch (BriefsTab, backward compatibility). New code should use useInfinitePosts. */
export function usePosts(accountId: number | null, ticker: string | null, limit = 200) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'posts', accountId, ticker, limit],
    queryFn: () => sentimentApi.listPosts(accountId!, ticker!, token!, { limit, days: 0 }),
    enabled: !!token && !!accountId && !!ticker,
    staleTime: 60_000,
  });
}

/** Pagination + time range -- used by ArticlesTab.
 *  Changing filters automatically resets to the first page (queryKey includes filters);
 *  fetchNextPage() appends the next segment. */
export interface InfinitePostsFilters {
  pageSize?: number;
  /** Time range: days or start/end, one of the two */
  days?: number;
  start?: string;
  end?: string;
  /** Starting offset -- set to (N-1)*pageSize when "jumping to page N",
   *  distinguished by queryKey; changing it resets the infinite query */
  startOffset?: number;
  /** Sort key -- see PostSortKey; changing it resets the infinite query to the first page */
  sortBy?: import('../types/sentiment').PostSortKey;
}

export function useInfinitePosts(
  accountId: number | null,
  ticker: string | null,
  filters: InfinitePostsFilters = {},
) {
  const { token } = useAuth();
  const pageSize = filters.pageSize ?? 50;
  const startOffset = filters.startOffset ?? 0;
  return useInfiniteQuery<PostsResponse, Error>({
    queryKey: [
      'sentiment', 'posts-infinite', accountId, ticker,
      pageSize, filters.days, filters.start, filters.end, startOffset,
      filters.sortBy,
    ],
    queryFn: ({ pageParam }) => {
      const q: PostsQuery = { limit: pageSize, offset: (pageParam as number) ?? startOffset };
      if (filters.start || filters.end) {
        if (filters.start) q.start = filters.start;
        if (filters.end) q.end = filters.end;
      } else if (filters.days !== undefined) {
        q.days = filters.days;
      }
      if (filters.sortBy) q.sort_by = filters.sortBy;
      return sentimentApi.listPosts(accountId!, ticker!, token!, q);
    },
    initialPageParam: startOffset,
    getNextPageParam: (lastPage, allPages) => {
      const fetched = allPages.reduce((sum, p) => sum + (p.items?.length ?? 0), 0);
      const total = lastPage.total ?? fetched;
      const consumed = startOffset + fetched;
      return consumed < total ? consumed : undefined;
    },
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

// ─────────────────── newsnow real-time trending ──────────────────

/** Recommended source catalog (static). Long staleTime -- almost never changes. */
export function useNewsnowSources() {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'newsnow-sources'],
    queryFn: () => sentimentApi.listNewsnowSources(token!),
    enabled: !!token,
    staleTime: 60 * 60_000,
  });
}

/** Trending list for a given source. newsnow caches for 30 minutes itself; the frontend expires after 5 minutes to trigger a refresh. */
export function useNewsnowHot(source: string | null, limit = 30) {
  const { token } = useAuth();
  return useQuery({
    queryKey: ['sentiment', 'newsnow-hot', source, limit],
    queryFn: () => sentimentApi.getNewsnowHot(source!, token!, limit),
    enabled: !!token && !!source,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useGenerateDraft() {
  const { token } = useAuth();
  const { canWrite } = useAgencyContext();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: Parameters<typeof sentimentApi.generateDraft>[0]) => {
      if (!canWrite) throw new Error('当前企业业务处于只读状态');
      return sentimentApi.generateDraft(payload, token!);
    },
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
  const { canWrite } = useAgencyContext();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, key, body }: { accountId: number; key: string; body: string }) => {
      if (!canWrite) throw new Error('当前企业业务处于只读状态');
      return sentimentApi.upsertKnowledge(accountId, key, body, token!);
    },
    onSuccess: (_data, { accountId }) => {
      qc.invalidateQueries({ queryKey: ['sentiment', 'knowledge', accountId] });
    },
  });
}
