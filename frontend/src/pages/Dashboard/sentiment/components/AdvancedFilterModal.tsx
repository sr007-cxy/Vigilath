// 高级筛选 modal — 对齐 WisersOne 原型图 2 的批量筛选对话框.
//
// 顶部 chip 行(ArticlesTab)只放高频筛选(风险/情感/热门媒体/搜索),其他维度
// (媒体分类/媒体类型/仅相关)进这个 modal,避免顶部 chip 行过长.
//
// modal 内部用 draft 状态;点「保存」才 commit 到 ArticlesTab,避免边改边过滤抖动.
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import {
  MEDIA_TYPE_ORDER, INDUSTRY_ORDER,
  type MediaType, type Industry,
} from '../../../../constants/sentimentPlatforms';
import type { SentimentPlatform } from '../../../../types/sentiment';
import {
  ChipGrid, GridChip, ExpandableGridChip, Section,
} from './filterChips';

export interface AdvancedFilterValue {
  mediaTypes: Set<string>;
  industries: Set<string>;
  sources: Set<string>;
  onlyRelevant: boolean;
}

interface Props {
  // 父组件用 {advancedOpen && <Modal />} 控制挂载,组件内 draft state 在 mount 时
  // 自然从 value 初始化,关闭(卸载)后丢弃 — 不需要 open 这个 prop.
  value: AdvancedFilterValue;
  totalCount: number;
  platforms: SentimentPlatform[];
  axisCounts: { mediaType: Map<string, number>; industry: Map<string, number> };
  sourceCounts: Map<string, number>;
  platformLabel: (code: string) => string;
  onSave: (v: AdvancedFilterValue) => void;
  onCancel: () => void;
}

// 切换 Set 中某项
function toggle<T>(s: Set<T>, v: T): Set<T> {
  const n = new Set(s);
  if (n.has(v)) n.delete(v); else n.add(v);
  return n;
}

export function AdvancedFilterModal({
  value, totalCount, platforms,
  axisCounts, sourceCounts, platformLabel,
  onSave, onCancel,
}: Props) {
  const { t } = useTranslation();

  // 挂载时从 props 初始化 draft;关闭即卸载,自然丢弃 draft.
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

  // 按 media_type 分组 sourceGroups(与 ArticlesTab 一致)
  const sourceGroups = useMemo(() => {
    const byMt = new Map<MediaType | 'other', string[]>();
    for (const p of platforms) {
      const mt = (p.media_type as MediaType) || 'other';
      const list = byMt.get(mt) ?? [];
      list.push(p.code);
      byMt.set(mt, list);
    }
    const order: (MediaType | 'other')[] = [...MEDIA_TYPE_ORDER, 'other'];
    return order
      .filter(m => byMt.has(m))
      .map(m => ({ mediaType: m, codes: byMt.get(m)! }));
  }, [platforms]);

  const node = (
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        className="rounded-xl shadow-2xl w-full max-w-3xl max-h-[88vh] flex flex-col"
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

        <div className="p-5 space-y-5 overflow-y-auto">
          {/* 媒体分类 */}
          <Section
            title={t('dashboard.sentiment.articles.filters.industry')}
            hint={t('dashboard.sentiment.articles.filters.multiSelectHint')}
          >
            <ChipGrid>
              <GridChip
                label={t('dashboard.sentiment.articles.filters.all')}
                count={totalCount}
                active={!draftInd.size}
                onClick={() => setDraftInd(new Set())}
              />
              {INDUSTRY_ORDER.map(ind => {
                const c = axisCounts.industry.get(ind) ?? 0;
                const codes = platforms.filter(p => p.industry === ind).map(p => p.code);
                return (
                  <ExpandableGridChip key={ind}
                    label={t(`dashboard.sentiment.articles.industries.${ind}`)}
                    count={c}
                    active={draftInd.has(ind)}
                    onToggleSelf={() => setDraftInd(toggle(draftInd, ind as Industry))}
                    codes={codes}
                    platformLabel={platformLabel}
                    countOf={(code) => sourceCounts.get(code) ?? 0}
                    isSourceActive={(code) => draftSrc.has(code)}
                    onToggleSource={(code) => setDraftSrc(toggle(draftSrc, code))}
                  />
                );
              })}
            </ChipGrid>
          </Section>

          {/* 媒体类型 */}
          <Section
            title={t('dashboard.sentiment.articles.filters.mediaType')}
            hint={t('dashboard.sentiment.articles.filters.multiSelectHint')}
          >
            <ChipGrid>
              <GridChip
                label={t('dashboard.sentiment.articles.filters.all')}
                count={totalCount}
                active={!draftMt.size}
                onClick={() => setDraftMt(new Set())}
              />
              {sourceGroups.map(({ mediaType, codes }) => {
                const mtLabelKey = `dashboard.sentiment.articles.mediaTypes.${mediaType}`;
                const mtLabel = t(mtLabelKey);
                const mtDisplay = mtLabel === mtLabelKey ? mediaType : mtLabel;
                const groupCount = axisCounts.mediaType.get(mediaType) ?? 0;
                const isOther = mediaType === 'other';
                return (
                  <ExpandableGridChip key={mediaType}
                    label={mtDisplay}
                    count={groupCount}
                    active={!isOther && draftMt.has(mediaType)}
                    onToggleSelf={isOther ? null : (() => setDraftMt(toggle(draftMt, mediaType as MediaType)))}
                    codes={codes}
                    platformLabel={platformLabel}
                    countOf={(code) => sourceCounts.get(code) ?? 0}
                    isSourceActive={(code) => draftSrc.has(code)}
                    onToggleSource={(code) => setDraftSrc(toggle(draftSrc, code))}
                  />
                );
              })}
            </ChipGrid>
          </Section>

          {/* 仅相关 */}
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={draftOnly}
              onChange={(e) => setDraftOnly(e.target.checked)} />
            <span className="text-secondary">
              {t('dashboard.sentiment.articles.filters.onlyRelevant')}
            </span>
          </label>
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
