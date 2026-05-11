// 高级筛选 modal — 贴 WisersOne 原型图 2(image copy.png)的稠密 checkbox 行布局.
//
// 每行 = 左侧标签 + 右侧一排 checkbox(可换行).
// 包含:媒体分类 / 媒体类型 / 平台来源 / 仅相关.
//
// 与右栏 FilterRail 内的「时间/搜索/风险/情感/热门媒体」互补 — 这些「常用」筛选
// 留在 rail,而「媒体维度切片」放在 modal,触发点是 rail 的「⚙ 高级筛选」按钮.
//
// modal 内用 draft state,确认后才 commit 到 ArticlesTab,避免边改边过滤抖动.
// 关闭即卸载,draft 自然丢弃(父组件用 {open && <Modal />} 控制挂载).
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import {
  MEDIA_TYPE_ORDER, INDUSTRY_ORDER,
  type MediaType, type Industry,
} from '../../../../constants/sentimentPlatforms';
import type { SentimentPlatform } from '../../../../types/sentiment';

export interface AdvancedFilterValue {
  mediaTypes: Set<string>;
  industries: Set<string>;
  sources: Set<string>;
  onlyRelevant: boolean;
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
  const [draftMt, setDraftMt] = useState<Set<string>>(() => new Set(value.mediaTypes));
  const [draftInd, setDraftInd] = useState<Set<string>>(() => new Set(value.industries));
  const [draftSrc, setDraftSrc] = useState<Set<string>>(() => new Set(value.sources));
  const [draftOnly, setDraftOnly] = useState<boolean>(value.onlyRelevant);

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  // 按 media_type 分组 platforms — 平台来源段位按媒体类型 group 排序
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

        {/* 稠密 checkbox 行 — 每行 = 标签 + 一排 checkbox */}
        <div className="px-5 py-4 overflow-y-auto space-y-3.5">
          {/* 媒体分类 */}
          <FilterRow label={t('dashboard.sentiment.articles.filters.industry')}>
            {INDUSTRY_ORDER.map(ind => {
              const c = axisCounts.industry.get(ind) ?? 0;
              return (
                <CheckOption key={ind}
                  label={t(`dashboard.sentiment.articles.industries.${ind}`)}
                  count={c}
                  checked={draftInd.has(ind)}
                  onChange={() => setDraftInd(toggle(draftInd, ind as Industry))}
                />
              );
            })}
          </FilterRow>

          {/* 媒体类型 */}
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
                  onChange={() => setDraftMt(toggle(draftMt, mt as MediaType))}
                />
              );
            })}
          </FilterRow>

          {/* 平台来源(按 media_type 分组排序,平铺) */}
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

          {/* 显示选项 — 仅相关 */}
          <FilterRow label={t('dashboard.sentiment.articles.filters.displayOptions')}>
            <CheckOption
              label={t('dashboard.sentiment.articles.filters.onlyRelevant')}
              checked={draftOnly}
              onChange={() => setDraftOnly(!draftOnly)}
            />
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
              mediaTypes: draftMt,
              industries: draftInd,
              sources: draftSrc,
              onlyRelevant: draftOnly,
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

/** 一行筛选 — 左标签 + 右一排 checkbox.对齐 image copy.png 视觉. */
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
