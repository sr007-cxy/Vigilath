// GEO optimization agent — conversational backend (separate service, nginx reverse-proxies /api/agent/*).
// chat streams via SSE: POST + ReadableStream (EventSource doesn't support a custom Authorization header).

import { localizedHeaders } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface AgentCard {
  tool: string;
  data: Record<string, unknown>;
}

/** Page action command issued by the agent: action = action name, args = arguments. */
export interface AgentUiAction {
  action: string;
  args?: Record<string, unknown>;
}

/** "Current page" context reported with each message, so the agent can operate the UI. */
export interface AgentPageContext {
  page: string;
  describe?: string;
  snapshot?: Record<string, unknown>;
  actions: string[];   // Action names available on the current page (including the built-in navigate)
  nav_targets?: { label: string; path: string }[];   // Directory of in-site targets available to navigate, so the agent jumps to the right path
  route?: string;      // Current path. Backend resolve_scope falls back on it to determine the ops console line when the frontend omits scope
}

/** Two-step confirmation preview: irreversible/paid actions return this first, and the agent only really executes after the user replies "confirm". */
export interface AgentConfirm {
  action: string;
  action_label: string;
  topic?: { id: number; name: string };
  preview?: Record<string, unknown>;
  impact?: string[];
  irreversible?: string[];
  cost?: { currency?: string; amount?: string; note?: string } | null;
  expires_in?: number;
}

export interface ChatCallbacks {
  onDelta: (text: string) => void;
  /** "What it's doing now" -- when a tool node starts running, push a plain-language sentence, so a long round isn't just a lone "Thinking...". */
  onStage?: (text: string) => void;
  onCards?: (cards: AgentCard[]) => void;       // Data tool returns -> structured cards
  onUnauthorized?: () => void;                  // 401: the caller decides whether to re-login or show a hint
  onUiActions?: (actions: AgentUiAction[]) => void;  // control_page commands -> executed by the frontend
  onConfirms?: (confirms: AgentConfirm[]) => void;   // Two-step confirmation preview -> render confirmation cards
  onDone?: () => void;
  onError?: (msg: string) => void;
  signal?: AbortSignal;
}

/** Streaming conversation. Send message to the agent, calling onDelta per token. */
export async function streamAgentChat(
  message: string,
  topicId: number | null,
  pageContext: AgentPageContext | null,
  cb: ChatCallbacks,
  scope?: 'admin',
  /** Override login credentials. The WeCom passwordless page holds a jwt exchanged via OAuth, not in localStorage. */
  authToken?: string,
): Promise<void> {
  const token = authToken ?? localStorage.getItem('token');
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/agent/chat`, {
      method: 'POST',
      headers: localizedHeaders({
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token ?? ''}`,
      }),
      body: JSON.stringify({ message, topic_id: topicId, page_context: pageContext, scope }),
      signal: cb.signal,
    });
  } catch (e) {
    cb.onError?.(e instanceof Error ? e.message : '网络错误');
    return;
  }

  if (resp.status === 401) {
    // Separate callback for 401: the WeCom passwordless page relies on it to redo OAuth, rather than showing "network error"
    if (cb.onUnauthorized) { cb.onUnauthorized(); return; }
    cb.onError?.('未登录或登录已过期');
    return;
  }
  if (resp.status === 503) { cb.onError?.('Agent 服务暂不可用'); return; }
  if (!resp.ok || !resp.body) { cb.onError?.(`请求失败(${resp.status})`); return; }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() ?? '';
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        try {
          const obj = JSON.parse(payload) as { delta?: string; stage?: string; cards?: AgentCard[]; ui_actions?: AgentUiAction[]; confirms?: AgentConfirm[]; error?: string; done?: boolean };
          if (obj.delta) cb.onDelta(obj.delta);
          else if (obj.stage) cb.onStage?.(obj.stage);
          else if (obj.cards) cb.onCards?.(obj.cards);
          else if (obj.ui_actions) cb.onUiActions?.(obj.ui_actions);
          else if (obj.confirms) cb.onConfirms?.(obj.confirms);
          else if (obj.error) cb.onError?.(obj.error);
          else if (obj.done) { cb.onDone?.(); return; }
        } catch { /* Skip non-JSON lines */ }
      }
    }
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') cb.onError?.((e as Error).message);
    return;
  }
  cb.onDone?.();
}

/** A historical message for replay (only user-visible things: body + cards). */
export interface AgentHistoryMsg {
  role: 'user' | 'assistant';
  text: string;
  cards?: AgentCard[];
}

/**
 * Fetch the last few rounds of history to fill the window when the chat mounts.
 *
 * The frontend msgs are pure component state: one refresh or route change and the whole conversation is gone -- while the backend has kept it all along.
 * What users feel is "I just discussed the diagnosis with it, and after a refresh it remembers nothing", so they ask again and place another order.
 */
export async function fetchAgentHistory(scope?: 'admin', limit = 20): Promise<AgentHistoryMsg[]> {
  const token = localStorage.getItem('token');
  try {
    const qs = new URLSearchParams({ limit: String(limit), ...(scope ? { scope } : {}) });
    const r = await fetch(`${API_BASE}/agent/history?${qs}`, {
      headers: { Authorization: `Bearer ${token ?? ''}` },
    });
    if (!r.ok) return [];
    const d = await r.json();
    return (d.messages as AgentHistoryMsg[]) || [];
  } catch {
    return [];      // Failing to fetch history shouldn't block the chat window from opening
  }
}

export interface AgentNotification {
  id: number;
  title: string;
  body: string;
  created_at: string | null;
}

/** Fetch unread proactive notifications (high-risk sentiment monitoring alerts etc.). */
export async function fetchNotifications(): Promise<AgentNotification[]> {
  const token = localStorage.getItem('token');
  try {
    const r = await fetch(`${API_BASE}/agent/notifications`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!r.ok) return [];
    const d = await r.json();
    return (d.notifications as AgentNotification[]) || [];
  } catch {
    return [];
  }
}

/** Mark all unread as read. */
export async function markNotificationsRead(): Promise<void> {
  const token = localStorage.getItem('token');
  try {
    await fetch(`${API_BASE}/agent/notifications/read`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch { /* Ignore */ }
}
