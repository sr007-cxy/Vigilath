// Start-project config modal -- one component shared by the plan page empty state and the profile card's "⚡ Start project".
//
// This only sets the "scheduling metric definition": months to lay out / articles per day / seed source / template rotation / covered queries.
// **Placement platforms are not chosen here** -- platform quotas are computed from measured cited-source domain share, then the publish table is laid out
// (backend services/source_plan.py). If the topic ran recently its data is reused directly; only otherwise is a baseline
// run first (backend _recent_success_run_id). So the modal only explains;
// after laying out, see the quota basis in the "Source analysis" tab, or edit platforms row by row in the publish table.

import { useEffect, useMemo, useState } from 'react';
import { adminReviewApi, type StartTopicOptions } from '../../services/adminReviewApi';
import type { Topic } from '../../services/aiTelemetryApi';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';
import {
  contentTemplatesApi, type ContentTemplate, type TemplateScoreItem,
} from '../../services/contentTemplatesApi';

const MONTH_OPTS = [1, 2, 3];

export function StartProjectModal({
  topicId, topic, onClose, onStarted,
}: {
  topicId: number;
  /** Use the topic object directly if given (card entry); otherwise fetch once by topicId (plan page entry) */
  topic?: Topic | null;
  onClose: () => void;
  onStarted: () => void;
}) {
  const token = localStorage.getItem('token') || '';
  const [months, setMonths] = useState(1);
  const [perDay, setPerDay] = useState(5);
  const [source, setSource] = useState<'seeds' | 'strategy'>('seeds');
  const [tmplIds, setTmplIds] = useState<number[]>([]);
  const [templates, setTemplates] = useState<ContentTemplate[]>([]);
  // Template scorecard: template_id -> score; if unavailable, just show names as before
  const [scores, setScores] = useState<Map<number, TemplateScoreItem>>(new Map());
  const [detail, setDetail] = useState<Topic | null>(topic ?? null);
  // Covered queries: empty set = all prompts (unchecked means full coverage; don't force ops to click through first)
  const [coverage, setCoverage] = useState<string[]>([]);
  const [qFilter, setQFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sys, tenant, topicScope] = await Promise.all([
          contentTemplatesApi.list(token, { scope: 'system' }),
          contentTemplatesApi.list(token, { scope: 'tenant' }),
          contentTemplatesApi.list(token, { scope: 'topic', topicId }),
        ]);
        if (!cancelled) setTemplates([...sys, ...tenant, ...topicScope]);
      } catch { /* Failing to fetch templates doesn't block start; the backend auto-picks system templates */ }
      try {
        const sc = await contentTemplatesApi.scorecard(token, { topicId });
        if (!cancelled) setScores(new Map(sc.items.map(it => [it.template_id, it])));
      } catch { /* Missing scorecard doesn't affect template picking */ }
    })();
    return () => { cancelled = true; };
  }, [token, topicId]);

  useEffect(() => {
    if (topic) { setDetail(topic); return; }
    let cancelled = false;
    adminReviewApi.getTopicReview(topicId, token)
      .then(d => { if (!cancelled) setDetail(d); })
      .catch(() => { /* If unavailable there are just no queries to check; doesn't block start */ });
    return () => { cancelled = true; };
  }, [topic, topicId, token]);

  // Covered query candidates = queries selected as prompts (missing query_selected counts as selected)
  const monitored = useMemo(() => {
    const qs = detail?.queries || [];
    const sel = detail?.query_selected;
    return qs.filter((_, i) => (sel ? sel[i] !== false : true));
  }, [detail]);

  const shownQueries = qFilter.trim()
    ? monitored.filter(q => q.toLowerCase().includes(qFilter.trim().toLowerCase()))
    : monitored;

  const handleStart = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    const options: StartTopicOptions = {
      months,
      per_day: Math.min(10, Math.max(1, perDay)),
      source,
      template_ids: tmplIds,
      coverage_queries: coverage,
      platform_mode: 'sources',
    };
    try {
      await adminReviewApi.startTopic(topicId, token, false, options);
      onStarted();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = {
    background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
    border: '1px solid var(--border-color)',
  } as const;
  const totalRows = months * 30 * perDay;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4"
         style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(busy ? undefined : onClose)}>
      <div className="w-[600px] max-w-full rounded-md p-4 flex flex-col gap-3"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                    maxHeight: '85vh', overflowY: 'auto' }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-primary">启动项目</h3>
          <button type="button" onClick={onClose} disabled={busy}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </div>

        {/* This **must not** unconditionally say "starting runs a baseline audit first": if the topic ran recently that run is reused
            (backend _recent_success_run_id, default within 7 days) and nothing reruns. And the 7-day rule
            shouldn't be duplicated in the frontend -- a copy will drift from the backend sooner or later. So the text only states what holds in both branches. */}
        <p className="text-[11px] text-muted leading-relaxed">
          启动后系统按各引擎实际引用的信源域名占比决定投放平台,再按下面的口径铺发文表。
          主题最近已经跑过监测就直接用那批实测数据,没跑过才先跑一轮基线检测。
          投放平台不用在这里选 —— 排完可在「信源分析」页签看配额依据,也可以在发文表里逐行手改。
        </p>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <div className="text-muted mb-1.5">生成周期</div>
            <div className="flex flex-wrap gap-1.5">
              {MONTH_OPTS.map(m => (
                <button key={m} type="button" onClick={() => setMonths(m)}
                        className="px-2.5 py-1 rounded-full text-xs"
                        style={{
                          background: months === m ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                          color: months === m ? '#fff' : 'var(--text-secondary)',
                          border: '1px solid var(--border-color)',
                        }}>
                  {months === m && '✓ '}{m} 个月
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-muted mb-1">每天生成篇数(1-10)</label>
            <input type="number" min={1} max={10}
                   className="w-24 rounded-md px-2 py-1.5" style={inputStyle}
                   value={perDay}
                   onChange={e => setPerDay(Number(e.target.value) || 1)} />
            <span className="text-muted ml-2">约 {totalRows} 篇</span>
          </div>
        </div>

        <div className="text-xs">
          <div className="text-muted mb-1.5">选题来源</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {([
              { v: 'seeds' as const, title: '种子 × 模板轮换',
                desc: '用主题里已通过的种子提示词按天轮换排期(默认方式)' },
              { v: 'strategy' as const, title: '按策略方案生成',
                desc: '用战略方案的关键词体系轮换;方案没生成时自动退回种子轮换' },
            ]).map(opt => (
              <button key={opt.v} type="button" onClick={() => setSource(opt.v)}
                      className="text-left rounded-md p-2.5"
                      style={{
                        background: source === opt.v ? 'rgba(59,130,246,0.08)' : 'var(--bg-tertiary)',
                        border: source === opt.v
                          ? '1px solid var(--accent-primary)'
                          : '1px solid var(--border-color)',
                      }}>
                <div className="font-semibold"
                     style={{ color: source === opt.v ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
                  {source === opt.v ? '● ' : '○ '}{opt.title}
                </div>
                <div className="text-[10px] text-muted mt-0.5">{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="text-xs">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-muted">覆盖词(不勾 = 全部 {monitored.length} 个提示词)</span>
            <span className="text-muted">已选 {coverage.length}</span>
            <div className="flex-1" />
            {coverage.length > 0 && (
              <button type="button" onClick={() => setCoverage([])}
                      className="text-[11px] px-1.5 py-0.5 rounded-md"
                      style={{ color: 'var(--text-secondary)',
                               border: '1px solid var(--border-color)' }}>
                清空
              </button>
            )}
            <input type="text" value={qFilter} onChange={e => setQFilter(e.target.value)}
                   placeholder="过滤…"
                   className="w-[130px] rounded-md px-2 py-1" style={inputStyle} />
          </div>
          <div className="overflow-y-auto rounded-md"
               style={{ border: '1px solid var(--border-color)', maxHeight: 150 }}>
            {shownQueries.length === 0 ? (
              <div className="p-3 text-center text-muted">
                {monitored.length === 0 ? '还没有提示词' : '无匹配的提示词'}
              </div>
            ) : shownQueries.map(q => (
              <label key={q}
                     className="flex items-start gap-2 px-3 py-1.5 cursor-pointer"
                     style={{ borderBottom: '1px solid var(--border-color)',
                              color: 'var(--text-primary)' }}>
                <input type="checkbox" checked={coverage.includes(q)}
                       onChange={() => setCoverage(prev =>
                         prev.includes(q) ? prev.filter(x => x !== q) : [...prev, q])}
                       className="mt-0.5 shrink-0" />
                <span>{q}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="text-xs">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-muted">内容模板(不选 = 自动挑系统模板)</span>
            <span className="text-muted">已选 {tmplIds.length}</span>
          </div>
          <div className="overflow-y-auto rounded-md"
               style={{ border: '1px solid var(--border-color)', maxHeight: 150 }}>
            {templates.length === 0 ? (
              <div className="p-3 text-center text-muted">暂无可用模板</div>
            ) : templates.map(t => (
              <label key={t.id}
                     className="flex items-center gap-2 px-3 py-1.5 cursor-pointer"
                     style={{ borderBottom: '1px solid var(--border-color)',
                              color: 'var(--text-primary)' }}>
                <input type="checkbox" checked={tmplIds.includes(t.id)}
                       onChange={() => setTmplIds(ids =>
                         ids.includes(t.id) ? ids.filter(x => x !== t.id) : [...ids, t.id])}
                       className="shrink-0" />
                <span className="flex-1 truncate" title={t.name}>{t.name}</span>
                {/* Template score (dominated by citation rate): see which template actually works when picking */}
                {(() => {
                  const s = scores.get(t.id);
                  if (!s) return null;
                  if (s.score === null) {
                    return (
                      <span className="text-[10px] text-muted shrink-0"
                            title={`出稿 ${s.docs} 篇,已发布够久的还不到起评篇数`}>
                        样本不足
                      </span>
                    );
                  }
                  const color = s.score >= 80 ? '#10b981' : s.score >= 60 ? '#eab308' : '#ef4444';
                  return (
                    <span className="text-[10px] shrink-0" style={{ color }}
                          title={`综合 ${s.score} 分 · 出稿 ${s.docs} 篇 · `
                                 + (s.cited_measurable
                                     ? `被引用 ${Math.round(s.rates.cited ?? 0)}%`
                                     : '暂无可判定引用的稿(缺投放链接),分数只含出稿质量')}>
                      {s.score} 分{s.cited_measurable ? '' : '*'}
                    </span>
                  );
                })()}
                {t.kind && <span className="text-[10px] text-muted shrink-0">{t.kind}</span>}
              </label>
            ))}
          </div>
        </div>

        {err && (
          <div className="rounded-md p-2 text-xs"
               style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
            {err}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            取消
          </button>
          <button type="button" onClick={handleStart} disabled={busy}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
            {busy ? '启动中…' : '⚡ 启动项目'}
          </button>
        </div>
      </div>
    </div>
  );
}
