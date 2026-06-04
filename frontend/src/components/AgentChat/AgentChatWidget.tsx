// GEO 优化 Agent —— 悬浮聊天窗(用户侧入口)。
// 仅登录后显示;右下角浮标点开,SSE 流式对话。
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
        style={{
          position: 'fixed', right: 24, bottom: 24, zIndex: 1000,
          width: 56, height: 56, borderRadius: '50%', border: 'none',
          background: '#2563eb', color: '#fff', fontSize: 24, cursor: 'pointer',
          boxShadow: '0 4px 16px rgba(0,0,0,.25)',
        }}
      >
        {open ? '×' : '🤖'}
      </button>

      {/* 聊天面板 */}
      {open && (
        <div
          style={{
            position: 'fixed', right: 24, bottom: 92, zIndex: 1000,
            width: 380, maxWidth: 'calc(100vw - 48px)', height: 520, maxHeight: 'calc(100vh - 140px)',
            display: 'flex', flexDirection: 'column',
            background: '#fff', borderRadius: 12, overflow: 'hidden',
            boxShadow: '0 8px 32px rgba(0,0,0,.22)', border: '1px solid #e5e7eb',
          }}
        >
          <div style={{ padding: '12px 16px', background: '#2563eb', color: '#fff', fontWeight: 600 }}>
            GEO 优化助手
          </div>
          <div ref={bodyRef} style={{ flex: 1, overflowY: 'auto', padding: 12, background: '#f8fafc' }}>
            {msgs.map((m, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 10 }}>
                <div
                  style={{
                    maxWidth: '82%', padding: '8px 12px', borderRadius: 10, fontSize: 14, lineHeight: 1.5,
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    background: m.role === 'user' ? '#2563eb' : '#fff',
                    color: m.role === 'user' ? '#fff' : '#111827',
                    border: m.role === 'user' ? 'none' : '1px solid #e5e7eb',
                  }}
                >
                  {m.text || (busy && i === msgs.length - 1 ? '…' : '')}
                </div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, padding: 10, borderTop: '1px solid #e5e7eb', background: '#fff' }}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }}
              placeholder={busy ? '助手回复中…' : '输入消息…'}
              disabled={busy}
              style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 14, outline: 'none' }}
            />
            <button
              onClick={() => void send()}
              disabled={busy || !input.trim()}
              style={{
                padding: '8px 16px', borderRadius: 8, border: 'none', fontSize: 14, cursor: busy ? 'default' : 'pointer',
                background: busy || !input.trim() ? '#93c5fd' : '#2563eb', color: '#fff',
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
