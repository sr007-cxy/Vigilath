// Shared data fetch for source analysis -- the "Source analysis" and "Placement strategy" tabs use the same data.
//
// Lifted to the parent (ExecutionPlanView) to fetch once: this endpoint scans all responses and aggregates domains,
// so fetching once per tab would redo heavy work for nothing.

import { useCallback, useEffect, useState } from 'react';
import {
  adminReviewApi, type ExecutionPlan, type SourceInsights,
} from '../../services/adminReviewApi';
import { perDayOf } from './planWhy';

export interface InsightsState {
  data: SourceInsights | null;
  loading: boolean;
  err: string | null;
  reload: () => Promise<void>;
  /** Quota preview computed for N articles per day (inferred from the existing plan; 5 without a plan) */
  perDay: number;
}

export function useSourceInsights(
  topicId: number, plan: ExecutionPlan | null,
): InsightsState {
  const token = localStorage.getItem('token') || '';
  const [data, setData] = useState<SourceInsights | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const perDay = perDayOf(plan?.publishing_plan || []);

  const reload = useCallback(async () => {
    if (!topicId) return;
    setLoading(true);
    try {
      setData(await adminReviewApi.getSourceInsights(topicId, token, perDay));
      setErr(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [topicId, token, perDay]);

  useEffect(() => { reload(); }, [reload]);

  return { data, loading, err, reload, perDay };
}
