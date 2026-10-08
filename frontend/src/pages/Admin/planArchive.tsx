// The plan's **archive area** -- project overview / topic log / generalization log.
//
// All three are "consult when verifying" cards, collapsed by default, side by side in one row at the bottom of the page. They aren't part of the daily flow,
// so they were split out of ExecutionPlan -- that file was 1497 lines, stacking the container logic read daily with
// these twice-a-year archives, so finding anything meant scrolling past them first.

import { fmtTime } from '../../utils/datetime';
import { useTranslation } from 'react-i18next';
import { type ExecutionPlan } from '../../services/adminReviewApi';
import type {} from '../../services/adminReviewApi';
import { SectionCard } from '../../components/ui/SectionCard';


export function OverviewSection({ plan }: { plan: ExecutionPlan }) {
  const { t } = useTranslation();
  const o = plan.overview || {};
  // Status and drafting counts moved to rows 1 and 2; not computed here -- computing the same number in two places will drift sooner or later
  const rows: [string, unknown][] = [
    ['项目名称', o.topic_name],
    ['品牌简称', o.company_short_name],
    ['行业', o.industry],
    ['服务地域', o.service_geo],
    ['提示词数', o.monitored_queries_count],
    ['引擎', Array.isArray(o.engines) ? (o.engines as string[]).join(', ') : ''],
    ['预估 cell 数', o.estimated_cells],
    ['计划书快照时间', plan.generated_at && fmtTime(plan.generated_at)],
    ['确认时间', plan.confirmed_at && fmtTime(plan.confirmed_at)],
    ['通过触发的 run_id', plan.run_id || '—'],
  ];
  return (
    // Pure archive card: "consult when verifying" fields like project name / industry / service region / snapshot time.
    // Status and counts moved up to rows 1 and 2 (closer to what they describe), and "✎ Edit publish table" moved up to
    // row 1 -- it is the only table-editing entry in confirmed state and shouldn't be buried in a collapsed-by-default card.
    <SectionCard title={t('admin.executionPlan.section.overview')}
                 collapsible defaultCollapsed>
      <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
        {rows.filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => (
          <div key={k} className="flex items-start gap-2">
            <dt className="text-muted shrink-0 min-w-[100px]">{k}:</dt>
            <dd className="text-primary break-words">{String(v)}</dd>
          </div>
        ))}
      </dl>
    </SectionCard>
  );
}


export function ChangelogSection({ plan }: { plan: ExecutionPlan }) {
  const { t } = useTranslation();
  return (
    <SectionCard collapsible defaultCollapsed
                 title={t('admin.executionPlan.section.changelog', { n: plan.topic_changelog.length })}>
      {plan.topic_changelog.length === 0 && (
        <div className="text-xs text-muted">—</div>
      )}
      <ul className="text-xs space-y-1 max-h-60 overflow-auto">
        {[...plan.topic_changelog].reverse().map((e, i) => (
          <li key={i} className="text-secondary">
            <span className="text-muted">{new Date(e.at).toLocaleString()}</span>
            <span className="mx-1">·</span>
            <span style={{ color: e.actor_role === 'admin' ? 'var(--danger-fg)' : 'var(--success-fg)' }}>{e.actor_role}</span>
            <span className="mx-1">·</span>
            <span>{e.field}</span>
            {e.after && <span className="text-primary ml-1">→ {e.after}</span>}
            {e.note && <span className="text-muted ml-1">({e.note})</span>}
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}


export function ExpansionSection({ plan }: { plan: ExecutionPlan }) {
  const { t } = useTranslation();
  return (
    <SectionCard collapsible defaultCollapsed
                 title={t('admin.executionPlan.section.expansion', { n: plan.expansion_log.length })}>
      {plan.expansion_log.length === 0 && <div className="text-xs text-muted">—</div>}
      <ul className="text-xs space-y-2 max-h-60 overflow-auto">
        {[...plan.expansion_log].reverse().map((e, i) => (
          <li key={i} className="text-secondary border-l pl-2"
              style={{ borderColor: 'var(--border-color)' }}>
            <div>
              <span className="text-muted">{new Date(e.at).toLocaleString()}</span>
              <span className="mx-1">·</span>
              <span className="text-primary">种子:{e.seed}</span>
              <span className="mx-1">·</span>
              <span>{e.model}</span>
              <span className="mx-1">·</span>
              <span>+{e.expanded_count} 条</span>
            </div>
            {e.raw_excerpt && (
              <div className="text-muted truncate mt-0.5">{e.raw_excerpt}</div>
            )}
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
