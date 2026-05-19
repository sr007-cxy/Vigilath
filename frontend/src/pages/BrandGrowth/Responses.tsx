import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import { aiTelemetryApi, type ResponseRow } from '../../services/aiTelemetryApi';

export function Responses() {
  return (
    <BrandGrowthShell title="原始引用" breadcrumb={[{ label: '品牌增长', to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic, period } = state;
  const [params] = useSearchParams();
  const engine = params.get('engine') || undefined;
  const queryFilter = params.get('q') || undefined;
  const [rows, setRows] = useState<ResponseRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    if (!topic) return;
    setLoading(true);
    aiTelemetryApi.listTopicResponses(topic.id, token, {
      engine, query: queryFilter, period, limit: 200,
    }).then(setRows).catch(() => setRows([])).finally(() => setLoading(false));
  }, [token, topic?.id, period, engine, queryFilter]);

  if (!topic) return null;

  return (
    <div className="max-w-[1200px] mx-auto grid gap-4">
      <div className="text-xs text-muted">
        {engine && `引擎: ${engine} · `}
        {queryFilter && `Query: ${queryFilter} · `}
        近 {period} 天共 {rows.length} 条
      </div>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">加载中…</div>
      ) : (
        <ul className="space-y-2">
          {rows.map(r => (
            <li key={r.id} className="p-3 rounded-lg text-xs"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="flex justify-between mb-2">
                <span className="text-primary font-medium">{r.engine} · {r.query}</span>
                <span className="text-muted">{new Date(r.created_at).toLocaleString()}</span>
              </div>
              <div className="flex gap-2 mb-2 text-[10px]">
                <span className="px-1.5 py-0.5 rounded"
                  style={{ background: r.hit ? 'rgba(34,197,94,0.15)' : 'rgba(148,163,184,0.15)',
                           color: r.hit ? '#15803d' : '#64748b' }}>
                  {r.hit ? `✓ hit${r.brand_rank ? ` #${r.brand_rank}` : ''}` : '✕ miss'}
                </span>
                {r.mention_position && (
                  <span className="px-1.5 py-0.5 rounded text-muted" style={{ background: 'var(--bg-input)' }}>
                    {r.mention_position}
                  </span>
                )}
                {r.citations.length > 0 && (
                  <span className="px-1.5 py-0.5 rounded text-muted" style={{ background: 'var(--bg-input)' }}>
                    {r.citations.length} citations
                  </span>
                )}
              </div>
              {r.hit_excerpt && (
                <div className="text-secondary mb-2">{r.hit_excerpt}</div>
              )}
              <button
                type="button"
                onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                className="text-xs text-accent hover:underline"
              >
                {expanded === r.id ? '收起' : '展开正文'}
              </button>
              {expanded === r.id && (
                <div className="mt-2 p-2 rounded text-secondary whitespace-pre-wrap"
                  style={{ background: 'var(--bg-input)', maxHeight: 320, overflowY: 'auto' }}>
                  {r.answer || '(无正文)'}
                  {r.citations.length > 0 && (
                    <div className="mt-2 pt-2 border-t text-[10px]" style={{ borderColor: 'var(--border-color)' }}>
                      Citations:
                      <ul className="mt-1 space-y-0.5">
                        {r.citations.map((c, i) => (
                          <li key={i}>
                            <a href={c.url} target="_blank" rel="noopener noreferrer"
                              className="text-accent hover:underline">
                              [{i + 1}] {c.domain} · {c.title}
                            </a>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
