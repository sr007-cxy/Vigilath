// Shared model for "one row" of the publish plan -- the table editor (PublishingPlanEditor) and the calendar page
// (PlanCalendarTab) share one row structure, validation and conversion, avoiding two drifting metric definitions.
//
// Components can't hold these: PublishingPlanEditor.tsx may only export components (react-refresh rules),
// so types and pure functions live in this file.

import type { PlanItemDraft, PublishPlanItem } from '../../services/adminReviewApi';
import { dayOf } from './planWeeks';
import { PAID_VENDORS } from '../../components/publish/publishShared';

export type RowDraft = {
  id?: string;           // Old rows carry a backend uuid; new rows start empty, filled on submit
  localKey: string;      // Per-row React key — always stable
  seq: number;
  publish_date: string;
  // New seed-based flow: seed non-empty, query empty; legacy: query ∈ monitored, seed empty.
  // One or the other; at least one must be non-empty.
  seed: string;
  query: string;
  // 2026-06-11 — multi-select monitored queries (legacy single-query rows are merged in here on load)
  queries: string[];
  template_id: number | null;
  platform: string;
  // 2026-08-04 — one draft, multiple placements: additional placement platforms. The main platform is still `platform` (writing rules follow it);
  // each platform here gets its own placement task after approval (no outlet, not via the paid vendor).
  extra_platforms: string[];
  // Which channel: '' undecided / aitoearn own account / own owned site /
  // rwj|mjk paid placement (two vendors, each with its own resource IDs).
  // platform is the real platform name (Sohu/Zhihu), the channel is recorded separately -- whether a paid resource ID is needed
  // depends on this field, not platform (in old data only platform=='软文匠' ("Ruanwenjiang" paid vendor) meant paid placement).
  channel: string;
  // Preset placement outlet: on approval tasks are auto-created by (platform, media); the paid vendor carries a resource ID
  media: string;
  rwj_resource_id: number | null;
  note: string;
  // 2026-05-28 — per-row combo override (empty -> use profile default)
  creation_directions: string[];
  copywriting_types: string[];
};

// publish_date supports two forms: YYYY-MM-DD (old; publish time set by backend PUBLISH_HOUR, default 11:00)
// and YYYY-MM-DDTHH:MM (minute-level scheduling). The datetime-local input only accepts the latter; date-only values display as 11:00.
export const toDatetimeLocal = (v: string): string =>
  !v ? '' : (v.includes('T') ? v.slice(0, 16) : `${v}T11:00`);

// Paid placement channel keys / platform names (kept in sync with backend rwj_client.SITES and publishShared.PAID_VENDORS)
export const PAID_CHANNELS = PAID_VENDORS.map(v => v.key);
export const PAID_PLATFORMS = PAID_VENDORS.map(v => v.platform);

// Whether this row uses paid placement: new rows check channel; old rows have no channel, so fall back to platform being the vendor name
export const isRwjRow = (r: { channel?: string | null; platform?: string | null }): boolean =>
  PAID_CHANNELS.includes(r.channel || '')
  || (!r.channel && PAID_PLATFORMS.includes(r.platform || ''));

// The official-site blog is an owned site (free, controllable, crawlable by AI); every article is placed there by default and new rows check it automatically.
export const OWN_BLOG = '官网博客';
// Platform candidate pool: the template's own target platforms + common fixed entries
export const PLATFORM_POOL = ['公众号', '小红书', '抖音', '视频号', '知乎', OWN_BLOG,
  ...PAID_PLATFORMS];
export const platformOptionsFor = (extra: string[] = []): string[] =>
  [...new Set([...extra, ...PLATFORM_POOL])];

export const fromPlanItem = (it: PublishPlanItem): RowDraft => ({
  id: it.id,
  localKey: it.id,
  seq: it.seq,
  publish_date: it.publish_date,
  seed: it.seed || '',
  query: it.query,
  // Legacy single-query rows merge into the multi-select queries; when both exist, queries wins
  queries: (it.queries && it.queries.length > 0) ? it.queries : (it.query ? [it.query] : []),
  template_id: it.template_id ?? null,
  platform: it.platform || '',
  extra_platforms: (it.extra_platforms || []).filter(p => p && p !== it.platform),
  channel: it.channel || '',
  media: it.media || '',
  rwj_resource_id: it.rwj_resource_id ?? null,
  note: it.note || '',
  creation_directions: it.creation_directions || [],
  copywriting_types: it.copywriting_types || [],
});

// Per-row required-field validation -- run per row before saving; the table / calendar also use it to mark "needs completion", so the definition is always consistent.
// Returns null = row is valid; otherwise a human-readable reason.
export const rowIssue = (r: RowDraft, monSet: Set<string>): string | null => {
  const seed = r.seed.trim();
  const queries = r.queries.map(q => q.trim()).filter(Boolean);
  if (!seed && queries.length === 0) return '种子和提示词至少要填一个';
  const badQ = queries.find(q => !monSet.has(q));
  if (badQ) return `提示词不在监测列表里:${badQ}`;
  if (!r.template_id) return '没选模板';
  if (!r.platform) return '没选平台';
  return null;
};

/** RowDraft -> PlanItemDraft submitted to the backend (must pass rowIssue before calling) */
export const toPlanItemDraft = (r: RowDraft): PlanItemDraft => {
  const seed = r.seed.trim();
  const queries = r.queries.map(q => q.trim()).filter(Boolean);
  return {
    id: r.id || null,
    seq: r.seq,
    publish_date: r.publish_date,
    seed: seed || null,
    // Legacy single-value field: for pure query rows without a seed, backfill the first one for old backend/data compatibility
    query: (!seed && queries.length > 0) ? queries[0] : '',
    queries,
    template_id: r.template_id as number,
    platform: r.platform,
    extra_platforms: r.extra_platforms.filter(p => p && p !== r.platform),
    channel: r.channel || null,
    media: r.media || null,
    rwj_resource_id: isRwjRow(r) ? (r.rwj_resource_id || null) : null,
    note: r.note || null,
    creation_directions: r.creation_directions,
    copywriting_types: r.copywriting_types,
  };
};

/**
 * Row addressing (for the agent): key (full localKey or a unique prefix) first, then date + nth (nth article of the day, 1-based).
 *
 * **Plain indexes are forbidden**: the table renders in three layers, sortedRows -> pageRows (sliced by week) -> dayGroups (grouped by day),
 * so "row 3" is a different row in each layer and the model can't align them. And localKey for new rows is `new-<ts>-<rand>`,
 * which becomes a backend uuid after saving -- reusing an old key across saves is always wrong, so if no **unique** row resolves we throw; better to fail than guess.
 */
export function resolveRowKey(
  rows: RowDraft[],
  args: { key?: string; date?: string; nth?: number },
): string {
  const key = String(args.key || '').trim();
  if (key) {
    const exact = rows.filter(r => r.localKey === key);
    if (exact.length === 1) return exact[0].localKey;
    const pre = rows.filter(r => r.localKey.startsWith(key));
    if (pre.length === 1) return pre[0].localKey;
    if (pre.length > 1) throw new Error(`行标识「${key}」对应 ${pre.length} 行,不唯一`);
    throw new Error(`找不到行标识「${key}」的发文行(可能已保存过、标识变了,请重新查一次)`);
  }

  const date = String(args.date || '').trim();
  if (!date) throw new Error('要定位发文行,请给 key,或给 date(+ 当天第几篇 nth)');
  const same = rows.filter(r => dayOf(r.publish_date) === date)
    .sort((a, b) => (a.publish_date || '').localeCompare(b.publish_date || ''));
  if (same.length === 0) throw new Error(`${date} 这天没有发文行`);
  const nth = Number(args.nth ?? (same.length === 1 ? 1 : NaN));
  if (!Number.isFinite(nth)) throw new Error(`${date} 这天有 ${same.length} 行,请说清是第几篇(nth)`);
  const hit = same[Math.trunc(nth) - 1];
  if (!hit) throw new Error(`${date} 这天只有 ${same.length} 行,没有第 ${nth} 篇`);
  return hit.localKey;
}
