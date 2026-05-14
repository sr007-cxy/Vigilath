// Admin 审核 — Phase C 接入真实数据 + approve/reject.
// 数据 = 全平台所有 Topic 里 status=pending 的种子词 + query.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import {
  adminReviewApi,
  type PendingReview,
  type PendingSeedItem,
  type PendingQueryItem,
} from '../../services/adminReviewApi';

type ReviewTab = 'seed' | 'queries';

export function AdminReview() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const [tab, setTab] = useState<ReviewTab>('seed');
  const [data, setData] = useState<PendingReview | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 客户端预检:localStorage.user.is_admin 不为 true 直接跳首页
  const isAdmin = useMemo(() => {
    try {
      const stored = localStorage.getItem('user');
      return stored ? !!JSON.parse(stored).is_admin : false;
    } catch {
      return false;
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await adminReviewApi.listPending(token);
      setData(r);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!isAdmin) {
      navigate('/', { replace: true });
      return;
    }
    refresh();
  }, [isAdmin, navigate, refresh]);

  if (!isAdmin) return null;

  return (
    <div className="min-h-[calc(100vh-5rem)] px-4 sm:px-6 py-6 md:py-8">
      <div className="max-w-5xl mx-auto space-y-4">
        <PageHead titleKey="admin.review.title" titleFallback="Review" />

        <header className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-xl font-semibold text-primary leading-tight">
              {t('admin.review.title')}
            </h1>
            <p className="text-xs text-secondary mt-0.5">
              {t('admin.review.subtitle')}
            </p>
          </div>
          <button
            type="button"
            onClick={refresh}
            className="text-xs px-3 py-1.5 rounded-md"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}
          >
            ⟳ {t('admin.review.refresh')}
          </button>
        </header>

        <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
          {(['seed', 'queries'] as ReviewTab[]).map(k => {
            const count =
              k === 'seed' ? (data?.seed_prompts.length ?? 0) :
              (data?.queries.length ?? 0);
            return (
              <button
                key={k}
                type="button"
                onClick={() => setTab(k)}
                className="px-3 py-2 text-sm -mb-px"
                style={{
                  borderBottom: tab === k ? '2px solid var(--accent-primary)' : '2px solid transparent',
                  color: tab === k ? 'var(--accent-primary)' : 'var(--text-secondary)',
                }}
              >
                {t(`admin.review.tab.${k}`)} ({count})
              </button>
            );
          })}
        </div>

        {err && (
          <div
            className="rounded-md p-3 text-sm"
            style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}
          >
            {err}
          </div>
        )}

        {loading && <div className="py-12 text-center text-sm text-muted">…</div>}

        {!loading && data && tab === 'seed' && (
          <SeedList items={data.seed_prompts} token={token} onChange={refresh} />
        )}
        {!loading && data && tab === 'queries' && (
          <QueryList items={data.queries} token={token} onChange={refresh} />
        )}
      </div>
    </div>
  );
}

function SeedList({
  items, token, onChange,
}: { items: PendingSeedItem[]; token: string; onChange: () => void }) {
  const { t } = useTranslation();
  if (items.length === 0) {
    return <div className="py-12 text-center text-sm text-muted">{t('admin.review.emptySeed')}</div>;
  }
  // 按 topic 分组(同时保留 target / user_email 上下文)
  const grouped = new Map<number, { name: string; target: string; userEmail: string; items: PendingSeedItem[] }>();
  for (const it of items) {
    if (!grouped.has(it.topic_id)) {
      grouped.set(it.topic_id, {
        name: it.topic_name, target: it.target, userEmail: it.user_email, items: [],
      });
    }
    grouped.get(it.topic_id)!.items.push(it);
  }
  return (
    <div className="space-y-4">
      {Array.from(grouped.entries()).map(([topicId, g]) => (
        <section key={topicId} className="space-y-2">
          <TopicGroupHeader name={g.name} topicId={topicId} target={g.target} userEmail={g.userEmail} />
          {g.items.map(it => (
            <SeedRow key={`${it.topic_id}-${it.idx}`} item={it} token={token} onChange={onChange} />
          ))}
        </section>
      ))}
    </div>
  );
}

function TopicGroupHeader({
  name, topicId, target, userEmail,
}: { name: string; topicId: number; target: string; userEmail: string }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 flex-wrap text-xs">
      <span className="font-semibold text-primary">📂 {name}</span>
      <span className="text-muted">#{topicId}</span>
      {target && (
        <span className="text-secondary">
          · {t('admin.review.targetLabel')} <span className="text-primary">{target}</span>
        </span>
      )}
      {userEmail && (
        <span className="text-secondary ml-auto">
          {t('admin.review.byUser')} <span className="text-primary">{userEmail}</span>
        </span>
      )}
    </div>
  );
}

function SeedRow({
  item, token, onChange,
}: { item: PendingSeedItem; token: string; onChange: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  const doAction = async (action: 'approve' | 'reject') => {
    if (busy) return;
    setBusy(true);
    try {
      if (action === 'approve') {
        await adminReviewApi.approveSeed(item.topic_id, item.idx, token);
      } else {
        await adminReviewApi.rejectSeed(item.topic_id, item.idx, token);
      }
      onChange();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="rounded-md p-3 flex items-center gap-3 flex-wrap"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <p className="text-sm text-primary flex-1 min-w-0">{item.text}</p>
      {item.submitted_at && (
        <span className="text-[11px] text-muted shrink-0">
          {new Date(item.submitted_at).toLocaleString()}
        </span>
      )}
      <div className="flex gap-2 shrink-0">
        <button
          type="button"
          disabled={busy}
          onClick={() => doAction('reject')}
          className="text-xs px-3 py-1 rounded-md"
          style={{
            background: 'var(--bg-input)',
            color: '#ef4444',
            border: '1px solid var(--border-color)',
            opacity: busy ? 0.5 : 1,
          }}
        >
          {t('admin.review.reject')}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => doAction('approve')}
          className="text-xs px-3 py-1 rounded-md text-white"
          style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}
        >
          {t('admin.review.approve')}
        </button>
      </div>
    </div>
  );
}

function QueryList({
  items, token, onChange,
}: { items: PendingQueryItem[]; token: string; onChange: () => void }) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<Set<string>>(new Set());

  if (items.length === 0) {
    return <div className="py-12 text-center text-sm text-muted">{t('admin.review.emptyQueries')}</div>;
  }

  const grouped = new Map<number, { name: string; target: string; userEmail: string; items: PendingQueryItem[] }>();
  for (const it of items) {
    if (!grouped.has(it.topic_id)) {
      grouped.set(it.topic_id, {
        name: it.topic_name, target: it.target, userEmail: it.user_email, items: [],
      });
    }
    grouped.get(it.topic_id)!.items.push(it);
  }

  const toggle = (key: string) => {
    setPicked(prev => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key); else n.add(key);
      return n;
    });
  };

  const batchAction = async (topicId: number, action: 'approve' | 'reject') => {
    const items = grouped.get(topicId)?.items ?? [];
    const indices = items
      .filter(it => picked.has(`${it.topic_id}-${it.idx}`))
      .map(it => it.idx);
    if (indices.length === 0) return;
    try {
      if (action === 'approve') {
        await adminReviewApi.approveQueries(topicId, indices, token);
      } else {
        await adminReviewApi.rejectQueries(topicId, indices, token);
      }
      // 清掉已处理项的选中
      setPicked(prev => {
        const n = new Set(prev);
        for (const idx of indices) n.delete(`${topicId}-${idx}`);
        return n;
      });
      onChange();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-4">
      {Array.from(grouped.entries()).map(([topicId, g]) => {
        const pickedInTopic = g.items.filter(it => picked.has(`${it.topic_id}-${it.idx}`)).length;
        return (
          <section key={topicId} className="space-y-2">
            <TopicGroupHeader name={g.name} topicId={topicId} target={g.target} userEmail={g.userEmail} />
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-xs text-muted">
                · {g.items.length} {t('admin.review.queryCountUnit')}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-muted">
                  {t('admin.review.pickedHint', { n: pickedInTopic })}
                </span>
                <button
                  type="button"
                  disabled={pickedInTopic === 0}
                  onClick={() => batchAction(topicId, 'reject')}
                  className="text-xs px-3 py-1 rounded-md"
                  style={{
                    background: 'var(--bg-input)',
                    color: '#ef4444',
                    border: '1px solid var(--border-color)',
                    opacity: pickedInTopic === 0 ? 0.4 : 1,
                  }}
                >
                  {t('admin.review.batchReject')}
                </button>
                <button
                  type="button"
                  disabled={pickedInTopic === 0}
                  onClick={() => batchAction(topicId, 'approve')}
                  className="text-xs px-3 py-1 rounded-md text-white"
                  style={{
                    background: 'var(--accent-primary)',
                    opacity: pickedInTopic === 0 ? 0.4 : 1,
                  }}
                >
                  {t('admin.review.batchApprove')}
                </button>
              </div>
            </div>
            {g.items.map(it => {
              const key = `${it.topic_id}-${it.idx}`;
              const checked = picked.has(key);
              return (
                <label
                  key={key}
                  className="rounded-md p-2 flex items-start gap-2 cursor-pointer"
                  style={{
                    background: checked ? 'var(--bg-tertiary)' : 'var(--bg-card)',
                    border: `1px solid ${checked ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(key)}
                    className="mt-0.5"
                    style={{ accentColor: 'var(--accent-primary)' }}
                  />
                  <span className="text-sm text-primary flex-1 leading-snug">{it.text}</span>
                  {typeof it.cluster_id === 'number' && it.cluster_id >= 0 && (
                    <span className="text-[10px] text-muted shrink-0">cluster #{it.cluster_id}</span>
                  )}
                </label>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}
