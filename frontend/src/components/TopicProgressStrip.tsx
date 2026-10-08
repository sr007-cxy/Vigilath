// Topic progress bar + this round's details -- shared by the "Monitoring & batch runs" panel and the dashboard's today tab.
// All status criteria come from GET /topics/{id}/progress (geo/services/topic_progress.py); the frontend doesn't re-derive them.
import type { ReactNode } from 'react';
import type { TopicProgress, TopicStage } from '../services/aiTelemetryApi';
import { engineLabel } from '../constants/engines';
import { fmtTime } from '../utils/datetime';

// Status bar -- "which step / what state / what's next".
//
// Why it exists: before this, all the user could see was a badge drawn from submission_status, which is the **ops
// review** state, not the state of their monitoring. So "terms added but not enough credits, not a single Q&A sent" showed as
// "approved" and the user thought it was running; while all 849 terms of a draft topic were actually running. After terms stopped being reviewed
// (2026-09-09), that badge was left with only two dead states that could never be drawn.
//
// Stages and copy all come from backend geo/services/topic_progress.py -- the frontend no longer re-derives them,
// or the two sides would sooner or later say different things.
//
// title / children are for the card on the plan page (step 4): the same status, plus a layer of "what ran"
// details. Deliberately a slot rather than a second component -- the status bar criteria can only exist once.
export function TopicProgressStrip({ p, title, children }: {
  p: TopicProgress; title?: string; children?: ReactNode;
}) {
  const tone: Record<TopicStage, { bg: string; fg: string }> = {
    no_profile:   { bg: 'rgba(148,163,184,0.18)', fg: '#64748b' },
    no_queries:   { bg: 'rgba(148,163,184,0.18)', fg: '#64748b' },
    insufficient: { bg: 'var(--danger-bg)',       fg: '#dc2626' },
    ready:        { bg: 'rgba(59,130,246,0.15)',  fg: '#3b82f6' },
    running:      { bg: 'rgba(234,179,8,0.15)',   fg: '#b45309' },
    done:         { bg: 'var(--success-bg)',      fg: '#16a34a' },
    paused:       { bg: 'var(--bg-input)',        fg: 'var(--text-muted)' },
    off:          { bg: 'var(--bg-input)',        fg: 'var(--text-muted)' },
  };
  const c = tone[p.stage] ?? tone.ready;
  const yuan = (cents: number) => (cents / 100).toFixed(0);

  return (
    <section
      className="rounded-lg px-4 py-3 space-y-2"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      {title && <h2 className="text-sm font-semibold text-primary">{title}</h2>}
      <div className="flex flex-wrap items-center gap-3">
        <span className="px-2 py-0.5 rounded text-xs font-medium"
              style={{ background: c.bg, color: c.fg }}>
          {p.label}
        </span>
        <span className="text-sm text-primary">{p.next_action.text}</span>
      </div>

      {/* Running: progress bar + which Q&A number. This is what the user watching the page most wants to know */}
      {p.stage === 'running' && p.run && (
        <div className="space-y-1">
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-input)' }}>
            <div className="h-full rounded-full transition-all"
                 style={{ width: `${p.run.percent}%`, background: 'var(--accent-primary)' }} />
          </div>
          <div className="text-xs text-muted">
            已问 {p.run.cells_done} / {p.run.cells_total} 次问答({p.run.percent}%)·
            {' '}{p.queries.active} 个词 × {p.engines} 个引擎
          </div>
        </div>
      )}

      {/* Money: deducted before the run starts, so "how much needed / how much you have" must be visible before starting */}
      {p.cost.chargeable && p.stage !== 'off' && p.stage !== 'paused' && (
        <div className="text-xs text-muted">
          本轮 {p.cost.cells} 次问答 × {p.cost.price_cents} 积分 ={' '}
          <span style={{ color: p.cost.affordable ? undefined : '#dc2626' }}>
            {p.cost.need_cents} 积分
          </span>
          {' '}· 余额 {p.cost.balance_cents} 积分
          {!p.cost.affordable && (
            <span style={{ color: '#dc2626' }}>
              {' '}· 还差 {p.cost.need_cents - p.cost.balance_cents} 积分(约 ¥
              {yuan(p.cost.need_cents - p.cost.balance_cents)})
            </span>
          )}
        </div>
      )}

      {/* Term list metric definition: 800 of 849 terms are running, the rest were unchecked/deleted; without saying so users think they were lost */}
      {p.queries.total > p.queries.active && (
        <div className="text-xs text-muted">
          词表共 {p.queries.total} 条,其中 {p.queries.active} 条在监测
          (其余已取消勾选或删除,不跑也不计费)。
        </div>
      )}

      {children}
    </section>
  );
}

// Batch run status card on the GEO strategy optimization plan page (step 4).
//
// Why this card was added: after selecting terms in the previous step, **saving itself doesn't trigger a batch run**, but the drip scheduler re-reads the term list every tick
// (telemetry scheduler.py:744), so new terms start running on their own within an hour at most -- the user never clicked any
// "start", yet the page was already spending money. This page previously only had the "audit results overview", which was empty before the first round produced data,
// so nowhere in the whole product could "it's already running" be said, and users could only guess whether they'd missed a step.
//

// "What ran + how much it cost" -- seed terms -> prompts -> actual runs per engine -> credits actually deducted this round.
//
// This block is **user-facing** and only covers what users can make decisions on, so it deliberately hides three things:
//   · per-engine failure counts -- rate limits/dropouts are our business; users can neither handle them nor judge severity,
//     and showing them here would make a normally producing monitoring round look broken;
//   · batch run round id (`round #9985`) -- an internal primary key, of no use to users;
//   · raw `run.error` text (`aborted: 人工终止 —— …`) -- that's ops-level logging,
//     not words meant for people.
// What replaces them is the number users actually chase: **how many credits this round took from the balance**.
// Failure details / round ids / error causes are still in the API, and the ops side (AdminRunDetail) can see them as before.
export function RunDetail({ p }: { p: TopicProgress }) {
  const run = p.run;
  const SEED_SHOWN = 12;
  return (
    <div className="space-y-1.5 pt-1" style={{ borderTop: '1px solid var(--border-color)' }}>
      {p.seeds.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 text-xs text-muted pt-1.5">
          <span className="shrink-0">
            按 {p.seeds.length} 个种子提示词扩出的 {p.queries.active} 条提示词:
          </span>
          {p.seeds.slice(0, SEED_SHOWN).map(sd => (
            <span key={sd} className="px-1.5 py-0.5 rounded"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
              {sd}
            </span>
          ))}
          {p.seeds.length > SEED_SHOWN && (
            <span>等 {p.seeds.length} 个</span>
          )}
        </div>
      )}

      {run && run.engines.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {run.engines.map(e => (
            <span key={e.engine}>
              {engineLabel(e.engine)}
              <span style={{ color: '#16a34a' }}> {e.ok} 条</span>
            </span>
          ))}
        </div>
      )}

      {run && (
        <div className="text-xs text-muted">
          开跑 {fmtTime(run.started_at, false)}
          {run.finished_at && <> · 完成 {fmtTime(run.finished_at, false)}</>}
          {run.engines.length === 0 && !run.finished_at && <> · 引擎还没回话</>}
        </div>
      )}

      {/* Money. State the net amount as one number, and if anything was refunded, spell out "pre-deducted X / refunded Y" -- reporting only the net,
          users looking at the pre-deduction on their bill would think they were overcharged. Even zero deducted must be stated explicitly (a round that didn't start
          for lack of balance is like this): otherwise users have to dig through the bill themselves. */}
      {run && (
        <div className="text-xs text-muted">
          {run.charged_cents > 0 ? (
            <>
              本轮扣 <span className="text-primary font-medium">{run.net_cents}</span> 积分
              {run.refunded_cents > 0 && (
                <> (开跑时预扣 {run.charged_cents},没跑掉的 {run.refunded_cents} 已退回)</>
              )}
            </>
          ) : '本轮未扣积分'}
        </div>
      )}

      {!run && (
        <div className="text-xs text-muted pt-1.5">还没跑过 —— 第一轮跑完这里会显示各引擎的实跑条数。</div>
      )}
    </div>
  );
}
