import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { mockDrafts } from '../../../../mocks/sentiment';
import { useDrafts } from '../../../../hooks/useSentiment';
import type { DraftRow, SentimentAccount } from '../../../../types/sentiment';

import { DraftCard } from '../components/DraftCard';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

interface Props {
  account: SentimentAccount;
  usingMock?: boolean;
}

/** 把 yuqin drafts 表的多行(每行一个 variant)聚合成 UI 用的 Draft 形态.
 *  分组键: (source, post_id, topic, generated_at 同一时刻).
 */
function groupDrafts(rows: DraftRow[]) {
  const map = new Map<string, {
    id: number;
    title: string;
    summary: string;
    source: string | null;
    post_id: string | null;
    topic: string | null;
    drafts: DraftRow[];
    generated_at: string;
    model: string;
    recommendation: 'conservative' | 'standard' | 'proactive';
    hitl_required: boolean;
    hitl_notes: string;
    status: 'pending_review' | 'approved' | 'archived';
  }>();
  for (const r of rows) {
    const key = `${r.source ?? ''}-${r.post_id ?? ''}-${r.topic ?? ''}-${r.generated_at}`;
    if (!map.has(key)) {
      map.set(key, {
        id: r.id,
        title: r.topic ?? `回应 ${r.source}/${r.post_id}`,
        summary: r.cautions ?? '',
        source: r.source, post_id: r.post_id, topic: r.topic,
        drafts: [],
        generated_at: r.generated_at,
        model: r.model,
        recommendation: 'standard',
        hitl_required: false,
        hitl_notes: '',
        status: 'pending_review',
      });
    }
    map.get(key)!.drafts.push(r);
  }
  return Array.from(map.values()).sort((a, b) => b.generated_at.localeCompare(a.generated_at));
}

export function DraftsTab({ account, usingMock }: Props) {
  const { t } = useTranslation();

  const draftsQuery = useDrafts(usingMock ? null : account.id, usingMock ? null : account.ticker);

  // 真实模式 → groupDrafts(rows) 把多行 variant 合成一组
  // mock 模式 → 直接用 mock 形态
  const realGrouped = useMemo(
    () => groupDrafts(draftsQuery.data?.items ?? []),
    [draftsQuery.data],
  );
  const list = usingMock ? mockDrafts : realGrouped;

  const [selectedId, setSelectedId] = useState<number | null>(list[0]?.id ?? null);
  const selected = list.find(d => d.id === selectedId) || list[0] || null;

  if (!usingMock && draftsQuery.isLoading) {
    return <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
      {t('common.loading') || 'Loading...'}
    </div>;
  }
  if (!usingMock && draftsQuery.error) {
    return <div className="rounded-xl py-6 px-4 text-sm" style={{ background: 'rgba(239,68,68,0.1)', color: '#dc2626' }}>
      ⚠ {draftsQuery.error instanceof Error ? draftsQuery.error.message : 'Failed to load drafts'}
    </div>;
  }

  const handleNewTopic = () => {
    if (usingMock) {
      alert('主题草稿生成(演示)');
      return;
    }
    const topic = prompt('请输入草稿主题(例:回应做空报告)');
    if (!topic) return;
    alert('已入队生成 — 实现请见 useGenerateDraft');
  };

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[300px_minmax(0,1fr)] gap-4">
      <aside className="rounded-xl p-3 self-start" style={cardStyle}>
        <header className="flex items-center justify-between px-1 mb-2">
          <h4 className="text-sm font-semibold text-primary">
            {t('dashboard.sentiment.drafts.list')}
          </h4>
          <button type="button" onClick={handleNewTopic}
            className="text-xs" style={{ color: 'var(--accent-primary)' }}>
            {t('dashboard.sentiment.drafts.newTopic')}
          </button>
        </header>
        {list.length === 0 ? (
          <p className="text-xs text-muted px-2 py-4 text-center">尚无草稿。</p>
        ) : (
          <ul className="space-y-1">
            {list.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => setSelectedId(d.id)}
                  className="w-full text-left px-2.5 py-2 rounded text-sm transition-colors"
                  style={{
                    background: selectedId === d.id ? 'var(--bg-tertiary)' : 'transparent',
                    border: '1px solid transparent',
                  }}>
                  <div className="font-medium text-primary text-sm">{d.title}</div>
                  <div className="flex items-center gap-2 mt-1">
                    <StatusChip status={d.status} t={t} />
                    {d.hitl_required && (
                      <span className="text-[10px] font-semibold" style={{ color: '#b45309' }}>
                        {t('dashboard.sentiment.drafts.hitlRequired')}
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-muted mt-1">
                    {new Date(d.generated_at).toLocaleString('zh-CN')}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section>
        {selected ? (
          <DraftCard draft={normalizeDraft(selected)} />
        ) : (
          <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
            {t('dashboard.sentiment.drafts.selectDraft')}
          </div>
        )}
      </section>
    </div>
  );
}

interface DraftLikeInput {
  id: number;
  symbol?: string;
  source?: string | null;
  post_id?: string | null;
  topic?: string | null;
  title: string;
  summary?: string;
  recommendation?: 'conservative' | 'standard' | 'proactive';
  hitl_required?: boolean;
  hitl_notes?: string;
  drafts?: Array<{
    variant?: string;
    body?: string;
    rationale?: string | null;
    predicted_effect?: string | null;
    cautions?: string | null;
  }>;
  generated_at: string;
  model: string;
  status?: 'pending_review' | 'approved' | 'archived';
}

function normalizeDraft(d: DraftLikeInput) {
  return {
    id: d.id,
    symbol: d.symbol ?? '',
    source: d.source ?? null,
    post_id: d.post_id ?? null,
    topic: d.topic ?? null,
    title: d.title,
    summary: d.summary ?? '',
    recommendation: d.recommendation || 'standard',
    hitl_required: d.hitl_required ?? false,
    hitl_notes: d.hitl_notes ?? '',
    drafts: (d.drafts ?? []).map((row) => ({
      variant: (row.variant || 'standard') as 'conservative' | 'standard' | 'proactive',
      body: row.body || '',
      rationale: row.rationale || '',
      predicted_effect: row.predicted_effect || '',
      cautions: row.cautions || '',
    })),
    generated_at: d.generated_at,
    model: d.model,
    status: d.status || 'pending_review',
  };
}

function StatusChip({ status, t }: {
  status: 'pending_review' | 'approved' | 'archived';
  t: (k: string) => string;
}) {
  const cfg = {
    pending_review: { bg: 'rgba(251,191,36,0.18)', fg: '#b45309', key: 'pendingReview' },
    approved:       { bg: 'rgba(22,163,74,0.18)',  fg: '#16a34a', key: 'approved' },
    archived:       { bg: 'rgba(148,163,184,0.18)', fg: '#64748b', key: 'archived' },
  }[status];
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold"
      style={{ background: cfg.bg, color: cfg.fg }}>
      {t(`dashboard.sentiment.drafts.${cfg.key}`)}
    </span>
  );
}
