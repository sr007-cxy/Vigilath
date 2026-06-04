// GEO 优化 Agent —— 悬浮聊天窗(用户侧入口)。
// 仅登录后显示;右下角浮标点开,SSE 流式对话。
// 样式走全局 CSS 变量(--accent-* / --bg-* / --text-* / --border-*),自动跟随明暗主题、与页面风格一致。
import { useState, useRef, useEffect, useCallback } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { streamAgentChat, type AgentCard } from '../../services/agentApi';

interface Msg {
  role: 'user' | 'assistant';
  text: string;
  cards?: AgentCard[];
}

const TOOL_TITLE: Record<string, string> = {
  get_today_effect: '今日投放效果',
  get_query_coverage: '累计命中(被搜到)',
  get_growth_summary: '品牌增长数据',
  get_publish_status: '发布进度',
  get_report: '诊断报告',
  get_batch_results: '最近跑批',
};

function pct(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : '—';
}

function CardView({ card }: { card: AgentCard }) {
  const d = card.data || {};
  const rows: [string, string][] = [];
  if (card.tool === 'get_today_effect') {
    rows.push(['扩展问题被搜到(今天)', `${d.expanded_hit_today ?? 0} / ${d.expanded_total ?? 0}`]);
    rows.push(['种子词被搜到(今天)', `${d.seed_hit_today ?? 0} / ${d.seed_total ?? 0}`]);
    if (d.date) rows.push(['日期', String(d.date)]);
  } else if (card.tool === 'get_query_coverage') {
    rows.push(['被搜到的问题(累计)', `${d.hit_queries ?? 0} / ${d.monitored_queries ?? 0}`]);
    rows.push(['被搜到的种子词(累计)', `${d.hit_seeds ?? 0} / ${d.seed_total ?? 0}`]);
  } else if (card.tool === 'get_growth_summary') {
    rows.push(['命中率', pct(d.hit_rate)]);
    const br = (d.brand_rank || {}) as Record<string, unknown>;
    rows.push(['品牌位次', `Top1 ${br.top1 ?? 0} · Top3 ${br.top3 ?? 0}`]);
    const comps = (d.top_competitors || []) as Array<{ name: string; count: number }>;
    if (comps.length) rows.push(['高频竞品', comps.slice(0, 3).map((c) => c.name).join('、')]);
  } else if (card.tool === 'get_publish_status') {
    rows.push(['已发布', `${d.published ?? 0} / ${d.total ?? 0}`]);
  } else if (card.tool === 'get_report') {
    rows.push(['状态', String(d.status ?? '—')]);
  } else if (card.tool === 'get_batch_results') {
    const be = (d.by_engine || {}) as Record<string, { hits: number; total: number }>;
    Object.entries(be).slice(0, 5).forEach(([e, v]) => rows.push([e, `${v.hits}/${v.total}`]));
  }
  if (!rows.length) return null;
  return (
    <div style={{
      marginTop: 8, padding: '10px 12px', borderRadius: 12,
      background: 'var(--bg-surface)', border: '1px solid var(--border-color)',
    }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent-secondary)', marginBottom: 6 }}>
        📊 {TOOL_TITLE[card.tool] || card.tool}
      </div>
      {rows.map(([k, v], i) => (
        <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '2px 0', color: 'var(--text-primary)' }}>
          <span style={{ color: 'var(--text-secondary)' }}>{k}</span>
          <span style={{ fontWeight: 600 }}>{v}</span>
        </div>
      ))}
    </div>
  );
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
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, role: 'assistant', text: last.text + d };
          return next;
        }),
      onCards: (cards) =>
        setMsgs((m) => {
          const next = [...m];
          next[next.length - 1] = { ...next[next.length - 1], cards };
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
          // 「预约演示」浮动按钮在 right:24/bottom:24(ContactModal),把 🤖 上移堆在其上方,避免重叠
          position: 'fixed', right: 24, bottom: 88, zIndex: 1000,
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
            position: 'fixed', right: 24, bottom: 152, zIndex: 1000,
            width: 384, maxWidth: 'calc(100vw - 48px)', height: 540, maxHeight: 'calc(100vh - 200px)',
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
              <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 10 }}>
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
                {m.cards?.length ? (
                  <div style={{ width: '100%', maxWidth: '92%' }}>
                    {m.cards.map((c, ci) => <CardView key={ci} card={c} />)}
                  </div>
                ) : null}
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
