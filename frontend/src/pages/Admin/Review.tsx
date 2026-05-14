// Admin 审核 — 列出全平台所有待审核的 seed prompt + query.
// Phase B 仅做脚手架:require_admin 守门 + 空数据展示;
// Phase C 接入 Topic.seed_prompts_json / queries_json 的 status 字段后,
// 这里渲染真实待审核项 + approve/reject 按钮.

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';

type ReviewTab = 'seed' | 'queries';

interface PendingResponse {
  seed_prompts: unknown[];
  queries: unknown[];
}

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export function AdminReview() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const [tab, setTab] = useState<ReviewTab>('seed');
  const [data, setData] = useState<PendingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [denied, setDenied] = useState(false);

  // 客户端预检:localStorage.user.is_admin 不为 true 直接跳首页
  const isAdmin = useMemo(() => {
    try {
      const stored = localStorage.getItem('user');
      return stored ? !!JSON.parse(stored).is_admin : false;
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) {
      navigate('/', { replace: true });
      return;
    }
    setLoading(true);
    fetch(`${API_BASE}/admin/review/pending`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => {
        if (r.status === 403) { setDenied(true); return null; }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(j => { if (j) setData(j as PendingResponse); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [isAdmin, navigate, token]);

  if (!isAdmin || denied) {
    return null;
  }

  return (
    <div className="min-h-[calc(100vh-5rem)] px-4 sm:px-6 py-6 md:py-8">
      <div className="max-w-5xl mx-auto space-y-4">
        <PageHead titleKey="admin.review.title" titleFallback="Review" />

        <header>
          <h1 className="text-xl font-semibold text-primary leading-tight">
            {t('admin.review.title')}
          </h1>
          <p className="text-xs text-secondary mt-0.5">
            {t('admin.review.subtitle')}
          </p>
        </header>

        <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
          {(['seed', 'queries'] as ReviewTab[]).map(k => (
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
              {t(`admin.review.tab.${k}`)}
            </button>
          ))}
        </div>

        {loading && (
          <div className="py-12 text-center text-sm text-muted">…</div>
        )}

        {!loading && data && tab === 'seed' && (
          <PendingList items={data.seed_prompts} emptyKey="admin.review.emptySeed" />
        )}
        {!loading && data && tab === 'queries' && (
          <PendingList items={data.queries} emptyKey="admin.review.emptyQueries" />
        )}

        <p className="text-[11px] text-muted">
          {t('admin.review.phaseBNotice')}
        </p>
      </div>
    </div>
  );
}

function PendingList({ items, emptyKey }: { items: unknown[]; emptyKey: string }) {
  const { t } = useTranslation();
  if (!items || items.length === 0) {
    return (
      <div className="py-12 text-center text-sm text-muted">
        {t(emptyKey)}
      </div>
    );
  }
  // Phase C 渲染真数据,Phase B 不会进这里
  return (
    <div className="space-y-2">
      {items.map((_, i) => (
        <div
          key={i}
          className="rounded-md p-3"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
        >
          (item {i})
        </div>
      ))}
    </div>
  );
}
