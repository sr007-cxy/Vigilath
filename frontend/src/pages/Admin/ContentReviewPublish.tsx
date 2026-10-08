// Admin content review -- standalone "publish center" page for a single article (entered via "Choose publish platform" in the detail page header).
// Route: /ops/content-review/:docId/publish  ·  four-level breadcrumbs: workbench/article management/article title/publish center
// Reuses PublishCenterDrawer (asPage layout) -- same source as the detail drawer and the Dashboard content page, consistent behavior.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { useAuth } from '../../contexts/AuthContext';
import { PublishCenterDrawer } from '../../components/publish/PublishCenterDrawer';
import { adminContentReviewApi, type GeneratedDoc } from '../../services/adminContentReviewApi';

export function AdminContentReviewPublish() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { docId: docIdParam } = useParams();
  const [searchParams] = useSearchParams();
  const docId = Number(docIdParam);
  const token = localStorage.getItem('token') || '';

  // Copy re-review is a workbench module (backend require_workbench): both admin and operator can enter
  const { isStaff } = useAuth();

  const [doc, setDoc] = useState<GeneratedDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [topicName, setTopicName] = useState<string | null>(null);

  const topicContextId = useMemo(() => {
    const raw = searchParams.get('topic');
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) ? n : null;
  }, [searchParams]);
  const inTopicStep6 = searchParams.get('context') === 'topic-step6' && topicContextId !== null;

  const safeTopicReturnTo = useMemo(() => {
    if (!inTopicStep6 || topicContextId === null) return '';
    const raw = searchParams.get('returnTo') || '';
    if (raw.startsWith('/ops/') && !raw.startsWith('//')) return raw;
    return `/ops/topics/${topicContextId}/edit?step=6`;
  }, [inTopicStep6, searchParams, topicContextId]);

  // Breadcrumb back links carry the original filters / topic-step6 context, so stepping back level by level restores the list context.
  const qs = useMemo(() => {
    const s = searchParams.toString();
    return s ? `?${s}` : '';
  }, [searchParams]);
  const backToList = inTopicStep6 ? safeTopicReturnTo : `/ops/content-review${qs}`;
  const backToDoc = `/ops/content-review/${docIdParam}${qs}`;

  // Same guard as the list/detail pages: non-admin direct URL access is always kicked back to the dashboard.
  useEffect(() => {
    if (!isStaff) navigate('/app', { replace: true });
  }, [isStaff, navigate]);

  // Fetch data; doesn't flip loading (initial true covers it), used for silent refresh after publishing, keeping the breadcrumb title up to date with the doc.
  const reload = useCallback(() => {
    if (!isStaff) return;
    if (!Number.isFinite(docId)) { setLoading(false); return; }
    setErr(null);
    adminContentReviewApi.getDoc(docId, token)
      .then(setDoc)
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [isStaff, docId, token]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    if (!isStaff || !inTopicStep6 || topicContextId === null) return;
    let cancelled = false;
    adminContentReviewApi.listTopics(token)
      .then(list => {
        if (cancelled) return;
        const topic = list.find(tp => tp.topic_id === topicContextId);
        setTopicName(topic?.topic_name || null);
      })
      .catch(() => { if (!cancelled) setTopicName(null); });
    return () => { cancelled = true; };
  }, [isStaff, inTopicStep6, topicContextId, token]);

  // One-click official blog publish: same metric definition as the detail view (publishDoc goes live directly); refreshed via onChanged after a truthy return.
  const handlePublishMediumsly = useCallback(async () => {
    if (!doc) return false;
    try {
      await adminContentReviewApi.publishDoc(doc.id, [], token, true);
      return true;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return false;
    }
  }, [doc, token]);

  if (!isStaff) return null;
  const currentDoc = doc?.id === docId ? doc : null;

  return (
    <div className="space-y-4">
      <PageHead titleKey="admin.contentReview.title" titleFallback="内容审核" />

      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-xs text-muted flex-wrap">
        <Link to="/ops" className="hover:underline">{t('nav.opsTitle')}</Link>
        <span>/</span>
        {inTopicStep6 && topicContextId !== null ? (
          <>
            <Link to={`/ops/topics/${topicContextId}/edit?step=1`} className="hover:underline truncate max-w-[220px]">
              {topicName || `项目 #${topicContextId}`}
            </Link>
            <span>/</span>
            <Link to={backToList} className="hover:underline">内容与投放</Link>
          </>
        ) : (
          <Link to={backToList} className="hover:underline">{t('nav.adminContentReview')}</Link>
        )}
        <span>/</span>
        <Link to={backToDoc} className="hover:underline truncate max-w-[280px]">
          {currentDoc ? (currentDoc.title || `#${currentDoc.id}`) : `#${docIdParam}`}
        </Link>
        <span>/</span>
        <span className="text-secondary">发布中心</span>
      </nav>

      {loading ? (
        <div className="text-xs text-muted py-10 text-center">加载中…</div>
      ) : err ? (
        <div className="text-xs py-10 text-center" style={{ color: 'var(--danger-fg)' }}>{err}</div>
      ) : !currentDoc ? (
        <div className="text-xs text-muted py-10 text-center">未找到该文档</div>
      ) : (
        <PublishCenterDrawer
          key={currentDoc.id}
          asPage
          doc={currentDoc}
          token={token}
          onChanged={reload}
          onPublishMediumsly={handlePublishMediumsly}
          onClose={() => navigate(backToDoc)}
        />
      )}
    </div>
  );
}
