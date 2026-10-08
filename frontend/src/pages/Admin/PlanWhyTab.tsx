// Plan "Why this layout" tab -- plan-level self-justification.
//
// This page answers four things: why these placement platforms and outlets, why these templates,
// why these seeds and prompts, and why the plan as a whole looks this way.
//
// Every item carries an evidence badge (decided at the time / recomputed now / scheduling rule), no hedging --
// legacy plans that kept no record of the original reasoning say so plainly, instead of passing off today's recomputation as it.

import type { ExecutionPlan, SourceInsights } from '../../services/adminReviewApi';
import { SectionCard } from '../../components/ui/SectionCard';
import { LoadingBlock } from '../../components/ui/Loading';
import { AsOfBadge, KindBadge, WhyList } from './WhyBlock';
import {
  KIND_HINT, KIND_LABEL, planBasisOf, planReasons, quotaDrift, TRIGGER_LABEL,
  fmtWhen, type ReasonKind,
} from './planWhy';

export function PlanWhyTab({
  plan, insights, insightsLoading, isStaff, onGoSources,
}: {
  plan: ExecutionPlan;
  insights: SourceInsights | null;
  insightsLoading: boolean;
  isStaff: boolean;
  /** Jump for "go to the Source analysis page to replan" */
  onGoSources: () => void;
}) {
  const basis = planBasisOf(plan);
  const reasons = planReasons(plan, insights);
  const drift = quotaDrift(plan, insights);

  return (
    <div className="space-y-4">
      {/* Legend: what each of the three evidence badge levels means -- used across the page, so explain up front */}
      <div className="rounded-md p-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5"
           style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
        {(['settled', 'recomputed', 'rule'] as ReasonKind[]).map(k => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <KindBadge kind={k} />
            <span className="text-[11px] text-muted">{KIND_HINT[k]}</span>
          </span>
        ))}
      </div>

      {/* Legacy plan: state plainly that nothing was kept; don't pass off today's recomputation as the original basis */}
      {!basis && (
        <div className="rounded-md p-3 text-xs leading-relaxed"
             style={{ background: 'rgba(234,179,8,0.10)', color: 'var(--warn-fg)',
                      border: '1px solid rgba(234,179,8,0.3)' }}>
          本计划排期于系统开始记录排期依据之前,<b>当时的判定未留存</b>。
          下方标注「{KIND_LABEL.recomputed}」的部分按当前数据计算,仅能回答
          「按当前数据衡量,该排期是否仍然成立」。
          {isStaff && (
            <>
              {' '}如需完整依据,请前往
              <button type="button" onClick={onGoSources}
                      className="underline mx-1"
                      style={{ color: 'inherit' }}>信源分析</button>
              页执行「按信源重排计划」—— 重排采用最新数据,并自本次起留存依据。
            </>
          )}
        </div>
      )}

      {basis?.quota === null && (
        <div className="rounded-md p-3 text-xs leading-relaxed"
             style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)',
                      border: '1px solid var(--border-color)' }}>
          本计划<b>按模板自带的适用平台</b>铺设,未按实测被引数据分配投放平台。
          如需改为按数据排期,请前往「信源分析」页执行「按信源重排计划」。
        </div>
      )}

      <SectionCard
        title="排期依据"
        hint={basis?.built_at
          ? `最近一次排期:${fmtWhen(basis.built_at)}`
            + `${basis.trigger ? ` · ${TRIGGER_LABEL[basis.trigger] || basis.trigger}` : ''}`
          : undefined}
      >
        {insightsLoading && !insights ? (
          <LoadingBlock label="正在计算排期依据…" hint="汇总实测被引数据与排期口径" />
        ) : (
          <WhyList reasons={reasons} isStaff={isStaff}
                   empty="本计划暂无可展示的排期依据。" />
        )}
      </SectionCard>

      {/* Consistency self-check: surface "the data changed" instead of letting it silently drift */}
      <SectionCard title="数据一致性核验">
        <div className="space-y-2">
          <AsOfBadge live at={insights?.computed_at} planBuiltAt={basis?.built_at} />
          <div className="text-xs leading-relaxed"
               style={{ color: drift.state === 'drifted' ? 'var(--warn-fg)' : 'var(--text-secondary)' }}>
            {drift.state === 'aligned' && '✓ '}
            {drift.state === 'drifted' && '⚠️ '}
            {drift.text}
          </div>
          {drift.rows && drift.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="text-xs w-full max-w-md">
                <thead>
                  <tr style={{ color: 'var(--text-muted)' }}>
                    <th className="text-left py-1">平台</th>
                    <th className="text-left py-1">计划排期(每日)</th>
                    <th className="text-left py-1">按当前数据建议</th>
                  </tr>
                </thead>
                <tbody>
                  {drift.rows.map(r => (
                    <tr key={r.platform} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td className="py-1 text-primary">{r.platform}</td>
                      <td className="py-1 tabular-nums">{r.planned} 篇</td>
                      <td className="py-1 tabular-nums">{r.suggested} 篇</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {isStaff && drift.state === 'drifted' && (
            <button type="button" onClick={onGoSources}
                    className="text-xs px-2.5 py-1 rounded-md"
                    style={{ border: '1px solid var(--border-color)',
                             color: 'var(--accent-primary)' }}>
              前往信源分析页重排 →
            </button>
          )}
        </div>
      </SectionCard>

      {/* How the plan became what it is today -- only publish-table-related entries from the change log */}
      <PlanTimeline plan={plan} />

      <p className="text-[10px] text-muted leading-relaxed">
        说明:标注「{KIND_LABEL.settled}」的为排期当时留存的判定,不随后续数据变化;
        标注「{KIND_LABEL.recomputed}」的按当前数据实时计算;
        标注「{KIND_LABEL.rule}」的无行级证据,仅说明排期算法的处理规则。
      </p>
    </div>
  );
}

function PlanTimeline({ plan }: { plan: ExecutionPlan }) {
  const rows = (plan.topic_changelog || [])
    .filter(c => c.field === 'publishing_plan' || c.field === 'execution_plan')
    .slice(-8).reverse();
  const exp = (plan.expansion_log || []).slice(-3).reverse();
  if (rows.length === 0 && exp.length === 0) return null;
  return (
    <SectionCard title="计划变更记录">
      {rows.length > 0 && (
        <ul className="space-y-1.5 mb-3">
          {rows.map((c, i) => (
            <li key={i} className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              <span className="text-muted">{fmtWhen(c.at)}</span>
              {' · '}{c.note || c.field}
              {c.after && <span className="text-muted"> · {c.after}</span>}
            </li>
          ))}
        </ul>
      )}
      {exp.length > 0 && (
        <>
          <div className="text-[11px] text-muted mb-1">提示词扩展记录</div>
          <ul className="space-y-1">
            {exp.map((e, i) => (
              <li key={i} className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                <span className="text-muted">{fmtWhen(e.at)}</span>
                {' · 种子「'}{e.seed}{'」扩出 '}{e.expanded_count} 条
              </li>
            ))}
          </ul>
        </>
      )}
    </SectionCard>
  );
}
