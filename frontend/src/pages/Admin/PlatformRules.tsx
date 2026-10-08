// Platform review rule library — admins maintain each publish platform's "review red lines", injected into the prompt per platform at article generation.
// Left: platform list (with rejection counts); right: rule editor + AI first draft + pending increments (learned from rejections).
//
// 2026-08-04 — provide both automatic and manual paths in full:
//   Automatic: run one "Auto-adapt" round (also a daily cron) -- platforms in use without rules get a row + AI first draft
//        that takes effect directly (marked "AI draft" pending proofreading); rejection reasons for existing platforms are distilled into increments awaiting adoption.
//   Manual: "+ Add platform" to create a row by typing a platform name, delete rule rows, edit and save in the editor.
// Platforms "in use · missing rules" are listed first and marked red -- their drafts are being generated with zero guardrails.

import { useCallback, useEffect, useState } from 'react';
import { platformRulesApi, type PlatformRule } from '../../services/platformRulesApi';
import { uiConfirm, uiPrompt } from '../../components/ui/dialogs';

const card: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border-color)',
};
const inputStyle: React.CSSProperties = {
  background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
};

export function AdminPlatformRules() {
  const token = localStorage.getItem('token') || '';
  const [rules, setRules] = useState<PlatformRule[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [draft, setDraft] = useState('');           // Current editor content
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);  // 'save' | 'seed' | 'learn' | 'approve'
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const reload = useCallback(async (keepActive = true) => {
    try {
      const list = await platformRulesApi.list(token);
      setRules(list);
      setErr(null);
      if (!keepActive || !active) {
        const first = list[0]?.platform || null;
        setActive(a => (keepActive && a) ? a : first);
      }
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [token, active]);

  useEffect(() => { reload(false); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const current = rules.find(r => r.platform === active) || null;

  // Switch platform -> load that platform's rules into the editor
  useEffect(() => {
    setDraft(current?.rules_text || '');
    setDirty(false);
    setMsg(null);
  }, [active, current?.rules_text]);

  const run = async (key: string, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(key); setErr(null); setMsg(null);
    try { await fn(); } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  };

  const save = () => run('save', async () => {
    if (!active) return;
    const updated = await platformRulesApi.update(active, draft, token);
    setRules(rs => rs.map(r => r.platform === updated.platform ? updated : r));
    setDirty(false);
    setMsg('已保存,后续生成的文章将遵守这份规则');
  });

  const seed = () => run('seed', async () => {
    if (!active) return;
    const { draft: d } = await platformRulesApi.seed(active, token);
    setDraft(prev => prev.trim() ? `${prev.trim()}\n${d}` : d);
    setDirty(true);
    setMsg('AI 初稿已填入编辑框,请校对后点保存');
  });

  const learn = () => run('learn', async () => {
    const updated = await platformRulesApi.learn(token);
    setRules(rs => rs.map(r => updated.find(u => u.platform === r.platform) || r));
    setMsg(`已从拒稿学习:${updated.map(u => u.platform).join('、')} 产出待采纳增量`);
  });

  const approve = () => run('approve', async () => {
    if (!active) return;
    const updated = await platformRulesApi.approvePending(active, token);
    setRules(rs => rs.map(r => r.platform === updated.platform ? updated : r));
    setMsg('增量已并入规则并生效');
  });

  const dismissPending = () => run('save', async () => {
    if (!active || !current) return;
    const updated = await platformRulesApi.update(active, current.rules_text, token, true);
    setRules(rs => rs.map(r => r.platform === updated.platform ? updated : r));
    setMsg('已忽略本次增量');
  });

  // One auto-adapt round: same service as the daily cron
  const sync = () => run('sync', async () => {
    const r = await platformRulesApi.sync(token);
    setRules(r.rules);
    const bits = [
      r.created.length ? `新增平台 ${r.created.join('、')}` : '',
      r.seeded.length ? `已生成初稿 ${r.seeded.join('、')}` : '',
      r.learned.length ? `产出待采纳增量 ${r.learned.join('、')}` : '',
      r.skipped_seed.length ? `本轮未排到初稿 ${r.skipped_seed.join('、')}(下轮补)` : '',
      r.seed_failed.length ? `初稿失败 ${r.seed_failed.join(';')}` : '',
      r.learn_failed.length ? `学习失败 ${r.learn_failed.join(';')}` : '',
    ].filter(Boolean);
    setMsg(bits.length
      ? `在用平台 ${r.in_use} 个 · ${bits.join(' · ')}`
      : `在用平台 ${r.in_use} 个,规则齐全,没有新增或改动`);
  });

  // Manually add a platform: enter the name -> create an empty row (then click AI draft or write it yourself)
  const addPlatform = () => run('add', async () => {
    const name = (await uiPrompt('输入平台名(与发文计划里的平台名一致才能被注入)', {
      title: '新增平台规则', placeholder: '如:雪球 / 百家号 / today头条',
    }))?.trim();
    if (!name) return;
    if (rules.some(r => r.platform === name)) {
      setActive(name);
      setMsg(`「${name}」已在列表里`);
      return;
    }
    const created = await platformRulesApi.update(name, '', token);
    await reload();
    setActive(created.platform);
    setMsg(`已新增「${created.platform}」—— 点「AI 生成初稿」或直接手写规则,保存后生效`);
  });

  const removePlatform = () => run('remove', async () => {
    if (!current) return;
    const ok = await uiConfirm(
      `删除「${current.platform}」的规则行?${current.in_use
        ? '这个平台还在投放中,删掉后下次自动适配会重新建行并生成 AI 初稿。' : ''}`,
      { title: '删除平台规则', okText: '删除', danger: true });
    if (!ok) return;
    await platformRulesApi.remove(current.platform, token);
    setActive(null);
    await reload(false);
    setMsg(`已删除「${current.platform}」`);
  });

  const totalRejected = rules.reduce((s, r) => s + r.rejected_count, 0);
  // In use but no rules yet -- these platforms' drafts are generated with zero guardrails; handle them first
  const naked = rules.filter(r => r.in_use && !r.rules_text.trim()).map(r => r.platform);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-primary">平台审核规则</h1>
          <p className="text-xs text-muted mt-1">
            每个发布平台一份审核红线,生成文章时自动注入;回填的平台拒稿原因可一键提炼成规则增量。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" disabled={busy !== null} onClick={addPlatform}
                  className="text-xs px-3 py-1.5 rounded-md disabled:opacity-40"
                  style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
            {busy === 'add' ? '…' : '+ 新增平台'}
          </button>
          <button type="button" disabled={busy !== null} onClick={sync}
                  title="发现在用但没规则的平台 → 自动建行 + AI 初稿;已有平台的拒稿原因 → 炼成待采纳增量。每天也会自动跑一轮。"
                  className="text-xs px-3 py-1.5 rounded-md disabled:opacity-40"
                  style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
            {busy === 'sync' ? '适配中…' : '🔄 自动适配'}
          </button>
          <button type="button" disabled={busy !== null || totalRejected === 0}
                  onClick={learn}
                  title={totalRejected === 0 ? '还没有已回填的平台拒稿原因(在内容审核详情里回填)' : ''}
                  className="text-xs px-3 py-1.5 rounded-md text-white disabled:opacity-40"
                  style={{ background: 'var(--accent-primary)' }}>
            {busy === 'learn' ? '学习中…' : `🧠 从拒稿学习(素材 ${totalRejected} 条)`}
          </button>
        </div>
      </div>

      {naked.length > 0 && (
        <div className="rounded-md p-2 mb-3 text-xs"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)',
                      border: '1px solid rgba(239,68,68,0.3)' }}>
          这些平台正在投放但还没有审核红线,生成时是**零护栏**:{naked.join('、')}
          —— 点右上「自动适配」自动补 AI 初稿,或逐个手写。
        </div>
      )}

      {err && (
        <div className="rounded-md p-2 mb-3 text-xs"
             style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>{err}</div>
      )}
      {msg && (
        <div className="rounded-md p-2 mb-3 text-xs"
             style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>{msg}</div>
      )}

      <div className="flex gap-4">
        {/* Platform list */}
        <div className="w-[180px] shrink-0 rounded-lg overflow-hidden self-start" style={card}>
          {rules.map(r => (
            <button key={r.platform} type="button"
                    onClick={() => setActive(r.platform)}
                    className="w-full text-left px-3 py-2.5 text-sm flex items-center justify-between"
                    style={{
                      background: active === r.platform ? 'var(--bg-tertiary)' : 'transparent',
                      color: r.rules_text ? 'var(--text-primary)' : 'var(--text-muted)',
                      borderBottom: '1px solid var(--border-color)',
                    }}>
              <span className="truncate" title={r.usage?.length ? `在用:${r.usage.join('、')}` : ''}>
                {r.in_use && !r.rules_text.trim() && (
                  <span style={{ color: 'var(--danger-fg)' }} title="在投放但没有规则,生成时零护栏">● </span>
                )}
                {r.platform}
              </span>
              <span className="flex items-center gap-1">
                {r.ai_draft && (
                  <span className="text-[10px] px-1 rounded"
                        style={{ background: 'rgba(139,92,246,0.15)', color: '#8b5cf6' }}
                        title="规则是 AI 自动生成的初稿,还没人工校对">AI</span>
                )}
                {r.pending_rules_text && (
                  <span className="text-[10px] px-1 rounded"
                        style={{ background: 'rgba(234,179,8,0.15)', color: 'var(--warn-fg)' }}
                        title="有待采纳的学习增量">增量</span>
                )}
                {r.rejected_count > 0 && (
                  <span className="text-[10px] text-muted tabular-nums"
                        title="已回填的平台拒稿数">{r.rejected_count}</span>
                )}
              </span>
            </button>
          ))}
        </div>

        {/* Editor */}
        <div className="flex-1 min-w-0">
          {!current ? (
            <div className="rounded-lg p-8 text-center text-sm text-muted" style={card}>加载中…</div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-lg p-3" style={card}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-sm font-medium text-primary">{current.platform} 审核红线</span>
                  <span className="text-[11px] text-muted">
                    {current.updated_at ? `更新于 ${current.updated_at.replace('T', ' ').slice(0, 16)}` : '尚未配置'}
                  </span>
                  {current.in_use && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                          style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}
                          title={`在用来源:${(current.usage || []).join('、')}`}>
                      在用 · {(current.usage || []).join('/')}
                    </span>
                  )}
                  {current.ai_draft && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                          style={{ background: 'rgba(139,92,246,0.14)', color: '#8b5cf6' }}
                          title="自动适配生成的初稿,已在生效但还没人工校对 —— 改完保存即视为已校对">
                      AI 初稿待校对
                    </span>
                  )}
                  <div className="flex-1" />
                  <button type="button" disabled={busy !== null} onClick={removePlatform}
                          className="text-xs px-2 py-1 rounded-md disabled:opacity-40"
                          style={{ color: 'var(--danger-fg)', border: '1px solid rgba(239,68,68,0.4)' }}>
                    {busy === 'remove' ? '…' : '删除'}
                  </button>
                  <button type="button" disabled={busy !== null} onClick={seed}
                          className="text-xs px-2.5 py-1 rounded-md disabled:opacity-40"
                          style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
                    {busy === 'seed' ? '生成中…' : '✨ AI 生成初稿'}
                  </button>
                  <button type="button" disabled={busy !== null || !dirty} onClick={save}
                          className="text-xs px-3 py-1 rounded-md text-white disabled:opacity-40"
                          style={{ background: 'var(--accent-primary)' }}>
                    {busy === 'save' ? '…' : '保存'}
                  </button>
                </div>
                <textarea
                  className="w-full rounded-md p-2.5 text-xs leading-5 font-mono"
                  style={{ ...inputStyle, minHeight: 320, resize: 'vertical' }}
                  placeholder={'每条一行,以「- 」开头,例如:\n- 禁止绝对化用语(最/第一/唯一/100%)\n- 正文不得出现微信号 / 二维码 / 站外链接\n- 医疗、金融领域表述不得承诺效果或收益'}
                  value={draft}
                  onChange={e => { setDraft(e.target.value); setDirty(true); }}
                />
              </div>

              {current.pending_rules_text && (
                <div className="rounded-lg p-3"
                     style={{ background: 'rgba(234,179,8,0.06)', border: '1px solid rgba(234,179,8,0.35)' }}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-medium" style={{ color: 'var(--warn-fg)' }}>
                      待采纳的学习增量
                    </span>
                    <span className="text-[11px] text-muted">
                      从该平台已回填的拒稿原因提炼;采纳后并入上方规则生效
                    </span>
                    <div className="flex-1" />
                    <button type="button" disabled={busy !== null} onClick={dismissPending}
                            className="text-xs px-2.5 py-1 rounded-md disabled:opacity-40"
                            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
                      忽略
                    </button>
                    <button type="button" disabled={busy !== null} onClick={approve}
                            className="text-xs px-3 py-1 rounded-md text-white disabled:opacity-40"
                            style={{ background: '#eab308' }}>
                      {busy === 'approve' ? '…' : '✓ 采纳并生效'}
                    </button>
                  </div>
                  <pre className="text-xs leading-5 whitespace-pre-wrap text-primary m-0">
                    {current.pending_rules_text}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
