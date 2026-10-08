// Plan page "Source analysis" tab -- why the plan is laid out this way; all the evidence is here.
//
// Three blocks:
//   1. Platform quota: measured cited-domain share -> how N articles per day split across platforms (same source as publish table scheduling;
//      backend services/source_plan.py computes exactly this) + "Replan by source" action
//   2. Source domain ranking: which sites are cited most, what type they are, whether we can place there
//   3. Sources per prompt: not-hit / low-citation first, to see the weak spots directly
//
// Data via GET /admin/review/topic/{id}/source-insights (pure aggregation, no LLM).

import { useState } from 'react';
import { useRegisterAgentActions } from '../../components/AgentChat/agentActions';
import { adminReviewApi, type ExecutionPlan } from '../../services/adminReviewApi';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { LoadingBlock } from '../../components/ui/Loading';
import { SectionCard } from '../../components/ui/SectionCard';
import { AsOfBadge } from './WhyBlock';
import { explainQuotaNote, planBasisOf, quotaNotes } from './planWhy';
import type { InsightsState } from './useSourceInsights';

// Source type = which "recommendation factor" this cited content counts as in the AI's eyes, judged by backend geo_checker/analyzers/
// source_factor.py::classify_factor. Backend keys stay in English (they are the data metric definition); this only does
// display-layer translation -- code values like list_mention never appear in the UI.
const SOURCE_TYPE_LABEL: Record<string, string> = {
  list_mention: '榜单提及',
  reviews: '评价点评站',
  directory: '名录 / 百科',
  awards: '奖项认证',
  customer: '客户案例',
  social: '社交内容平台',
  authority: '权威机构',
  tech_community: '技术社区',
  news_media: '新闻媒体',
  vendor: '厂商官网',
  other: '其他',
};

const SOURCE_TYPE_HINT: Record<string, string> = {
  list_mention: '「best of / 最佳 X」这类榜单合辑里提到了你 —— AI 最爱引的一类',
  reviews: 'G2 / Trustpilot / 大众点评这类第三方点评平台',
  directory: '天眼查 / 企查查 / 百科条目等企业名录与词条',
  awards: '奖项、榜单认证、资质页',
  customer: '客户案例 / 成功案例页',
  social: '知乎 / 小红书 / B站 等社交内容平台',
  authority: '政府、院校、行业协会等权威站点',
  tech_community: 'CSDN / 掘金 / 博客园 等技术社区',
  news_media: '新闻媒体报道',
  vendor: '厂商 / 竞品自己的官网',
  other: '未归类',
};

const sourceTypeLabel = (t?: string) => (t ? SOURCE_TYPE_LABEL[t] || t : '—');

export function SourceInsightsPanel({
  topicId, plan, canAct, insights, onRegenerated,
}: {
  topicId: number;
  /** With an existing plan, align the quota preview to its articles per day; otherwise preview with 5 */
  plan?: ExecutionPlan | null;
  canAct: boolean;
  /** Data fetching is lifted to the parent (one request shared with the "Why this layout" page) */
  insights: InsightsState;
  onRegenerated?: (updated: ExecutionPlan) => void;
}) {
  const token = localStorage.getItem('token') || '';
  const { data, err, loading, perDay } = insights;
  const [busy, setBusy] = useState(false);
  const [qFilter, setQFilter] = useState('');
  const planBuiltAt = planBasisOf(plan)?.built_at;

  // ── Agent page actions (batch 1: read-only). Replan (handleRegen) is an irreversible write; register it in batch 2.
  // The snapshot **always strips sample_titles** -- that's the biggest token sink in this data.
  useRegisterAgentActions({
    page: 'admin_source_insights',
    scope: 'sources',
    weight: 20,
    describe: '执行计划书 · 信源分析页(被引域名榜 / 平台配额,按此刻数据现算)',
    snapshot: {
      是否现算: true,
      排计划时的依据时间: planBuiltAt || '(这份计划没存排期依据)',
      过滤词: qFilter,
      应答总数: data?.responses_total ?? null,
      自有文章被引: data?.own_cited_responses ?? null,
      被引域名榜: (data?.top_sources || []).slice(0, 10).map((s2) => ({
        域名: s2.domain, 被引: s2.count, 类型: s2.source_type,
      })),
      平台配额槽位: (data?.quota?.slots || []).slice(0, 10),
      实测被引渠道: (data?.quota?.measured || []).slice(0, 8).map((m2) => ({
        平台: m2.platform, 被引: m2.citations, 占比: m2.share_pct, 可投: m2.investable,
      })),
    },
    selfConfirm: ['replanBySources'],
    actions: {
      setQueryFilter: (a) => setQFilter(String(a.text ?? '')),
      reload: () => { void insights.reload?.(); },
      // Irreversible write: replan the publish table from today on. handleRegen has its own uiConfirm (text explains the decoupling consequences); reused as-is.
      // selfConfirm: tells the chat window not to show the generic confirmation again (see agentActions.selfConfirm).
      ...(canAct ? { replanBySources: () => { void handleRegen(); } } : {}),
    },
  });

  const handleRegen = async () => {
    if (busy) return;
    if (!(await uiConfirm(
      '按当前实测信源重排发文表?只动今天及以后的排期,今天之前的历史行原样保留。' +
      '今天起已出稿的文章不会删除,但与旧计划行的关联会断开;' +
      '重排后需要再次确认计划才会为新行生成文章。',
      { title: '按信源重排(今天起)', okText: '重排' }))) return;
    setBusy(true);
    try {
      const updated = await adminReviewApi.generatePlanItems(topicId, {
        source: 'seeds', per_day: perDay, template_ids: [],
        platform_mode: 'sources',
      }, token);
      toast.success('已按信源重排今天及以后的发文表');
      onRegenerated?.(updated);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (loading && !data) {
    return <LoadingBlock label="正在分析信源…"
                         hint="汇总实测被引域名、平台配额与各提示词短板" />;
  }
  if (err) {
    return (
      <div className="rounded-md p-3 text-sm"
           style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                    border: '1px solid rgba(239,68,68,0.3)' }}>
        {err}
      </div>
    );
  }
  if (!data) return null;

  const q = data.quota || { breakdown: [], unavailable: [], unmapped: [], notes: [],
                            available: [], slots: [], cold_start: false };
  const measured = q.measured || [];
  const noteRows = quotaNotes(q);
  const noData = data.top_sources.length === 0;
  const shownQueries = qFilter.trim()
    ? data.per_query.filter(r => r.query.toLowerCase().includes(qFilter.trim().toLowerCase()))
    : data.per_query;

  return (
    <div className="space-y-4">
      {/* Background refresh with existing data (changing articles per day refetches): don't flash the whole page to a skeleton, just show a light hint */}
      {loading && (
        <div className="text-[11px] text-muted flex items-center gap-1.5">
          <span className="inline-block w-2.5 h-2.5 rounded-full animate-pulse"
                style={{ background: 'var(--accent-primary)' }} />
          正在刷新信源数据…
        </div>
      )}
      {/* Metric definition bar -- this page is "recomputed now", not "what was seen when the table was laid out".
          Account authorization, resource library and batch-run results all drift; without labeling this clearly people misread it. */}
      <AsOfBadge live at={data.computed_at} planBuiltAt={planBuiltAt} />

      {/* ── 1. Platform quota ─────────────────────────────── */}
      <SectionCard title="投放平台配额(按现在的被引占比重算)">
        <div className="flex items-start gap-3 mb-3">
          <p className="text-xs text-muted flex-1">
            共 {data.responses_total} 条应答,其中 {data.own_cited_responses} 条引到了自有文章。
            下面这份配额是<b>按此刻的数据重算</b>的,口径与排期一致:每天 {perDay} 篇按占比分给各平台。
            这份计划当初按什么策略排的、凭什么这么排,看「投放策略」页。
          </p>
          {canAct && (
            <button type="button" onClick={handleRegen} disabled={busy}
                    className="text-xs px-3 py-1.5 rounded-md text-white whitespace-nowrap"
                    style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
              {busy ? '重排中…' : '↻ 按信源重排计划'}
            </button>
          )}
        </div>

        {/* ① Measured cited channels -- show the batch run's raw conclusion first, unaffected by "can we place there".
            Without this section, in an environment with no external accounts authorized, the whole card shows a single
            "Official-site blog 100%" bar and looks like there's no data. */}
        <div className="text-xs text-muted mb-1.5">实测被引渠道(本次跑批)</div>
        {measured.length === 0 ? (
          <div className="text-xs text-muted mb-3">
            {data.top_sources.length === 0
              ? '本次跑批的应答里没有引用数据'
              : `被引的 ${data.top_sources.length} 个域名都不是可投放的内容平台`
                + '(百科 / 垂媒 / 竞品官网等),只能作为内容形态参考'}
          </div>
        ) : (
          <div className="space-y-2 mb-3">
            {measured.map(row => (
              <div key={row.platform} className="flex items-center gap-3">
                <span className="text-xs text-primary w-20 shrink-0">{row.platform}</span>
                <div className="flex-1 h-2 rounded-full overflow-hidden"
                     style={{ background: 'var(--bg-tertiary)' }}>
                  <div className="h-full rounded-full"
                       style={{ width: `${Math.max(2, row.share_pct)}%`,
                                background: row.investable_kind === 'free' ? 'var(--accent-primary)'
                         : row.investable_kind === 'paid' ? '#f59e0b' : '#94a3b8' }} />
                </div>
                <span className="text-xs tabular-nums text-secondary w-14 text-right">
                  {row.share_pct}%
                </span>
                <span className="text-xs tabular-nums text-muted w-16 text-right">
                  {row.citations} 次
                </span>
                {/* Three states: own account can place (free) / paid placement only / cannot place at all */}
                <span className="text-[10px] w-24 text-right shrink-0"
                      title={row.investable_kind === 'paid'
                        ? `付费代发 · ${row.resource_count || 0} 个可投资源`
                        : row.investable_kind === 'free'
                          ? '自有授权账号 / 自有站点,免费' : '没有任何可投路径'}
                      style={{
                        color: row.investable_kind === 'free' ? 'var(--success-fg)'
                          : row.investable_kind === 'paid' ? '#f59e0b' : '#94a3b8',
                      }}>
                  {row.investable_kind === 'free' ? '自有可投'
                    : row.investable_kind === 'paid'
                      ? `付费 ¥${row.unit_price ?? '—'}`
                      : '不可投'}
                </span>
                <span className="text-[10px] text-muted w-40 truncate"
                      title={row.domains.map(d => `${d.domain} ×${d.count}`).join('  ')}>
                  {row.domains.map(d => d.domain).join('、') || '—'}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* ② Quota actually used for scheduling -- only placeable channels, may differ from above; must be explained */}
        <div className="text-xs text-muted mb-1.5 pt-1"
             style={{ borderTop: '1px solid var(--border-color)' }}>
          发文表实际排期(每天 {perDay} 篇)
        </div>
        {q.breakdown.length === 0 ? (
          <div className="text-xs text-muted">—</div>
        ) : (
          <div className="space-y-1">
            {q.breakdown.map(row => (
              <div key={row.platform} className="flex items-center gap-3 text-xs">
                <span className="text-primary w-20 shrink-0">{row.platform}</span>
                <span className="tabular-nums text-primary w-16">{row.slots} 篇/天</span>
                <span className="w-24 shrink-0"
                      style={{ color: row.paid ? 'var(--warn-fg)' : 'var(--success-fg)' }}>
                  {row.paid ? `付费 ¥${row.unit_price ?? '—'}/篇` : '自有渠道'}
                </span>
                <span className="text-muted truncate"
                      title={row.domains.map(d => `${d.domain} ×${d.count}`).join('  ')}>
                  {row.domains.map(d => d.domain).join('、')}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Cost estimate -- should be visible before scheduling is decided */}
        {q.cost && (q.cost.paid_slots > 0 || q.cost.per_day_yuan > 0) && (
          <div className="mt-2 text-xs flex items-center gap-3 flex-wrap">
            <span className="text-secondary">
              预估 <span className="text-primary font-semibold tabular-nums">
                ¥{q.cost.per_day_yuan}
              </span>/天 · <span className="text-primary font-semibold tabular-nums">
                ¥{q.cost.per_month_yuan}
              </span>/月
            </span>
            <span className="text-muted">
              付费 {q.cost.paid_slots} 篇 / 自有 {q.cost.free_slots} 篇
              {q.paid_max_share ? `(付费封顶 ${Math.round(q.paid_max_share * 100)}%)` : ''}
            </span>
            <span className="text-[10px] text-muted">
              {q.cost.disclaimer}
              {q.cost.basis === 'reference_median'
                ? '(取资源库同平台在架资源的参考价中位数,不是最低价也不是最高价)' : ''}
            </span>
          </div>
        )}

        {q.cold_start && (
          <div className="rounded-md p-2 text-xs mt-2"
               style={{ background: 'rgba(234,179,8,0.10)', color: 'var(--warn-fg)' }}>
            实测被引的渠道一个都没授权,排期只能退到自有渠道 ——
            去发布中心授权上面标「未授权」的平台,重排后配额就会按实测占比走。
          </div>
        )}

        {q.unavailable.length > 0 && !q.cold_start && (
          <div className="mt-2 text-xs" style={{ color: 'var(--warn-fg)' }}>
            被引但当前投不了(去发布中心授权后可纳入配额):
            {q.unavailable.map(u => `${u.platform}(${u.citations})`).join('、')}
          </div>
        )}

        {/* Channels dropped by the long-tail threshold -- this section used to vanish, and the panel couldn't answer
            "Xiaohongshu was clearly cited, why wasn't a single article scheduled" */}
        {(q.excluded?.length ?? 0) > 0 && (
          <div className="mt-3 rounded-md p-2 text-xs"
               style={{ background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)' }}>
            <div className="text-primary mb-1">
              这次没排进去的渠道({q.excluded!.length} 个)
            </div>
            <div className="text-muted leading-relaxed">
              它们被引用过,但占比不到 {q.thresholds?.min_share_pct ?? 8}%,当长尾噪音跳过了 ——
              不然「一个渠道占九成、其余各占一两个点」的分布也会被摊成平均排,反而稀释重点。
              想让它们排上:调大每天篇数,或在发文表里手工加行。
            </div>
            <div className="mt-1 text-muted">
              {q.excluded!.map(e => `${e.platform} ${e.share_pct}%(${e.citations} 次)`).join('、')}
            </div>
          </div>
        )}

        {/* Per-platform migration under the paid cap -- previously just one vague note */}
        {(q.paid_cap_moves?.length ?? 0) > 0 && (
          <div className="mt-2 rounded-md p-2 text-xs"
               style={{ background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)' }}>
            <div className="text-primary mb-1">付费封顶挪走了几篇</div>
            <div className="text-muted">
              {q.paid_cap_moves!.map(m => `${m.from} → ${m.to} ${m.slots} 篇`).join('、')}
              {q.thresholds?.paid_max_share
                ? `(付费最多占 ${Math.round(q.thresholds.paid_max_share * 100)}%)` : ''}
            </div>
          </div>
        )}

        {/* Explanation of scheduling decisions -- used to be a single line of 11px gray join(';'), with no "so what" */}
        {noteRows.length > 0 && (
          <div className="mt-3 space-y-1.5">
            <div className="text-xs text-primary">排期时的判断</div>
            {noteRows.map((n, i) => {
              const ex = explainQuotaNote(n.text, n.code);
              const warn = ex.level === 'warn';
              return (
                <div key={i} className="rounded-md p-2 text-xs"
                     style={{ background: warn ? 'rgba(234,179,8,0.08)' : 'var(--bg-secondary)',
                              border: `1px solid ${warn ? 'rgba(234,179,8,0.3)'
                                : 'var(--border-color)'}` }}>
                  <div style={{ color: warn ? 'var(--warn-fg)' : 'var(--text-primary)' }}>
                    {ex.title}
                  </div>
                  {ex.why && (
                    <div className="text-muted mt-0.5 leading-relaxed">
                      {ex.why}{ex.action ? ` ${ex.action}` : ''}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* ── 1.5 Recommended outlets ───────────────────────────── */}
      {measured.some(m => (m.media?.length ?? 0) > 0) && (
        <SectionCard title="推荐投放媒体">
          <p className="text-xs text-muted mb-3">
            按「出稿率降序 → 价格升序」从代发资源库挑的。排期时每个平台的槽位在
            这几个号之间按天轮换 —— 一个月 30 篇全灌同一个号,对方那边的收录风险很高。
            发文表里可以逐行改成你指定的媒体。
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left" style={{ color: 'var(--text-muted)' }}>
                  <th className="py-2 px-2 font-medium">平台</th>
                  <th className="py-2 px-2 font-medium">媒体 / 账号</th>
                  <th className="py-2 px-2 font-medium text-right">单价</th>
                  <th className="py-2 px-2 font-medium text-right">出稿率</th>
                  <th className="py-2 px-2 font-medium">资源编号</th>
                </tr>
              </thead>
              <tbody>
                {measured.flatMap(m => (m.media || []).map((r, i) => (
                  <tr key={`${m.platform}:${r.resource_id}`}
                      style={{ borderTop: i === 0 ? '2px solid var(--border-color)'
                                                  : '1px solid var(--border-color)' }}>
                    <td className="py-2 px-2 text-primary">
                      {i === 0 ? m.platform : ''}
                    </td>
                    <td className="py-2 px-2 text-primary max-w-[220px] truncate"
                        title={r.title}>{r.title}</td>
                    <td className="py-2 px-2 text-right tabular-nums"
                        style={{ color: 'var(--warn-fg)' }}>¥{r.price_yuan}</td>
                    <td className="py-2 px-2 text-right tabular-nums text-secondary">
                      {r.publish_rate == null ? '—' : `${r.publish_rate}%`}
                    </td>
                    <td className="py-2 px-2 text-[10px] text-muted">
                      #{r.resource_id} · {r.res_type === 'Wemedia' ? '自媒体' : '门户网媒'}
                    </td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* ── 1.7 Updatable directories / encyclopedias ─────────────── */}
      {(q.profile_sites?.length ?? 0) > 0 && (
        <SectionCard title="可更新资料(投不了稿,但能自己改)">
          <p className="text-xs text-muted mb-2">
            这些是 AI 拿来当事实来源的名录 / 百科站,付费代发投不了稿,
            但企业可以自己去更新工商简介、补业务标签、建词条 —— 零成本,
            而且改完直接影响 AI 复述你的口径。
          </p>
          <div className="flex flex-wrap gap-1.5">
            {(q.profile_sites || []).map(p2 => (
              <span key={p2.domain}
                    className="inline-block px-2 py-1 rounded-md text-xs"
                    style={{ background: 'rgba(59,130,246,0.08)', color: 'var(--text-primary)',
                             border: '1px solid rgba(59,130,246,0.25)' }}>
                {p2.domain}
                <span className="text-muted ml-1 tabular-nums">×{p2.count}</span>
              </span>
            ))}
          </div>
        </SectionCard>
      )}

      {/* ── 2. Source domain ranking ───────────────────────────── */}
      <SectionCard title={`外部信源域名榜(${data.top_sources.length})`}>
        {noData ? (
          <div className="text-xs text-muted">
            还没有引用数据 —— 基线跑完、应答里出现引用后这里才有内容。
          </div>
        ) : (
          <div className="overflow-x-auto">
            {/* Type share: an overview for the "Type" column -- which kind of content AI actually prefers to cite */}
            {data.source_type_share.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-3">
                {data.source_type_share.map(s => (
                  <span key={s.source_type}
                        className="inline-block px-2 py-0.5 rounded-full text-[10px]"
                        style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
                        title={SOURCE_TYPE_HINT[s.source_type] || ''}>
                    {sourceTypeLabel(s.source_type)} {s.pct}%
                  </span>
                ))}
              </div>
            )}
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left" style={{ color: 'var(--text-muted)' }}>
                  <th className="py-2 px-2 font-medium">域名</th>
                  <th className="py-2 px-2 font-medium"
                      title="这条被引内容算哪种推荐因子:榜单提及 / 评价点评站 / 名录百科 / 社交内容平台 …">
                    类型
                  </th>
                  <th className="py-2 px-2 font-medium text-right">被引</th>
                  <th className="py-2 px-2 font-medium">引擎</th>
                  <th className="py-2 px-2 font-medium">可投渠道</th>
                  <th className="py-2 px-2 font-medium">标题样本</th>
                </tr>
              </thead>
              <tbody>
                {data.top_sources.map(s => (
                  <tr key={s.domain} style={{ borderTop: '1px solid var(--border-color)' }}>
                    <td className="py-2 px-2 text-primary">{s.domain}</td>
                    <td className="py-2 px-2 text-secondary"
                        title={s.source_type ? (SOURCE_TYPE_HINT[s.source_type] || '') : ''}>
                      {sourceTypeLabel(s.source_type)}
                    </td>
                    <td className="py-2 px-2 text-right tabular-nums text-primary">{s.count}</td>
                    <td className="py-2 px-2 text-[10px] text-muted">
                      {(s.engines || []).join('、') || '—'}
                    </td>
                    <td className="py-2 px-2">
                      {s.action === 'platform' ? (
                        <span className="inline-block px-1.5 py-0.5 rounded-full text-[10px]"
                              style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
                          {s.platform}
                        </span>
                      ) : s.action === 'rwj_site' ? (
                        <span className="inline-block px-1.5 py-0.5 rounded-full text-[10px]"
                              style={{ background: 'var(--warn-bg)', color: 'var(--warn-fg)' }}
                              title={(s.media || []).map(m =>
                                `${m.title} ¥${m.price_yuan}`).join('\n') || '可付费代发'}>
                          可代发 匠
                          {(s.media || []).length > 0
                            && ` ¥${Math.min(...(s.media || []).map(m => m.price_yuan))}起`}
                        </span>
                      ) : s.action === 'profile' ? (
                        <span className="inline-block px-1.5 py-0.5 rounded-full text-[10px]"
                              style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--info-fg)' }}
                              title="投不了稿,但可以自己去更新企业资料 / 词条 —— 零成本">
                          可更新资料
                        </span>
                      ) : (
                        <span className="text-[10px] text-muted">仅参考</span>
                      )}
                    </td>
                    <td className="py-2 px-2 max-w-[280px] truncate text-[10px] text-muted"
                        title={(s.sample_titles || []).join('\n')}>
                      {(s.sample_titles || [])[0] || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* ── 3. Sources per prompt ─────────────────────── */}
      <SectionCard title={`每个提示词的信源(${data.per_query.length})`}>
        <div className="flex items-center gap-2 mb-2">
          <p className="text-xs text-muted flex-1">
            按命中率升序排 —— 排在前面的是还没被引 / 引用少的词,优先补内容。
          </p>
          <input type="text" value={qFilter} onChange={e => setQFilter(e.target.value)}
                 placeholder="过滤提示词…"
                 className="w-[180px] rounded-md px-2 py-1 text-xs"
                 style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                          border: '1px solid var(--border-color)' }} />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left" style={{ color: 'var(--text-muted)' }}>
                <th className="py-2 px-2 font-medium">提示词</th>
                <th className="py-2 px-2 font-medium text-right">命中率</th>
                <th className="py-2 px-2 font-medium text-right">引用数</th>
                <th className="py-2 px-2 font-medium text-right">引到自有</th>
                <th className="py-2 px-2 font-medium">这个词被引的信源</th>
              </tr>
            </thead>
            <tbody>
              {shownQueries.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-muted"
                      style={{ borderTop: '1px solid var(--border-color)' }}>
                    没有匹配的提示词
                  </td>
                </tr>
              )}
              {shownQueries.map(r => (
                <tr key={r.query} style={{ borderTop: '1px solid var(--border-color)' }}>
                  <td className="py-2 px-2 max-w-[260px] text-primary" title={r.query}>
                    <div className="truncate">{r.query}</div>
                    <div className="text-[10px] text-muted">
                      {r.responses} 条应答 · {(r.engines || []).join('、')}
                    </div>
                  </td>
                  <td className="py-2 px-2 text-right tabular-nums"
                      style={{ color: r.hit_pct === 0 ? 'var(--danger-fg)'
                                     : r.hit_pct < 50 ? '#eab308' : '#10b981' }}>
                    {r.hit_pct}%
                  </td>
                  <td className="py-2 px-2 text-right tabular-nums text-secondary">
                    {r.citations}
                  </td>
                  <td className="py-2 px-2 text-right tabular-nums text-secondary">
                    {r.own_cited || '—'}
                  </td>
                  <td className="py-2 px-2">
                    {r.top_domains.length === 0 ? (
                      <span className="text-[10px] text-muted">这个词的回答没带引用</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {r.top_domains.map(d => (
                          <span key={d.domain}
                                className="inline-block px-1.5 py-0.5 rounded-full text-[10px]"
                                style={{
                                  background: d.platform
                                    ? 'var(--success-bg)' : 'var(--bg-tertiary)',
                                  color: d.platform ? 'var(--success-fg)' : 'var(--text-secondary)',
                                }}
                                title={d.platform ? `可投:${d.platform}` : '投不了,仅参考'}>
                            {d.domain} ×{d.count}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
}
