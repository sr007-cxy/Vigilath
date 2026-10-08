// Site-wide unified pagination component -- generic for client-side pagination (in-memory slicing).
// Provides: range/total summary · page-size dropdown (optional) · first/previous/numbered pages (with ellipsis)/next/last.
// Theme via CSS variables, language subscribed via useTranslation (re-renders automatically when switching Chinese/English). Each page only manages page / pageSize state.
//
// Usage:
//   <Pagination page={page} pageSize={pageSize} total={rows.length}
//              onPageChange={setPage} onPageSizeChange={setPageSize} />
// Without onPageSizeChange, the "per page" dropdown is hidden (fixed page size).

import { useTranslation } from 'react-i18next';
import { UiSelect } from './ui/Select';

const DEFAULT_OPTIONS = [10, 20, 50, 100];

// Page number sequence with ellipsis: first, last and current±1 always shown, the rest folded into '…'. All listed when total pages ≤7.
function pageItems(current: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out: (number | '…')[] = [1];
  const lo = Math.max(2, current - 1);
  const hi = Math.min(total - 1, current + 1);
  if (lo > 2) out.push('…');
  for (let p = lo; p <= hi; p++) out.push(p);
  if (hi < total - 1) out.push('…');
  out.push(total);
  return out;
}

export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
  className?: string;
}

export function Pagination({
  page, pageSize, total, onPageChange, onPageSizeChange,
  pageSizeOptions = DEFAULT_OPTIONS, className = '',
}: PaginationProps) {
  const { i18n } = useTranslation();
  const zh = (i18n.language || 'zh').toLowerCase().startsWith('zh');
  const T = (z: string, e: string) => (zh ? z : e);

  if (total <= 0) return null;

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const from = (safePage - 1) * pageSize + 1;
  const to = Math.min(safePage * pageSize, total);

  const navBtn = 'px-2 py-1 rounded-md disabled:opacity-40 disabled:cursor-not-allowed';
  const navStyle = { background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' };

  return (
    <div className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-secondary ${className}`}>
      {/* Left: range + total + page size */}
      <div className="flex items-center gap-3">
        <span className="tabular-nums text-muted">
          {from}-{to}{' / '}{T(`共 ${total} 条`, `of ${total}`)}
        </span>
        {onPageSizeChange && (
          <span className="flex items-center gap-1 text-muted">
            {T('每页', 'Per page')}
            <UiSelect value={String(pageSize)} onChange={val => onPageSizeChange(Number(val))}
              className="px-1.5 py-1 rounded-md text-secondary"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
              options={pageSizeOptions.map(n => ({ value: String(n), label: n }))} />
            {zh && '条'}
          </span>
        )}
      </div>

      {/* Right: first / previous / numbered pages / next / last (shown only with multiple pages) */}
      {totalPages > 1 && (
        <div className="flex items-center gap-1">
          <button type="button" disabled={safePage <= 1} onClick={() => onPageChange(1)}
            title={T('首页', 'First')} className={navBtn} style={navStyle}>«</button>
          <button type="button" disabled={safePage <= 1} onClick={() => onPageChange(safePage - 1)}
            className={navBtn} style={navStyle}>{T('上一页', 'Prev')}</button>
          {pageItems(safePage, totalPages).map((it, i) =>
            it === '…' ? (
              <span key={`e${i}`} className="px-1.5 text-muted select-none">…</span>
            ) : (
              <button key={it} type="button" onClick={() => onPageChange(it)}
                aria-current={it === safePage ? 'page' : undefined}
                className="min-w-[28px] px-2 py-1 rounded-md tabular-nums"
                style={it === safePage
                  ? { background: 'var(--accent-primary)', color: 'var(--accent-contrast, #fff)', border: '1px solid var(--accent-primary)' }
                  : { background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
                {it}
              </button>
            )
          )}
          <button type="button" disabled={safePage >= totalPages} onClick={() => onPageChange(safePage + 1)}
            className={navBtn} style={navStyle}>{T('下一页', 'Next')}</button>
          <button type="button" disabled={safePage >= totalPages} onClick={() => onPageChange(totalPages)}
            title={T('末页', 'Last')} className={navBtn} style={navStyle}>»</button>
        </div>
      )}
    </div>
  );
}
