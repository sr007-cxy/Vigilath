// Admin 执行计划书 — 审核通过后展示项目总体状况 / 主题日志 / 泛化日志 / 运行进度.
// 路由:/workbench/topics/:topicId/execution-plan

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { adminReviewApi, type ExecutionPlan } from '../../services/adminReviewApi';

export function AdminExecutionPlan() {
  const { t } = useTranslation();
  const { topicId } = useParams();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const tid = Number(topicId);
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const p = await adminReviewApi.getExecutionPlan(tid, token);
      setPlan(p);
      // run 完成或 plan 失败 → 停止轮询
      if (p.status !== 'generating' && p.run_status !== 'running') {
        if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    }
  }, [tid, token]);

  useEffect(() => {
    fetchOnce();
    pollRef.current = window.setInterval(fetchOnce, 3000);
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [fetchOnce]);

  return (
    <div className="space-y-4">
      <PageHead titleKey="admin.executionPlan.title" titleFallback="执行计划书" />
      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-primary">{t('admin.executionPlan.title')}</h1>
          <p className="text-xs text-secondary mt-0.5">{t('admin.executionPlan.subtitle')}</p>
        </div>
        <button type="button" onClick={() => navigate('/workbench/review')}
                className="text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
          ← {t('admin.executionPlan.backToReview')}
        </button>
      </header>

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {!plan && !err && <div className="py-12 text-center text-sm text-muted">…</div>}

      {plan && (
        <div className="space-y-4">
          <OverviewSection plan={plan} />
          <ChangelogSection plan={plan} />
          <ExpansionSection plan={plan} />
          <ProgressSection plan={plan} />
        </div>
      )}
    </div>
  );
}

function OverviewSection({ plan }: { plan: ExecutionPlan }) {
  const { t } = useTranslation();
  const o = plan.overview || {};
  const rows: [string, unknown][] = [
    ['项目名称', o.topic_name],
    ['品牌简称', o.company_short_name],
    ['行业', o.industry],
    ['服务地域', o.service_geo],
    ['监测问题数', o.monitored_queries_count],
    ['引擎', Array.isArray(o.engines) ? (o.engines as string[]).join(', ') : ''],
    ['预估 cell 数', o.estimated_cells],
    ['计划生成时间', plan.generated_at && new Date(plan.generated_at).toLocaleString()],
    ['通过触发的 run_id', plan.run_id || '—'],
  ];
  return (
    <SectionCard title={t('admin.executionPlan.section.overview')}>
      <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
        {rows.filter(([_, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => (
          <div key={k} className="flex items-start gap-2">
            <dt className="text-muted shrink-0 min-w-[100px]">{k}:</dt>
            <dd className="text-primary break-words">{String(v)}</dd>
          </div>
        ))}
      </dl>
      {plan.error && (
        <div className="mt-2 text-xs" style={{ color: '#ef4444' }}>
          {t('admin.executionPlan.errorPrefix')}: {plan.error}
        </div>
      )}
    </SectionCard>
  );
}

function ChangelogSection({ plan }: { plan: ExecutionPlan }) {
  const { t } = useTranslation();
  return (
    <SectionCard title={t('admin.executionPlan.section.changelog', { n: plan.topic_changelog.length })}>
      {plan.topic_changelog.length === 0 && (
        <div className="text-xs text-muted">—</div>
      )}
      <ul className="text-xs space-y-1 max-h-60 overflow-auto">
        {[...plan.topic_changelog].reverse().map((e, i) => (
          <li key={i} className="text-secondary">
            <span className="text-muted">{new Date(e.at).toLocaleString()}</span>
            <span className="mx-1">·</span>
            <span style={{ color: e.actor_role === 'admin' ? '#ef4444' : '#10b981' }}>{e.actor_role}</span>
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

function ExpansionSection({ plan }: { plan: ExecutionPlan }) {
  const { t } = useTranslation();
  return (
    <SectionCard title={t('admin.executionPlan.section.expansion', { n: plan.expansion_log.length })}>
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

function ProgressSection({ plan }: { plan: ExecutionPlan }) {
  const { t } = useTranslation();
  const pct = plan.progress_total > 0
    ? Math.round(plan.progress_done / plan.progress_total * 100)
    : 0;
  return (
    <SectionCard title={t('admin.executionPlan.section.progress')}>
      <div className="space-y-2">
        <div className="flex items-center gap-3 text-xs">
          <div className="flex-1 h-2 rounded-full overflow-hidden"
               style={{ background: 'var(--bg-tertiary)' }}>
            <div className="h-full transition-all"
                 style={{ width: `${pct}%`, background: 'var(--accent-primary)' }} />
          </div>
          <div className="text-muted shrink-0">
            {plan.progress_done}/{plan.progress_total} ({pct}%)
          </div>
        </div>
        <div className="text-xs text-secondary">
          run #{plan.run_id || '—'} · status:
          <span className="ml-1"
                style={{ color: plan.run_status === 'success' ? '#10b981'
                       : plan.run_status === 'failed' ? '#ef4444'
                       : plan.run_status === 'running' ? '#eab308' : '#94a3b8' }}>
            {plan.run_status || 'unknown'}
          </span>
        </div>
        {plan.progress.length > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5 mt-2 max-h-80 overflow-auto">
            {plan.progress.map((c, i) => (
              <div key={i} className="rounded-md p-1.5 text-[11px]"
                   style={{
                     background: c.status === 'done' ? 'rgba(16,185,129,0.10)'
                               : c.status === 'running' ? 'rgba(234,179,8,0.10)'
                               : 'var(--bg-card)',
                     border: '1px solid var(--border-color)',
                   }}>
                <div className="font-semibold truncate" style={{
                  color: c.status === 'done' ? '#10b981'
                       : c.status === 'running' ? '#eab308' : 'var(--text-secondary)',
                }}>
                  {c.engine}
                  {c.hit === true && <span className="ml-1">✓</span>}
                </div>
                <div className="text-muted truncate" title={c.query}>{c.query}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </SectionCard>
  );
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-md p-4"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <h2 className="text-sm font-semibold text-primary mb-3">{title}</h2>
      {children}
    </section>
  );
}
