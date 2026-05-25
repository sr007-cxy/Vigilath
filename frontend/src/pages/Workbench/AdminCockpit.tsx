// 项目进度 — admin 主入口,一屏看完每个主题在 GEO 管线上的进度.
// 路由:/workbench/cockpit (workbench 默认页)
// 不按 pipeline stage 让 admin 一步步点 sidebar,而是把所有主题铺开 + 每个 stage 用 chip
// 显示状态(完成 / 进行中 / 待处理 / 异常 / 未启动),admin 直接点 chip 跳详情页.

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

      {loading ? (
        <div className="text-xs text-muted text-center py-10">{t('common.loading')}</div>
      ) : err ? (
        <div className="text-xs text-red-500">{err}</div>
      ) : rows.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">
          {t('workbench.adminCockpit.empty')}
        </div>
      ) : (
        <div className="rounded-lg overflow-hidden"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted">
                <th className="text-left px-3 py-2 whitespace-nowrap">{t('workbench.adminCockpit.colTopic')}</th>
                <th className="text-left px-3 py-2 whitespace-nowrap">{t('workbench.adminCockpit.colCustomer')}</th>
                {STAGE_ORDER.map((s, i) => (
                  <th key={s} className="px-2 py-2 text-center whitespace-nowrap">
                    <span className="text-muted">{i + 1}.</span>{' '}
                    {t(`workbench.adminCockpit.stage.${s}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(({ topic, stages }) => (
                <tr key={topic.topic_id} className="border-t"
                  style={{ borderColor: 'var(--border-color)' }}>
                  <td className="px-3 py-2 text-primary">
                    {topic.profile_name || topic.topic_name}
                    <div className="text-[10px] text-muted">#{topic.topic_id}</div>
                  </td>
                  <td className="px-3 py-2 text-secondary">
                    {topic.user_email}
                  </td>
                  {STAGE_ORDER.map(key => {
                    const { state, to } = stages[key];
                    const c = STATE_COLOR[state];
                    return (
                      <td key={key} className="px-2 py-2 text-center">
                        <Link to={to}
                          className="inline-block px-2 py-0.5 rounded-md text-[11px] border transition-colors"
                          style={{ color: c.c, background: c.bg, borderColor: c.border }}>
                          {t(`workbench.adminCockpit.stageStatus.${state}`)}
                        </Link>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
