// 高级筛选 modal — 贴 WisersOne 原型图 2(image copy.png)的稠密 checkbox 行布局.
//
// 每行 = 左侧标签 + 右侧一排 checkbox(自动换行).控件维度与右栏 FilterRail 一致:
// 风险 / 情感 / 热门媒体(平台来源)/ 媒体分类 / 媒体类型 / 情感类型(立场).
//
// modal 内用 draft state,确认后才 commit;关闭即卸载,draft 自然丢弃.
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

  const node = (
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        className="rounded-xl shadow-2xl w-full max-w-4xl max-h-[88vh] flex flex-col"
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

        {/* 稠密 checkbox 行 — 每行 = 标签 + 一排 checkbox(顺序与 rail 一致) */}
        <div className="px-5 py-4 overflow-y-auto space-y-3.5">
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

          {/* 3. 热门媒体 / 平台来源(按 media_type 分组,平铺) */}
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

        <footer className="px-5 py-3 flex justify-end gap-2"
          style={{ borderTop: '1px solid var(--border-color)' }}>
          <button type="button" onClick={onCancel}
            className="text-xs px-3 py-1.5 rounded"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
            {t('dashboard.sentiment.articles.filters.cancel')}
          </button>
          <button type="button"
            onClick={() => onSave({
              risks: draftRisks,
              sentiments: draftSent,
              mediaTypes: draftMt,
              industries: draftInd,
              sources: draftSrc,
            })}
            className="text-xs px-3 py-1.5 rounded"
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
