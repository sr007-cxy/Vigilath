// In-project "Copy" page — landing for stepper step 3, wrapping AdminContentReview locked to the current topic.
// Route: /ops/topics/:topicId/docs

import { useParams } from 'react-router-dom';
import { PageHead } from '../../components/PageHead';
import { TopicStepper } from '../../components/TopicStepper';
import { AdminContentReview } from '../Admin/ContentReview';

export function AdminTopicDocs() {
  const { topicId } = useParams();
  const tid = Number(topicId);

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.topicStepper.docs" titleFallback="文案" />
      <TopicStepper topicId={tid} active="docs" />
      <AdminContentReview lockedTopicId={tid} />
    </div>
  );
}
