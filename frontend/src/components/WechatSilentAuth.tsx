import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { wechatAuthApi } from '../services/wechatAuthApi';

// Whether silent authorization has been attempted in this session. Prevents infinite loops: when authorization fails (new user need_phone, channel not configured)
// the token is still empty, and without this gate entering the page would redirect again and flicker repeatedly.
const TRIED_KEY = 'wx_silent_tried';

function inWechat(): boolean {
  return typeof navigator !== 'undefined' && /micromessenger/i.test(navigator.userAgent);
}

/** Strip code / state from the address bar. code is single-use; leaving it means a refresh always hits 40163. */
function stripCode() {
  try {
    const u = new URL(window.location.href);
    if (!u.searchParams.has('code') && !u.searchParams.has('state')) return;
    u.searchParams.delete('code');
    u.searchParams.delete('state');
    window.history.replaceState({}, '', u.pathname + (u.search || '') + u.hash);
  } catch { /* noop */ }
}

/**
 * Site-wide WeChat silent login guard (mounted at the route root, renders nothing itself).
 *
 * The redirect back **lands on the page that initiated authorization** (the frontend passes the current path to the backend to build redirect_uri), no longer
 * going through the home page -- one less redirect, fewer address bar jumps for the user. The whole chain is two hops: current page -> WeChat authorization ->
 * current page (?code), the minimum for snsapi_base web authorization; it can't be removed.
 *
 *  1) Out: inside WeChat, not logged in, not tried this session -> redirect to snsapi_base silent authorization.
 *  2) Back: the same page returns with ?code= -> exchange for openid to identify the user, strip code. Returning users get a JWT directly
 *     (zero-click login); new users store a bind_token, and pages show the binding prompt when needed (e.g. the audit page).
 *
 * Does nothing when not in WeChat, already logged in, or the channel is off (web authorization domain not configured -> enabled:false).
 */
export function WechatSilentAuth() {
  const { pathname, search } = useLocation();
  const { token, setToken } = useAuth();
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    if (!inWechat()) return;

    const code = new URLSearchParams(search).get('code');

    // ── Back: redirected with code (on the current page, no further redirect) -> identify user -> strip code ──
    if (code) {
      ranRef.current = true;
      (async () => {
        try {
          const s = await wechatAuthApi.h5Session(code);
          if (s.status === 'authorized' && s.access_token) {
            setToken(s.access_token);                 // returning user: zero-click login
          } else if (s.bind_token) {
try {
              sessionStorage.setItem('wx_bind_token', s.bind_token);
              // Exchanging the code is async; components on the page that decide "whether to prompt binding" may have mounted before it's set,
              // so dispatch an event to let them re-check (see the listener in DiagnosisHome), avoiding a missed race.
              window.dispatchEvent(new Event('wx-need-phone'));
            } catch { /* noop */ }
          }
        } catch { /* code expired/reused: don't redirect again, avoid infinite loops, treat as a regular site */ }
        stripCode();
      })();
      return;
    }

    // ── Out: if not logged in, start silent authorization, redirecting back to the current page ──
    if (token) return;
    try { if (sessionStorage.getItem(TRIED_KEY)) return; } catch { return; }

    ranRef.current = true;
    (async () => {
      const { enabled, oauth_url } = await wechatAuthApi.h5Authorize(pathname + (search || ''));
      if (!enabled || !oauth_url) return;             // channel not configured -> treat as a regular site, no redirect
      try { sessionStorage.setItem(TRIED_KEY, '1'); } catch { /* private mode */ }
      window.location.replace(oauth_url);
    })();
  }, [pathname, search, token, setToken]);

  return null;
}
