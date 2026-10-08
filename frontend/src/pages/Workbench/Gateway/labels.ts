// Shared metric definitions for the gateway pages: localized labels and palette for tiers / purposes / task statuses.
// Kept in a pure ts file so the tenant table, log table and create modal share one source instead of each writing their own.
import type { TFunction } from 'i18next';
import type { BadgeTone } from '../../../components/ui';

export const JOB_STATUSES = ['queued', 'claimed', 'done', 'failed'] as const;

export function tierLabel(t: TFunction, x: string): string {
  return x === 'pro'
    ? t('workbench.adminGateway.tierPro', { defaultValue: '专业' })
    : t('workbench.adminGateway.tierFree', { defaultValue: '免费' });
}

export function kindLabel(t: TFunction, x?: string): string {
  if (x === 'internal') return t('workbench.adminGateway.kindInternal', { defaultValue: '自有系统' });
  if (x === 'test') return t('workbench.adminGateway.kindTest', { defaultValue: '联调测试' });
  return t('workbench.adminGateway.kindCustomer', { defaultValue: '对外客户' });
}

/** Purpose -> Badge color. Used to be three hardcoded hex values (#6366f1 / #f59e0b / default); now uses the semantic palette. */
export function kindTone(x?: string): BadgeTone {
  if (x === 'internal') return 'info';
  if (x === 'test') return 'warn';
  return 'neutral';
}

export const KIND_OPTIONS = (t: TFunction) => [
  { value: 'customer', label: kindLabel(t, 'customer') },
  { value: 'internal', label: kindLabel(t, 'internal') },
  { value: 'test', label: kindLabel(t, 'test') },
];

export function jobStatusLabel(t: TFunction, s: string): string {
  switch (s) {
    case 'queued': return t('workbench.adminGateway.jobQueued', { defaultValue: '排队' });
    case 'claimed': return t('workbench.adminGateway.jobClaimed', { defaultValue: '执行中' });
    case 'done': return t('workbench.adminGateway.jobDone', { defaultValue: '完成' });
    case 'failed': return t('workbench.adminGateway.jobFailed', { defaultValue: '失败' });
    default: return s;
  }
}

export function jobStatusTone(s: string): BadgeTone {
  return s === 'done' ? 'success' : s === 'failed' ? 'danger' : s === 'claimed' ? 'info' : 'neutral';
}
