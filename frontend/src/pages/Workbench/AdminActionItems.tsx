// Flywheel P2 — optimization task center: daily report suggestions / retest regressions / insight suggestions converge into one unified to-do queue.
// Pick a topic -> list tasks (filtered by status) -> ignore / one-click convert to a publish plan / send to retest.
// The task center is the manual steering wheel of the flywheel's "iterate again" stage.
import { useCallback, useEffect, useState } from 'react';
import { PageHead } from '../../components/PageHead';
import { Pagination } from '../../components/Pagination';
import { useAuth } from '../../contexts/AuthContext';
import { useAgencyContext } from '../../contexts/AgencyContext';
import { adminReviewApi } from '../../services/adminReviewApi';
import { aiTelemetryApi } from '../../services/aiTelemetryApi';
import {
  adminActionItemsApi, type ActionItem, type ActionItemStatus,
} from '../../services/adminActionItemsApi';
import { UiSelect } from '../../components/ui/Select';
import { uiPrompt } from '../../components/ui/dialogs';

// Normalized shape of the topic dropdown -- admins use the platform-wide request list, clients their own topic list.
interface TopicOption { id: number; label: string; }

const SOURCE_LABEL: Record<string, string> = {
  daily_brief: '日报建议',
  retest: '复测退步',
  cell_insight: '洞察建议',
  source_strategy: '信源策略',
  placement_strategy: '投放策略',
  manual: '手录',
};

const STATUS_TABS: { key: ActionItemStatus | 'all'; label: string }[] = [
  { key: 'open', label: '待处理' },
  { key: 'converted', label: '已转化' },
  { key: 'dismissed', label: '已忽略' },
  { key: 'all', label: '全部' },
];

export function AdminActionItems() {
  const token = localStorage.getItem('token') || '';
  // The workbench is open externally: non-workbench staff (clients) can only view/operate tasks of their own topics, via the self-service channel.
  const { isStaff, isAgency } = useAuth();
  const { enterpriseUserId } = useAgencyContext();
  const self = !isStaff;
  const [topics, setTopics] = useState<TopicOption[]>([]);
  const [topicId, setTopicId] = useState<number | null>(null);
  const [tab, setTab] = useState<ActionItemStatus | 'all'>('open');
  const [items, setItems] = useState<ActionItem[] | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Switch topic / switch status / change page size -> back to page one
  useEffect(() => { setPage(1); }, [topicId, tab, pageSize]);

  useEffect(() => {
    const load = self
      ? aiTelemetryApi.listTopics(token, isAgency ? enterpriseUserId : null).then(
          (ts): TopicOption[] => ts.map(t => ({ id: t.id, label: t.name })))
      : adminReviewApi.listTopicReviews(token, 'approved').then(
          (ts): TopicOption[] => ts.map(t => ({
            id: t.topic_id, label: t.profile_name || t.topic_name })));
    load
      .then(opts => {
        setTopics(opts);
        setTopicId(prev => opts.some(topic => topic.id === prev) ? prev : opts[0]?.id ?? null);
      })
      .catch(() => { setTopics([]); setTopicId(null); });
  }, [token, self, isAgency, enterpriseUserId]);

  const refresh = useCallback(() => {
    if (topicId === null) return;
    setItems(null);
    adminActionItemsApi.list(topicId, token, tab === 'all' ? undefined : tab, self)
      .then(setItems)
      .catch(e => { setItems([]); setErr(e.message); });
  }, [topicId, tab, token, self]);

  useEffect(() => { refresh(); }, [refresh]);

  const onDismiss = async (it: ActionItem) => {
    setBusyId(it.id); setErr(null);
    try { await adminActionItemsApi.dismiss(it.id, token, self); refresh(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusyId(null);
  };

  const onConvert = async (it: ActionItem, action: 'add_plan_item' | 'retest') => {
    let seed: string | undefined;
    if (action === 'add_plan_item') {
      // Placement media suggestions only say "where to place", with no topic; other tasks' titles can serve as topics themselves, so give a default.
      // Using the title as the seed without asking would schedule unpublishable rows like 「投放媒体建议:博客园」 ("Placement media suggestion: Cnblogs").
      const recPlatform = String(it.source_ref?.platform || '');
      const isMedia = it.source === 'source_strategy' && !!recPlatform;
      const input = await uiPrompt(
        isMedia
          ? `建议投放到「${recPlatform}」。请填写这一篇写什么(选题):`
          : '这一行的选题(可改):',
        { title: '转发文计划', defaultValue: isMedia ? '' : it.title,
          placeholder: '例如:工业色母粒选型时怎么比对分散性' },
      );
      seed = (input || '').trim();
      if (!seed) return;              // Canceled or left empty = don't schedule
    }
    setBusyId(it.id); setErr(null);
    try { await adminActionItemsApi.convert(it.id, token, { action, seed }, self); refresh(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusyId(null);
  };

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.actionItems.title" titleFallback="优化任务中心" />
      <header className="flex items-end gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-primary">优化任务中心</h1>
          <p className="text-xs text-secondary mt-0.5">
            日报建议 · 复测退步 · 洞察建议统一收口 —— 一键转发文计划或转复测,驱动下一轮迭代
          </p>
        </div>
        <div className="flex-1" />
        <UiSelect
          value={topicId != null ? String(topicId) : ''}
          onChange={val => setTopicId(Number(val))}
          className="text-xs px-2 py-1.5 rounded-md"
          style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                   border: '1px solid var(--border-color)' }}
          options={topics.map(tp => ({ value: String(tp.id), label: `${tp.label} (#${tp.id})` }))}
        />
      </header>

      <div className="flex gap-1.5">
        {STATUS_TABS.map(s => (
          <button key={s.key} type="button" onClick={() => setTab(s.key)}
            className="text-xs px-2.5 py-1 rounded-md"
            style={tab === s.key
              ? { background: 'var(--accent-primary)', color: '#fff' }
              : { background: 'var(--bg-input)', color: 'var(--text-secondary)',
                  border: '1px solid var(--border-color)' }}>
            {s.label}
          </button>
        ))}
      </div>

      {err && (
        <div className="rounded-md p-2 text-xs"
          style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>{err}</div>
      )}

      {items === null ? (
        <div className="text-xs text-muted text-center py-10">加载中…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">
          {tab === 'open' ? '没有待处理任务 🎉(日报/复测/洞察产出后会自动汇入)' : '暂无记录'}
        </div>
      ) : (
        <div className="space-y-2">
          {items.slice((page - 1) * pageSize, page * pageSize).map(it => (
            <div key={it.id} className="p-3 rounded-lg"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="flex items-center gap-2 flex-wrap">
                <PriorityBadge p={it.priority} />
                <span className="text-[10px] px-1.5 py-0.5 rounded"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-muted)' }}>
                  {SOURCE_LABEL[it.source] || it.source}
                </span>
                <span className="text-sm font-medium text-primary">{it.title}</span>
                <div className="flex-1" />
                <span className="text-[10px] text-muted">
                  {(it.created_at || '').slice(0, 10)}
                </span>
              </div>
              {it.body_md && (
                <div className="text-[11px] text-secondary mt-1.5 whitespace-pre-wrap">
                  {it.body_md}
                </div>
              )}
              {it.status === 'open' ? (
                <div className="flex gap-2 mt-2">
                  <button type="button" disabled={busyId === it.id}
                    onClick={() => onConvert(it, 'add_plan_item')}
                    className="text-xs px-2.5 py-1 rounded-md disabled:opacity-50"
                    style={{ background: 'var(--accent-primary)', color: '#fff' }}>
                    ➕ 转发文计划
                  </button>
                  {it.linked_doc_id != null && (
                    <button type="button" disabled={busyId === it.id}
                      onClick={() => onConvert(it, 'retest')}
                      className="text-xs px-2.5 py-1 rounded-md disabled:opacity-50"
                      style={{ background: 'transparent', color: 'var(--text-secondary)',
                               border: '1px solid var(--border-color)' }}>
                      🔁 再复测
                    </button>
                  )}
                  <button type="button" disabled={busyId === it.id}
                    onClick={() => onDismiss(it)}
                    className="text-xs px-2.5 py-1 rounded-md disabled:opacity-50"
                    style={{ background: 'transparent', color: 'var(--text-muted)',
                             border: '1px solid var(--border-color)' }}>
                    忽略
                  </button>
                </div>
              ) : (
                <div className="text-[10px] text-muted mt-2">
                  {it.status === 'converted' && `已转化${it.linked_plan_item_id ? ' · 已进发文计划' : ''}`}
                  {it.status === 'dismissed' && '已忽略'}
                  {it.status === 'done' && '已完成'}
                  {it.resolved_at ? ` · ${it.resolved_at.slice(0, 10)}` : ''}
                </div>
              )}
            </div>
          ))}
          <Pagination page={page} pageSize={pageSize} total={items.length}
            onPageChange={setPage} onPageSizeChange={setPageSize} className="pt-2" />
        </div>
      )}
    </div>
  );
}

function PriorityBadge({ p }: { p: string }) {
  const color = p === 'P0' ? '#dc2626' : p === 'P1' ? '#ea580c' : '#64748b';
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ background: `${color}1f`, color }}>{p}</span>
  );
}
