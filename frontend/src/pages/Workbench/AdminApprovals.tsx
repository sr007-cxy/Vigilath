// 待审批 — admin 需要介入的两类审批集中入口:
//   ① 主题审批  → /workbench/review
//   ② 内容审批  → /workbench/content-review
// 主题待审计数从 listTopicReviews(status='pending') 拿;内容待审暂留 placeholder.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { adminReviewApi } from '../../services/adminReviewApi';

function Card({
  title, hint, count, to, t,
}: { title: string; hint: string; count: number | null; to: string; t: (k: string, p?: Record<string, unknown>) => string }) {
  return (
    <Link to={to} className="block rounded-lg p-5 transition-colors hover:bg-tertiary"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-primary">{title}</h3>
          <p className="text-xs text-secondary mt-1">{hint}</p>
        </div>
        {count != null && (
          <span className="text-[11px] px-2 py-0.5 rounded-md"
                style={count > 0
                  ? { background: 'rgba(239,68,68,0.12)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.35)' }
                  : { background: 'rgba(16,185,129,0.10)', color: '#10b981', border: '1px solid rgba(16,185,129,0.30)' }}>
            {count > 0
              ? t('workbench.adminApprovals.pendingBadge', { n: count })
              : t('workbench.adminApprovals.allClear')}
          </span>
        )}
      </div>
      <div className="mt-3 text-xs" style={{ color: 'var(--accent-primary)' }}>
        {t('workbench.adminApprovals.goReview')}
      </div>
    </Link>
  );
}

export function AdminApprovals() {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [topicPending, setTopicPending] = useState<number | null>(null);

  useEffect(() => {
    adminReviewApi.listTopicReviews(token, 'pending')
      .then(rows => setTopicPending(rows.length))
      .catch(() => setTopicPending(null));
  }, [token]);

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.adminApprovals.title" titleFallback="待审批" />
      <header>
        <h1 className="text-xl font-semibold text-primary">
          {t('workbench.adminApprovals.heading')}
        </h1>
        <p className="text-xs text-secondary mt-0.5">
          {t('workbench.adminApprovals.subtitle')}
        </p>
      </header>

      <div className="grid gap-3 md:grid-cols-2">
        <Card t={t}
              title={t('workbench.adminApprovals.topicReview')}
              hint={t('workbench.adminApprovals.topicReviewHint')}
              count={topicPending}
              to="/workbench/review" />
        <Card t={t}
              title={t('workbench.adminApprovals.contentReview')}
              hint={t('workbench.adminApprovals.contentReviewHint')}
              count={null}
              to="/workbench/content-review" />
      </div>
    </div>
  );
}
