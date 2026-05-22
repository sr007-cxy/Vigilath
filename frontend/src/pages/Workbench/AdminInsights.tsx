// 监测报告 — admin 视角的「效果查验与更新」反哺看板入口.
// 列出所有 approved 主题,点入跳到 /brand-growth/insights(用户端反哺数据看板).

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { adminReviewApi, type TopicReviewListItem } from '../../services/adminReviewApi';

export function AdminInsights() {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [topics, setTopics] = useState<TopicReviewListItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminReviewApi.listTopicReviews(token, 'approved')
      .then(setTopics)
      .catch(() => setTopics([]))
      .finally(() => setLoading(false));
  }, [token]);

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.adminInsights.title" titleFallback="监测报告" />
      <header>
        <h1 className="text-xl font-semibold text-primary">
          {t('workbench.adminInsights.heading')}
        </h1>
        <p className="text-xs text-secondary mt-0.5">
          {t('workbench.adminInsights.subtitle')}
        </p>
      </header>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading')}</div>
      ) : topics.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">{t('workbench.adminInsights.empty')}</div>
      ) : (
        <>
          <p className="text-xs text-muted">{t('workbench.adminInsights.pickTopic')}</p>
          <div className="rounded-lg overflow-hidden"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <table className="w-full text-xs">
              <tbody>
                {topics.map(tp => (
                  <tr key={tp.topic_id} className="border-t"
                      style={{ borderColor: 'var(--border-color)' }}>
                    <td className="px-3 py-2 text-primary">
                      {tp.profile_name || tp.topic_name}
                      <span className="ml-2 text-[10px] text-muted">#{tp.topic_id}</span>
                    </td>
                    <td className="px-3 py-2 text-secondary">{tp.user_email}</td>
                    <td className="px-3 py-2 text-right">
                      <Link to={`/brand-growth/insights?topicId=${tp.topic_id}`}
                            className="text-accent hover:underline">
                        {t('workbench.adminInsights.view')}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
