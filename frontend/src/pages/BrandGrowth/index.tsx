import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type Overview, type PositionBreakdownResp,
  type Briefing, type Topic,
} from '../../services/aiTelemetryApi';
import { contentApi, type ContentDoc } from '../../services/contentApi';

export function BrandGrowth() {
  return (
    <BrandGrowthShell title="品牌增长">
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const [overview, setOverview] = useState<Overview | null>(null);
  const [pb, setPb] = useState<PositionBreakdownResp | null>(null);
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [published, setPublished] = useState<ContentDoc[]>([]);

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getOverview(topic.id, period, token).then(setOverview).catch(() => setOverview(null));
    aiTelemetryApi.getPositionBreakdown(topic.id, period, token).then(setPb).catch(() => setPb(null));
    aiTelemetryApi.listBriefings(topic.id, token, 10).then(setBriefings).catch(() => setBriefings([]));
    contentApi.listDocs(topic.id, { status: 'published' }, token).then(setPublished).catch(() => setPublished([]));
  }, [token, topic?.id, period]);

  if (!topic) return null;

  return (
    <div className="grid gap-4 max-w-[1400px] mx-auto">
      <TopMetricsRow overview={overview} topic={topic} />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <RadarBlock pb={pb} />
        <EntryCardGrid overview={overview} pb={pb} />
        <CoreMetricsPanel pb={pb} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BriefingsBlock briefings={briefings} topic={topic} />
        <PublishedFeedBlock published={published} topic={topic} />
      </div>
    </div>
  );
}

// ── 顶部 3 大数 ───────────────────────────────────────
function TopMetricsRow({ overview, topic }: { overview: Overview | null; topic: Topic }) {
  const navigate = useNavigate();
  const total = overview?.citations.value ?? 0;
  const owned = overview?.owned_split.owned ?? 0;
  const other = overview?.owned_split.other ?? 0;
  const totalDelta = overview?.citations.delta_pct ?? null;
  const ownedDelta = overview?.owned_split.delta_pct ?? null;
  const tq = topic.id;
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <BigMetric label="推荐总词数" value={total} delta={totalDelta}
        onClick={() => navigate(`/brand-growth/responses?topic=${tq}`)} />
      <BigMetric label="权威媒体推荐数" value={owned} delta={ownedDelta}
        onClick={() => navigate(`/brand-growth/sources?topic=${tq}&filter=owned`)} />
      <BigMetric label="第三方引用总数" value={other}
        onClick={() => navigate(`/brand-growth/sources?topic=${tq}&filter=third_party`)} />
    </div>
  );
}

function BigMetric({ label, value, delta, onClick }: {
  label: string; value: number; delta?: number | null; onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="p-5 rounded-lg text-left transition hover:scale-[1.01]"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <div className="text-xs text-muted mb-2">{label}</div>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-bold text-primary tabular-nums">{value.toLocaleString()}</span>
        {typeof delta === 'number' && delta !== 0 && (
          <span className="text-xs tabular-nums"
            style={{ color: delta > 0 ? '#10b981' : '#ef4444' }}>
            {delta > 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(1)}%
          </span>
        )}
      </div>
    </button>
  );
}

// ── 雷达(5 维)──────────────────────────────────────
function RadarBlock({ pb }: { pb: PositionBreakdownResp | null }) {
  const navigate = useNavigate();
  if (!pb) {
    return <CardShell title="趋势分图(5 维占比)"><div className="text-xs text-muted py-10 text-center">暂无数据</div></CardShell>;
  }
  const dims: { key: 'top1_pct' | 'top3_pct' | 'top5_pct' | 'visible_pct' | 'source_pct'; label: string; layer: string }[] = [
    { key: 'top1_pct', label: 'Top1 占比', layer: 'top1' },
    { key: 'visible_pct', label: '可见占比', layer: 'visible' },
    { key: 'source_pct', label: '信源占比', layer: 'source' },
    { key: 'top5_pct', label: 'Top5 占比', layer: 'top5' },
    { key: 'top3_pct', label: 'Top3 占比', layer: 'top3' },
  ];
  const max = Math.max(1, ...dims.map(d => pb.breakdown[d.key]));
  const baseline = pb.industry_baseline;
  // 简单 SVG 雷达
  const cx = 130, cy = 130, r = 100;
  const points = (vals: number[]) =>
    vals.map((v, i) => {
      const angle = (Math.PI * 2 / vals.length) * i - Math.PI / 2;
      const dist = (v / max) * r;
      return `${cx + Math.cos(angle) * dist},${cy + Math.sin(angle) * dist}`;
    }).join(' ');
  const labels = dims.map((d, i) => {
    const angle = (Math.PI * 2 / dims.length) * i - Math.PI / 2;
    const lx = cx + Math.cos(angle) * (r + 18);
    const ly = cy + Math.sin(angle) * (r + 18);
    return { ...d, lx, ly };
  });
  const brandPts = points(dims.map(d => pb.breakdown[d.key]));
  const basePts = baseline ? points(dims.map(d => baseline[d.key])) : null;

  return (
    <CardShell title="趋势分图(5 维占比)">
      <svg viewBox="0 0 260 260" className="w-full max-w-[260px] mx-auto">
        {[0.25, 0.5, 0.75, 1].map(s => (
          <polygon
            key={s} fill="none" stroke="var(--border-color)" strokeWidth="0.5"
            points={points(dims.map(() => max * s))}
          />
        ))}
        {basePts && (
          <polygon fill="rgba(148,163,184,0.18)" stroke="rgba(148,163,184,0.6)" strokeWidth="1" points={basePts} />
        )}
        <polygon fill="rgba(59,130,246,0.25)" stroke="var(--accent-primary)" strokeWidth="1.5" points={brandPts} />
        {labels.map(l => (
          <text
            key={l.key} x={l.lx} y={l.ly}
            fontSize="9" textAnchor="middle" dominantBaseline="middle"
            fill="var(--text-secondary)"
            style={{ cursor: 'pointer' }}
            onClick={() => navigate(`/brand-growth/matrix?topic=${pb.topic_id}&layer=${l.layer}`)}
          >
            {l.label}
          </text>
        ))}
      </svg>
      {!baseline && (
        <div className="text-[10px] text-muted text-center mt-2">行业基准样本不足,暂不展示</div>
      )}
    </CardShell>
  );
}

// ── 6 入口卡 ─────────────────────────────────────────
function EntryCardGrid({ overview, pb }: { overview: Overview | null; pb: PositionBreakdownResp | null }) {
  const navigate = useNavigate();
  const tid = overview?.topic_id ?? pb?.topic_id ?? 0;
  const cards: { title: string; sub: string; to: string }[] = [
    {
      title: '信源分析',
      sub: overview?.top_domains[0] ? `Top: ${overview.top_domains[0].domain}` : '—',
      to: `/brand-growth/sources?topic=${tid}`,
    },
    {
      title: '平台分析',
      sub: `${overview?.engines_covered.value ?? 0} / ${overview?.engines_total ?? 0} 引擎`,
      to: `/brand-growth/engines?topic=${tid}`,
    },
    {
      title: '竞品分析',
      sub: '被替代证据 →',
      to: `/brand-growth/competitors?topic=${tid}`,
    },
    {
      title: 'AI 词测验',
      sub: '命中矩阵 →',
      to: `/brand-growth/matrix?topic=${tid}`,
    },
    {
      title: '智能洞察',
      sub: 'briefings + 诊断',
      to: `/brand-growth/insights?topic=${tid}`,
    },
    {
      title: '关键词管理',
      sub: '只读 · 配置在 admin',
      to: `/brand-growth/queries?topic=${tid}`,
    },
  ];
  return (
    <CardShell title="功能入口">
      <div className="grid grid-cols-3 gap-2">
        {cards.map(c => (
          <button
            key={c.title}
            type="button"
            onClick={() => navigate(c.to)}
            className="p-3 rounded text-left hover:scale-[1.02] transition"
            style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
          >
            <div className="text-sm font-medium text-primary mb-1">{c.title}</div>
            <div className="text-[10px] text-muted truncate">{c.sub}</div>
          </button>
        ))}
      </div>
    </CardShell>
  );
}

// ── 右核心指标 4+1 ────────────────────────────────────
function CoreMetricsPanel({ pb }: { pb: PositionBreakdownResp | null }) {
  const navigate = useNavigate();
  if (!pb) {
    return <CardShell title="核心指标表现"><div className="text-xs text-muted py-10 text-center">暂无数据</div></CardShell>;
  }
  const baseline = pb.industry_baseline;
  const metrics: { key: keyof typeof pb.breakdown; label: string; layer: string }[] = [
    { key: 'top1_pct', label: 'Top1 占比', layer: 'top1' },
    { key: 'visible_pct', label: '可见占比', layer: 'visible' },
    { key: 'top5_pct', label: 'Top5 占比', layer: 'top5' },
    { key: 'source_pct', label: '信源占比', layer: 'source' },
  ];
  return (
    <CardShell title="核心指标表现">
      <div className="grid grid-cols-2 gap-2">
        {metrics.map(m => {
          const v = pb.breakdown[m.key];
          const bv = baseline ? baseline[m.key] : null;
          return (
            <button
              key={m.key}
              type="button"
              onClick={() => navigate(`/brand-growth/matrix?topic=${pb.topic_id}&layer=${m.layer}`)}
              className="p-3 rounded text-left hover:scale-[1.02] transition"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
            >
              <div className="text-[10px] text-muted">{m.label}</div>
              <div className="text-xl font-bold text-primary tabular-nums">{v.toFixed(2)}%</div>
              {bv !== null && (
                <div className="text-[10px] text-muted">行业 {bv.toFixed(2)}%</div>
              )}
            </button>
          );
        })}
      </div>
    </CardShell>
  );
}

// ── 报告明细(briefings) ──────────────────────────────
function BriefingsBlock({ briefings, topic }: { briefings: Briefing[]; topic: Topic }) {
  return (
    <CardShell
      title="报告明细"
      action={<Link to={`/brand-growth/insights?topic=${topic.id}`} className="text-xs text-accent">查看全部 →</Link>}
    >
      {briefings.length === 0 ? (
        <div className="text-xs text-muted py-6 text-center">暂无 briefing</div>
      ) : (
        <ul className="divide-y" style={{ borderColor: 'var(--border-color)' }}>
          {briefings.slice(0, 6).map(b => (
            <li key={b.id} className="py-2">
              <Link to={`/brand-growth/insights?topic=${topic.id}&briefing=${b.id}`}
                className="text-sm text-primary hover:underline block truncate">
                Briefing · {new Date(b.period_start).toLocaleDateString()} → {new Date(b.period_end).toLocaleDateString()}
              </Link>
              <div className="text-[10px] text-muted">生成于 {new Date(b.generated_at).toLocaleDateString()}</div>
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}

// ── 投放战果 ─────────────────────────────────────────
function PublishedFeedBlock({ published, topic }: { published: ContentDoc[]; topic: Topic }) {
  return (
    <CardShell
      title="投放战果"
      action={<Link to={`/brand-growth/published?topic=${topic.id}`} className="text-xs text-accent">查看全部 →</Link>}
    >
      {published.length === 0 ? (
        <div className="text-xs text-muted py-6 text-center">暂无已投放内容</div>
      ) : (
        <ul className="divide-y" style={{ borderColor: 'var(--border-color)' }}>
          {published.slice(0, 6).map(d => {
            const target = d.publish_targets?.[0];
            return (
              <li key={d.id} className="py-2">
                <a
                  href={target?.url || `/brand-growth/published?topic=${topic.id}&doc=${d.id}`}
                  target={target?.url ? '_blank' : undefined}
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline block truncate"
                >
                  {d.title}
                </a>
                <div className="text-[10px] text-muted">
                  {target ? `${target.platform} · ${target.media}` : '未标记平台'}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </CardShell>
  );
}

// ── shared ───────────────────────────────────────────
function CardShell({ title, action, children }: {
  title: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-primary">{title}</h3>
        {action}
      </div>
      {children}
    </div>
  );
}
