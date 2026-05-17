// Phase D — 用户侧资料编辑 + 监测问题勾选 + 提交审核.
// 路由:/dashboard/topics/:topicId/profile
//
// 状态机:
//   draft → (submit) → pending → admin review → approved | rejected
//   rejected 可重新编辑后再 submit;approved 后整张表只读
//
// 三 tab:资料 / 种子词 / 监测问题(候选 + 勾选 ≤50)
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import {
  aiTelemetryApi, EMPTY_BRAND_PROFILE, MAX_SELECTED_QUERIES,
  type BrandProfile, type Topic, type SubmissionStatus,
} from '../../services/aiTelemetryApi';
import { topicProfileApi } from '../../services/topicProfileApi';
import {
  BrandProfileForm, PROFILE_REQUIRED_FIELDS as REQUIRED_FIELDS,
  profileMissingFields,
} from '../../components/BrandProfileForm';
import { ProfileImporter } from '../../components/ProfileImporter';

type Tab = 'profile' | 'seeds' | 'monitor';

export function TopicProfile() {
  const { t } = useTranslation();
  const { topicId } = useParams();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const tid = Number(topicId);

  const [tab, setTab] = useState<Tab>('profile');
  const [topic, setTopic] = useState<Topic | null>(null);
  const [profile, setProfile] = useState<BrandProfile>(EMPTY_BRAND_PROFILE);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const all = await aiTelemetryApi.listTopics(token);
      const found = all.find(t => t.id === tid);
      if (!found) {
        setErr(t('topic.profile.notFound'));
        return;
      }
      setTopic(found);
      setProfile({ ...EMPTY_BRAND_PROFILE, ...(found.profile || {}) });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [tid, token, t]);

  useEffect(() => { refresh(); }, [refresh]);

  const status: SubmissionStatus = (topic?.submission_status as SubmissionStatus) || 'draft';
  const isEditable = status === 'draft' || status === 'rejected';

  const handleSaveProfile = async () => {
    if (busy) return;
    setBusy(true); setErr(null); setOkMsg(null);
    try {
      const updated = await topicProfileApi.updateProfile(tid, profile, token);
      setTopic(updated);
      setProfile({ ...EMPTY_BRAND_PROFILE, ...(updated.profile || {}) });
      setOkMsg(t('topic.profile.saved'));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const missingFields = useMemo(
    () => profileMissingFields(profile, REQUIRED_FIELDS),
    [profile],
  );

  const selectedCount = useMemo(
    () => (topic?.query_selected || []).filter(Boolean).length,
    [topic],
  );

  const handleSubmit = async () => {
    if (busy) return;
    setBusy(true); setErr(null); setOkMsg(null);
    try {
      const updated = await topicProfileApi.submitForReview(tid, token);
      setTopic(updated);
      setOkMsg(t('topic.profile.submitted'));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-[50vh] flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2"
             style={{ borderColor: 'var(--accent-primary)' }} />
      </div>
    );
  }

  if (!topic) {
    return <div className="p-6 text-sm text-muted">{err || t('topic.profile.notFound')}</div>;
  }

  return (
    <div className="space-y-4 max-w-5xl mx-auto p-4 md:p-6">
      <PageHead titleKey="topic.profile.title" titleFallback="主题资料" />

      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-primary leading-tight">
            {t('topic.profile.title')} — {topic.name}
          </h1>
          <p className="text-xs text-secondary mt-0.5">{t('topic.profile.subtitle')}</p>
        </div>
        <button type="button" onClick={() => navigate('/dashboard')}
                className="text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
          ← {t('topic.profile.backToDashboard')}
        </button>
      </header>

      <StatusBanner status={status} t={t} rejectedReason={null} />

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}
      {okMsg && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'rgba(16,185,129,0.1)', color: '#10b981', border: '1px solid rgba(16,185,129,0.3)' }}>
          {okMsg}
        </div>
      )}

      <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
        {(['profile', 'seeds', 'monitor'] as Tab[]).map(k => (
          <button key={k} type="button" onClick={() => setTab(k)}
                  className="px-3 py-2 text-sm -mb-px"
                  style={{
                    borderBottom: tab === k ? '2px solid var(--accent-primary)' : '2px solid transparent',
                    color: tab === k ? 'var(--accent-primary)' : 'var(--text-secondary)',
                  }}>
            {t(`topic.profile.tab.${k}`)}
            {k === 'monitor' && (
              <span className="ml-1 text-[10px] text-muted">({selectedCount}/{MAX_SELECTED_QUERIES})</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'profile' && (
        <div className="space-y-4">
          <ProfileImporter profile={profile} onApply={setProfile}
                           token={token} disabled={!isEditable} />
          <BrandProfileForm profile={profile} onChange={setProfile}
                            readOnly={!isEditable} requiredKeys={REQUIRED_FIELDS} />
        </div>
      )}
      {tab === 'seeds' && (
        <SeedsView topic={topic} onChange={refresh} token={token} readOnly={!isEditable} />
      )}
      {tab === 'monitor' && (
        <MonitorView topic={topic} onChange={refresh} token={token} readOnly={!isEditable} />
      )}

      {/* 操作栏 */}
      <div className="sticky bottom-0 mt-6 flex items-center justify-between gap-3 flex-wrap p-3 rounded-md"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="text-xs text-muted">
          {missingFields.length > 0 && (
            <span style={{ color: '#f59e0b' }}>
              {t('topic.profile.missing', { n: missingFields.length })}
            </span>
          )}
          {missingFields.length === 0 && (
            <span style={{ color: '#10b981' }}>{t('topic.profile.allFilled')}</span>
          )}
          <span className="mx-2">·</span>
          <span>{t('topic.profile.selectedNow', { n: selectedCount, max: MAX_SELECTED_QUERIES })}</span>
        </div>
        <div className="flex items-center gap-2">
          {tab === 'profile' && isEditable && (
            <button type="button" disabled={busy} onClick={handleSaveProfile}
                    className="text-sm px-4 py-2 rounded-md text-white"
                    style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
              {busy ? t('topic.profile.saving') : t('topic.profile.save')}
            </button>
          )}
          {isEditable && (
            <button type="button" disabled={busy || missingFields.length > 0 || selectedCount < 1}
                    onClick={handleSubmit}
                    className="text-sm px-4 py-2 rounded-md text-white"
                    style={{
                      background: '#10b981',
                      opacity: (busy || missingFields.length > 0 || selectedCount < 1) ? 0.4 : 1,
                    }}>
              {busy ? t('topic.profile.submitting') : t('topic.profile.submitForReview')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusBanner({ status, t }: { status: SubmissionStatus; t: (k: string) => string; rejectedReason: string | null }) {
  const styleMap: Record<SubmissionStatus, { bg: string; color: string; label: string }> = {
    draft:     { bg: 'rgba(148,163,184,0.15)', color: '#94a3b8', label: t('topic.profile.status.draft') },
    pending:   { bg: 'rgba(234,179,8,0.15)',   color: '#eab308', label: t('topic.profile.status.pending') },
    approved:  { bg: 'rgba(16,185,129,0.15)',  color: '#10b981', label: t('topic.profile.status.approved') },
    rejected:  { bg: 'rgba(239,68,68,0.15)',   color: '#ef4444', label: t('topic.profile.status.rejected') },
  };
  const s = styleMap[status];
  return (
    <div className="rounded-md p-2.5 text-sm flex items-center gap-2"
         style={{ background: s.bg, color: s.color, border: `1px solid ${s.color}33` }}>
      <span className="font-semibold">{s.label}</span>
      <span className="text-xs opacity-80">{t(`topic.profile.statusHint.${status}`)}</span>
    </div>
  );
}

// ProfileForm + Section/Label/TextRow/TextAreaRow/TagsRow 已抽到 components/BrandProfileForm.tsx


// ─────────────── SeedsView ───────────────

interface SeedsViewProps {
  topic: Topic; onChange: () => void; token: string; readOnly: boolean;
}

function SeedsView({ topic, onChange, token, readOnly }: SeedsViewProps) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    const v = text.trim();
    if (!v || busy) return;
    setBusy(true); setErr(null);
    try {
      const resp = await fetch(
        `/api/ai-telemetry/topics/${topic.id}/seed-prompts`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: v }),
        },
      );
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(body || `HTTP ${resp.status}`);
      }
      setText('');
      onChange();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">{t('topic.profile.seeds.hint')}</p>
      {err && <div className="text-xs" style={{ color: '#ef4444' }}>{err}</div>}
      {!readOnly && (
        <div className="flex gap-2">
          <input type="text" value={text}
                 onChange={e => setText(e.target.value)}
                 placeholder={t('topic.profile.seeds.placeholder')}
                 className="flex-1 text-sm px-3 py-2 rounded-md"
                 style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                          border: '1px solid var(--border-color)' }} />
          <button type="button" onClick={submit} disabled={busy || !text.trim()}
                  className="text-sm px-4 py-2 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: (busy || !text.trim()) ? 0.5 : 1 }}>
            {busy ? '...' : t('topic.profile.seeds.add')}
          </button>
        </div>
      )}
      <div className="space-y-2">
        {(topic.seed_prompts || []).map((s, i) => (
          <div key={i}
               className="rounded-md p-2.5 flex items-center gap-2 flex-wrap"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
            <span className="text-sm text-primary flex-1">{s.text}</span>
            <StatusChip status={s.status} />
          </div>
        ))}
        {(topic.seed_prompts || []).length === 0 && (
          <div className="text-xs text-muted py-6 text-center">{t('topic.profile.seeds.empty')}</div>
        )}
      </div>
    </div>
  );
}

function StatusChip({ status }: { status: 'pending' | 'approved' | 'rejected' }) {
  const color = status === 'approved' ? '#10b981' : status === 'rejected' ? '#ef4444' : '#eab308';
  const label = status === 'approved' ? '已批准' : status === 'rejected' ? '已拒绝' : '待审';
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full"
          style={{ background: `${color}22`, color }}>
      {label}
    </span>
  );
}

// ─────────────── MonitorView — 扩展 + 勾选 ─────────────

interface MonitorViewProps {
  topic: Topic; onChange: () => void; token: string; readOnly: boolean;
}

function MonitorView({ topic, onChange, token, readOnly }: MonitorViewProps) {
  const { t } = useTranslation();
  const [seed, setSeed] = useState('');
  const [count, setCount] = useState(30);
  const [candidates, setCandidates] = useState<string[]>([]);
  const [expanding, setExpanding] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 当前已知 query(资料里) + selected map
  const existing = useMemo(() => {
    const m = new Map<string, boolean>();
    (topic.queries || []).forEach((q, i) => m.set(q, (topic.query_selected || [])[i] ?? true));
    candidates.forEach(c => { if (!m.has(c)) m.set(c, false); });
    return m;
  }, [topic, candidates]);

  const totalSelected = useMemo(
    () => Array.from(existing.values()).filter(Boolean).length,
    [existing],
  );

  const togglePick = (text: string) => {
    if (readOnly) return;
    const cur = existing.get(text) ?? false;
    if (!cur && totalSelected >= MAX_SELECTED_QUERIES) {
      setErr(t('topic.profile.monitor.cap', { max: MAX_SELECTED_QUERIES }));
      return;
    }
    const items = Array.from(existing.entries()).map(([k, v]) => ({
      text: k, selected: k === text ? !cur : v,
    }));
    persistSelection(items);
  };

  const persistSelection = async (items: { text: string; selected: boolean }[]) => {
    setBusy(true); setErr(null);
    try {
      await topicProfileApi.updateSelectedQueries(topic.id, items, token);
      onChange();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const expand = async () => {
    if (!seed.trim() || expanding) return;
    setExpanding(true); setErr(null);
    try {
      const resp = await topicProfileApi.expandQueries(topic.id, seed.trim(), count, token);
      setCandidates(resp.queries || []);
      onChange();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setExpanding(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="rounded-md p-3"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <p className="text-sm font-semibold text-primary mb-2">{t('topic.profile.monitor.expandTitle')}</p>
        <p className="text-xs text-muted mb-2">{t('topic.profile.monitor.expandHint')}</p>
        {!readOnly && (
          <div className="flex gap-2 flex-wrap">
            <input type="text" value={seed} onChange={e => setSeed(e.target.value)}
                   placeholder={t('topic.profile.monitor.seedPlaceholder')}
                   className="flex-1 min-w-[180px] text-sm px-3 py-2 rounded-md"
                   style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                            border: '1px solid var(--border-color)' }} />
            <input type="number" min={5} max={MAX_SELECTED_QUERIES} value={count}
                   onChange={e => setCount(Math.min(MAX_SELECTED_QUERIES, Math.max(5, +e.target.value)))}
                   className="w-20 text-sm px-3 py-2 rounded-md"
                   style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                            border: '1px solid var(--border-color)' }} />
            <button type="button" onClick={expand} disabled={expanding || !seed.trim()}
                    className="text-sm px-4 py-2 rounded-md text-white"
                    style={{ background: 'var(--accent-primary)',
                             opacity: (expanding || !seed.trim()) ? 0.5 : 1 }}>
              {expanding ? '...' : t('topic.profile.monitor.expand')}
            </button>
          </div>
        )}
      </div>

      {err && <div className="text-xs" style={{ color: '#ef4444' }}>{err}</div>}

      <p className="text-xs text-muted">
        {t('topic.profile.monitor.selectedHint', { n: totalSelected, max: MAX_SELECTED_QUERIES })}
      </p>

      <div className="space-y-1.5">
        {Array.from(existing.entries()).map(([text, sel]) => (
          <label key={text}
                 className="flex items-start gap-2 p-2 rounded-md cursor-pointer"
                 style={{
                   background: sel ? 'var(--bg-tertiary)' : 'var(--bg-card)',
                   border: `1px solid ${sel ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                   opacity: busy ? 0.6 : 1,
                 }}>
            <input type="checkbox" checked={sel}
                   disabled={readOnly || busy}
                   onChange={() => togglePick(text)}
                   className="mt-0.5"
                   style={{ accentColor: 'var(--accent-primary)' }} />
            <span className="text-sm text-primary flex-1">{text}</span>
          </label>
        ))}
        {existing.size === 0 && (
          <div className="text-xs text-muted py-8 text-center">{t('topic.profile.monitor.empty')}</div>
        )}
      </div>
    </div>
  );
}
