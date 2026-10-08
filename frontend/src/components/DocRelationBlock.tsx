// Article detail "Creation relations" block -- which seed prompt, which template, and which prompts this draft was scheduled to cover.
// All data is injected by the backend plan_link from the publish plan snapshot; user-supplied drafts have only a few items, and the block isn't rendered if there are none.
// Shared by the user side (Dashboard/Content) and the review console (Admin/ContentReview); the doc types on both sides are structurally compatible.
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface DocRelationFields {
  source_query_text?: string;
  template_id?: number | null;
  template_name?: string | null;
  template_kind?: string | null;
  plan_seed?: string | null;
  plan_queries?: string[];
  plan_platform?: string | null;
  plan_seq?: number | null;
  plan_publish_date?: string | null;
}

export function DocRelationBlock({ doc }: { doc: DocRelationFields }) {
  const { t } = useTranslation();
  const seed = doc.plan_seed || doc.source_query_text || '';
  // If the seed text got mixed into the covered prompts, dedupe (the seed is already shown on its own line)
  const queries = (doc.plan_queries || []).filter(q => q && q !== seed);
  const hasTemplate = !!(doc.template_name || doc.template_id);
  const hasPlan = doc.plan_seq != null;
  if (!seed && !queries.length && !hasTemplate && !hasPlan && !doc.plan_platform) return null;

  const row = (label: string, node: ReactNode) => (
    <div className="flex gap-2 items-start">
      <span className="text-[11px] text-muted shrink-0 w-16 pt-0.5">{label}</span>
      <div className="flex-1 min-w-0 text-xs text-primary">{node}</div>
    </div>
  );

  return (
    <div className="rounded-md p-3 space-y-2"
         style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
      <div className="text-[11px] font-semibold text-secondary">
        {t('dashboard.content.relationTitle', '创作关联')}
      </div>

      {seed && row(
        t('dashboard.content.relationSeed', '种子提示词'),
        <span className="px-1.5 py-0.5 rounded"
              style={{ background: 'rgba(139,92,246,0.12)', color: '#8b5cf6' }}>{seed}</span>,
      )}

      {queries.length > 0 && row(
        t('dashboard.content.relationQueries', '提示词'),
        <div className="flex flex-wrap gap-1">
          {queries.map((q, i) => (
            <span key={i} className="px-1.5 py-0.5 rounded"
                  title={t('dashboard.content.relationQueriesHint',
                           '生成时要求正文自然覆盖这些问题')}
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
              {q}
            </span>
          ))}
        </div>,
      )}

      {hasTemplate && row(
        t('dashboard.content.relationTemplate', '内容模板'),
        <span className="px-1.5 py-0.5 rounded"
              style={{ background: 'rgba(59,130,246,0.12)', color: '#3b82f6' }}>
          {doc.template_name || `#${doc.template_id}`}
          {doc.template_kind ? ` · ${doc.template_kind}` : ''}
        </span>,
      )}

      {(hasPlan || doc.plan_platform) && row(
        t('dashboard.content.relationPlan', '发文计划'),
        <span className="text-secondary">
          {hasPlan && `#${doc.plan_seq}`}
          {doc.plan_publish_date && ` · ${doc.plan_publish_date.slice(0, 10)}`}
          {doc.plan_platform && ` · ${doc.plan_platform}`}
        </span>,
      )}
    </div>
  );
}
