// IM acquisition landing page: deep links `/onboard?t=<token>` sent by Feishu/WeCom bots to not-yet-activated enterprises land here.
// Flow: read and persist the token -> if logged out, open AuthModal (sign-up by default) -> after login, auto POST /onboard/bind to complete enterprise binding.
import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { AuthModal } from '../components/AuthModal';
import { localizedHeaders } from '../services/apiError';
import { PageHead } from '../components/PageHead';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

type Status = 'init' | 'no-token' | 'need-login' | 'binding' | 'done' | 'expired' | 'error';

export default function Onboard() {
  const [sp] = useSearchParams();
  const { token, isLoggedIn } = useAuth();
  const [onboardToken, setOnboardToken] = useState('');
  const [status, setStatus] = useState<Status>('init');
  const [authOpen, setAuthOpen] = useState(false);
  const [msg, setMsg] = useState('');

  // 1) Read the onboard token and persist it (login/sign-up leaves this component's state, so localStorage holds it)
  useEffect(() => {
    const t = sp.get('t') || localStorage.getItem('onboard_token') || '';
    if (t) {
      setOnboardToken(t);
      localStorage.setItem('onboard_token', t);
    } else {
      setStatus('no-token');
    }
  }, [sp]);

  const doBind = useCallback(async () => {
    if (!onboardToken || !token) return;
    setStatus('binding');
    try {
      const r = await fetch(`${API_BASE}/agent/im/onboard/bind`, {
        method: 'POST',
        headers: localizedHeaders({ 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }),
        body: JSON.stringify({ token: onboardToken }),
      });
      if (r.ok) {
        localStorage.removeItem('onboard_token');
        setStatus('done');
      } else if (r.status === 400) {
        setStatus('expired');
      } else {
        const d = await r.json().catch(() => ({}));
        setMsg((d as { detail?: string }).detail || `绑定失败(${r.status})`);
        setStatus('error');
      }
    } catch (e) {
      setMsg(String(e));
      setStatus('error');
    }
  }, [onboardToken, token]);

  // 2) Once there's a token: bind directly if logged in; otherwise prompt to log in
  useEffect(() => {
    if (!onboardToken) return;
    if (isLoggedIn) doBind();
    else setStatus('need-login');
  }, [onboardToken, isLoggedIn, doBind]);

  const card: CSSProperties = {
    maxWidth: 520, margin: '12vh auto', padding: '36px 32px', borderRadius: 16,
    background: '#fff', boxShadow: '0 8px 40px rgba(0,0,0,0.08)', textAlign: 'center',
    fontFamily: 'system-ui, -apple-system, sans-serif', color: '#1f2937', lineHeight: 1.7,
  };
  const btn: CSSProperties = {
    display: 'inline-block', marginTop: 18, padding: '12px 28px', borderRadius: 10,
    background: '#2563eb', color: '#fff', border: 'none', fontSize: 16, cursor: 'pointer', textDecoration: 'none',
  };

  return (
    <>
      <PageHead titleKey="onboard.title" titleFallback="开通 Vigilath" />
      <div style={card}>
        {status === 'init' && <p>正在准备开通…</p>}

        {status === 'no-token' && (
          <>
            <h2>开通链接无效</h2>
            <p>请从飞书/企业微信机器人里点击「注册并选购服务」链接进入本页。</p>
          </>
        )}

        {status === 'need-login' && (
          <>
            <h2>开通 Vigilath</h2>
            <p>登录或注册后,即可为贵公司开通 AI 搜索可见性 + 舆情监控,开通后回到 IM 直接使用。</p>
            <button style={btn} onClick={() => setAuthOpen(true)}>登录 / 注册</button>
          </>
        )}

        {status === 'binding' && <p>正在为贵公司开通绑定…</p>}

        {status === 'done' && (
          <>
            <h2>✅ 开通成功</h2>
            <p>已绑定到您的账号。回到飞书/企业微信,直接问机器人「今天舆情怎么样」「帮我诊断」即可。</p>
            <p style={{ fontSize: 14, color: '#6b7280', marginTop: 16 }}>
              还没有有效套餐?<Link to="/products-services">点此选购</Link> 后即可使用全部能力。
            </p>
            <Link to="/account/membership" style={{ ...btn, background: 'var(--success-solid)' }}>查看我的套餐</Link>
          </>
        )}

        {status === 'expired' && (
          <>
            <h2>链接已过期</h2>
            <p>开通链接已失效,请回到飞书/企业微信机器人重新获取一个新链接。</p>
          </>
        )}

        {status === 'error' && (
          <>
            <h2>开通遇到问题</h2>
            <p>{msg || '请稍后重试,或联系我们。'}</p>
            <button style={btn} onClick={doBind}>重试</button>
          </>
        )}
      </div>

      <AuthModal isOpen={authOpen} onClose={() => setAuthOpen(false)} defaultTab="register" onSuccess={() => setAuthOpen(false)} />
    </>
  );
}
