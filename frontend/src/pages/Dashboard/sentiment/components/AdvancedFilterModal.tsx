// Advanced filter modal — follows the structure of the WisersOne prototype image.png.
//
// Layout:
//   - Left main area (grow): filter rows, each row = label + "All N" chip + multiple checkbox options
//                  groups separated by dashed lines
//                  Group 1: media type / media category / popular media
//                  Group 2: full-text sentiment / risk level
//   - Right sidebar (280px): three inputs, keyword / start time / end time
//   - Bottom: three buttons, reset / cancel / save (blue)
//
// The "All" chip = clear that dimension (it is "active" when no specific option is checked).
// Everything in the modal uses draft state, committed only on confirm; closing unmounts it and the draft is naturally discarded.
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import {
  MEDIA_TYPE_ORDER, INDUSTRY_ORDER,
} from '../../../../constants/sentimentPlatforms';
import type {
  SentimentPlatform, RiskLevel, SentimentLabel,
} from '../../../../types/sentiment';
import { DateRangeFilter } from '../../../../components/ui/FilterBar';
import { fromQueryRange, toQueryRange } from '../../../../components/ui/dateFilter';

const ALL_RISKS: RiskLevel[] = ['none', 'low', 'medium', 'high'];
const ALL_SENTIMENTS: SentimentLabel[] = ['bullish', 'bearish', 'neutral', 'mixed', 'unknown'];

// Fixed platform order for the popular media section (same as ArticlesTab)
const POPULAR_PLATFORMS: readonly string[] = [
  'weibo', 'weixin', 'xueqiu',
  'eastmoney', 'zhihu', 'toutiao',
  'bilibili', 'douyin', 'xiaohongshu',
];

export interface AdvancedFilterValue {
  risks: Set<string>;
  sentiments: Set<string>;
  mediaTypes: Set<string>;
  industries: Set<string>;
  sources: Set<string>;
  /** Keyword mention scope: '' = all, 'title' = title only, 'body' = body only */
  keywordScope: '' | 'title' | 'body';
  /** Author region: '' = all, 'domestic' = domestic, 'overseas' = overseas (placeholder, awaiting backend data) */
  authorRegion: '' | 'domestic' | 'overseas';
  /** Article type: '' = all, 'original' = original, 'reprint' = reprint (placeholder, awaiting backend data) */
  articleType: '' | 'original' | 'reprint';
  topic: string;
  /** Specified media names, comma-separated (placeholder; not used for filtering while the backend lacks the field) */
  specificMedia: string;
  /** Section name (placeholder) */
  column: string;
  /** Specified author name (active: substring match on post.author) */
  authorInput: string;
  startDate: string;  // 'YYYY-MM-DD', empty means unbounded
  endDate: string;
}

interface Props {
  value: AdvancedFilterValue;
  platforms: SentimentPlatform[];
  axisCounts: { mediaType: Map<string, number>; industry: Map<string, number> };
  sourceCounts: Map<string, number>;
  totalCount: number;
  platformLabel: (code: string) => string;
  onSave: (v: AdvancedFilterValue) => void;
  onCancel: () => void;
  onReset: () => void;
}

function toggle<T>(s: Set<T>, v: T): Set<T> {
  const n = new Set(s);
  if (n.has(v)) n.delete(v); else n.add(v);
  return n;
}

export function AdvancedFilterModal({
  value, platforms, axisCounts, sourceCounts, totalCount,
  platformLabel, onSave, onCancel, onReset,
}: Props) {
  const { t } = useTranslation();

  // draft state — initialized from props on mount, discarded on unmount
  const [draftRisks, setDraftRisks] = useState<Set<string>>(() => new Set(value.risks));
  const [draftSent, setDraftSent] = useState<Set<string>>(() => new Set(value.sentiments));
  const [draftSrc, setDraftSrc] = useState<Set<string>>(() => new Set(value.sources));
  const [draftInd, setDraftInd] = useState<Set<string>>(() => new Set(value.industries));
  const [draftMt, setDraftMt] = useState<Set<string>>(() => new Set(value.mediaTypes));
  const [draftTopic, setDraftTopic] = useState<string>(value.topic);
  const [draftKwScope, setDraftKwScope] = useState<'' | 'title' | 'body'>(value.keywordScope);
  const [draftAuthorRegion, setDraftAuthorRegion] = useState<'' | 'domestic' | 'overseas'>(value.authorRegion);
  const [draftArticleType, setDraftArticleType] = useState<'' | 'original' | 'reprint'>(value.articleType);
  const [draftSpecificMedia, setDraftSpecificMedia] = useState<string>(value.specificMedia);
  const [draftColumn, setDraftColumn] = useState<string>(value.column);
  const [draftAuthorInput, setDraftAuthorInput] = useState<string>(value.authorInput);
  const [draftStart, setDraftStart] = useState<string>(value.startDate);
  const [draftEnd, setDraftEnd] = useState<string>(value.endDate);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const startEndError = draftStart && draftEnd && draftStart > draftEnd;

  const handleSave = () => {
    if (startEndError) return;
    onSave({
      risks: draftRisks,
      sentiments: draftSent,
      mediaTypes: draftMt,
      industries: draftInd,
      sources: draftSrc,
      topic: draftTopic,
      keywordScope: draftKwScope,
      authorRegion: draftAuthorRegion,
      articleType: draftArticleType,
      specificMedia: draftSpecificMedia,
      column: draftColumn,
      authorInput: draftAuthorInput,
      startDate: draftStart,
      endDate: draftEnd,
    });
  };

  const handleResetDraft = () => {
    // Local reset — clear all drafts; don't commit to the parent directly (the user can still edit before saving)
    setDraftRisks(new Set());
    setDraftSent(new Set());
    setDraftSrc(new Set());
    setDraftInd(new Set());
    setDraftMt(new Set());
    setDraftTopic('');
    setDraftKwScope('');
    setDraftAuthorRegion('');
    setDraftArticleType('');
    setDraftSpecificMedia('');
    setDraftColumn('');
    setDraftAuthorInput('');
    setDraftStart('');
    setDraftEnd('');
    // Also call the parent reset to zero the rail state, avoiding a partial reset on save
    onReset();
  };

  const node = (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        className="rounded-xl shadow-2xl w-full max-w-6xl max-h-[88vh] flex flex-col"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >
        <header className="px-5 py-3 flex items-center justify-between"
          style={{ borderBottom: '1px solid var(--border-color)' }}>
          <h3 className="text-sm font-semibold text-primary">
            {t('dashboard.sentiment.articles.filters.advancedTitle')}
          </h3>
          <button type="button" onClick={onCancel}
            className="text-muted hover:text-primary text-lg leading-none px-2">
            ×
          </button>
        </header>

        {/* Body: left filter rows (grow) + right 280px input column */}
        <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_280px] gap-6 px-5 py-4 overflow-y-auto">

          {/* ── Left: filter rows ─────────────────────────────────── */}
          <div className="space-y-3">
            {/* Group 1: media dimensions */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.mediaType')}>
              <AllOption checked={!draftMt.size} count={totalCount}
                onClick={() => setDraftMt(new Set())} t={t} />
              {MEDIA_TYPE_ORDER.map(mt => {
                const c = axisCounts.mediaType.get(mt) ?? 0;
                const lk = `dashboard.sentiment.articles.mediaTypes.${mt}`;
                const lv = t(lk);
                return (
                  <CheckOption key={mt}
                    label={lv === lk ? mt : lv}
                    count={c}
                    checked={draftMt.has(mt)}
                    onChange={() => setDraftMt(toggle(draftMt, mt))} />
                );
              })}
            </FilterRow>

            <FilterRow label={t('dashboard.sentiment.articles.filters.industry')}>
              <AllOption checked={!draftInd.size} count={totalCount}
                onClick={() => setDraftInd(new Set())} t={t} />
              {INDUSTRY_ORDER.map(ind => {
                const c = axisCounts.industry.get(ind) ?? 0;
                return (
                  <CheckOption key={ind}
                    label={t(`dashboard.sentiment.articles.industries.${ind}`)}
                    count={c}
                    checked={draftInd.has(ind)}
                    onChange={() => setDraftInd(toggle(draftInd, ind))} />
                );
              })}
            </FilterRow>

            <FilterRow label={t('dashboard.sentiment.articles.filters.popular')}>
              <AllOption checked={!draftSrc.size} count={totalCount}
                onClick={() => setDraftSrc(new Set())} t={t} />
              {POPULAR_PLATFORMS
                .filter(c => platforms.some(p => p.code === c))
                .map(code => (
                  <CheckOption key={code}
                    label={platformLabel(code)}
                    count={sourceCounts.get(code) ?? 0}
                    checked={draftSrc.has(code)}
                    onChange={() => setDraftSrc(toggle(draftSrc, code))} />
                ))}
            </FilterRow>

            {/* Dashed divider */}
            <div style={{ borderTop: '1px dashed var(--border-color)' }} />

            {/* Group 2: article content dimensions */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.risk')}>
              <AllOption checked={!draftRisks.size}
                onClick={() => setDraftRisks(new Set())} t={t} />
              {ALL_RISKS.map(r => (
                <CheckOption key={r}
                  label={t(`dashboard.sentiment.articles.risk.${r}`)}
                  checked={draftRisks.has(r)}
                  onChange={() => setDraftRisks(toggle(draftRisks, r))} />
              ))}
            </FilterRow>

            <FilterRow label={t('dashboard.sentiment.articles.filters.sentiment')}>
              <AllOption checked={!draftSent.size}
                onClick={() => setDraftSent(new Set())} t={t} />
              {ALL_SENTIMENTS.map(s => (
                <CheckOption key={s}
                  label={t(`dashboard.sentiment.articles.labels.${s}`)}
                  checked={draftSent.has(s)}
                  onChange={() => setDraftSent(toggle(draftSent, s))} />
              ))}
            </FilterRow>

            {/* Keyword mention — single choice (all / title / body) */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.keywordMention')}>
              <AllOption checked={!draftKwScope}
                onClick={() => setDraftKwScope('')} t={t} />
              <CheckOption
                label={t('dashboard.sentiment.articles.filters.titleMention')}
                checked={draftKwScope === 'title'}
                onChange={() => setDraftKwScope(draftKwScope === 'title' ? '' : 'title')} />
              <CheckOption
                label={t('dashboard.sentiment.articles.filters.bodyMention')}
                checked={draftKwScope === 'body'}
                onChange={() => setDraftKwScope(draftKwScope === 'body' ? '' : 'body')} />
            </FilterRow>

            {/* Author region */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.authorRegion')}>
              <AllOption checked={!draftAuthorRegion}
                onClick={() => setDraftAuthorRegion('')} t={t} />
              <CheckOption
                label={t('dashboard.sentiment.articles.filters.authorRegionDomestic')}
                checked={draftAuthorRegion === 'domestic'}
                onChange={() => setDraftAuthorRegion(draftAuthorRegion === 'domestic' ? '' : 'domestic')} />
              <CheckOption
                label={t('dashboard.sentiment.articles.filters.authorRegionOverseas')}
                checked={draftAuthorRegion === 'overseas'}
                onChange={() => setDraftAuthorRegion(draftAuthorRegion === 'overseas' ? '' : 'overseas')} />
            </FilterRow>

            {/* Article type */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.articleType')}>
              <AllOption checked={!draftArticleType}
                onClick={() => setDraftArticleType('')} t={t} />
              <CheckOption
                label={t('dashboard.sentiment.articles.filters.articleTypeOriginal')}
                checked={draftArticleType === 'original'}
                onChange={() => setDraftArticleType(draftArticleType === 'original' ? '' : 'original')} />
              <CheckOption
                label={t('dashboard.sentiment.articles.filters.articleTypeReprint')}
                checked={draftArticleType === 'reprint'}
                onChange={() => setDraftArticleType(draftArticleType === 'reprint' ? '' : 'reprint')} />
            </FilterRow>
          </div>

          {/* ── Right: keyword + specified media + section + author + time ──────────── */}
          <div className="space-y-3"
            style={{ borderLeft: '1px solid var(--border-color)', paddingLeft: '1.25rem' }}>
            <TextInputRow label={t('dashboard.sentiment.articles.filters.topic')}
              value={draftTopic} onChange={setDraftTopic}
              placeholder={t('dashboard.sentiment.articles.filters.search')} />

            <TextInputRow label={t('dashboard.sentiment.articles.filters.specificMedia')}
              value={draftSpecificMedia} onChange={setDraftSpecificMedia}
              placeholder={t('dashboard.sentiment.articles.filters.specificMediaPlaceholder')} />

            <TextInputRow label={t('dashboard.sentiment.articles.filters.column')}
              value={draftColumn} onChange={setDraftColumn}
              placeholder={t('dashboard.sentiment.articles.filters.columnPlaceholder')} />

            <TextInputRow label={t('dashboard.sentiment.articles.filters.authorInput')}
              value={draftAuthorInput} onChange={setDraftAuthorInput}
              placeholder={t('dashboard.sentiment.articles.filters.authorInputPlaceholder')} />

            {/* The start and end date boxes merged into one range picker -- the same component as list pages site-wide */}
            <div>
              <label className="block text-xs font-semibold text-primary mb-1.5">
                {t('dashboard.sentiment.articles.filters.timeStart')}
                {' ~ '}
                {t('dashboard.sentiment.articles.filters.timeEnd')}
              </label>
              <DateRangeFilter
                value={fromQueryRange(draftStart, draftEnd)}
                onChange={(v) => {
                  const q = toQueryRange(v);
                  setDraftStart(q.since);
                  setDraftEnd(q.until);
                }} />
              {startEndError && (
                <p className="text-[11px] mt-1" style={{ color: '#dc2626' }}>
                  {t('dashboard.sentiment.articles.filters.timeRangeError')}
                </p>
              )}
            </div>
          </div>
        </div>

        <footer className="px-5 py-3 flex justify-end gap-2"
          style={{ borderTop: '1px solid var(--border-color)' }}>
          <button type="button" onClick={handleResetDraft}
            className="text-xs px-3 py-1.5 rounded"
            style={{ background: 'transparent', color: 'var(--text-secondary)' }}>
            {t('dashboard.sentiment.articles.filters.reset')}
          </button>
          <button type="button" onClick={onCancel}
            className="text-xs px-3 py-1.5 rounded"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
            {t('dashboard.sentiment.articles.filters.cancel')}
          </button>
          <button type="button" onClick={handleSave}
            disabled={!!startEndError}
            className="text-xs px-3 py-1.5 rounded disabled:opacity-50"
            style={{ background: 'var(--accent-primary)', color: '#fff' }}>
            {t('dashboard.sentiment.articles.filters.save')}
          </button>
        </footer>
      </div>
    </div>
  );

  return createPortal(node, document.body);
}

/** One filter row — label on the left + a row of checkboxes + AllOption on the right (wraps). Follows image.png visuals. */
function FilterRow({ label, children }:
  { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6rem_minmax(0,1fr)] gap-3 items-start">
      <div className="text-xs font-semibold text-primary pt-1 text-right pr-1">
        {label}:
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {children}
      </div>
    </div>
  );
}

/** "All" chip — shortcut to clear that dimension's options. checked = active by default when nothing specific is checked. */
function AllOption({ checked, count, onClick, t }:
  { checked: boolean; count?: number; onClick: () => void; t: (k: string) => string }) {
  return (
    <label className="flex items-center gap-1 text-xs cursor-pointer hover:text-primary"
      style={{ color: checked ? 'var(--accent-primary)' : 'var(--text-secondary)' }}
      onClick={(e) => { e.preventDefault(); onClick(); }}
    >
      <input type="checkbox" checked={checked} readOnly
        className="cursor-pointer pointer-events-none" />
      <span className="font-medium">{t('dashboard.sentiment.articles.filters.all')}</span>
      {count !== undefined && <span className="text-[10px] text-muted">{count}</span>}
    </label>
  );
}

/** Regular text input in the right column — label above / input below */
function TextInputRow({ label, value, onChange, placeholder }:
  { label: string; value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-primary mb-1.5">{label}</label>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-2 py-1.5 text-xs rounded"
        style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
    </div>
  );
}

/** Single checkbox option — with optional count */
function CheckOption({ label, count, checked, onChange }:
  { label: string; count?: number; checked: boolean; onChange: () => void }) {
  return (
    <label className="flex items-center gap-1 text-xs cursor-pointer hover:text-primary"
      style={{ color: checked ? 'var(--accent-primary)' : 'var(--text-secondary)' }}>
      <input type="checkbox" checked={checked} onChange={onChange}
        className="cursor-pointer" />
      <span>{label}</span>
      {count !== undefined && (
        <span className="text-[10px] text-muted">{count >= 1000 ? `${(count/1000).toFixed(1)}K` : count}</span>
      )}
    </label>
  );
}
