import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi, type Topic, type TopicPayload } from '../../services/aiTelemetryApi';
import { adminReviewApi } from '../../services/adminReviewApi';
import { TopicEditor } from '../Dashboard/AiTelemetry';

export function AdminAccountTopics() {
  const { userId } = useParams<{ userId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  // undefined = 列表;null = 新建;Topic = 编辑(admin 也用同一个 editor)
  // URL 带 ?new=1 时(从「客户中心」注册后跳过来)直接进入新建编辑器
  const [editing, setEditing] = useState<Topic | null | undefined>(
    searchParams.get('new') === '1' ? null : undefined,
  );
  // 保存成功后弹「审批报告」模态;关闭或点跳生成才消失
  const [savedTopic, setSavedTopic] = useState<Topic | null>(null);
  const [genBusy, setGenBusy] = useState(false);
  const [genErr, setGenErr] = useState<string | null>(null);
  // 模态打开后预查现有方案状态:有 ready 报告时按钮文案改成「查看」而不是「生成」,
  // 避免误导用户以为非要重新跑一次。
  const [hasExistingReport, setHasExistingReport] = useState(false);

  useEffect(() => {
    if (!savedTopic) { setHasExistingReport(false); return; }
    let cancelled = false;
    adminReviewApi.getStrategicSolution(savedTopic.id, token)
      .then(s => { if (!cancelled) setHasExistingReport(s.status === 'ready' || s.status === 'generating'); })
      .catch(() => { if (!cancelled) setHasExistingReport(false); });
    return () => { cancelled = true; };
  }, [savedTopic, token]);

  // 模态内一键进入体检报告 — 先查现存方案的 status:
  //   - ready / generating:复用,不再调 generate(不然会把已有报告冲掉成 generating)
  //   - idle / failed:才触发生成
  // website 取主题资料里填的(选填,留空时后端走 4 块降级)。
  const handleGenerateFromModal = async () => {
    if (!savedTopic || genBusy) return;
    setGenBusy(true); setGenErr(null);
    try {
      const id = savedTopic.id;
      const existing = await adminReviewApi.getStrategicSolution(id, token);
      const reuseStatuses: Array<typeof existing.status> = ['ready', 'generating'];
      if (!reuseStatuses.includes(existing.status)) {
        const website = (savedTopic.profile?.website || '').trim();
        await adminReviewApi.generateStrategicSolution(id, website, token);
      }
      setSavedTopic(null);
      navigate(`/workbench/topics/${id}/solution`);
    } catch (e) {
      setGenErr(e instanceof Error ? e.message : String(e));
    } finally {
      setGenBusy(false);
    }
  };

  // 进入新建模式后清掉 URL 参数,避免刷新页面又被自动打开
  useEffect(() => {
    if (searchParams.get('new') === '1') {
      const next = new URLSearchParams(searchParams);
      next.delete('new');
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

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
          onSaveDone={(saved) => {
            setEditing(undefined);
            reload();
            if (saved?.id) setSavedTopic(saved);
          }}
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

      {/* ── 审批报告弹窗:保存后展示,只放一个「生成 GEO 体检报告」按钮 ── */}
      {savedTopic && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
             style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(2px)' }}
             onClick={() => setSavedTopic(null)}>
          <div className="w-full max-w-md rounded-xl p-5 shadow-2xl"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-3 mb-4">
              <h2 className="text-base font-semibold text-primary">
                {t('workbench.adminAccountTopics.savedModal.title')}
              </h2>
              <button type="button" onClick={() => setSavedTopic(null)}
                      className="text-muted hover:text-primary text-lg leading-none"
                      aria-label="close">×</button>
            </div>

            <p className="text-xs text-secondary mb-3">
              {t('workbench.adminAccountTopics.savedModal.body')}
            </p>

            <dl className="text-xs space-y-1.5 mb-5 pl-2"
                style={{ borderLeft: '2px solid var(--accent-primary)' }}>
              <div className="flex gap-2 pl-2">
                <dt className="text-muted shrink-0 w-20">{t('workbench.adminAccountTopics.savedModal.colName')}</dt>
                <dd className="text-primary">{savedTopic.name || '—'}</dd>
              </div>
              <div className="flex gap-2 pl-2">
                <dt className="text-muted shrink-0 w-20">{t('workbench.adminAccountTopics.savedModal.colIndustry')}</dt>
                <dd className="text-secondary">{savedTopic.industry || '—'}</dd>
              </div>
              <div className="flex gap-2 pl-2">
                <dt className="text-muted shrink-0 w-20">{t('workbench.adminAccountTopics.savedModal.colQueries')}</dt>
                <dd className="text-secondary tabular-nums">
                  {t('workbench.adminAccountTopics.savedModal.queriesValue', {
                    selected: savedTopic.selected_query_count ?? savedTopic.queries.length,
                    total: savedTopic.queries.length,
                  })}
                </dd>
              </div>
              <div className="flex gap-2 pl-2">
                <dt className="text-muted shrink-0 w-20">{t('workbench.adminAccountTopics.savedModal.colWebsite')}</dt>
                <dd className="text-secondary truncate">{savedTopic.profile?.website || '—'}</dd>
              </div>
            </dl>

            {genErr && (
              <div className="mb-3 text-xs text-red-500">{genErr}</div>
            )}

            <div className="flex items-center justify-end gap-2">
              <button type="button" disabled={genBusy}
                      onClick={() => setSavedTopic(null)}
                      className="text-xs px-3 py-1.5 rounded-md"
                      style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
                {t('workbench.adminAccountTopics.savedModal.later')}
              </button>
              <button type="button" disabled={genBusy}
                      onClick={handleGenerateFromModal}
                      className="text-xs px-3 py-1.5 rounded-md text-white"
                      style={{ background: 'var(--accent-primary)', opacity: genBusy ? 0.5 : 1 }}>
                {genBusy
                  ? t('workbench.adminAccountTopics.savedModal.generating')
                  : `${t(hasExistingReport
                      ? 'workbench.adminAccountTopics.savedModal.viewReport'
                      : 'workbench.adminAccountTopics.savedModal.generateReport')} →`}
              </button>
            </div>

            <p className="text-[11px] text-muted mt-4 leading-relaxed">
              {t('workbench.adminAccountTopics.savedModal.nextHint')}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
