// Data fetching and derivation layer for the plan's "Placement strategy" -- turns the backend-computed strategy into the shape the page needs.
//
// Pure functions + a fetch hook live here rather than in the component file: planWhy.ts /
// useSourceInsights.ts in the same directory are handled the same way (component files are bound by react-refresh rules).
//
// ## What this page is about
//
//   Strategy (top half)    which sources each of the five major engines trusts -> where this brand should place, in what format, at what cadence
//   Basis (bottom half)    the plan laid out by the strategy: why these platforms, these article counts
//
// The top half is the **conclusion**, the bottom half the **proof**. Their time frames differ:
//
//   Recomputed now     the copy this page fetches, computed from current data (endpoint placement-strategy)
//   Decided at the time   the copy used when this plan was laid out, stored in plan.overview.plan_basis.strategy
//
// A mismatch = the data changed or someone manually edited the schedule; strategyDrift() surfaces it,
// instead of letting it silently drift.

import { useCallback, useEffect, useState } from 'react';
import {
  adminReviewApi, type ExecutionPlan, type PlacementStrategy,
} from '../../services/adminReviewApi';
import { planBasisOf } from './planWhy';
import { todayISO } from './planWeeks';

// Source tiers -- the backend uses T0-T3 codes; the page always uses the human-readable tier names
export const TIER_LABEL: Record<string, string> = {
  T0: '顶级权威',
  T1: '高权威',
  T2: '普通补充',
  T3: '不建议投放',
};

export const TIER_COLOR: Record<string, string> = {
  T0: '#10b981',
  T1: '#3b82f6',
  T2: '#94a3b8',
  T3: '#ef4444',
};

// What the two kinds of actions that can't go into the publish table are
export const INFRA_KIND_LABEL: Record<string, string> = {
  profile: '资料建设',
  account: '账号建设',
};

export interface StrategyState {
  data: PlacementStrategy | null;
  loading: boolean;
  err: string | null;
  reload: () => Promise<void>;
}

/** Fetch a freshly computed placement strategy; perDay only affects article-count conversion in the cadence table */
export function usePlacementStrategy(topicId: number, perDay: number): StrategyState {
  const token = localStorage.getItem('token') || '';
  const [data, setData] = useState<PlacementStrategy | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!topicId) return;
    setLoading(true);
    try {
      const out = await adminReviewApi.getPlacementStrategy(topicId, token, perDay);
      setData(out.strategy);
      setErr(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [topicId, token, perDay]);

  useEffect(() => { reload(); }, [reload]);

  return { data, loading, err, reload };
}

/** The strategy used when this plan was laid out (none = this plan was not laid out by strategy) */
export function strategySnapshotOf(
  plan: ExecutionPlan | null | undefined,
): PlacementStrategy | null {
  const s = planBasisOf(plan)?.strategy;
  return s && typeof s === 'object' ? s : null;
}

/** How many articles each platform has scheduled from today -- used to mark "N scheduled in the publish table" on the checklist */
export function plannedByPlatform(plan: ExecutionPlan | null | undefined): Map<string, number> {
  const out = new Map<string, number>();
  const today = todayISO();   // Must use the local calendar day -- the UTC version is yesterday before 08:00 in UTC+8
  for (const it of plan?.publishing_plan || []) {
    if (String(it.publish_date || '').slice(0, 10) < today) continue;
    const p = it.platform || '';
    if (p) out.set(p, (out.get(p) || 0) + 1);
  }
  return out;
}

export interface StrategyDrift {
  state: 'none' | 'aligned' | 'drifted';
  text: string;
  /** Engines whose rank changed: rank then -> rank now */
  rows?: { name: string; was: number; now: number }[];
}

/**
 * Strategy decided at the time vs. strategy computed now.
 *
 * Only compares engine priority order -- quotas are weighted by it, so if the order is unchanged the plan still holds;
 * checklist item states change daily with placement progress, so comparing them would report "changed" every day.
 */
export function strategyDrift(
  snapshot: PlacementStrategy | null, live: PlacementStrategy | null,
): StrategyDrift {
  if (!snapshot) {
    return { state: 'none', text: '本计划未按投放策略排期 —— 以下为按当前数据计算的策略,需执行「按策略重排」才会落入发文表。' };
  }
  if (!live) return { state: 'none', text: '' };
  const wasRank = new Map(snapshot.engine_priority.map(p => [p.engine, p.rank]));
  const rows = live.engine_priority
    .filter(p => wasRank.has(p.engine) && wasRank.get(p.engine) !== p.rank)
    .map(p => ({ name: p.name, was: wasRank.get(p.engine) || 0, now: p.rank }));
  if (rows.length === 0) {
    return { state: 'aligned', text: '与排期时采用的策略一致 —— 引擎优先级未发生变化。' };
  }
  return {
    state: 'drifted',
    text: '实测数据已变化,引擎优先级与排期时不一致。如需同步至发文表,请执行一次重排。',
    rows,
  };
}

/** Also warn when the knowledge-base version changed -- old plans' strategies were computed under old rules */
export function kbChanged(
  snapshot: PlacementStrategy | null, live: PlacementStrategy | null,
): boolean {
  return !!snapshot && !!live && !!snapshot.kb_version
    && snapshot.kb_version !== live.kb_version;
}
