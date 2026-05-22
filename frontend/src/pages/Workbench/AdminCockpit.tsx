// 操盘台 — admin 主入口,一屏看完每个主题在 GEO 管线上的进度.
// 路由:/workbench/cockpit (workbench 默认页)
// 不按 pipeline stage 让 admin 一步步点 sidebar,而是把所有主题铺开 + 每个 stage 用 chip
// 显示状态(完成 / 进行中 / 待处理 / 异常 / 未启动),admin 直接点 chip 跳详情页.

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../../components/PageHead';
import { adminReviewApi, type TopicReviewListItem } from '../../services/adminReviewApi';

type StageKey = 'submit' | 'review' | 'diagnose' | 'plan' | 'content' | 'insight';
type StageState = 'done' | 'running' | 'pending' | 'blocked' | 'idle';

const STAGE_ORDER: StageKey[] = ['submit', 'review', 'diagnose', 'plan', 'content', 'insight'];

const STATE_COLOR: Record<StageState, { c: string; bg: string; border: string }> = {
  done:    { c: '#10b981', bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.35)' },
  running: { c: '#3b82f6', bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.35)' },
  pending: { c: '#eab308', bg: 'rgba(234,179,8,0.12)',  border: 'rgba(234,179,8,0.35)'  },
  blocked: { c: '#ef4444', bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)'  },
  idle:    { c: '#94a3b8', bg: 'rgba(148,163,184,0.10)', border: 'rgba(148,163,184,0.30)' },
};

function deriveStages(t: TopicReviewListItem): Record<StageKey, { state: StageState; to: string }> {
  const tid = t.topic_id;
  const sub = t.submission_status;
  // submit = 品牌与主题创建(只要有 topic 就算 done)
  const submit: StageState = 'done';
  // review = 诊断与方案预评估,pending/rejected/approved
  const review: StageState =
    sub === 'approved' ? 'done' :
    sub === 'rejected' ? 'blocked' :
    sub === 'pending'  ? 'pending' : 'idle';
  // 审批没过就不进入后续 stage
  const approved = sub === 'approved';
  // 健康度诊断报告 / 执行策略与规划 / 内容发布与审核 / 效果查验与更新:批准后才"可启动",
  // 这里没拉详细状态,全部标 idle(可点入查看),后续如果后端给字段再升级
  const after: StageState = approved ? 'idle' : 'idle';
  return {
    submit:   { state: submit,  to: `/workbench/accounts/${t.user_id}/topics` },
    review:   { state: review,  to: `/workbench/review` },
    diagnose: { state: after,   to: `/workbench/topics/${tid}/solution` },
    plan:     { state: after,   to: `/workbench/topics/${tid}/execution-plan` },
    content:  { state: after,   to: `/workbench/content-review?topic=${tid}` },
    // 效果查验与更新 = 原 crawl(运行结果)+ insight(反哺监测)合并;入口走监测看板,
    // 跑批结果可从 sidebar「跑批结果」单独进入
    insight:  { state: after,   to: `/workbench/insights` },
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
      <PageHead titleKey="workbench.adminCockpit.title" titleFallback="操盘台" />
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
