// Phase D — user-side materials editing + prompt selection + submit for review.
// Route: /dashboard/topics/:topicId/profile
//
// State machine:
//   draft → (submit) → pending → admin review → approved | rejected
//   rejected can be edited and resubmitted; after approved the whole form is read-only
//
// Three tabs: materials / seed queries / prompts (candidates + selection ≤50)
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import {
  aiTelemetryApi, EMPTY_BRAND_PROFILE, MAX_SELECTED_QUERIES,
  type BrandProfile, type SceneType, type Topic, type SubmissionStatus,
} from '../../services/aiTelemetryApi';
import {
  topicProfileApi,
} from '../../services/topicProfileApi';
import { SceneExpander } from '../../components/SceneExpander';
import {
  BrandProfileForm, PROFILE_REQUIRED_FIELDS as REQUIRED_FIELDS,
  profileMissingFields,
} from '../../components/BrandProfileForm';
import { ProfileImporter } from '../../components/ProfileImporter';
import { MonitorRunPanel } from '../../components/MonitorRunPanel';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { readApiError, friendlyErrorMessage } from '../../services/apiError';

type Tab = 'profile' | 'seeds' | 'monitor';

export function TopicProfile() {
  const { t } = useTranslation();
  const { topicId } = useParams();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const tid = Number(topicId);

  // ?tab=monitor: the dashboard "Monitoring & batch runs" button lands directly on the prompts tab (the panel is at the top of that page)
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(() => {
    const q = searchParams.get('tab');
    return q === 'seeds' || q === 'monitor' ? q : 'profile';
  });
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
        <button type="button" onClick={() => navigate('/app')}
                className="text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
          ← {t('topic.profile.backToDashboard')}
        </button>
      </header>

      <StatusBanner status={status} t={t} rejectedReason={null} />

      {err && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)', border: '1px solid rgba(239,68,68,0.3)' }}>
          {err}
        </div>
      )}
      {okMsg && (
        <div className="rounded-md p-3 text-sm"
             style={{ background: 'var(--success-bg)', color: 'var(--success-fg)', border: '1px solid rgba(16,185,129,0.3)' }}>
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
                           token={token} disabled={!isEditable}
                           topicId={topic.id} />
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

      {/* Action bar */}
      <div className="sticky bottom-0 mt-6 flex items-center justify-between gap-3 flex-wrap p-3 rounded-md"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="text-xs text-muted">
          {missingFields.length > 0 && (
            <span style={{ color: 'var(--warn-fg)' }}>
              {t('topic.profile.missing', { n: missingFields.length })}
            </span>
          )}
          {missingFields.length === 0 && (
            <span style={{ color: 'var(--success-fg)' }}>{t('topic.profile.allFilled')}</span>
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
          {/* Review flow removed: clients no longer "submit for review". Instead admins "Start project" in the workbench. */}
        </div>
      </div>
    </div>
  );
}

function StatusBanner({ status, t }: { status: SubmissionStatus; t: (k: string) => string; rejectedReason: string | null }) {
  const styleMap: Record<SubmissionStatus, { bg: string; color: string; label: string }> = {
    draft:     { bg: 'rgba(148,163,184,0.15)', color: '#94a3b8', label: t('topic.profile.status.draft') },
    pending:   { bg: 'rgba(234,179,8,0.15)',   color: 'var(--warn-fg)', label: t('topic.profile.status.pending') },
    approved:  { bg: 'var(--success-bg)',  color: 'var(--success-fg)', label: t('topic.profile.status.approved') },
    rejected:  { bg: 'var(--danger-bg)',   color: 'var(--danger-fg)', label: t('topic.profile.status.rejected') },
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

// ProfileForm + Section/Label/TextRow/TextAreaRow/TagsRow have been extracted to components/BrandProfileForm.tsx


// ─────────────── SeedsView ───────────────

interface SeedsViewProps {
  topic: Topic; onChange: () => void; token: string; readOnly: boolean;
}

function SeedsView({ topic, onChange, token, readOnly }: SeedsViewProps) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const v = text.trim();
    if (!v || busy) return;
    setBusy(true);
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
        // Echoing the raw response body would splash nginx's full 502 HTML page onto the UI during the few seconds of a deploy restart
        // (see services/apiError.readApiError). Only take the normalized one-line message here.
        throw new Error(await readApiError(resp, '添加失败'));
      }
      setText('');
      onChange();
    } catch (e: unknown) {
      toast.error(friendlyErrorMessage(e, '添加失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">{t('topic.profile.seeds.hint')}</p>
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
            <StatusChip />
          </div>
        ))}
        {(topic.seed_prompts || []).length === 0 && (
          <div className="text-xs text-muted py-6 text-center">{t('topic.profile.seeds.empty')}</div>
        )}
      </div>
    </div>
  );
}

// Seed queries are no longer reviewed (2026-09-09, see backend api/ai_telemetry.submit_seed_prompt): submitting makes them
// approved, joining batch runs the same day. So this always shows "Active" -- drawing "Pending review" would make users think the query is held back,
// when it's actually already running and has already consumed credits per Q&A.
function StatusChip() {
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full"
          style={{ background: '#10b98122', color: '#10b981' }}>
      已生效
    </span>
  );
}

// The "Continuous monitoring" card has been merged into components/MonitorRunPanel.tsx (the monitoring & batch-run panel):
// models / cadence / day and time / pause / run new / run all once / current status, one card mounted in two places.

// ─────────────── MonitorView — expansion + selection ─────────────

interface MonitorViewProps {
  topic: Topic; onChange: () => void; token: string; readOnly: boolean;
}

function MonitorView({ topic, onChange, token, readOnly }: MonitorViewProps) {
  const { t } = useTranslation();
  // 2026-05-28 — 4-dimension scene expansion. Candidates (query expansion output not yet persisted) are maintained inside SceneExpander;
  // here we only track the seed query + scene tag for "candidate text the user picked from a scene", sent to the backend together on persist.
  const [candidateSeeds, setCandidateSeeds] = useState<Map<string, string>>(new Map());
  const [candidateScenes, setCandidateScenes] = useState<Map<string, SceneType>>(new Map());
  // All texts the user has checked in SceneExpander tabs (for echo + syncing into existing).
  // Once checked, these texts also appear in the "Selected" area, even if later unchecked via togglePick.
  const [pickedTexts, setPickedTexts] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 2026-08-17 — soft-deleted queries. The backend still returns all elements (historical metric definitions need to look up scene tags by text),
  // distinguished by this parallel array; hidden by default, revealed and restorable only with "Include deleted".
  const deletedSet = useMemo(() => {
    const s = new Set<string>();
    (topic.queries || []).forEach((q, i) => {
      if ((topic.query_deleted || [])[i]) s.add(q);
    });
    return s;
  }, [topic]);
  const [showDeleted, setShowDeleted] = useState(false);

  // Currently known queries (in materials) + selected map. Once a candidate is added it counts as existing too.
  // Soft-deleted queries aren't included here: they don't take part in selection and must never be written back to selected-queries via buildItems.
  const existing = useMemo(() => {
    const m = new Map<string, boolean>();
    (topic.queries || []).forEach((q, i) => {
      if ((topic.query_deleted || [])[i]) return;
      m.set(q, (topic.query_selected || [])[i] ?? true);
    });
    pickedTexts.forEach(c => { if (!m.has(c)) m.set(c, false); });
    return m;
  }, [topic, pickedTexts]);

  // 2026-05-26 — locked query (derived from the seed's original text) map: the UI can't uncheck them
  const lockedSet = useMemo(() => {
    const s = new Set<string>();
    (topic.queries || []).forEach((q, i) => {
      if ((topic.query_locked || [])[i]) s.add(q);
    });
    return s;
  }, [topic]);

  // 2026-05-28 — query -> 4-dimension scene tag map (for badges in the selected list).
  // Old queries without scene_type fall back to search in the backend; new candidates use candidateScenes. Priority: candidateScenes > topic.query_scene_types
  const sceneByText = useMemo(() => {
    const m = new Map<string, SceneType>();
    (topic.queries || []).forEach((q, i) => {
      const sc = (topic.query_scene_types || [])[i];
      if (sc) m.set(q, sc);
    });
    candidateScenes.forEach((sc, text) => { if (!m.has(text)) m.set(text, sc); });
    return m;
  }, [topic, candidateScenes]);

  const totalSelected = useMemo(
    () => Array.from(existing.values()).filter(Boolean).length,
    [existing],
  );

  const buildItems = (overrideText: string | null, overrideSel: boolean): {
    text: string; selected: boolean; seed?: string; scene_type?: SceneType;
  }[] => {
    return Array.from(existing.entries()).map(([k, v]) => {
      const item: { text: string; selected: boolean; seed?: string; scene_type?: SceneType } = {
        text: k,
        selected: k === overrideText ? overrideSel : v,
      };
      const candSeed = candidateSeeds.get(k);
      if (candSeed) item.seed = candSeed;
      const candScene = candidateScenes.get(k);
      if (candScene) item.scene_type = candScene;
      return item;
    });
  };

  const togglePick = (text: string) => {
    if (readOnly) return;
    // 2026-05-26 — locked queries (seed original text) are always selected and can't be unchecked
    if (lockedSet.has(text)) return;
    const cur = existing.get(text) ?? false;
    if (!cur && totalSelected >= MAX_SELECTED_QUERIES) {
      setErr(t('topic.profile.monitor.cap', { max: MAX_SELECTED_QUERIES }));
      return;
    }
    persistSelection(buildItems(text, !cur));
  };

  /** Soft delete / restore. Deleted queries no longer join batch runs, report metrics or publish query selection; the query and historical responses are kept. */
  const setDeleted = async (text: string, deleted: boolean) => {
    if (readOnly || busy) return;
    if (deleted) {
      const ok = await uiConfirm(
        t('topic.profile.monitor.deleteConfirmBody', { q: text })
          || `「${text}」将不再跑批、不进报告口径、也不再用于发文选词。`
             + '历史应答与已发文章都保留,随时可以恢复。'
             + '注意:近几天的报告数字会跟着变(趋势图会标出口径变更),'
             + '本主题正在进行中的智能选题动作也会作废。',
        {
          title: t('topic.profile.monitor.deleteConfirmTitle') || '不再监测这个问题?',
          okText: t('topic.profile.monitor.deleteConfirm') || '确认删除',
          danger: true,
        },
      );
      if (!ok) return;
    }
    setBusy(true); setErr(null);
    try {
      await topicProfileApi.deleteQueries(topic.id, [{ text, deleted }], token);
      onChange();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const persistSelection = async (
    items: { text: string; selected: boolean; seed?: string }[],
  ) => {
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

  // 2026-05-28 — SceneExpander triggers query expansion; nothing is persisted here, the output is just candidates,
  // and only after the user checks them in a SceneExpander tab do they go through pickFromScene to persistSelection.
  const handleExpand = async (seedText: string, scenes: SceneType[], countPerScene: number) => {
    setErr(null);
    const resp = await topicProfileApi.expandQueries(
      topic.id, seedText, { scenes, countPerScene }, token,
    );
    // Record each candidate's seed/scene membership, sent to the backend together on later persist
    setCandidateSeeds(prev => {
      const next = new Map(prev);
      const sceneMap = resp.scenes || {};
      for (const sc of scenes) {
        const qs = sceneMap[sc]?.queries || [];
        qs.forEach(q => { if (!next.has(q)) next.set(q, seedText); });
      }
      // Old schema fallback
      if (Object.keys(sceneMap).length === 0 && Array.isArray(resp.queries)) {
        resp.queries.forEach(q => { if (!next.has(q)) next.set(q, seedText); });
      }
      return next;
    });
    setCandidateScenes(prev => {
      const next = new Map(prev);
      const sceneMap = resp.scenes || {};
      for (const sc of scenes) {
        const qs = sceneMap[sc]?.queries || [];
        qs.forEach(q => { if (!next.has(q)) next.set(q, sc); });
      }
      return next;
    });
    return resp;
  };

  // The user checks a candidate in some scene tab -> add to existing + trigger persistence (default selected=true)
  const pickFromScene = (text: string, scene: SceneType, seedText: string) => {
    if (readOnly) return;
    const currentlySelected = existing.get(text) ?? false;
    // Capacity check (only new options never checked before need it)
    if (!currentlySelected && totalSelected >= MAX_SELECTED_QUERIES) {
      setErr(t('topic.profile.monitor.cap', { max: MAX_SELECTED_QUERIES }));
      return;
    }
    // Write this text into picked + the membership map, then persist
    setPickedTexts(prev => prev.has(text) ? prev : new Set(prev).add(text));
    setCandidateSeeds(prev => prev.has(text) ? prev : new Map(prev).set(text, seedText));
    setCandidateScenes(prev => prev.has(text) ? prev : new Map(prev).set(text, scene));
    // Toggle selection
    persistSelection(buildItems(text, !currentlySelected));
  };

  // Selected set, passed to SceneExpander so it echoes check states on candidates correctly
  const selectedSet = useMemo(() => {
    const s = new Set<string>();
    existing.forEach((sel, text) => { if (sel) s.add(text); });
    return s;
  }, [existing]);

  return (
    <div className="space-y-3">
      {/* ★ Deliberately **ignores** readOnly: that lock means "materials/queries are finalized and can't change",
          while approved (the normal running state) is exactly when toggling monitoring is most needed.
          Following that lock would make this switch clickable only on topics not yet live -- back to "can't find the entry". */}
      <MonitorRunPanel topicId={topic.id} token={token} readOnly={false} />

      <SceneExpander
        brandSceneAvailable={!!(topic.target && topic.target.trim())}
        onExpand={handleExpand}
        onPickCandidate={pickFromScene}
        selectedSet={selectedSet}
        readOnly={readOnly}
        products={topic.profile?.products}
        onAddProduct={async p => {
          // The seed is our own product but not registered in the materials -> write it into section 3 of the materials and save (the query expansion endpoint reads profile_json from the DB),
          // then the parent refreshes to get the new topic; clicking "Expand" again uses product mode
          setErr(null);
          try {
            const base = { ...EMPTY_BRAND_PROFILE, ...(topic.profile || {}) };
            await topicProfileApi.updateProfile(topic.id, { ...base, products: [...(base.products || []), p] }, token);
            onChange();
          } catch (e: unknown) {
            setErr(e instanceof Error ? e.message : String(e));
          }
        }}
      />

      {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted">
          {t('topic.profile.monitor.selectedHint', { n: totalSelected, max: MAX_SELECTED_QUERIES })}
        </p>
        {deletedSet.size > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
            <input type="checkbox" checked={showDeleted}
                   onChange={e => setShowDeleted(e.target.checked)}
                   style={{ accentColor: 'var(--accent-primary)' }} />
            {t('topic.profile.monitor.showDeleted', { n: deletedSet.size })
              || `含已删除(${deletedSet.size})`}
          </label>
        )}
      </div>

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
                   disabled={readOnly || busy || lockedSet.has(text)}
                   onChange={() => togglePick(text)}
                   className="mt-0.5"
                   style={{ accentColor: 'var(--accent-primary)' }} />
            <span className="text-sm text-primary flex-1">
              {lockedSet.has(text) && (
                <span
                  className="inline-flex items-center mr-1.5 px-1.5 py-0.5 rounded text-[9px] font-medium align-middle"
                  style={{ background: 'rgba(99,102,241,0.18)', color: '#6366f1' }}
                  title={t('topic.profile.monitor.seedLockedHint') || '种子原文 query,默认监测不可取消'}
                >
                  {t('topic.profile.monitor.seedBadge') || '种子'}
                </span>
              )}
              {(() => {
                const sc = sceneByText.get(text);
                if (!sc || sc === 'search') return null;  // search is the default; no badge, to reduce noise
                const colorMap: Record<SceneType, string> = {
                  search: '#6366f1', qa: '#10b981', intent: '#f59e0b', brand: '#ec4899',
                };
                const c = colorMap[sc] || '#6366f1';
                return (
                  <span
                    className="inline-flex items-center mr-1.5 px-1.5 py-0.5 rounded text-[9px] font-medium align-middle"
                    style={{ background: `${c}22`, color: c }}
                    title={t(`topic.profile.monitor.scene.${sc}.label`) as string}
                  >
                    {t(`topic.profile.monitor.scene.${sc}.label`)}
                  </span>
                );
              })()}
              {text}
            </span>
            {!readOnly && !lockedSet.has(text) && (
              <button type="button" disabled={busy}
                      onClick={e => { e.preventDefault(); void setDeleted(text, true); }}
                      className="text-[11px] px-1.5 py-0.5 rounded shrink-0 disabled:opacity-50"
                      style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
                      title={t('topic.profile.monitor.deleteHint')
                             || '不再监测这个问题(可恢复)'}>
                {t('common.delete') || '删除'}
              </button>
            )}
          </label>
        ))}
        {showDeleted && Array.from(deletedSet).map(text => (
          <div key={`del-${text}`}
               className="flex items-start gap-2 p-2 rounded-md"
               style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)',
                        opacity: busy ? 0.6 : 0.75 }}>
            <span className="text-sm flex-1" style={{ color: 'var(--text-secondary)',
                                                      textDecoration: 'line-through' }}>
              {text}
            </span>
            {!readOnly && (
              <button type="button" disabled={busy}
                      onClick={() => void setDeleted(text, false)}
                      className="text-[11px] px-1.5 py-0.5 rounded shrink-0 disabled:opacity-50"
                      style={{ color: 'var(--accent-primary)',
                               border: '1px solid var(--accent-primary)' }}>
                {t('topic.profile.monitor.restore') || '恢复'}
              </button>
            )}
          </div>
        ))}
        {existing.size === 0 && (
          <div className="text-xs text-muted py-8 text-center">{t('topic.profile.monitor.empty')}</div>
        )}
      </div>
    </div>
  );
}
