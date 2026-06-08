// 对外集成 Tab —— 自助生成「接入 token」,把本账号的 GEO agent 作为 skill 接进外部 agent(小龙虾)。
// 生成后直接给出一行安装命令(用当前站点 origin,自动对齐 test/prod),复制即用。
import { useCallback, useEffect, useState } from 'react';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

interface TokenRow {
  tid: string;
  caps: string;
  label: string;
  enabled: boolean;
  expires_at: string | null;
  created_at: string | null;
}

const card: React.CSSProperties = { background: 'var(--bg-card)', border: '1px solid var(--border-color)' };

function authHeaders(): HeadersInit {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token') || ''}` };
}

function CopyBtn({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={() => { void navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }}
      className="px-3 py-1.5 rounded-md text-xs font-semibold transition-opacity"
      style={{ background: 'var(--accent-primary)', color: '#fff' }}
    >
      {done ? '已复制 ✓' : '复制'}
    </button>
  );
}

export function AgentIntegrationTab() {
  const [tokens, setTokens] = useState<TokenRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [caps, setCaps] = useState<'read' | 'read,write'>('read,write');
  const [label, setLabel] = useState('');
  const [fresh, setFresh] = useState<string | null>(null);   // 刚生成的明文 token(只显示这一次)
  const [err, setErr] = useState('');

  const origin = window.location.origin;
  const installCmd = fresh ? `curl -fsSL ${origin}/skill/install.sh | bash -s -- ${fresh}` : '';

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/account/agent-tokens`, { headers: authHeaders() });
      const d = await r.json();
      setTokens(d.tokens || []);
    } catch { setErr('加载失败'); } finally { setLoading(false); }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const generate = async () => {
    setBusy(true); setErr(''); setFresh(null);
    try {
      const r = await fetch(`${API_BASE}/account/agent-token`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ caps: caps.split(','), label }),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setFresh(d.token);
      setLabel('');
      await reload();
    } catch (e) { setErr(`生成失败:${e instanceof Error ? e.message : e}`); } finally { setBusy(false); }
  };

  const revoke = async (tid: string) => {
    if (!confirm(`确认吊销 ${tid}?吊销后用此 token 的对接立即失效。`)) return;
    await fetch(`${API_BASE}/account/agent-token/${tid}/revoke`, { method: 'POST', headers: authHeaders() });
    await reload();
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-primary">对接集成</h1>
        <p className="text-sm text-secondary mt-1">
          把本账号的 GEO 优化助手接进你自己的 AI agent(小龙虾)。生成一个接入 token,复制一行命令即可安装。
        </p>
      </div>

      {/* 生成区 */}
      <div className="rounded-xl p-5" style={card}>
        <h2 className="text-base font-semibold text-primary mb-4">生成接入 token</h2>
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm">
            <span className="block text-secondary mb-1">能力范围</span>
            <select
              value={caps} onChange={(e) => setCaps(e.target.value as 'read' | 'read,write')}
              className="px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            >
              <option value="read">只读(查数据、看诊断)</option>
              <option value="read,write">读写(可跑诊断 / 产稿)</option>
            </select>
          </label>
          <label className="text-sm flex-1 min-w-[180px]">
            <span className="block text-secondary mb-1">备注(可选)</span>
            <input
              value={label} onChange={(e) => setLabel(e.target.value)} placeholder="如:给某某小龙虾"
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
          </label>
          <button
            onClick={() => void generate()} disabled={busy}
            className="px-5 py-2 rounded-lg text-sm font-semibold transition-opacity"
            style={{ background: 'var(--accent-primary)', color: '#fff', opacity: busy ? 0.5 : 1 }}
          >
            {busy ? '生成中…' : '生成'}
          </button>
        </div>
        <p className="text-xs text-secondary mt-3">真实对外发布不在对接能力内(由平台护栏控制)。token 等同密钥,只显示一次,请妥善保管。</p>
        {err && <p className="text-xs mt-2" style={{ color: '#f43f5e' }}>{err}</p>}
      </div>

      {/* 刚生成:token + 安装命令(只此一次)*/}
      {fresh && (
        <div className="rounded-xl p-5" style={{ ...card, borderColor: 'var(--accent-primary)' }}>
          <h2 className="text-base font-semibold text-primary mb-1">✅ 已生成(请立即复制,关闭后不再显示)</h2>
          <p className="text-xs text-secondary mb-3">在对方 agent 的机器上执行下面这行,自动安装 skill + 写入 token,装完即用:</p>
          <div className="flex items-center gap-2 mb-4">
            <code className="flex-1 px-3 py-2 rounded-lg text-xs break-all" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}>
              {installCmd}
            </code>
            <CopyBtn text={installCmd} />
          </div>
          <p className="text-xs text-secondary mb-1">token(单独用):</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 rounded-lg text-xs break-all" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}>
              {fresh}
            </code>
            <CopyBtn text={fresh} />
          </div>
        </div>
      )}

      {/* 已有 token 列表 */}
      <div className="rounded-xl p-5" style={card}>
        <h2 className="text-base font-semibold text-primary mb-4">已发放的 token</h2>
        {loading ? (
          <p className="text-sm text-secondary">加载中…</p>
        ) : tokens.length === 0 ? (
          <p className="text-sm text-secondary">还没有 token。上面生成一个开始对接。</p>
        ) : (
          <div className="space-y-2">
            {tokens.map((tk) => (
              <div key={tk.tid} className="flex items-center justify-between gap-3 py-2 px-3 rounded-lg" style={{ background: 'var(--bg-surface)' }}>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-primary truncate">
                    {tk.label || tk.tid} <span className="text-xs text-secondary">({tk.caps})</span>
                  </div>
                  <div className="text-xs text-secondary">
                    {tk.enabled ? '启用' : '已吊销'} · 到期 {tk.expires_at?.slice(0, 10) || '—'} · {tk.tid}
                  </div>
                </div>
                {tk.enabled && (
                  <button
                    onClick={() => void revoke(tk.tid)}
                    className="px-3 py-1.5 rounded-md text-xs font-semibold whitespace-nowrap"
                    style={{ color: '#f43f5e', border: '1px solid rgba(244,63,94,0.35)', background: 'transparent' }}
                  >
                    吊销
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
