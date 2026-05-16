// Admin 审核 — Phase D:按申请聚合(每张卡片 = 一个 topic 申请).
// 展示画像 + 种子 + 监测问题;支持 admin 内联修改 / 批准 / 拒绝.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import {
  adminReviewApi,
  type TopicReviewListItem, type TopicReviewDetail,
} from '../../services/adminReviewApi';
import type { BrandProfile, SubmissionStatus } from '../../services/aiTelemetryApi';
import { MAX_SELECTED_QUERIES } from '../../services/aiTelemetryApi';

const STATUS_FILTERS: { key: SubmissionStatus | 'all'; label: string }[] = [
  { key: 'pending',  label: 'pending' },
  { key: 'rejected', label: 'rejected' },
  { key: 'approved', label: 'approved' },
  { key: 'all',      label: 'all' },
];

export function AdminReview() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const [filter, setFilter] = useState<SubmissionStatus | 'all'>('pending');
  const [items, setItems] = useState<TopicReviewListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  const isAdmin = useMemo(() => {
    try {
      const stored = localStorage.getItem('user');
      return stored ? !!JSON.parse(stored).is_admin : false;
    } catch { return false; }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const rs = await adminReviewApi.listTopicReviews(
        token,
        filter === 'all' ? undefined : filter,
      );
      setItems(rs);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token, filter]);

  useEffect(() => {
    if (!isAdmin) { navigate('/dashboard', { replace: true }); return; }
    refresh();
  }, [isAdmin, navigate, refresh]);

  if (!isAdmin) return null;

  return (
    <div className="space-y-4">
      <PageHead titleKey="admin.review.title" titleFallback="Review" />
      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-primary leading-tight">
            {t('admin.review.title')}
          </h1>
          <p className="text-xs text-secondary mt-0.5">{t('admin.review.subtitle')}</p>
        </div>
        <button type="button" onClick={refresh}
                className="text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}>
          ⟳ {t('admin.review.refresh')}
        </button>
      </header>

      <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
        {STATUS_FILTERS.map(f => (
          <button key={f.key} type="button" onClick={() => setFilter(f.key)}
                  className="px-3 py-2 text-sm -mb-px capitalize"
                  style={{
                    borderBottom: filter === f.key ? '2px solid var(--accent-primary)' : '2px solid transparent',
                    color: filter === f.key ? 'var(--accent-primary)' : 'var(--text-secondary)',
                  }}>
            {t(`admin.review.filter.${f.key}`)}
          </button>
        ))}
      </div>

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}

      {loading && <div className="py-12 text-center text-sm text-muted">…</div>}

      {!loading && items.length === 0 && (
        <div className="py-12 text-center text-sm text-muted">{t('admin.review.empty')}</div>
      )}

      <div className="space-y-3">
        {items.map(it => (
          <ApplicationCard key={it.topic_id} item={it}
                           expanded={openId === it.topic_id}
                           onToggle={() => setOpenId(openId === it.topic_id ? null : it.topic_id)}
                           token={token}
                           onChange={refresh} />
        ))}
      </div>
    </div>
  );
}

function ApplicationCard({
  item, expanded, onToggle, token, onChange,
}: {
  item: TopicReviewListItem;
  expanded: boolean;
  onToggle: () => void;
  token: string;
  onChange: () => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<TopicReviewDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [draft, setDraft] = useState<BrandProfile | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    (async () => {
      try {
        const d = await adminReviewApi.getTopicReview(item.topic_id, token);
        if (!cancelled) setDetail(d);
      } catch (e: unknown) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [expanded, item.topic_id, token]);

  const handleApprove = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await adminReviewApi.approveTopic(item.topic_id, token);
      onChange();
      navigate(`/workbench/topics/${item.topic_id}/execution-plan`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await adminReviewApi.rejectTopic(item.topic_id, rejectReason.trim(), token);
      onChange();
      setRejectOpen(false); setRejectReason('');
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveProfile = async () => {
    if (!draft || busy) return;
    setBusy(true); setErr(null);
    try {
      const d = await adminReviewApi.patchTopic(item.topic_id, { profile: draft }, token);
      setDetail(d); setEditingProfile(false); setDraft(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-md"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <button type="button" onClick={onToggle}
              className="w-full p-3 flex items-start gap-3 flex-wrap text-left">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-primary">{item.profile_name || item.topic_name}</span>
            <span className="text-xs text-muted">#{item.topic_id}</span>
            <StatusChip status={item.submission_status} />
          </div>
          <div className="text-xs text-secondary mt-0.5">
            {item.company_short_name && `${item.company_short_name} · `}
            {item.industry && `${item.industry} · `}
            {item.user_email}
          </div>
          <div className="text-[11px] text-muted mt-1">
            {t('admin.review.card.seedCount', { n: item.seed_count })} · {t('admin.review.card.selectedCount', { n: item.selected_query_count, max: MAX_SELECTED_QUERIES })}
            {item.submitted_at && (
              <> · {t('admin.review.card.submittedAt')} {new Date(item.submitted_at).toLocaleString()}</>
            )}
          </div>
        </div>
        <span className="text-xs text-accent">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t pt-3"
             style={{ borderColor: 'var(--border-color)' }}>
          {err && <div className="text-xs" style={{ color: '#ef4444' }}>{err}</div>}
          {!detail && <div className="text-xs text-muted">…</div>}
          {detail && (
            <>
              {/* 画像 */}
              <Block title={t('admin.review.section.profile')} onEdit={item.submission_status !== 'approved' ? () => {
                setDraft(detail.profile || null); setEditingProfile(true);
              } : undefined}>
                {!editingProfile && <ProfileSummary profile={detail.profile} />}
                {editingProfile && draft && (
                  <ProfileQuickEditor draft={draft} setDraft={setDraft}
                                      onSave={saveProfile}
                                      onCancel={() => { setEditingProfile(false); setDraft(null); }}
                                      busy={busy} />
                )}
              </Block>

              {/* 种子 */}
              <Block title={t('admin.review.section.seeds')}>
                <ul className="text-sm space-y-1">
                  {(detail.seed_prompts || []).map((s, i) => (
                    <li key={i} className="flex items-center gap-2 flex-wrap">
                      <span className="text-primary">{s.text}</span>
                      <StatusChip status={(s.status as SubmissionStatus)} small />
                    </li>
                  ))}
                  {(detail.seed_prompts || []).length === 0 && (
                    <li className="text-xs text-muted">—</li>
                  )}
                </ul>
              </Block>

              {/* 监测问题 (selected) */}
              <Block title={t('admin.review.section.monitored', {
                n: detail.query_selected?.filter(Boolean).length || 0,
                max: MAX_SELECTED_QUERIES,
              })}>
                <ul className="text-sm space-y-1">
                  {(detail.queries || []).map((q, i) => {
                    const sel = (detail.query_selected || [])[i];
                    if (!sel) return null;
                    return (
                      <li key={i} className="flex items-center gap-2">
                        <span className="text-primary flex-1">{q}</span>
                        <StatusChip status={(detail.query_statuses?.[i] || 'approved') as SubmissionStatus} small />
                      </li>
                    );
                  })}
                </ul>
              </Block>

              {/* 主题日志 */}
              {detail.topic_changelog?.length > 0 && (
                <Block title={t('admin.review.section.changelog')}>
                  <ul className="text-xs space-y-1 max-h-40 overflow-auto">
                    {detail.topic_changelog.slice(-10).reverse().map((e, i) => (
                      <li key={i} className="text-secondary">
                        <span className="text-muted">{new Date(e.at).toLocaleString()}</span>
                        <span className="mx-1">·</span>
                        <span style={{ color: e.actor_role === 'admin' ? '#ef4444' : '#10b981' }}>{e.actor_role}</span>
                        <span className="mx-1">·</span>
                        <span>{e.field}</span>
                        {e.after && <span className="text-primary ml-1">→ {e.after}</span>}
                        {e.note && <span className="text-muted ml-1">({e.note})</span>}
                      </li>
                    ))}
                  </ul>
                </Block>
              )}

              {/* 操作 */}
              {item.submission_status === 'pending' && (
                <div className="flex justify-end gap-2 pt-2">
                  <button type="button" disabled={busy} onClick={() => setRejectOpen(true)}
                          className="text-xs px-4 py-1.5 rounded-md"
                          style={{ background: 'var(--bg-input)', color: '#ef4444',
                                   border: '1px solid var(--border-color)',
                                   opacity: busy ? 0.5 : 1 }}>
                    {t('admin.review.reject')}
                  </button>
                  <button type="button" disabled={busy} onClick={handleApprove}
                          className="text-xs px-4 py-1.5 rounded-md text-white"
                          style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
                    {t('admin.review.approve')}
                  </button>
                </div>
              )}

              {item.submission_status === 'approved' && (
                <button type="button"
                        onClick={() => navigate(`/workbench/topics/${item.topic_id}/execution-plan`)}
                        className="text-xs px-4 py-1.5 rounded-md text-white w-full"
                        style={{ background: 'var(--accent-primary)' }}>
                  {t('admin.review.viewPlan')} →
                </button>
              )}

              {rejectOpen && (
                <div className="rounded-md p-3"
                     style={{ background: 'rgba(239,68,68,0.05)', border: '1px solid rgba(239,68,68,0.3)' }}>
                  <p className="text-xs text-secondary mb-2">{t('admin.review.rejectReasonLabel')}</p>
                  <textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)}
                            rows={2}
                            className="w-full text-sm px-3 py-2 rounded-md"
                            style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                                     border: '1px solid var(--border-color)' }} />
                  <div className="flex justify-end gap-2 mt-2">
                    <button type="button" onClick={() => { setRejectOpen(false); setRejectReason(''); }}
                            className="text-xs px-3 py-1 rounded-md"
                            style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                      {t('admin.review.cancel')}
                    </button>
                    <button type="button" disabled={busy} onClick={handleReject}
                            className="text-xs px-3 py-1 rounded-md text-white"
                            style={{ background: '#ef4444', opacity: busy ? 0.5 : 1 }}>
                      {t('admin.review.confirmReject')}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function Block({ title, children, onEdit }:
  { title: string; children: React.ReactNode; onEdit?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-md p-3"
         style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-semibold text-secondary">{title}</p>
        {onEdit && (
          <button type="button" onClick={onEdit}
                  className="text-[10px] px-2 py-0.5 rounded-md"
                  style={{ background: 'var(--bg-card)', color: 'var(--accent-primary)' }}>
            {t('admin.review.edit')}
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

function ProfileSummary({ profile }: { profile?: BrandProfile }) {
  if (!profile) return <div className="text-xs text-muted">—</div>;
  const rows: [string, string | string[]][] = [
    ['公司全称', profile.company_full_name],
    ['行业', profile.industry],
    ['服务地域', profile.service_geo],
    ['核心业务', profile.core_business_lines],
    ['创作方向', profile.creation_directions],
    ['适配平台', profile.target_platforms],
    ['内容调性', profile.content_tones],
    ['内容雷区', profile.content_redlines],
    ['品牌差异化', profile.brand_diff_tags],
    ['用户痛点', profile.user_pain_points],
    ['品牌故事', profile.brand_story],
    ['Slogan', profile.brand_slogan],
    ['核心信息', profile.core_message],
  ];
  return (
    <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
      {rows.filter(([_, v]) => Array.isArray(v) ? v.length > 0 : !!v).map(([k, v]) => (
        <div key={k} className="flex items-start gap-2">
          <dt className="text-muted shrink-0 min-w-[70px]">{k}:</dt>
          <dd className="text-primary break-words">
            {Array.isArray(v) ? v.join(' · ') : v}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ProfileQuickEditor({
  draft, setDraft, onSave, onCancel, busy,
}: {
  draft: BrandProfile;
  setDraft: (p: BrandProfile) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  // Admin 编辑器 — 只暴露最常调整的几项,要改全表去 user 端 /dashboard/topics/:id/profile.
  return (
    <div className="space-y-2">
      <div className="text-[10px] text-muted">{t('admin.review.editorNote')}</div>
      {([
        ['profile_name', '画像名称'],
        ['company_full_name', '公司全称'],
        ['company_short_name', '公司简称'],
        ['industry', '行业'],
        ['service_geo', '服务地域'],
        ['brand_slogan', 'Slogan'],
        ['core_message', '核心信息'],
      ] as [keyof BrandProfile, string][]).map(([k, label]) => (
        <div key={k} className="flex items-center gap-2">
          <label className="text-xs text-muted w-20 shrink-0">{label}</label>
          <input type="text" value={(draft[k] as string) || ''}
                 onChange={e => setDraft({ ...draft, [k]: e.target.value })}
                 className="flex-1 text-xs px-2 py-1 rounded-md"
                 style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                          border: '1px solid var(--border-color)' }} />
        </div>
      ))}
      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onCancel}
                className="text-xs px-3 py-1 rounded-md"
                style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
          {t('admin.review.cancel')}
        </button>
        <button type="button" disabled={busy} onClick={onSave}
                className="text-xs px-3 py-1 rounded-md text-white"
                style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
          {t('admin.review.save')}
        </button>
      </div>
    </div>
  );
}

function StatusChip({ status, small = false }: { status: SubmissionStatus; small?: boolean }) {
  const { t } = useTranslation();
  const cm: Record<SubmissionStatus, { c: string; bg: string }> = {
    draft:     { c: '#94a3b8', bg: 'rgba(148,163,184,0.15)' },
    pending:   { c: '#eab308', bg: 'rgba(234,179,8,0.15)' },
    approved:  { c: '#10b981', bg: 'rgba(16,185,129,0.15)' },
    rejected:  { c: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
  };
  const s = cm[status] || cm.draft;
  return (
    <span className={small ? 'text-[10px] px-1.5 py-0 rounded-full' : 'text-xs px-2 py-0.5 rounded-full'}
          style={{ background: s.bg, color: s.c }}>
      {t(`admin.review.filter.${status}`)}
    </span>
  );
}
