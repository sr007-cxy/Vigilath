// Device code authorization confirmation page -- the skill install command (no args) prints a pairing code in the terminal; the user enters it here to approve.
// Only after approval does CLI-side polling mint a token; this page never shows the token in plain text, nor does the token go through the clipboard.
import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { AuthModal } from '../components/AuthModal';
import { extractBackendMessage, localizedHeaders } from '../services/apiError';
import { PageHead } from '../components/PageHead';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

/** Discard the stored pairing code. Must be called on every dead state (expired/used/denied/not found), otherwise it pollutes the next attempt. */
function dropSavedCode() {
  try { localStorage.removeItem('device_link_code'); } catch { /* private mode */ }
}

type Status = 'input' | 'need-login' | 'loading' | 'confirm' | 'approved' | 'denied' | 'expired' | 'used' | 'error';

interface DeviceInfo {
  user_code: string;
  status: string;
  client: string;
  client_ip: string;
  created_at: string | null;
  expires_at: string | null;
}

// Same rules as backend _normalize_user_code: strip non-alphanumerics, uppercase, insert the hyphen
function normalize(s: string): string {
  const raw = (s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  return raw.length >= 8 ? `${raw.slice(0, 4)}-${raw.slice(4, 8)}` : raw;
}

export default function DeviceLink() {
  const [sp] = useSearchParams();
  const { token, isLoggedIn } = useAuth();
  const [code, setCode] = useState('');
  const [status, setStatus] = useState<Status>('input');
  const [info, setInfo] = useState<DeviceInfo | null>(null);
  const [msg, setMsg] = useState('');
  const [authOpen, setAuthOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  // Prefilled for deep link /link?code=XXXX-XXXX; logging in leaves this component's state, so localStorage holds it as a fallback.
  //
  // Stored with a timestamp: the backend gives pairing codes only 600 seconds (CODE_TTL_SECONDS in device.py),
  // and an old code left in localStorage pollutes the next attempt -- the user closes the page, it expires, reruns the install and gets
  // a new code, then opening bare /link prefills the previous one, the auto-lookup lands on "expired",
  // and they never get a chance to enter the new code. When the URL carries a code, the URL always wins.
  useEffect(() => {
    const fromUrl = sp.get('code');
    let c = fromUrl || '';
    if (!c) {
      try {
        const raw = localStorage.getItem('device_link_code');
        if (raw) {
          const saved = JSON.parse(raw) as { code?: string; at?: number };
          if (saved.code && saved.at && Date.now() - saved.at < 15 * 60_000) c = saved.code;
          else localStorage.removeItem('device_link_code');
        }
      } catch {
        localStorage.removeItem('device_link_code');   // Old format (bare string): just discard it
      }
    }
    if (c) {
      setCode(normalize(c));
      try {
        localStorage.setItem('device_link_code',
          JSON.stringify({ code: normalize(c), at: Date.now() }));
      } catch { /* If private mode can't write, never mind; it only affects prefill across the login round trip */ }
    }
  }, [sp]);

  const lookup = useCallback(async (userCode: string) => {
    if (!token) { setStatus('need-login'); return; }
    setStatus('loading');
    setMsg('');
    try {
      const r = await fetch(`${API_BASE}/account/agent-device/${encodeURIComponent(userCode)}`, {
        headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        // The backend's uniform shape is {"error":{"code","message"}} (see utils/error_handler.ErrorResponse.dict),
        // with neither message nor detail at the top level -- reading it the old way, users never saw any of the backend's
        // messages (things like 「配对码已过期,请在终端重新发起安装」 ("pairing code expired, restart the install in the terminal")).
        setMsg(extractBackendMessage(d, `查询失败(${r.status})`));
        dropSavedCode();
        setStatus('error');
        return;
      }
      const dev = d as DeviceInfo;
      setInfo(dev);
      // Discard the code on every dead state -- it can never be approved again, and keeping it would only send
      // the user straight to "expired" on the next prefill, when they actually hold a new code.
      if (dev.status !== 'pending') dropSavedCode();
      if (dev.status === 'expired') setStatus('expired');
      else if (dev.status === 'consumed') setStatus('used');
      else if (dev.status === 'denied') setStatus('denied');
      else if (dev.status === 'approved') setStatus('approved');
      else setStatus('confirm');
    } catch (e) {
      console.error('[device-link]', e);
      setMsg('网络没连上,或者站点暂时不可达。检查网络后重试。');
      setStatus('error');
    }
  }, [token]);

  // Logged in + have a code -> look it up once automatically
  useEffect(() => {
    const c = normalize(code);
    if (c.length === 9 && isLoggedIn && status === 'input') void lookup(c);
  }, [code, isLoggedIn, status, lookup]);

  const act = async (action: 'approve' | 'deny') => {
    if (!token || !info) return;
    setBusy(true);
    try {
      const r = await fetch(`${API_BASE}/account/agent-device/${encodeURIComponent(info.user_code)}/${action}`, {
        method: 'POST',
        headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        setMsg(extractBackendMessage(d, `操作失败(${r.status})`));
        setStatus('error');
        return;
      }
      localStorage.removeItem('device_link_code');
      setStatus(action === 'approve' ? 'approved' : 'denied');
    } catch (e) {
      console.error('[device-link]', e);
      setMsg('网络没连上,或者站点暂时不可达。检查网络后重试。');
      setStatus('error');
    } finally {
      setBusy(false);
    }
  };

  const card: CSSProperties = {
    maxWidth: 520, margin: '12vh auto', padding: '36px 32px', borderRadius: 16,
    background: '#fff', boxShadow: '0 8px 40px rgba(0,0,0,0.08)', textAlign: 'center',
    fontFamily: 'system-ui, -apple-system, sans-serif', color: '#1f2937', lineHeight: 1.7,
  };
  const btn: CSSProperties = {
    display: 'inline-block', marginTop: 18, padding: '12px 28px', borderRadius: 10,
    background: '#2563eb', color: '#fff', border: 'none', fontSize: 16, cursor: 'pointer', textDecoration: 'none',
  };
  const ghostBtn: CSSProperties = { ...btn, background: 'transparent', color: '#6b7280', border: '1px solid #d1d5db', marginLeft: 12 };
  const input: CSSProperties = {
    width: '100%', padding: '14px 16px', fontSize: 22, letterSpacing: 4, textAlign: 'center',
    borderRadius: 10, border: '1px solid #d1d5db', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    textTransform: 'uppercase', marginTop: 12,
  };
  const meta: CSSProperties = { fontSize: 14, color: '#6b7280', textAlign: 'left', marginTop: 18, padding: '14px 16px', background: '#f9fafb', borderRadius: 10 };

  const codeReady = normalize(code).length === 9;

  return (
    <>
      <PageHead titleKey="deviceLink.title" titleFallback="授权接入 Vigilath" />
      <div style={card}>
        {(status === 'input' || status === 'loading') && (
          <>
            <h2>授权接入</h2>
            <p>请输入终端里显示的配对码,确认后你的 AI 助手就能查询本账号的 GEO 与舆情数据。</p>
            <input
              style={input}
              value={code}
              onChange={(e) => setCode(normalize(e.target.value))}
              placeholder="XXXX-XXXX"
              maxLength={9}
              autoFocus
            />
            <div>
              <button
                style={{ ...btn, opacity: codeReady && status !== 'loading' ? 1 : 0.5 }}
                disabled={!codeReady || status === 'loading'}
                onClick={() => (isLoggedIn ? void lookup(normalize(code)) : setAuthOpen(true))}
              >
                {status === 'loading' ? '查询中…' : '下一步'}
              </button>
            </div>
          </>
        )}

        {status === 'need-login' && (
          <>
            <h2>请先登录</h2>
            <p>授权前需要登录你的 Vigilath 账号,授权后 AI 助手只能访问这个账号的数据。</p>
            <button style={btn} onClick={() => setAuthOpen(true)}>登录 / 注册</button>
          </>
        )}

        {status === 'confirm' && info && (
          <>
            <h2>确认授权?</h2>
            <p>下面这台设备正在申请接入你的账号。<strong>如果不是你本人发起的,请点「拒绝」。</strong></p>
            <div style={meta}>
              <div>配对码:<strong>{info.user_code}</strong></div>
              <div>发起设备:{info.client || '未提供'}</div>
              <div>发起 IP:{info.client_ip || '未知'}</div>
              <div>发起时间:{info.created_at ? new Date(info.created_at + 'Z').toLocaleString() : '—'}</div>
              <div style={{ marginTop: 8 }}>将获得:查询数据、发起诊断、生成稿件。<strong>不含对外发布</strong>。</div>
            </div>
            <div>
              <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={() => void act('approve')}>
                {busy ? '处理中…' : '批准'}
              </button>
              <button style={ghostBtn} disabled={busy} onClick={() => void act('deny')}>拒绝</button>
            </div>
          </>
        )}

        {status === 'approved' && (
          <>
            <h2>✅ 已授权</h2>
            <p>回到终端,安装会自动继续完成。之后可在「账号 · 对接集成」里随时吊销这个授权。</p>
          </>
        )}

        {status === 'denied' && (
          <>
            <h2>已拒绝</h2>
            <p>本次接入申请已被拒绝,对方拿不到任何凭证。</p>
          </>
        )}

        {status === 'expired' && (
          <>
            <h2>配对码已过期</h2>
            <p>配对码 10 分钟内有效。请回到终端重新运行安装命令,拿一个新码。</p>
          </>
        )}

        {status === 'used' && (
          <>
            <h2>配对码已使用</h2>
            <p>这个码已经完成过一次授权,不能重复使用。如需新增设备,请在终端重新发起安装。</p>
          </>
        )}

        {status === 'error' && (
          <>
            <h2>出了点问题</h2>
            <p>{msg || '请稍后重试。'}</p>
            <button style={btn} onClick={() => { setStatus('input'); setMsg(''); }}>重新输入配对码</button>
          </>
        )}
      </div>

      <AuthModal
        isOpen={authOpen}
        onClose={() => setAuthOpen(false)}
        defaultTab="login"
        onSuccess={() => { setAuthOpen(false); if (codeReady) void lookup(normalize(code)); }}
      />
    </>
  );
}
