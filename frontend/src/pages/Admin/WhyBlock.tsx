// Shared display pieces for "Why this layout" -- the whole plan / a day / a row all use the same rendering.
//
// Here rather than in each page: RowWhy is used by PublishingPlanEditor.tsx, and that file is bound by
// react-refresh rules to export only components, no new pure-function exports.
//
// Visibility: clients see the same blocks through the project wizard's read-only embed, so staffOnly entries
// (internal metrics like resource IDs, rotation slots, unit prices) must be filtered by isStaff.
// **Can't use canAct** -- it is also false for "ops in a read-only embed" and would block ops themselves.

import { useState } from 'react';
import type { PublishPlanItem, SourceInsights } from '../../services/adminReviewApi';
import {
  dayReasons, fmtWhen, KIND_COLOR, KIND_HINT, KIND_LABEL, rowReasons,
  type Reason, type ReasonKind,
} from './planWhy';

export function KindBadge({ kind }: { kind: ReasonKind }) {
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap shrink-0"
          style={{ background: `${KIND_COLOR[kind]}1f`, color: KIND_COLOR[kind],
                   border: `1px solid ${KIND_COLOR[kind]}55` }}
          title={KIND_HINT[kind]}>
      {KIND_LABEL[kind]}
    </span>
  );
}

/** Metric definition bar: states when and how the numbers in this block were computed */
export function AsOfBadge({ live, at, planBuiltAt }: {
  live: boolean; at?: string | null; planBuiltAt?: string | null;
}) {
  return (
    <div className="rounded-md p-2 text-[11px] leading-relaxed"
         style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)',
                  border: '1px solid var(--border-color)' }}>
      {live ? (
        <>
          本区块数值<b>按当前数据实时重算</b>
          {at ? `(计算于 ${fmtWhen(at)})` : ''}。
          {planBuiltAt
            ? `本计划排期于 ${fmtWhen(planBuiltAt)},当时的数据与当前未必一致。`
            : ''}
          因此它回答的是「按当前数据衡量,该排期是否仍然成立」,而非排期当时的数据状况。
        </>
      ) : (
        <>
          本区块为<b>排期当时留存的判定</b>
          {planBuiltAt ? `(排期于 ${fmtWhen(planBuiltAt)})` : ''},不随后续数据变化。
          如需按当前数据查看,请前往「信源分析」页。
        </>
      )}
    </div>
  );
}

export function WhyItem({ reason }: { reason: Reason }) {
  const warn = reason.level === 'warn';
  return (
    <div className="py-2" style={{ borderTop: '1px solid var(--border-color)' }}>
      <div className="flex items-start gap-2 flex-wrap">
        <KindBadge kind={reason.kind} />
        <span className="text-xs font-medium"
              style={{ color: warn ? 'var(--warn-fg)' : 'var(--text-primary)' }}>
          {reason.title}
        </span>
        {reason.asOf && (
          <span className="text-[10px] text-muted">· {fmtWhen(reason.asOf)}</span>
        )}
      </div>
      {reason.text && (
        <p className="text-xs mt-1 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {reason.text}
        </p>
      )}
      {reason.bullets && reason.bullets.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {reason.bullets.map((b, i) => (
            <li key={i} className="text-[11px] text-muted">· {b}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function WhyList({ reasons, isStaff, empty }: {
  reasons: Reason[]; isStaff: boolean; empty?: string;
}) {
  const shown = reasons.filter(r => !r.staffOnly || isStaff);
  if (shown.length === 0) {
    return empty ? <div className="text-xs text-muted py-2">{empty}</div> : null;
  }
  return <div>{shown.map((r, i) => <WhyItem key={i} reason={r} />)}</div>;
}

/** For the calendar day detail: collapsed by default, expands on click (the day action bar is already crowded) */
export function DayWhy({ dayItems, insights, isStaff }: {
  dayItems: PublishPlanItem[];
  insights?: SourceInsights | null;
  isStaff: boolean;
}) {
  const [open, setOpen] = useState(false);
  const reasons = dayReasons(dayItems, insights);
  if (reasons.length === 0) return null;
  return (
    <div className="rounded-md mb-2"
         style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
      <button type="button" onClick={() => setOpen(o => !o)}
              className="w-full text-left px-2.5 py-1.5 text-xs flex items-center gap-2"
              style={{ color: 'var(--text-secondary)' }}>
        <span>{open ? '▾' : '▸'}</span>
        <span>当日排期依据</span>
        <span className="text-[10px] text-muted ml-auto">{open ? '收起' : '展开'}</span>
      </button>
      {open && (
        <div className="px-2.5 pb-2">
          <WhyList reasons={reasons} isStaff={isStaff} />
        </div>
      )}
    </div>
  );
}

/** Top of the plan row edit modal: read-only rationale block */
export function RowWhy({ item, dayItems, insights, isStaff }: {
  item: PublishPlanItem | null | undefined;
  dayItems?: PublishPlanItem[];
  insights?: SourceInsights | null;
  isStaff: boolean;
}) {
  if (!item) return null;
  const reasons = rowReasons(item, { dayItems, insights });
  return (
    <div className="rounded-md p-2.5 mb-3"
         style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
      <div className="text-xs font-medium text-primary mb-1">本行排期依据(只读)</div>
      <WhyList reasons={reasons} isStaff={isStaff} />
      <p className="text-[10px] text-muted mt-2 pt-2"
         style={{ borderTop: '1px solid var(--border-color)' }}>
        以上为排期时的系统判定,不可编辑。在下方修改平台 / 模板 / 种子 / 提示词后,
        对应条目将标记为「人工指定」。
      </p>
    </div>
  );
}
