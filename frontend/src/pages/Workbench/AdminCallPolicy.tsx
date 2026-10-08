// Call management (admin-only) -- "who can trigger batch runs, which execution path, run now or queue".
//
// Background: the external multi-tenant gateway long had a policy (engine allowlist + daily quota + queue priority), while internal
// batch runs bypassed it entirely -- neither constrained nor leaving any call records. Now both are folded into the same layer:
// channels provide default tiers, individual tokens / tenants can override; the internal tier is unlimited, but call records are written all the same.
//
// The two configurable execution dimensions are the page's two core columns:
//   Execution path   automation (browser account pool) / direct API / unrestricted
//   Execution timing run now / queue for scheduling / unrestricted
import { useCallback, useEffect, useState } from 'react';
import { PageHead } from '../../components/PageHead';
import { SectionCard } from '../../components/ui/SectionCard';
import { UiSelect } from '../../components/ui/Select';
import { toast } from '../../components/ui/toast';
import {
  CHANNEL_LABEL, DECISION_LABEL, EXEC_LABEL, RUN_LABEL, adminCallPolicyApi,
} from '../../services/adminCallPolicyApi';
import type {
  AuditPage, AuditSummary, CallPolicy, PolicyListOut,
} from '../../services/adminCallPolicyApi';

const inputStyle = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
  borderRadius: 6,
  padding: '4px 8px',
  width: 80,
};

const th: React.CSSProperties = {
  textAlign: 'left', padding: '8px 10px', fontWeight: 600,
  color: 'var(--text-secondary)', whiteSpace: 'nowrap',
};
const td: React.CSSProperties = { padding: '8px 10px', verticalAlign: 'middle' };

function num(v: string): number {
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

export function AdminCallPolicy() {
  const token = localStorage.getItem('token') || '';

  const [data, setData] = useState<PolicyListOut | null>(null);
  const [draft, setDraft] = useState<Record<string, Partial<CallPolicy>>>({});
  const [saving, setSaving] = useState('');
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [channel, setChannel] = useState('');
  const [decision, setDecision] = useState('');
  const [days, setDays] = useState('7');

  const loadPolicies = useCallback(async () => {
    try {
      setData(await adminCallPolicyApi.list(token));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '档位加载失败');
    }
  }, [token]);

  const loadAudit = useCallback(async () => {
    try {
      const d = parseInt(days, 10) || 7;
      const [s, a] = await Promise.all([
        adminCallPolicyApi.summary(token, d),
        adminCallPolicyApi.audit(token, { channel, decision, days: d, limit: 100 }),
      ]);
      setSummary(s);
      setAudit(a);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '调用记录加载失败');
    }
  }, [token, channel, decision, days]);

  useEffect(() => { void loadPolicies(); }, [loadPolicies]);
  useEffect(() => { void loadAudit(); }, [loadAudit]);

  const patch = (key: string, k: keyof CallPolicy, v: string | number) =>
    setDraft((d) => ({ ...d, [key]: { ...d[key], [k]: v } }));

  const save = async (p: CallPolicy) => {
    const body = draft[p.key];
    if (!body || Object.keys(body).length === 0) return;
    setSaving(p.key);
    try {
      const next = await adminCallPolicyApi.update(token, p.key, body);
      setData((d) => d && {
        ...d, policies: d.policies.map((x) => (x.key === p.key ? next : x)),
      });
      setDraft((d) => { const n = { ...d }; delete n[p.key]; return n; });
      toast.success(`已保存:${next.label || next.key}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving('');
    }
  };

  const val = <K extends keyof CallPolicy>(p: CallPolicy, k: K): CallPolicy[K] =>
    (draft[p.key]?.[k] ?? p[k]) as CallPolicy[K];

  return (
    <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <PageHead titleKey="nav.adminCallPolicy" titleFallback="调用管理" />

      <SectionCard
        title="调用档位"
        hint="内部与对外共用一套档。内部档不限额,但调用记录照样写;对外档按每日次数、每分钟次数收敛。单个令牌或租户可绑到自定义档,覆盖通道默认档。"
      >
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                <th style={th}>档位</th>
                <th style={th}>执行路径</th>
                <th style={th}>执行时机</th>
                <th style={th}>每日次数</th>
                <th style={th}>每分钟</th>
                <th style={th}>并发主题</th>
                <th style={th}>排队优先级</th>
                <th style={th}>禁用的动作</th>
                <th style={th}>状态</th>
                <th style={th} />
              </tr>
            </thead>
            <tbody>
              {(data?.policies || []).map((p) => (
                <tr key={p.key} style={{ borderBottom: '1px solid var(--border-color)' }}>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>{p.label || p.key}</div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                      {CHANNEL_LABEL[p.channel] || p.channel}
                      {p.unlimited ? ' · 不限额' : ''}
                    </div>
                  </td>
                  <td style={td}>
                    <UiSelect
                      value={String(val(p, 'exec_mode'))}
                      onChange={(v) => patch(p.key, 'exec_mode', v)}
                      options={(data?.exec_modes || []).map((m) => ({ value: m, label: EXEC_LABEL[m] || m }))}
                    />
                  </td>
                  <td style={td}>
                    <UiSelect
                      value={String(val(p, 'run_mode'))}
                      onChange={(v) => patch(p.key, 'run_mode', v)}
                      options={(data?.run_modes || []).map((m) => ({ value: m, label: RUN_LABEL[m] || m }))}
                    />
                  </td>
                  <td style={td}>
                    <input style={inputStyle} value={String(val(p, 'daily_runs'))}
                           onChange={(e) => patch(p.key, 'daily_runs', num(e.target.value))} />
                  </td>
                  <td style={td}>
                    <input style={inputStyle} value={String(val(p, 'rpm'))}
                           onChange={(e) => patch(p.key, 'rpm', num(e.target.value))} />
                  </td>
                  <td style={td}>
                    <input style={inputStyle} value={String(val(p, 'max_concurrent'))}
                           onChange={(e) => patch(p.key, 'max_concurrent', num(e.target.value))} />
                  </td>
                  <td style={td}>
                    <input style={inputStyle} value={String(val(p, 'priority'))}
                           onChange={(e) => patch(p.key, 'priority', num(e.target.value))} />
                  </td>
                  <td style={td}>
                    <input style={{ ...inputStyle, width: 160 }} value={String(val(p, 'deny_actions') || '')}
                           placeholder="留空=不禁"
                           onChange={(e) => patch(p.key, 'deny_actions', e.target.value)} />
                  </td>
                  <td style={td}>
                    <UiSelect
                      value={String(val(p, 'enabled'))}
                      onChange={(v) => patch(p.key, 'enabled', Number(v))}
                      options={[{ value: '1', label: '启用' }, { value: '0', label: '停用' }]}
                    />
                  </td>
                  <td style={td}>
                    <button
                      type="button"
                      disabled={!draft[p.key] || saving === p.key}
                      onClick={() => void save(p)}
                      style={{
                        padding: '5px 12px', borderRadius: 6, cursor: draft[p.key] ? 'pointer' : 'default',
                        border: '1px solid var(--border-color)',
                        background: draft[p.key] ? 'var(--accent)' : 'transparent',
                        color: draft[p.key] ? '#fff' : 'var(--text-secondary)',
                      }}
                    >
                      {saving === p.key ? '保存中' : '保存'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
          次数类填 0 表示不限。改完立刻生效,不用重启服务。
        </div>
      </SectionCard>

      <SectionCard title="调用记录" hint="内部与对外同一张表 —— 谁在什么时候触发了多少跑批,一处看全。">
        <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
          <UiSelect
            value={channel} onChange={setChannel}
            options={[{ value: '', label: '全部通道' },
                      ...(data?.channels || []).map((c) => ({ value: c, label: CHANNEL_LABEL[c] || c }))]}
          />
          <UiSelect
            value={decision} onChange={setDecision}
            options={[{ value: '', label: '全部结果' },
                      { value: 'allow', label: '放行' }, { value: 'deny', label: '拦截' }]}
          />
          <UiSelect
            value={days} onChange={setDays}
            options={[{ value: '1', label: '今天' }, { value: '7', label: '近 7 天' },
                      { value: '30', label: '近 30 天' }]}
          />
        </div>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          {(summary?.by_channel || []).map((s) => (
            <div key={s.channel} style={{
              border: '1px solid var(--border-color)', borderRadius: 8, padding: '10px 14px', minWidth: 150,
            }}>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {CHANNEL_LABEL[s.channel] || s.channel}
              </div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>{s.calls}</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                问题×引擎 {s.cells}{s.denied ? ` · 拦截 ${s.denied}` : ''}
              </div>
            </div>
          ))}
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                <th style={th}>时间</th>
                <th style={th}>通道</th>
                <th style={th}>档位</th>
                <th style={th}>动作</th>
                <th style={th}>账号</th>
                <th style={th}>规模</th>
                <th style={th}>执行路径</th>
                <th style={th}>执行时机</th>
                <th style={th}>结果</th>
              </tr>
            </thead>
            <tbody>
              {(audit?.items || []).map((r) => (
                <tr key={r.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                  <td style={td}>{r.ts ? r.ts.replace('T', ' ').slice(0, 19) : '—'}</td>
                  <td style={td}>{CHANNEL_LABEL[r.channel] || r.channel}</td>
                  <td style={td}>{r.policy_key}</td>
                  <td style={td}>{r.action}</td>
                  <td style={td}>{r.account_id ?? '—'}</td>
                  <td style={td}>{r.cells || '—'}</td>
                  <td style={td}>{EXEC_LABEL[r.exec_mode] || r.exec_mode || '—'}</td>
                  <td style={td}>{RUN_LABEL[r.run_mode] || r.run_mode || '—'}</td>
                  <td style={{ ...td, color: r.decision === 'deny' ? 'var(--danger, #d33)' : undefined }}>
                    {DECISION_LABEL[r.decision] || r.decision}
                    {r.reason ? ` · ${r.reason}` : ''}
                  </td>
                </tr>
              ))}
              {!audit?.items?.length && (
                <tr><td style={{ ...td, color: 'var(--text-secondary)' }} colSpan={9}>暂无记录</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
}
