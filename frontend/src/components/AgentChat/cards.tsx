// Agent structured card rendering -- a registry of tool name -> card spec.
//
// Extracted from AgentChatWidget because it used to be a hardcoded if-else chain outputting a fixed "label / value" two-column layout,
// while ops console results are mostly **list-shaped** (plan rows, drafts, placement tasks, domain rankings), which two columns can't hold.
// This provides three primitives (KvBlock / TableBlock / BadgeRow) that each card composes.
//
// ★ Tool names are a frontend-backend contract: new cards must also be added to CARD_TOOLS in backend/agents/geo/api.py,
//   otherwise the backend never pushes this tool's return as a cards event.
//
// Only components are exported (CARD_SPECS / utility functions never are), satisfying react-refresh/only-export-components.
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { CSSProperties, ReactNode } from 'react';
import { engineLabel } from '../../constants/engines';
import { fullDiagnosisApi } from '../../services/fullDiagnosisApi';
import type { DiagnosisStep, FullDiagnosisReport } from '../../services/fullDiagnosisApi';
import type { AgentCard } from '../../services/agentApi';

// Fixed display order of the report center's six metrics; Chinese names prefer the backend's metric_labels (structurally preventing made-up metric names)
const METRIC_ORDER = ['exposure', 'top1', 'top3', 'accuracy', 'citation', 'mention'] as const;
const PERIOD_LABEL: Record<string, string> = { daily: '日报', weekly: '周报', monthly: '月报' };

// ⚠️ Report center percentage fields are already final 0-100 values -- use p1(); **never** use pct() (it multiplies by 100 again, showing 2430%).
function p1(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? `${n.toFixed(1)}%` : '—';
}

/** Six-metric row: Chinese names prefer the backend's metric_labels, preventing the frontend from inventing metric names. */
function metricRows(metrics: Record<string, unknown>, labels: Record<string, string>): [string, string][] {
  return METRIC_ORDER.filter((k) => metrics?.[k] != null)
    .map((k) => [labels?.[k] || k, p1(metrics[k])] as [string, string]);
}

const S = (v: unknown, dash = '—'): string => {
  const t = v == null ? '' : String(v);
  return t.trim() ? t : dash;
};

// ── Three rendering primitives ────────────────────────────────────────────────────────
export function KvBlock({ rows }: { rows: [string, string][] }) {
  if (!rows.length) return null;
  return (
    <>
      {rows.map(([k, v], i) => (
        <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 13, padding: '2px 0', color: 'var(--text-primary)' }}>
          <span style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>{k}</span>
          <span style={{ fontWeight: 600, textAlign: 'right', wordBreak: 'break-all' }}>{v}</span>
        </div>
      ))}
    </>
  );
}

/** Compact table. The panel is fixed-narrow, so keep columns within 4; if wider, scroll horizontally, never break the panel. */
export function TableBlock({ cols, rows, total }: { cols: string[]; rows: ReactNode[][]; total?: number }) {
  if (!rows.length) return null;
  return (
    <div style={{ overflowX: 'auto', marginTop: 4 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} style={{ textAlign: 'left', padding: '3px 6px 3px 0', color: 'var(--text-secondary)', fontWeight: 500, whiteSpace: 'nowrap' }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderTop: '1px solid var(--border-color)' }}>
              {r.map((cell, j) => (
                <td key={j} style={{ padding: '3px 6px 3px 0', color: 'var(--text-primary)', verticalAlign: 'top' }}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total != null && total > rows.length && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>共 {total} 条,以上为前 {rows.length} 条</div>
      )}
    </div>
  );
}

export function BadgeRow({ items }: { items: [string, number][] }) {
  if (!items.length) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
      {items.map(([k, v]) => (
        <span key={k} style={{
          fontSize: 11, padding: '1px 7px', borderRadius: 10,
          background: 'var(--bg-elevated, var(--bg-surface))', border: '1px solid var(--border-color)',
          color: 'var(--text-secondary)',
        }}>{k} <b style={{ color: 'var(--text-primary)' }}>{v}</b></span>
      ))}
    </div>
  );
}

const countPairs = (o: unknown, limit = 8): [string, number][] =>
  Object.entries((o || {}) as Record<string, number>)
    .filter(([, v]) => Number.isFinite(Number(v)))
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, limit)
    .map(([k, v]) => [k, Number(v)] as [string, number]);

// ── Card registry ──────────────────────────────────────────────────────────
type CardData = Record<string, unknown>;

interface CardSpec {
  title: string;
  /** onSend: sends the user's input in the card back to the agent. Only interactive cards use it. */
  render: (d: CardData, onSend?: (text: string) => void, disabled?: boolean) => ReactNode;
}

/** Get a sub-object / array -- card data comes from backend JSON; asserting each one is too noisy, so it's centralized here. */
const obj = (v: unknown): CardData => (v && typeof v === 'object' && !Array.isArray(v) ? v as CardData : {});
const arr = (v: unknown): CardData[] => (Array.isArray(v) ? v.filter((x): x is CardData => !!x && typeof x === 'object') : []);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);


/**
 * Industry term pending-input card.
 *
 * The industry term decides what questions the whole diagnosis asks the AI; guess wrong and the entire report asks the wrong things -- so this step
 * must be decided by the user, not guessed by the model. Candidates exist **only when they can really be inferred from the topic**; if not,
 * only an input box is shown, no fabricated candidates (this is the case when the account has no topic yet).
 */
function IndustryTermsAsk({ d, onSend, disabled }: {
  d: CardData; onSend?: (text: string) => void; disabled?: boolean;
}) {
  const suggestions = strs(d.suggestions);
  const [picked, setPicked] = useState<string[]>([]);
  const [text, setText] = useState('');

  const terms = [...picked, ...text.split(/[\s,,、]+/).map((x) => x.trim()).filter(Boolean)]
    .filter((v, i, a) => a.indexOf(v) === i)
    .slice(0, 5);

  const usable = !disabled && !!onSend;

  const chip = (active: boolean): CSSProperties => ({
    padding: '3px 10px', borderRadius: 999, fontSize: 12,
    cursor: usable ? 'pointer' : 'not-allowed',
    border: `1px solid ${active ? 'var(--accent-primary)' : 'var(--border-color)'}`,
    background: active ? 'var(--accent-primary)' : 'transparent',
    color: active ? '#fff' : 'var(--text-secondary)',
  });

  return (
    <div>
      <div style={{ fontSize: 13, marginBottom: 6 }}>
        {String(d.question || '你们这行的客户,会用什么词去问 AI?')}
      </div>
      {d.example ? (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>{String(d.example)}</div>
      ) : null}

      {suggestions.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {suggestions.map((sug) => {
            const on = picked.includes(sug);
            return (
              <button
                key={sug}
                type="button"
                disabled={!usable}
                style={chip(on)}
                onClick={() => setPicked((cur) => (on ? cur.filter((x) => x !== sug) : [...cur, sug]))}
              >
                {sug}
              </button>
            );
          })}
        </div>
      )}

      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={!usable}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && terms.length && usable) { onSend!(`行业词:${terms.join(' ')}`); }
        }}
        placeholder={suggestions.length ? '还可以自己补,空格分隔' : '填 1–3 个,空格分隔'}
        style={{
          width: '100%', padding: '6px 10px', borderRadius: 8, fontSize: 13,
          border: '1px solid var(--border-color)', background: 'var(--bg-base)',
          color: 'var(--text-primary)',
        }}
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <button
          type="button"
          disabled={!terms.length || !usable}
          onClick={() => onSend?.(`行业词:${terms.join(' ')}`)}
          style={{
            padding: '4px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: 'none',
            cursor: terms.length && usable ? 'pointer' : 'not-allowed',
            opacity: terms.length && usable ? 1 : 0.5,
            background: 'var(--accent-primary)', color: '#fff',
          }}
        >
          用这些词开始诊断
        </button>
        {terms.length > 0 && (
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{terms.join(' · ')}</span>
        )}
      </div>
    </div>
  );
}

/** Sub-audit -> user-facing wording. The backend's progress.steps stores mode (base/aeo/...); showing it directly exposes internal keys to users. */
const STEP_ZH: Record<string, string> = {
  base: '官网基础检测', aeo: '问答优化检测', crawlTest: 'AI 爬虫可达性', authority: '站外权威信号',
  visibility: '跨引擎可见性', entity: '品牌实体识别', competitiveIntel: '竞品对比', sentiment: '口碑与舆情',
};
const STEP_MARK: Record<string, string> = {
  ok: '✅', failed: '⚠️', skipped: '⊘', running: '⏳', pending: '⏸️', not_selected: '—',
};

/**
 * Diagnosis progress card -- polls itself every 3s instead of relying on the model to relay.
 *
 * Diagnosis is an 8-20 minute async heavy task, and after it started the chat window used to be completely silent: progress appeared only when the user asked
 * and the model called get_full_diagnosis, and then **as the model's text paraphrase**.
 * The consequence in the 2026-08-27 session was direct -- the user asked five times "is it done / so slow / what's going on",
 * and the "5/8" the model quoted was actually the progress of **another brand's** order (<brand-c> #95).
 *
 * Letting the card fetch its own data means progress has a single source: numbers can't leak across brands, and a hang is visible at a glance.
 * Polling cadence and checklist structure are copied from the report page (ProgressView in pages/Diagnosis/Report.tsx).
 */
function DiagnosisProgressCard({ reportId, brand }: { reportId: number; brand?: string }) {
  const [report, setReport] = useState<FullDiagnosisReport | null>(null);
  const [err, setErr] = useState('');
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    const stop = () => { if (timer.current) { window.clearInterval(timer.current); timer.current = null; } };
    const tick = async () => {
      try {
        const r = await fullDiagnosisApi.get(reportId);
        if (!alive) return;
        setReport(r);
        if (r.status !== 'generating') stop();      // finished / failed: stop polling
      } catch (e) {
        if (!alive) return;
        setErr(e instanceof Error ? e.message : '取进度失败');
        stop();                                     // stop if unavailable; don't report an error every 3s
      }
    };
    void tick();
    timer.current = window.setInterval(tick, 3000);
    return () => { alive = false; stop(); };
  }, [reportId]);

  const prog = report?.progress;
  const steps: DiagnosisStep[] = prog?.steps?.length ? prog.steps : [];
  // The denominator always uses the backend's total (tiers differ: full has 8 items, lite has 4).
  // Never use a constant like "eight sub-audits" for numerator/denominator -- that's where odd numbers like 8/5 come from.
  const total = prog?.total ?? steps.length;
  const done = Math.min(prog?.done ?? 0, total || Infinity);
  const running = report?.status === 'generating';

  return (
    <div>
      <div style={{ fontSize: 13, marginBottom: 6 }}>
        {brand ? <b>{brand}</b> : null} 诊断单 #{reportId}{' '}
        {report == null ? '取进度中…'
          : running ? <span style={{ color: 'var(--accent-primary)' }}>进行中 {total ? `${done}/${total} 项` : ''}</span>
          : report.status === 'ready' ? <span style={{ color: 'var(--success-fg, #16a34a)' }}>已完成</span>
          : <span style={{ color: 'var(--danger-fg, #dc2626)' }}>失败</span>}
      </div>

      {err ? <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{err}(可到报告页查看)</div> : null}

      {steps.length > 0 && (
        <div style={{ fontSize: 12, lineHeight: 1.8, color: 'var(--text-secondary)' }}>
          {steps.map((st) => (
            <div key={st.mode} style={{ display: 'flex', gap: 6 }}>
              <span style={{ flexShrink: 0 }}>{STEP_MARK[st.status] || '·'}</span>
              <span>{STEP_ZH[st.mode] || st.mode}</span>
            </div>
          ))}
        </div>
      )}

      {prog?.stage === 'llm' && running && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
          子检测已跑完,正在生成报告正文…
        </div>
      )}

      <Link
        to={`/diagnosis/${reportId}`}
        style={{
          display: 'inline-block', marginTop: 8, padding: '4px 14px', borderRadius: 8,
          fontSize: 12, fontWeight: 600, textDecoration: 'none',
          background: running ? 'transparent' : 'var(--accent-primary)',
          color: running ? 'var(--accent-primary)' : '#fff',
          border: running ? '1px solid var(--accent-primary)' : 'none',
        }}
      >
        {running ? '打开报告页看实时进度' : '查看报告'}
      </Link>
    </div>
  );
}

const CARD_SPECS: Record<string, CardSpec> = {
  // ── Customer lane (the original six from CardView, moved verbatim) ──────────────────────────
  run_full_diagnosis: {
    title: '一键全面诊断',
    render: (d, onSend, disabled) => {
      if (d.status === 'need_input') return <IndustryTermsAsk d={d} onSend={onSend} disabled={disabled} />;
      // Just started / already running -> attach a self-polling progress card directly; don't make the user ask the model.
      const rid = Number(d.report_id);
      if (rid > 0) return <DiagnosisProgressCard reportId={rid} brand={S(d.brand, '')} />;
      return null;
    },
  },
  get_report_overview: {
    title: '报告中心 · 最新数据',
    render: (d) => {
      const labels = obj(d.metric_labels) as Record<string, string>;
      const day = obj(d.daily);
      const rows: [string, string][] = [];
      if (day.key) rows.push(['日报期次', String(day.key)]);
      rows.push(...metricRows(obj(day.metrics), labels));
      const tp = obj(d.today_partial);
      if (Object.keys(tp).length) rows.push(['今天(进行中)', `露出 ${p1(obj(tp.metrics).exposure)} · ${tp.responses ?? 0} 条`]);
      return <KvBlock rows={rows} />;
    },
  },
  get_period_report: {
    title: '期次报告',
    render: (d) => {
      const labels = (d.metric_labels || {}) as Record<string, string>;
      const rows: [string, string][] = [['期次', `${PERIOD_LABEL[String(d.period_type)] || ''} ${d.key ?? '—'}`]];
      rows.push(...metricRows(obj(d.metrics), labels));
      const eng = arr(d.by_engine_exposure);
      if (eng.length) rows.push(['分平台露出', eng.slice(0, 3).map((e) => `${engineLabel(String(e.engine))} ${p1(e.exposure)}`).join(' · ')]);
      return <KvBlock rows={rows} />;
    },
  },
  get_query_coverage: {
    title: '累计命中(被搜到)',
    render: (d) => <KvBlock rows={[
      ['被搜到的问题(累计)', `${d.hit_queries ?? 0} / ${d.monitored_queries ?? 0}`],
      ['被搜到的种子词(累计)', `${d.hit_seeds ?? 0} / ${d.seed_total ?? 0}`],
    ]} />,
  },
  get_publish_status: {
    title: '发布进度',
    render: (d) => <KvBlock rows={[['已发布', `${d.published ?? 0} / ${d.total ?? 0}`]]} />,
  },
  get_full_diagnosis: {
    title: '一键全面诊断',
    render: (d) => {
      // Still running -> swap in a live progress card. The problem with a static snapshot is that it's **only correct at this moment**;
      // looking at a static "5/8" the user can't tell if it's stuck or the page didn't refresh, and keeps asking.
      const rid = Number(d.report_id);
      if (d.status === 'generating' && rid > 0) {
        return <DiagnosisProgressCard reportId={rid} brand={S(d.brand, '')} />;
      }
      const ST: Record<string, string> = { generating: '检测中', ready: '已完成', failed: '失败', none: '尚未检测' };
      const rows: [string, string][] = [['状态', ST[String(d.status)] || String(d.status ?? '—')]];
      if (d.total_score != null) rows.push(['综合评分', `${d.total_score} / 100 ${d.grade ?? ''}`]);
      const dims = arr(d.dimensions);
      dims.filter((x) => x['得分'] != null).slice(0, 5)
        .forEach((x) => rows.push([String(x['维度']), `${x['得分']} 分${x['口径'] ? '(估算)' : ''}`]));
      const miss = strs(d['未采集维度']);
      if (miss.length) rows.push(['未采集', miss.join('、')]);
      return <KvBlock rows={rows} />;
    },
  },
  get_run_status: {
    title: '最近跑批',
    render: (d) => {
      const rows: [string, string][] = [
        ['状态', String(d.status ?? '—')],
        ['采集条数', `${d.valid_count ?? 0} 有效 / ${d.response_count ?? 0} 总`],
      ];
      Object.entries(obj(d.by_engine)).slice(0, 5).forEach(([e, raw]) => {
        const v = obj(raw);
        rows.push([e, v.error ? `${v.done} 条(${v.error} 报错)` : `${v.done} 条`]);
      });
      return <KvBlock rows={rows} />;
    },
  },

  // ── Ops workbench lane ──────────────────────────────────────────────────────
  admin_topic_overview: {
    title: '项目概览',
    render: (d) => (
      <>
        <KvBlock rows={[
          ['项目', S(d.topic_name)],
          ['官网', S(d['官网'])],
          ['审核状态', S(d['审核状态'])],
          ['计划状态', S(d['计划状态'])],
          ['计划行总数', String(d['计划行总数'] ?? 0)],
        ]} />
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>文章</div>
        <BadgeRow items={countPairs(d['文章漏斗'])} />
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>投放</div>
        <BadgeRow items={countPairs(d['投放漏斗'])} />
      </>
    ),
  },
  admin_get_plan: {
    title: '执行计划书 · 概览',
    render: (d) => (
      <>
        <KvBlock rows={[
          ['计划状态', S(d['计划状态'])],
          ['排期区间', `${S(d['排期起'], '?')} ~ ${S(d['排期止'], '?')}`],
          ['计划行总数', String(d['计划行总数'] ?? 0)],
          ['已出稿 / 未出稿', `${d['已出稿行数'] ?? 0} / ${d['未出稿行数'] ?? 0}`],
        ]} />
        <BadgeRow items={countPairs(d['按平台分布'])} />
      </>
    ),
  },
  admin_list_plan_items: {
    title: '发文计划 · 明细',
    render: (d) => {
      const items = arr(d.items);
      return (
        <TableBlock
          cols={['日期', '种子', '平台', '出稿']}
          rows={items.slice(0, 8).map((r) => [S(r['日期']), S(r['种子']), S(r['平台']), S(r['出稿'])])}
          total={Number(d['窗口内总行数'] ?? items.length)}
        />
      );
    },
  },
  admin_list_docs: {
    title: '文章列表',
    render: (d) => {
      const docs = arr(d.docs);
      return (
        <>
          <BadgeRow items={countPairs(d['各状态篇数'])} />
          <TableBlock
            cols={['#', '标题', '状态', '平台']}
            rows={docs.slice(0, 8).map((r) => [String(r.doc_id), S(r['标题']), S(r['状态']), S(r['平台'])])}
            total={docs.length}
          />
        </>
      );
    },
  },
  admin_list_publish_tasks: {
    title: '投放任务',
    render: (d) => {
      const tasks = arr(d.tasks);
      return (
        <>
          <BadgeRow items={countPairs(d['各状态任务数'])} />
          <TableBlock
            cols={['平台', '媒体', '状态', '链接']}
            rows={tasks.slice(0, 8).map((r) => [
              S(r['平台']), S(r['媒体']), S(r['状态']),
              r['已发链接']
                ? <a href={String(r['已发链接'])} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-secondary)' }}>查看</a>
                : '—',
            ])}
            total={Number(d['任务总数'] ?? tasks.length)}
          />
        </>
      );
    },
  },
  admin_source_insights: {
    title: '信源分析',
    render: (d) => {
      const doms = arr(d['被引域名榜']);
      const quota = arr(d['平台配额预览']);
      return (
        <>
          <KvBlock rows={[
            ['应答总数', String(d['应答总数'] ?? '—')],
            ['自有文章被引', String(d['自有文章被引应答数'] ?? '—')],
          ]} />
          <TableBlock
            cols={['域名', '被引', '类型', '可投']}
            rows={doms.slice(0, 8).map((r) => [S(r['域名']), String(r['被引次数'] ?? '—'), S(r['信源类型']), S(r['可投'])])}
            total={doms.length}
          />
          {quota.length > 0 && <BadgeRow items={quota.slice(0, 8).map((q) => [S(q['平台']), Number(q['每天篇数'] ?? 0)] as [string, number])} />}
        </>
      );
    },
  },
  admin_placement_strategy: {
    title: '投放策略',
    render: (d) => {
      const prio = arr(d['引擎优先级']);
      const plans = arr(d['各引擎投放清单']);
      const byEngine = new Map(plans.map((p) => [String(p['引擎']), strs(p['优先投'])]));
      return (
        <TableBlock
          cols={['引擎', '排名', '命中率', '优先投']}
          rows={prio.slice(0, 8).map((r) => [
            engineLabel(String(r['引擎'])), String(r['排名'] ?? '—'),
            r['命中率'] == null ? '—' : p1(r['命中率']),
            (byEngine.get(String(r['引擎'])) || []).slice(0, 2).join('、') || '—',
          ])}
          total={prio.length}
        />
      );
    },
  },
};

export function CardView({ card, onSend, disabled }: {
  card: AgentCard; onSend?: (text: string) => void; disabled?: boolean;
}) {
  const spec = CARD_SPECS[card.tool];
  if (!spec) return null;
  const body = spec.render(obj(card.data), onSend, disabled);
  if (!body) return null;
  return (
    <div style={{
      marginTop: 8, padding: '10px 12px', borderRadius: 12,
      background: 'var(--bg-surface)', border: '1px solid var(--border-color)',
    }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent-secondary)', marginBottom: 6 }}>
        📊 {spec.title}
      </div>
      {body}
    </div>
  );
}
