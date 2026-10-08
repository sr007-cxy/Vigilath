// Admin content review -- single article detail page (entered via "View" from the review desk list; supports direct URL / back / sharing).
// Route: /ops/content-review/:docId
// Full functionality: edit / regenerate / approve / reject / publish center / platform review backfill are all on this page -- reusing
// the same-source DocDetailView as the list dialog (asPage layout), so the standalone list no longer needs a dialog.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { useAuth } from '../../contexts/AuthContext';
import { adminContentReviewApi, type GeneratedDoc } from '../../services/adminContentReviewApi';
import { DocDetailView } from './ContentReview';

export function AdminContentReviewDetail() {
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

  // Back link for the "Article management / Content & placement" breadcrumb: carries the original filters, restoring context on return to the list.
  const backToList = useMemo(() => {
    if (inTopicStep6) return safeTopicReturnTo;
    const qs = new URLSearchParams();
    const topic = searchParams.get('topic');
    const status = searchParams.get('status');
    if (topic) qs.set('topic', topic);
    if (status) qs.set('status', status);
    const s = qs.toString();
    return `/ops/content-review${s ? `?${s}` : ''}`;
  }, [inTopicStep6, safeTopicReturnTo, searchParams]);

  // "Choose publish platform" goes to the standalone publish page (four-level breadcrumbs), keeping the same filters / topic-step6 query.
  const toPublish = useMemo(() => {
    const s = searchParams.toString();
    return `/ops/content-review/${docIdParam}/publish${s ? `?${s}` : ''}`;
  }, [searchParams, docIdParam]);

  // Same guard as the list page: non-admin direct URL access is always kicked back to the dashboard.
  useEffect(() => {
    if (!isStaff) navigate('/app', { replace: true });
  }, [isStaff, navigate]);

  // Fetch data; doesn't flip loading (initially covered by useState's initial true), used for silent refresh after mutations,
  // keeping the breadcrumb title up to date along with the doc.
  const loadDoc = useCallback(() => {
    if (!isStaff) return;
    if (!Number.isFinite(docId)) { setLoading(false); return; }
    setErr(null);
    adminContentReviewApi.getDoc(docId, token)
      .then(setDoc)
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [isStaff, docId, token]);

  useEffect(() => { loadDoc(); }, [loadDoc]);

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
        <span className="text-secondary truncate max-w-[420px]">
          {currentDoc ? (currentDoc.title || `#${currentDoc.id}`) : `#${docIdParam}`}
        </span>
      </nav>

      {loading ? (
        <div className="text-xs text-muted py-10 text-center">加载中…</div>
      ) : err ? (
        <div className="text-xs py-10 text-center" style={{ color: 'var(--danger-fg)' }}>{err}</div>
      ) : !currentDoc ? (
        <div className="text-xs text-muted py-10 text-center">未找到该文档</div>
      ) : (
        <DocDetailView
          key={currentDoc.id}
          asPage
          doc={currentDoc}
          token={token}
          onClose={() => navigate(backToList)}
          onAnyChange={loadDoc}
          onOpenPublish={() => navigate(toPublish)}
        />
      )}
    </div>
  );
}
