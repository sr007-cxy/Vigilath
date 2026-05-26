// 项目进度 — admin 主入口,一屏看完每个主题在 GEO 管线上的进度.
// 路由:/workbench/cockpit (workbench 默认页)
// 形态:一行一个主题卡片,内嵌横向 6 节点 stepper(圆点 + 连线 + stage 名 + 描述),
//       节点按状态着色(完成 / 进行中 / 待处理 / 未启动),点节点跳对应详情页.

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { adminReviewApi, type StageState, type TopicReviewListItem } from '../../services/adminReviewApi';

type StageKey = 'submit' | 'review' | 'diagnose' | 'plan' | 'content' | 'insight';

const STAGE_ORDER: StageKey[] = ['submit', 'review', 'diagnose', 'plan', 'content', 'insight'];

const STATE_COLOR: Record<StageState, { c: string; bg: string; border: string }> = {
  done: { c: '#10b981', bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.35)' },
  running: { c: '#3b82f6', bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.35)' },
  pending: { c: '#eab308', bg: 'rgba(234,179,8,0.12)', border: 'rgba(234,179,8,0.35)' },
  blocked: { c: '#ef4444', bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)' },
  idle: { c: '#94a3b8', bg: 'rgba(148,163,184,0.10)', border: 'rgba(148,163,184,0.30)' },
};

function deriveStages(t: TopicReviewListItem): Record<StageKey, { state: StageState; to: string }> {
  const tid = t.topic_id;
  const sub = t.submission_status;
  // stage 1 submit:有 topic 就算 done.
  // stage 2 review:由 submission_status 推.
  // stage 3-6:后端 admin_review.py 已经按各自表的状态算好(idle/running/done/blocked/pending),
  //           前端直接读字段;后端缺字段时 Pydantic 会回退到 'idle'.
  const reviewRaw: StageState =
    sub === 'approved' ? 'done' :
      sub === 'rejected' ? 'blocked' :
        sub === 'pending' ? 'pending' : 'idle';

  // 工作流式管线 — backward infer:任意 stage 状态非 idle(done / running /
  // pending / blocked),意味着上游早已发生过,即便上游 DB 行缺失.把上游的
  // idle 推升为 done,避免「下游已完成、上游仍未启动」的错位.
  //
  // 这条规则替代了原来的「forward 门禁」(上游非 done → 下游强制 idle).
  // forward 门禁在老数据/缺失行场景下会把真实完成的下游硬塞成 idle —
  // 例如 程晓峰 topic_id=2:plan 行未生成、但 content 70 篇已 published、
  // telemetry success,plan 的「样本缺失」不应该把 content 拖成未启动.
  const rawByKey: Record<StageKey, StageState> = {
    submit: 'done',
    review: reviewRaw,
    diagnose: t.diagnose_status,
    plan: t.plan_status,
    content: t.content_status,
    insight: t.insight_status,
  };
  const effective: Record<StageKey, StageState> = { ...rawByKey };
  let downstreamProgressed = false;
  for (let i = STAGE_ORDER.length - 1; i >= 0; i--) {
    const key = STAGE_ORDER[i];
    if (downstreamProgressed && effective[key] === 'idle') {
      effective[key] = 'done';
    }
    if (effective[key] !== 'idle') downstreamProgressed = true;
  }
  // 用户偏好:项目进度看板不展示「异常」(red),blocked 一律按「进行中」(blue)呈现.
  // 真实失败状态查 stage 详情页(执行计划 / 审核 / 监测各页都会还原 raw status).
  for (const key of STAGE_ORDER) {
    if (effective[key] === 'blocked') effective[key] = 'running';
  }

  return {
    submit: { state: effective.submit, to: `/workbench/accounts/${t.user_id}/topics` },
    review: { state: effective.review, to: `/workbench/review` },
    diagnose: { state: effective.diagnose, to: `/workbench/topics/${tid}/solution` },
    plan: { state: effective.plan, to: `/workbench/topics/${tid}/execution-plan` },
    content: { state: effective.content, to: `/workbench/content-review?topic=${tid}` },
    // 效果查验与更新 = 原 crawl(运行结果)+ insight(反哺监测)合并;入口走监测看板,
    // 跑批结果可从 sidebar「跑批结果」单独进入
    insight: { state: effective.insight, to: `/workbench/insights` },
  };
}

export function AdminCockpit() {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [topics, setTopics] = useState<TopicReviewListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    adminReviewApi.listTopicReviews(token)
      .then(setTopics)
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token]);

  const rows = useMemo(
    () => topics.map(tp => ({ topic: tp, stages: deriveStages(tp) })),
    [topics],
  );

  // 待办计数(去审核流):
  //   - 未启动 = approved 但 plan 为 idle(admin 直建后未点「启动」)
  //   - 跑批待处理 = plan_status === 'blocked' (run failed) 或 'pending'
  //   - 待复审文案 = content_status === 'pending'
  const todoCounts = useMemo(() => {
    let notStarted = 0, runStuck = 0, docPending = 0;
    for (const tp of topics) {
      if (tp.submission_status === 'approved' && tp.plan_status === 'idle') notStarted++;
      if (tp.plan_status === 'blocked' || tp.plan_status === 'pending') runStuck++;
      if (tp.content_status === 'pending') docPending++;
    }
    return { notStarted, runStuck, docPending };
  }, [topics]);

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.adminCockpit.title" titleFallback="项目进度" />
      <header>
        <h1 className="text-xl font-semibold text-primary">
          {t('workbench.adminCockpit.heading')}
        </h1>
        <p className="text-xs text-secondary mt-0.5">
          {t('workbench.adminCockpit.subtitle')}
        </p>
      </header>

      {/* 待办区:3 张计数卡 — 点击跳到对应列表(带过滤) */}
      {!loading && !err && topics.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          <TodoCard count={todoCounts.notStarted}
                    label={t('workbench.adminCockpit.todo.notStarted')}
                    color="#3b82f6"
                    to="/workbench/accounts" />
          <TodoCard count={todoCounts.runStuck}
                    label={t('workbench.adminCockpit.todo.runStuck')}
                    color="#eab308"
                    to="/workbench/accounts" />
          <TodoCard count={todoCounts.docPending}
                    label={t('workbench.adminCockpit.todo.docPending')}
                    color="#10b981"
                    to="/workbench/content-review?status=pending_review" />
        </div>
      )}

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading')}</div>
      ) : err ? (
        <div className="text-xs text-red-500">{err}</div>
      ) : rows.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">
          {t('workbench.adminCockpit.empty')}
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map(({ topic, stages }) => (
            <PipelineCard key={topic.topic_id} topic={topic} stages={stages} />
          ))}
        </div>
      )}
    </div>
  );
}

function PipelineCard({
  topic,
  stages,
}: {
  topic: TopicReviewListItem;
  stages: Record<StageKey, { state: StageState; to: string }>;
}) {
  const { t } = useTranslation();
  return (
    <div className="rounded-lg p-4"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      {/* header */}
      <div className="flex items-baseline justify-between mb-4">
        <div className="text-sm font-medium text-primary">
          {topic.profile_name || topic.topic_name}
          <span className="text-[10px] text-muted ml-2">#{topic.topic_id}</span>
        </div>
        <div className="text-xs text-secondary">{topic.user_email}</div>
      </div>
      {/* stepper */}
      <div className="flex items-start">
        {STAGE_ORDER.map((key, i) => {
          const { state, to } = stages[key];
          const prev = i > 0 ? stages[STAGE_ORDER[i - 1]].state : null;
          const next = i < STAGE_ORDER.length - 1 ? stages[STAGE_ORDER[i + 1]].state : null;
          return (
            <PipelineNode
              key={key}
              stageKey={key}
              state={state}
              to={to}
              prevState={prev}
              nextState={next}
              isFirst={i === 0}
              isLast={i === STAGE_ORDER.length - 1}
              label={t(`workbench.adminCockpit.stage.${key}`)}
              desc={t(`workbench.adminCockpit.stageDesc.${key}`)}
              status={t(`workbench.adminCockpit.stageStatus.${state}`)}
            />
          );
        })}
      </div>
    </div>
  );
}

// 连线颜色:两端都 done → 实绿;否则灰.
function connectorColor(left: StageState | null, right: StageState | null): string {
  if (left === 'done' && right === 'done') return STATE_COLOR.done.c;
  return 'var(--border-color)';
}

function PipelineNode({
  stageKey,
  state,
  to,
  prevState,
  nextState,
  isFirst,
  isLast,
  label,
  desc,
  status,
}: {
  stageKey: StageKey;
  state: StageState;
  to: string;
  prevState: StageState | null;
  nextState: StageState | null;
  isFirst: boolean;
  isLast: boolean;
  label: string;
  desc: string;
  status: string;
}) {
  const c = STATE_COLOR[state];
  const isDone = state === 'done';
  const isIdle = state === 'idle';
  const leftLine = isFirst ? 'transparent' : connectorColor(prevState, state);
  const rightLine = isLast ? 'transparent' : connectorColor(state, nextState);
  return (
    <div className="flex-1 flex flex-col items-center min-w-0">
      <div className="w-full flex items-center">
        <div className="flex-1 h-0.5" style={{ background: leftLine }} />
        <Link to={to}
          title={status}
          className="flex items-center justify-center rounded-full border-2 transition-transform hover:scale-105"
          style={{
            width: 32,
            height: 32,
            color: c.c,
            background: isDone ? c.c : c.bg,
            borderColor: c.border,
          }}>
          {isDone ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white"
              strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          ) : (
            <span className="text-[11px] font-semibold tabular-nums"
              style={{ color: isIdle ? 'var(--text-muted)' : c.c }}>
              {STAGE_ORDER.indexOf(stageKey) + 1}
            </span>
          )}
        </Link>
        <div className="flex-1 h-0.5" style={{ background: rightLine }} />
      </div>
      <div className="mt-2 text-center px-1">
        <div className="text-[12px] font-medium text-primary leading-tight">{label}</div>
        <div className="text-[10px] text-muted mt-0.5 leading-tight">{desc}</div>
      </div>
    </div>
  );
}

function TodoCard({ count, label, color, to }: {
  count: number; label: string; color: string; to: string;
}) {
  const isEmpty = count === 0;
  return (
    <Link to={to}
          className="rounded-lg p-3 flex items-center justify-between transition-colors"
          style={{
            background: isEmpty ? 'var(--bg-card)' : `${color}15`,
            border: `1px solid ${isEmpty ? 'var(--border-color)' : `${color}40`}`,
            opacity: isEmpty ? 0.55 : 1,
          }}>
      <div>
        <div className="text-xs text-muted">{label}</div>
        <div className="text-2xl font-semibold tabular-nums" style={{ color: isEmpty ? 'var(--text-muted)' : color }}>
          {count}
        </div>
      </div>
      <div className="text-xs" style={{ color: isEmpty ? 'var(--text-muted)' : color }}>→</div>
    </Link>
  );
}
