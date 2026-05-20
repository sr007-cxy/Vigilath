import { useEffect, useMemo, useState } from 'react';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type TrackingMatrix, type IntentBreakdown,
  type ResponseRow, type EngineId,
} from '../../services/aiTelemetryApi';
import { useBgLang, engineLabel } from './lang';
import { InfoHint } from './charts';

type HitFilter = 'all' | 'hit' | 'miss';

interface Row {
  idx: number;
  query: string;
  seed: string;
  hitEngines: EngineId[];
  totalEngines: number;
  totalHits: number;
  totalRuns: number;
}

export function Queries() {
  const L = useBgLang();
  return (
    <BrandGrowthShell title={L.queriesTitle} breadcrumb={[{ label: L.pageTitle, to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const L = useBgLang();
  const [matrix, setMatrix] = useState<TrackingMatrix | null>(null);
  const [intent, setIntent] = useState<IntentBreakdown | null>(null);
  const [active, setActive] = useState<Row | null>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<HitFilter>('all');

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getTrackingMatrix(topic.id, token).then(setMatrix).catch(() => setMatrix(null));
    aiTelemetryApi.getIntentBreakdown(topic.id, period, token).then(setIntent).catch(() => setIntent(null));
  }, [token, topic?.id, period]);

  // queries → seed 映射(仅当用户从扩展流程入库,后端才有 seed 字段)
  const seedByQuery = useMemo(() => {
    const m = new Map<string, string>();
    if (!topic) return m;
    (topic.queries || []).forEach((q, i) => {
      const s = (topic.query_seeds || [])[i];
      if (q && s) m.set(q, s);
    });
    return m;
  }, [topic]);

  const rows: Row[] = useMemo(() => {
    if (!matrix) return [];
    return matrix.queries.map((q, i) => {
      const cells = matrix.cells.filter(c => c.query === q);
      const hitEngines = cells.filter(c => c.total_hits > 0).map(c => c.engine);
      return {
        idx: i + 1,
        query: q,
        seed: seedByQuery.get(q) || '',
        hitEngines,
        totalEngines: matrix.engines.length,
        totalHits: cells.reduce((s, c) => s + c.total_hits, 0),
        totalRuns: cells.reduce((s, c) => s + c.total_runs, 0),
      };
    });
  }, [matrix, seedByQuery]);

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase();
    return rows.filter(r => {
      if (kw && !(r.query.toLowerCase().includes(kw) || r.seed.toLowerCase().includes(kw))) {
        return false;
      }
      if (filter === 'hit') return r.totalHits > 0;
      if (filter === 'miss') return r.totalHits === 0;
      return true;
    });
  }, [rows, search, filter]);

  if (!topic || !matrix) return <div className="text-muted">{L.loading}</div>;

  const brandKeywords = [topic.target, ...(topic.target_aliases || [])].filter(Boolean);

  return (
    <div className="grid gap-4">
      <div className="text-xs text-muted flex items-center gap-1.5 flex-wrap">
        <span>{L.queriesSummary(matrix.queries.length, matrix.engines.length)}</span>
        <InfoHint text={L.hintQueries} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={L.queriesSearchPlaceholder}
          className="text-xs px-3 py-1.5 rounded-md flex-1 min-w-[200px] max-w-[360px]"
          style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                   border: '1px solid var(--border-color)' }}
        />
        <FilterPill active={filter === 'all'} onClick={() => setFilter('all')} label={L.queriesFilterAll} />
        <FilterPill active={filter === 'hit'} onClick={() => setFilter('hit')} label={L.queriesFilterHit} />
        <FilterPill active={filter === 'miss'} onClick={() => setFilter('miss')} label={L.queriesFilterMiss} />
      </div>

      <div className="p-3 rounded-lg overflow-x-auto"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted" style={{ background: 'var(--bg-input)' }}>
              <th className="text-left px-3 py-2 w-12">{L.queriesColIdx}</th>
              <th className="text-left px-3 py-2 w-[200px]">{L.queriesColSeed}</th>
              <th className="text-left px-3 py-2">{L.queriesColExpanded}</th>
              <th className="text-left px-3 py-2 w-[260px]">{L.queriesColEngines}</th>
              <th className="text-right px-3 py-2 w-[80px]">{L.queriesColAction}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(r => (
              <tr key={r.query} className="border-t hover:bg-[var(--bg-input)]"
                style={{ borderColor: 'var(--border-color)' }}>
                <td className="px-3 py-2 text-muted tabular-nums">{r.idx}</td>
                <td className="px-3 py-2 text-secondary truncate max-w-[200px]" title={r.seed}>
                  {r.seed || <span className="text-muted">{L.queriesNoSeed}</span>}
                </td>
                <td className="px-3 py-2 text-primary">
                  <HighlightedText text={r.query} keywords={brandKeywords} />
                </td>
                <td className="px-3 py-2">
                  <EngineBadges
                    all={matrix.engines}
                    hit={r.hitEngines}
                  />
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => setActive(r)}
                    className="text-xs px-2.5 py-1 rounded text-accent hover:underline"
                  >
                    {L.queriesViewDetail}
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="text-center text-muted py-8">{L.sourcesNoData}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {intent && intent.clusters.length > 0 && (
        <div className="p-4 rounded-lg"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <h3 className="text-sm font-medium text-primary mb-3 pb-2 border-b flex items-center gap-2"
            style={{ borderColor: 'var(--border-color)' }}>
            <span className="w-1 h-4 rounded-sm" style={{ background: 'var(--accent-primary)' }} />
            {L.queriesIntentBlock}
            <InfoHint text={L.hintIntentBreakdown} />
          </h3>
          <p className="text-[11px] text-muted mb-3">{L.queriesIntentHint}</p>
          <ul className="space-y-2">
            {intent.clusters.map(c => (
              <li key={c.cluster_id} className="flex items-center gap-2 text-xs">
                <span className="text-primary w-40 truncate">{c.label}</span>
                <div className="flex-1 h-2 rounded overflow-hidden" style={{ background: 'var(--bg-input)' }}>
                  <div className="h-full" style={{
                    width: `${c.mention_rate * 100}%`,
                    background: c.mention_rate >= 0.5 ? 'var(--accent-primary)' : c.mention_rate >= 0.25 ? '#f59e0b' : '#ef4444',
                  }} />
                </div>
                <span className="text-muted tabular-nums w-20 text-right">
                  {(c.mention_rate * 100).toFixed(0)}% ({c.mention_count}/{c.response_count})
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {active && topic && (
        <QueryDetailModal
          row={active}
          topicId={topic.id}
          token={token}
          period={period}
          brandKeywords={brandKeywords}
          onClose={() => setActive(null)}
        />
      )}
    </div>
  );
}

function FilterPill({ active, onClick, label }: {
  active: boolean; onClick: () => void; label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-xs px-3 py-1.5 rounded-full transition"
      style={{
        background: active ? 'var(--accent-primary)' : 'var(--bg-input)',
        color: active ? 'white' : 'var(--text-secondary)',
        border: '1px solid var(--border-color)',
      }}
    >
      {label}
    </button>
  );
}

function EngineBadges({ all, hit }: { all: EngineId[]; hit: EngineId[] }) {
  const hitSet = new Set(hit);
  return (
    <div className="flex flex-wrap gap-1">
      {all.map(e => {
        const on = hitSet.has(e);
        return (
          <span key={e}
            className="text-[10px] px-1.5 py-0.5 rounded"
            title={engineLabel(e)}
            style={{
              background: on ? 'rgba(34,197,94,0.15)' : 'var(--bg-input)',
              color: on ? '#15803d' : 'var(--text-secondary)',
              border: `1px solid ${on ? 'rgba(34,197,94,0.4)' : 'var(--border-color)'}`,
            }}
          >
            {on ? '●' : '○'} {engineLabel(e)}
          </span>
        );
      })}
    </div>
  );
}

// ── 检索词高亮 ───────────────────────────────────────
function HighlightedText({ text, keywords }: { text: string; keywords: string[] }) {
  const parts = useMemo(() => splitByKeywords(text, keywords), [text, keywords]);
  return (
    <>
      {parts.map((p, i) =>
        p.match ? (
          <mark key={i} className="px-0.5 rounded"
            style={{ background: 'rgba(250,204,21,0.45)', color: 'inherit' }}>
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        )
      )}
    </>
  );
}

function splitByKeywords(text: string, keywords: string[]): { text: string; match: boolean }[] {
  const kws = keywords.filter(k => k && k.length > 0).sort((a, b) => b.length - a.length);
  if (kws.length === 0) return [{ text, match: false }];
  // 用大小写不敏感的正则切分;转义元字符
  const escaped = kws.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`(${escaped.join('|')})`, 'gi');
  const out: { text: string; match: boolean }[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), match: false });
    out.push({ text: m[0], match: true });
    last = m.index + m[0].length;
    if (m.index === re.lastIndex) re.lastIndex++;  // 防止零宽匹配死循环
  }
  if (last < text.length) out.push({ text: text.slice(last), match: false });
  return out;
}

// ── 查看 — 详情弹窗(各引擎回答) ─────────────────────
function QueryDetailModal({ row, topicId, token, period, brandKeywords, onClose }: {
  row: Row; topicId: number; token: string; period: number;
  brandKeywords: string[]; onClose: () => void;
}) {
  const L = useBgLang();
  const [rows, setRows] = useState<ResponseRow[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    aiTelemetryApi.listTopicResponses(topicId, token, {
      query: row.query, period, limit: 50,
    }).then(setRows).catch(() => setRows([])).finally(() => setLoading(false));
  }, [topicId, token, period, row.query]);

  // 同一 engine 取最近一条回答展示
  const latestByEngine = useMemo(() => {
    if (!rows) return [];
    const m = new Map<EngineId, ResponseRow>();
    for (const r of rows) {
      const prev = m.get(r.engine);
      if (!prev || new Date(r.created_at) > new Date(prev.created_at)) m.set(r.engine, r);
    }
    return Array.from(m.values()).sort((a, b) =>
      Number(b.hit ?? 0) - Number(a.hit ?? 0) || a.engine.localeCompare(b.engine));
  }, [rows]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[85vh] overflow-hidden rounded-lg flex flex-col"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-4 py-3 border-b flex-shrink-0"
          style={{ borderColor: 'var(--border-color)' }}>
          <div className="min-w-0 flex-1">
            {row.seed && (
              <div className="text-[10px] text-muted mb-1 truncate">
                {L.queriesColSeed}:{row.seed}
              </div>
            )}
            <div className="text-sm font-medium text-primary leading-snug">
              <HighlightedText text={row.query} keywords={brandKeywords} />
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-3 text-muted hover:text-primary text-lg leading-none flex-shrink-0"
            aria-label={L.queriesDetailClose}
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {loading ? (
            <div className="text-xs text-muted text-center py-10">{L.loading}</div>
          ) : latestByEngine.length === 0 ? (
            <div className="text-xs text-muted text-center py-10">{L.queriesDetailEmpty}</div>
          ) : (
            <>
              <div className="text-[11px] text-muted">
                {L.queriesDetailEnginesHeader(latestByEngine.length)}
              </div>
              {latestByEngine.map(r => (
                <EngineAnswerCard key={r.id} row={r} brandKeywords={brandKeywords} />
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function EngineAnswerCard({ row, brandKeywords }: {
  row: ResponseRow; brandKeywords: string[];
}) {
  const L = useBgLang();
  return (
    <div className="rounded-md p-3 text-xs"
      style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-primary font-medium">{engineLabel(row.engine)}</span>
        <span className="px-1.5 py-0.5 rounded text-[10px]"
          style={{
            background: row.hit ? 'rgba(34,197,94,0.15)' : 'rgba(148,163,184,0.15)',
            color: row.hit ? '#15803d' : '#64748b',
          }}>
          {row.hit
            ? `✓ ${row.brand_rank ? `Top${row.brand_rank}` : L.queriesDetailHitBadge}`
            : `✕ ${L.queriesDetailMissBadge}`}
        </span>
        <span className="text-muted ml-auto">{new Date(row.created_at).toLocaleString()}</span>
      </div>
      <div className="text-secondary whitespace-pre-wrap leading-relaxed max-h-72 overflow-y-auto">
        <HighlightedText text={row.answer || ''} keywords={brandKeywords} />
      </div>
      {row.citations && row.citations.length > 0 && (
        <div className="mt-2 pt-2 border-t text-[10px]" style={{ borderColor: 'var(--border-color)' }}>
          <div className="text-muted mb-1">{L.queriesDetailCitations}:</div>
          <ul className="space-y-0.5">
            {row.citations.map((c, i) => (
              <li key={i}>
                <a href={c.url} target="_blank" rel="noopener noreferrer"
                  className="text-accent hover:underline">
                  [{i + 1}] {c.domain}{c.title ? ` · ${c.title}` : ''}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
