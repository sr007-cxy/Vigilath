import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { DraftVariant } from '../../../../types/sentiment';

interface DraftEntry {
  variant: DraftVariant;
  body: string;
  rationale: string | null;
  predicted_effect: string | null;
  cautions: string | null;
}

interface DraftBundle {
  id: number;
  symbol: string;
  source: string | null;
  post_id: string | null;
  topic: string | null;
  title: string;
  summary: string;
  recommendation: DraftVariant;
  hitl_required: boolean;
  hitl_notes: string;
  drafts: DraftEntry[];
  generated_at: string;
  model: string;
  status: 'pending_review' | 'approved' | 'archived';
}

interface Props {
  draft: DraftBundle;
}

const VARIANT_COLOR: Record<DraftVariant, { bg: string; fg: string }> = {
  conservative: { bg: 'rgba(22,163,74,0.10)', fg: '#16a34a' },
  standard:     { bg: 'rgba(251,191,36,0.18)', fg: '#b45309' },
  proactive:    { bg: 'rgba(59,130,246,0.12)', fg: '#1d4ed8' },
};

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function DraftCard({ draft }: Props) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<DraftVariant | null>(null);
  const [edits, setEdits] = useState<Record<DraftVariant, string>>({
    conservative: draft.drafts.find(d => d.variant === 'conservative')?.body || '',
    standard:     draft.drafts.find(d => d.variant === 'standard')?.body || '',
    proactive:    draft.drafts.find(d => d.variant === 'proactive')?.body || '',
  });
  const [copied, setCopied] = useState<DraftVariant | null>(null);

  const handleCopy = (variant: DraftVariant, body: string) => {
    navigator.clipboard?.writeText(body).catch(() => {});
    setCopied(variant);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="space-y-4">
      {/* 顶栏:推荐 + HITL */}
      <header
        className="rounded-xl p-4"
        style={{
          background: draft.hitl_required ? 'rgba(251,191,36,0.10)' : 'var(--bg-card)',
          border: `1px solid ${draft.hitl_required ? 'rgba(251,191,36,0.35)' : 'var(--border-color)'}`,
        }}
      >
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-base font-bold text-primary">{draft.title}</h2>
          <span
            className="px-2 py-0.5 rounded-md text-xs font-semibold"
            style={{
              background: VARIANT_COLOR[draft.recommendation].bg,
              color: VARIANT_COLOR[draft.recommendation].fg,
            }}
          >
            ⭐ {t('dashboard.sentiment.drafts.recommended')}: {t(`dashboard.sentiment.drafts.variants.${draft.recommendation}`)}
          </span>
          {draft.hitl_required && (
            <span className="text-xs font-semibold" style={{ color: '#b45309' }}>
              {t('dashboard.sentiment.drafts.hitlRequired')}
            </span>
          )}
        </div>
        {draft.summary && (
          <p className="text-sm text-secondary mt-2">
            <strong>{t('dashboard.sentiment.drafts.summary')}:</strong> {draft.summary}
          </p>
        )}
        {draft.hitl_required && draft.hitl_notes && (
          <p className="text-xs text-secondary mt-2">
            <strong>{t('dashboard.sentiment.drafts.hitlNotes')}:</strong> {draft.hitl_notes}
          </p>
        )}
      </header>

      {/* 三档并列 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {draft.drafts.map((d) => {
          const c = VARIANT_COLOR[d.variant];
          const isRecommend = d.variant === draft.recommendation;
          const isEditing = editing === d.variant;
          return (
            <article
              key={d.variant}
              className="rounded-xl p-4 flex flex-col gap-2"
              style={{
                ...cardStyle,
                outline: isRecommend ? `2px solid ${c.fg}` : 'none',
                outlineOffset: -2,
              }}
            >
              <header className="flex items-center justify-between">
                <span
                  className="px-2 py-0.5 rounded-md text-xs font-bold"
                  style={{ background: c.bg, color: c.fg }}
                >
                  {t(`dashboard.sentiment.drafts.variants.${d.variant}`)}
                </span>
                {isRecommend && <span className="text-xs">⭐</span>}
              </header>

              {isEditing ? (
                <textarea
                  className="text-sm leading-relaxed rounded p-2 min-h-32 resize-y"
                  style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-color)',
                    color: 'var(--text-primary)',
                  }}
                  value={edits[d.variant]}
                  onChange={(e) => setEdits((p) => ({ ...p, [d.variant]: e.target.value }))}
                />
              ) : (
                <p className="text-sm text-primary whitespace-pre-wrap leading-relaxed">
                  {edits[d.variant]}
                </p>
              )}

              <details className="text-xs">
                <summary className="cursor-pointer text-muted">
                  {t('dashboard.sentiment.drafts.fields.rationale')} / {t('dashboard.sentiment.drafts.fields.predicted_effect')} / {t('dashboard.sentiment.drafts.fields.cautions')}
                </summary>
                <dl className="mt-2 space-y-2 text-xs">
                  <div>
                    <dt className="text-muted font-semibold">{t('dashboard.sentiment.drafts.fields.rationale')}</dt>
                    <dd className="text-secondary mt-0.5">{d.rationale}</dd>
                  </div>
                  <div>
                    <dt className="text-muted font-semibold">{t('dashboard.sentiment.drafts.fields.predicted_effect')}</dt>
                    <dd className="text-secondary mt-0.5">{d.predicted_effect}</dd>
                  </div>
                  <div>
                    <dt className="text-muted font-semibold">{t('dashboard.sentiment.drafts.fields.cautions')}</dt>
                    <dd className="text-secondary mt-0.5">{d.cautions}</dd>
                  </div>
                </dl>
              </details>

              <div className="flex items-center gap-2 mt-auto pt-2" style={{ borderTop: '1px dashed var(--border-color)' }}>
                <button
                  type="button"
                  onClick={() => handleCopy(d.variant, edits[d.variant])}
                  className="text-xs font-semibold px-2.5 py-1 rounded"
                  style={{ color: c.fg, background: c.bg }}
                >
                  {copied === d.variant ? t('dashboard.sentiment.drafts.copied') : t('dashboard.sentiment.drafts.copy')}
                </button>
                {isEditing ? (
                  <button
                    type="button"
                    onClick={() => setEditing(null)}
                    className="text-xs font-semibold px-2.5 py-1 rounded"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
                  >
                    {t('dashboard.sentiment.drafts.save')}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => setEditing(d.variant)}
                    className="text-xs font-semibold px-2.5 py-1 rounded"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
                  >
                    {t('dashboard.sentiment.drafts.edit')}
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
