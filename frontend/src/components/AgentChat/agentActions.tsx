// Agent action bus: lets the backend agent "control the current page" (fill forms / adjust filters / switch topics / navigate / click buttons).
//
// Mechanism:
//   - Pages that need agent control register "available actions + current data snapshot" globally via useRegisterAgentActions.
//   - When AgentChatWidget sends a message, it reports the current page's page_context (page name + snapshot + action name list) to the backend.
//   - The backend agent calls control_page to return ui_actions instructions, and the widget dispatches them to the registered executors.
//
// 2026-08 changed from "single slot" to "multi-source composition". Reason: on the /ops/topics/:id/edit?step=5 path,
// ExecutionPlanView is the **child** and TopicEditor the **parent**; React runs child effects before parent effects,
// so in the old implementation the later-registered parent overwrote the child's getter entirely -- plan / article management actions never surfaced,
// while the wizard's setField/addSeed/next/prev are useless at step 5. This was hidden and couldn't be avoided by adjusting mount order.
//
// Composition rules:
//   - With a single source, **return it as-is** (old pages' action names unchanged, zero regression);
//   - With multiple sources, prefix with the `${scope}.${action}` namespace, and group snapshot by scope;
//   - Higher weight sorts first; page / describe come from the highest-weight one (= the surface the user is actually looking at).
import { createContext, useCallback, useContext, useEffect, useRef, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';

/** Capabilities a page exposes to the agent. Just pass the latest snapshot/actions on each render -- the bus reads the latest closure via ref, no manual re-registration needed. */
export interface AgentPageActions {
  /** Page identifier (short English name, e.g. 'create_topic' / 'brand_growth'). */
  page: string;
  /** One-line page description for the agent (in Chinese), e.g. 「创建主题表单」 ("create topic form"). */
  describe?: string;
  /** Snapshot of current form/filter values, so the agent knows the current state (e.g. { name:'', target:'https://x.com' }). */
  snapshot?: Record<string, unknown>;
  /** Action name -> executor. The executor receives the args dispatched by the agent (e.g. setField receives { name, value }).
   *
   * Executors **may optionally** return an AgentActionResult to tell the user "what exactly changed" --
   * if not returned, it falls back to the old "show action name only" behavior, with zero change to registered pages. */
  actions: Record<string, (args: Record<string, unknown>) => AgentActionOutcome | Promise<AgentActionOutcome>>;
  /** Action namespace. With multiple surfaces mounted, it prefixes action names and snapshot keys; defaults to page. */
  scope?: string;
  /** Composition weight; higher sorts first (when nested, the inner one gets a higher value). Default 0. */
  weight?: number;
  /**
   * These actions **already show their own confirmation**; the chat window must not show another.
   *
   * The page's own copy is always more accurate than generic copy (replanBySources spells out the "decoupling consequences"),
   * and stacking the generic gate on top means confirming twice -- users click the second one blindly, which weakens the gate.
   * Bare action names are enough; the namespace prefix is added automatically during composition.
   */
  selfConfirm?: string[];
}

/**
 * The optional "what changed" an executor may return.
 *
 * Showing only the action name (setTab / runBatch) tells the user nothing -- they want to know **which item, changed from what to what**,
 * and which block on the left actually moved. The page knows all three best, so the executor brings them back along the way.
 */
export interface AgentActionResult {
  /** Human-readable description, e.g. "Filter: All -> Pending review". If given, it replaces the action name. */
  label?: string;
  before?: unknown;
  after?: unknown;
  /** The changed element. The widget highlights it for 1.5s so the user sees at a glance what changed on the left. */
  el?: HTMLElement | null;
}

export type AgentActionOutcome = void | AgentActionResult;

interface Entry { id: number; getter: () => AgentPageActions | null }

/**
 * Actions that need a second confirmation on the **page side**.
 *
 * Not a duplicate of the backend confirm.py two-step confirmation: that gate covers backend writes (with HMAC tickets),
 * this one covers "the agent directly clicked a button on the page" -- runBatch really runs a batch run, replanBy* reschedules the publish plan,
 * both cost money or change the plan and shouldn't execute on a single natural-language sentence.
 * Prefix matching covers similar actions added later without maintaining a list; the explicit list catches ones that don't match the prefix.
 */
const RISKY_PREFIX = /^(run|replan|submit|delete|remove|publish|approve|reject|send|apply)/i;
const RISKY_NAMES = new Set(['runBatch']);

/** Whether an action name (possibly with a `scope.` prefix) needs page-side confirmation. */
export function isRiskyAction(action: string, selfConfirm?: string[]): boolean {
  const name = String(action || '');
  const bare = name.split('.').pop() || '';
  // If the page shows its own confirmation, the generic gate steps aside -- otherwise it's two clicks, and users click the second one blindly
  if (selfConfirm?.some((x) => x === name || x.split('.').pop() === bare)) return false;
  return RISKY_NAMES.has(bare) || RISKY_PREFIX.test(bare);
}

interface Ctx {
  register: (getter: () => AgentPageActions | null) => () => void;
  current: () => AgentPageActions | null;
  /** Subscribe to changes in "does this page have actionable actions". Used to decide whether the chat window docks on the right. */
  subscribe: (cb: () => void) => () => void;
  /** Cheap check: whether any getter actually returns actions. No composition, no clipSnapshot. */
  hasLive: () => boolean;
  /** Notify manually when a page's actions flip between null <-> non-null (pages that only have actions after data loads). */
  notify: () => void;
}

const AgentActionsContext = createContext<Ctx | null>(null);

/** Cap on composed action names: exceeding it inflates every turn's preamble and lowers tool selection accuracy. Truncated by weight. */
const MAX_ACTIONS = 40;
/** Character budget for the serialized snapshot. It enters the model context every turn **and is persisted in session history**, a permanent cost. */
const MAX_SNAPSHOT_CHARS = 3000;

const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.-]+/g;

/**
 * Fallback snapshot trimming:
 *   - Email addresses are always masked (ContentReview's project dropdown carries customer emails, which would land in ops' own session history via the snapshot);
 *   - Over budget, the longest array is downgraded to "first 3 items + N total"; if still over, the whole block is dropped and marked.
 * Pages must control their own budget too; this is only the last line of defense.
 */
function clipSnapshot(
  o: Record<string, unknown>,
  maxChars = MAX_SNAPSHOT_CHARS,
): Record<string, unknown> {
  const mask = (v: unknown): unknown => {
    if (typeof v === 'string') return v.replace(EMAIL_RE, '***');
    if (Array.isArray(v)) return v.map(mask);
    if (v && typeof v === 'object') {
      return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, mask(x)]));
    }
    return v;
  };
  let out = mask(o) as Record<string, unknown>;
  const size = (x: unknown) => { try { return JSON.stringify(x)?.length ?? 0; } catch { return 0; } };
  if (size(out) <= maxChars) return out;

  // Each round, cut "the array taking the most space" down to the first 3 items + a count, at most 6 rounds
  for (let i = 0; i < 6 && size(out) > maxChars; i += 1) {
    let key: string | null = null;
    let worst = 0;
    for (const [k, v] of Object.entries(out)) {
      if (Array.isArray(v) && v.length > 3 && size(v) > worst) { worst = size(v); key = k; }
    }
    if (!key) break;
    const arr = out[key] as unknown[];
    out = { ...out, [key]: [...arr.slice(0, 3), `…共 ${arr.length} 项(已省略)`] };
  }
  if (size(out) > maxChars) {
    out = { ...out, _clipped: '当前页数据较多,已省略明细;需要时请用对应的列表工具查询' };
    for (const [k, v] of Object.entries(out)) {
      if (size(v) > maxChars / 3) out = { ...out, [k]: '…(已省略)' };
    }
  }
  return out;
}

export function AgentActionsProvider({ children }: { children: ReactNode }) {
  const entriesRef = useRef<Entry[]>([]);
  const seqRef = useRef(0);
  const subsRef = useRef<Set<() => void>>(new Set());

  const notify = useCallback(() => { subsRef.current.forEach((cb) => cb()); }, []);
  const subscribe = useCallback((cb: () => void) => {
    subsRef.current.add(cb);
    return () => { subsRef.current.delete(cb); };
  }, []);
  /** Only asks "are there any", no composition -- getSnapshot is called frequently by React, so clipSnapshot can't run inside it. */
  const hasLive = useCallback(() => entriesRef.current.some((e) => {
    try { return !!e.getter(); } catch { return false; }
  }), []);

  const register = useCallback((getter: () => AgentPageActions | null) => {
    // ★ Unregister by auto-increment id, not by "reference equality": under StrictMode effects run twice,
    //   and reference comparison lets the first cleanup wrongly delete the second registration, showing in dev as "actions sometimes present, sometimes not".
    const id = (seqRef.current += 1);
    entriesRef.current = [...entriesRef.current, { id, getter }];
    notify();
    return () => {
      entriesRef.current = entriesRef.current.filter((e) => e.id !== id);
      notify();
    };
  }, [notify]);

  const current = useCallback((): AgentPageActions | null => {
    const live = entriesRef.current
      .map((e) => { try { return e.getter(); } catch { return null; } })
      .filter((x): x is AgentPageActions => !!x);
    if (live.length === 0) return null;

    const sorted = [...live].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
    if (sorted.length === 1) return sorted[0];   // single-source zero regression: no prefix on action names

    const actions: AgentPageActions['actions'] = {};
    const snapshot: Record<string, unknown> = {};
    const selfConfirm: string[] = [];
    for (const s of sorted) {
      const ns = s.scope || s.page;
      for (const [k, fn] of Object.entries(s.actions)) {
        if (Object.keys(actions).length >= MAX_ACTIONS) break;
        actions[`${ns}.${k}`] = fn;
      }
      if (s.snapshot) snapshot[ns] = s.snapshot;
      for (const k of s.selfConfirm || []) selfConfirm.push(`${ns}.${k}`);
    }
    return {
      page: sorted.map((s) => s.scope || s.page).join('+'),
      describe: sorted.map((s) => s.describe).filter(Boolean).join(';'),
      snapshot: clipSnapshot(snapshot),
      actions,
      selfConfirm,
    };
  }, []);

  return (
    <AgentActionsContext.Provider value={{ register, current, subscribe, hasLive, notify }}>
      {children}
    </AgentActionsContext.Provider>
  );
}

/** Called by pages: register actions on this page that the agent may operate. Automatically unregistered on unmount. */
export function useRegisterAgentActions(actions: AgentPageActions | null) {
  const ctx = useContext(AgentActionsContext);
  const ref = useRef(actions);
  const hadRef = useRef(!!actions);
  // Refresh the ref after every commit so snapshot/executor closures are always the latest (actions are read asynchronously at send time; updating after commit is enough)
  useEffect(() => {
    ref.current = actions;
    // Some pages only provide actions after data loads (passing null before that). The notification at register time doesn't cover
    // this "in-place flip", so notify once more here -- only when null <-> non-null actually changed, avoiding pointless renders.
    if (ctx && hadRef.current !== !!actions) {
      hadRef.current = !!actions;
      ctx.notify();
    }
  });
  useEffect(() => {
    if (!ctx) return;
    return ctx.register(() => ref.current);
  }, [ctx]);
}

/** Called by the widget: read the currently registered page actions. */
export function useAgentActions(): Ctx | null {
  return useContext(AgentActionsContext);
}

/**
 * Whether the current page has actions the agent can operate.
 *
 * The chat window uses it to decide whether to dock on the right: **only operable pages deserve side-by-side**;
 * read-only pages (home / FAQ / landing pages) keep the draggable overlay and don't squeeze the content.
 * Better than hardcoding path prefixes: new pages benefit automatically once they register actions, without changing the criterion.
 */
export function useHasAgentActions(): boolean {
  const ctx = useContext(AgentActionsContext);
  const subscribe = useCallback((cb: () => void) => (ctx ? ctx.subscribe(cb) : () => {}), [ctx]);
  const getSnapshot = useCallback(() => (ctx ? ctx.hasLive() : false), [ctx]);
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
