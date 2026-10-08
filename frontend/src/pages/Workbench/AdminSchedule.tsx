// Batch-run scheduling -- manage the audit frequency of all topics on one screen, without clicking into each client.
// Top half: engine capacity comparison (daily limit vs. daily demand); check whether it can handle the load before changing tiers;
// Bottom half: topic list, edited directly with a 4-state control (daily / weekly / off / paused N days).
// Status always renders the backend's effective_state -- the sole arbiter is geo/services/topic_schedule.py.
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageHead } from '../../components/PageHead';
import { Pagination } from '../../components/Pagination';
import {
  aiTelemetryApi, type ScheduleEngineRow, type ScheduleOverview,
  type ScheduleTier, type ScheduleTopicRow, type TopicSchedulePatch,
} from '../../services/aiTelemetryApi';
import { engineLabel, isCanonicalEngine } from '../../constants/engines';
import { UiSelect } from '../../components/ui/Select';

const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const hhmm = (h: number, m = 0) => `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;

const STATE_META: Record<string, { label: string; bg: string; fg: string }> = {
  daily:  { label: '每天跑', bg: 'var(--success-bg)', fg: '#10b981' },
  weekly: { label: '每周跑', bg: 'rgba(59,130,246,0.12)', fg: '#3b82f6' },
  off:    { label: '不跑',   bg: 'var(--danger-bg)',  fg: '#ef4444' },
  paused: { label: '暂停中', bg: 'rgba(234,179,8,0.14)',  fg: '#ca8a04' },
};

type FilterKey = 'all' | 'running' | 'off';

export function AdminSchedule() {
  const token = localStorage.getItem('token') || '';
  const [data, setData] = useState<ScheduleOverview | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [filter, setFilter] = useState<FilterKey>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(() => {
    aiTelemetryApi.getScheduleOverview(token)
      .then(setData)
      .catch(e => {
        setData({
          topics: [], engines: [],
          default_anchor_weekday: 0, default_anchor_hour: 2, default_anchor_minute: 5,
        });
        setErr(e.message);
      });
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  const patch = async (tid: number, p: TopicSchedulePatch) => {
    if (busyId !== null) return;
    setBusyId(tid); setErr(null);
    try {
      await aiTelemetryApi.patchTopicSchedule(tid, p, token);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
    setBusyId(null);
  };

  const rows = (data?.topics || []).filter(r =>
    filter === 'all' ? true
      : filter === 'off' ? r.effective_state === 'off'
        : r.effective_state !== 'off');

  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  useEffect(() => { if (page > totalPages) setPage(totalPages); }, [page, totalPages]);
  const pagedRows = rows.slice((page - 1) * pageSize, page * pageSize);

  const setFilterReset = (k: FilterKey) => { setFilter(k); setPage(1); };

  return (
    <div className="space-y-4">
      <PageHead titleKey="workbench.schedule.title" titleFallback="跑批调度" />
      <header>
        <h1 className="text-xl font-semibold text-primary">跑批调度</h1>
        <p className="text-xs text-secondary mt-0.5">
          控制每个主题多久检测一轮、从哪天几点开跑。改完下一轮调度即生效(约 1 小时内),
          无需重启服务;把开跑时间改到今天(且已过点)就等于让它今天开一轮。
        </p>
      </header>

      {err && (
        <div className="rounded-md p-2 text-xs"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>{err}</div>
      )}

      {/* Engine capacity -- check whether it can handle the load before changing tiers */}
      <section className="rounded-lg p-3"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <h2 className="text-sm font-medium text-primary mb-2">引擎容量</h2>
        <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(190px,1fr))' }}>
          {(data?.engines || []).filter(e => isCanonicalEngine(e.engine)).map(e => <EngineCard key={e.engine} row={e} />)}
        </div>
        <p className="text-[11px] text-muted mt-2">
          日需求 = 每天跑的主题词量 + 每周跑的词量 ÷ 7。红色表示当前档位下这个引擎跑不完,
          结果是覆盖变慢(有积压保护,不会把队列撑爆)。
        </p>
      </section>

      {/* Topic list */}
      <div className="flex gap-1.5">
        {([['all', '全部'], ['running', '在跑'], ['off', '不跑']] as [FilterKey, string][]).map(([k, label]) => (
          <button key={k} type="button" onClick={() => setFilterReset(k)}
                  className="text-xs px-2.5 py-1 rounded-md"
                  style={filter === k
                    ? { background: 'var(--accent-primary)', color: '#fff' }
                    : { background: 'var(--bg-input)', color: 'var(--text-secondary)',
                        border: '1px solid var(--border-color)' }}>
            {label}
          </button>
        ))}
      </div>

      {!data ? (
        <div className="text-xs text-muted text-center py-10">加载中…</div>
      ) : (
        <div className="rounded-lg overflow-x-auto"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ color: 'var(--text-muted)' }}>
                <th className="py-2 px-3 text-left font-normal">主题</th>
                <th className="py-2 px-3 text-left font-normal">状态</th>
                <th className="py-2 px-3 text-right font-normal">词 × 引擎</th>
                <th className="py-2 px-3 text-right font-normal">排队中</th>
                <th className="py-2 px-3 text-left font-normal">开跑时间</th>
                <th className="py-2 px-3 text-left font-normal">最近跑批</th>
                <th className="py-2 px-3 text-left font-normal">调整</th>
              </tr>
            </thead>
            <tbody>
              {pagedRows.map(r => (
                <tr key={r.topic_id} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                  <td className="py-2 px-3">
                    <Link to={`/ops/accounts/${r.user_id}/topics`}
                          className="text-primary hover:underline">{r.name}</Link>
                    <span className="text-muted ml-1.5 text-[10px]">#{r.topic_id}</span>
                    <div className="text-[10px] text-muted">{r.user_email}</div>
                  </td>
                  <td className="py-2 px-3"><StateBadge row={r} /></td>
                  <td className="py-2 px-3 text-right text-secondary tabular-nums">
                    {r.query_count} × {r.engines.length}
                    <span className="text-muted"> = {r.pairs_per_cycle}</span>
                  </td>
                  <td className="py-2 px-3 text-right tabular-nums"
                      style={{ color: r.backlog > 0 ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                    {r.backlog || '—'}
                  </td>
                  <td className="py-2 px-3">
                    <AnchorControl row={r} data={data} busy={busyId === r.topic_id}
                                   onPatch={(p) => patch(r.topic_id, p)} />
                  </td>
                  <td className="py-2 px-3 text-muted">
                    {r.last_run_at ? r.last_run_at.slice(0, 10) : '从未'}
                  </td>
                  <td className="py-2 px-3">
                    <TierControl row={r} busy={busyId === r.topic_id}
                                 onPatch={(p) => patch(r.topic_id, p)} />
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={7} className="py-8 text-center text-muted">没有主题</td></tr>
              )}
            </tbody>
          </table>
          <Pagination page={page} pageSize={pageSize} total={rows.length}
            onPageChange={setPage} onPageSizeChange={setPageSize}
            className="px-3 py-2 border-t" />
        </div>
      )}
    </div>
  );
}

function EngineCard({ row }: { row: ScheduleEngineRow }) {
  const unlimited = row.daily_cap === null;
  const bad = !row.sustainable;
  return (
    <div className="rounded-md p-2" style={{ background: 'var(--bg-input)' }}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-primary">{engineLabel(row.engine)}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded"
              style={bad ? { background: 'var(--danger-bg)', color: 'var(--danger-fg)' }
                         : { background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
          {unlimited ? '不限' : bad ? '跑不完' : '够用'}
        </span>
      </div>
      <div className="text-[11px] text-secondary mt-1 tabular-nums">
        日上限 {unlimited ? '—' : row.daily_cap} · 日需求 {row.demand_per_day}
      </div>
      <div className="text-[10px] text-muted tabular-nums">
        今日已跑 {row.used_today}
        {row.channel === 'api' ? ' · API 通道(不占账号)' : ` · 可用账号 ${row.usable_accounts}`}
      </div>
    </div>
  );
}

function StateBadge({ row }: { row: ScheduleTopicRow }) {
  const meta = STATE_META[row.effective_state] || STATE_META.weekly;
  const resume = row.resumes_at ? new Date(row.resumes_at) : null;
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded whitespace-nowrap"
          style={{ background: meta.bg, color: meta.fg }}>
      {meta.label}
      {row.effective_state === 'paused' && resume
        ? ` · ${resume.getMonth() + 1}-${resume.getDate()} 恢复` : ''}
    </span>
  );
}

// Start time -- topic-level override of the cycle anchor. Previously there was only one global env (default Monday 02:05),
// pinning all weekly topics to start on the same day; now each topic can pick its own weekday / hour, empty = follow the default.
// When set to "today and already past the hour", this cycle starts today, and the next scheduling round (within about 1 hour) starts it.
function AnchorControl({ row, data, busy, onPatch }: {
  row: ScheduleTopicRow; data: ScheduleOverview | null; busy: boolean;
  onPatch: (p: TopicSchedulePatch) => void;
}) {
  const defWd = data?.default_anchor_weekday ?? 0;
  const defHour = data?.default_anchor_hour ?? 2;
  const defMin = data?.default_anchor_minute ?? 5;
  const wd = row.cycle_anchor_weekday;
  const hour = row.cycle_anchor_hour;
  const daily = row.schedule_tier === 'daily';
  const off = row.schedule_tier === 'off';

  if (off) return <span className="text-[11px] text-muted">—</span>;

  const wdOptions = [
    { value: '', label: `默认(${WEEKDAY_LABELS[defWd] || '周一'})` },
    ...WEEKDAY_LABELS.map((label, i) => ({ value: String(i), label })),
  ];
  const hourOptions = [
    { value: '', label: `默认(${hhmm(defHour, defMin)})` },
    ...Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: hhmm(h, defMin) })),
  ];
  // Empty string = clear the override back to default (backend sentinel -1)
  const pick = (field: 'cycle_anchor_weekday' | 'cycle_anchor_hour') => (v: string) =>
    onPatch({ [field]: v === '' ? -1 : Number(v) } as TopicSchedulePatch);

  const sel = { fontSize: 11, padding: '2px 6px', minWidth: 0 } as const;
  return (
    <div className="inline-flex items-center gap-1">
      {daily ? (
        <span className="text-[11px] text-muted whitespace-nowrap">每天</span>
      ) : (
        <UiSelect value={wd === null || wd === undefined ? '' : String(wd)}
                  onChange={pick('cycle_anchor_weekday')} options={wdOptions}
                  disabled={busy} style={sel} title="每周从周几开始跑" />
      )}
      <UiSelect value={hour === null || hour === undefined ? '' : String(hour)}
                onChange={pick('cycle_anchor_hour')} options={hourOptions}
                disabled={busy} style={sel} title="当天几点开跑" />
    </div>
  );
}

function TierControl({ row, busy, onPatch }: {
  row: ScheduleTopicRow; busy: boolean; onPatch: (p: TopicSchedulePatch) => void;
}) {
  const [openPause, setOpenPause] = useState(false);
  const paused = row.effective_state === 'paused';
  const seg = (key: ScheduleTier, label: string) => (
    <button key={key} type="button" disabled={busy}
            onClick={() => { setOpenPause(false); onPatch({ schedule_tier: key, clear_pause: true }); }}
            className="text-[11px] px-2 py-1 first:rounded-l-md"
            style={{
              background: row.schedule_tier === key && !paused ? 'var(--accent-primary)' : 'transparent',
              color: row.schedule_tier === key && !paused ? '#fff' : 'var(--text-secondary)',
              border: '1px solid var(--border-color)', marginLeft: -1, opacity: busy ? 0.5 : 1,
            }}>
      {label}
    </button>
  );
  return (
    <div className="relative inline-flex items-center">
      {seg('daily', '每天')}
      {seg('weekly', '每周')}
      {seg('off', '不跑')}
      <button type="button" disabled={busy}
              onClick={() => (paused ? onPatch({ clear_pause: true }) : setOpenPause(v => !v))}
              className="text-[11px] px-2 py-1 rounded-r-md"
              style={{
                background: paused ? '#ca8a04' : 'transparent',
                color: paused ? '#fff' : 'var(--text-secondary)',
                border: '1px solid var(--border-color)', marginLeft: -1, opacity: busy ? 0.5 : 1,
              }}>
        {busy ? '…' : paused ? '恢复' : '暂停'}
      </button>
      {openPause && (
        <div className="absolute right-0 top-full mt-1 z-20 rounded-md p-1 flex gap-1"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          {[1, 3, 7].map(d => (
            <button key={d} type="button"
                    onClick={() => {
                      setOpenPause(false);
                      onPatch({ paused_until: new Date(Date.now() + d * 86400_000).toISOString() });
                    }}
                    className="text-[11px] px-2 py-1 rounded whitespace-nowrap"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
              {d} 天
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
