// TopicStepper — 项目详情页共享的「5 步管线」导航条.
// 用法:任何 /workbench/topics/:topicId/* 的页面在顶部 render 一个 <TopicStepper topicId={tid} active="plan" />
// 5 步:① 画像 ② 监测问题 ③ 计划书 ④ 文案 ⑤ 投放
// 状态符号:✓ done / ⋯ running / ─ idle / ✗ failed
//
// 设计文档:docs/no-audit-flow-design.md §4.4

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { adminReviewApi, type TopicReviewDetail } from '../services/adminReviewApi';

export type StepKey = 'profile' | 'queries' | 'plan' | 'docs' | 'publish';
export type StepStatus = 'idle' | 'running' | 'done' | 'failed';

interface Props {
  topicId: number;
  active: StepKey;
}

interface StepDef {
  key: StepKey;
  label: string;
  status: StepStatus;
  hint?: string;     // 副标题(进度数字等)
  onClick: () => void;
}

export function TopicStepper({ topicId, active }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const token = localStorage.getItem('token') || '';
  const [topic, setTopic] = useState<TopicReviewDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!topicId) return;
    let cancelled = false;
    adminReviewApi.getTopicReview(topicId, token)
      .then((d) => { if (!cancelled) setTopic(d); })
      .catch((e) => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [topicId, token]);

  // 各步状态推断 — 文案 / 投放 的精确数字需要 ContentReview API 聚合,首期先用粗略态.
  const profileOk = !!(topic?.profile?.company_short_name);
  const queriesOk = (topic?.queries?.length ?? 0) > 0
                    && ((topic?.selected_query_count ?? 0) > 0
                        || (topic?.query_selected?.some(Boolean) ?? false));
  const runChip: StepStatus = topic?.last_run_status === 'success' ? 'done'
                            : topic?.last_run_status === 'running' ? 'running'
                            : topic?.last_run_status === 'failed' ? 'failed'
                            : 'idle';

  const steps: StepDef[] = [
    {
      key: 'profile', label: t('workbench.topicStepper.profile'),
      status: profileOk ? 'done' : 'idle',
      onClick: () => topic && navigate(`/workbench/accounts/${topic.user_id}/topics?edit=${topicId}`),
    },
    {
      key: 'queries', label: t('workbench.topicStepper.queries'),
      status: queriesOk ? 'done' : 'idle',
      hint: queriesOk ? String(topic?.selected_query_count ?? 0) : undefined,
      onClick: () => topic && navigate(`/workbench/accounts/${topic.user_id}/topics?edit=${topicId}`),
    },
    {
      key: 'plan', label: t('workbench.topicStepper.plan'),
      status: runChip,
      onClick: () => navigate(`/workbench/topics/${topicId}/execution-plan`),
    },
    {
      key: 'docs', label: t('workbench.topicStepper.docs'),
      status: 'idle',
      onClick: () => navigate(`/workbench/content-review?topic=${topicId}`),
    },
    {
      key: 'publish', label: t('workbench.topicStepper.publish'),
      status: 'idle',
      onClick: () => navigate(`/workbench/content-review?topic=${topicId}&status=published`),
    },
  ];

  return (
    <div className="rounded-md p-4 mb-4" style={{
      background: 'var(--bg-card)', border: '1px solid var(--border-color)',
    }}>
      {/* 头部:面包屑 + 辅助按钮 */}
      <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
        <div className="text-xs">
          {topic && (
            <span className="text-muted">
              <button type="button"
                      onClick={() => navigate(`/workbench/accounts/${topic.user_id}/topics`)}
                      className="hover:underline">
                ← {topic.user_email || `用户 #${topic.user_id}`}
              </button>
              <span className="mx-1">/</span>
              <span className="text-primary font-medium">{topic.name}</span>
            </span>
          )}
          {err && <span className="text-red-500">{err}</span>}
        </div>
        <div className="flex items-center gap-2">
          <button type="button"
                  onClick={() => navigate(`/workbench/topics/${topicId}/solution`)}
                  className="text-xs px-2.5 py-1 rounded"
                  style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
            🩺 {t('workbench.topicStepper.aux.healthReport')}
          </button>
        </div>
      </div>

      {/* Stepper bar */}
      <div className="flex items-stretch gap-1 overflow-x-auto scrollbar-hide">
        {steps.map((s, i) => (
          <StepCell key={s.key}
                    step={s}
                    active={s.key === active}
                    isLast={i === steps.length - 1} />
        ))}
      </div>
    </div>
  );
}

const STATUS_PALETTE: Record<StepStatus, { fg: string; bg: string; line: string; mark: string }> = {
  done:    { fg: '#10b981', bg: 'rgba(16,185,129,0.10)', line: '#10b981', mark: '✓' },
  running: { fg: '#eab308', bg: 'rgba(234,179,8,0.10)',  line: '#eab308', mark: '⋯' },
  failed:  { fg: '#ef4444', bg: 'rgba(239,68,68,0.10)',  line: '#ef4444', mark: '✗' },
  idle:    { fg: 'var(--text-muted)', bg: 'var(--bg-tertiary)', line: 'var(--border-color)', mark: '─' },
};

function StepCell({ step, active, isLast }: { step: StepDef; active: boolean; isLast: boolean }) {
  const p = STATUS_PALETTE[step.status];
  return (
    <>
      <button type="button" onClick={step.onClick}
              className="flex-1 min-w-[100px] flex flex-col items-center gap-1 px-2 py-2 rounded transition-colors"
              style={{
                background: active ? p.bg : 'transparent',
                border: active ? `1px solid ${p.line}` : '1px solid transparent',
                cursor: 'pointer',
              }}>
        <div className="flex items-center justify-center w-6 h-6 rounded-full font-semibold text-xs"
             style={{ background: p.bg, color: p.fg, border: `1px solid ${p.line}` }}>
          {p.mark}
        </div>
        <div className="text-xs font-medium" style={{ color: active ? p.fg : 'var(--text-secondary)' }}>
          {step.label}
        </div>
        {step.hint && (
          <div className="text-[10px] text-muted">{step.hint}</div>
        )}
      </button>
      {!isLast && (
        <div className="flex items-center px-0.5">
          <div className="w-3 h-px" style={{ background: 'var(--border-color)' }} />
        </div>
      )}
    </>
  );
}
