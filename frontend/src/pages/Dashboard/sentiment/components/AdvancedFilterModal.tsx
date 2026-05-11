// 高级筛选 modal — 贴 WisersOne 原型图 2(image copy.png)的 2 列布局.
//
// 左列:稠密 checkbox 行(每行 = 标签 + 一排勾选项),5 行 — 风险 / 情感 /
//       平台来源 / 媒体分类 / 媒体类型.
// 右列:文本/日期输入 — 关键字 + 起始时间 + 结束时间.
//
// modal 内全部用 draft state,确认后才一次性 commit 到 parent ArticlesTab.
// 关闭即卸载(父用 {open && <Modal />} 控制挂载),draft 自然丢弃.
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import {
  MEDIA_TYPE_ORDER, INDUSTRY_ORDER,
} from '../../../../constants/sentimentPlatforms';
import type {
  SentimentPlatform, RiskLevel, SentimentLabel,
} from '../../../../types/sentiment';

const ALL_RISKS: RiskLevel[] = ['none', 'low', 'medium', 'high'];
const ALL_SENTIMENTS: SentimentLabel[] = ['bullish', 'bearish', 'neutral', 'mixed', 'unknown'];

export interface AdvancedFilterValue {
  risks: Set<string>;
  sentiments: Set<string>;
  mediaTypes: Set<string>;
  industries: Set<string>;
  sources: Set<string>;
  topic: string;
  /** 'YYYY-MM-DD',空表示不限 */
  startDate: string;
  endDate: string;
}

interface Props {
  value: AdvancedFilterValue;
  platforms: SentimentPlatform[];
  axisCounts: { mediaType: Map<string, number>; industry: Map<string, number> };
  sourceCounts: Map<string, number>;
  platformLabel: (code: string) => string;
  onSave: (v: AdvancedFilterValue) => void;
  onCancel: () => void;
}

function toggle<T>(s: Set<T>, v: T): Set<T> {
  const n = new Set(s);
  if (n.has(v)) n.delete(v); else n.add(v);
  return n;
}

export function AdvancedFilterModal({
  value, platforms, axisCounts, sourceCounts,
  platformLabel, onSave, onCancel,
}: Props) {
  const { t } = useTranslation();

  // 挂载时从 props 初始化 draft;卸载时丢弃
  const [draftRisks, setDraftRisks] = useState<Set<string>>(() => new Set(value.risks));
  const [draftSent, setDraftSent] = useState<Set<string>>(() => new Set(value.sentiments));
  const [draftSrc, setDraftSrc] = useState<Set<string>>(() => new Set(value.sources));
  const [draftInd, setDraftInd] = useState<Set<string>>(() => new Set(value.industries));
  const [draftMt, setDraftMt] = useState<Set<string>>(() => new Set(value.mediaTypes));
  const [draftTopic, setDraftTopic] = useState<string>(value.topic);
  const [draftStart, setDraftStart] = useState<string>(value.startDate);
  const [draftEnd, setDraftEnd] = useState<string>(value.endDate);

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  // 按 media_type 分组 platforms,平台来源段位按媒体类型 group 排序后平铺
  const platformsByMt = MEDIA_TYPE_ORDER
    .map(mt => ({
      mediaType: mt,
      codes: platforms.filter(p => p.media_type === mt).map(p => p.code),
    }))
    .filter(g => g.codes.length > 0);

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
      startDate: draftStart,
      endDate: draftEnd,
    });
  };

  const node = (
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        className="rounded-xl shadow-2xl w-full max-w-5xl max-h-[88vh] flex flex-col"
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

        {/* 主体:2 列 grid — 左 checkbox 行 / 右 文本+日期 */}
        <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_280px] gap-6 px-5 py-4 overflow-y-auto">

          {/* ── 左列:5 行 checkbox 筛选 ───────────────────── */}
          <div className="space-y-3.5">
            {/* 1. 风险 */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.risk')}>
              {ALL_RISKS.map(r => (
                <CheckOption key={r}
                  label={t(`dashboard.sentiment.articles.risk.${r}`)}
                  checked={draftRisks.has(r)}
                  onChange={() => setDraftRisks(toggle(draftRisks, r))}
                />
              ))}
            </FilterRow>

            {/* 2. 情感 */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.sentiment')}>
              {ALL_SENTIMENTS.map(s => (
                <CheckOption key={s}
                  label={t(`dashboard.sentiment.articles.labels.${s}`)}
                  checked={draftSent.has(s)}
                  onChange={() => setDraftSent(toggle(draftSent, s))}
                />
              ))}
            </FilterRow>

            {/* 3. 平台来源(按 media_type 分组平铺) */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.source')}>
              {platformsByMt.flatMap(g =>
                g.codes.map(code => (
                  <CheckOption key={code}
                    label={platformLabel(code)}
                    count={sourceCounts.get(code) ?? 0}
                    checked={draftSrc.has(code)}
                    onChange={() => setDraftSrc(toggle(draftSrc, code))}
                  />
                )),
              )}
            </FilterRow>

            {/* 4. 媒体分类 */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.industry')}>
              {INDUSTRY_ORDER.map(ind => {
                const c = axisCounts.industry.get(ind) ?? 0;
                return (
                  <CheckOption key={ind}
                    label={t(`dashboard.sentiment.articles.industries.${ind}`)}
                    count={c}
                    checked={draftInd.has(ind)}
                    onChange={() => setDraftInd(toggle(draftInd, ind))}
                  />
                );
              })}
            </FilterRow>

            {/* 5. 媒体类型 */}
            <FilterRow label={t('dashboard.sentiment.articles.filters.mediaType')}>
              {MEDIA_TYPE_ORDER.map(mt => {
                const c = axisCounts.mediaType.get(mt) ?? 0;
                const mtLabelKey = `dashboard.sentiment.articles.mediaTypes.${mt}`;
                const mtLabel = t(mtLabelKey);
                const display = mtLabel === mtLabelKey ? mt : mtLabel;
                return (
                  <CheckOption key={mt}
                    label={display}
                    count={c}
                    checked={draftMt.has(mt)}
                    onChange={() => setDraftMt(toggle(draftMt, mt))}
                  />
                );
              })}
            </FilterRow>
          </div>

          {/* ── 右列:关键字 + 时间范围 ───────────────────── */}
          <div className="space-y-4"
            style={{ borderLeft: '1px solid var(--border-color)', paddingLeft: '1.25rem' }}>
            {/* 关键字 */}
            <div>
              <label className="block text-xs font-semibold text-primary mb-1.5">
                {t('dashboard.sentiment.articles.filters.topic')}
              </label>
              <input type="text" value={draftTopic}
                onChange={(e) => setDraftTopic(e.target.value)}
                placeholder={t('dashboard.sentiment.articles.filters.search')}
                className="w-full px-2 py-1.5 text-xs rounded"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              />
            </div>

            {/* 起始时间 */}
            <div>
              <label className="block text-xs font-semibold text-primary mb-1.5">
                {t('dashboard.sentiment.articles.filters.timeStart')}
              </label>
              <input type="date" value={draftStart}
                onChange={(e) => setDraftStart(e.target.value)}
                className="w-full px-2 py-1.5 text-xs rounded"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              />
            </div>

            {/* 结束时间 */}
            <div>
              <label className="block text-xs font-semibold text-primary mb-1.5">
                {t('dashboard.sentiment.articles.filters.timeEnd')}
              </label>
              <input type="date" value={draftEnd}
                onChange={(e) => setDraftEnd(e.target.value)}
                className="w-full px-2 py-1.5 text-xs rounded"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              />
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

/** 一行筛选 — 左标签 + 右一排 checkbox.贴 image copy.png 的视觉. */
function FilterRow({ label, children }:
  { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6rem_minmax(0,1fr)] gap-3 items-start">
      <div className="text-xs font-semibold text-primary pt-1">
        {label}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {children}
      </div>
    </div>
  );
}

/** 单个 checkbox 选项 — 含可选计数 */
function CheckOption({ label, count, checked, onChange }:
  { label: string; count?: number; checked: boolean; onChange: () => void }) {
  return (
    <label className="flex items-center gap-1 text-xs cursor-pointer hover:text-primary"
      style={{ color: checked ? 'var(--accent-primary)' : 'var(--text-secondary)' }}>
      <input type="checkbox" checked={checked} onChange={onChange}
        className="cursor-pointer" />
      <span>{label}</span>
      {count !== undefined && count > 0 && (
        <span className="text-[10px] text-muted">({count >= 1000 ? `${(count/1000).toFixed(1)}K` : count})</span>
      )}
    </label>
  );
}
