// "Monitoring & batch runs" panel -- everything a user can control about a topic's batch runs is on this one card:
//   (1) which models x which client (one row for browser / one for mobile)
//   (2) cadence (manual / daily / weekly) + weekday and time to start + pause
//   (3) batch run (run new / run everything once, seeing the cost before starting)   (4) current status and this round's details
//
// Mounted in two places with the same logic: topic settings page -> top of the prompts tab; top of wizard step 4.
// From 2026-09-21 new topics default to "manual" (no periodic batch runs), so this card is the **only** entry for users to start a run;
// "Generate GEO strategy optimization plan" only generates the plan and no longer triggers a batch run along the way.
//
// Layout (rearranged 2026-09-30): each section is one row of "label on the left + controls on the right", separated by thin dividers.
// Previously all four sections were same-level subheadings + a pile of same-size chips, with cadence, client and batch run buttons mixed together with no
// hierarchy; now the label column has a fixed width that folds into two rows on narrow screens, and the main button (run new) is the only solid button.
//
// Client (2026-09-30): the same model can run once in the browser and once on mobile, **each counting as one Q&A**.
// The browser row submits the model name itself (whether it goes via the server account pool / office physical device / Doubao mobile web is decided by ops,
// and backend _merge_user_engines keeps the channel key ops chose); the mobile row submits {model}_app,
// executed by the dispatch center's region=android emulator fleet (V-networks/workers/emulator-cn).
//
// Read: GET /topics/{id}/schedule (cadence / engines / anchor / unit price) + GET /topics/{id}/progress
//     (stage / running progress / balance) + GET /topics/{id}/run-quote (quotes under both metric definitions)
// Write: PATCH /topics/{id}/schedule (user side, owner permission, not the ops console)
// Run: POST /topics/{id}/run?only_new=
// All copy criteria come from the backend (topic_progress / topic_schedule.describe); not re-derived here.
import { useCallback, useEffect, useRef, useState } from 'react';
import { aiTelemetryApi, type RunQuote, type TopicProgress } from '../services/aiTelemetryApi';
import { topicProfileApi, type SchedulePatch, type TopicScheduleInfo } from '../services/topicProfileApi';
import {
  CHANNELS, ENGINES, engineChannel, engineKeyFor, normalizeEngine,
  type ChannelKey, type EngineKey,
} from '../constants/engines';
import { TopicProgressStrip, RunDetail } from './TopicProgressStrip';
import { toast } from './ui/toast';

const TIERS: { key: 'off' | 'daily' | 'weekly'; label: string; hint: string }[] = [
  { key: 'off', label: '手动', hint: '不自动跑,只在下面点按钮时跑一轮' },
  { key: 'daily', label: '每天一轮', hint: '数据最新,消耗也最快' },
  { key: 'weekly', label: '每周一轮', hint: '每周固定一天开跑' },
];
const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const HOURS = Array.from({ length: 24 }, (_, h) => h);

/** One section = left label + right controls. Folds into two rows on narrow screens so the label doesn't take half the screen width. */
function Row({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-3">
      <div className="shrink-0 sm:w-[76px] sm:pt-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}
           title={hint}>
        {label}
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">{children}</div>
    </div>
  );
}

export function MonitorRunPanel({ topicId, token, readOnly = false }: {
  topicId: number; token: string; readOnly?: boolean;
}) {
  const [info, setInfo] = useState<TopicScheduleInfo | null>(null);
  const [progress, setProgress] = useState<TopicProgress | null>(null);
  const [quoteNew, setQuoteNew] = useState<RunQuote | null>(null);
  const [quoteAll, setQuoteAll] = useState<RunQuote | null>(null);
  const [busy, setBusy] = useState(false);       // PATCH in progress
  const [running, setRunning] = useState(false); // triggering batch run
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const timerRef = useRef<number | null>(null);
  const ticksRef = useRef(0);

  const loadSchedule = useCallback(async () => {
    try { setInfo(await topicProfileApi.getSchedule(topicId, token)); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [topicId, token]);

  const loadQuotes = useCallback(async () => {
    const [n, a] = await Promise.all([
      aiTelemetryApi.getRunQuote(topicId, token, true).catch(() => null),
      aiTelemetryApi.getRunQuote(topicId, token, false).catch(() => null),
    ]);
    setQuoteNew(n); setQuoteAll(a);
  }, [topicId, token]);

  // Progress polling, copied from the old TopicRunStatusCard: every 5s while running; when pending start, watch at most two minutes (a just-triggered round
  // often hasn't written its run row yet); other states don't change on their own, so stop once fetched.
  const stopPolling = useCallback(() => {
    if (timerRef.current) { window.clearInterval(timerRef.current); timerRef.current = null; }
  }, []);
  const startPolling = useCallback(() => {
    stopPolling();
    ticksRef.current = 0;
    let stale = false;
    const tick = async () => {
      try {
        const next = await aiTelemetryApi.getProgress(topicId, token);
        if (stale) return;
        setProgress(next);
        ticksRef.current += 1;
        const keep = next.stage === 'running' || (next.stage === 'ready' && ticksRef.current < 24);
        if (!keep) {
          stopPolling();
          // The quote changes after a run (new cells were run), so refresh it along the way
          void loadQuotes();
        }
      } catch {
        if (!stale) { setProgress(null); stopPolling(); }
      }
    };
    void tick();
    timerRef.current = window.setInterval(tick, 5000);
    return () => { stale = true; };
  }, [topicId, token, stopPolling, loadQuotes]);

  useEffect(() => {
    void loadSchedule();
    void loadQuotes();
    const cancel = startPolling();
    return () => { cancel(); stopPolling(); };
  }, [loadSchedule, loadQuotes, startPolling, stopPolling]);

  const patch = async (p: SchedulePatch) => {
    if (busy || readOnly) return;
    setBusy(true); setErr(null);
    try {
      setInfo(await topicProfileApi.patchSchedule(topicId, p, token));
      // When engines change, the "x N engines" in the quote and progress must change too
      void loadQuotes();
      try { setProgress(await aiTelemetryApi.getProgress(topicId, token)); } catch { /* progress unavailable doesn't block */ }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const run = async (onlyNew: boolean) => {
    if (running || readOnly) return;
    setRunning(true); setErr(null); setNotice(null);
    try {
      const r = await aiTelemetryApi.triggerRun(topicId, token, { onlyNew });
      if (r.status === 'nothing_new') setNotice('提示词没有新增,这次不跑、不扣积分。');
      else if (r.status === 'started') setNotice(`已开始跑 ${r.cells ?? '?'} 次问答,跑完自动出报告,不用守着。`);
      else setNotice(r.message || `状态:${r.status}`);
      startPolling();
      void loadQuotes();
    } catch (e: unknown) {
      // For 402 (insufficient credits) the backend already gives a readable message; the old "Audit now" button had no catch, so clicking did nothing
      toast.error(e instanceof Error ? e.message : String(e));
      void loadQuotes();
    } finally { setRunning(false); }
  };

  if (!info && !err) return null;

  const isRunning = progress?.stage === 'running';
  const paused = info?.状态 === 'paused';
  const tier = paused ? null : info?.状态;
  const anchor = info?.锚点;

  // Raw engine keys in the DB -> the "(model, client)" checkbox set. Browser-row keys may be channel variants chosen by ops
  // (qwen_browser / doubao_mobile), so first normalizeEngine folds them back to the model, then they're placed by client.
  const picked = new Set<string>();   // `${channel}:${model}`
  for (const raw of info?.引擎 ?? []) {
    picked.add(`${engineChannel(raw)}:${normalizeEngine(raw)}`);
  }
  const isPicked = (m: EngineKey, ch: ChannelKey) => picked.has(`${ch}:${m}`);

  /** Checkbox set -> engine keys submitted to the backend (browser row gives model names, mobile row gives {model}_app). */
  const keysFrom = (set: Set<string>): string[] => {
    const out: string[] = [];
    for (const ch of CHANNELS) {
      for (const e of ENGINES) {
        if (set.has(`${ch.key}:${e.key}`)) out.push(engineKeyFor(e.key, ch.key));
      }
    }
    return out;
  };

  const toggle = (m: EngineKey, ch: ChannelKey) => {
    const next = new Set(picked);
    const id = `${ch}:${m}`;
    if (next.has(id)) {
      if (next.size <= 1) { setErr('至少保留 1 个模型'); return; }
      next.delete(id);
    } else {
      next.add(id);
    }
    setErr(null);
    void patch({ engines: keysFrom(next) });
  };

  const chip = (active: boolean, disabled: boolean) => ({
    background: active ? 'var(--accent-primary)' : 'transparent',
    color: active ? '#fff' : 'var(--text-secondary)',
    border: '1px solid var(--border-color)',
    opacity: disabled ? 0.5 : 1,
  });
  const selectStyle = {
    background: 'var(--bg-input)', border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  };
  const divider = { borderTop: '1px solid var(--border-color)' };
  const engineDisabled = busy || readOnly || isRunning;

  return (
    <div className="rounded-md"
         style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      {/* Card header */}
      <div className="flex items-center justify-between gap-2 flex-wrap px-3 py-2.5">
        <div className="text-sm font-medium text-primary">监测与跑批</div>
        <div className="text-xs" style={{ color: isRunning ? 'var(--success-fg)' : 'var(--text-muted)' }}>
          {isRunning ? '● 监测中' : info?.在跑 ? '● 已排入周期' : '○ 未在跑'}
        </div>
      </div>

      {err && (
        <div className="px-3 pb-2 text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>
      )}

      {/* (1) Which models x which client -- one row for browser, one for mobile */}
      <div className="px-3 py-2.5 space-y-2" style={divider}>
        {CHANNELS.map(ch => (
          <Row key={ch.key} label={ch.zh} hint={ch.hint_zh}>
            <div className="flex gap-1.5 flex-wrap">
              {ENGINES.map(e => {
                const active = isPicked(e.key, ch.key);
                return (
                  <button key={e.key} type="button" disabled={engineDisabled}
                          title={isRunning ? '这一轮还在跑,跑完再改' : `${ch.zh} · ${e.zh}`}
                          onClick={() => toggle(e.key, ch.key)}
                          className="text-xs px-2.5 py-1.5 rounded-md"
                          style={chip(active, engineDisabled)}>
                    {active ? '✓ ' : ''}{e.zh}
                  </button>
                );
              })}
            </div>
          </Row>
        ))}
        <div className="text-xs sm:pl-[88px]" style={{ color: 'var(--text-muted)' }}>
          {isRunning
            ? '这一轮还在跑,跑完再改模型。'
            : '同一个模型两排都能勾,浏览器采网页版、移动端采手机 App,各算一次问答。'}
        </div>
      </div>

      {/* (2) Cadence and start time */}
      <div className="px-3 py-2.5 space-y-2" style={divider}>
        <Row label="节奏">
          <div className="flex gap-1.5 flex-wrap items-center">
            {TIERS.map(o => (
              <button key={o.key} type="button" disabled={busy || readOnly} title={o.hint}
                      onClick={() => void patch({ schedule_tier: o.key, clear_pause: true })}
                      className="text-xs px-2.5 py-1.5 rounded-md"
                      style={chip(tier === o.key, busy || readOnly)}>
                {o.label}
              </button>
            ))}
            {!readOnly && (paused ? (
              <button type="button" disabled={busy}
                      onClick={() => void patch({ clear_pause: true })}
                      className="text-xs px-2.5 py-1.5 rounded-md disabled:opacity-50"
                      style={{ color: 'var(--accent-primary)', border: '1px solid var(--accent-primary)' }}>
                立即恢复
              </button>
            ) : (
              <button type="button" disabled={busy || tier === 'off'}
                      onClick={() => void patch({ pause_days: 7 })}
                      className="text-xs px-2.5 py-1.5 rounded-md disabled:opacity-50"
                      style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
                暂停 7 天
              </button>
            ))}
          </div>
        </Row>

        {tier !== 'off' && anchor && (
          <Row label="开跑时间">
            <div className="flex gap-2 flex-wrap items-center text-xs" style={{ color: 'var(--text-secondary)' }}>
              {tier === 'weekly' && (
                <select value={anchor.weekday} disabled={busy || readOnly}
                        onChange={e => void patch({ cycle_anchor_weekday: Number(e.target.value) })}
                        className="px-2 py-1 rounded text-xs" style={selectStyle}>
                  {WEEKDAYS.map((w, i) => <option key={i} value={i}>{w}</option>)}
                </select>
              )}
              <select value={anchor.hour} disabled={busy || readOnly}
                      onChange={e => void patch({ cycle_anchor_hour: Number(e.target.value) })}
                      className="px-2 py-1 rounded text-xs" style={selectStyle}>
                {HOURS.map(h => (
                  <option key={h} value={h}>{String(h).padStart(2, '0')}:{String(anchor.minute).padStart(2, '0')}</option>
                ))}
              </select>
              <span className="text-muted">北京时间</span>
              {anchor.自定义 ? (
                !readOnly && (
                  <button type="button" disabled={busy}
                          onClick={() => void patch({ cycle_anchor_weekday: -1, cycle_anchor_hour: -1 })}
                          className="text-xs underline text-muted disabled:opacity-50">
                    恢复平台默认
                  </button>
                )
              ) : <span className="text-muted">(平台默认)</span>}
            </div>
          </Row>
        )}

        <div className="text-xs sm:pl-[88px]" style={{ color: 'var(--text-muted)' }}>
          {info?.开跑时间}。开跑时间是排队起点,实际按各引擎产能按小时派发;节奏与时间改完从下一个周期起生效。
        </div>
      </div>

      {/* (3) Batch run */}
      <div className="px-3 py-2.5 space-y-2" style={divider}>
        <Row label="跑一轮">
          <div className="flex gap-2 flex-wrap items-center">
            <button type="button"
                    disabled={running || readOnly || isRunning || !quoteNew || quoteNew.cells === 0}
                    onClick={() => void run(true)}
                    className="text-xs px-3 py-1.5 rounded-md text-white disabled:opacity-40"
                    style={{ background: 'var(--accent-primary)' }}>
              {running ? '…'
                : !quoteNew || quoteNew.cells === 0 ? '跑新增(没有新提示词)'
                : `跑新增的 ${quoteNew.cells} 次问答${quoteNew.chargeable ? `(约 ${quoteNew.need_cents} 积分)` : ''}`}
            </button>
            <button type="button"
                    disabled={running || readOnly || isRunning || !quoteAll || quoteAll.cells === 0}
                    onClick={() => void run(false)}
                    className="text-xs px-3 py-1.5 rounded-md disabled:opacity-40"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
                             border: '1px solid var(--border-color)' }}>
              {running ? '…'
                : `全部跑一轮${quoteAll ? `(${quoteAll.cells} 次问答${quoteAll.chargeable ? `,约 ${quoteAll.need_cents} 积分` : ''})` : ''}`}
            </button>
          </div>
          {quoteAll?.chargeable && (
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
              每次问答 {quoteAll.price_cents} 积分 · 余额 {quoteAll.balance_cents} 积分
              {quoteNew && quoteNew.cells > 0 && quoteNew.balance_cents < quoteNew.need_cents && (
                <span style={{ color: '#dc2626' }}> · 跑新增还差 {quoteNew.need_cents - quoteNew.balance_cents} 积分</span>
              )}
              {' '}· 开跑前先扣整轮,没跑成的问答收口后退回
            </div>
          )}
          {isRunning && (
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>这一轮还在跑,跑完才能再触发。</div>
          )}
          {notice && (
            <div className="text-xs rounded-md p-2"
                 style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)',
                          color: 'var(--text-secondary)' }}>
              {notice}
            </div>
          )}
        </Row>
      </div>

      {/* (4) Status and this round's details */}
      {progress && (
        <div className="px-3 py-2.5" style={divider}>
          <TopicProgressStrip p={progress} title="当前状态">
            <RunDetail p={progress} />
          </TopicProgressStrip>
        </div>
      )}
    </div>
  );
}
