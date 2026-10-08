// "Email this batch of companies at 3 pm" -> pick a batch in the right drawer -> one click on OK schedules it.
//
// Same form as the "Company send records" drawer (overlay + slide in from the right), not the persistent right column `<aside>`:
// the persistent column is occupied by reports / send plan details, and this is transient; squeezing in would push away what the user is viewing.
//
// The drawer **only reports counts** ("will send to 8 · 2 can't be sent"), without listing companies: at the batch selection step the user
// only needs to judge "which batch, how many"; the full list and per-company skip reasons are in the post-scheduling card and right panel.
//
// Clicking "OK" = create the plan + schedule it (armed) in one go. Splitting it into two clicks doesn't make authorization clearer --
// the user has already seen the full time and counts on this drawer, so that click is the authorization; asking for another confirmation would only make
// "OK" seem to do nothing.
import { X } from 'lucide-react';
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { toast } from '../../../components/ui/toast';
import { createSendSchedule, previewSendSchedule, type SalesAgentSendSchedulePreview } from '../../../services/salesAgentApi';
import { type A2UISendSchedulePickerModel } from '../a2uiBridge';
import { friendlyError, numberOf } from './utils';

const primaryBtn: CSSProperties = { border: 'none', background: 'var(--aui-primary)', color: 'var(--aui-primary-foreground)', borderRadius: 999, padding: '10px 18px', cursor: 'pointer', fontWeight: 700, fontSize: 13 };
const ghostBtn: CSSProperties = { border: '1px solid var(--border-color)', background: 'var(--bg-card)', color: 'var(--text-muted)', borderRadius: 12, padding: '9px 14px', cursor: 'pointer', fontSize: 13 };
const fieldLabel: CSSProperties = { color: 'var(--text-muted)', fontSize: 12, marginBottom: 6 };
const noteDanger: CSSProperties = { fontSize: 12.5, lineHeight: 1.6, color: 'var(--color-danger, #dc2626)' };
const noteMuted: CSSProperties = { fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-secondary)' };

/** Sentinel value used when "All" is selected. Real campaign_ids start at 1 and 0 is the historical empty batch bucket, so neither collides. */
const ALL_BATCHES = -1;

/** Slide in/out duration. The component stays mounted during the exit -- unmounting directly makes the drawer vanish with a "snap",
 *  and users can't tell whether it closed or the page glitched. */
const SLIDE_MS = 220;
/** If the system has "reduce motion" on, don't slide: that's someone with vestibular sensitivity telling you it's uncomfortable. */
const reducedMotion = () => typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

export function SendScheduleBatchDrawer({
  model,
  onClose,
  onScheduled,
}: {
  model: A2UISendSchedulePickerModel;
  onClose: () => void;
  /** Scheduled successfully -- the caller only needs to refresh the pending count, not push plan details open along the way */
  onScheduled: () => void;
}) {
  const multi = model.batches.length > 1;
  const [choice, setChoice] = useState<number>(() => {
    const preferred = model.batches.find(b => b.campaignId === model.defaultCampaignId);
    return preferred ? preferred.campaignId : (model.batches[0]?.campaignId ?? ALL_BATCHES);
  });
  // Bare Beijing-time string (`2026-09-20T15:00`) -- the backend convention reads bare strings as Beijing time,
  // and it's exactly the value format of `<input type="datetime-local">`, so no conversion is needed.
  const [scheduledAt, setScheduledAt] = useState(model.scheduledAt);
  const [preview, setPreview] = useState<SalesAgentSendSchedulePreview | null>(null);
  const [previewing, setPreviewing] = useState(true);
  const [previewError, setPreviewError] = useState('');
  const [busy, setBusy] = useState(false);
  // Switching batch/changing time triggers previews in succession, and response order isn't guaranteed. Only accept the last sequence number sent,
  // otherwise a slow earlier response arriving late would overwrite the new count with the old one -- the number shown wouldn't match the selected batch.
  const requestSeq = useRef(0);

  // Enter: after mounting, wait one frame before flipping open to true, so the browser has a frame resting in the "off-screen" state,
  // giving the transition a starting point to interpolate from -- setting it within the same frame jumps straight to the end state, no animation.
  const [open, setOpen] = useState(false);
  const closeTimer = useRef<number | null>(null);
  useEffect(() => {
    const raf = window.requestAnimationFrame(() => setOpen(true));
    return () => {
      window.cancelAnimationFrame(raf);
      if (closeTimer.current) window.clearTimeout(closeTimer.current);
    };
  }, []);

  /** Play the exit first and only truly unmount afterwards. `force` is for the scheduled-successfully path -- busy is still true then. */
  const requestClose = (force = false) => {
    if ((busy && !force) || closeTimer.current) return;
    setOpen(false);
    closeTimer.current = window.setTimeout(onClose, reducedMotion() ? 0 : SLIDE_MS);
  };

  const campaignIds = choice === ALL_BATCHES ? model.batches.map(b => b.campaignId) : [choice];

  useEffect(() => {
    const seq = ++requestSeq.current;
    setPreviewing(true);
    setPreviewError('');
    const timer = window.setTimeout(() => {
      previewSendSchedule({ campaign_ids: campaignIds, scheduled_at: scheduledAt, session_id: model.sessionId || null })
        .then(res => {
          if (seq !== requestSeq.current) return;
          setPreview(res);
          setPreviewing(false);
        })
        .catch(e => {
          if (seq !== requestSeq.current) return;
          // A failed preview means "can't compute how many can be sent right now"; it must not be treated as "0" --
          // that would make users think none of the batch can be sent. Disable OK and state the reason.
          setPreview(null);
          setPreviewError(friendlyError(e instanceof Error ? e.message : '试算失败,暂时算不出这批能发几家'));
          setPreviewing(false);
        });
    }, 180);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [choice, scheduledAt, model.sessionId]);

  // Only one time rule remains: not earlier than now. The former hardcoded 09:00-18:00 send window has been removed entirely.
  const warning = preview?.time_warning || (scheduledAt === model.scheduledAt ? model.timeWarning : '');
  const sendable = numberOf(preview?.sendable_count, 0);
  const blocked = numberOf(preview?.blocked_count, 0);
  const blockedByTime = !!warning;
  const canConfirm = !busy && !previewing && !previewError && !blockedByTime && sendable > 0;

  const confirm = async () => {
    setBusy(true);
    try {
      const res = await createSendSchedule({
        lead_ids: [],
        campaign_ids: campaignIds,
        scheduled_at: scheduledAt,
        session_id: model.sessionId || null,
        confirm: true,
      });
      // The backend's returned hint is authoritative -- re-writing "it will send automatically on time" in the frontend will drift from the backend sooner or later.
      toast.success(res.hint || '已排期,到点会自动逐封发。');
      onScheduled();
      // Close the drawer once scheduled and return to the screen the user was on. The confirmation feedback is the toast above;
      // no extra plan details pushed out to get in the way -- to check the list, go in via the "Send plans" entry.
      requestClose(true);
    } catch (e) {
      toast.error(friendlyError(e instanceof Error ? e.message : '排期失败'));
      setBusy(false);
    }
  };

  // "How many selected" can be computed locally without waiting for the preview -- this number echoes the user's latest click,
  // and making it flicker with a network request makes no sense. The preview only decides the second half, "how many will be sent".
  const selectedLeads = choice === ALL_BATCHES
    ? model.totalLeads
    : numberOf(model.batches.find(b => b.campaignId === choice)?.leadCount, 0);

  // Second half of the line under the title. When the preview fails, this only says "can't compute right now"; the full reason stays in the
  // red text block in the body -- the title row can't fit an error message, and truncating it would hide what to do.
  const summaryTail = previewing
    ? '正在算能发几家…'
    : previewError
      ? '暂时算不出能发几家'
      : blocked
        ? `会发 ${sendable} 家 · ${blocked} 家发不了`
        : `会发 ${sendable} 家`;

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 82, display: 'flex', justifyContent: 'flex-end' }}>
      {/* The overlay uses a button rather than a div with onClick: click requires press and release on the same element,
          so dragging a text selection from the drawer onto the overlay and releasing won't close it by mistake. Closing is disabled while scheduling, so
          the request isn't still in flight after closing with the user thinking it wasn't scheduled. */}
      <button
        type="button"
        aria-label="关闭定时发送"
        disabled={busy}
        onClick={() => requestClose()}
        style={{ position: 'absolute', inset: 0, border: 'none', background: 'rgba(15,23,42,.38)', cursor: busy ? 'not-allowed' : 'pointer', opacity: open ? 1 : 0, transition: `opacity ${SLIDE_MS}ms ease` }}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="sales-agent-batch-send-title"
        style={{
          // Same sizing as the right draft detail panel (`SalesReportSidePanel`): 620 is that panel's
          // initial `sidePanelWidth`, and `maxWidth` is copied too. They appear one after the other, and different widths
          // would look like switching to a different product.
          position: 'relative', width: 620, maxWidth: 'min(860px, 92vw)', height: '100vh', background: 'var(--bg-card)',
          borderLeft: '1px solid var(--border-color)', boxShadow: '-24px 0 48px rgba(15,23,42,.18)',
          display: 'flex', flexDirection: 'column',
          // Fast then slow, settling against the edge at the end -- a linear slide looks like it's being dragged.
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: `transform ${SLIDE_MS}ms cubic-bezier(.32,.72,0,1)`,
        }}
      >
        <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: 'var(--text-muted)', fontSize: 12, fontWeight: 800 }}>Sales Agent · 定时发送</div>
            <div id="sales-agent-batch-send-title" style={{ color: 'var(--text-primary)', fontSize: 20, fontWeight: 900, marginTop: 4 }}>
              {multi ? '发给哪一批?' : '确认这一批和时间'}
            </div>
            {/* Counts go under the title, where the eye lands first: what the user must confirm on this drawer is
                "how many to send to, and how many can really be sent"; burying it in the third block of the body makes them hunt for it. */}
            <div style={{ color: 'var(--text-primary)', fontSize: 13.5, fontWeight: 800, marginTop: 6 }}>
              已选 {selectedLeads} 家
              <span style={{ color: previewError ? 'var(--color-danger, #dc2626)' : 'var(--text-secondary)', fontWeight: 700 }}>
                {' · '}{summaryTail}
              </span>
            </div>
          </div>
          <button type="button" aria-label="关闭定时发送" disabled={busy} onClick={() => requestClose()}
                  style={{ ...ghostBtn, width: 34, height: 34, borderRadius: 12, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, opacity: busy ? .5 : 1, cursor: busy ? 'not-allowed' : 'pointer' }}>
            <X size={17} />
          </button>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 18, display: 'grid', alignContent: 'start', gap: 14 }}>
          {/* (1) Pick recipients first, then the time -- the order in users' minds is "to whom -> when",
              and putting time on top makes them answer the second question first. */}
          <div>
            <div style={fieldLabel}>{multi ? '发给哪一批?' : '发给谁'}</div>
            {multi ? (
              <div style={{ display: 'grid', gap: 6 }}>
                {model.batches.map(batch => (
                  <label key={batch.campaignId} style={{ display: 'flex', alignItems: 'center', gap: 9, border: '1px solid var(--border-color)', borderRadius: 12, padding: '10px 12px', cursor: busy ? 'not-allowed' : 'pointer', background: choice === batch.campaignId ? 'var(--bg-tertiary)' : 'transparent' }}>
                    <input type="radio" name="sales-agent-send-batch" disabled={busy} checked={choice === batch.campaignId} onChange={() => setChoice(batch.campaignId)} />
                    <span style={{ fontWeight: 800, fontSize: 13.5, color: 'var(--text-primary)' }}>{batch.label}</span>
                    <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                      {[batch.createdAtText, `${batch.leadCount} 家`].filter(Boolean).join(' · ')}
                    </span>
                  </label>
                ))}
                <label style={{ display: 'flex', alignItems: 'center', gap: 9, border: '1px solid var(--border-color)', borderRadius: 12, padding: '10px 12px', cursor: busy ? 'not-allowed' : 'pointer', background: choice === ALL_BATCHES ? 'var(--bg-tertiary)' : 'transparent' }}>
                  <input type="radio" name="sales-agent-send-batch" disabled={busy} checked={choice === ALL_BATCHES} onChange={() => setChoice(ALL_BATCHES)} />
                  <span style={{ fontWeight: 800, fontSize: 13.5, color: 'var(--text-primary)' }}>全部 {model.batches.length} 批</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{model.totalLeads} 家</span>
                </label>
              </div>
            ) : (
              // With only one batch, don't draw radio buttons: an unchangeable radio takes up a whole block, and users must read it
              // to discover there's no choice. One line stating which batch and how many is enough.
              <div style={{ fontWeight: 800, fontSize: 13.5, color: 'var(--text-primary)' }}>
                {[model.batches[0]?.label, model.batches[0]?.createdAtText, model.batches[0] ? `${model.batches[0].leadCount} 家` : '']
                  .filter(Boolean).join(' · ') || '这一批'}
              </div>
            )}
          </div>

          {/* (2) When to send */}
          <label style={{ display: 'block' }}>
            {/* Don't echo "parsed what you said as X": the parsed result is right there in the input below,
                visible at a glance and directly editable; restating it in a sentence is just noise. */}
            <span style={{ ...fieldLabel, display: 'block' }}>什么时候发(北京时间)</span>
            <input
              type="datetime-local"
              value={scheduledAt}
              min={model.minScheduledAt || undefined}
              disabled={busy}
              onChange={e => setScheduledAt(e.target.value)}
              style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)', borderRadius: 12, padding: '9px 11px', fontSize: 14 }}
            />
          </label>

          {/* The hint area no longer has a border: counts are already in the title, and what remains here is supplementary,
              so another box would be a third block as heavy as the first two. */}
          <div style={{ display: 'grid', gap: 8 }}>
            {previewError && <div style={noteDanger}>{previewError}</div>}
            {warning && <div style={noteDanger}>{warning}</div>}
            {!previewing && !previewError && !blockedByTime && sendable === 0 && (
              <div style={noteDanger}>这批里没有一家现在能发(多半是没有公开邮箱或已退订)。换一批,或者先把联系方式补上。</div>
            )}
            {!warning && !previewError && sendable > 0 && (
              <div style={noteMuted}>发不了的那几家会逐条写明原因,排期之后在发送计划详情里能看到。</div>
            )}
            <div style={noteMuted}>确定之前一封都不会发出去;确定之后到点逐封发,中途还能改时间或整个取消。</div>
          </div>
        </div>

        <div style={{ borderTop: '1px solid var(--border-color)', padding: '12px 18px', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button type="button" disabled={busy} onClick={() => requestClose()} style={{ ...ghostBtn, opacity: busy ? .55 : 1, cursor: busy ? 'not-allowed' : 'pointer' }}>取消</button>
          <button
            type="button"
            disabled={!canConfirm}
            onClick={() => void confirm()}
            style={{ ...primaryBtn, opacity: canConfirm ? 1 : .55, cursor: canConfirm ? 'pointer' : 'not-allowed' }}
          >
            {busy ? '排期中…' : '确定,到点发'}
          </button>
        </div>
      </aside>
    </div>
  );
}
