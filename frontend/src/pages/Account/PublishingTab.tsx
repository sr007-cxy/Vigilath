// Placements & published -- the cross-topic placement ledger.
//
// Multi-topic accounts want to know "what have I placed in total, which have gone live, which are still waiting for link backfill",
// without clicking through topic by topic. Paid placement is real money spent, so it needs a ledger.
//
// Layout follows the "List pages" section of docs/03-系统设计/前端UI规范.md:
//   h-full flex flex-col -> header (filters/summary) shrink-0 · table flex-1 min-h-0 · footer shrink-0
// Padding is provided by SidebarShell's main; the page adds none (otherwise double padding).
// So the table is as tall as the window, the header is pinned, the summary is always visible at the bottom, and **no page-level scrolling occurs**.
// TableShell gets maxHeight={false} -- height is already bounded by the flex parent; limiting again would cause double scrollbars.
//
// It used to be a two-line list built from divs (title+time / status+cost), with no visible columns, no comparison,
// and no sorting. Paid placement is ledger-like and should be a standard table.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { fmtTime } from '../../utils/datetime';
import { contentApi, type PublishTask } from '../../services/contentApi';
import {
  taskStatusLabel, taskDisplayName, platformLabel, isPaidMediaTask,
} from '../../components/publish/publishShared';
import {
  TableShell, StickyHead, SkeletonRows, EmptyRow, UiSelect, Badge, type BadgeTone,
} from '../../components/ui';
import { points } from '../../services/balanceApi';

const FILTERS = [
  { value: '', label: '全部' },
  { value: 'planned', label: '待发布' },
  { value: 'pending_url', label: '待回填链接' },
  { value: 'published', label: '已投放' },
  { value: 'failed', label: '异常' },
];

// Status colors use Badge's semantic palette; no more hex in the page (spec rule 1: colors only via CSS variables).
const STATUS_TONE: Record<string, BadgeTone> = {
  published: 'success',
  failed: 'danger',
  pending_url: 'warn',
  planned: 'info',
};

const MODE_LABEL: Record<string, string> = {
  auto: '自动发布',
  manual: '人工发布',
  paid_media: '付费媒体',
};

const COLS = 6;

export function PublishingTab() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<PublishTask[]>([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const token = localStorage.getItem('token') || '';

  const load = useCallback(async (st: string) => {
    if (!token) return;
    setLoading(true); setErr('');
    try {
      setRows(await contentApi.listMyPublishTasks(token, { status: st || undefined }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t('common.errors.loadFailed', '加载失败'));
    } finally {
      setLoading(false);
    }
  }, [token, t]);

  useEffect(() => { load(status); }, [load, status]);

  const stat = useMemo(() => ({
    total: rows.length,
    published: rows.filter(r => r.status === 'published').length,
    paid: rows.filter(isPaidMediaTask).length,
    spent: rows.reduce((s, r) => s + (r.charged_cents || 0), 0),
  }), [rows]);

  return (
    <div className="h-full flex flex-col gap-4">
      {/* ── Header: title + filters. shrink-0, not part of height allocation ── */}
      <header className="shrink-0 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-primary">
            {t('account.publishing.title', '投放与已发布')}
          </h1>
          <p className="text-xs text-muted mt-0.5">跨主题的投放总账 —— 投了什么、出稿没有、花了多少</p>
        </div>
        <UiSelect
          value={status}
          onChange={v => setStatus(String(v))}
          className="text-sm px-2 py-1.5 rounded-md"
          style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
          options={FILTERS}
        />
      </header>

      {err && (
        <div className="shrink-0 text-sm px-3 py-2 rounded-lg"
             style={{ color: 'var(--danger-fg)', background: 'var(--danger-bg)', border: '1px solid var(--danger-border)' }}>
          {err}
        </div>
      )}

      {/* ── Table: the only layer that takes the remaining height. min-h-0 can't be omitted, or many rows push the parent open ── */}
      <div className="flex-1 min-h-0">
        <TableShell maxHeight={false} className="h-full">
          <table className="w-full text-xs">
            <StickyHead>
              <th className="px-3 py-2.5 text-left font-medium">文章</th>
              <th className="px-3 py-2.5 text-left font-medium">媒体</th>
              <th className="px-3 py-2.5 text-left font-medium">方式</th>
              <th className="px-3 py-2.5 text-left font-medium">状态</th>
              <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">时间</th>
              <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">实付</th>
            </StickyHead>

            {loading ? (
              <SkeletonRows rows={8} cols={COLS} />
            ) : rows.length === 0 ? (
              <EmptyRow
                colSpan={COLS}
                title={status ? '这个状态下还没有投放任务' : '还没有投放过任何文章'}
                hint={status
                  ? '换一个状态看看,或清掉筛选看全部。'
                  : '内容库里的稿件确认后可以投放到媒体,出稿后这里会记下链接与花费。'}
              />
            ) : (
              <tbody>
                {rows.map(r => {
                  // Time metric definition: actual publish > planned publish > created. For live items, only the actual time is meaningful
                  const when = r.actual_published_at || r.planned_publish_at || r.created_at || '';
                  return (
                    <tr key={r.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td className="px-3 py-2.5 max-w-[26rem]">
                        {r.published_url ? (
                          <a href={r.published_url} target="_blank" rel="noreferrer"
                             className="block truncate hover:underline"
                             style={{ color: 'var(--accent-primary)' }}
                             title={taskDisplayName(r)}>
                            {taskDisplayName(r)}
                          </a>
                        ) : (
                          <span className="block truncate text-primary" title={taskDisplayName(r)}>
                            {taskDisplayName(r)}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-secondary whitespace-nowrap">
                        {r.media_name || platformLabel(r.platform)}
                      </td>
                      <td className="px-3 py-2.5 text-secondary whitespace-nowrap">
                        {MODE_LABEL[r.publish_mode] || r.publish_mode}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <Badge tone={STATUS_TONE[r.status] || 'neutral'}>{taskStatusLabel(r)}</Badge>
                      </td>
                      <td className="px-3 py-2.5 text-muted whitespace-nowrap tabular-nums">
                        {when ? fmtTime(when) : '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right whitespace-nowrap tabular-nums text-secondary">
                        {(r.charged_cents ?? 0) > 0 ? points(r.charged_cents) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            )}
          </table>
        </TableShell>
      </div>

      {/* ── Footer: summary pinned at the bottom. These numbers used to be three big cards at the top of the page,
             scrolled out of view with many rows -- yet a ledger's totals are exactly what should always stay visible ── */}
      <footer className="shrink-0 flex items-center gap-6 flex-wrap text-xs px-1">
        {([
          ['投放任务', String(stat.total)],
          ['已出稿', String(stat.published)],
          ['付费投放', String(stat.paid)],
          ['累计花费', points(stat.spent)],
        ] as const).map(([label, v]) => (
          <span key={label} className="flex items-baseline gap-1.5">
            <span className="text-muted">{label}</span>
            <span className="text-sm font-semibold text-primary tabular-nums">{v}</span>
          </span>
        ))}
      </footer>
    </div>
  );
}

export default PublishingTab;
