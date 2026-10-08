// Derivation layer for the plan's "Why this layout" -- turns the backend's scheduling rationale into plain language.
//
// Pure functions live here rather than in the component file: PublishingPlanEditor.tsx is bound by react-refresh rules
// to export only components (planRow.ts / planWeeks.ts in the same directory are handled the same way).
//
// ## Three levels of evidence badges (the backbone of the whole feature)
//
//   Decided at the time   persisted to DB at scheduling time, doesn't change over time -- plan.overview.plan_basis, why/origin on rows
//   Recomputed now        computed by running on current data -- quotas/shares/costs from source-insights
//   Scheduling rule       no row-level evidence, can only describe what the algorithm does
//
// Legacy plans have no snapshot -> related entries fall back to "recomputed now"; newly laid-out plans -> upgraded to "decided at the time".
// Components need no change; only the badge color changes.
//
// ## What this version can't explain (don't pretend otherwise in the copy)
//
// 1. Legacy plans can't recover the original quota numbers, only today's recomputation
// 2. Template "hit/fallback" for legacy rows is judged by today's template config; misjudged if templates changed
// 3. Resource rotation slots for legacy rows can't be reconstructed
// 4. Can't explain the motive for manual edits (the change log only records field/before/after)
// 5. Authorization status can change the "recomputed now" part on the spot (account logged out -> explanation suddenly becomes cold start)

import type {
  ExecutionPlan, PlanBasis, PlanRowWhy, PlatformQuota, PublishPlanItem, SourceInsights,
} from '../../services/adminReviewApi';
import { PAID_CHANNELS } from './planRow';
import { todayISO } from './planWeeks';

export type ReasonKind = 'settled' | 'recomputed' | 'rule';

export interface Reason {
  kind: ReasonKind;
  title: string;
  /** Main explanation, plain language, no internal codes */
  text: string;
  /** Supplementary details (one per line) */
  bullets?: string[];
  /** Staff only: internal metrics like resource IDs, rotation slots, unit prices */
  staffOnly?: boolean;
  /** Recompute time for "recomputed now" entries */
  asOf?: string;
  /** warn renders in the warning color */
  level?: 'info' | 'warn';
}

export const KIND_LABEL: Record<ReasonKind, string> = {
  settled: '排期存档',
  recomputed: '实时重算',
  rule: '排期规则',
};

export const KIND_COLOR: Record<ReasonKind, string> = {
  settled: '#10b981',
  recomputed: '#eab308',
  rule: '#94a3b8',
};

export const KIND_HINT: Record<ReasonKind, string> = {
  settled: '排期当时留存的判定,不随后续数据变化',
  recomputed: '按当前数据实时重算,回答「按当前数据衡量,该排期是否仍然成立」',
  rule: '该项无行级证据留存,仅说明排期算法的处理规则',
};

// ── Basic data access ────────────────────────────────────────────────────────────

export function planBasisOf(plan: ExecutionPlan | null | undefined): PlanBasis | null {
  const raw = (plan?.overview as Record<string, unknown> | undefined)?.plan_basis;
  return raw && typeof raw === 'object' ? (raw as PlanBasis) : null;
}

export function startOptionsOf(plan: ExecutionPlan | null | undefined): Record<string, unknown> | null {
  const raw = (plan?.overview as Record<string, unknown> | undefined)?.start_options;
  return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null;
}

/** Infer articles per day from the plan (the day with the most rows; 5 when there are no plan rows) */
export function perDayOf(items: PublishPlanItem[]): number {
  const counts = new Map<string, number>();
  for (const it of items) {
    const d = String(it.publish_date || '').slice(0, 10);
    if (d) counts.set(d, (counts.get(d) || 0) + 1);
  }
  if (counts.size === 0) return 5;
  return Math.max(1, Math.min(10, Math.max(...counts.values())));
}

const fmtDate = (iso?: string | null): string =>
  iso ? new Date(iso).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' }) : '';

const fmtDateTime = (iso?: string | null): string =>
  iso ? new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric',
                                                hour: '2-digit', minute: '2-digit' }) : '';

const SOURCE_LABEL: Record<string, string> = {
  seeds: '按种子提示词展开',
  strategy: '按战略方案的关键词体系展开',
};
const PLATFORM_MODE_LABEL: Record<string, string> = {
  sources: '投放平台交给实测被引信源决定',
  template: '投放平台沿用模板自带的适用平台',
};

// ── notes translation ──────────────────────────────────────────────────────────

interface NoteExplain { title: string; why: string; action?: string; level: 'info' | 'warn' }

// Backend notes are one human-readable sentence; here we add "so what / what to do".
// Match by code (the backend has structured them); old data without code falls back to prefix matching; if nothing matches, pass through as-is.
const NOTE_BY_CODE: Record<string, NoteExplain> = {
  aitoearn_no_account: {
    title: '自有账号通道无可用账号',
    why: '发文通道已配置,但没有已授权的图文账号,本轮仅能使用自有站点与付费代发。',
    action: '前往发布中心授权账号,重排后即可纳入配额。',
    level: 'warn',
  },
  aitoearn_unconfigured: {
    title: '自有账号通道尚未接入',
    why: '尚未配置自有账号发文通道,本轮仅使用自有站点与付费代发。',
    level: 'info',
  },
  aitoearn_fetch_failed: {
    title: '拉取自有账号失败',
    why: '本次未能获取账号列表,排期仅使用自有渠道 —— 不代表账号本身异常。',
    action: '重排一次即可重试。',
    level: 'warn',
  },
  rwj_lib_empty: {
    title: '付费媒体资源库尚未同步',
    why: '资源库为空,无法为付费渠道匹配媒体,本轮全部排入自有渠道。',
    level: 'warn',
  },
  no_external_channel: {
    title: '暂无可投放的站外渠道',
    why: '自有账号未授权、付费资源库亦无内容,只能全部排入官网博客。'
       + '内容仍可被 AI 抓取,但覆盖面窄于多渠道投放。',
    action: '授权任一站外账号或同步付费资源库,重排后覆盖面将明显扩大。',
    level: 'warn',
  },
  cold_start: {
    title: '暂无实测被引数据,按冷启动顺序排期',
    why: '跑批中尚无可投渠道被引用的记录,无法按占比分配,先按经验顺序铺设一轮。'
       + '待本批内容发布、跑批积累数据后,重排一次即切换为按实测占比分配。',
    level: 'info',
  },
  paid_capped: {
    title: '付费渠道占比已封顶',
    why: '实测分布中付费平台占比偏高,若不封顶,多数篇目将产生费用;'
       + '超出上限的篇目已改投自有渠道。',
    level: 'info',
  },
  all_below_min_share: {
    title: '各渠道占比均未达标,保留被引最高者',
    why: '被引分布过于分散,无一渠道达到门槛,故保留被引最多者兜底,'
       + '避免无法生成排期。',
    level: 'info',
  },
};

const NOTE_PREFIXES: [string, string][] = [
  ['aitoearn 已配置但没有已授权', 'aitoearn_no_account'],
  ['aitoearn 通道未配置', 'aitoearn_unconfigured'],
  ['aitoearn 账号拉取失败', 'aitoearn_fetch_failed'],
  ['代发资源库还没同步', 'rwj_lib_empty'],
  ['没有可用的外部渠道', 'no_external_channel'],
  ['实测信源里没有可投渠道', 'cold_start'],
  ['付费渠道占比已封顶', 'paid_capped'],
  ['所有被引渠道占比都不到', 'all_below_min_share'],
];

/** Turn a note into {title, why, what to do}; pass through unchanged if unrecognized, never swallow it */
export function explainQuotaNote(text: string, code?: string): NoteExplain {
  const key = code && code !== 'other'
    ? code
    : NOTE_PREFIXES.find(([p]) => text.startsWith(p))?.[1];
  const hit = key ? NOTE_BY_CODE[key] : undefined;
  return hit || { title: text, why: '', level: 'info' };
}

/** Normalize notes / notes_struct into a list with codes (old data has plain text only) */
export function quotaNotes(quota?: PlatformQuota | null): { code: string; text: string }[] {
  if (!quota) return [];
  if (quota.notes_struct?.length) {
    return quota.notes_struct.map(n => ({ code: n.code, text: n.text }));
  }
  return (quota.notes || []).map(text => ({ code: '', text }));
}

// ── Layer A: the whole plan ──────────────────────────────────────────────────────

function quotaReasons(quota: PlatformQuota, kind: ReasonKind, asOf?: string): Reason[] {
  const out: Reason[] = [];
  const rows = quota.breakdown || [];
  const measured = quota.measured || [];
  if (rows.length > 0) {
    const total = quota.responses_total;
    const top = measured.slice(0, 4)
      .map(m => `${m.platform} ${m.share_pct}%(${m.citations} 次)`).join('、');
    const alloc = rows.map(r => `${r.platform} ${r.slots} 篇`).join('、');
    out.push({
      kind,
      title: '投放平台的选取依据',
      text: (total ? `跑批里 AI 一共引用了 ${total} 次外部内容。` : '')
        + (top ? `被引用最多的是${top} —— 即 AI 回答该行业问题时主要参考的来源。`
               : '')
        + `按能投的那部分折算成每天的篇数:${alloc}。`,
      asOf,
    });
  }
  const excluded = quota.excluded || [];
  if (excluded.length > 0) {
    const thr = quota.thresholds?.min_share_pct ?? 8;
    out.push({
      kind,
      title: `未纳入排期的渠道(${excluded.length} 个)`,
      text: `以上渠道虽被引用,但占比不足 ${thr}%,按长尾噪音处理,未纳入分配 ——`
        + '否则高度集中的被引分布会被摊薄成平均排期,反而稀释重点渠道。',
      bullets: excluded.map(e => `${e.platform} ${e.share_pct}%(${e.citations} 次)`),
      asOf,
    });
  }
  const moves = quota.paid_cap_moves || [];
  if (moves.length > 0) {
    const cap = Math.round((quota.thresholds?.paid_max_share ?? 0.6) * 100);
    out.push({
      kind,
      title: '付费渠道占比封顶后的篇数调整',
      text: `付费渠道占比上限为 ${cap}%,超出部分按被引权重由低到高改投自有渠道 ——`
        + '若不封顶,每日多数篇目将进入付费渠道,月度成本会显著上升。',
      bullets: moves.map(m => `${m.from} → ${m.to}:${m.slots} 篇`),
      asOf,
    });
  }
  const unavail = quota.unavailable || [];
  if (unavail.length > 0) {
    out.push({
      kind,
      title: `已被引用但当前不可投放的渠道(${unavail.length} 个)`,
      text: '既无已授权账号,付费资源库中也没有价格上限内的在架资源。'
        + '完成授权后重排一次,即可纳入配额。',
      bullets: unavail.map(u => `${u.platform}(被引 ${u.citations} 次)`),
      level: 'warn',
      asOf,
    });
  }
  const cost = quota.cost;
  if (cost && (cost.per_day_yuan > 0 || cost.paid_slots > 0)) {
    out.push({
      kind,
      title: '预计投放成本',
      text: `每日约 ¥${cost.per_day_yuan},每月约 ¥${cost.per_month_yuan};`
        + `其中 ${cost.paid_slots} 篇走付费代发,${cost.free_slots} 篇走自有渠道、不产生费用。`
        + `${cost.disclaimer}。`,
      asOf,
    });
  }
  return out;
}

/** Why the whole plan looks the way it does */
export function planReasons(
  plan: ExecutionPlan, insights?: SourceInsights | null,
): Reason[] {
  const out: Reason[] = [];
  const basis = planBasisOf(plan);
  const opts = (basis?.options as Record<string, unknown> | undefined) || startOptionsOf(plan);

  // 1. Scheduling metric definition
  if (opts) {
    const perDay = Number(opts.per_day || 0);
    const months = Number(opts.months || 1);
    const src = String(opts.source || 'seeds');
    const mode = String(opts.platform_mode || 'sources');
    const covered = Array.isArray(opts.coverage_queries) ? opts.coverage_queries.length : 0;
    const parts = [
      months ? `铺 ${months} 个月` : '',
      perDay ? `每天 ${perDay} 篇` : '',
      SOURCE_LABEL[src] || '',
      PLATFORM_MODE_LABEL[mode] || '',
    ].filter(Boolean);
    out.push({
      kind: 'settled',
      title: '排期口径',
      text: `项目启动时确定:${parts.join('、')}。`
        + (covered ? `内容覆盖锁定于选定的 ${covered} 个提示词。` : '')
        + '以上口径在启动时即写入计划,后续调整排期不会改变。',
    });
  } else {
    out.push({
      kind: 'rule',
      title: '本计划未留存排期口径',
      text: '本计划排期于系统开始记录启动配置之前。可确认的是:按模板自带的适用平台铺设,'
        + '每日固定篇数铺满一个月。如需完整排期依据,执行一次「按信源重排计划」即可。',
    });
  }

  // 2. Quota: prefer the original snapshot; fall back to recomputing now only without one (and say it's recomputed)
  const snapQuota = basis?.quota;
  if (snapQuota) {
    out.push(...quotaReasons(snapQuota, 'settled', basis?.built_at));
  } else if (insights?.quota) {
    out.push(...quotaReasons(insights.quota, 'recomputed', insights.computed_at || undefined));
  }

  // 3. Adjustments made during scheduling (notes)
  const notes = quotaNotes(snapQuota || insights?.quota);
  for (const n of notes) {
    const ex = explainQuotaNote(n.text, n.code);
    if (!ex.why) continue;          // Unrecognized notes are shown as-is by the panel; don't repeat here
    out.push({
      kind: snapQuota ? 'settled' : 'recomputed',
      title: ex.title,
      text: ex.why + (ex.action ? ` ${ex.action}` : ''),
      level: ex.level,
      asOf: snapQuota ? basis?.built_at : insights?.computed_at || undefined,
    });
  }

  // 4. Seed source
  if (basis?.seed_source?.kind) {
    const s = basis.seed_source;
    out.push({
      kind: 'settled',
      title: '种子提示词来源',
      text: (s.kind === 'strategy'
        ? `种子提示词取自战略方案的关键词体系,共 ${s.seed_count || 0} 组,按天轮换 ——`
        : `种子提示词取自已批准的种子词,共 ${s.seed_count || 0} 组,按天轮换 ——`)
        + '每个种子占用一天,当日全部篇目围绕该种子撰写,轮换完毕后循环。',
    });
  }

  // 5. Which prompts are covered, and how many are weak spots
  const items = plan.publishing_plan || [];
  if (items.length > 0) {
    const covered = new Set<string>();
    let zero = 0;
    for (const it of items) {
      for (const q of it.queries || []) covered.add(q);
      if ((it.coverage_pct ?? 0) === 0) zero += 1;
    }
    const total = (plan.monitored_queries || []).length;
    out.push({
      kind: 'recomputed',
      title: '本轮覆盖的提示词',
      text: (total ? `本项目共监测 ${total} 个问题,` : '')
        + `本计划覆盖其中 ${covered.size} 个。`
        + (total > covered.size
          ? '监测范围与内容覆盖为两套口径:未覆盖的问题继续监测,仅本轮不安排撰写。' : '')
        + (zero ? `另有 ${zero} 篇对应的问题至今未被 AI 引用,属覆盖短板,已优先安排。` : ''),
      asOf: insights?.computed_at || undefined,
    });
  }

  // 6. Template pool
  if (basis?.templates?.length) {
    out.push({
      kind: 'settled',
      title: '使用的模板池',
      text: `模板池共 ${basis.templates.length} 个,按天轮换;存在实测平台配额时优先选用`
        + '适用平台匹配的模板,无匹配时才按轮换顺位排入。',
      bullets: basis.templates.map(t => `${t.name}${t.kind ? `(${t.kind})` : ''}`
        + (t.platforms?.length ? ` · 适用 ${t.platforms.join('、')}` : '')),
    });
  }

  // 7. Past replans
  if (basis?.history?.length) {
    out.push({
      kind: 'settled',
      title: '历次重排记录',
      text: '每次重排都会重新计算配额,因此不同时间排入的行,依据可能不同。',
      bullets: basis.history.map(h =>
        `${fmtDate(h.built_at)} ${TRIGGER_LABEL[h.trigger || ''] || h.trigger || ''}`
        + (h.headline ? ` · ${h.headline}` : '')),
    });
  }

  return out;
}

export const TRIGGER_LABEL: Record<string, string> = {
  start: '项目启动铺表',
  generate_items: '按信源重排',
  refill: '每月自动续期',
  lazy_seed: '按模板默认铺表',
};

// ── Consistency self-check: as scheduled vs. current data ────────────────────────────────

export interface QuotaDrift {
  state: 'aligned' | 'drifted' | 'unknown';
  text: string;
  rows?: { platform: string; planned: number; suggested: number }[];
}

/** Actual schedule from today vs. quotas computed from current data -- surfaces "the data changed" */
export function quotaDrift(
  plan: ExecutionPlan, insights?: SourceInsights | null,
): QuotaDrift {
  const live = insights?.quota?.breakdown || [];
  if (live.length === 0) return { state: 'unknown', text: '暂无可比对的实测配额。' };
  const today = todayISO();   // Must use the local calendar day -- the UTC version is yesterday before 08:00 in UTC+8
  const future = (plan.publishing_plan || [])
    .filter(it => String(it.publish_date || '').slice(0, 10) >= today);
  if (future.length === 0) {
    return { state: 'unknown', text: '今日起尚无排期,无法比对。' };
  }
  const days = new Set(future.map(it => String(it.publish_date).slice(0, 10))).size || 1;
  const actual = new Map<string, number>();
  for (const it of future) {
    const p = it.platform || '';
    if (p) actual.set(p, (actual.get(p) || 0) + 1);
  }
  const rows = live.map(r => ({
    platform: r.platform,
    planned: Math.round(((actual.get(r.platform) || 0) / days) * 10) / 10,
    suggested: r.slots,
  }));
  const bad = rows.filter(r => Math.abs(r.planned - r.suggested) >= 1);
  if (bad.length === 0) {
    return { state: 'aligned', text: '今日起的排期与当前实测配额一致。', rows };
  }
  return {
    state: 'drifted',
    text: '当前实测数据已与排期当时不一致。差异可能来自数据本身变化,'
      + '也可能来自人工调整排期。如需按最新数据重排,请使用「信源分析」页的「按信源重排计划」。',
    rows: bad,
  };
}

// ── Layer B: a single day ────────────────────────────────────────────────────────

export function dayReasons(
  dayItems: PublishPlanItem[], insights?: SourceInsights | null,
): Reason[] {
  if (dayItems.length === 0) return [];
  const out: Reason[] = [];

  const byPlatform = new Map<string, number>();
  for (const it of dayItems) {
    const p = it.platform || '未定';
    byPlatform.set(p, (byPlatform.get(p) || 0) + 1);
  }
  const compo = [...byPlatform.entries()].map(([p, n]) => `${p} ${n} 篇`).join('、');
  const live = insights?.quota?.breakdown || [];
  const same = live.length > 0 && live.every(
    r => (byPlatform.get(r.platform) || 0) === r.slots);
  out.push({
    kind: 'settled',
    title: `当日排期 ${dayItems.length} 篇`,
    text: `平台构成:${compo}。`
      + (live.length === 0 ? ''
        : same ? '与当前实测配额一致。'
              : '与当前实测配额存在差异 —— 排期当时的数据与现在不同,或经过人工调整。'),
  });

  const seeds = [...new Set(dayItems.map(it => (it.seed || '').trim()).filter(Boolean))];
  if (seeds.length > 0) {
    const tier = dayItems.find(it => it.why?.seed?.tier)?.why?.seed?.tier;
    out.push({
      kind: 'rule',
      title: '当日主题来源',
      text: seeds.length === 1
        ? `当日轮换至种子提示词「${seeds[0]}」。种子按天轮换,每个种子占用一天,`
          + '当日全部篇目围绕该种子撰写。'
          + (tier ? `该种子取自战略方案的「${tier}」。` : '')
        : `当日涉及 ${seeds.length} 个种子提示词:${seeds.join('、')}。`,
    });
  }

  const qs = new Set<string>();
  let zero = 0;
  for (const it of dayItems) {
    for (const q of it.queries || []) qs.add(q);
    if ((it.coverage_pct ?? 0) === 0) zero += 1;
  }
  if (qs.size > 0) {
    out.push({
      kind: 'recomputed',
      title: '当日覆盖的提示词',
      text: `合计覆盖 ${qs.size} 个提示词`
        + (zero ? `,其中 ${zero} 篇对应的问题命中率仍为 0%,属覆盖短板,建议重点跟进。` : '。'),
      asOf: insights?.computed_at || undefined,
    });
  }

  const paid = dayItems.filter(it => PAID_CHANNELS.includes(it.channel || '')).length;
  if (paid > 0) {
    out.push({
      kind: 'settled',
      title: '当日投放成本',
      text: `${paid} 篇走付费代发,其余 ${dayItems.length - paid} 篇走自有渠道、不产生费用。`,
      staffOnly: true,
    });
  }
  return out;
}

// ── Layer C: a single row ────────────────────────────────────────────────────────

const MANUAL_TEXT = '本项为人工指定,非系统排期结果。';

function manualSuffix(w?: { prev_reason?: string | null; prev_value?: unknown }): string {
  if (!w?.prev_value) return MANUAL_TEXT;
  return `${MANUAL_TEXT}排期时系统给出的是「${String(w.prev_value)}」。`;
}

export interface RowContext {
  /** All rows of the day, used to compute "nth article of the day" */
  dayItems?: PublishPlanItem[];
  insights?: SourceInsights | null;
}

export function rowReasons(item: PublishPlanItem, ctx: RowContext = {}): Reason[] {
  const why: PlanRowWhy = item.why || {};
  const out: Reason[] = [];
  const asOf = ctx.insights?.computed_at || undefined;

  // Slot
  const day = ctx.dayItems || [];
  if (day.length > 1) {
    const idx = [...day].sort((a, b) => a.seq - b.seq)
      .findIndex(x => x.id === item.id);
    if (idx >= 0) {
      out.push({
        kind: 'rule',
        title: '位次',
        text: `当日共排 ${day.length} 篇,本篇位列第 ${idx + 1} 篇。`
          + '当日篇数按平台配额顺序分配,各位次归属哪个平台由配额决定。',
      });
    }
  }

  // Platform
  const p = why.platform;
  if (p) {
    if (p.reason === 'manual') {
      out.push({ kind: 'settled', title: '平台', text: manualSuffix(p) });
    } else if (p.reason === 'source_quota') {
      out.push({
        kind: 'settled', title: '平台',
        text: `${item.platform} 在实测被引里占 ${p.share_pct ?? '—'}%`
          + (p.citations ? `(${p.citations} 次)` : '')
          + (p.day_slots ? `,当日分配 ${p.day_slots} 篇。` : '。'),
      });
    } else if (p.reason === 'paid_cap_fallback') {
      out.push({
        kind: 'settled', title: '平台',
        text: `${item.platform} 本身未被引用 —— 本篇为付费渠道占比封顶后改投至此,`
          + '避免超预算投放付费渠道。',
      });
    } else if (p.reason === 'cold_start') {
      out.push({
        kind: 'settled', title: '平台',
        text: '本行排期时尚无实测被引数据,按冷启动的经验顺序排入。',
      });
    } else if (p.reason === 'template_default') {
      out.push({
        kind: 'settled', title: '平台',
        text: '本行未走实测配额,采用模板自带的适用平台。',
      });
    } else if (p.reason === 'media_recommendation') {
      out.push({
        kind: 'settled', title: '平台',
        text: `${item.platform} 来自信源策略的投放媒体建议 —— 该媒体在实测被引数据里`
          + '有表现,由人工从优化任务中心转入本计划。',
      });
    } else if (p.reason === 'legacy_fallback') {
      out.push({
        kind: 'rule', title: '平台',
        text: '本行来自早期排期路径,未留存平台选择依据。',
      });
    }
  } else if (item.platform) {
    // Legacy rows have no why: fall back to "judged by today's template config"
    const fit = (item.suggested_platforms || []).includes(item.platform);
    out.push({
      kind: 'recomputed', title: '平台',
      text: fit
        ? `${item.platform} 属于该模板的适用平台。本行排期时未留存依据,`
          + '此判定按当前模板配置得出。'
        : `${item.platform} 不属于该模板的适用平台。本行排期时未留存依据,`
          + '此判定按当前模板配置得出 —— 模板若经修改,判定可能失真。',
      asOf,
    });
  }

  // Template
  const t = why.template;
  if (t?.reason === 'manual') {
    out.push({ kind: 'settled', title: '模板', text: manualSuffix(t) });
  } else if (t?.reason === 'platform_match') {
    out.push({
      kind: 'settled', title: '模板',
      text: `「${item.template_name || '—'}」的适用平台包含${t.matched_platform || item.platform},`
        + '系按平台反查匹配选中,非轮换顺位排入。',
    });
  } else if (t?.reason === 'rotation') {
    out.push({
      kind: 'settled', title: '模板',
      text: t.wanted_platform
        ? `模板池中当时无适配${t.wanted_platform}的模板,「${item.template_name || '—'}」`
          + '按轮换顺位排入 —— 此类组合建议人工复核。'
        : `「${item.template_name || '—'}」按模板池轮换顺位排入。`,
      level: t.wanted_platform ? 'warn' : 'info',
    });
  }

  // Writing preferences
  const c = why.combo;
  if (c?.reason === 'kind_map') {
    out.push({
      kind: 'settled', title: '创作方向 / 文案类型',
      text: `由模板种类「${c.kind}」自动映射,亦可在本行单独指定。`,
    });
  } else if (c?.reason === 'rotation') {
    out.push({
      kind: 'settled', title: '创作方向 / 文案类型',
      text: '该模板种类无对应的默认搭配,按轮换分配 —— 如需固定,可在本行手动选择。',
    });
  }

  // Prompts
  const q = why.queries;
  const qn = (item.queries || []).length;
  if (q?.reason === 'manual') {
    out.push({ kind: 'settled', title: '提示词', text: MANUAL_TEXT });
  } else if (q?.reason === 'stride_split') {
    out.push({
      kind: 'settled', title: '提示词',
      text: `当日问题按篇数轮流切分,本篇分配 ${qn} 条。`,
    });
  } else if (q?.reason === 'reuse_rotate') {
    out.push({
      kind: 'settled', title: '提示词',
      text: `当日仅有 ${q.day_total ?? '若干'} 个问题,需撰写 ${q.per_day ?? '若干'} 篇,`
        + '本篇为轮转复用 —— 同一问题更换模板与切入角度再写一篇属常规打法,留空才是覆盖遗漏。',
    });
  } else if (q?.reason === 'backflow_unhit') {
    out.push({
      kind: 'settled', title: '提示词',
      text: '本行由「至今未被 AI 引用的问题」自动回流生成,用于补齐覆盖短板。',
    });
  } else if (q?.reason === 'none') {
    out.push({
      kind: 'settled', title: '提示词',
      text: '本篇未绑定提示词,仅按种子泛写 —— 发布后无法定向复测效果,建议补充绑定。',
      level: 'warn',
    });
  }

  // Outlet (staff only)
  const m = why.media;
  if (m?.reason === 'rwj_rotation') {
    out.push({
      kind: 'settled', title: '投放媒体',
      text: `同平台 ${m.pool ?? '若干'} 个候选按出稿率、价格排序,当日轮换至第 ${m.rank ?? '—'} 个。`
        + '轮换用于避免整月稿件集中投放同一账号,降低收录风险。'
        // publish_rate from the backend is already a percentage (100.0 = 100%); don't multiply by 100 again
        + (m.publish_rate != null ? ` 该号出稿率 ${Math.round(m.publish_rate)}%。` : '')
        + (m.price_yuan != null ? ` 参考价 ¥${m.price_yuan}。` : ''),
      staffOnly: true,
    });
  } else if (m?.reason === 'manual') {
    out.push({ kind: 'settled', title: '投放媒体', text: MANUAL_TEXT, staffOnly: true });
  }

  // Source
  const s = why.seed;
  const origin = item.origin;
  if (s?.reason === 'strategy_tier') {
    out.push({
      kind: 'settled', title: '本行来源',
      text: `取自战略方案的「${s.tier || '关键词层'}」。`,
    });
  } else if (s?.reason === 'backflow_unhit' || origin?.type === 'unhit_query') {
    out.push({
      kind: 'settled', title: '本行来源',
      text: '由「至今未被 AI 引用的问题」自动回流排入。',
    });
  } else if (s?.reason === 'candidate_insert' || origin?.type === 'manual') {
    out.push({
      kind: 'settled', title: '本行来源',
      text: '由人工从候选选题中插入,非自动排期生成。',
    });
  } else if (s?.reason === 'approved_seed') {
    out.push({
      kind: 'settled', title: '本行来源',
      text: '取自已批准的种子提示词,按天轮换排入当日。',
    });
  }

  if (out.length === 0) {
    out.push({
      kind: 'rule', title: '本行无排期依据',
      text: '本行为人工添加,或排期于系统开始记录依据之前。'
        + '在「信源分析」页重排一次后,新排入的行即会带上依据。',
    });
  }
  return out;
}

/** Very short explanation for the table row (≤20 chars); the full reason goes in title */
export function rowWhyShort(item: PublishPlanItem): string {
  const w = item.why;
  if (!w) return '人工添加 / 无依据';
  if (w.platform?.reason === 'manual' || w.template?.reason === 'manual') return '人工指定';
  if (w.platform?.reason === 'source_quota' && w.platform.share_pct != null) {
    return `被引 ${w.platform.share_pct}%,按占比排`;
  }
  if (w.platform?.reason === 'paid_cap_fallback') return '付费封顶,改投自有';
  if (w.platform?.reason === 'cold_start') return '冷启动默认顺序';
  if (w.platform?.reason === 'media_recommendation') return '投放媒体建议转入';
  if (w.template?.reason === 'platform_match') return '模板适配该平台';
  if (w.template?.reason === 'rotation') return '模板轮换顺位';
  if (w.seed?.reason === 'backflow_unhit') return '未命中问题回流';
  if (w.seed?.reason === 'strategy_tier') return `策略 · ${w.seed.tier || ''}`;
  return '按排期规则';
}

export const fmtWhen = fmtDateTime;
