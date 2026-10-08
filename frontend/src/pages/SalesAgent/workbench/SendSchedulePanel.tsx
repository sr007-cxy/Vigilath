import { ChevronDown, ChevronRight } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { toast } from '../../../components/ui/toast';
import {
  cancelSendSchedule,
  confirmSendSchedule,
  createSendSchedule,
  fetchSendScheduleDetail,
  fetchSendSchedules,
  patchSendSchedule,
  previewSendSchedule,
  type SalesAgentSendSchedule,
  type SalesAgentSendScheduleDetail,
  type SalesAgentSendSchedulePlan,
  type SalesAgentSendSchedulePreview,
} from '../../../services/salesAgentApi';
import { friendlyError, numberOf } from './utils';

/** Which screen the panel is on.
 *
 *  detail is **no longer a separate screen** but "which row in the list is expanded": entering with an id (the chat scheduling card's
 *  "Open list on the right" uses this) pre-expands that row. Previously clicking a plan replaced the whole list,
 *  and seeing a second one meant clicking "← Plan list" first -- in one right panel, viewing two plans took four screen jumps.
 *
 *  **Skipped companies never enter the item table** (they're filtered out when the plan is created), so `GET /outreach/schedules/{id}`
 *  can't return them; yet "state the reason per item in the skipped area" is a hard rule of 4.5. This list reads the same copy the server keeps in
 *  `metadata.skipped` (see `metadataSkipped`) -- not relying on the frontend holding on to the response from plan creation,
 *  so the reasons are still there when the page is closed and reopened the next day.
 */
export type SendScheduleView =
  | { mode: 'list' }
  | { mode: 'compose'; leadIds: number[] }
  | { mode: 'detail'; scheduleId: number };

const TERMINAL_STATUSES = new Set(['done', 'cancelled', 'expired']);
/** 4.6: poll only while running, every 10 seconds. Nothing changes in the armed state, so polling would be pure waste. */
const RUNNING_POLL_MS = 10_000;
/** Don't preview on every keystroke when changing the time -- same debounce value as the batch drawer. */
const PREVIEW_DEBOUNCE_MS = 180;

const panelBtn: CSSProperties = { border: '1px solid var(--aui-border)', background: 'var(--aui-card)', color: 'var(--aui-muted-foreground)', borderRadius: 12, padding: '7px 12px', fontSize: 12, cursor: 'pointer' };
const panelPrimaryBtn: CSSProperties = { border: 'none', background: 'var(--aui-primary)', color: 'var(--aui-primary-foreground)', borderRadius: 999, padding: '8px 16px', fontSize: 12, fontWeight: 700, cursor: 'pointer' };
const inputStyle: CSSProperties = { border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)', borderRadius: 10, padding: '7px 9px', fontSize: 12, outline: 'none' };
const sectionStyle: CSSProperties = { border: '1px solid var(--border-color)', background: 'var(--bg-card)', borderRadius: 12, padding: '9px 11px' };
/** Small cards in the expanded area. Use a different background rather than another same-color border -- same-color cards in cards read as a pile of boxes without hierarchy. */
const subSectionStyle: CSSProperties = { border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 10, padding: '8px 10px' };

function disabledStyle(base: CSSProperties, disabled: boolean): CSSProperties {
  return disabled ? { ...base, opacity: .5, cursor: 'not-allowed' } : base;
}

/** Default value for datetime-local: the next full hour. **This is Beijing time**, not browser-local time --
 *  the backend reads timezone-less strings as Asia/Shanghai, so it must be spelled out next to the input. */
function beijingInputValue(offsetMinutes = 60): string {
  const bj = new Date(Date.now() + offsetMinutes * 60_000 + (new Date().getTimezoneOffset() + 480) * 60_000);
  bj.setMinutes(0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${bj.getFullYear()}-${pad(bj.getMonth() + 1)}-${pad(bj.getDate())}T${pad(bj.getHours())}:${pad(bj.getMinutes())}`;
}

/** The batch filtered out at plan creation, kept by the server in `metadata.skipped` (see `plan_send_schedule`). */
function metadataSkipped(schedule: SalesAgentSendSchedule | null): Array<{ lead_id: number; company: string; reason: string }> {
  const raw = schedule?.metadata?.skipped;
  if (!Array.isArray(raw)) return [];
  return raw.map(item => {
    const row = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
    return {
      lead_id: Number(row.lead_id) || 0,
      company: String(row.company || ''),
      reason: String(row.reason || ''),
    };
  }).filter(row => row.reason);
}

function statusTone(status: string): CSSProperties {
  if (status === 'running') return { background: 'var(--aui-primary)', color: 'var(--aui-primary-foreground)' };
  // draft / armed are both "not sent yet but will be", so they get the same prominent color: this is the user's only window to change their mind (4.5).
  if (status === 'draft' || status === 'armed') return { background: 'color-mix(in srgb, var(--aui-primary) 16%, transparent)', color: 'var(--aui-primary)' };
  return { background: 'var(--bg-surface)', color: 'var(--text-muted)' };
}

function Badge({ text, status }: { text: string; status: string }) {
  if (!text) return null;
  return <span style={{ ...statusTone(status), borderRadius: 999, padding: '2px 8px', fontSize: 11, fontWeight: 800, whiteSpace: 'nowrap' }}>{text}</span>;
}

export function SendSchedulePanel({ view, onViewChange, onClose, onScheduleChanged }: {
  view: SendScheduleView;
  onViewChange: (view: SendScheduleView) => void;
  onClose: () => void;
  /** When the status changes (created / confirmed / canceled), have the workbench refresh the pending count on the persistent entry */
  onScheduleChanged?: () => void;
}) {
  if (view.mode === 'compose') {
    return (
      <ScheduleCompose
        leadIds={view.leadIds}
        onClose={onClose}
        // Same metric definition as the batch drawer: close the panel once scheduled, no longer flipping to the detail screen.
        onCreated={() => { onScheduleChanged?.(); onClose(); }}
      />
    );
  }
  // list and detail are the same screen, differing only in "which row is expanded". The expanded state still goes through view --
  // if it were in ScheduleList's local state, clicking "Open list on the right" in chat would have nowhere to put the id.
  return (
    <ScheduleList
      expandedId={view.mode === 'detail' ? view.scheduleId : null}
      onToggle={id => onViewChange(id ? { mode: 'detail', scheduleId: id } : { mode: 'list' })}
      onClose={onClose}
      onScheduleChanged={onScheduleChanged}
    />
  );
}

function PanelFrame({ title, subtitle, onClose, children, footer }: { title: string; subtitle?: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px', borderBottom: '1px solid var(--border-color)' }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontWeight: 800, fontSize: 14, color: 'var(--text-primary)' }}>{title}</div>
          {!!subtitle && <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{subtitle}</div>}
        </div>
        <button type="button" onClick={onClose} style={panelBtn}>关闭</button>
      </div>
      {/* `alignContent: start` is required: grid rows stretch by default to fill the flex:1 height,
          and with only two cards they'd be stretched into two half-screen-tall empty boxes (input at the top, hundreds of pixels blank below). */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px 14px', display: 'grid', alignContent: 'start', gap: 9 }}>{children}</div>
      {!!footer && <div style={{ borderTop: '1px solid var(--border-color)', padding: '10px 14px', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>{footer}</div>}
    </div>
  );
}

/** First screen of scheduled sending: pick a time -> see the preview -> one click creates the plan and schedules it.
 *
 *  **Not split into "generate list" + "confirm schedule" steps.** On this screen the user has already seen the full time and
 *  "how many will send / how many can't", so that click is the authorization; asking for another confirmation would only make the first click seem to do nothing.
 *  Same metric definition as the batch drawer (`SendScheduleBatchDrawer`), using the same `confirm: true` path.
 *
 *  Counts come from `previewSendSchedule` -- which shares `evaluate_send_candidates` with `plan_send_schedule`,
 *  so the 8 reported here can't differ from the 8 actually scheduled.
 */
function ScheduleCompose({ leadIds, onClose, onCreated }: { leadIds: number[]; onClose: () => void; onCreated: (plan: SalesAgentSendSchedulePlan) => void }) {
  const [when, setWhen] = useState(() => beijingInputValue());
  const [throttle, setThrottle] = useState(30);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<SalesAgentSendSchedulePreview | null>(null);
  const [previewing, setPreviewing] = useState(true);
  const [previewError, setPreviewError] = useState('');
  // Changing the time triggers previews in succession, and response order isn't guaranteed: only accept the last sequence number sent,
  // otherwise a slow earlier response arriving late would overwrite the new count with the old one.
  const requestSeq = useRef(0);

  useEffect(() => {
    if (!when) { setPreviewing(false); setPreview(null); setPreviewError(''); return; }
    const seq = ++requestSeq.current;
    setPreviewing(true);
    setPreviewError('');
    const timer = window.setTimeout(() => {
      previewSendSchedule({ lead_ids: leadIds, scheduled_at: when })
        .then(res => {
          if (seq !== requestSeq.current) return;
          setPreview(res);
          setPreviewing(false);
        })
        .catch(e => {
          if (seq !== requestSeq.current) return;
          // A failed preview is "can't compute how many can be sent right now", not "0" -- users mustn't think none of the batch can be sent.
          // Disable OK and state the reason.
          setPreview(null);
          setPreviewError(friendlyError(e instanceof Error ? e.message : '试算失败,暂时算不出这批能发几家'));
          setPreviewing(false);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // leadIds is a new array on every render, so putting it in deps would trigger endlessly; it doesn't change once this screen is mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [when]);

  const sendable = numberOf(preview?.sendable_count, 0);
  const blocked = numberOf(preview?.blocked_count, 0);
  const warning = preview?.time_warning || '';
  // Outside the window the backend hard-rejects before creating the plan (the confirm=true path), so the frontend disables it first --
  // it can't be clickable in the frontend only to be rejected by the backend.
  const canSubmit = !busy && !!when && !previewing && !previewError && !warning && sendable > 0;

  async function submit() {
    if (!leadIds.length) { toast.error('先选中要发送的企业。'); return; }
    if (!when) { toast.error('要几点发?定时发和立即发是两个不同的授权,不能默认现在就发。'); return; }
    setBusy(true);
    try {
      // scheduled_at is passed as a timezone-less string, which the backend reads as Beijing time -- consistent with the note on the input.
      // confirm:true = create plan + schedule in one go; the returned schedule is already armed.
      const res = await createSendSchedule({ lead_ids: leadIds, scheduled_at: when, throttle_seconds: throttle, confirm: true });
      // The backend's returned hint is authoritative; a separate frontend sentence will drift from the backend sooner or later.
      toast.success(res.hint || '已排期,到点会自动逐封发。');
      onCreated(res);
    } catch (e) {
      toast.error(friendlyError(e instanceof Error ? e.message : '发送计划操作失败'));
    } finally {
      setBusy(false);
    }
  }

  const countLine = !when
    ? '先选个时间,再算这批能发几家。'
    : previewing
      ? '正在算这批能发几家…'
      : previewError
        ? previewError
        : blocked
          ? `会发 ${sendable} 家 · ${blocked} 家发不了`
          : `会发 ${sendable} 家`;

  return (
    <PanelFrame
      title="定时发送"
      subtitle={`已选 ${leadIds.length} 家`}
      onClose={onClose}
      footer={<>
        <button type="button" disabled={!canSubmit} onClick={() => void submit()} style={disabledStyle(panelPrimaryBtn, !canSubmit)}>{busy ? '排期中…' : '确定,到点发'}</button>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>确定之后到点逐封发,中途还能改时间或整个取消。</span>
      </>}
    >
      <div style={sectionStyle}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 800, color: 'var(--text-secondary)', marginBottom: 6 }}>什么时候发(北京时间)</label>
        <input type="datetime-local" value={when} disabled={busy} onChange={e => setWhen(e.currentTarget.value)} style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }} />
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>按北京时间理解,不随你电脑的时区变。</div>
      </div>
      <div style={sectionStyle}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 800, color: 'var(--text-secondary)', marginBottom: 6 }}>每封间隔(秒)</label>
        <input type="number" min={5} max={600} value={throttle} disabled={busy} onChange={e => setThrottle(Math.max(5, Math.min(600, Number(e.currentTarget.value) || 30)))} style={{ ...inputStyle, width: 120 }} />
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
          {leadIds.length > 1 ? `${leadIds.length} 家按 ${throttle} 秒一封发,约 ${Math.max(1, Math.round(throttle * (leadIds.length - 1) / 60))} 分钟发完 —— 是「开始发」的时间,不是「发完」的时间。` : '一家企业,到点就发。'}
        </div>
      </div>
      <div style={sectionStyle}>
        <div style={{ fontSize: 13, fontWeight: 850, color: previewError ? 'var(--color-danger, #dc2626)' : 'var(--text-primary)' }}>{countLine}</div>
        {!!warning && <div style={{ marginTop: 7, fontSize: 12, lineHeight: 1.6, color: 'var(--color-danger, #dc2626)' }}>{warning}</div>}
        {!previewing && !previewError && !warning && sendable === 0 && !!when && (
          <div style={{ marginTop: 7, fontSize: 12, lineHeight: 1.6, color: 'var(--color-danger, #dc2626)' }}>
            这几家现在一家都发不了(多半是没有公开邮箱或已退订)。换一批,或者先把联系方式补上。
          </div>
        )}
        {!previewing && !previewError && !warning && sendable > 0 && (
          <div style={{ marginTop: 7, fontSize: 12, lineHeight: 1.6, color: 'var(--text-secondary)' }}>
            {blocked ? '发不了的那几家会逐条写明原因,排期之后在下面的清单里能看到。' : '确定之前一封都不会发出去。'}
          </div>
        )}
      </div>
    </PanelFrame>
  );
}

/** Plan list. Clicking a row expands that row's details **in place**, clicking again collapses it, clicking another row expands that one instead.
 *
 *  The expanded state is managed by `view` above rather than stored locally -- the chat scheduling card's "Open list on the right" must be able to
 *  specify which one to expand, and that id needs somewhere to land. */
function ScheduleList({ expandedId, onToggle, onClose, onScheduleChanged }: {
  expandedId: number | null;
  onToggle: (id: number | null) => void;
  onClose: () => void;
  onScheduleChanged?: () => void;
}) {
  const [rows, setRows] = useState<SalesAgentSendSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const aliveRef = useRef(true);
  const expandedRef = useRef<HTMLDivElement | null>(null);

  // After confirming / changing time / canceling in the expanded area, the badge and counts on the outer row must update too,
  // so load must be callable back by child components -- it can no longer be a one-shot effect.
  const load = useCallback(async () => {
    try {
      const res = await fetchSendSchedules({ limit: 50 });
      if (!aliveRef.current) return;
      setRows(res.schedules || []);
      setError('');
    } catch (e) {
      if (aliveRef.current) setError(friendlyError(e instanceof Error ? e.message : '发送计划加载失败'));
    } finally {
      // loading starts as true, so there's no need to set it again on entry (setting it would be flagged by lint as a synchronous setState in an effect).
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    void load();
    return () => { aliveRef.current = false; };
  }, [load]);

  // When entering from chat with an id, that row may be hidden below the fold. Don't use smooth:
  // the user didn't scroll there themselves, and a smooth animation would make it seem the page moved on its own.
  useEffect(() => {
    if (!expandedId || loading) return;
    expandedRef.current?.scrollIntoView({ block: 'nearest' });
  }, [expandedId, loading]);

  const pendingCount = rows.filter(r => r.status === 'draft' || r.status === 'armed').length;
  return (
    <PanelFrame
      title="发送计划"
      subtitle={loading ? '加载中…' : pendingCount ? `${pendingCount} 个计划还没发出去,现在改还来得及` : `共 ${rows.length} 个计划`}
      onClose={onClose}
    >
      {!!error && <div style={{ ...sectionStyle, color: 'var(--text-primary)' }}>{error}</div>}
      {!loading && !rows.length && !error && (
        <div style={{ border: '1px dashed var(--border-color)', borderRadius: 12, padding: '18px 12px', color: 'var(--text-muted)', fontSize: 13, textAlign: 'center' }}>
          还没有发送计划。在推荐列表里选中企业,点「定时发送」。
        </div>
      )}
      {rows.map(row => {
        const expanded = expandedId === row.id;
        return (
          <div key={row.id} ref={expanded ? expandedRef : undefined} style={{ ...sectionStyle, padding: 0, overflow: 'hidden' }}>
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => onToggle(expanded ? null : row.id)}
              style={{ width: '100%', border: 'none', background: 'transparent', color: 'inherit', font: 'inherit', textAlign: 'left', cursor: 'pointer', display: 'grid', gap: 4, padding: '9px 11px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {/* Same control as "Expand company details" in the recommendation list: 24×24 outlined square + lucide chevron 14.
                    This is a span, not a button -- the whole row is already a button, and wrapping another would nest buttons.
                    Purely decorative; screen readers get the outer aria-expanded. */}
                <span aria-hidden style={{ width: 24, height: 24, flexShrink: 0, border: '1px solid var(--border-color)', borderRadius: 8, background: 'var(--bg-surface)', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
                  {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </span>
                <span style={{ fontWeight: 800, fontSize: 13, color: 'var(--text-primary)' }}>{row.scheduled_at_text || '—'}</span>
                <Badge text={row.status_text} status={row.status} />
              </div>
              {/* Indent aligned with the time in the row above: arrow square 24 + gap 8. */}
              <div style={{ fontSize: 12, color: 'var(--text-muted)', paddingLeft: 32 }}>
                共 {row.total} 家 · 待发送 {row.pending} · 已发出 {row.sent}
                {row.failed ? ` · 失败 ${row.failed}` : ''}
                {row.skipped ? ` · 跳过 ${row.skipped}` : ''}
              </div>
            </button>
            {expanded && (
              <div style={{ borderTop: '1px solid var(--border-color)', padding: '9px 11px', display: 'grid', alignContent: 'start', gap: 9 }}>
                <ScheduleDetailBody
                  scheduleId={row.id}
                  onChanged={() => { onScheduleChanged?.(); void load(); }}
                />
              </div>
            )}
          </div>
        );
      })}
    </PanelFrame>
  );
}

/** Details of a single plan, **embedded in the list row**, so without its own title bar or bottom action bar --
 *  the time and badge are already on the outer row and aren't repeated.
 *
 *  `onChanged`: called after confirm / time change / cancel, so the outside refreshes the row counts and the workbench pending badge. */
function ScheduleDetailBody({ scheduleId, onChanged }: {
  scheduleId: number;
  onChanged?: () => void;
}) {
  const [detail, setDetail] = useState<SalesAgentSendScheduleDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [rescheduling, setRescheduling] = useState(false);
  const [newWhen, setNewWhen] = useState('');
  const aliveRef = useRef(true);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await fetchSendScheduleDetail(scheduleId);
      if (!aliveRef.current) return;
      setDetail(res);
      setError('');
    } catch (e) {
      if (aliveRef.current && !silent) setError(friendlyError(e instanceof Error ? e.message : '发送计划加载失败'));
    } finally {
      if (aliveRef.current && !silent) setLoading(false);
    }
  }, [scheduleId]);

  useEffect(() => {
    aliveRef.current = true;
    void load();
    return () => { aliveRef.current = false; };
  }, [load]);

  const status = detail?.schedule.status || '';
  useEffect(() => {
    // 4.6 phase-one definition: **poll only while running, every 10 seconds, stop at a terminal state**.
    // In the armed state not a single number on the panel changes; polling would just waste requests.
    if (status !== 'running') return;
    const timer = window.setInterval(() => void load(true), RUNNING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [status, load]);

  const schedule = detail?.schedule || null;
  const items = detail?.items || [];
  const open = items.filter(it => it.status === 'pending' || it.status === 'pending_draft');
  const inFlight = items.filter(it => it.status === 'sending');
  const done = items.filter(it => it.status === 'sent' || it.status === 'failed');
  // The skipped area has two sources: those filtered out at plan creation (not in the item table at all), and those blocked by the compliance gate
  // or excluded right before sending during execution (in items). Both carry per-item reasons and aren't merged.
  const droppedItems = items.filter(it => it.status === 'skipped' || it.status === 'cancelled');
  // The former's only source is the server metadata -- close the page and come back the next day, and the skip reasons are still there.
  const planSkipped = metadataSkipped(schedule);
  const editable = status === 'draft' || status === 'armed';
  const cancellable = !!status && !TERMINAL_STATUSES.has(status);

  async function run(action: () => Promise<{ hint?: string }>, silentReload = false) {
    setBusy(true);
    try {
      const res = await action();
      if (res?.hint) toast.success(res.hint);
      onChanged?.();
      await load(silentReload);
    } catch (e) {
      toast.error(friendlyError(e instanceof Error ? e.message : '发送计划操作失败'));
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }

  async function submitReschedule() {
    if (!newWhen) { toast.error('要改到几点?'); return; }
    await run(() => patchSendSchedule(scheduleId, { scheduled_at: newWhen }));
    setRescheduling(false);
  }

  if (loading) return <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>加载中…</div>;
  if (error || !schedule) return <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>{error || '这个计划打不开了。'}</div>;

  return (
    <>
      {/* Time, badge and counts are all on the outer row already; this only adds what doesn't fit there: the interval,
          and the fact that a draft can still be withdrawn. Repeating the counts would make people think they're two different sets of numbers. */}
      <div style={{ ...subSectionStyle, display: 'grid', gap: 6 }}>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>每封间隔 {schedule.throttle_seconds} 秒</span>
        {schedule.status === 'draft' && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>现在一封都还没发出去。确认之后才会到点逐封发 —— 确认的是「给这些企业发信」这个范围。</div>}
      </div>

      {/* The action bar used to sit on PanelFrame's fixed bottom bar; with details embedded in the list row there's no bottom bar of its own,
          so it follows the summary. Placed here rather than at the very end: with a long plan the list takes a while to scroll,
          and "Confirm schedule" mustn't sink to the bottom to be hunted for. */}
      {(schedule.status === 'draft' || editable || status === 'running' || cancellable) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          {schedule.status === 'draft' && (
            <button type="button" disabled={busy} onClick={() => void run(() => confirmSendSchedule(scheduleId, true))} style={disabledStyle(panelPrimaryBtn, busy)}>确认排期</button>
          )}
          {editable && (
            <button type="button" disabled={busy} onClick={() => { setRescheduling(v => !v); setNewWhen(v => v || beijingInputValue()); }} style={disabledStyle(panelBtn, busy)}>改时间</button>
          )}
          {/* Once running, "Change time" isn't offered at all, consistent with the backend reschedule rejecting running --
              it can't be clickable in the frontend only to be rejected by the backend. */}
          {status === 'running' && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>正在发送,时间改不了了;取消只能停住还没发出的。</span>}
          {cancellable && (
            <button type="button" disabled={busy} onClick={() => void run(() => cancelSendSchedule(scheduleId, '用户在计划面板取消'))} style={{ ...disabledStyle(panelBtn, busy), marginLeft: 'auto' }}>取消计划</button>
          )}
        </div>
      )}

      {rescheduling && editable && (
        <div style={{ ...subSectionStyle, display: 'grid', gap: 7 }}>
          <label style={{ fontSize: 12, fontWeight: 800, color: 'var(--text-secondary)' }}>改到什么时候(北京时间)</label>
          <input type="datetime-local" value={newWhen} onChange={e => setNewWhen(e.currentTarget.value)} style={{ ...inputStyle, width: '100%' }} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" disabled={busy} onClick={() => void submitReschedule()} style={disabledStyle(panelPrimaryBtn, busy)}>保存新时间</button>
            <button type="button" onClick={() => setRescheduling(false)} style={panelBtn}>算了</button>
          </div>
        </div>
      )}

      {!!(open.length || inFlight.length) && (
        <div style={{ ...subSectionStyle, display: 'grid', gap: 7 }}>
          {/* Read-only here: the per-company exclusion checkboxes have been removed. Checking one re-laid out the whole screen (the "Exclude N selected"
              button squeezed into the title row out of nowhere), and this list has already been curated once in the batch drawer --
              to change the scope at this point, canceling the whole plan and redoing it is more straightforward than checking companies one by one. */}
          <span style={{ fontWeight: 800, fontSize: 13, color: 'var(--text-primary)' }}>会发这 {open.length + inFlight.length} 家</span>
          {[...inFlight, ...open].map(item => (
            <div key={item.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <span style={{ fontWeight: 700, color: 'var(--text-primary)', minWidth: 0, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.company_name || `线索 ${item.lead_id}`}</span>
              {/* pending_draft and pending both show as "Pending send" in status_text: whether the draft is written is our business. */}
              <span style={{ color: 'var(--text-muted)' }}>{item.status_text}</span>
            </div>
          ))}
        </div>
      )}

      {!!done.length && (
        <div style={{ ...subSectionStyle, display: 'grid', gap: 7 }}>
          <span style={{ fontWeight: 800, fontSize: 13, color: 'var(--text-primary)' }}>已经处理 {done.length} 家</span>
          {done.map(item => (
            <div key={item.id} style={{ display: 'grid', gap: 2, fontSize: 12 }}>
              <div style={{ display: 'flex', gap: 8 }}>
                <span style={{ fontWeight: 700, color: 'var(--text-primary)', flex: 1 }}>{item.company_name || `线索 ${item.lead_id}`}</span>
                <span style={{ color: 'var(--text-muted)' }}>{item.status_text}</span>
              </div>
              {/* Fallback trail: the first mailbox bounced and the second succeeded; without writing it out there's no explaining why it went to this address. */}
              {item.attempt_count > 1 && <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>试过 {item.attempt_count} 个邮箱{item.attempted.map(a => a.email).filter(Boolean).length ? `:${item.attempted.map(a => a.email).filter(Boolean).join(' → ')}` : ''}</div>}
              {!!item.skip_reason && <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{item.skip_reason}</div>}
            </div>
          ))}
        </div>
      )}

      {!!(planSkipped.length || droppedItems.length) && (
        <div style={{ ...subSectionStyle, display: 'grid', gap: 7 }}>
          <span style={{ fontWeight: 800, fontSize: 13, color: 'var(--text-primary)' }}>发不了 / 已剔除 {planSkipped.length + droppedItems.length} 家</span>
          {planSkipped.map(row => (
            <div key={`plan-${row.lead_id}`} style={{ display: 'grid', gap: 2, fontSize: 12 }}>
              <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{row.company || `线索 ${row.lead_id}`}</span>
              <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{row.reason}</span>
            </div>
          ))}
          {droppedItems.map(item => (
            <div key={`item-${item.id}`} style={{ display: 'grid', gap: 2, fontSize: 12 }}>
              <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{item.company_name || `线索 ${item.lead_id}`}</span>
              <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{item.skip_reason || item.status_text}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
