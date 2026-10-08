// WeCom-dedicated agent page (/wecom): shows the agent chat directly; if logged out, opens login/sign-up.
// Doesn't use WeCom OAuth (that needs the WeCom API + an IP allowlist); uses the Vigilath login session + the web agent instead.
// Note: the WeCom passwordless login (OAuth) backend wecom_oauth.py is kept; switch back once the IP allowlist is ready.
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAuth } from '../contexts/AuthContext';
import { AuthModal } from '../components/AuthModal';
import { streamAgentChat, type AgentCard } from '../services/agentApi';
import { CardView } from '../components/AgentChat/cards';

interface Msg { role: 'user' | 'assistant'; text: string; cards?: AgentCard[] }

const btn: CSSProperties = { marginTop: 12, padding: '12px 30px', borderRadius: 10, background: '#2563eb',
  color: '#fff', border: 'none', fontSize: 16, cursor: 'pointer' };

// ── Markdown rendering (react-markdown + remark-gfm), colors adapted to the /wecom light bubbles ──
const mdComponents = {
  table: (p: any) => <div style={{ overflowX: 'auto', margin: '6px 0' }}><table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }} {...p} /></div>,
  th: (p: any) => <th style={{ border: '1px solid #e5e7eb', padding: '4px 8px', textAlign: 'left', background: '#f8fafc', fontWeight: 600 }} {...p} />,
  td: (p: any) => <td style={{ border: '1px solid #e5e7eb', padding: '4px 8px', textAlign: 'left' }} {...p} />,
  a: (p: any) => <a {...p} target="_blank" rel="noreferrer" style={{ color: '#2563eb', textDecoration: 'underline' }} />,
  code: (p: any) => <code style={{ background: '#f1f5f9', padding: '0 4px', borderRadius: 4, fontSize: 12 }} {...p} />,
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

// JWT expiry check: decode payload.exp; if it can't be decoded, don't treat it as expired (let a 401 handle it as the fallback).
function isJwtExpired(t: string): boolean {
  try {
    const b = t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const pad = b.length % 4 ? '='.repeat(4 - (b.length % 4)) : '';
    const p = JSON.parse(atob(b + pad));
    return typeof p.exp === 'number' && p.exp * 1000 <= Date.now();
  } catch { return false; }
}

export default function WecomAgent() {
  const { isLoggedIn, token, clearAuth } = useAuth();
  const [authOpen, setAuthOpen] = useState(false);
  // The WeCom webview often keeps stale expired tokens: isLoggedIn only checks whether a token exists, not whether it's expired,
  // so it goes straight into chat and then a 401 reports "network error". Here expiry is checked explicitly -> treat as logged out, clear the leftover, open login.
  const valid = isLoggedIn && !!token && !isJwtExpired(token);

  // Logged out / expired -> open login/sign-up automatically
  useEffect(() => { if (!valid) setAuthOpen(true); }, [valid]);
  // Clear the leftover expired token so other pages don't misuse it
  useEffect(() => { if (token && isJwtExpired(token)) clearAuth(); }, [token, clearAuth]);

  if (valid && token) return <WecomChat jwt={token} onExpired={clearAuth} />;

  const wrap: CSSProperties = { minHeight: '100vh', display: 'flex', flexDirection: 'column', background: '#f5f6f8',
    fontFamily: 'system-ui,-apple-system,sans-serif' };
  const center: CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', textAlign: 'center', color: '#334155', padding: 28, gap: 10 };
  return (
    <div style={wrap}>
      <div style={center}>
        <h2>威吉力 · AI 助手</h2>
        <p>登录或注册后即可使用:AI 搜索可见性检测 + 舆情监控助手。</p>
        <button style={btn} onClick={() => setAuthOpen(true)}>登录 / 注册</button>
      </div>
      <AuthModal isOpen={authOpen} onClose={() => setAuthOpen(false)} defaultTab="login" onSuccess={() => setAuthOpen(false)} />
    </div>
  );
}

// ── Full-screen chat (calls the web agent /api/agent/chat with the Vigilath login JWT) ────────────
function WecomChat({ jwt, onExpired }: { jwt: string; onExpired: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([{ role: 'assistant',
    text: '你好!我是威吉力 AI 助手,可以问我「今天舆情怎么样」「帮我跑 GEO 诊断」「投放进展」等。' }]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs]);

  async function send() {
    const q = input.trim(); if (!q || busy) return;
    setInput('');
    await sendText(q);
  }

  // Buttons in cards go through here -- exactly the same path as the user typing (sending a normal message).
  // Without extracting it, CardView can't get onSend, and the industry term card's buttons stay grayed out with Enter doing nothing:
  // a **broken form**, worse than not rendering (at least without rendering the model asks in text).
  async function sendText(q: string) {
    if (!q.trim() || busy) return;
    setBusy(true);
    setMsgs(m => [...m, { role: 'user', text: q }, { role: 'assistant', text: '' }]);

    // ★ SSE parsing uses agentApi's shared implementation instead of a private copy.
    //   The private copy only handled delta -- cards / confirms were all dropped, so WeCom users couldn't see
    //   structured cards like the six metrics and competitors, nor the two-step confirmation cards.
    const patch = (fn: (m: Msg) => Msg) =>
      setMsgs(m => { const c = [...m]; c[c.length - 1] = fn(c[c.length - 1]); return c; });

    await streamAgentChat(q, null, null, {
      onDelta: d => patch(m => ({ ...m, text: m.text + d })),
      onCards: cards => patch(m => ({ ...m, cards: [...(m.cards || []), ...cards] })),
      // The WeCom full-screen page has no actionable left-side page, so ui_actions are unusable -- but **not silently**:
      // render a line of gray text, lest users think "it said it would change things for me but nothing happened"
      onUiActions: acts => patch(m => ({
        ...m, text: m.text + `\n\n_(有 ${acts.length} 个页面操作需要在网页端完成)_`,
      })),
      onConfirms: () => patch(m => ({
        ...m, text: m.text + '\n\n_(这一步需要确认,请在网页端完成)_',
      })),
      onUnauthorized: () => {
        patch(m => ({ ...m, text: '登录已过期,正在为你重新登录…' }));
        onExpired();
      },
      onError: e => patch(m => ({ ...m, text: '抱歉,出错了:' + e })),
      onDone: () => setBusy(false),
    }, undefined, jwt);
    setBusy(false);
  }

  const page: CSSProperties = { height: '100vh', display: 'flex', flexDirection: 'column', background: '#f5f6f8', fontFamily: 'system-ui,-apple-system,sans-serif' };
  const head: CSSProperties = { padding: '12px 16px', background: '#fff', borderBottom: '1px solid #e5e7eb', fontWeight: 600, color: '#1f2937', flexShrink: 0 };
  const list: CSSProperties = { flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 10 };
  const bar: CSSProperties = { display: 'flex', gap: 8, padding: 10, background: '#fff', borderTop: '1px solid #e5e7eb', flexShrink: 0 };
  const bubble = (me: boolean): CSSProperties => ({ alignSelf: me ? 'flex-end' : 'flex-start', maxWidth: '82%',
    padding: '9px 13px', borderRadius: 12, whiteSpace: me ? 'pre-wrap' : 'normal', wordBreak: 'break-word', lineHeight: 1.6,
    background: me ? '#2563eb' : '#fff', color: me ? '#fff' : '#1f2937', border: me ? 'none' : '1px solid #e5e7eb' });

  return (
    <div style={page}>
      <div style={head}>威吉力 · AI 助手</div>
      <div style={list}>
        {msgs.map((m, i) => (
          <div key={i} style={bubble(m.role === 'user')}>
            {m.role === 'assistant'
              ? (
                <>
                  {m.text ? <MarkdownView text={m.text} /> : (busy && i === msgs.length - 1 ? '思考中…' : '')}
                  {(m.cards || []).map((c, ci) => (
                    <CardView
                      key={ci}
                      card={c}
                      disabled={busy || i !== msgs.length - 1}
                      onSend={t => void sendText(t)}
                    />
                  ))}
                </>
              )
              : m.text}
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div style={bar}>
        <input style={{ flex: 1, padding: '10px 12px', borderRadius: 10, border: '1px solid #d1d5db', fontSize: 15 }}
          value={input} onChange={e => setInput(e.target.value)} placeholder="问我点什么…"
          onKeyDown={e => { if (e.key === 'Enter') void send(); }} disabled={busy} />
        <button style={{ ...btn, marginTop: 0, padding: '10px 18px', opacity: busy ? 0.6 : 1 }} onClick={() => void send()} disabled={busy}>发送</button>
      </div>
    </div>
  );
}
