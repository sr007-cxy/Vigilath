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
    rows.push(['今日新增命中', String(d.today_new_hits ?? 0)]);
    rows.push(['累计被搜到问题', `${d.cumulative_hit_queries ?? 0} / ${d.monitored_queries ?? 0}`]);
    rows.push(['累计被搜到种子词', `${d.cumulative_hit_seeds ?? 0} / ${d.seed_total ?? 0}`]);
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

// 可拖拽:把 handle(头像/标题栏)的指针拖动转成容器的 left/top。
// pos=null 时用默认锚点(right/bottom);拖动后切到绝对坐标并夹在视口内。
// movedRef 用来区分「点击」与「拖动」,拖动后抑制 onClick(避免拖完就开关窗)。
function useDraggable() {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const st = useRef<{ sx: number; sy: number; bx: number; by: number; moved: boolean } | null>(null);
  const movedRef = useRef(false);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    st.current = { sx: e.clientX, sy: e.clientY, bx: rect.left, by: rect.top, moved: false };
    movedRef.current = false;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const s = st.current;
    if (!s) return;
    const dx = e.clientX - s.sx, dy = e.clientY - s.sy;
    if (!s.moved && Math.hypot(dx, dy) < 4) return;
    s.moved = true;
    movedRef.current = true;
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth, h = el.offsetHeight;
    const nx = Math.min(Math.max(8, s.bx + dx), window.innerWidth - w - 8);
    const ny = Math.min(Math.max(8, s.by + dy), window.innerHeight - h - 8);
    setPos({ x: nx, y: ny });
  };
  const onPointerUp = (e: React.PointerEvent) => {
    st.current = null;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  };

  return { ref, pos, movedRef, handleProps: { onPointerDown, onPointerMove, onPointerUp } };
}

export function AgentChatWidget() {
  const { isLoggedIn } = useAuth();
  const launcher = useDraggable();
  const panel = useDraggable();
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
      {/* 浮标:头像按钮 + 下方「GEO 市场专员」文字。可拖动改变位置。 */}
      <div
        ref={launcher.ref}
        style={{
          // 默认锚点:「预约演示」浮动按钮在 right:24/bottom:24,把助手堆在其上方避免重叠;拖动后切到绝对坐标
          position: 'fixed', zIndex: 1000,
          ...(launcher.pos
            ? { left: launcher.pos.x, top: launcher.pos.y }
            : { right: 24, bottom: 88 }),
          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
          touchAction: 'none', userSelect: 'none',
        }}
      >
        <button
          {...launcher.handleProps}
          onClick={() => { if (launcher.movedRef.current) return; setOpen((o) => !o); }}
          aria-label="GEO 市场专员"
          title="点击对话 · 拖动可移动位置"
          className="transition-transform hover:scale-105 active:scale-95"
          style={{
            width: 60, height: 60, borderRadius: '50%', border: 'none', padding: 0,
            background: open ? 'var(--accent-gradient)' : 'transparent',
            color: '#fff', cursor: 'grab', overflow: 'hidden',
            boxShadow: '0 6px 20px rgba(0,0,0,.22)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          {open ? (
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 9l6 6 6-6" />
            </svg>
          ) : (
            // 市场专员头像(自带圆环,透明角)
            <img
              src="/image/agent-avatar.png"
              alt=""
              aria-hidden="true"
              draggable={false}
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block', pointerEvents: 'none' }}
            />
          )}
        </button>
        {!open && (
          <span
            style={{
              fontSize: 11, fontWeight: 600, lineHeight: 1, whiteSpace: 'nowrap',
              color: '#fff', background: 'rgba(0,0,0,.55)', padding: '3px 8px', borderRadius: 999,
              boxShadow: '0 2px 8px rgba(0,0,0,.18)', pointerEvents: 'none',
            }}
          >
            GEO 市场专员
          </span>
        )}
      </div>

      {/* 聊天面板。标题栏可拖动改变窗口位置。 */}
      {open && (
        <div
          ref={panel.ref}
          className="animate-fade-in"
          style={{
            position: 'fixed', zIndex: 1000,
            ...(panel.pos
              ? { left: panel.pos.x, top: panel.pos.y }
              : { right: 24, bottom: 152 }),
            width: 384, maxWidth: 'calc(100vw - 48px)', height: 540, maxHeight: 'calc(100vh - 200px)',
            display: 'flex', flexDirection: 'column',
            background: 'var(--bg-card)', borderRadius: 16, overflow: 'hidden',
            boxShadow: '0 16px 48px rgba(0,0,0,.24)', border: '1px solid var(--border-color)',
          }}
        >
          {/* 标题栏:拖动手柄。头像放大并超出标题栏下沿。 */}
          <div
            {...panel.handleProps}
            style={{
              position: 'relative', padding: '13px 16px 13px 92px', minHeight: 58,
              background: 'var(--accent-gradient)', color: '#fff',
              borderTopLeftRadius: 16, borderTopRightRadius: 16,
              fontWeight: 600, fontSize: 15, display: 'flex', flexDirection: 'column', justifyContent: 'center',
              cursor: 'grab', touchAction: 'none', userSelect: 'none',
            }}
          >
            <img
              src="/image/agent-avatar.png"
              alt=""
              aria-hidden="true"
              draggable={false}
              style={{
                position: 'absolute', left: 14, bottom: -16, width: 68, height: 68,
                objectFit: 'cover', flexShrink: 0, pointerEvents: 'none',
                filter: 'drop-shadow(0 4px 10px rgba(0,0,0,.28))',
              }}
            />
            <span>GEO 市场专员</span>
            <span style={{ fontSize: 11, fontWeight: 400, opacity: 0.85, marginTop: 1 }}>在线 · 按住标题栏可拖动</span>
          </div>

          <div ref={bodyRef} style={{ flex: 1, overflowY: 'auto', padding: '20px 14px 14px', background: 'var(--bg-primary)', borderBottomLeftRadius: 16, borderBottomRightRadius: 16 }}>
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
