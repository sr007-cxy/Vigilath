// In-project "Profile" page — landing for stepper step 1, wrapping TopicEditor so admins edit materials/seeds/prompts
// within the project context, no longer jumping to the client list page's overlay mode.
// Route: /ops/topics/:topicId/edit

import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { TopicEditor } from '../Dashboard/AiTelemetry';
import { aiTelemetryApi, type Topic, type TopicPayload } from '../../services/aiTelemetryApi';
import { adminReviewApi } from '../../services/adminReviewApi';

export function AdminTopicEdit() {
  const { t } = useTranslation();
  const { topicId } = useParams();
  const navigate = useNavigate();
  const tid = Number(topicId);
  const token = localStorage.getItem('token') || '';
  // The project progress board points all 6 stage chips to /edit?step=N; read it here and pass it through to TopicEditor.
  const [searchParams] = useSearchParams();
  const stepParam = Number(searchParams.get('step'));
  const initialStep =
    stepParam >= 1 && stepParam <= 7 ? (stepParam as 1 | 2 | 3 | 4 | 5 | 6 | 7) : undefined;

  const [topic, setTopic] = useState<Topic | null>(null);
  const [userId, setUserId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!tid) return;
    let cancelled = false;
    adminReviewApi.getTopicReview(tid, token)
      .then((d) => {
        if (cancelled) return;
        setTopic(d);
        setUserId(d.user_id);
      })
      .catch((e) => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [tid, token]);

  const handleSave = async (payload: TopicPayload): Promise<Topic> => {
    return aiTelemetryApi.updateTopic(tid, payload, token);
  };

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.topicStepper.profile" titleFallback="画像" />

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {loading && !topic && (
        <div className="py-12 text-center text-sm text-muted">{t('common.loading')}</div>
      )}

      {topic && userId !== null && (
        <TopicEditor
          initial={topic}
          token={token}
          mode="edit"
          adminTargetUserId={userId}
          initialStep={initialStep}
          onCancel={() => navigate('/ops/cockpit')}
          onSave={handleSave}
          onSaveDone={() => {
            // Stay on the page after saving and refresh the topic data (so the stepper updates profile/prompt status)
            adminReviewApi.getTopicReview(tid, token).then(setTopic).catch(() => {});
          }}
        />
      )}
    </div>
  );
}
