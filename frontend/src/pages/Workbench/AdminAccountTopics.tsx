import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi, type Topic, type TopicPayload } from '../../services/aiTelemetryApi';
import { TopicEditor } from '../Dashboard/AiTelemetry';

export function AdminAccountTopics() {
  const { userId } = useParams<{ userId: string }>();
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  // undefined = 列表;null = 新建;Topic = 编辑(admin 也用同一个 editor)
  const [editing, setEditing] = useState<Topic | null | undefined>(undefined);

  const reload = () => {
    if (!userId) return;
    setLoading(true);
    aiTelemetryApi.adminListUserTopics(Number(userId), token)
      .then(setTopics).catch(() => setTopics([])).finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    setLoading(true);
    aiTelemetryApi.adminListUserTopics(Number(userId), token)
      .then(r => { if (!cancelled) setTopics(r); })
      .catch(() => { if (!cancelled) setTopics([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [userId, token]);

  if (!userId) return null;

  // 编辑场景 — admin 替别人 PUT,仍走 updateTopic(后端 allow_admin_user 已开)。
  const handleSave = async (payload: TopicPayload): Promise<Topic> => {
    if (editing && editing.id) {
      return aiTelemetryApi.updateTopic(editing.id, payload, token);
    }
    // 新建分支:实际由 TopicEditor 内部走 adminCreateTopicForUser,
    // 这里只是兜底 — onSave 在 admin-new 模式不会被调用。
    return aiTelemetryApi.adminCreateTopicForUser(Number(userId), payload, token);
  };

  if (editing !== undefined) {
    return (
      <div className="max-w-[1100px] mx-auto p-6 space-y-4">
        <div className="text-xs text-muted">
          <Link to="/workbench/accounts" className="hover:underline">
            {t('workbench.adminAccounts.title')}
          </Link>
          {' / '}
          <Link to={`/workbench/accounts/${userId}/topics`} className="hover:underline"
            onClick={(e) => { e.preventDefault(); setEditing(undefined); }}>
            {t('workbench.adminAccountTopics.title', { userId })}
          </Link>
          {' / '}
          {editing ? t('workbench.adminAccountTopics.editTopic') : t('workbench.adminAccountTopics.newTopic')}
        </div>
        <h1 className="text-xl font-semibold text-primary">
          {editing ? t('workbench.adminAccountTopics.editTopic') : t('workbench.adminAccountTopics.newTopic')}
        </h1>
        <TopicEditor
          initial={editing}
          token={token}
          mode="edit"
          adminTargetUserId={Number(userId)}
          onCancel={() => setEditing(undefined)}
          onSave={handleSave}
          onSaveDone={() => { setEditing(undefined); reload(); }}
        />
      </div>
    );
  }

  return (
    <div className="max-w-[1100px] mx-auto p-6">
      <div className="text-xs text-muted mb-2">
        <Link to="/workbench/accounts" className="hover:underline">
          {t('workbench.adminAccounts.title')}
        </Link>
        {' / '}
        {t('workbench.adminAccountTopics.title', { userId })}
      </div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-primary">
          {t('workbench.adminAccountTopics.heading')}
        </h1>
        <button
          type="button"
          onClick={() => setEditing(null)}
          className="px-3 py-1.5 text-sm rounded-md text-white"
          style={{ background: 'var(--accent-primary)' }}
        >
          + {t('workbench.adminAccountTopics.newTopic')}
        </button>
      </div>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">
          {t('common.loading')}
        </div>
      ) : topics.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">
          {t('workbench.adminAccountTopics.emptyHint')}
        </div>
      ) : (
        <ul className="grid gap-3">
          {topics.map(tp => (
            <li key={tp.id} className="p-4 rounded-lg"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="flex justify-between mb-2">
                <span className="text-sm font-medium text-primary">{tp.name}</span>
                <span className="text-xs text-muted">
                  {t('workbench.adminAccountTopics.metaSummary', {
                    target: tp.target,
                    queries: tp.queries.length,
                    engines: tp.engines.length,
                  })}
                </span>
              </div>
              <div className="text-xs text-secondary mb-2">
                {t('workbench.adminAccountTopics.industryLabel')}: {tp.industry || '—'}
                {' · '}
                {t('workbench.adminAccountTopics.statusLabel')}: {tp.last_run_status || t('workbench.adminAccountTopics.neverRun')}
              </div>
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => setEditing(tp)}
                  className="text-xs px-3 py-1 rounded"
                  style={{ background: 'var(--accent-primary)', color: 'white' }}
                >
                  {t('workbench.adminAccountTopics.edit')}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
