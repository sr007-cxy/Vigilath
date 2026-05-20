import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { BrandGrowthShell, type ShellState } from './shell';
import { contentApi, type ContentDoc } from '../../services/contentApi';
import { useBgLang, engineLabel } from './lang';
import { InfoHint } from './charts';

type RoiFilter = 'all' | 'hit' | 'miss';

export function Published() {
  const L = useBgLang();
  return (
    <BrandGrowthShell title={L.publishedTitle} breadcrumb={[{ label: L.pageTitle, to: '/brand-growth' }]}>
      {(state) => <Body state={state} />}
    </BrandGrowthShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const { token, topic } = state;
  const L = useBgLang();
  const [params, setParams] = useSearchParams();
  const roi = (params.get('roi') as RoiFilter) || 'all';
  const platform = params.get('platform') || '';
  const [docs, setDocs] = useState<ContentDoc[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!topic) return;
    setLoading(true);
    contentApi.listDocs(topic.id, {
      status: 'published',
      query_hit: roi === 'all' ? undefined : roi,
    }, token).then(setDocs).catch(() => setDocs([])).finally(() => setLoading(false));
  }, [token, topic?.id, roi]);

  if (!topic) return null;

  const platforms = Array.from(new Set(
    docs.flatMap(d => d.publish_targets.map(t => t.platform)).filter(Boolean)
  ));
  const visible = platform ? docs.filter(d => d.publish_targets.some(t => t.platform === platform)) : docs;

  const setRoi = (r: RoiFilter) => {
    const next = new URLSearchParams(params);
    if (r === 'all') next.delete('roi'); else next.set('roi', r);
    setParams(next, { replace: true });
  };
  const setPlatform = (p: string) => {
    const next = new URLSearchParams(params);
    if (!p) next.delete('platform'); else next.set('platform', p);
    setParams(next, { replace: true });
  };

  const roiLabels: Record<RoiFilter, string> = {
    all: L.publishedFilterAll,
    hit: L.publishedFilterHit,
    miss: L.publishedFilterMiss,
  };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <div className="flex gap-1 p-0.5 rounded" style={{ background: 'var(--bg-input)' }}>
            {(['all', 'hit', 'miss'] as RoiFilter[]).map(r => (
              <button
                key={r} type="button" onClick={() => setRoi(r)}
                className="px-3 py-1 text-xs rounded"
                style={{
                  background: roi === r ? 'var(--accent-primary)' : 'transparent',
                  color: roi === r ? 'white' : 'var(--text-secondary)',
                }}
              >
                {roiLabels[r]}
              </button>
            ))}
          </div>
          <InfoHint text={L.hintPublished} />
        </div>
        {platforms.length > 0 && (
          <select
            value={platform} onChange={e => setPlatform(e.target.value)}
            className="text-xs px-2 py-1 rounded"
            style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
          >
            <option value="">{L.publishedAllPlatform}</option>
            {platforms.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        )}
        <span className="text-xs text-muted ml-auto">{L.publishedTotal(visible.length)}</span>
      </div>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{L.loading}</div>
      ) : visible.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">{L.publishedEmpty}</div>
      ) : (
        <ul className="grid gap-3">
          {visible.map(d => <DocCard key={d.id} doc={d} topicId={topic.id} />)}
        </ul>
      )}
    </div>
  );
}

function DocCard({ doc, topicId }: { doc: ContentDoc; topicId: number }) {
  const navigate = useNavigate();
  const L = useBgLang();
  const earliestMark = doc.publish_targets
    .map(t => t.marked_at).filter(Boolean).sort()[0];
  const ts = earliestMark || doc.review_decision_at;
  const citedEngines = Object.entries(doc.cited_by || {})
    .filter(([, ids]) => ids && ids.length > 0)
    .map(([eng, ids]) => ({ eng, count: ids.length }));
  return (
    <li className="p-4 rounded-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="flex justify-between mb-2">
        <span className="px-2 py-0.5 rounded text-[10px]"
          style={{ background: doc.source === 'ai' ? 'rgba(59,130,246,0.15)' : 'rgba(168,85,247,0.15)',
                   color: doc.source === 'ai' ? '#3b82f6' : '#a855f7' }}>
          {doc.source === 'ai' ? L.publishedSourceAi : L.publishedSourceUser}
        </span>
        <span className="text-xs text-muted">
          {L.publishedAt} · {ts ? new Date(ts).toLocaleDateString() : '—'}
        </span>
      </div>
      <h3 className="text-base font-medium text-primary mb-2">{doc.title}</h3>
      {doc.summary && <p className="text-sm text-secondary mb-3 line-clamp-2">{doc.summary}</p>}

      {citedEngines.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          <span className="text-[10px] text-muted self-center">{L.publishedAiCited}:</span>
          {citedEngines.map(({ eng, count }) => (
            <span key={eng} className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] rounded"
              style={{ background: 'rgba(34,197,94,0.15)', color: '#22c55e' }}>
              {engineLabel(eng)} · {count}
            </span>
          ))}
        </div>
      )}

      {doc.publish_targets.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {doc.publish_targets.map((t, i) => (
            <a
              key={i}
              href={t.url || '#'}
              target={t.url ? '_blank' : undefined}
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded"
              style={{ background: 'var(--bg-input)', color: t.url ? 'var(--accent-primary)' : 'var(--text-muted)' }}
            >
              {t.platform} · {t.media}{t.url ? ' ↗' : ''}
            </a>
          ))}
        </div>
      )}

      {doc.source_query_text && (
        <div className="text-xs pt-2 border-t" style={{ borderColor: 'var(--border-color)' }}>
          <span className="text-muted">{L.publishedRelQuery}: </span>
          <button
            type="button"
            onClick={() => navigate(`/brand-growth/queries?topic=${topicId}&q=${encodeURIComponent(doc.source_query_text)}`)}
            className="text-accent hover:underline"
          >
            {doc.source_query_text}{L.publishedRelQueryArrow}
          </button>
        </div>
      )}
    </li>
  );
}
