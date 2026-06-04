// GEO 优化 Agent —— 悬浮聊天窗(用户侧入口)。
// 仅登录后显示;右下角浮标点开,SSE 流式对话。
// 样式走全局 CSS 变量(--accent-* / --bg-* / --text-* / --border-*),自动跟随明暗主题、与页面风格一致。
import { useState, useRef, useEffect, useCallback } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { streamAgentChat } from '../../services/agentApi';

interface Msg {
  role: 'user' | 'assistant';
  text: string;
}

export function AgentChatWidget() {
  const { isLoggedIn } = useAuth();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: 'assistant', text: '你好,我是 Vigilath GEO 优化助手。可以帮你建主题、跑诊断、看根因。试试:「帮我诊断当前主题」。' },
  ]);
  const bodyRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: 'smooth' });
  }, [msgs, open]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput('');
    setBusy(true);
    setMsgs((m) => [...m, { role: 'user', text }, { role: 'assistant', text: '' }]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    await streamAgentChat(text, null, {
      signal: ctrl.signal,
      onDelta: (d) =>
        setMsgs((m) => {
          const next = [...m];
          next[next.length - 1] = { role: 'assistant', text: next[next.length - 1].text + d };
          return next;
        }),
      onError: (msg) =>
        setMsgs((m) => {
          const next = [...m];
          next[next.length - 1] = { role: 'assistant', text: `⚠️ ${msg}` };
          return next;
        }),
    });
    setBusy(false);
  }, [input, busy]);

  if (!isLoggedIn) return null;

  return (
    <>
      {/* 浮标 */}
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="GEO 优化助手"
        className="transition-transform hover:scale-105 active:scale-95"
        style={{
          position: 'fixed', right: 24, bottom: 24, zIndex: 1000,
          width: 56, height: 56, borderRadius: '50%', border: 'none',
          background: 'var(--accent-gradient)', color: '#fff', fontSize: 24, cursor: 'pointer',
          boxShadow: '0 6px 20px rgba(0,0,0,.22)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        {open ? '×' : '🤖'}
      </button>

      {/* 聊天面板 */}
      {open && (
        <div
          className="animate-fade-in"
          style={{
            position: 'fixed', right: 24, bottom: 92, zIndex: 1000,
            width: 384, maxWidth: 'calc(100vw - 48px)', height: 540, maxHeight: 'calc(100vh - 140px)',
            display: 'flex', flexDirection: 'column',
            background: 'var(--bg-card)', borderRadius: 16, overflow: 'hidden',
            boxShadow: '0 16px 48px rgba(0,0,0,.24)', border: '1px solid var(--border-color)',
          }}
        >
          <div style={{
            padding: '14px 16px', background: 'var(--accent-gradient)', color: '#fff',
            fontWeight: 600, fontSize: 15, display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span>🤖 GEO 优化助手</span>
          </div>

          <div ref={bodyRef} style={{ flex: 1, overflowY: 'auto', padding: 14, background: 'var(--bg-primary)' }}>
            {msgs.map((m, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 10 }}>
                <div
                  style={{
                    maxWidth: '84%', padding: '9px 13px', borderRadius: 14, fontSize: 14, lineHeight: 1.55,
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    background: m.role === 'user' ? 'var(--accent-primary)' : 'var(--bg-card)',
                    color: m.role === 'user' ? '#fff' : 'var(--text-primary)',
                    border: m.role === 'user' ? 'none' : '1px solid var(--border-color)',
                    borderBottomRightRadius: m.role === 'user' ? 4 : 14,
                    borderBottomLeftRadius: m.role === 'user' ? 14 : 4,
                  }}
                >
                  {m.text || (busy && i === msgs.length - 1 ? '思考中…' : '')}
                </div>
              </div>
            ))}
          </div>

          <div style={{
            display: 'flex', gap: 8, padding: 12,
            borderTop: '1px solid var(--border-color)', background: 'var(--bg-card)',
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
