// Admin 内容审核 — 选账号 + 主题 → 看文档列表 → 勾选送审 → 通过 + 选发布平台/媒体 (或拒绝).
// 路由:/workbench/content-review

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import {
  adminContentReviewApi,
  type DocSource, type DocStatus, type GeneratedDoc, type TopicWithDocs,
} from '../../services/adminContentReviewApi';

type StatusFilter = DocStatus | 'to_review' | 'all';
type SourceFilter = DocSource | 'all';

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'to_review',      label: 'to_review' },
  { key: 'draft',          label: 'draft' },
  { key: 'pending_review', label: 'pending_review' },
  { key: 'approved',       label: 'approved' },
  { key: 'rejected',       label: 'rejected' },
  { key: 'published',      label: 'published' },
  { key: 'all',            label: 'all' },
];

interface AdminContentReviewProps {
  // 锁定到指定 topic;给则隐藏 topic 选择器(用于项目详情页 stepper 内嵌)
  lockedTopicId?: number;
}

export function AdminContentReview({ lockedTopicId }: AdminContentReviewProps = {}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';

  // 深链:?topic=X 跳到指定项目,?status=Y 跳到指定状态(stepper / cockpit 待办卡用)
  // 若 lockedTopicId 给了,优先用它,完全忽略 URL.
  const initialUrlParams = useMemo(() => {
    const sp = new URLSearchParams(window.location.search);
    const topicParam = sp.get('topic');
    const statusParam = sp.get('status');
    return {
      topic: lockedTopicId ?? (topicParam ? Number(topicParam) : null),
      status: (statusParam as StatusFilter) || 'to_review',
    };
  }, [lockedTopicId]);

  const [topics, setTopics] = useState<TopicWithDocs[]>([]);
  const [topicId, setTopicId] = useState<number | null>(initialUrlParams.topic);
  const [docs, setDocs] = useState<GeneratedDoc[]>([]);
  const [status, setStatus] = useState<StatusFilter>(initialUrlParams.status);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [openDocId, setOpenDocId] = useState<number | null>(null);

  const isAdmin = useMemo(() => {
    try {
      const stored = localStorage.getItem('user');
      return stored ? !!JSON.parse(stored).is_admin : false;
    } catch { return false; }
  }, []);

  const refreshTopics = useCallback(async () => {
    try {
      const rs = await adminContentReviewApi.listTopics(token);
      setTopics(rs);
      // lockedTopicId 模式不自动覆盖,锁定不变
      if (lockedTopicId === undefined && rs.length > 0 && topicId === null) {
        setTopicId(rs[0].topic_id);
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [token, topicId, lockedTopicId]);

  const refreshDocs = useCallback(async () => {
    if (topicId === null) return;
    setLoading(true); setErr(null);
    try {
      const ds = await adminContentReviewApi.listDocs(
        topicId, status === 'all' ? undefined : status, token,
        sourceFilter === 'all' ? undefined : sourceFilter,
      );
      setDocs(ds);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [topicId, status, sourceFilter, token]);

  useEffect(() => {
    if (!isAdmin) { navigate('/dashboard', { replace: true }); return; }
    refreshTopics();
  }, [isAdmin, navigate, refreshTopics]);

  useEffect(() => { refreshDocs(); }, [refreshDocs]);

  if (!isAdmin) return null;

  const selectedDoc = openDocId ? docs.find(d => d.id === openDocId) || null : null;

  const togglePick = (id: number) => {
    setPicked(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const sendToReview = async () => {
    if (picked.size === 0) return;
    try {
      await adminContentReviewApi.selectForReview(Array.from(picked), token);
      setPicked(new Set());
      refreshDocs(); refreshTopics();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-4">
      {/* 锁定模式由父页负责标题(stepper 已经接管上下文),不重复渲染 */}
      {lockedTopicId === undefined && (
        <>
          <PageHead titleKey="admin.contentReview.title" titleFallback="内容审核" />
          <header className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <h1 className="text-xl font-semibold text-primary">{t('admin.contentReview.title')}</h1>
              <p className="text-xs text-secondary mt-0.5">{t('admin.contentReview.subtitle')}</p>
            </div>
            <button type="button" onClick={() => { refreshTopics(); refreshDocs(); }}
                    className="text-xs px-3 py-1.5 rounded-md"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}>
              ⟳ {t('admin.contentReview.refresh')}
            </button>
          </header>
        </>
      )}

      <div className="rounded-md p-3 flex flex-wrap gap-3 items-end"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        {/* 锁定模式不显示 topic 选择器(stepper 上下文已锁定项目) */}
        {lockedTopicId === undefined && (
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">{t('admin.contentReview.topicLabel')}</label>
            <select value={topicId ?? ''}
                    onChange={e => { setTopicId(Number(e.target.value)); setPicked(new Set()); }}
                    className="text-sm px-3 py-1.5 rounded-md min-w-[260px]"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                             border: '1px solid var(--border-color)' }}>
              {topics.map(tp => (
                <option key={tp.topic_id} value={tp.topic_id}>
                  {tp.topic_name} · {tp.user_email} · ({tp.draft_count}/{tp.doc_count})
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">{t('admin.contentReview.statusLabel')}</label>
          <div className="flex gap-1">
            {STATUS_FILTERS.map(f => (
              <button key={f.key} type="button" onClick={() => setStatus(f.key)}
                      className="text-xs px-2.5 py-1 rounded-md"
                      style={{
                        background: status === f.key ? 'var(--accent-primary)' : 'var(--bg-input)',
                        color: status === f.key ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border-color)',
                      }}>
                {t(`admin.contentReview.statusFilter.${f.key}`)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">{t('admin.contentReview.sourceLabel')}</label>
          <div className="flex gap-1">
            {(['all', 'ai', 'user'] as SourceFilter[]).map(k => (
              <button key={k} type="button" onClick={() => setSourceFilter(k)}
                      className="text-xs px-2.5 py-1 rounded-md"
                      style={{
                        background: sourceFilter === k ? 'var(--accent-primary)' : 'var(--bg-input)',
                        color: sourceFilter === k ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border-color)',
                      }}>
                {t(`admin.contentReview.sourceFilter.${k}`)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1" />

        {picked.size > 0 && (
          <button type="button" onClick={sendToReview}
                  className="text-sm px-4 py-2 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)' }}>
            {t('admin.contentReview.sendToReview', { n: picked.size })}
          </button>
        )}
      </div>

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {loading && <div className="py-12 text-center text-sm text-muted">…</div>}

      {!loading && docs.length === 0 && (
        <div className="py-12 text-center text-sm text-muted">{t('admin.contentReview.empty')}</div>
      )}

      <div className="space-y-2">
        {docs.map(d => (
          <DocCard key={d.id} doc={d}
                   pickable={d.status === 'draft'}
                   picked={picked.has(d.id)}
                   onPick={() => togglePick(d.id)}
                   onOpen={() => setOpenDocId(d.id)} />
        ))}
      </div>

      {selectedDoc && (
        <DocDetailModal doc={selectedDoc}
                        token={token}
                        onClose={() => setOpenDocId(null)}
                        onAnyChange={() => { setOpenDocId(null); refreshDocs(); refreshTopics(); }} />
      )}
    </div>
  );
}

function DocCard({ doc, pickable, picked, onPick, onOpen }: {
  doc: GeneratedDoc; pickable: boolean; picked: boolean;
  onPick: () => void; onOpen: () => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="rounded-md p-3 flex items-start gap-3 flex-wrap"
             style={{ background: picked ? 'var(--bg-tertiary)' : 'var(--bg-card)',
                      border: `1px solid ${picked ? 'var(--accent-primary)' : 'var(--border-color)'}` }}>
      {pickable && (
        <input type="checkbox" checked={picked} onChange={onPick} className="mt-1"
               style={{ accentColor: 'var(--accent-primary)' }} />
      )}
      <div className="flex-1 min-w-0 cursor-pointer" onClick={onOpen}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-semibold text-primary">{doc.title || `#${doc.id}`}</span>
          <DocStatusChip status={doc.status} />
          <DocSourceChip source={doc.source} />
          {doc.generation_error && (
            <span className="text-[10px]" style={{ color: '#ef4444' }}>
              ⚠ {t('admin.contentReview.genError')}
            </span>
          )}
        </div>
        {doc.source_query_text && (
          <div className="text-[11px] text-muted mt-0.5">
            {t('admin.contentReview.sourceQuery')}:{doc.source_query_text}
          </div>
        )}
        {doc.summary && (
          <div className="text-xs text-secondary mt-1 line-clamp-2">{doc.summary}</div>
        )}
        {doc.publish_targets.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {doc.publish_targets.map((p, i) => (
              <span key={i} className="text-[10px] px-2 py-0.5 rounded-full"
                    style={{ background: 'rgba(59,130,246,0.12)', color: '#3b82f6' }}>
                {p.platform}{p.media ? ` / ${p.media}` : ''}
              </span>
            ))}
          </div>
        )}
      </div>
      <button type="button" onClick={onOpen}
              className="text-xs px-3 py-1 rounded-md self-center"
              style={{ background: 'var(--bg-input)', color: 'var(--accent-primary)',
                       border: '1px solid var(--border-color)' }}>
        {t('admin.contentReview.view')}
      </button>
    </section>
  );
}

function DocSourceChip({ source }: { source: DocSource }) {
  const { t } = useTranslation();
  const cm = source === 'ai'
    ? { c: '#8b5cf6', bg: 'rgba(139,92,246,0.15)' }
    : { c: '#06b6d4', bg: 'rgba(6,182,212,0.15)' };
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full"
          style={{ background: cm.bg, color: cm.c }}>
      {t(`admin.contentReview.sourceFilter.${source}`)}
    </span>
  );
}

function DocStatusChip({ status }: { status: DocStatus }) {
  const { t } = useTranslation();
  const cm: Record<DocStatus, { c: string; bg: string }> = {
    draft:          { c: '#94a3b8', bg: 'rgba(148,163,184,0.15)' },
    pending_review: { c: '#eab308', bg: 'rgba(234,179,8,0.15)' },
    approved:       { c: '#10b981', bg: 'rgba(16,185,129,0.15)' },
    rejected:       { c: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
    published:      { c: '#3b82f6', bg: 'rgba(59,130,246,0.15)' },
  };
  const s = cm[status];
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full"
          style={{ background: s.bg, color: s.c }}>
      {t(`admin.contentReview.statusFilter.${status}`)}
    </span>
  );
}

function DocDetailModal({ doc, token, onClose, onAnyChange }:
  { doc: GeneratedDoc; token: string; onClose: () => void; onAnyChange: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [showPublish, setShowPublish] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(doc.title);
  const [editBody, setEditBody] = useState(doc.body_markdown);
  const [editSummary, setEditSummary] = useState(doc.summary);

  const wrap = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true); setErr(null);
    try { await fn(); onAnyChange(); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const handleSaveEdit = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await adminContentReviewApi.updateDoc(doc.id, {
        title: editTitle, body_markdown: editBody, summary: editSummary,
      }, token);
      setEditing(false);
      onAnyChange();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.5)' }}>
      <div className="rounded-md w-full max-w-3xl max-h-[90vh] flex flex-col"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="p-4 flex items-start justify-between gap-3 border-b"
             style={{ borderColor: 'var(--border-color)' }}>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-lg font-semibold text-primary">{doc.title || `#${doc.id}`}</h2>
              <DocStatusChip status={doc.status} />
              <DocSourceChip source={doc.source} />
            </div>
            <div className="text-xs text-muted mt-1">
              {doc.source_query_text}
              {' · '}{new Date(doc.created_at).toLocaleString()}
            </div>
          </div>
          {!editing && (
            <button type="button"
                    onClick={() => {
                      setEditTitle(doc.title);
                      setEditBody(doc.body_markdown);
                      setEditSummary(doc.summary);
                      setEditing(true);
                    }}
                    className="text-xs px-2.5 py-1 rounded-md"
                    style={{ background: 'transparent', color: 'var(--text-secondary)',
                             border: '1px solid var(--border-color)' }}>
              ✎ 编辑
            </button>
          )}
          <button type="button" onClick={onClose} className="text-muted hover:text-primary">✕</button>
        </div>

        <div className="p-4 flex-1 overflow-auto space-y-3">
          {err && <div className="text-xs" style={{ color: '#ef4444' }}>{err}</div>}
          {doc.generation_error && (
            <div className="rounded-md p-2 text-xs"
                 style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
              {doc.generation_error}
            </div>
          )}
          {doc.reject_reason && (
            <div className="rounded-md p-2 text-xs"
                 style={{ background: 'rgba(239,68,68,0.05)', color: '#ef4444',
                          border: '1px solid rgba(239,68,68,0.3)' }}>
              {t('admin.contentReview.rejectReason')}:{doc.reject_reason}
            </div>
          )}
          {editing ? (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-muted mb-1">标题</label>
                <input className="w-full rounded-md px-2 py-1.5 text-sm"
                       style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                border: '1px solid var(--border-color)' }}
                       value={editTitle} onChange={e => setEditTitle(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">正文</label>
                <textarea className="w-full rounded-md px-2 py-1.5 text-sm font-mono leading-relaxed"
                          style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                   border: '1px solid var(--border-color)', minHeight: 280 }}
                          value={editBody} onChange={e => setEditBody(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">摘要</label>
                <textarea rows={2} className="w-full rounded-md px-2 py-1.5 text-sm"
                          style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                   border: '1px solid var(--border-color)' }}
                          value={editSummary} onChange={e => setEditSummary(e.target.value)} />
              </div>
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => setEditing(false)} disabled={busy}
                        className="text-xs px-3 py-1.5 rounded-md"
                        style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                                 border: '1px solid var(--border-color)' }}>
                  取消
                </button>
                <button type="button" onClick={handleSaveEdit} disabled={busy}
                        className="text-xs px-3 py-1.5 rounded-md text-white"
                        style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
                  {busy ? '保存中…' : '保存'}
                </button>
              </div>
            </div>
          ) : (
            <article className="text-sm text-primary whitespace-pre-wrap leading-relaxed">
              {doc.body_markdown || <span className="text-muted">{t('admin.contentReview.emptyBody')}</span>}
            </article>
          )}
          {doc.publish_targets.length > 0 && (
            <div>
              <p className="text-xs text-muted mb-1">{t('admin.contentReview.publishedTo')}:</p>
              <div className="flex flex-wrap gap-1.5">
                {doc.publish_targets.map((p, i) => (
                  <span key={i}
                        className="text-[11px] px-2 py-0.5 rounded-full inline-flex items-center gap-1.5"
                        style={{ background: 'rgba(59,130,246,0.12)', color: '#3b82f6' }}>
                    {p.platform}{p.media ? ` / ${p.media}` : ''}
                    {p.url && (
                      <a href={p.url} target="_blank" rel="noreferrer"
                         className="underline opacity-80 hover:opacity-100">🔗</a>
                    )}
                    <span className="text-muted ml-1">
                      {p.marked_at && new Date(p.marked_at).toLocaleDateString()}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="p-3 border-t flex justify-end gap-2 flex-wrap"
             style={{ borderColor: 'var(--border-color)' }}>
          {(doc.status === 'draft' || doc.status === 'pending_review') && (
            <>
              <button type="button" onClick={() => setShowReject(true)} disabled={busy}
                      className="text-xs px-4 py-1.5 rounded-md"
                      style={{ background: 'var(--bg-input)', color: '#ef4444',
                               border: '1px solid var(--border-color)' }}>
                {t('admin.contentReview.reject')}
              </button>
              <button type="button" disabled={busy}
                      onClick={() => wrap(() => adminContentReviewApi.approveDoc(doc.id, token))}
                      className="text-xs px-4 py-1.5 rounded-md text-white"
                      style={{ background: 'var(--accent-primary)' }}>
                {t('admin.contentReview.approve')}
              </button>
            </>
          )}
          {doc.status === 'approved' && (
            <button type="button" onClick={() => setShowPublish(true)} disabled={busy}
                    className="text-xs px-4 py-1.5 rounded-md text-white"
                    style={{ background: '#3b82f6' }}>
              {t('admin.contentReview.choosePublish')}
            </button>
          )}
        </div>

        {showReject && (
          <div className="p-3 border-t"
               style={{ borderColor: 'var(--border-color)', background: 'var(--bg-tertiary)' }}>
            <p className="text-xs text-secondary mb-2">{t('admin.contentReview.rejectReasonLabel')}</p>
            <textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)} rows={2}
                      className="w-full text-sm px-3 py-2 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                               border: '1px solid var(--border-color)' }} />
            <div className="flex justify-end gap-2 mt-2">
              <button type="button" onClick={() => { setShowReject(false); setRejectReason(''); }}
                      className="text-xs px-3 py-1 rounded-md"
                      style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                {t('admin.contentReview.cancel')}
              </button>
              <button type="button" disabled={busy}
                      onClick={() => wrap(() => adminContentReviewApi.rejectDoc(doc.id, rejectReason.trim(), token))}
                      className="text-xs px-3 py-1 rounded-md text-white"
                      style={{ background: '#ef4444' }}>
                {t('admin.contentReview.confirmReject')}
              </button>
            </div>
          </div>
        )}

        {showPublish && (
          <PublishPicker onCancel={() => setShowPublish(false)}
                         onConfirm={(targets) =>
                           wrap(() => adminContentReviewApi.publishDoc(doc.id, targets, token))} />
        )}
      </div>
    </div>
  );
}

const PLATFORM_OPTIONS = ['抖音', '小红书', '视频号', '公众号', 'B站', '知乎', '微博', 'Twitter', 'LinkedIn'];

interface PublishRow {
  platform: string;
  media: string;
  url: string;
}

function PublishPicker({ onCancel, onConfirm }: {
  onCancel: () => void;
  onConfirm: (targets: { platform: string; media: string; url?: string }[]) => void;
}) {
  const { t } = useTranslation();
  // 同一稿子可能投到多家平台 + 多个媒体号,所以一行一组 (platform, media, url)
  const [rows, setRows] = useState<PublishRow[]>([
    { platform: PLATFORM_OPTIONS[0], media: '', url: '' },
  ]);

  const update = (idx: number, patch: Partial<PublishRow>) => {
    setRows(prev => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };
  const addRow = () => setRows(prev => [...prev, { platform: PLATFORM_OPTIONS[0], media: '', url: '' }]);
  const removeRow = (idx: number) => setRows(prev =>
    prev.length === 1 ? prev : prev.filter((_, i) => i !== idx),
  );

  const validRows = rows.filter(r => r.platform.trim());
  const submit = () => {
    if (validRows.length === 0) return;
    onConfirm(validRows.map(r => ({
      platform: r.platform.trim(),
      media: r.media.trim(),
      url: r.url.trim() || undefined,
    })));
  };

  return (
    <div className="p-3 border-t space-y-3"
         style={{ borderColor: 'var(--border-color)', background: 'var(--bg-tertiary)' }}>
      <p className="text-xs text-secondary">{t('admin.contentReview.publishPickerHint')}</p>

      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-12 gap-2 items-start">
            <select value={r.platform}
                    onChange={e => update(i, { platform: e.target.value })}
                    className="col-span-3 text-xs px-2 py-1.5 rounded-md"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                             border: '1px solid var(--border-color)' }}>
              {PLATFORM_OPTIONS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
            <input type="text" value={r.media}
                   onChange={e => update(i, { media: e.target.value })}
                   placeholder={t('admin.contentReview.publishMediaPlaceholder')}
                   className="col-span-4 text-xs px-2 py-1.5 rounded-md"
                   style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                            border: '1px solid var(--border-color)' }} />
            <input type="url" value={r.url}
                   onChange={e => update(i, { url: e.target.value })}
                   placeholder={t('admin.contentReview.publishUrlPlaceholder')}
                   className="col-span-4 text-xs px-2 py-1.5 rounded-md"
                   style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                            border: '1px solid var(--border-color)' }} />
            <button type="button" onClick={() => removeRow(i)}
                    disabled={rows.length === 1}
                    className="col-span-1 text-xs px-2 py-1.5 rounded-md"
                    style={{
                      background: 'var(--bg-input)',
                      color: rows.length === 1 ? 'var(--text-muted)' : '#ef4444',
                      border: '1px solid var(--border-color)',
                      opacity: rows.length === 1 ? 0.5 : 1,
                    }}>
              ✕
            </button>
          </div>
        ))}
      </div>

      <button type="button" onClick={addRow}
              className="text-xs px-3 py-1 rounded-md"
              style={{ background: 'var(--bg-input)', color: 'var(--accent-primary)',
                       border: '1px dashed var(--border-color)' }}>
        + {t('admin.contentReview.addPublishRow')}
      </button>

      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel}
                className="text-xs px-3 py-1 rounded-md"
                style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
          {t('admin.contentReview.cancel')}
        </button>
        <button type="button" disabled={validRows.length === 0} onClick={submit}
                className="text-xs px-3 py-1 rounded-md text-white"
                style={{ background: '#3b82f6', opacity: validRows.length === 0 ? 0.5 : 1 }}>
          {t('admin.contentReview.confirmPublish', { n: validRows.length })}
        </button>
      </div>
    </div>
  );
}
