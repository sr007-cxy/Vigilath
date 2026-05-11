// 高级筛选 modal — 贴 WisersOne 原型图 image.png 的结构.
//
// 布局:
//   - 左主区(grow):filter 行,每行 = 标签 + 「全部 N」chip + 多个 checkbox 选项
//                  组与组之间用虚线分隔
//                  组 1:媒体类型 / 媒体分类 / 热门媒体
//                  组 2:全文情感 / 风险等级
//   - 右侧栏(280px):关键字 / 起始时间 / 结束时间 三个输入
//   - 底部:重置 / 取消 / 保存(蓝)三个按钮
//
// 「全部」chip = 该维度清空(没有任何具体选项被勾时它处于"激活"状态).
// modal 内全部用 draft state,确认后才 commit;关闭即卸载,draft 自然丢弃.
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

// 热门媒体段位的固定平台顺序(与 ArticlesTab 一致)
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
  topic: string;
  startDate: string;  // 'YYYY-MM-DD',空表示不限
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

  // draft state — 挂载时从 props 初始化,卸载时丢弃
  const [draftRisks, setDraftRisks] = useState<Set<string>>(() => new Set(value.risks));
  const [draftSent, setDraftSent] = useState<Set<string>>(() => new Set(value.sentiments));
  const [draftSrc, setDraftSrc] = useState<Set<string>>(() => new Set(value.sources));
  const [draftInd, setDraftInd] = useState<Set<string>>(() => new Set(value.industries));
  const [draftMt, setDraftMt] = useState<Set<string>>(() => new Set(value.mediaTypes));
  const [draftTopic, setDraftTopic] = useState<string>(value.topic);
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
      startDate: draftStart,
      endDate: draftEnd,
    });
  };

  const handleResetDraft = () => {
    // 本地重置 — 清空所有 draft;不直接 commit 到 parent(用户还可改后再保存)
    setDraftRisks(new Set());
    setDraftSent(new Set());
    setDraftSrc(new Set());
    setDraftInd(new Set());
    setDraftMt(new Set());
    setDraftTopic('');
    setDraftStart('');
    setDraftEnd('');
    // 同时也调 parent reset 让 rail 状态归零,避免保存时只重置部分
    onReset();
  };

  const node = (
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center p-4"
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

        {/* 主体:左 filter 行(grow)+ 右 280px 输入栏 */}
        <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_280px] gap-6 px-5 py-4 overflow-y-auto">

          {/* ── 左侧:filter 行 ─────────────────────────────────── */}
          <div className="space-y-3">
            {/* 组 1:媒体维度 */}
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

            {/* 虚线分隔 */}
            <div style={{ borderTop: '1px dashed var(--border-color)' }} />

            {/* 组 2:文章内容维度 */}
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
          </div>

          {/* ── 右侧:关键字 + 时间 ─────────────────────────────────── */}
          <div className="space-y-4"
            style={{ borderLeft: '1px solid var(--border-color)', paddingLeft: '1.25rem' }}>
            <div>
              <label className="block text-xs font-semibold text-primary mb-1.5">
                {t('dashboard.sentiment.articles.filters.topic')}
              </label>
              <input type="text" value={draftTopic}
                onChange={(e) => setDraftTopic(e.target.value)}
                placeholder={t('dashboard.sentiment.articles.filters.search')}
                className="w-full px-2 py-1.5 text-xs rounded"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
            </div>

            <div>
              <label className="block text-xs font-semibold text-primary mb-1.5">
                {t('dashboard.sentiment.articles.filters.timeStart')}
              </label>
              <input type="date" value={draftStart}
                onChange={(e) => setDraftStart(e.target.value)}
                className="w-full px-2 py-1.5 text-xs rounded"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
            </div>

            <div>
              <label className="block text-xs font-semibold text-primary mb-1.5">
                {t('dashboard.sentiment.articles.filters.timeEnd')}
              </label>
              <input type="date" value={draftEnd}
                onChange={(e) => setDraftEnd(e.target.value)}
                className="w-full px-2 py-1.5 text-xs rounded"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
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

/** 一行筛选 — 左标签 + 右一排 checkbox + AllOption(可换行).贴 image.png 视觉. */
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

/** 「全部」chip — 该维度清空选项的快捷.checked = 没有具体勾选时它默认激活. */
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

/** 单个 checkbox 选项 — 含可选计数 */
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
