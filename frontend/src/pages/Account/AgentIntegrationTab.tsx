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
  const [label, setLabel] = useState('');
  const [fresh, setFresh] = useState<string | null>(null);   // 刚生成的明文 token(只显示这一次)
  const [err, setErr] = useState('');

  // IM 接入器(自建应用)
  const [im, setIm] = useState({ app_id: '', app_secret: '', verify_token: '', aes_key: '' });
  const [imConns, setImConns] = useState<Array<{ id: number; platform: string; app_id: string; app_secret_masked: string; enabled: boolean }>>([]);
  const [imBusy, setImBusy] = useState(false);
  const [imMsg, setImMsg] = useState('');

  const origin = window.location.origin;
  const installCmd = fresh ? `curl -fsSL ${origin}/skill/install.sh | bash -s -- ${fresh}` : '';
  const feishuCallback = `${origin}/api/agent/im/feishu/callback`;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/account/agent-tokens`, { headers: authHeaders() });
      const d = await r.json();
      setTokens(d.tokens || []);
      const r2 = await fetch(`${API_BASE}/account/im-connectors`, { headers: authHeaders() });
      const d2 = await r2.json();
      setImConns(d2.connectors || []);
    } catch { setErr('加载失败'); } finally { setLoading(false); }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const saveIm = async () => {
    setImBusy(true); setImMsg('');
    try {
      const r = await fetch(`${API_BASE}/account/im-connector`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ platform: 'feishu', ...im }),
      });
      if (!r.ok) throw new Error(await r.text());
      setImMsg('✅ 已保存。把上面的回调地址填回飞书事件订阅即可。');
      setIm({ app_id: '', app_secret: '', verify_token: '', aes_key: '' });
      await reload();
    } catch (e) { setImMsg(`保存失败:${e instanceof Error ? e.message : e}`); } finally { setImBusy(false); }
  };

  const deleteIm = async (id: number) => {
    if (!confirm('确认删除此 IM 接入?删除后机器人将无法再回复。')) return;
    await fetch(`${API_BASE}/account/im-connector/${id}/delete`, { method: 'POST', headers: authHeaders() });
    await reload();
  };

  const generate = async () => {
    setBusy(true); setErr(''); setFresh(null);
    try {
      const r = await fetch(`${API_BASE}/account/agent-token`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ label }),
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
        <p className="text-xs text-secondary mt-3">token 含完整对接能力(查数据 / 诊断 / 产稿);真实对外发布不在对接能力内(由平台护栏控制)。token 等同密钥,只显示一次,请妥善保管。</p>
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

      {/* ── IM 接入(飞书,自建应用)── */}
      <div className="rounded-xl p-5" style={card}>
        <h2 className="text-base font-semibold text-primary mb-1">IM 对话接入 · 飞书</h2>
        <p className="text-sm text-secondary mb-4">
          让团队在飞书里直接 @机器人 问「我被搜到几个?」「帮我诊断」——非技术同事零安装、零命令。
          在你公司飞书后台建一个<strong>企业自建应用</strong>,把凭证填到这里即可(应用与凭证都在你自己飞书,不经过我们)。
        </p>

        {/* 现有连接 */}
        {imConns.filter((c) => c.platform === 'feishu').map((c) => (
          <div key={c.id} className="flex items-center justify-between gap-3 py-2 px-3 rounded-lg mb-3" style={{ background: 'var(--bg-surface)' }}>
            <div className="text-sm text-primary min-w-0">
              已连接 · App ID <span className="text-secondary">{c.app_id}</span> · Secret {c.app_secret_masked}
            </div>
            <button onClick={() => void deleteIm(c.id)} className="px-3 py-1.5 rounded-md text-xs font-semibold whitespace-nowrap" style={{ color: '#f43f5e', border: '1px solid rgba(244,63,94,0.35)', background: 'transparent' }}>删除</button>
          </div>
        ))}

        {/* 回调地址(填回飞书事件订阅)*/}
        <div className="mb-4">
          <p className="text-xs text-secondary mb-1">① 在飞书「事件订阅」里把请求地址填成:</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 rounded-lg text-xs break-all" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}>{feishuCallback}</code>
            <CopyBtn text={feishuCallback} />
          </div>
        </div>

        {/* 凭证表单 */}
        <p className="text-xs text-secondary mb-2">② 把飞书应用「凭证与基础信息」「事件订阅」里的值填进来:</p>
        <div className="grid sm:grid-cols-2 gap-3">
          {([
            ['app_id', 'App ID', 'cli_xxxxx'],
            ['app_secret', 'App Secret', '应用密钥'],
            ['verify_token', 'Verification Token', '事件订阅校验串'],
            ['aes_key', 'Encrypt Key(可选,留空=不加密)', '留空即可'],
          ] as const).map(([k, lbl, ph]) => (
            <label key={k} className="text-sm">
              <span className="block text-secondary mb-1">{lbl}</span>
              <input
                value={(im as Record<string, string>)[k]}
                onChange={(e) => setIm((p) => ({ ...p, [k]: e.target.value }))}
                placeholder={ph}
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              />
            </label>
          ))}
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={() => void saveIm()} disabled={imBusy || !im.app_id || !im.app_secret}
            className="px-5 py-2 rounded-lg text-sm font-semibold"
            style={{ background: 'var(--accent-primary)', color: '#fff', opacity: imBusy || !im.app_id || !im.app_secret ? 0.5 : 1 }}
          >
            {imBusy ? '保存中…' : '保存并连接'}
          </button>
          {imMsg && <span className="text-xs" style={{ color: imMsg.startsWith('✅') ? 'var(--accent-secondary)' : '#f43f5e' }}>{imMsg}</span>}
        </div>
        <p className="text-xs text-secondary mt-3">③ 回飞书把应用发布给成员,在「机器人」里开启,即可在飞书内对话。企业微信接入同模式,即将开放。</p>
      </div>
    </div>
  );
}
