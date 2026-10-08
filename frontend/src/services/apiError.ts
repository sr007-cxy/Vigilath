/**
 * Shared API error extraction helpers.
 *
 * The backend returns errors in two shapes depending on the exception class:
 *
 * - FastAPI `HTTPException`      → `{"detail": "..."}`
 * - Our custom `AppException`    → `{"error": {"code": 429, "message": "..."}}`
 *
 * Callers used to only read `body.detail || body.message`, so any AppException
 * fell through to the hardcoded English fallback and the real backend message
 * (e.g. "Monthly quota exceeded") never reached the user. These helpers
 * normalize both shapes so pages can surface the real reason the request
 * failed.
 */

export function extractBackendMessage(body: unknown, fallback: string): string {
  if (!isRecord(body)) return fallback;
  const obj = body;
  // FastAPI HTTPException(422, dict) stuffs the whole dict into detail —
  // e.g. _validate_topic_runnable raises {"code": "PROFILE_INCOMPLETE", "message": "资料必填项缺失" ("required materials fields missing"), "missing": [...]}.
  // Previously only string detail was accepted; an object detail fell through to the fallback and the real error was swallowed.
  const fromDetail =
    typeof obj.detail === 'string'
      ? obj.detail
      : isRecord(obj.detail) && typeof obj.detail.message === 'string'
        ? obj.detail.message
        : null;
  // FastAPI's 422 is detail: [{loc, msg, type}, ...] -- an array.
  // It used to be neither a string nor a record, so it fell straight into the fallback, and users saw
  // an uninformative fallback like "Request POST /accounts failed",
  // while the real reason ("ticker 不能为空" ("ticker must not be empty") / "邮箱格式不对" ("invalid email format")) was swallowed entirely.
  const fromValidation = Array.isArray(obj.detail) ? formatValidationDetail(obj.detail) : null;
  const fromNested =
    isRecord(obj.error) && typeof obj.error.message === 'string'
      ? obj.error.message
      : null;
  const fromMessage = typeof obj.message === 'string' ? obj.message : null;
  return fromDetail || fromValidation || fromNested || fromMessage || fallback;
}

/** pydantic validation errors -> readable "field: reason" text (at most 3, the rest elided). */
function formatValidationDetail(items: unknown[]): string | null {
  const lines: string[] = [];
  for (const item of items) {
    if (!isRecord(item)) continue;
    const msg = typeof item.msg === 'string' ? item.msg : null;
    if (!msg) continue;
    // loc = ["body", "ticker"] / ["body", "notify_emails", 0]; strip the body prefix
    const loc = Array.isArray(item.loc)
      ? item.loc.filter(p => p !== 'body').map(String).join('.')
      : '';
    lines.push(loc ? `${loc}: ${msg}` : msg);
  }
  if (lines.length === 0) return null;
  const shown = lines.slice(0, 3).join('；');
  return lines.length > 3 ? `${shown}(共 ${lines.length} 项)` : shown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object';
}

/** Machine-readable code in a backend error (`detail.code` / `error.code`). null if absent. */
export function extractBackendCode(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const obj = body as Record<string, any>;
  const d = obj.detail;
  if (d && typeof d === 'object' && typeof d.code === 'string') return d.code;
  if (obj.error && typeof obj.error === 'object' && typeof obj.error.code === 'string') {
    return obj.error.code;
  }
  return typeof obj.code === 'string' ? obj.code : null;
}

/** Error carrying a business code. Use it when callers need to branch on the code (e.g. insufficient balance should offer a top-up entry). */
export class BackendError extends Error {
  code: string | null;
  status: number;
  constructor(message: string, code: string | null, status: number) {
    super(message);
    this.name = 'BackendError';
    this.code = code;
    this.status = status;
  }
}

/** The single sentence shown to users when the server/gateway misbehaves. The cause goes to the console, not the UI. */
export const SERVICE_ERROR_MESSAGE = '服务错误,请稍后重试';

/** All 5xx count as "the service isn't working right now" -- 502/503/504 are especially normal during deploy restarts. */
function isServiceError(status: number): boolean {
  return status >= 500;
}

/**
 * Whether the response body is a full HTML page (nginx's 502/504 error page, the index.html sent back by the SPA fallback).
 *
 * Why it must be intercepted: `readApiError` used to return non-JSON responses as-is via `response.text()`,
 * so during the few seconds of a deploy restart, what appeared in the user's UI was
 * `<html><head><title>502 Bad Gateway</title>...</head>...nginx/1.18.0 (Ubuntu)...`
 * an entire HTML page -- incomprehensible, and leaking the gateway model and internal network topology to users.
 */
function looksLikeHtml(text: string): boolean {
  const head = text.slice(0, 200).toLowerCase();
  return head.startsWith('<!doctype') || head.startsWith('<html') || head.includes('<title>');
}

export async function throwApiError(response: Response, fallback: string): Promise<never> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON response (gateway 502 and the like) -- body stays null, and the service error/fallback text below applies
  }
  throw new BackendError(
    isServiceError(response.status)
      ? extractBackendMessage(body, SERVICE_ERROR_MESSAGE)
      : extractBackendMessage(body, fallback),
    extractBackendCode(body),
    response.status,
  );
}

export async function readApiError(response: Response, fallback: string): Promise<string> {
  try {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return normalizeBackendMessage(extractBackendMessage(await response.json(), fallback), response.status);
    }
    const text = (await response.text()).trim();
    // ★ Non-JSON response bodies are **never echoed**. They're either a gateway error page or the SPA fallback's
    //   index.html -- both full HTML pages with zero information for users. The original goes to the console.
    if (!text || looksLikeHtml(text) || isServiceError(response.status)) {
      if (text) console.warn('[api] Non-JSON error response (hidden):', response.status, text.slice(0, 300));
      return normalizeBackendMessage('', response.status, fallback);
    }
    return normalizeBackendMessage(text, response.status, fallback);
  } catch {
    return normalizeBackendMessage('', response.status, fallback);
  }
}

function normalizeBackendMessage(message: string, status: number, fallback = ''): string {
  const text = (message || '').trim();
  // No details for service errors: all users can do is wait and retry, and every extra word is noise.
  if (isServiceError(status)) return SERVICE_ERROR_MESSAGE;
  if (status === 404 && /^not found$/i.test(text)) return '接口不存在或后端路由未加载';
  if (/^not found$/i.test(text)) return '未找到对应资源';
  return text || fallback.trim() || `HTTP ${status}`;
}

/**
 * Anything caught -> one sentence that can be shown to users.
 *
 * Covers three kinds: BackendError (already normalized), TypeError thrown by fetch (offline / service not up,
 * the browser only gives "Failed to fetch"), and any other Error. Full HTML pages are caught once more here --
 * some call sites do their own `await resp.text()` and then `throw new Error(body)`.
 */
export function friendlyErrorMessage(err: unknown, fallback = SERVICE_ERROR_MESSAGE): string {
  if (err instanceof BackendError) return err.message || fallback;
  const raw = err instanceof Error ? err.message : String(err ?? '');
  const text = raw.trim();
  if (!text) return fallback;
  if (looksLikeHtml(text)) {
    console.warn('[api] HTML error body (hidden):', text.slice(0, 300));
    return SERVICE_ERROR_MESSAGE;
  }
  // The browser's raw text for "can't connect" -- passing it to users literally says nothing
  if (/failed to fetch|networkerror|load failed/i.test(text)) return SERVICE_ERROR_MESSAGE;
  return text;
}

/** Axios variant: pulls `error.response.data` and runs the same extraction. */
export function extractAxiosErrorMessage(error: unknown, fallback: string): string {
  const data = isRecord(error) && isRecord(error.response) ? error.response.data : undefined;
  return extractBackendMessage(data, fallback);
}

/**
 * Current UI language as stored by i18next. Used to set `X-Locale` on outgoing
 * API requests so the backend can return localized error messages (see
 * `backend/geo/utils/error_i18n.py`). Falls back to "zh" because that is the
 * i18next default in `frontend/src/i18n/index.ts`.
 */
export function currentLocale(): string {
  try {
    return localStorage.getItem('i18nextLng') || 'zh';
  } catch {
    return 'zh';
  }
}

/**
 * Build request headers with the locale attached. Accepts any extra headers
 * (auth tokens, content type) and merges them on top of the locale header.
 */
export function localizedHeaders(
  extra: Record<string, string> = {},
): Record<string, string> {
  return { 'X-Locale': currentLocale(), ...extra };
}
