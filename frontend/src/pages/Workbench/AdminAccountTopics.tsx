import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi, type Topic, type TopicPayload } from '../../services/aiTelemetryApi';
import { adminReviewApi, type StageState, type TopicReviewListItem } from '../../services/adminReviewApi';
import { TopicEditor } from '../Dashboard/AiTelemetry';

// 项目进度 6 段顺序,与 cockpit `AdminCockpit.tsx` 对齐.
type StageKey = 'submit' | 'review' | 'solution' | 'plan' | 'content' | 'insight';
const STAGE_ORDER: StageKey[] = ['submit', 'review', 'solution', 'plan', 'content', 'insight'];

// 把 cockpit 的 StageState 折算到本卡片 chip 调色板(无 pending,合并到 running).
function stageToChip(s: StageState): PipelineChipState {
  return s === 'done' ? 'done'
    : s === 'running' || s === 'pending' ? 'running'
    : s === 'blocked' ? 'failed'
    : 'idle';
}

// 复刻 cockpit 的 backward-infer:下游已 progress 时,上游 idle 推为 done.
function deriveStages(item: TopicReviewListItem | undefined): Record<StageKey, StageState> {
  const sub = item?.submission_status;
  const reviewRaw: StageState =
    sub === 'approved' ? 'done'
    : sub === 'rejected' ? 'blocked'
    : sub === 'pending' ? 'pending'
    : 'idle';
  const raw: Record<StageKey, StageState> = {
    submit: 'done',
    review: reviewRaw,
    solution: item?.diagnose_status ?? 'idle',
    plan: item?.plan_status ?? 'idle',
    content: item?.content_status ?? 'idle',
    insight: item?.insight_status ?? 'idle',
  };
  const eff = { ...raw };
  let progressed = false;
  for (let i = STAGE_ORDER.length - 1; i >= 0; i--) {
    const k = STAGE_ORDER[i];
    if (progressed && eff[k] === 'idle') eff[k] = 'done';
    if (eff[k] !== 'idle') progressed = true;
  }
  return eff;
}

export function AdminAccountTopics() {
  const { userId } = useParams<{ userId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  // 项目进度 6 段状态,从 admin 列表接口拿,按 user_id 过滤 + topic_id 索引.
  const [stagesByTid, setStagesByTid] = useState<Record<number, TopicReviewListItem>>({});
  // undefined = 列表;null = 新建;Topic = 编辑(admin 也用同一个 editor)
  // URL 带 ?new=1 时(从「画像」注册后跳过来)直接进入新建编辑器
  const [editing, setEditing] = useState<Topic | null | undefined>(
    searchParams.get('new') === '1' ? null : undefined,
  );
  // 启动按钮的 per-topic busy / error state
  const [startBusyId, setStartBusyId] = useState<number | null>(null);
  const [startErrByTopic, setStartErrByTopic] = useState<Record<number, string>>({});

  // 启动项目(首次) — 走新的 /admin/topics/{id}/start.
  // 「重启」入口在执行计划书页里(走 /start?force=true),不在卡片上.
  const handleStart = async (tp: Topic) => {
    if (startBusyId !== null) return;
    setStartBusyId(tp.id);
    setStartErrByTopic((prev) => { const n = { ...prev }; delete n[tp.id]; return n; });
    try {
      await adminReviewApi.startTopic(tp.id, token);
      // 启动后直接进「画像」主流程的「计划书」step,不再跳到独立的 execution-plan 详情页.
      navigate(`/workbench/topics/${tp.id}/edit?step=5`);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setStartErrByTopic((prev) => ({ ...prev, [tp.id]: msg }));
    } finally {
      setStartBusyId(null);
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

  // 客户名称:优先取首个 topic 的 target(= ai_telemetry_topics.target,客户填的品牌名),
  // 没有时 fallback 到「用户 #id」.AdminAccount 接口的 brand_target 走同一来源,这里不
  // 额外拉账号列表以节约请求.
  const customerName = topics[0]?.target || `用户 #${userId}`;

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
    // 项目进度 6 段状态拉一次,filter 本用户的,按 topic_id 索引;失败不影响主列表(chip 显示 idle).
    adminReviewApi.listTopicReviews(token)
      .then(items => {
        if (cancelled) return;
        const m: Record<number, TopicReviewListItem> = {};
        const uid = Number(userId);
        for (const it of items) {
          if (it.user_id === uid) m[it.topic_id] = it;
        }
        setStagesByTid(m);
      })
      .catch(() => { if (!cancelled) setStagesByTid({}); });
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
            {t('workbench.adminAccountTopics.title', { name: customerName })}
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
            // 不再关闭编辑器、不弹「审批报告」模态:TopicEditor 已自动进 step 4 内嵌健康报告,
            // 同步效果在 step 4「查看完整报告」/「重新生成」里;关闭由 step 4 的「完成」按钮触发.
            reload();
            // 让 editing 跟着 saved 同步,确保后续动作(再次编辑等)拿到新 id
            if (saved) setEditing(saved);
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
        {t('workbench.adminAccountTopics.title', { name: customerName })}
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
          {topics.map(tp => {
            // 「是否启动过」用 last_run_at 推断:approve_topic / start_topic / rerun 都会拿
            // run_id,有 last_run_at 说明至少触发过一次跑批.老 approved 主题(admin 直建,
            // 未跑过)走 last_run_at == null 分支,显示「启动项目」首发按钮.
            const hasRun = !!tp.last_run_at;
            const isStartBusy = startBusyId === tp.id;
            const startErr = startErrByTopic[tp.id];
            // 项目进度 6 段:走 cockpit 同源 deriveStages,缺数据时退到 idle.
            const stages = deriveStages(stagesByTid[tp.id]);
            return (
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

                {/* 项目进度 6 段 chip — 标签 / 顺序 / 状态与 cockpit 同源 */}
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] mt-2 mb-3">
                  {STAGE_ORDER.map(k => (
                    <PipelineChip key={k}
                      label={t(`workbench.adminCockpit.stage.${k}`)}
                      state={stageToChip(stages[k])} />
                  ))}
                </div>

                {startErr && (
                  <div className="mb-2 text-[11px] text-red-500 break-words">
                    {startErr}
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2 mt-3">
                  <button
                    type="button"
                    onClick={() => navigate(`/workbench/topics/${tp.id}/edit`)}
                    className="text-xs px-3 py-1 rounded"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
                  >
                    {t('workbench.adminAccountTopics.edit')}
                  </button>

                  {!hasRun && (
                    <button
                      type="button"
                      disabled={isStartBusy}
                      onClick={() => handleStart(tp)}
                      className="text-xs px-3 py-1 rounded text-white"
                      style={{ background: 'var(--accent-primary)', opacity: isStartBusy ? 0.5 : 1 }}
                    >
                      {isStartBusy ? '…' : t('workbench.adminAccountTopics.startProject')}
                    </button>
                  )}

                  {hasRun && (
                    <>
                      {/* 全部走「画像」主流程的对应 step,不再跳独立详情页 */}
                      <button type="button"
                        onClick={() => navigate(`/workbench/topics/${tp.id}/edit?step=4`)}
                        className="text-xs px-3 py-1 rounded"
                        style={{ background: 'var(--accent-primary)', color: 'white' }}>
                        {t('workbench.adminAccountTopics.viewSolution')}
                      </button>
                      <button type="button"
                        onClick={() => navigate(`/workbench/topics/${tp.id}/edit?step=5`)}
                        className="text-xs px-3 py-1 rounded"
                        style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
                        {t('workbench.adminAccountTopics.viewPlan')}
                      </button>
                      <button type="button"
                        onClick={() => navigate(`/workbench/topics/${tp.id}/edit?step=6`)}
                        className="text-xs px-3 py-1 rounded"
                        style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
                        {t('workbench.adminAccountTopics.reviewDocs')}
                      </button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

    </div>
  );
}

// 管线状态 chip — 5 步骤共用,被动展示.
// idle = 未开始(灰)、running = 进行中(黄)、done = 完成(绿)、failed = 失败(红).
type PipelineChipState = 'idle' | 'running' | 'done' | 'failed';

function PipelineChip({ label, state }: { label: string; state: PipelineChipState }) {
  const palette: Record<PipelineChipState, { fg: string; bg: string; mark: string }> = {
    done:    { fg: '#10b981', bg: 'rgba(16,185,129,0.10)', mark: '✓' },
    running: { fg: '#eab308', bg: 'rgba(234,179,8,0.10)',  mark: '⋯' },
    failed:  { fg: '#ef4444', bg: 'rgba(239,68,68,0.10)',  mark: '✗' },
    idle:    { fg: 'var(--text-muted)', bg: 'var(--bg-tertiary)', mark: '─' },
  };
  const p = palette[state];
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full"
          style={{ background: p.bg, color: p.fg }}>
      <span className="font-semibold">{p.mark}</span>
      <span>{label}</span>
    </span>
  );
}
