// GEO optimization Agent -- floating chat window (user-side entry).
// Shown only after login; opened from the bottom-right launcher, SSE streaming conversation.
// Styles use global CSS variables (--accent-* / --bg-* / --text-* / --border-*), automatically following light/dark themes and matching page style.
import { useState, useRef, useEffect, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAuth } from '../../contexts/AuthContext';
import { streamAgentChat, fetchAgentHistory, fetchNotifications, markNotificationsRead, type AgentCard, type AgentUiAction, type AgentPageContext, type AgentConfirm } from '../../services/agentApi';
import { useCurrentTopicId, useAgentScope } from './useCurrentTopicId';
import { isRiskyAction, useAgentActions, useHasAgentActions } from './agentActions';
import type { AgentActionResult } from './agentActions';
import { uiConfirm } from '../ui/dialogs';
import { CardView } from './cards';
import { isWorkbenchPath } from '../../utils/workbenchPath';

// Quick nav: page navigation inside the chat window (a lightweight "find a page for you").
// keywords are matched against user input; on a hit, a "Go to X" button is appended to the conversation.
interface NavLink {
  label: string; path: string; keywords: string[];
  staffOnly?: boolean;   // shown / reported only to workbench staff
  hidden?: boolean;      // not in the bottom quick bar, only in the navigate whitelist
}
// 2026-07 redesign: the brand growth entry is hidden, and data display is consolidated into the "Report center /reports".
// Copy is taken from pages/Reports/lang.ts, paths follow the actual tabs in pages/Reports/shell.tsx.
// ★ This array is also fed to the backend via buildPageContext's nav_targets as the agent's navigable whitelist
//   (see _page_preamble in backend/agents/geo/api.py) -- changing it here takes effect on both sides.
const NAV_LINKS: NavLink[] = [
  { label: '报告总览', path: '/reports', keywords: ['报告', '总览', '看板', '数据', '概览', '指标', '露出率', '中正率', '声量', 'report'] },
  { label: '日报', path: '/reports/daily', keywords: ['日报', '每日', '昨天', '今天数据'] },
  { label: '周报', path: '/reports/weekly', keywords: ['周报', '本周', '每周', '达标'] },
  { label: '月报', path: '/reports/monthly', keywords: ['月报', '本月', '每月', '结案'] },
  { label: '任务监控', path: '/task-monitor', keywords: ['任务', '监控', '跑批', '运行结果', '应答', '原文'] },
  { label: '合规审计', path: '/reports/compliance', keywords: ['合规', '审计', '风控', '违禁词'] },
  { label: '舆情监控', path: '/sentiment', keywords: ['舆情', '监控', '热点', 'sentiment'] },
  { label: '对接集成', path: '/account/integration', keywords: ['对接', '集成', '领号', 'token', '接入', 'api'] },
  { label: '账号设置', path: '/account/profile', keywords: ['账号', '设置', '资料', '品牌设置', '会员', '个人'] },
  // Ops workbench (staffOnly: regular customers neither see the buttons nor get them fed into the model context -- otherwise the model would
  // suggest customers go to /ops/..., where they'd be kicked back to /dashboard, a bad experience that also exposes internal structure).
  // ★ Parameterized paths **must not** go here (e.g. the plan /ops/topics/:topicId/edit?step=5):
  //   with template strings in the catalog, the model would copy the :topicId literal. Such navigation is done via goPlan/goDocs actions registered by the page,
  //   which build the URL internally from the current project id -- also enforcing "the model can't switch projects".
  { label: '项目进度', path: '/ops/cockpit', keywords: ['项目进度', '驾驶舱', '工作台'], staffOnly: true },
  { label: '客户项目', path: '/ops/accounts', keywords: ['客户', '项目列表'], staffOnly: true },
  { label: '文章管理', path: '/ops/content-review', keywords: ['文章管理', '内容审核', '稿件', '审稿', '送审', '退回'], staffOnly: true },
  { label: '优化任务', path: '/ops/action-items', keywords: ['优化任务', '任务中心', '待办'], staffOnly: true },
  { label: '平台规则', path: '/ops/platform-rules', keywords: ['平台规则', '投放规则'], staffOnly: true, hidden: true },
];

function matchNavLinks(text: string, isStaff: boolean): NavLink[] {
  const t = text.toLowerCase();
  return NAV_LINKS.filter((l) => (!l.staffOnly || isStaff) && !l.hidden
    && l.keywords.some((k) => t.includes(k.toLowerCase())));
}

/** One step executed for the user in this turn. pending->(asking)->running->ok/fail/skipped. */
interface ActStep {
  action: string;
  status: 'pending' | 'asking' | 'running' | 'ok' | 'fail' | 'skipped';
  /** Human-readable description from the executor, e.g. "Filter: All -> Pending review". Falls back to the action name if not given. */
  label?: string;
  before?: string;
  after?: string;
  error?: string;
}

/**
 * Highlight the changed element on the left for 1.5 seconds.
 *
 * The easiest link to lose in "talk on the right, change on the left": the page did change, but the user's eyes are still on the right,
 * and by the time they look over there's no trace left, so they have to search. Scrolling into view + outlining costs very little.
 * Use outline, not border -- border changes the box model and nudges the layout.
 */
function flashElement(el: HTMLElement) {
  try {
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    const prev = { outline: el.style.outline, offset: el.style.outlineOffset };
    el.style.outline = '2px solid var(--accent-primary)';
    el.style.outlineOffset = '2px';
    window.setTimeout(() => {
      el.style.outline = prev.outline;
      el.style.outlineOffset = prev.offset;
    }, 1500);
  } catch { /* element unmounted etc.: a failed highlight must not affect the action itself */ }
}

/** Workbench dock width. The same value as --agent-dock-w read by WorkbenchLayout. */
const DOCK_W = 380;

/** How long the launcher is shown in full after entering the workbench (then it auto-shrinks to the edge handle). */
const INTRO_MS = 15000;
/** Collapse animation duration. Keep in sync with .agent-launcher-shrink in index.css, or it gets cut off. */
const INTRO_SHRINK_MS = 320;
/** Flag for "already shown in full this session". Uses sessionStorage: recomputed after closing the tab. */
const INTRO_SEEN_KEY = 'agent_launcher_intro_seen';

interface Msg {
  role: 'user' | 'assistant';
  text: string;
  cards?: AgentCard[];
  confirms?: AgentConfirm[];   // two-step confirmation previews: rendered as highlighted cards with "Confirm / Cancel"
  nav?: NavLink[];
}

/**
 * Two-step confirmation card -- preview of irreversible / paid actions.
 *
 * The "Confirm" button **doesn't send back a token or hit a new endpoint**; it just sends a plain message 「确认执行」 ("confirm") into the conversation:
 * the model takes the token from its own context, so the confirmation path is identical to the user typing "confirm".
 * This way the backend's "can't self-confirm in the same turn" check holds naturally (clicking = a new HTTP request = a new turn).
 */
function ConfirmCard({ confirm, disabled, onDecide }: {
  confirm: AgentConfirm;
  disabled?: boolean;
  onDecide: (yes: boolean) => void;
}) {
  const preview = Object.entries(confirm.preview || {}).slice(0, 8);
  const danger = (confirm.irreversible?.length ?? 0) > 0 || !!confirm.cost;
  return (
    <div style={{
      marginTop: 8, padding: '10px 12px', borderRadius: 12,
      background: 'var(--bg-surface)',
      border: `1px solid ${danger ? 'var(--accent-danger, #d9534f)' : 'var(--accent-secondary)'}`,
    }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: danger ? 'var(--accent-danger, #d9534f)' : 'var(--accent-secondary)', marginBottom: 6 }}>
        {danger ? '⚠️' : '❓'} 待确认:{confirm.action_label}
        {confirm.topic ? <span style={{ fontWeight: 400, color: 'var(--text-secondary)' }}>({confirm.topic.name})</span> : null}
      </div>
      {preview.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 13, padding: '1px 0' }}>
          <span style={{ color: 'var(--text-secondary)' }}>{k}</span>
          <span style={{ fontWeight: 600, textAlign: 'right', wordBreak: 'break-all' }}>
            {typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—')}
          </span>
        </div>
      ))}
      {(confirm.impact?.length ?? 0) > 0 && (
        <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text-secondary)' }}>
          {confirm.impact!.slice(0, 5).map((x, i) => <li key={i}>{x}</li>)}
        </ul>
      )}
      {(confirm.irreversible?.length ?? 0) > 0 && (
        <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--accent-danger, #d9534f)' }}>
          {confirm.irreversible!.slice(0, 4).map((x, i) => <li key={i}>不可撤销:{x}</li>)}
        </ul>
      )}
      {confirm.cost && (
        <div style={{ marginTop: 4, fontSize: 12, color: 'var(--accent-danger, #d9534f)' }}>
          费用:{confirm.cost.amount} {confirm.cost.currency || ''}{confirm.cost.note ? `(${confirm.cost.note})` : ''}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <button
          disabled={disabled}
          onClick={() => onDecide(true)}
          className="transition-opacity hover:opacity-80"
          style={{
            padding: '4px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: 'none',
            cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
            background: danger ? 'var(--accent-danger, #d9534f)' : 'var(--accent-primary)', color: '#fff',
          }}
        >确认执行</button>
        <button
          disabled={disabled}
          onClick={() => onDecide(false)}
          className="transition-opacity hover:opacity-80"
          style={{
            padding: '4px 14px', borderRadius: 8, fontSize: 12,
            cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
            background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-color)',
          }}
        >取消</button>
      </div>
    </div>
  );
}

// Draggable: turns pointer drags on the handle (avatar/title bar) into the container's left/top.
// With pos=null the default anchor (right/bottom) is used; after dragging it switches to absolute coordinates clamped to the viewport.
// movedRef distinguishes "click" from "drag"; after a drag onClick is suppressed (so the window doesn't toggle right after dragging).
function useDraggable() {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const st = useRef<{ sx: number; sy: number; bx: number; by: number; moved: boolean } | null>(null);
  const movedRef = useRef(false);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    st.current = { sx: e.clientX, sy: e.clientY, bx: rect.left, by: rect.top, moved: false };
    movedRef.current = false;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const s = st.current;
    if (!s) return;
    const dx = e.clientX - s.sx, dy = e.clientY - s.sy;
    if (!s.moved && Math.hypot(dx, dy) < 4) return;
    s.moved = true;
    movedRef.current = true;
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth, h = el.offsetHeight;
    const nx = Math.min(Math.max(8, s.bx + dx), window.innerWidth - w - 8);
    const ny = Math.min(Math.max(8, s.by + dy), window.innerHeight - h - 8);
    setPos({ x: nx, y: ny });
  };
  const onPointerUp = (e: React.PointerEvent) => {
    st.current = null;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  };

  return { ref, pos, movedRef, handleProps: { onPointerDown, onPointerMove, onPointerUp } };
}

// ── Markdown rendering: react-markdown + remark-gfm (full GFM: tables/lists/code/links/strikethrough etc.) ──
const mdComponents = {
  table: (p: any) => <div style={{ overflowX: 'auto', margin: '6px 0' }}><table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }} {...p} /></div>,
  th: (p: any) => <th style={{ border: '1px solid var(--border-color)', padding: '4px 8px', textAlign: 'left', background: 'var(--bg-surface)', fontWeight: 600 }} {...p} />,
  td: (p: any) => <td style={{ border: '1px solid var(--border-color)', padding: '4px 8px', textAlign: 'left' }} {...p} />,
  a: (p: any) => <a {...p} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)', textDecoration: 'underline' }} />,
  code: (p: any) => <code style={{ background: 'var(--bg-surface)', padding: '0 4px', borderRadius: 4, fontSize: 12 }} {...p} />,
  ul: (p: any) => <ul style={{ margin: '4px 0', paddingLeft: 18 }} {...p} />,
  ol: (p: any) => <ol style={{ margin: '4px 0', paddingLeft: 18 }} {...p} />,
  li: (p: any) => <li style={{ margin: '1px 0' }} {...p} />,
  p: (p: any) => <p style={{ margin: '3px 0' }} {...p} />,
  h1: (p: any) => <div style={{ fontWeight: 700, fontSize: 15, margin: '6px 0 2px' }} {...p} />,
  h2: (p: any) => <div style={{ fontWeight: 700, margin: '6px 0 2px' }} {...p} />,
  h3: (p: any) => <div style={{ fontWeight: 700, margin: '6px 0 2px' }} {...p} />,
};

function MarkdownView({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{text}</ReactMarkdown>;
}

export function AgentChatWidget() {
  const { isLoggedIn, isStaff } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // Docked on the right, or a draggable overlay.
  //
  // The criterion changed from "is the path /workbench" to "has this page registered actionable actions" --
  // the value of this chat window is **talk on the right, the left changes along**; only operable pages deserve a side-by-side column;
  // read-only pages (home / FAQ / landing pages) keep the overlay and don't squeeze the content.
  // Judging by capability rather than path means new pages benefit automatically once they register actions, without coming back to change this line.
  // Users can force an override (dockPref below); the preference is stored in localStorage.
  const hasPageActions = useHasAgentActions();
  const [dockPref, setDockPref] = useState<'auto' | 'dock' | 'float'>(() => {
    try {
      const v = window.localStorage.getItem('agent_dock');
      return v === 'dock' || v === 'float' ? v : 'auto';
    } catch { return 'auto'; }        // private mode can't read localStorage; use auto
  });
  const docked = dockPref === 'auto' ? hasPageActions : dockPref === 'dock';

  // Whether the launcher should shrink to the edge handle -- **a different question from docked; don't share one criterion again**.
  //
  // docked asks "does the chat window deserve a side-by-side column on the right", judged by whether the page registered actions; that's correct.
  // But "collapse the launcher 15s after entering the workbench" asks **where the person is**: on any workbench page the bottom right is occupied by
  // table actions, and a 64px avatar + "GEO marketing specialist" + 400 hotline sitting on top is in the way.
  // It used to reuse docked, so admin pages like /ops/users and /ops/gateway that **registered no actions**
  // were forever docked=false -- not only never collapsing, the 15s timer never even started, and the big launcher just hung there
  // (user screenshot feedback on /ops/users, 2026-09-01).
  // Judge by path: collapse across the whole workbench; outside the workbench keep as before (pages with registered actions still collapse).
  // ★ The workbench has **two** shells: customer /app and ops /ops. This used to check only /ops, so inside the customer workbench
  //   collapsible depended entirely on docked -- "registered agent actions or not" -- pages without actions were forever false,
  //   the 15s collapse timer never started, and the 64px launcher kept covering the bottom-right action area. The criterion is unified in
  //   utils/workbenchPath; don't write another copy.
  const inWorkbench = isWorkbenchPath(pathname);
  const collapsible = docked || inWorkbench;
  const topicId = useCurrentTopicId();        // current topic (parsed from URL), reported with messages so the agent is context-aware
  const scope = useAgentScope();              // on an ops workbench project page, request the admin lane (access still hard-gated by backend role)
  const agentActions = useAgentActions();     // actionable actions registered by the current page (action bus)
  const launcher = useDraggable();
  const panel = useDraggable();
  // The most recent page action executed for the user. Failures **must be visible** -- silently swallowing errors in the ops console becomes
  // "the agent says it's done, the table didn't change", worse than not executing.
  // Each step executed for the user this turn. **Update one by one** rather than one summary after everything runs --
  // the user wants to "watch it change"; telling them only at the end means they never saw the process.
  const [steps, setSteps] = useState<ActStep[] | null>(null);

  // Auto-dismiss; in dock mode actions have heavier consequences, so keep it longer. No timer while still running, so it doesn't vanish halfway.
  useEffect(() => {
    if (!steps || steps.some((x) => x.status === 'running' || x.status === 'asking')) return;
    const t = setTimeout(() => setSteps(null), docked ? 12000 : 6000);
    return () => clearTimeout(t);
  }, [steps, docked]);

  // Whether the expanded state uses the "right dock": workbench + wide enough viewport (narrow screens fall back to overlay, don't squeeze the content away)
  const [wide, setWide] = useState(() => typeof window === 'undefined' || window.innerWidth >= 1024);
  // Mobile mode (<640px): the launcher doesn't show "GEO marketing specialist", only a dialable phone number.
  const [phone, setPhone] = useState(() => typeof window !== 'undefined' && window.innerWidth < 640);
  useEffect(() => {
    const onR = () => setPhone(window.innerWidth < 640);
    window.addEventListener('resize', onR);
    return () => window.removeEventListener('resize', onR);
  }, []);
  useEffect(() => {
    const onResize = () => setWide(window.innerWidth >= 1024);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Assemble the "current page" context reported to the backend: page name + data snapshot + available action names (always including built-in navigate)
  const buildPageContext = useCallback((): AgentPageContext => {
    const ctx = agentActions?.current() ?? null;
    return {
      page: ctx?.page ?? '',
      describe: ctx?.describe,
      snapshot: ctx?.snapshot ?? {},
      actions: ['navigate', ...(ctx ? Object.keys(ctx.actions) : [])],
      // staffOnly catalog entries aren't fed to regular customers (see the note on NAV_LINKS)
      nav_targets: NAV_LINKS.filter((l) => !l.staffOnly || isStaff).map((l) => ({ label: l.label, path: l.path })),
      route: pathname,   // backend resolve_scope falls back to this when the frontend omits scope
    };
  }, [agentActions, isStaff, pathname]);

  // Execute page actions dispatched by the agent. navigate is a built-in global action; the rest go to executors registered by the current page.
  const runUiActions = useCallback(async (acts: AgentUiAction[]) => {
    const ctx = agentActions?.current() ?? null;
    // Lay out the whole sequence first (all pending), then advance one by one -- the user sees at a glance "how many steps in total, which step now"
    const list: ActStep[] = acts.map((a) => ({ action: a.action, status: 'pending' }));
    setSteps(list);
    const patch = (i: number, v: Partial<ActStep>) =>
      setSteps((prev) => (prev ? prev.map((x, k) => (k === i ? { ...x, ...v } : x)) : prev));

    for (let i = 0; i < acts.length; i += 1) {
      const a = acts[i];
      try {
        if (a.action === 'navigate') {
          const path = String(a.args?.path || '');
          if (!path) throw new Error('缺少目标路径');
          patch(i, { status: 'running' });
          navigate(path);
          patch(i, { status: 'ok', label: `跳转 ${path}` });
          continue;
        }
        // With multiple surfaces mounted, action names carry a namespace prefix (plan.setTab). The model occasionally drops the prefix;
        // here we fall back once by **unique suffix**; if not unique it fails, never guesses.
        let fn = ctx?.actions[a.action];
        if (!fn && ctx) {
          const hit = Object.keys(ctx.actions).filter((k) => k.endsWith(`.${a.action}`));
          if (hit.length === 1) fn = ctx.actions[hit[0]];
          else if (hit.length > 1) throw new Error(`动作名不明确(${hit.join(' / ')})`);
        }
        if (!fn) throw new Error('当前页没有这个动作');

        // Actions that cost money / change the plan ask first. This doesn't duplicate the backend confirm.py two-step confirmation:
        // that gate covers backend writes, this one covers "the agent directly clicked a button on the page".
        if (isRiskyAction(a.action, ctx?.selfConfirm)) {
          patch(i, { status: 'asking' });
          const okToRun = await uiConfirm(
            `助手要替你执行「${a.action}」,这会真的动数据或跑批。确认执行吗?`,
            { title: '助手要动数据', okText: '执行', cancelText: '不执行', danger: true },
          );
          if (!okToRun) { patch(i, { status: 'skipped', label: '你取消了这一步' }); continue; }
        }

        patch(i, { status: 'running' });
        const r = (await fn(a.args || {})) as AgentActionResult | void;
        patch(i, {
          status: 'ok',
          label: r?.label,
          before: r?.before === undefined ? undefined : String(r.before),
          after: r?.after === undefined ? undefined : String(r.after),
        });
        if (r?.el) flashElement(r.el);
      } catch (e) {
        patch(i, { status: 'fail', error: e instanceof Error ? e.message : '执行失败' });
      }
    }
  }, [agentActions, navigate]);

  // Navigate to the target page and collapse the chat window
  const go = useCallback((path: string) => {
    navigate(path);
    setOpen(false);
  }, [navigate]);
  const [open, setOpen] = useState(false);

  // On entering the workbench, show it in full once (avatar + "GEO marketing specialist" + 400 hotline), then auto-shrink to the small edge handle after 15s.
  //
  // The edge handle is only 30x44 with opacity .6, so new users never notice there's someone on the right to ask;
  // but a full launcher hanging there all the time covers the workbench's bottom-right action area -- so it's shown in full only "right after entering".
  //
  // ★ The timer is anchored to **the first time this session enters the collapsible state**, not every flip of collapsible. Its docked part
  //   derives from "has this page registered agent actions" (see hasPageActions above), which briefly drops back to false when
  //   paging within the workbench -- old page unregisters first, new page registers after -- so timing by flips would pop the big launcher on every page change.
  //   Recorded in sessionStorage: shown in full only once per tab; closing the tab and coming back counts as a new time.
  const [intro, setIntro] = useState(false);          // full-display period
  const [introShrinking, setIntroShrinking] = useState(false);   // collapse animation playing
  const introStartedRef = useRef(false);
  const introTimersRef = useRef<number[]>([]);
  useEffect(() => {
    if (!collapsible || introStartedRef.current) return;
    introStartedRef.current = true;
    // Private mode can't read sessionStorage -- treat as "not shown yet"; better to show once more than never
    try { if (window.sessionStorage.getItem(INTRO_SEEN_KEY) === '1') return; } catch { /* unreadable, keep showing */ }
    setIntro(true);
    introTimersRef.current.push(
      window.setTimeout(() => setIntroShrinking(true), INTRO_MS),
      window.setTimeout(() => {
        setIntro(false);
        setIntroShrinking(false);
        try { window.sessionStorage.setItem(INTRO_SEEN_KEY, '1'); } catch { /* if it can't be written, show again next time */ }
      }, INTRO_MS + INTRO_SHRINK_MS),
    );
  }, [collapsible]);
  // The user opened the chat window during the display period -- they've seen it, so end now; don't leave a big launcher hanging after they close it
  useEffect(() => {
    if (!open || !intro) return;
    introTimersRef.current.forEach(window.clearTimeout);
    introTimersRef.current = [];
    setIntro(false);
    setIntroShrinking(false);
    try { window.sessionStorage.setItem(INTRO_SEEN_KEY, '1'); } catch { /* same as above */ }
  }, [open, intro]);
  useEffect(() => () => { introTimersRef.current.forEach(window.clearTimeout); }, []);

  // Shrink to the small edge handle: workbench (or a page with registered actions) + chat window closed + full-display period over
  const collapsed = collapsible && !open && !intro;

  // Workbench + expanded + wide enough viewport -> right dock column (content area yields); otherwise keep the draggable overlay
  const dockPanel = docked && open && wide;
  // When the dock is expanded, make room in the content area: WorkbenchLayout reads this CSS variable as padding-right
  useEffect(() => {
    const root = document.documentElement;
    if (dockPanel) root.style.setProperty('--agent-dock-w', `${DOCK_W}px`);
    else root.style.removeProperty('--agent-dock-w');
    return () => { root.style.removeProperty('--agent-dock-w'); };
  }, [dockPanel]);

  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  // "What it's doing". A heavy task takes tens of seconds to minutes per turn; with only a "Thinking..." indicator,
  // the user can't tell whether it's working or stuck -- in the 2026-08-27 session they asked "is it done yet" five times in a row.
  const [stage, setStage] = useState('');
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: 'assistant', text: '你好,我是 Vigilath GEO 市场专员。可以帮你跑诊断、看根因、查数据,也能带你去对应页面 —— 试试问「发布进度在哪看」,或点下方「快速前往」。' },
  ]);
  const [unread, setUnread] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: 'smooth' });
  }, [msgs, open]);

  // Replay history on first expand -- msgs is pure component state, lost on page refresh, while the backend keeps it.
  // Fill only once (hydrated); afterwards this turn's in-memory messages win, so replay doesn't wash over what was just sent.
  const hydrated = useRef(false);
  useEffect(() => {
    if (!open || hydrated.current || !isLoggedIn) return;
    hydrated.current = true;
    void (async () => {
      const past = await fetchAgentHistory(scope);
      if (!past.length) return;
      setMsgs((m) => [
        ...past.map((h) => ({ role: h.role, text: h.text, cards: h.cards })),
        // With history, drop the greeting -- "Hi, I'm the GEO marketing specialist" sitting under dozens of old turns
        // reads like it's introducing itself all over again. Messages already sent this turn are kept as usual.
        ...m.filter((x, i) => !(i === 0 && x.role === 'assistant' && !x.cards && !x.confirms)),
      ]);
    })();
  }, [open, isLoggedIn, scope]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Proactive notifications: after login, poll the unread count (launcher red dot); when the chat window opens, fetch full text + mark as read.
  useEffect(() => {
    if (!isLoggedIn) return;
    let stop = false;
    const poll = async () => { if (!stop) setUnread((await fetchNotifications()).length); };
    void poll();
    const t = setInterval(poll, 60000);
    return () => { stop = true; clearInterval(t); };
  }, [isLoggedIn]);

  useEffect(() => {
    if (!open || unread === 0) return;
    void (async () => {
      const ns = await fetchNotifications();
      if (ns.length) {
        setMsgs((m) => [...m, ...ns.map((n) => ({ role: 'assistant' as const, text: `⚠️ **${n.title}**\n\n${n.body}` }))]);
        await markNotificationsRead();
      }
      setUnread(0);
    })();
  }, [open, unread]);

  // The actual send. Confirmation card buttons go through here too -- clicking "Confirm" = sending a plain message,
  // exactly the same path as the user typing "confirm", so the backend's cross-turn check holds naturally (new request = new turn).
  const sendText = useCallback(async (raw: string) => {
    const text = raw.trim();
    if (!text || busy) return;
    setBusy(true);
    // Lightweight "find a page": when a nav keyword matches, attach a "Go to X" button to this assistant reply
    const navs = matchNavLinks(text, isStaff).slice(0, 4);
    setMsgs((m) => [...m, { role: 'user', text }, { role: 'assistant', text: '', nav: navs.length ? navs : undefined }]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    await streamAgentChat(text, topicId, buildPageContext(), {
      signal: ctrl.signal,
      onStage: (t) => setStage(t),
      onDelta: (d) => {
        setStage('');            // body has started streaming; the progress hint steps aside
        setMsgs((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, role: 'assistant', text: last.text + d };
          return next;
        });
      },
      // Append rather than overwrite the whole group: within one turn the backend may push in batches (cards -> actions -> confirms),
      // and overwriting would make the earlier batch vanish.
      onCards: (cards) =>
        setMsgs((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, cards: [...(last.cards ?? []), ...cards] };
          return next;
        }),
      onUiActions: (acts) => { void runUiActions(acts); },
      onConfirms: (confirms) =>
        setMsgs((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, confirms: [...(last.confirms ?? []), ...confirms] };
          return next;
        }),
      onError: (msg) =>
        setMsgs((m) => {
          const next = [...m];
          next[next.length - 1] = { role: 'assistant', text: `⚠️ ${msg}` };
          return next;
        }),
    }, scope);
    setStage('');
    setBusy(false);
  }, [busy, topicId, scope, isStaff, buildPageContext, runUiActions]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput('');
    await sendText(text);
  }, [input, busy, sendText]);

  // /sales-agent is the standalone full-screen Sales Agent workbench; don't stack the generic "GEO marketing specialist" launcher on it.
  if (!isLoggedIn || pathname.startsWith('/sales-agent')) return null;

  // Mobile mode: show only the phone number (inline-flex centered + normal line height, fixing the squeeze from lineHeight:1)
  if (phone) {
    return (
      <a
        href="tel:<support-phone>"
        aria-label="客服热线 <support-phone>"
        style={{
          position: 'fixed', zIndex: 1000, right: 16, bottom: 16,
          display: 'inline-flex', alignItems: 'center', gap: 6,
          fontSize: 13, fontWeight: 600, lineHeight: 1.4, whiteSpace: 'nowrap',
          color: '#fff', background: 'var(--accent-gradient, #1a56db)',
          padding: '10px 16px', borderRadius: 999, textDecoration: 'none',
          boxShadow: '0 4px 16px rgba(0,0,0,.22)',
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
        </svg>
        {'<support-phone>'}
      </a>
    );
  }

  return (
    <>
      {/* Workbench: small edge handle that doesn't block actions; click to expand the chat window. The first 15s give way to the full launcher (see intro). */}
      {collapsed && (
        <button
          onClick={() => setOpen(true)}
          aria-label="GEO 市场专员"
          title="GEO 市场专员"
          className="opacity-60 hover:opacity-100 transition-opacity"
          style={{
            position: 'fixed', zIndex: 1000, right: 0, bottom: 152,
            width: 30, height: 44, borderRadius: '10px 0 0 10px', border: 'none', padding: 0,
            background: 'var(--bg-card)', boxShadow: '0 2px 8px rgba(0,0,0,.2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
          }}
        >
          <img src="/image/agent-avatar.png" alt="" aria-hidden="true" draggable={false}
               style={{ width: 24, height: 24, borderRadius: '50%', pointerEvents: 'none' }} />
          {unread > 0 && (
            <span style={{
              position: 'absolute', top: 2, left: 2, width: 10, height: 10,
              borderRadius: 5, background: 'var(--danger-solid)', border: '2px solid var(--bg-card)',
            }} />
          )}
        </button>
      )}

      {/* Launcher: avatar button + "GEO marketing specialist" text below. Draggable to reposition. */}
      {!collapsed && (
      <div
        ref={launcher.ref}
        className={introShrinking ? 'agent-launcher-shrink' : undefined}
        style={{
          // Default anchor: the floating "Book a demo" button is at right:24/bottom:24, so stack the assistant above it to avoid overlap; switch to absolute coordinates after dragging
          position: 'fixed', zIndex: 1000,
          ...(launcher.pos
            ? { left: launcher.pos.x, top: launcher.pos.y }
            : { right: 24, bottom: 88 }),
          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
          touchAction: 'none', userSelect: 'none',
        }}
      >
        <button
          {...launcher.handleProps}
          onClick={() => { if (launcher.movedRef.current) return; setOpen((o) => !o); }}
          aria-label="GEO 市场专员"
          title="点击对话 · 拖动可移动位置"
          className="transition-transform hover:scale-105 active:scale-95"
          style={{
            position: 'relative',
            width: 60, height: 60, borderRadius: '50%', padding: 0,
            border: open ? 'none' : '2.5px solid #fff',
            background: open ? 'var(--accent-gradient)' : 'transparent',
            color: '#fff', cursor: 'grab', overflow: 'visible',
            boxShadow: '0 6px 20px rgba(0,0,0,.22)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          {open ? (
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 9l6 6 6-6" />
            </svg>
          ) : (
            // Marketing specialist avatar (with built-in ring, transparent corners)
            <img
              src="/image/agent-avatar.png"
              alt=""
              aria-hidden="true"
              draggable={false}
              style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '50%', display: 'block', pointerEvents: 'none' }}
            />
          )}
          {!open && unread > 0 && (
            <span style={{
              position: 'absolute', top: -2, right: -2, minWidth: 18, height: 18, padding: '0 5px',
              borderRadius: 9, background: 'var(--danger-solid)', color: '#fff', fontSize: 11, fontWeight: 700,
              lineHeight: '18px', textAlign: 'center', border: '2px solid #fff',
            }}>{unread > 99 ? '99+' : unread}</span>
          )}
        </button>
        {!open && (
          <>
            <span
              style={{
                fontSize: 11, fontWeight: 600, lineHeight: 1, whiteSpace: 'nowrap',
                color: '#fff', background: 'rgba(0,0,0,.55)', padding: '3px 8px', borderRadius: 999,
                boxShadow: '0 2px 8px rgba(0,0,0,.18)', pointerEvents: 'none',
              }}
            >
              GEO 市场专员
            </span>
            {/* Support hotline. The launcher as a whole is draggable; clicks are allowed here separately so phones can dial directly. */}
            <a
              href="tel:<support-phone>"
              onPointerDown={(e) => e.stopPropagation()}
              style={{
                fontSize: 11, fontWeight: 600, lineHeight: 1, whiteSpace: 'nowrap',
                color: '#fff', background: 'rgba(0,0,0,.55)', padding: '3px 8px', borderRadius: 999,
                boxShadow: '0 2px 8px rgba(0,0,0,.18)', textDecoration: 'none',
              }}
            >
              {'<support-phone>'}
            </a>
          </>
        )}
      </div>
      )}

      {/* Chat panel. The title bar can be dragged to move the window. */}
      {open && (
        <div
          ref={panel.ref}
          className="animate-fade-in"
          style={{
            position: 'fixed', zIndex: 1000,
            display: 'flex', flexDirection: 'column',
            background: 'var(--bg-card)',
            // The floating window needs overflow:visible so the avatar can poke out of the title bar; in docked mode the avatar no longer overflows, so use hidden
            // to keep long inner content from breaking the rounded corners/border.
            overflow: dockPanel ? 'hidden' : 'visible',
            // The shadow direction follows the form: the floating window is a hovering card (shadow down), docked is a wall (shadow left).
            // Docked with a downward shadow looks like it floats above the page rather than being embedded on the right.
            boxShadow: dockPanel ? '-8px 0 24px rgba(0,0,0,.16)' : '0 16px 48px rgba(0,0,0,.24)',
            // Workbench: docked to the right edge, with the content area yielding in sync (see the useEffect below).
            // All five ops surfaces are wide/long tables, and when the agent edits the publish table the user needs to see both sides clearly --
            // so it "pushes" rather than "covers", which is also why dragging is disabled in dock mode.
            ...(dockPanel
              ? {
                  top: 0, right: 0, height: '100vh', width: DOCK_W,
                  borderRadius: 0, borderLeft: '1px solid var(--border-color)',
                }
              : {
                  ...(panel.pos ? { left: panel.pos.x, top: panel.pos.y } : { right: 24, bottom: 152 }),
                  width: 384, maxWidth: 'calc(100vw - 48px)', height: 540, maxHeight: 'calc(100vh - 200px)',
                  borderRadius: 16, border: '1px solid var(--border-color)',
                }),
          }}
        >
          {/* Title bar: drag handle (short). Avatar enlarged, half poking below the title bar's bottom edge. */}
          <div
            {...(dockPanel ? {} : panel.handleProps)}
            title={dockPanel ? '' : '按住标题栏可拖动'}
            style={{
              position: 'relative', minHeight: 40,
              // Floating window: the 84 left padding makes room for "the big avatar poking out of the title bar"; in docked mode the avatar is a regular inline one,
              // and keeping this gap would be an inexplicable indent.
              padding: dockPanel ? '10px 14px' : '9px 14px 9px 84px',
              background: 'var(--accent-gradient)', color: '#fff',
              // In docked mode the container has square corners (borderRadius:0); a 16px-rounded title bar would show the background at the two top corners.
              ...(dockPanel ? null : { borderTopLeftRadius: 16, borderTopRightRadius: 16 }),
              fontWeight: 600, fontSize: 15, display: 'flex', alignItems: 'center', gap: dockPanel ? 8 : 0,
              cursor: dockPanel ? 'default' : 'grab', touchAction: 'none', userSelect: 'none',
            }}
          >
            <img
              src="/image/agent-avatar.png"
              alt=""
              aria-hidden="true"
              draggable={false}
              style={{
                borderRadius: '50%', objectFit: 'cover', flexShrink: 0, pointerEvents: 'none',
                // ★ The floating setup has "the big avatar poking half above the title bar's top edge" (top:-32). In docked mode the panel sits at top:0,
                //   so the poking half gets cut off by the viewport -- only half a face remains. Hence docked mode uses a regular small inline avatar.
                ...(dockPanel
                  ? { width: 26, height: 26, border: '1.5px solid rgba(255,255,255,.55)' }
                  : {
                      position: 'absolute', left: 14, top: -32, width: 64, height: 64,
                      border: '2.5px solid var(--bg-card)', boxShadow: '0 4px 10px rgba(0,0,0,.28)',
                    }),
              }}
            />
            <span>GEO 市场专员</span>
            {/* Docked / floating toggle. The auto setting judges by "does this page have actionable actions"; this is the manual override:
                on an operable page wanting to see the full content temporarily -> switch to floating; on a reading page wanting side-by-side -> switch to docked.
                Narrow screens have no docked form (wide=false), so the button isn't shown to avoid a click doing nothing. */}
            {wide && (
              <button
                onClick={() => {
                  const next = docked ? 'float' : 'dock';
                  setDockPref(next);
                  try { window.localStorage.setItem('agent_dock', next); } catch { /* ignore in private mode */ }
                }}
                onPointerDown={(e) => e.stopPropagation()}
                aria-label={docked ? '改为浮窗' : '贴到右侧'}
                title={docked ? '改为浮窗' : '贴到右侧'}
                className="transition-opacity hover:opacity-80"
                style={{
                  marginLeft: 'auto', width: 28, height: 28, borderRadius: 8, flexShrink: 0,
                  border: 'none', background: 'rgba(255,255,255,.18)', color: '#fff', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff"
                     strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                  {docked ? <path d="M9 4v16" /> : <path d="M15 4v16" />}
                </svg>
              </button>
            )}
            {/* Collapse button: stopPropagation to avoid triggering title bar dragging */}
            <button
              onClick={() => setOpen(false)}
              onPointerDown={(e) => e.stopPropagation()}
              aria-label="收起"
              title="收起"
              className="transition-opacity hover:opacity-80"
              style={{
                marginLeft: wide ? 6 : 'auto', width: 28, height: 28, borderRadius: 8, flexShrink: 0,
                border: 'none', background: 'rgba(255,255,255,.18)', color: '#fff', cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
          </div>

          <div ref={bodyRef} style={{ flex: 1, overflowY: 'auto', padding: '16px 14px 14px', background: 'var(--bg-primary)' }}>
            {msgs.map((m, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 10 }}>
                <div
                  style={{
                    maxWidth: dockPanel ? '92%' : '84%', padding: '9px 13px', borderRadius: 14, fontSize: 14, lineHeight: 1.55,
                    whiteSpace: m.role === 'user' ? 'pre-wrap' : 'normal', wordBreak: 'break-word',
                    background: m.role === 'user' ? 'var(--accent-primary)' : 'var(--bg-card)',
                    color: m.role === 'user' ? '#fff' : 'var(--text-primary)',
                    border: m.role === 'user' ? 'none' : '1px solid var(--border-color)',
                    borderBottomRightRadius: m.role === 'user' ? 4 : 14,
                    borderBottomLeftRadius: m.role === 'user' ? 14 : 4,
                  }}
                >
                  {m.role === 'assistant'
                    ? (m.text
                        ? <MarkdownView text={m.text} />
                        : (busy && i === msgs.length - 1
                            ? <span style={{ color: 'var(--text-secondary)' }}>{stage || '思考中…'}</span>
                            : ''))
                    : m.text}
                </div>
                {m.cards?.length ? (
                  <div style={{ width: '100%', maxWidth: '92%' }}>
                    {m.cards.map((c, ci) => (
                      <CardView
                        key={ci}
                        card={c}
                        // Only cards in the last message are interactive -- the same criterion as ConfirmCard below.
                        // If the previous turn's industry term card were still clickable, clicking it would restart an order with stale context.
                        disabled={busy || i !== msgs.length - 1}
                        onSend={(t) => { setInput(''); void sendText(t); }}
                      />
                    ))}
                  </div>
                ) : null}
                {m.confirms?.length ? (
                  <div style={{ width: '100%', maxWidth: '92%' }}>
                    {m.confirms.map((c, ci) => (
                      <ConfirmCard
                        key={ci}
                        confirm={c}
                        disabled={busy || i !== msgs.length - 1}
                        onDecide={(yes) => { setInput(''); void sendText(yes ? '确认执行' : '算了,取消'); }}
                      />
                    ))}
                  </div>
                ) : null}
                {m.nav?.length ? (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8, maxWidth: '92%' }}>
                    {m.nav.map((l) => (
                      <button
                        key={l.path}
                        onClick={() => go(l.path)}
                        className="transition-opacity hover:opacity-80"
                        style={{
                          fontSize: 13, fontWeight: 600, padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
                          border: '1px solid var(--accent-primary)', background: 'transparent', color: 'var(--accent-primary)',
                        }}
                      >
                        前往 · {l.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          {/* Quick nav: persistent page navigation bar (horizontal scroll) */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px', overflowX: 'auto',
            borderTop: '1px solid var(--border-color)', background: 'var(--bg-surface)',
          }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap', flexShrink: 0 }}>快速前往</span>
            {NAV_LINKS.map((l) => (
              <button
                key={l.path}
                onClick={() => go(l.path)}
                className="transition-colors hover:opacity-80"
                style={{
                  fontSize: 12, fontWeight: 500, padding: '4px 10px', borderRadius: 999, cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0,
                  border: '1px solid var(--border-color)', background: 'var(--bg-card)', color: 'var(--text-primary)',
                }}
              >
                {l.label}
              </button>
            ))}
          </div>

          {/* Safety notice bar: after auto-execution, tells the user what was just done for them (so mistakes can be corrected manually in time).
              Failures get their own line in a conspicuous color -- silently swallowing errors becomes "the agent says it's done, the table didn't change". */}
          {/* Visibility of "talk on the right, change on the left": list step by step what was done for the user this turn.
              Reporting only action names (setTab) tells the user nothing; they want **which item, changed from what to what**;
              if the executor returns label / before / after, use them; otherwise fall back to the action name (zero change for old pages). */}
          {steps && steps.length > 0 && (
            <div style={{
              padding: '8px 12px', fontSize: 12, color: 'var(--text-secondary)',
              background: 'var(--bg-surface)', borderTop: '1px solid var(--border-color)',
              maxHeight: 132, overflowY: 'auto',
            }}>
              {steps.map((st, i) => {
                const mark = st.status === 'ok' ? '✅'
                  : st.status === 'fail' ? '⚠️'
                  : st.status === 'skipped' ? '⊘'
                  : st.status === 'asking' ? '❓' : '⏳';
                const tone = st.status === 'fail' ? 'var(--aui-destructive)'
                  : st.status === 'skipped' ? 'var(--text-muted)' : 'var(--text-secondary)';
                return (
                  <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'baseline', color: tone,
                                        lineHeight: 1.7, opacity: st.status === 'pending' ? 0.55 : 1 }}>
                    <span style={{ flexShrink: 0 }}>{mark}</span>
                    <span style={{ minWidth: 0, wordBreak: 'break-word' }}>
                      {st.label || st.action}
                      {st.before !== undefined && st.after !== undefined && (
                        <span style={{ color: 'var(--text-muted)' }}>
                          {' '}{st.before || '(空)'} → <b style={{ color: 'var(--text-primary)' }}>{st.after || '(空)'}</b>
                        </span>
                      )}
                      {st.error && <span> —— {st.error}</span>}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          <div style={{
            display: 'flex', gap: 8, padding: 12,
            borderTop: '1px solid var(--border-color)', background: 'var(--bg-card)',
            // Same as the title bar: the docked container has square corners, so rounding here would show the background at the two bottom corners
            ...(dockPanel ? null : { borderBottomLeftRadius: 16, borderBottomRightRadius: 16 }),
            // Docked is full screen height; the input area follows the safe area so it isn't covered by the phone's bottom bar
            paddingBottom: dockPanel ? 'calc(12px + env(safe-area-inset-bottom, 0px))' : 12,
          }}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }}
              placeholder={busy ? '助手回复中…' : '输入消息…'}
              disabled={busy}
              style={{
                flex: 1, padding: '9px 13px', borderRadius: 10, fontSize: 14, outline: 'none',
                border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)',
              }}
            />
            <button
              onClick={() => void send()}
              disabled={busy || !input.trim()}
              className="transition-opacity"
              style={{
                padding: '9px 18px', borderRadius: 10, border: 'none', fontSize: 14, fontWeight: 600,
                cursor: busy || !input.trim() ? 'default' : 'pointer',
                background: 'var(--accent-primary)', color: '#fff',
                opacity: busy || !input.trim() ? 0.5 : 1,
              }}
            >
              发送
            </button>
          </div>
        </div>
      )}
    </>
  );
}
