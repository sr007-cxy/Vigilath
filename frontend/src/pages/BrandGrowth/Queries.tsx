import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { BrandGrowthShell, type ShellState } from './shell';
import {
  aiTelemetryApi, type TrackingMatrix,
  type ResponseRow, type EngineId,
} from '../../services/aiTelemetryApi';
import { useBgLang, engineLabel, sortEngines } from './lang';
import { InfoHint } from './charts';

interface Row {
  query: string;
  seed: string;
  isSeed: boolean;            // 2026-05-26 — 是否种子原文 query
  locked: boolean;            // 2026-05-26 — UI 不可取消勾选
  totalRuns: number;
  totalHits: number;
  rate: number;
  hitEngines: string[];
  aliasesHit: string[];       // 2026-05-26 — 此 query 在任一引擎上命中过的 target / alias 字面词
}

type SeedKindFilter = '' | 'seed_only' | 'expanded_only';  // '' = 全部

export function Queries() {
  const L = useBgLang();
  return (
    <BrandGrowthShell title={L.queriesTitle} breadcrumb={[{ label: L.pageTitle, to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

type HitFilter = '' | 'hit' | 'miss'; // '' = 全部 / 'hit' = 命中 / 'miss' = 未命中

function Body({ state }: { state: ShellState }) {
  const { token, topic, selectedEngines } = state;
  const L = useBgLang();
  const [matrix, setMatrix] = useState<TrackingMatrix | null>(null);
  const [active, setActive] = useState<Row | null>(null);
  // 2026-05-26 — seedFilter 从 string(单选)改 string[](多选,空数组 = 全部)
  const [seedFilter, setSeedFilter] = useState<string[]>([]);
  const [hitFilter, setHitFilter] = useState<HitFilter>('');    // 命中筛选,scope 由 soleEngine 决定
  const [seedKindFilter, setSeedKindFilter] = useState<SeedKindFilter>('');  // '' = 全部 / 种子原文 / 扩展
  // 2026-05-26 — 命中词改多选 + AND 语义:勾上「竞天公诚」和「程晓峰」→ 只看两个都命中的 query
  const [hitTermFilter, setHitTermFilter] = useState<string[]>([]);
  const toggleHitTerm = (t: string) => {
    setHitTermFilter(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]);
  };

  const soleEngine: EngineId | null =
    selectedEngines.length === 1 ? (selectedEngines[0] as EngineId) : null;

  // soleEngine 在 → 命中 = 该引擎有命中;否则 = 任一引擎有命中
  const isHitInScope = (r: Row): boolean =>
    soleEngine ? r.hitEngines.includes(soleEngine) : r.totalHits > 0;

  useEffect(() => {
    if (!topic) return;
    aiTelemetryApi.getTrackingMatrix(topic.id, token).then(setMatrix).catch(() => setMatrix(null));
  }, [token, topic?.id]);

  // text → seed 映射:从 topic.queries / topic.query_seeds 同长数组建出
  // 同时建 text → is_seed / locked 标记表(2026-05-26)
  const queryMeta = useMemo<{
    seedByQuery: Map<string, string>;
    isSeedByQuery: Map<string, boolean>;
    lockedByQuery: Map<string, boolean>;
  }>(() => {
    const seedByQuery = new Map<string, string>();
    const isSeedByQuery = new Map<string, boolean>();
    const lockedByQuery = new Map<string, boolean>();
    if (!topic) return { seedByQuery, isSeedByQuery, lockedByQuery };
    const qs = topic.queries || [];
    const seeds = topic.query_seeds || [];
    const isSeed = topic.query_is_seed || [];
    const locked = topic.query_locked || [];
    for (let i = 0; i < qs.length; i++) {
      seedByQuery.set(qs[i], seeds[i] || '');
      isSeedByQuery.set(qs[i], !!isSeed[i]);
      lockedByQuery.set(qs[i], !!locked[i]);
    }
    return { seedByQuery, isSeedByQuery, lockedByQuery };
  }, [topic]);
  const { seedByQuery, isSeedByQuery, lockedByQuery } = queryMeta;

  const queryRows: Row[] = useMemo(() => {
    if (!matrix) return [];
    return matrix.queries.map(q => {
      const cells = matrix.cells.filter(c => c.query === q);
      const totalRuns = cells.reduce((s, c) => s + c.total_runs, 0);
      const totalHits = cells.reduce((s, c) => s + c.total_hits, 0);
      const hitEngines = sortEngines(
        cells.filter(c => c.total_hits > 0).map(c => c.engine),
      );
      // 2026-05-26 — 聚合该 query 在所有引擎上命中过的 target / alias 字面词(去重)
      const aliasesSet = new Set<string>();
      for (const c of cells) {
        for (const a of (c.aliases_hit || [])) aliasesSet.add(a);
      }
      return {
        query: q,
        seed: seedByQuery.get(q) || '',
        isSeed: isSeedByQuery.get(q) || false,
        locked: lockedByQuery.get(q) || false,
        totalRuns, totalHits,
        rate: totalRuns ? totalHits / totalRuns : 0,
        hitEngines,
        aliasesHit: Array.from(aliasesSet),
      };
    });
  }, [matrix, seedByQuery, isSeedByQuery, lockedByQuery]);

  // 命中词下拉选项:从 topic.target + aliases 列出,显示 (count) 命中过此词的 query 数
  // 2026-05-26 — 前端也做 dedup 防御(DB 里 target 跟 alias 可能写重复,如 target='竞天程晓峰'
  // 且 aliases 含 '竞天程晓峰')
  const hitTermOptions = useMemo<{ term: string; count: number }[]>(() => {
    if (!matrix) return [];
    const raw: string[] = [
      ...(matrix.target ? [matrix.target] : []),
      ...(matrix.target_aliases || []),
    ];
    const seen = new Set<string>();
    const uniqueTerms: string[] = [];
    for (const t of raw) {
      const tt = (t || '').trim();
      if (tt && !seen.has(tt)) {
        seen.add(tt);
        uniqueTerms.push(tt);
      }
    }
    const counts = new Map<string, number>();
    for (const r of queryRows) {
      for (const a of r.aliasesHit) counts.set(a, (counts.get(a) || 0) + 1);
    }
    return uniqueTerms.map(t => ({ term: t, count: counts.get(t) || 0 }));
  }, [matrix, queryRows]);

  // 用于筛选下拉的可选值
  const seedOptions = useMemo(() => {
    const s = new Set<string>();
    for (const r of queryRows) if (r.seed) s.add(r.seed);
    return Array.from(s);
  }, [queryRows]);

  const filteredRows = useMemo(() => {
    return queryRows.filter(r => {
      // 空数组 = 不过滤(显示全部);非空 = 只显示选中的种子
      if (seedFilter.length > 0 && !seedFilter.includes(r.seed)) return false;
      if (seedKindFilter === 'seed_only' && !r.isSeed) return false;
      if (seedKindFilter === 'expanded_only' && r.isSeed) return false;
      // AND 语义:每个勾选的 term 都必须在 aliasesHit 里
      if (hitTermFilter.length > 0 &&
          !hitTermFilter.every(t => r.aliasesHit.includes(t))) return false;
      if (hitFilter) {
        const hit = isHitInScope(r);
        if (hitFilter === 'hit' && !hit) return false;
        if (hitFilter === 'miss' && hit) return false;
      }
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryRows, seedFilter, seedKindFilter, hitTermFilter, hitFilter, soleEngine]);

  // 计算每条 seed 下匹配 row 的数量,chip 上显示
  const seedCounts = useMemo<Map<string, number>>(() => {
    const m = new Map<string, number>();
    for (const r of queryRows) {
      if (r.seed) m.set(r.seed, (m.get(r.seed) || 0) + 1);
    }
    return m;
  }, [queryRows]);

  const toggleSeed = (s: string) => {
    setSeedFilter(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
  };

  if (!topic || !matrix) return <div className="text-muted">{L.loading}</div>;

  const brandKeywords = [topic.target, ...(topic.target_aliases || [])].filter(Boolean);
  const inputStyle: CSSProperties = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  };

  return (
    <div className="grid gap-4">
      <div className="text-xs text-muted flex items-center gap-1.5">
        {L.queriesSummary(matrix.queries.length, matrix.engines.length)}
        <InfoHint text={L.hintQueries} />
      </div>

      {/* 筛选区:种子提示词多选 chip + 类型 / 命中下拉 + 计数 */}
      <div
        className="rounded-lg p-3 space-y-2.5"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >
        {/* 第 1 行:种子提示词 chip 多选 */}
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-[11px] uppercase tracking-wide flex-shrink-0"
                style={{ color: 'var(--text-muted)' }}>
            {L.queriesFilterSeedsLabel}
          </span>
          <button
            type="button"
            onClick={() => setSeedFilter([])}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition-all"
            style={seedFilter.length === 0 ? {
              background: 'var(--accent-primary)',
              color: 'var(--solid-btn-text)',
              border: '1px solid var(--accent-primary)',
            } : {
              background: 'transparent',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-color)',
            }}
          >
            <span>{L.queriesFilterAllSeeds}</span>
            <span className="text-[10px] opacity-70 tabular-nums">{queryRows.length}</span>
          </button>
          {seedOptions.map(s => {
            const selected = seedFilter.includes(s);
            return (
              <button
                key={s}
                type="button"
                onClick={() => toggleSeed(s)}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition-all max-w-[260px]"
                style={selected ? {
                  background: 'var(--accent-primary)',
                  color: 'var(--solid-btn-text)',
                  border: '1px solid var(--accent-primary)',
                } : {
                  background: 'transparent',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                }}
                title={s}
              >
                {selected && (
                  <span aria-hidden style={{ flexShrink: 0, fontSize: 10 }}>✓</span>
                )}
                <span className="truncate">{s}</span>
                <span className="text-[10px] opacity-70 tabular-nums flex-shrink-0">
                  {seedCounts.get(s) || 0}
                </span>
              </button>
            );
          })}
        </div>

        {/* 第 2 行:类型 / 命中下拉 + 计数 */}
        <div className="flex items-center gap-2 text-xs flex-wrap"
             style={{
               paddingTop: '8px',
               borderTop: '1px dashed var(--border-color)',
             }}>
          {/* 类型 select(扩展词 vs 种子提示词)*/}
          <span className="text-[11px] uppercase tracking-wide flex-shrink-0"
                style={{ color: 'var(--text-muted)' }}>
            {L.queriesFilterSeedKindLabel}
          </span>
          <select
            value={seedKindFilter}
            onChange={e => setSeedKindFilter(e.target.value as SeedKindFilter)}
            className="px-2 py-1 rounded-md text-[11px]"
            style={{ ...inputStyle, minWidth: 130 }}
            title={L.queriesFilterSeedKindHint}
          >
            <option value="">{L.queriesFilterAllSeedKind}</option>
            <option value="seed_only">{L.queriesFilterOnlySeed}</option>
            <option value="expanded_only">{L.queriesFilterOnlyExpanded}</option>
          </select>
          {/* 命中状态 select */}
          <span className="text-[11px] uppercase tracking-wide flex-shrink-0 ml-2"
                style={{ color: 'var(--text-muted)' }}>
            命中
          </span>
          <select
            value={hitFilter}
            onChange={e => setHitFilter(e.target.value as HitFilter)}
            className="px-2 py-1 rounded-md text-[11px]"
            style={{ ...inputStyle, minWidth: 130 }}
          >
            <option value="">{L.queriesFilterAllHit}</option>
            <option value="hit">{L.queriesFilterOnlyHit}</option>
            <option value="miss">{L.queriesFilterOnlyMiss}</option>
          </select>
          {/* 计数 — 移到这行末尾 */}
          {soleEngine && (
            <span
              className="px-2 py-0.5 rounded-md text-[11px]"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}
            >
              {L.queriesFilterEngineBadge(engineLabel(soleEngine))}
            </span>
          )}
          <span className="ml-auto text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
            <strong style={{ color: 'var(--text-primary)' }}>{filteredRows.length}</strong>
            {' '}/ {queryRows.length}
          </span>
        </div>

        {/* 第 3 行:命中词 chip 多选(AND 语义)*/}
        {hitTermOptions.length > 0 && (
          <div className="flex items-baseline gap-2 flex-wrap"
               style={{
                 paddingTop: '8px',
                 borderTop: '1px dashed var(--border-color)',
               }}>
            <span className="text-[11px] uppercase tracking-wide flex-shrink-0"
                  style={{ color: 'var(--text-muted)' }}
                  title={L.queriesFilterHitTermHint}>
              {L.queriesFilterHitTermLabel}
              {hitTermFilter.length > 1 && (
                <span className="ml-1 normal-case text-[10px]" style={{ opacity: 0.7 }}>
                  ({L.queriesFilterHitTermAnd})
                </span>
              )}
            </span>
            <button
              type="button"
              onClick={() => setHitTermFilter([])}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition-all"
              style={hitTermFilter.length === 0 ? {
                background: 'var(--accent-primary)',
                color: 'var(--solid-btn-text)',
                border: '1px solid var(--accent-primary)',
              } : {
                background: 'transparent',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
              }}
            >
              <span>{L.queriesFilterAllHitTerms}</span>
            </button>
            {hitTermOptions.map(o => {
              const selected = hitTermFilter.includes(o.term);
              return (
                <button
                  key={o.term}
                  type="button"
                  onClick={() => toggleHitTerm(o.term)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition-all max-w-[260px]"
                  style={selected ? {
                    background: 'var(--accent-primary)',
                    color: 'var(--solid-btn-text)',
                    border: '1px solid var(--accent-primary)',
                  } : {
                    background: 'transparent',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-color)',
                  }}
                  title={o.term}
                >
                  {selected && (
                    <span aria-hidden style={{ flexShrink: 0, fontSize: 10 }}>✓</span>
                  )}
                  <span className="truncate">{o.term}</span>
                  <span className="text-[10px] opacity-70 tabular-nums flex-shrink-0">
                    {o.count}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div
        className="rounded-lg overflow-auto"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          maxHeight: 'clamp(420px, 70vh, 720px)',
        }}
      >
        <table className="w-full text-xs">
          <thead className="sticky top-0 z-10" style={{ background: 'var(--bg-card)' }}>
            <tr className="text-muted" style={{ boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
              <th className="text-right px-2 py-2 w-12">{L.queriesColIndex}</th>
              {/* 2026-05-26 — 种子提示词列暂时隐藏,seed 已由上方 chip 多选承载;
                  恢复时取消下行 + 表 body 里对应 td 的注释 */}
              {/* <th className="text-left px-2 py-2">{L.queriesColSeed}</th> */}
              <th className="text-left px-2 py-2">{L.queriesColExpansion}</th>
              <th className="text-center px-2 py-2 w-20">
                <span className="inline-flex items-center gap-1 justify-center">
                  {L.queriesColHit}<InfoHint text={L.hintQueriesTable} />
                </span>
              </th>
              <th className="text-left px-2 py-2">{L.queriesColModel}</th>
              <th className="text-right px-2 py-2 w-20">{L.queriesColAction}</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-10 text-center text-muted">
                  {L.queriesEmptyAfterFilter}
                </td>
              </tr>
            ) : filteredRows.map((r, i) => (
              <tr key={r.query} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                <td className="px-2 py-2 text-right tabular-nums text-muted">{i + 1}</td>
                {/* 种子提示词列暂隐藏(2026-05-26)
                <td className="px-2 py-2 text-primary truncate max-w-[200px]"
                    style={!r.seed ? { color: 'var(--text-muted)' } : undefined}
                    title={r.seed || undefined}>
                  {r.seed ? (
                    <span className="inline-flex items-center gap-1">
                      <span aria-hidden style={{ flexShrink: 0 }}>🌱</span>
                      <span className="truncate">{r.seed}</span>
                    </span>
                  ) : L.queriesNoSeed}
                </td>
                */}
                <td className="px-2 py-2 text-primary truncate max-w-[360px]" title={r.query}>
                  {r.isSeed && (
                    <span
                      className="inline-flex items-center mr-1.5 px-1.5 py-0.5 rounded text-[9px] font-medium"
                      style={{ background: 'rgba(99,102,241,0.18)', color: '#6366f1' }}
                      title={L.queriesSeedOriginalBadgeHint}
                    >
                      {L.queriesSeedOriginalBadge}
                    </span>
                  )}
                  {r.query}
                </td>
                <td className="px-2 py-2 text-center">
                  {isHitInScope(r) ? (
                    <span
                      className="inline-flex items-center justify-center w-5 h-5 rounded-full text-[11px] font-medium"
                      style={{ background: 'rgba(34,197,94,0.18)', color: '#15803d' }}
                      title={L.queriesDetailHitBadge}
                    >
                      ✓
                    </span>
                  ) : (
                    <span className="text-muted">{L.queriesNoSeed}</span>
                  )}
                </td>
                <td className="px-2 py-2 text-primary">
                  {(() => {
                    // 顶部 chip 选了单一引擎 → 只渲染该引擎的命中标签;
                    // 「全部」时仍展示该 query 命中过的全部引擎
                    const enginesToShow = soleEngine
                      ? (r.hitEngines.includes(soleEngine) ? [soleEngine] : [])
                      : r.hitEngines;
                    if (enginesToShow.length === 0) {
                      return <span className="text-muted">{L.queriesNeverHit}</span>;
                    }
                    return (
                      <span className="inline-flex flex-wrap gap-1">
                        {enginesToShow.map(e => (
                          <span
                            key={e}
                            className="px-1.5 py-0.5 rounded text-[10px]"
                            style={{ background: 'rgba(34,197,94,0.15)', color: '#15803d' }}
                          >
                            {engineLabel(e)}
                          </span>
                        ))}
                      </span>
                    );
                  })()}
                </td>
                <td className="px-2 py-2 text-right">
                  {isHitInScope(r) && (
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
          engineFilter={soleEngine}
          onClose={() => setActive(null)}
        />
      )}
    </div>
  );
}

// ── 命中详情弹窗 ───────────────────────────────────────
function QueryDetailModal({ row, topicId, token, brandKeywords, engineFilter, onClose }: {
  row: Row; topicId: number; token: string;
  brandKeywords: string[]; engineFilter: EngineId | null; onClose: () => void;
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
      ...(engineFilter ? { engine: engineFilter } : {}),
    })
      .then(setRows)
      .catch(e => {
        setRows([]);
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
  }, [topicId, token, row.query, engineFilter]);

  // 弹窗只展示命中的引擎:按 engine 取最近一条命中(hit=true),未命中的不进结果集
  // 顶部 chip 选了单一引擎时,engineFilter 会让后端只返回该引擎,这里再做一次防御兜底
  const hitEngines = useMemo(() => {
    if (!rows) return [];
    const m = new Map<EngineId, ResponseRow>();
    for (const r of rows) {
      if (!r.hit) continue;
      if (engineFilter && r.engine !== engineFilter) continue;
      const prev = m.get(r.engine);
      if (!prev || new Date(r.created_at) > new Date(prev.created_at)) m.set(r.engine, r);
    }
    return Array.from(m.values()).sort((a, b) => a.engine.localeCompare(b.engine));
  }, [rows, engineFilter]);

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
          ) : hitEngines.length === 0 ? (
            <div className="text-xs text-muted text-center py-10">
              {L.queriesDetailEmpty}
              {err && <div className="mt-2 text-[10px]" style={{ color: '#ef4444' }}>{err}</div>}
            </div>
          ) : (
            <>
              <div className="text-[11px] text-muted">
                {L.queriesDetailEnginesHeader(hitEngines.length)}
              </div>
              {hitEngines.map(r => (
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
          style={{ background: 'rgba(34,197,94,0.15)', color: '#15803d' }}>
          ✓ {row.brand_rank ? `Top${row.brand_rank}` : L.queriesDetailHitBadge}
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
