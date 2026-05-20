import { useEffect, useMemo, useState } from 'react';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type TrackingMatrix,
  type ResponseRow, type EngineId,
} from '../../services/aiTelemetryApi';
import { useBgLang, engineLabel } from './lang';
import { InfoHint } from './charts';

interface Row {
  query: string;
  seed: string;
  totalRuns: number;
  totalHits: number;
  rate: number;
  firstHitEngine: string;
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
  const { token, topic } = state;
  const L = useBgLang();
  const [matrix, setMatrix] = useState<TrackingMatrix | null>(null);
  const [active, setActive] = useState<Row | null>(null);

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getTrackingMatrix(topic.id, token).then(setMatrix).catch(() => setMatrix(null));
  }, [token, topic?.id]);

  // text → seed 映射:从 topic.queries / topic.query_seeds 同长数组建出
  const seedByQuery = useMemo<Map<string, string>>(() => {
    const m = new Map<string, string>();
    if (!topic) return m;
    const qs = topic.queries || [];
    const seeds = topic.query_seeds || [];
    for (let i = 0; i < qs.length; i++) {
      m.set(qs[i], seeds[i] || '');
    }
    return m;
  }, [topic]);

  const queryRows: Row[] = useMemo(() => {
    if (!matrix) return [];
    return matrix.queries.map(q => {
      const cells = matrix.cells.filter(c => c.query === q);
      const totalRuns = cells.reduce((s, c) => s + c.total_runs, 0);
      const totalHits = cells.reduce((s, c) => s + c.total_hits, 0);
      const firstHitEngine = cells.filter(c => c.first_hit_at).sort((a, b) =>
        (a.first_hit_at || '').localeCompare(b.first_hit_at || ''))[0]?.engine || '';
      return {
        query: q,
        seed: seedByQuery.get(q) || '',
        totalRuns, totalHits,
        rate: totalRuns ? totalHits / totalRuns : 0,
        firstHitEngine,
      };
    });
  }, [matrix, seedByQuery]);

  if (!topic || !matrix) return <div className="text-muted">{L.loading}</div>;

  const brandKeywords = [topic.target, ...(topic.target_aliases || [])].filter(Boolean);

  return (
    <div className="grid gap-4">
      <div className="text-xs text-muted flex items-center gap-1.5">
        {L.queriesSummary(matrix.queries.length, matrix.engines.length)}
        <InfoHint text={L.hintQueries} />
      </div>

      <div className="p-3 rounded-lg overflow-x-auto" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted">
              <th className="text-right px-2 py-2 w-12">{L.queriesColIndex}</th>
              <th className="text-left px-2 py-2">{L.queriesColSeed}</th>
              <th className="text-left px-2 py-2">{L.queriesColExpansion}</th>
              <th className="text-right px-2 py-2">
                <span className="inline-flex items-center gap-1 justify-end">
                  {L.queriesColHit}<InfoHint text={L.hintQueriesTable} />
                </span>
              </th>
              <th className="text-left px-2 py-2">{L.queriesColModel}</th>
              <th className="text-right px-2 py-2 w-20">{L.queriesColAction}</th>
            </tr>
          </thead>
          <tbody>
            {queryRows.map((r, i) => (
              <tr key={r.query} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                <td className="px-2 py-2 text-right tabular-nums text-muted">{i + 1}</td>
                <td className="px-2 py-2 text-primary truncate max-w-[160px]"
                    style={!r.seed ? { color: 'var(--text-muted)' } : undefined}
                    title={r.seed || undefined}>
                  {r.seed || L.queriesNoSeed}
                </td>
                <td className="px-2 py-2 text-primary truncate max-w-[360px]" title={r.query}>{r.query}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.totalHits}</td>
                <td className="px-2 py-2 text-primary">{r.firstHitEngine ? engineLabel(r.firstHitEngine) : L.queriesNeverHit}</td>
                <td className="px-2 py-2 text-right">
                  {r.totalHits > 0 && (
                    <button
                      type="button"
                      onClick={() => setActive(r)}
                      className="text-xs text-accent hover:underline"
                    >
                      {L.queriesColAction}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {active && (
        <QueryDetailModal
          row={active}
          topicId={topic.id}
          token={token}
          brandKeywords={brandKeywords}
          onClose={() => setActive(null)}
        />
      )}
    </div>
  );
}

// ── 命中详情弹窗 ───────────────────────────────────────
function QueryDetailModal({ row, topicId, token, brandKeywords, onClose }: {
  row: Row; topicId: number; token: string;
  brandKeywords: string[]; onClose: () => void;
}) {
  const L = useBgLang();
  const [rows, setRows] = useState<ResponseRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    // 弹窗里不跟随页面 period(7/30/90),直接用后端最大 90 天,避免命中
    // 答案落在窗口外 → 弹窗看着空。
    aiTelemetryApi.listTopicResponses(topicId, token, {
      query: row.query, period: 90, limit: 50,
    })
      .then(setRows)
      .catch(e => {
        setRows([]);
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
  }, [topicId, token, row.query]);

  // 同 engine 取最近一条;命中的排前面
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
          <div className="min-w-0 flex-1 text-sm font-medium text-primary leading-snug">
            <HighlightedText text={row.query} keywords={brandKeywords} />
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
            <div className="text-xs text-muted text-center py-10">
              {L.queriesDetailEmpty}
              {err && <div className="mt-2 text-[10px]" style={{ color: '#ef4444' }}>{err}</div>}
            </div>
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

// ── 关键词高亮 ────────────────────────────────────────
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
  const escaped = kws.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`(${escaped.join('|')})`, 'gi');
  const out: { text: string; match: boolean }[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), match: false });
    out.push({ text: m[0], match: true });
    last = m.index + m[0].length;
    if (m.index === re.lastIndex) re.lastIndex++;
  }
  if (last < text.length) out.push({ text: text.slice(last), match: false });
  return out;
}
