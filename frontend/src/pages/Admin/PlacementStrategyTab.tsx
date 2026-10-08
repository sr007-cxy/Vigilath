// Plan "Placement strategy" tab -- this page is upstream of the publish table.
//
// Top half: which sources each of the five major AI engines trusts -> where this brand should place (priority), what (checklist),
//       how to write (content format), and at what cadence. Only clicking "Replan by strategy" writes to the publish table.
// Bottom half: why the plan laid out by this strategy looks the way it does (the former "Why this layout" block moved here,
//       component unchanged, just attached after the conclusion as evidence).
//
// The strategy is computed deterministically (backend services/placement_strategy.py, no LLM):
// the same measured data + the same knowledge-base version always yield the same strategy, which is what makes
// "change the strategy and subsequent plans change" hold true.

import { useState } from 'react';
import { useRegisterAgentActions } from '../../components/AgentChat/agentActions';
import type { ExecutionPlan, PlacementStrategy, SourceInsights,
              StrategyChecklistItem, StrategyPlatformPlan } from '../../services/adminReviewApi';
import { adminReviewApi } from '../../services/adminReviewApi';
import { SectionCard } from '../../components/ui/SectionCard';
import { LoadingBlock } from '../../components/ui/Loading';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { PlanWhyTab } from './PlanWhyTab';
import { fmtWhen } from './planWhy';
import {
  INFRA_KIND_LABEL, TIER_COLOR, TIER_LABEL, kbChanged, plannedByPlatform,
  strategyDrift, strategySnapshotOf, usePlacementStrategy,
} from './placementStrategy';

export function PlacementStrategyTab({
  topicId, plan, insights, insightsLoading, isStaff, canAct, perDay,
  onGoSources, onRegenerated,
}: {
  topicId: number;
  plan: ExecutionPlan;
  insights: SourceInsights | null;
  insightsLoading: boolean;
  isStaff: boolean;
  /** Whether this plan can be modified (false for the client read-only view / non-ops users) */
  canAct: boolean;
  perDay: number;
  onGoSources: () => void;
  onRegenerated?: (updated: ExecutionPlan) => void;
}) {
  const token = localStorage.getItem('token') || '';
  const { data: live, loading, err, reload } = usePlacementStrategy(topicId, perDay);
  const snapshot = strategySnapshotOf(plan);
  const drift = strategyDrift(snapshot, live);
  const planned = plannedByPlatform(plan);
  const [busy, setBusy] = useState(false);

  // ── Agent page actions (batch 1: read-only). Replan by strategy (handleReplan) is an irreversible write; register it in batch 2.
  useRegisterAgentActions({
    page: 'admin_placement_strategy',
    scope: 'strategy',
    weight: 20,
    describe: '执行计划书 · 投放策略页(引擎优先级 / 各引擎投放清单,按此刻数据现算)',
    snapshot: {
      是否现算: true,
      每天篇数口径: perDay,
      与计划的偏差: drift?.text || '',
      引擎优先级: (live?.engine_priority || []).slice(0, 6).map((e) => ({
        引擎: e.engine, 排名: e.rank, 命中率: e.hit_rate_pct,
      })),
      各引擎待办数: (live?.platform_plan || []).slice(0, 6).map((p2) => ({
        引擎: p2.engine, 可投: (p2.publishable || []).length, 基建: (p2.infra || []).length,
      })),
      已排平台: planned,
    },
    selfConfirm: ['replanByStrategy'],
    actions: {
      reload: () => { void reload(); },
      goSources: () => onGoSources(),
      // Irreversible write: replan by placement strategy. handleReplan has its own uiConfirm; reused as-is.
      // selfConfirm: tells the chat window not to show the generic confirmation again (see agentActions.selfConfirm).
      ...(canAct ? { replanByStrategy: () => { void handleReplan(); } } : {}),
    },
  });

  const handleReplan = async () => {
    if (busy) return;
    if (!(await uiConfirm(
      '按当前投放策略重排发文表?仅调整今日及以后的排期,今日之前的历史行原样保留。' +
      '今日起已出稿的文章不会删除,但与原计划行的关联会断开;' +
      '重排后需再次确认计划,方可为新行生成文章。' +
      '策略中无法排入发文表的事项(百科词条、账号认证等)将推送至优化任务中心。',
      { title: '按策略重排(今日起)', okText: '重排' }))) return;
    setBusy(true);
    try {
      const updated = await adminReviewApi.generatePlanItems(topicId, {
        source: 'seeds', per_day: perDay, template_ids: [],
        platform_mode: 'strategy',
      }, token);
      toast.success('已按投放策略重排今日及以后的发文表');
      onRegenerated?.(updated);
      await reload();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {loading && !live && (
        <LoadingBlock label="正在计算投放策略…"
                      hint="汇总各引擎实测被引数据,比对信源口径" />
      )}

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {live && (
        <>
          <StrategyHeader live={live} drift={drift} snapshot={snapshot}
                          canAct={canAct} busy={busy} onReplan={handleReplan} />
          <Diagnosis live={live} />
          <EnginePriority live={live} />
          <PlatformPlan live={live} planned={planned} />
          <ContentForms live={live} />
          <Cadence live={live} />
        </>
      )}

      {/* ── Bottom half: the plan laid out by this strategy -- why these platforms and these article counts ── */}
      <div className="pt-2" style={{ borderTop: '1px solid var(--border-color)' }}>
        <div className="text-xs text-muted mb-3 leading-relaxed">
          以上为<b>策略结论</b>(投放方向),以下为<b>排期依据</b>
          (按该策略排出的计划,为何是这些平台与篇数)。
        </div>
        <PlanWhyTab plan={plan} insights={insights} insightsLoading={insightsLoading}
                    isStaff={isStaff} onGoSources={onGoSources} />
      </div>
    </div>
  );
}

// ── Top: metric definition + consistency + replan action ──────────────────────────────────────

function StrategyHeader({
  live, snapshot, drift, canAct, busy, onReplan,
}: {
  live: PlacementStrategy;
  snapshot: PlacementStrategy | null;
  drift: ReturnType<typeof strategyDrift>;
  canAct: boolean;
  busy: boolean;
  onReplan: () => void;
}) {
  const warn = drift.state === 'drifted' || drift.state === 'none';
  return (
    <div className="space-y-2">
      <div className="rounded-md p-2.5 text-[11px] leading-relaxed"
           style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)',
                    border: '1px solid var(--border-color)' }}>
        本策略<b>按当前数据实时计算</b>
        {live.computed_at ? `(计算于 ${fmtWhen(live.computed_at)})` : ''} ——
        由信源知识库(各 AI 引擎认可的信源口径)叠加本项目实测被引数据,按规则推导得出,
        无大模型参与:相同数据恒定得出相同策略。
        {snapshot?.computed_at
          ? ` 排期时采用的策略版本定于 ${fmtWhen(snapshot.computed_at)}。`
          : ''}
      </div>

      <div className="rounded-md p-3 text-xs leading-relaxed flex items-start gap-3 flex-wrap"
           style={{
             background: warn ? 'rgba(234,179,8,0.10)' : 'var(--bg-secondary)',
             color: warn ? 'var(--warn-fg)' : 'var(--text-secondary)',
             border: `1px solid ${warn ? 'rgba(234,179,8,0.3)' : 'var(--border-color)'}`,
           }}>
        <div className="flex-1 min-w-[16rem]">
          {drift.state === 'aligned' && '✓ '}
          {warn && '⚠️ '}
          {drift.text}
          {drift.rows && drift.rows.length > 0 && (
            <div className="mt-1">
              {drift.rows.map(r => (
                <span key={r.name} className="mr-3">
                  {r.name}:第 {r.was} → 第 {r.now}
                </span>
              ))}
            </div>
          )}
          {kbChanged(snapshot, live) && (
            <div className="mt-1">
              信源口径亦已更新 —— 排期时采用的是旧版口径,重排一次方可切换至新口径。
            </div>
          )}
        </div>
        {canAct && (
          <button type="button" onClick={onReplan} disabled={busy}
                  className="text-xs px-3 py-1.5 rounded-md text-white shrink-0"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.6 : 1 }}>
            {busy ? '重排中…' : '↻ 按策略重排计划(今日起)'}
          </button>
        )}
      </div>

      {(live.notes || []).map((n, i) => (
        <div key={i} className="rounded-md p-2.5 text-xs leading-relaxed"
             style={{
               background: n.level === 'warn' ? 'rgba(234,179,8,0.10)' : 'var(--bg-secondary)',
               color: n.level === 'warn' ? 'var(--warn-fg)' : 'var(--text-secondary)',
               border: `1px solid ${n.level === 'warn'
                 ? 'rgba(234,179,8,0.3)' : 'var(--border-color)'}`,
             }}>
          {n.text}
        </div>
      ))}
    </div>
  );
}

// ── Step 1: where things stand now ─────────────────────────────────────────────────

function Diagnosis({ live }: { live: PlacementStrategy }) {
  const d = live.diagnosis;
  return (
    <SectionCard
      title="现状诊断"
      hint={`跑批共 ${d.responses_total} 条应答,其中 ${d.own_cited_responses} 条引用了自有内容。`
        + `行业口径:${live.industry.name}${live.industry.matched ? '' : '(未匹配)'}`}
    >
      {live.industry.core && (
        <div className="text-xs mb-3 leading-relaxed text-secondary">{live.industry.core}</div>
      )}
      <div className="overflow-x-auto">
        <table className="text-xs w-full">
          <thead>
            <tr style={{ color: 'var(--text-muted)' }}>
              <th className="text-left py-1">AI 引擎</th>
              <th className="text-left py-1">应答数</th>
              <th className="text-left py-1">品牌命中率</th>
              <th className="text-left py-1">外部引用次数</th>
              <th className="text-left py-1">主要引用来源</th>
            </tr>
          </thead>
          <tbody>
            {d.engines.map(e => (
              <tr key={e.engine} style={{ borderTop: '1px solid var(--border-color)' }}>
                <td className="py-1 text-primary">{e.name}</td>
                <td className="py-1 tabular-nums">{e.responses}</td>
                <td className="py-1 tabular-nums">
                  {e.measured ? `${e.hit_rate_pct}%` : '—'}
                </td>
                <td className="py-1 tabular-nums">{e.citations}</td>
                <td className="py-1 text-secondary">
                  {(e.top_domains || []).slice(0, 3).map(x => x.domain).join('、') || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

// ── Step 2: where to place (engine priority) ────────────────────────────────────

function EnginePriority({ live }: { live: PlacementStrategy }) {
  return (
    <SectionCard
      title="引擎投放优先级"
      hint="排序依据 = 该引擎在本项目的提问量 × 当前未命中品牌的程度 × 行业权重。发文表的平台配额即按此权重加权计算。"
    >
      <div className="space-y-2">
        {live.engine_priority.map(p => (
          <div key={p.engine} className="py-2"
               style={{ borderTop: '1px solid var(--border-color)' }}>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs px-1.5 py-0.5 rounded-full tabular-nums"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}>
                第 {p.rank}
              </span>
              <span className="text-sm text-primary">{p.name}</span>
              <span className="text-[11px] text-muted">{p.ecosystem}</span>
              {p.generic && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                      style={{ background: 'rgba(148,163,184,0.15)', color: '#94a3b8' }}>
                  暂无专属口径
                </span>
              )}
              <span className="ml-auto text-[11px] text-muted tabular-nums">
                投放权重 {Math.round(p.weight * 100)}%
              </span>
            </div>
            <div className="mt-1 h-1 rounded-full overflow-hidden"
                 style={{ background: 'var(--bg-tertiary)' }}>
              <div style={{ width: `${Math.max(2, Math.round(p.weight * 100))}%`,
                            height: '100%', background: 'var(--accent-primary)' }} />
            </div>
            <div className="text-xs text-secondary mt-1.5 leading-relaxed">{p.rationale}</div>
            {p.summary && (
              <div className="text-[11px] text-muted mt-1 leading-relaxed">{p.summary}</div>
            )}
            {p.exclusive && (
              <div className="text-[11px] mt-1" style={{ color: 'var(--accent-primary)' }}>
                独家信源:{p.exclusive}
              </div>
            )}
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

// ── Step 3: what to place (per-engine checklist) ────────────────────────────────────────

function TierChip({ tier }: { tier: string }) {
  if (!tier) return null;
  const color = TIER_COLOR[tier] || '#94a3b8';
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap"
          style={{ background: `${color}1f`, color, border: `1px solid ${color}55` }}>
      {TIER_LABEL[tier] || tier}
    </span>
  );
}

function PlatformPlan({ live, planned }: {
  live: PlacementStrategy; planned: Map<string, number>;
}) {
  const [open, setOpen] = useState<string | null>(
    live.platform_plan[0]?.engine || null);
  return (
    <SectionCard
      title="各引擎投放清单"
      hint="可排入发文表的条目会随「按策略重排」自动落入排期;需人工建设的条目(百科词条、账号认证等)已推送至优化任务中心。"
    >
      <div className="space-y-1">
        {live.platform_plan.map(row => (
          <EngineChecklist key={row.engine} row={row} planned={planned}
                           open={open === row.engine}
                           onToggle={() => setOpen(open === row.engine ? null : row.engine)} />
        ))}
      </div>
    </SectionCard>
  );
}

function EngineChecklist({ row, planned, open, onToggle }: {
  row: StrategyPlatformPlan;
  planned: Map<string, number>;
  open: boolean;
  onToggle: () => void;
}) {
  const todo = row.publishable.filter(it => !it.occupied).length
    + row.infra.filter(it => !it.occupied).length;
  return (
    <div style={{ borderTop: '1px solid var(--border-color)' }}>
      <button type="button" onClick={onToggle}
              className="w-full text-left py-2 flex items-center gap-2">
        <span className="text-xs text-muted tabular-nums">第 {row.rank}</span>
        <span className="text-sm text-primary">{row.name}</span>
        <span className="text-[11px] text-muted">
          {row.publishable.length} 项可排期 · {row.infra.length} 项需人工建设
        </span>
        {todo > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                style={{ background: 'rgba(234,179,8,0.15)', color: 'var(--warn-fg)' }}>
            {todo} 项待完成
          </span>
        )}
        <span className="ml-auto text-xs text-muted">{open ? '收起' : '展开'}</span>
      </button>
      {open && (
        <div className="pb-3 space-y-3">
          {row.priority_list.length > 0 && (
            <div className="text-[11px] text-muted leading-relaxed">
              信源优先级:{row.priority_list.join(' → ')}
            </div>
          )}
          <ChecklistRows title="可排入发文表" items={row.publishable} planned={planned} />
          <ChecklistRows title="需人工建设(已进入优化任务中心)" items={row.infra} planned={planned} />
        </div>
      )}
    </div>
  );
}

function ChecklistRows({ title, items, planned }: {
  title: string; items: StrategyChecklistItem[]; planned: Map<string, number>;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="text-[11px] text-muted mb-1">{title}</div>
      <ul className="space-y-1">
        {items.map(it => {
          const slots = it.platform ? (planned.get(it.platform) || 0) : 0;
          return (
            <li key={it.key} className="text-xs flex items-start gap-2 flex-wrap">
              <span style={{ color: it.occupied ? 'var(--success-fg)' : 'var(--text-muted)' }}>
                {it.occupied ? '✓' : '○'}
              </span>
              <span className="text-primary">{it.label}</span>
              <TierChip tier={it.tier} />
              {it.platform && (
                <span className="text-[11px] text-muted">投放至 {it.platform}</span>
              )}
              {it.kind && INFRA_KIND_LABEL[it.kind] && (
                <span className="text-[11px] text-muted">{INFRA_KIND_LABEL[it.kind]}</span>
              )}
              {it.platform && it.investable === false && (
                <span className="text-[11px]" style={{ color: 'var(--warn-fg)' }}>
                  当前不可投放(无已授权账号,付费资源库亦无在架资源)
                </span>
              )}
              {it.paid && (
                <span className="text-[11px] text-muted">付费代发</span>
              )}
              {slots > 0 && (
                <span className="text-[11px]" style={{ color: 'var(--accent-primary)' }}>
                  今日起已排 {slots} 篇
                </span>
              )}
              {it.note && <span className="text-[11px] text-muted">— {it.note}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ── Step 4: how to write / at what cadence ──────────────────────────────────────────

function ContentForms({ live }: { live: PlacementStrategy }) {
  return (
    <SectionCard title="内容形态要求"
                 hint="同一条品牌信息需按各引擎偏好调整呈现形态 —— 单一形态通投,在其他引擎往往不被引用。">
      <div className="space-y-2">
        {live.content_forms.map(c => (
          <div key={c.engine} className="py-2 text-xs"
               style={{ borderTop: '1px solid var(--border-color)' }}>
            <div className="text-primary mb-1">{c.name}</div>
            <div className="text-secondary leading-relaxed">
              偏好形态:{c.forms.join('、') || '—'}
            </div>
            {c.avoid && (
              <div className="text-muted leading-relaxed mt-0.5">需规避:{c.avoid}</div>
            )}
            {c.recency && (
              <div className="text-muted leading-relaxed mt-0.5">时效性:{c.recency}</div>
            )}
            {c.observed_formats.length > 0 && (
              <div className="text-muted leading-relaxed mt-0.5">
                实测中该引擎回答本项目问题时最常见的组织形式:
                {c.observed_formats.map(f => `${f.format}(${f.count} 次)`).join('、')}
              </div>
            )}
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function Cadence({ live }: { live: PlacementStrategy }) {
  return (
    <SectionCard title="投放节奏"
                 hint="先夯实顶级权威信源,再铺设高权威信源做交叉印证,最后补充普通信源 —— 顺序颠倒会导致后续内容缺少可印证的来源。">
      <div className="space-y-2">
        {live.cadence.map(c => (
          <div key={c.key} className="py-2"
               style={{ borderTop: '1px solid var(--border-color)' }}>
            <div className="flex items-center gap-2 flex-wrap text-xs">
              <span className="text-primary">{c.weeks}</span>
              <span className="text-secondary">{c.focus}</span>
              <span className="ml-auto text-[11px] text-muted tabular-nums">
                本阶段约排 {c.planned_docs} 篇
              </span>
            </div>
            {c.items.length > 0 ? (
              <ul className="mt-1 space-y-0.5">
                {c.items.map((it, i) => (
                  <li key={i} className="text-xs text-secondary">
                    {it.engine} · {it.label}
                    {it.platform ? ` · 投放至 ${it.platform}` : ''}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-[11px] text-muted mt-1">本阶段清单已全部完成。</div>
            )}
          </div>
        ))}
      </div>
    </SectionCard>
  );
}
