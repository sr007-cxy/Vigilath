import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { Pagination } from '../../components/Pagination';
import { toast } from '../../components/ui/toast';
import { UiSelect } from '../../components/ui/Select';
import { uiConfirm } from '../../components/ui/dialogs';
import { smsSegments } from '../../utils/smsSegments';
import { SmsProviderIcon } from './SmsProviderIcon';
import {
  createSalesAgentSmsAccount,
  createSalesAgentSmsTemplate,
  disableSalesAgentSmsAccount,
  disableSalesAgentSmsTemplate,
  fetchSalesAgentSmsAccounts,
  fetchSalesAgentSmsDeliveries,
  fetchSalesAgentSmsSettings,
  fetchSalesAgentSmsStats,
  fetchSalesAgentSmsTemplates,
  testSalesAgentSmsAccount,
  updateSalesAgentSmsAccount,
  updateSalesAgentSmsSettings,
  updateSalesAgentSmsTemplate,
  type SalesAgentSmsAccount,
  type SalesAgentSmsAccountInput,
  type SalesAgentSmsDelivery,
  type SalesAgentSmsEffectiveSettings,
  type SalesAgentSmsProviderOption,
  type SalesAgentSmsProviderPreset,
  type SalesAgentSmsSetting,
  type SalesAgentSmsStats,
  type SalesAgentSmsTemplate,
  type SalesAgentSmsTemplateInput,
} from '../../services/salesAgentApi';

const card: CSSProperties = { background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 16 };
const input: CSSProperties = { background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)', borderRadius: 8, padding: '8px 10px' };
const fieldHint: CSSProperties = { color: 'var(--text-muted)', fontSize: 11, lineHeight: 1.5 };
const th: CSSProperties = { textAlign: 'left', padding: '9px 10px', color: 'var(--text-secondary)', fontSize: 12, whiteSpace: 'nowrap' };
const td: CSSProperties = { padding: '9px 10px', borderTop: '1px solid var(--border-color)', verticalAlign: 'top', fontSize: 13 };

const emptyAccount: SalesAgentSmsAccountInput = {
  display_name: '',
  provider: 'tencent_cloud',
  content_mode: '',
  region_scope: '',
  enabled: true,
  is_default: false,
  sign_name: '',
  sdk_app_id: '',
  api_key_id: '',
  api_secret: '',
  api_base_url: '',
  from_number: '',
};

const emptyTemplate: SalesAgentSmsTemplateInput = {
  template_code: '',
  name: '',
  body: '',
  param_slots: [],
  purpose: 'outreach',
  approved_status: 'pending',
  enabled: true,
};

function fmtTime(v?: string | null) {
  return v ? v.replace('T', ' ').slice(0, 19) : '—';
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    draft: '草稿',
    sending: '发送中',
    sent: '已发出',
    delivered: '已送达',
    failed: '失败',
    rejected: '已拦截',
    disabled: '已关闭',
    no_recipient: '无可发号码',
    ok: '成功',
  };
  return map[status] || status || '—';
}

function messageTypeLabel(value: string) {
  const map: Record<string, string> = { outreach: '首次触达', followup: '跟进', report: '报告', test: '通道测试' };
  return map[value] || value || '—';
}

function contentModeLabel(mode: string) {
  return mode === 'template' ? '报备模板' : mode === 'freeform' ? '自由文案' : mode || '—';
}

function regionScopeLabel(scope: string) {
  const map: Record<string, string> = { cn: '仅国内号码', intl: '仅国际号码', all: '国内 + 国际' };
  return map[scope] || scope || '—';
}

function approvalLabel(value: string) {
  const map: Record<string, string> = { approved: '已通过', pending: '待审核', rejected: '未通过' };
  return map[value] || value || '—';
}

function purposeLabel(value: string) {
  const map: Record<string, string> = { outreach: '首次触达', followup: '跟进', test: '测试' };
  return map[value] || value || '—';
}

function senderPolicyLabel(value?: string) {
  return value === 'round_robin' ? '多通道轮询' : '默认通道优先';
}

// GSM-03.38 basic character set. Text within it is billed as 7bit, otherwise the whole message switches to UCS-2.
type ButtonVariant = 'primary' | 'secondary' | 'success' | 'danger' | 'ghost';

const buttonVariantStyle: Record<ButtonVariant, CSSProperties> = {
  primary: { background: 'var(--accent-primary)', color: '#fff', borderColor: 'var(--accent-primary)' },
  secondary: { background: 'rgba(99, 102, 241, 0.08)', color: 'var(--accent-primary)', borderColor: 'rgba(99, 102, 241, 0.28)' },
  success: { background: 'var(--success-bg)', color: '#047857', borderColor: 'rgba(16, 185, 129, 0.35)' },
  danger: { background: 'var(--danger-bg)', color: '#b91c1c', borderColor: 'var(--danger-border)' },
  ghost: { background: 'transparent', color: 'var(--text-secondary)', borderColor: 'var(--border-color)' },
};

function ActionButton({
  children,
  variant = 'secondary',
  busy = false,
  disabled = false,
  type = 'button',
  onClick,
  style,
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  busy?: boolean;
  disabled?: boolean;
  type?: 'button' | 'submit';
  onClick?: () => void;
  style?: CSSProperties;
}) {
  return (
    <button
      type={type}
      disabled={disabled || busy}
      onClick={onClick ? () => onClick() : undefined}
      style={{
        ...input,
        ...buttonVariantStyle[variant],
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        fontWeight: 800,
        cursor: disabled || busy ? 'wait' : 'pointer',
        opacity: disabled || busy ? 0.65 : 1,
        whiteSpace: 'nowrap',
        flexShrink: 0,
        ...style,
      }}
    >
      {busy && <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
      {children}
    </button>
  );
}

function Drawer({
  open,
  title,
  subtitle,
  children,
  onClose,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex', justifyContent: 'flex-end' }}>
      <button type="button" aria-label="关闭抽屉" onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15, 23, 42, 0.38)', border: 'none', cursor: 'pointer' }} />
      <aside style={{ position: 'relative', width: 'min(600px, 100vw)', height: '100%', background: 'var(--bg-card)', borderLeft: '1px solid var(--border-color)', boxShadow: '-24px 0 48px rgba(15, 23, 42, 0.18)', overflowY: 'auto' }}>
        <div style={{ position: 'sticky', top: 0, zIndex: 1, background: 'var(--bg-card)', borderBottom: '1px solid var(--border-color)', padding: '18px 20px', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 18, fontWeight: 900 }}>{title}</h2>
            {subtitle && <p style={{ margin: '6px 0 0', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.6 }}>{subtitle}</p>}
          </div>
          <ActionButton variant="ghost" onClick={onClose}>关闭</ActionButton>
        </div>
        <div style={{ padding: 20 }}>{children}</div>
      </aside>
    </div>
  );
}

function EffectiveNotice({ effective }: { effective: SalesAgentSmsEffectiveSettings | null }) {
  if (!effective) return null;
  const reasons = effective.disabled_reasons || [];
  const ok = effective.sending_enabled;
  const account = effective.account;
  return (
    <div style={{ ...card, padding: 14, borderColor: ok ? 'var(--border-color)' : '#d97706' }}>
      <div style={{ fontWeight: 800, color: ok ? 'var(--text-primary)' : '#b45309' }}>
        {ok ? '当前真实发送已开启' : '当前真实发送已关闭'}
      </div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4, lineHeight: 1.7 }}>
        {ok
          ? `有效通道:${account?.display_name || account?.provider_label || '未配置'} · ${contentModeLabel(account?.content_mode || '')} · ${regionScopeLabel(account?.region_scope || '')} · 来源:${effective.account_source === 'global' ? '全局默认' : '当前账号'} · 调度策略:${senderPolicyLabel(effective.sender_selection_policy)}`
          : (reasons.length ? reasons.join('；') : '未配置可用短信通道或发送开关已关闭。')}
        <br />回执接收:{effective.receipt_enabled ? '开启(送达状态 + 上行退订)' : '关闭'}
        {' · '}日发送上限:{effective.daily_send_cap ? `${effective.daily_send_cap} 条` : '不限'}
        {' · '}同一号码冷却:{effective.per_contact_cooldown_days ? `${effective.per_contact_cooldown_days} 天` : '不限'}
      </div>
    </div>
  );
}

function ProviderPicker({
  presets,
  options,
  value,
  disabled,
  onPick,
}: {
  presets: SalesAgentSmsProviderPreset[];
  options: SalesAgentSmsProviderOption[];
  value: string;
  disabled: boolean;
  onPick: (preset: SalesAgentSmsProviderPreset) => void;
}) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {presets.map(preset => {
        const option = options.find(o => o.provider === preset.provider);
        // Unintegrated platforms are still listed but not selectable: let users know "coming later" instead of being rejected by the backend after configuring.
        const available = option ? option.available : true;
        const active = value === preset.provider;
        return (
          <button
            key={preset.key}
            type="button"
            disabled={disabled || !available}
            onClick={() => onPick(preset)}
            style={{
              ...input,
              textAlign: 'left',
              minWidth: 168,
              cursor: available ? 'pointer' : 'not-allowed',
              opacity: available ? 1 : 0.5,
              borderColor: active ? 'var(--accent-primary)' : 'var(--border-color)',
              boxShadow: active ? '0 0 0 2px rgba(99, 102, 241, 0.18)' : 'none',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <SmsProviderIcon iconKey={preset.key} size={22} />
              <div style={{ fontWeight: 800, color: 'var(--text-primary)', fontSize: 13 }}>{preset.label}</div>
            </div>
            <div style={{ ...fieldHint, marginTop: 4 }}>
              {contentModeLabel(preset.content_mode)} · {regionScopeLabel(preset.region_scope)}
              {!available && ' · 暂未接入'}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function AccountForm({
  value,
  preset,
  presets,
  options,
  editingId,
  busy,
  onChange,
  onPickProvider,
  onSubmit,
  onCancel,
}: {
  value: SalesAgentSmsAccountInput;
  preset: SalesAgentSmsProviderPreset | null;
  presets: SalesAgentSmsProviderPreset[];
  options: SalesAgentSmsProviderOption[];
  editingId: number | null;
  busy: boolean;
  onChange: (next: SalesAgentSmsAccountInput) => void;
  onPickProvider: (preset: SalesAgentSmsProviderPreset) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const set = (patch: Partial<SalesAgentSmsAccountInput>) => onChange({ ...value, ...patch });
  const keyIdLabel = preset?.key_id_label || 'API Key ID';
  const secretLabel = preset?.secret_label || 'API Secret';
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
      style={{ display: 'grid', gap: 14 }}
    >
      <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
        <span>短信平台</span>
        <ProviderPicker presets={presets} options={options} value={String(value.provider || '')} disabled={busy || editingId !== null} onPick={onPickProvider} />
        {editingId !== null && <span style={fieldHint}>已保存的通道不能改平台 —— 凭据字段含义完全不同,改了等于换一个通道,请新建。</span>}
        {preset?.hint && <span style={{ ...fieldHint, display: 'flex', gap: 8 }}><SmsProviderIcon iconKey={preset.key} size={22} style={{ marginTop: 2 }} /><span>{preset.hint}{preset.help_url ? <> <a href={preset.help_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>官方文档</a></> : null}</span></span>}
      </label>

      <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
        <span>备注名</span>
        <input style={input} value={value.display_name || ''} onChange={(e) => set({ display_name: e.target.value })} placeholder="例如:腾讯云-营销签名" />
      </label>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>文案模式</span>
          <UiSelect style={input} value={String(value.content_mode || preset?.content_mode || 'template')}
                    onChange={(val) => set({ content_mode: val })}
                    options={[
                      { value: 'template', label: '报备模板(国内必须)' },
                      { value: 'freeform', label: '自由文案(国际)' },
                    ]} />
          <span style={fieldHint}>国内运营商只放行已报备模板,自由文案会被直接拒发。</span>
        </label>
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>可发范围</span>
          <UiSelect style={input} value={String(value.region_scope || preset?.region_scope || 'cn')}
                    onChange={(val) => set({ region_scope: val })}
                    options={[
                      { value: 'cn', label: '仅国内号码(+86)' },
                      { value: 'intl', label: '仅国际号码' },
                      { value: 'all', label: '国内 + 国际' },
                    ]} />
          <span style={fieldHint}>发送时按收件号码的国家码挑通道,范围不匹配的通道不会被选中。</span>
        </label>
      </div>

      {preset?.needs_sign_name !== false && (
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>短信签名</span>
          <input style={input} value={value.sign_name || ''} onChange={(e) => set({ sign_name: e.target.value })} placeholder="平台已报备通过的签名,如:某某科技" />
          <span style={fieldHint}>必须与平台控制台里审核通过的签名完全一致,差一个字平台就会拒发。</span>
        </label>
      )}

      {preset?.needs_sdk_app_id && (
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>{preset.sdk_app_id_label || '应用 ID'}</span>
          <input style={input} value={value.sdk_app_id || ''} onChange={(e) => set({ sdk_app_id: e.target.value })} placeholder="如 1400123456" />
        </label>
      )}

      {preset?.needs_from_number && (
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>主叫号码</span>
          <input style={input} value={value.from_number || ''} onChange={(e) => set({ from_number: e.target.value })} placeholder="E.164 格式,如 +15551234567" />
        </label>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>{keyIdLabel}</span>
          <input style={input} value={value.api_key_id || ''} onChange={(e) => set({ api_key_id: e.target.value })} placeholder={editingId ? '留空表示不修改' : keyIdLabel} />
        </label>
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>{secretLabel}</span>
          <input style={input} type="password" autoComplete="new-password" value={value.api_secret || ''} onChange={(e) => set({ api_secret: e.target.value })} placeholder={editingId ? '留空表示不修改' : secretLabel} />
        </label>
      </div>
      <span style={fieldHint}>凭据加密保存,保存后不再回显;编辑时留空即保持原值。</span>

      <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
        <span>接口地址(可选)</span>
        <input style={input} value={value.api_base_url || ''} onChange={(e) => set({ api_base_url: e.target.value })} placeholder="留空用平台默认接入点" />
      </label>

      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
          <input type="checkbox" checked={value.enabled !== false} onChange={(e) => set({ enabled: e.target.checked })} /> 启用该通道
        </label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
          <input type="checkbox" checked={value.is_default === true} onChange={(e) => set({ is_default: e.target.checked })} /> 设为默认通道
        </label>
      </div>

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <ActionButton variant="ghost" disabled={busy} onClick={onCancel}>取消</ActionButton>
        <ActionButton variant="primary" type="submit" busy={busy}>{editingId ? '保存修改' : '新增通道'}</ActionButton>
      </div>
    </form>
  );
}

function CallbackUrlCell({ account }: { account: SalesAgentSmsAccount }) {
  if (!account.supports_callback) {
    // Alibaba Cloud uses an MNS queue rather than HTTP push; if we gave an address here, users would wait forever for a receipt that never comes.
    return <span style={fieldHint}>该平台不支持 HTTP 回执推送</span>;
  }
  const url = account.callback_url || '';
  if (!url) return <span style={fieldHint}>未配置公网地址,回执地址无法生成</span>;
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <code style={{ fontSize: 11, wordBreak: 'break-all', color: 'var(--text-secondary)' }}>{url}</code>
      <ActionButton
        variant="ghost"
        style={{ padding: '4px 8px', fontSize: 11 }}
        onClick={() => {
          void navigator.clipboard.writeText(url)
            .then(() => toast.success('回执地址已复制,粘到平台控制台的回调配置里'))
            .catch(() => toast.error('复制失败,请手动选中复制'));
        }}
      >复制回执地址</ActionButton>
    </div>
  );
}

function AccountList({
  accounts,
  presets,
  options,
  admin,
  defaultingId,
  disablingId,
  onCreate,
  onEdit,
  onDisable,
  onSetDefault,
  onTest,
  onManageTemplates,
}: {
  accounts: SalesAgentSmsAccount[];
  presets: SalesAgentSmsProviderPreset[];
  options: SalesAgentSmsProviderOption[];
  admin: boolean;
  defaultingId: number | null;
  disablingId: number | null;
  onCreate: () => void;
  onEdit: (a: SalesAgentSmsAccount) => void;
  onDisable: (a: SalesAgentSmsAccount) => void;
  onSetDefault: (a: SalesAgentSmsAccount) => void;
  onTest: (a: SalesAgentSmsAccount) => void;
  onManageTemplates: (a: SalesAgentSmsAccount) => void;
}) {
  const labelOf = (a: SalesAgentSmsAccount) =>
    a.provider_label || presets.find(p => p.provider === a.provider)?.label || options.find(o => o.provider === a.provider)?.label || a.provider;
  return (
    <div style={{ ...card, overflow: 'hidden' }}>
      <div style={{ padding: 14, display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 16, fontWeight: 800 }}>{admin ? '全局/用户短信通道' : '我的短信通道'}</h2>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{accounts.length} 个通道</span>
        </div>
        <ActionButton variant="primary" onClick={onCreate}>新增通道</ActionButton>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1080 }}>
          <thead><tr><th style={th}>备注名</th><th style={th}>平台</th><th style={th}>签名/主叫</th><th style={th}>状态</th><th style={th}>回执地址</th><th style={th}>测试</th><th style={th}>操作</th></tr></thead>
          <tbody>
            {accounts.map(a => (
              <tr key={a.id}>
                <td style={td}>
                  <div style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{a.display_name || labelOf(a)}</div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{a.scope}{a.user_id ? ` · 用户 ${a.user_id}` : ''}{a.is_default ? ' · 默认' : ''}</div>
                </td>
                <td style={td}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {/* Recognizing channel types by color tile is faster than by text -- especially when a column mixes CN template channels and international channels */}
                    <SmsProviderIcon iconKey={a.provider} size={22} />
                    <span style={{ color: 'var(--text-primary)' }}>{labelOf(a)}</span>
                  </div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 4 }}>{contentModeLabel(a.content_mode)} · {regionScopeLabel(a.region_scope)}</div>
                </td>
                <td style={td}>
                  {a.sign_name || '—'}
                  <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{a.from_number ? `主叫 ${a.from_number}` : (a.sdk_app_id ? `应用 ${a.sdk_app_id}` : '')}</div>
                </td>
                <td style={td}>{a.enabled ? '启用' : '停用'} · {a.has_api_secret ? '密钥已保存' : '未保存密钥'}</td>
                <td style={{ ...td, maxWidth: 280 }}><CallbackUrlCell account={a} /></td>
                <td style={td}>
                  {a.last_test_status ? `${statusLabel(a.last_test_status)} ${fmtTime(a.last_tested_at)}` : '未测试'}
                  {a.last_test_error && <div style={{ color: '#b91c1c', fontSize: 12 }}>{a.last_test_error}</div>}
                </td>
                <td style={td}>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <ActionButton variant="secondary" onClick={() => onEdit(a)}>编辑</ActionButton>
                    {a.content_mode === 'template' && <ActionButton variant="secondary" onClick={() => onManageTemplates(a)}>模板</ActionButton>}
                    <ActionButton variant="success" busy={defaultingId === a.id} disabled={a.is_default} onClick={() => onSetDefault(a)}>{a.is_default ? '已默认' : '设默认'}</ActionButton>
                    <ActionButton variant="primary" onClick={() => onTest(a)}>测试</ActionButton>
                    <ActionButton variant="danger" busy={disablingId === a.id} disabled={!a.enabled} onClick={() => onDisable(a)}>{a.enabled ? '停用' : '已停用'}</ActionButton>
                  </div>
                </td>
              </tr>
            ))}
            {!accounts.length && <tr><td style={td} colSpan={7}>暂无短信通道,点击右上角新增。国内发送需要先在平台报备签名与模板。</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TemplateForm({
  value,
  busy,
  editingId,
  onChange,
  onSubmit,
  onCancel,
}: {
  value: SalesAgentSmsTemplateInput;
  busy: boolean;
  editingId: number | null;
  onChange: (next: SalesAgentSmsTemplateInput) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const slots = value.param_slots || [];
  const set = (patch: Partial<SalesAgentSmsTemplateInput>) => onChange({ ...value, ...patch });
  const setSlot = (index: number, patch: Partial<{ label: string; max_len: number }>) => {
    set({ param_slots: slots.map((s, i) => (i === index ? { ...s, ...patch } : s)) });
  };
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(); }} style={{ display: 'grid', gap: 14 }}>
      <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
        <span>模板 ID</span>
        <input style={input} value={value.template_code || ''} onChange={(e) => set({ template_code: e.target.value })} placeholder="腾讯云 TemplateId / 阿里云 TemplateCode" />
        <span style={fieldHint}>平台审核通过后下发的编号,发送时下发的是它 + 变量,不是这里的正文。</span>
      </label>
      <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
        <span>模板名称</span>
        <input style={input} value={value.name || ''} onChange={(e) => set({ name: e.target.value })} placeholder="留空则用模板 ID" />
      </label>
      <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
        <span>模板正文</span>
        <textarea style={{ ...input, minHeight: 96, resize: 'vertical' }} value={value.body || ''} onChange={(e) => set({ body: e.target.value })} placeholder="与平台报备正文完全一致,变量写成 {1}{2}" />
        <span style={fieldHint}>这里只用于预览和长度估算;正文改了不等于平台改了,平台侧要重新报备。</span>
      </label>

      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>变量槽位({slots.length})</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <ActionButton variant="secondary" style={{ padding: '6px 10px', fontSize: 12 }}
                          onClick={() => set({ param_slots: [...slots, { index: slots.length + 1, label: `变量${slots.length + 1}`, max_len: 0 }] })}>
              添加变量
            </ActionButton>
            <ActionButton variant="ghost" style={{ padding: '6px 10px', fontSize: 12 }} disabled={!slots.length}
                          onClick={() => set({ param_slots: slots.slice(0, -1) })}>
              删除末位
            </ActionButton>
          </div>
        </div>
        <span style={fieldHint}>顺序即平台的 {'{1}{2}'} 位置,不能跳号;max_len 填 0 表示不限。</span>
        {slots.map((slot, i) => (
          <div key={slot.index || i} style={{ display: 'grid', gridTemplateColumns: '48px 1fr 120px', gap: 8, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{`{${i + 1}}`}</span>
            <input style={input} value={slot.label || ''} onChange={(e) => setSlot(i, { label: e.target.value })} placeholder="变量含义,如「企业名称」" />
            <input style={input} type="number" min={0} value={slot.max_len ?? 0} onChange={(e) => setSlot(i, { max_len: Number(e.target.value) || 0 })} placeholder="最大长度" />
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>用途</span>
          <UiSelect style={input} value={String(value.purpose || 'outreach')} onChange={(val) => set({ purpose: val })}
                    options={[
                      { value: 'outreach', label: '首次触达' },
                      { value: 'followup', label: '跟进' },
                      { value: 'test', label: '测试' },
                    ]} />
        </label>
        <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
          <span>平台审核状态</span>
          <UiSelect style={input} value={String(value.approved_status || 'pending')} onChange={(val) => set({ approved_status: val })}
                    options={[
                      { value: 'pending', label: '待审核' },
                      { value: 'approved', label: '已通过' },
                      { value: 'rejected', label: '未通过' },
                    ]} />
          <span style={fieldHint}>只有「已通过」且启用的模板会被自动触达选用。</span>
        </label>
      </div>

      <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
        <input type="checkbox" checked={value.enabled !== false} onChange={(e) => set({ enabled: e.target.checked })} /> 启用该模板
      </label>

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <ActionButton variant="ghost" disabled={busy} onClick={onCancel}>取消</ActionButton>
        <ActionButton variant="primary" type="submit" busy={busy}>{editingId ? '保存修改' : '新增模板'}</ActionButton>
      </div>
    </form>
  );
}

function TestSmsDialog({
  account,
  templates,
  value,
  busy,
  onChange,
  onClose,
  onSubmit,
}: {
  account: SalesAgentSmsAccount | null;
  templates: SalesAgentSmsTemplate[];
  value: { to_phone: string; content: string; template_code: string; template_params: string[] };
  busy: boolean;
  onChange: (next: { to_phone: string; content: string; template_code: string; template_params: string[] }) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  if (!account) return null;
  const isTemplate = account.content_mode === 'template';
  const template = templates.find(t => t.template_code === value.template_code) || null;
  const slots = template?.param_slots || [];
  const preview = template
    // Replace by the index declared in the slot, not the array position -- backend `_normalize_param_slots` guarantees indexes are contiguous from 1,
    // but what actually decides the position in the template is the index itself
    ? slots.reduce((body, slot, i) => body.replace(`{${slot.index}}`, value.template_params[i] || `{${slot.index}}`), template.body || '')
    : value.content;
  const segments = smsSegments(preview);
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 70, display: 'grid', placeItems: 'center', padding: 18 }}>
      <button type="button" aria-label="关闭测试短信弹窗" onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15, 23, 42, 0.45)', border: 'none', cursor: 'pointer' }} />
      <div style={{ position: 'relative', width: 'min(620px, 100%)', maxHeight: '88vh', ...card, boxShadow: '0 24px 70px rgba(15, 23, 42, 0.24)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <SmsProviderIcon iconKey={account.provider} size={26} />
            <div>
              <div style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 900 }}>发送测试短信</div>
              <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 4 }}>
                {account.display_name || account.provider_label} · {contentModeLabel(account.content_mode)} · {regionScopeLabel(account.region_scope)}
              </div>
            </div>
          </div>
        </div>
        <div style={{ padding: 20, display: 'grid', gap: 14, overflowY: 'auto' }}>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
            <span>接收号码</span>
            <input style={input} value={value.to_phone} onChange={(e) => onChange({ ...value, to_phone: e.target.value })} placeholder="国内直接填 11 位手机号,国际填 +15551234567" />
            <span style={fieldHint}>座机、400/800 号码收不到短信,会被直接拒绝。</span>
          </label>

          {isTemplate ? (
            <>
              <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
                <span>使用模板</span>
                <UiSelect style={input} value={value.template_code}
                          onChange={(val) => onChange({ ...value, template_code: val, template_params: [] })}
                          options={[
                            { value: '', label: templates.length ? '请选择已报备模板' : '该通道下还没有模板,请先添加' },
                            ...templates.map(t => ({ value: t.template_code, label: `${t.name || t.template_code} · ${approvalLabel(t.approved_status)}` })),
                          ]} />
                <span style={fieldHint}>模板通道只能发已报备模板,自由文案发不出去。</span>
              </label>
              {slots.map((slot, i) => (
                <label key={slot.index || i} style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
                  <span>{`{${i + 1}} ${slot.label || `变量${i + 1}`}`}{slot.max_len ? ` · 最长 ${slot.max_len} 字` : ''}</span>
                  <input style={input} value={value.template_params[i] || ''}
                         onChange={(e) => {
                           const next = [...value.template_params];
                           while (next.length < slots.length) next.push('');
                           next[i] = e.target.value;
                           onChange({ ...value, template_params: next });
                         }} />
                </label>
              ))}
            </>
          ) : (
            <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
              <span>短信正文</span>
              <textarea style={{ ...input, minHeight: 96, resize: 'vertical' }} value={value.content} onChange={(e) => onChange({ ...value, content: e.target.value })} />
              <span style={fieldHint}>留空则发一条默认测试文案。</span>
            </label>
          )}

          {!!preview && (
            <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 12 }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: 12, fontWeight: 800, marginBottom: 6 }}>
                实际下发预览 · {preview.length} 字 · 约 {segments} 条计费
              </div>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 12, color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.7 }}>{preview}</pre>
            </div>
          )}
        </div>
        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border-color)', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <ActionButton variant="ghost" disabled={busy} onClick={onClose}>取消</ActionButton>
          <ActionButton variant="primary" busy={busy} onClick={onSubmit}>发送测试</ActionButton>
        </div>
      </div>
    </div>
  );
}

export function SmsDeliveryContentDialog({ delivery, onClose }: { delivery: SalesAgentSmsDelivery | null; onClose: () => void }) {
  if (!delivery) return null;
  const content = delivery.content || delivery.content_preview || '暂无可查看正文。';
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 70, display: 'grid', placeItems: 'center', padding: 18 }}>
      <button type="button" aria-label="关闭短信内容弹窗" onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15, 23, 42, 0.45)', border: 'none', cursor: 'pointer' }} />
      <div style={{ position: 'relative', width: 'min(720px, 100%)', maxHeight: '88vh', ...card, boxShadow: '0 24px 70px rgba(15, 23, 42, 0.24)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border-color)', background: 'linear-gradient(135deg, rgba(16,185,129,.12), rgba(59,130,246,.10))' }}>
          <div style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 900 }}>短信内容</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 6 }}>{messageTypeLabel(delivery.message_type)} · {statusLabel(delivery.status)} · {fmtTime(delivery.sent_at || delivery.failed_at || delivery.created_at)}</div>
        </div>
        <div style={{ padding: 20, display: 'grid', gap: 12, overflowY: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: '8px 12px', fontSize: 13, lineHeight: 1.6 }}>
            <div style={{ color: 'var(--text-muted)' }}>接收号码</div><div style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{delivery.recipient_phone || '—'}{delivery.recipient_name ? ` (${delivery.recipient_name})` : ''}{delivery.recipient_country ? ` · ${delivery.recipient_country}` : ''}</div>
            <div style={{ color: 'var(--text-muted)' }}>通道</div><div style={{ color: 'var(--text-primary)' }}>{delivery.provider || '—'}{delivery.sign_name ? ` · 签名【${delivery.sign_name}】` : ''}</div>
            {delivery.template_code && (
              <>
                <div style={{ color: 'var(--text-muted)' }}>模板</div>
                <div style={{ color: 'var(--text-primary)' }}>{delivery.template_code}{delivery.template_params?.length ? ` · 变量 ${delivery.template_params.join(' / ')}` : ''}</div>
              </>
            )}
            <div style={{ color: 'var(--text-muted)' }}>计费</div><div style={{ color: 'var(--text-primary)' }}>{delivery.segments || 0} 条 · 平台消息号 {delivery.provider_message_id || '—'}</div>
            <div style={{ color: 'var(--text-muted)' }}>互动</div><div style={{ color: 'var(--text-primary)' }}>点击 {delivery.click_count || 0} · 回复 {delivery.reply_count || 0}{delivery.opted_out_at ? ` · 已退订 ${fmtTime(delivery.opted_out_at)}` : ''}</div>
            {delivery.failure_reason && (
              <>
                <div style={{ color: 'var(--text-muted)' }}>失败原因</div>
                <div style={{ color: '#b91c1c' }}>{delivery.failure_reason}{delivery.failure_code ? `(${delivery.failure_code})` : ''}</div>
              </>
            )}
          </div>
          <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 14 }}>
            <div style={{ color: 'var(--text-secondary)', fontSize: 12, fontWeight: 800, marginBottom: 8 }}>正文</div>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: 12, padding: 14, color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.7 }}>{content}</pre>
          </div>
        </div>
        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border-color)', display: 'flex', justifyContent: 'flex-end' }}>
          <ActionButton variant="primary" onClick={onClose}>关闭</ActionButton>
        </div>
      </div>
    </div>
  );
}

/**
 * SMS delivery record table. Deliberately not reusing the email `DeliveryTable` -- the columns that matter on each side barely overlap
 * (sending mailbox/opens vs. signature template/billed segments/unsubscribes), and cramming them into one component would branch every cell,
 * making it harder to read than two tables.
 */
export function SmsDeliveryTable({ deliveries, onViewContent }: { deliveries: SalesAgentSmsDelivery[]; onViewContent: (delivery: SalesAgentSmsDelivery) => void }) {
  return (
    <div style={{ ...card, overflow: 'hidden' }}>
      <div style={{ padding: 14 }}>
        <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 16, fontWeight: 800 }}>发送记录</h2>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1040 }}>
          <thead><tr><th style={th}>企业/用户</th><th style={th}>类型</th><th style={th}>接收号码</th><th style={th}>通道/模板</th><th style={th}>状态</th><th style={th}>互动</th><th style={th}>条数</th><th style={th}>时间</th><th style={th}>失败原因</th><th style={{ ...th, width: 96 }}>内容</th></tr></thead>
          <tbody>
            {deliveries.map(d => (
              <tr key={d.id}>
                <td style={td}><div style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{d.company_name || d.company?.name || '—'}</div><div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{d.user_email || `用户 ${d.user_id}`}</div></td>
                <td style={td}>{messageTypeLabel(d.message_type)}</td>
                <td style={td}>{d.recipient_phone || '—'}<div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{d.recipient_name || ''}{d.recipient_country ? ` · ${d.recipient_country}` : ''}</div></td>
                <td style={td}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <SmsProviderIcon iconKey={d.provider || ''} size={18} />
                    <span>{d.provider || '—'}</span>
                  </div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 3 }}>{d.template_code ? `模板 ${d.template_code}` : '自由文案'}</div>
                </td>
                <td style={td}>{statusLabel(d.status)}{d.opted_out_at && <div style={{ color: '#b45309', fontSize: 12 }}>已退订</div>}</td>
                <td style={td}>点击 {d.click_count || 0}<div style={{ color: 'var(--text-muted)', fontSize: 12 }}>回复 {d.reply_count || 0}</div></td>
                <td style={td}>{d.segments || 0}</td>
                <td style={td}>{fmtTime(d.delivered_at || d.sent_at || d.failed_at || d.created_at)}</td>
                <td style={{ ...td, maxWidth: 240 }}>{d.failure_reason || '—'}</td>
                <td style={{ ...td, whiteSpace: 'nowrap', width: 96 }}><ActionButton variant="secondary" style={{ minWidth: 76, padding: '7px 10px' }} onClick={() => onViewContent(d)}>查看内容</ActionButton></td>
              </tr>
            ))}
            {!deliveries.length && <tr><td style={td} colSpan={10}>暂无短信发送记录。</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatCard({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div style={{ ...card, padding: 14, minWidth: 130 }}>
      <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{label}</div>
      <div style={{ color: 'var(--text-primary)', fontSize: 24, fontWeight: 800, lineHeight: 1.2 }}>{value}</div>
      {hint && <div style={{ color: 'var(--text-secondary)', fontSize: 11, marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

export function SalesAgentSmsManager({ admin = false }: { admin?: boolean }) {
  const [settings, setSettings] = useState<SalesAgentSmsSetting | null>(null);
  const [effective, setEffective] = useState<SalesAgentSmsEffectiveSettings | null>(null);
  const [providerOptions, setProviderOptions] = useState<SalesAgentSmsProviderOption[]>([]);
  const [providerPresets, setProviderPresets] = useState<SalesAgentSmsProviderPreset[]>([]);
  const [accounts, setAccounts] = useState<SalesAgentSmsAccount[]>([]);
  const [form, setForm] = useState<SalesAgentSmsAccountInput>(emptyAccount);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [accountDrawerOpen, setAccountDrawerOpen] = useState(false);

  const [templateAccount, setTemplateAccount] = useState<SalesAgentSmsAccount | null>(null);
  const [templates, setTemplates] = useState<SalesAgentSmsTemplate[]>([]);
  const [templateForm, setTemplateForm] = useState<SalesAgentSmsTemplateInput>(emptyTemplate);
  const [editingTemplateId, setEditingTemplateId] = useState<number | null>(null);
  const [savingTemplate, setSavingTemplate] = useState(false);

  const [testingAccount, setTestingAccount] = useState<SalesAgentSmsAccount | null>(null);
  const [testTemplates, setTestTemplates] = useState<SalesAgentSmsTemplate[]>([]);
  const [testForm, setTestForm] = useState({ to_phone: '', content: '', template_code: '', template_params: [] as string[] });

  const [deliveries, setDeliveries] = useState<SalesAgentSmsDelivery[]>([]);
  const [stats, setStats] = useState<SalesAgentSmsStats | null>(null);
  const [viewingDelivery, setViewingDelivery] = useState<SalesAgentSmsDelivery | null>(null);
  const [status, setStatus] = useState('');
  const [query, setQuery] = useState('');
  const [deliveryTotal, setDeliveryTotal] = useState(0);
  const [deliveryPage, setDeliveryPage] = useState(1);
  const [deliveryPageSize, setDeliveryPageSize] = useState(20);

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [defaultingId, setDefaultingId] = useState<number | null>(null);
  const [disablingId, setDisablingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const pageTitle = admin ? '短信配置管理' : '短信配置';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, a] = await Promise.all([
        fetchSalesAgentSmsSettings(admin),
        fetchSalesAgentSmsAccounts(admin, admin ? 'all' : 'user'),
      ]);
      setSettings(s.settings);
      setEffective(s.effective);
      setProviderOptions(s.provider_options || a.provider_options || []);
      setProviderPresets(s.provider_presets || a.provider_presets || []);
      setAccounts(a.accounts || []);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [admin]);

  const loadDeliveries = useCallback(async () => {
    try {
      const [d, st] = await Promise.all([
        fetchSalesAgentSmsDeliveries({ status, q: query, limit: deliveryPageSize, offset: (deliveryPage - 1) * deliveryPageSize }, admin),
        fetchSalesAgentSmsStats({}, admin),
      ]);
      setDeliveries(d.deliveries || []);
      setDeliveryTotal(d.total || 0);
      setStats(st);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '发送记录加载失败');
    }
  }, [admin, deliveryPage, deliveryPageSize, query, status]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setDeliveryPage(1); }, [status, query]);
  useEffect(() => { void loadDeliveries(); }, [loadDeliveries]);

  const scopedAccounts = useMemo(() => (admin ? accounts.filter(a => a.scope === 'global') : accounts), [accounts, admin]);
  const currentPreset = useMemo(
    () => providerPresets.find(p => p.provider === form.provider) || null,
    [providerPresets, form.provider],
  );

  const saveSettings = async (patch: Partial<SalesAgentSmsSetting>) => {
    try {
      const next = await updateSalesAgentSmsSettings({
        sending_enabled: settings?.sending_enabled ?? true,
        receipt_enabled: settings?.receipt_enabled ?? true,
        default_account_id: settings?.default_account_id ?? null,
        sender_selection_policy: settings?.sender_selection_policy || 'default_only',
        daily_send_cap: settings?.daily_send_cap ?? 0,
        per_contact_cooldown_days: settings?.per_contact_cooldown_days ?? 0,
        ...patch,
      }, admin);
      setSettings(next.settings);
      setEffective(next.effective);
      toast.success('短信设置已保存');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败');
    }
  };

  const openCreateAccount = () => {
    setEditingId(null);
    const preset = providerPresets[0];
    setForm(preset
      ? { ...emptyAccount, provider: preset.provider, content_mode: preset.content_mode, region_scope: preset.region_scope }
      : emptyAccount);
    setAccountDrawerOpen(true);
  };

  const closeAccountDrawer = () => {
    if (saving) return;
    setAccountDrawerOpen(false);
    setEditingId(null);
    setForm(emptyAccount);
  };

  const pickProvider = (preset: SalesAgentSmsProviderPreset) => {
    // Switching platforms makes mode/scope follow the preset -- overwriting manually changed values here is intentional:
    // Tencent Cloud configured as freeform can never send, and keeping the old value would only make people think it's set up.
    setForm(prev => ({ ...prev, provider: preset.provider, content_mode: preset.content_mode, region_scope: preset.region_scope }));
  };

  const submitAccount = async () => {
    setSaving(true);
    try {
      if (editingId) await updateSalesAgentSmsAccount(editingId, form, admin);
      else await createSalesAgentSmsAccount(form, admin);
      toast.success('短信通道已保存');
      setForm(emptyAccount);
      setEditingId(null);
      setAccountDrawerOpen(false);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const editAccount = (a: SalesAgentSmsAccount) => {
    setEditingId(a.id);
    setAccountDrawerOpen(true);
    setForm({
      display_name: a.display_name,
      provider: a.provider,
      content_mode: a.content_mode,
      region_scope: a.region_scope,
      enabled: a.enabled,
      is_default: a.is_default,
      sign_name: a.sign_name || '',
      sdk_app_id: a.sdk_app_id || '',
      api_key_id: '',
      api_secret: '',
      api_base_url: a.api_base_url || '',
      from_number: a.from_number || '',
    });
  };

  const disableAccount = async (a: SalesAgentSmsAccount) => {
    if (!(await uiConfirm(`确认停用短信通道 ${a.display_name || a.provider}?`, { danger: true }))) return;
    setDisablingId(a.id);
    try {
      await disableSalesAgentSmsAccount(a.id, admin);
      toast.success('已停用');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '停用失败');
    } finally {
      setDisablingId(null);
    }
  };

  const setDefaultAccount = async (a: SalesAgentSmsAccount) => {
    setDefaultingId(a.id);
    try {
      await updateSalesAgentSmsAccount(a.id, { is_default: true }, admin);
      await saveSettings({ default_account_id: a.id });
      toast.success('默认短信通道已更新');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '设置默认失败');
    } finally {
      setDefaultingId(null);
    }
  };

  const openTemplates = async (a: SalesAgentSmsAccount) => {
    setTemplateAccount(a);
    setEditingTemplateId(null);
    setTemplateForm(emptyTemplate);
    try {
      const r = await fetchSalesAgentSmsTemplates(a.id, admin);
      setTemplates(r.templates || []);
    } catch (e) {
      setTemplates([]);
      toast.error(e instanceof Error ? e.message : '模板加载失败');
    }
  };

  const submitTemplate = async () => {
    if (!templateAccount) return;
    setSavingTemplate(true);
    try {
      if (editingTemplateId) await updateSalesAgentSmsTemplate(editingTemplateId, templateForm, admin);
      else await createSalesAgentSmsTemplate(templateAccount.id, templateForm, admin);
      toast.success('模板已保存');
      setTemplateForm(emptyTemplate);
      setEditingTemplateId(null);
      const r = await fetchSalesAgentSmsTemplates(templateAccount.id, admin);
      setTemplates(r.templates || []);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '模板保存失败');
    } finally {
      setSavingTemplate(false);
    }
  };

  const editTemplate = (t: SalesAgentSmsTemplate) => {
    setEditingTemplateId(t.id);
    setTemplateForm({
      template_code: t.template_code,
      name: t.name,
      body: t.body,
      param_slots: t.param_slots || [],
      purpose: t.purpose,
      approved_status: t.approved_status,
      enabled: t.enabled,
    });
  };

  const disableTemplate = async (t: SalesAgentSmsTemplate) => {
    if (!templateAccount) return;
    if (!(await uiConfirm(`确认停用模板 ${t.name || t.template_code}?`, { danger: true }))) return;
    try {
      await disableSalesAgentSmsTemplate(t.id, admin);
      const r = await fetchSalesAgentSmsTemplates(templateAccount.id, admin);
      setTemplates(r.templates || []);
      toast.success('模板已停用');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '停用失败');
    }
  };

  const openTestDialog = async (a: SalesAgentSmsAccount) => {
    setTestingAccount(a);
    setTestForm({
      to_phone: '',
      content: a.content_mode === 'freeform'
        ? `这是一条来自 ${a.display_name || a.provider} 的 Sales Agent 通道测试短信。回复 TD 退订。`
        : '',
      template_code: '',
      template_params: [],
    });
    if (a.content_mode !== 'template') {
      setTestTemplates([]);
      return;
    }
    try {
      const r = await fetchSalesAgentSmsTemplates(a.id, admin);
      setTestTemplates(r.templates || []);
    } catch (e) {
      setTestTemplates([]);
      toast.error(e instanceof Error ? e.message : '模板加载失败');
    }
  };

  const submitTestSms = async () => {
    if (!testingAccount) return;
    setTesting(true);
    try {
      const r = await testSalesAgentSmsAccount(testingAccount.id, {
        to_phone: testForm.to_phone,
        content: testForm.content,
        template_code: testForm.template_code,
        template_params: testForm.template_params,
      }, admin);
      if (r.ok) {
        toast.success('测试短信已发出');
        setTestingAccount(null);
      } else {
        toast.error(r.error || r.delivery?.failure_reason || '测试失败');
      }
      await load();
      await loadDeliveries();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '测试失败');
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-5" style={{ padding: admin ? '20px 24px' : 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 24, fontWeight: 900 }}>{pageTitle}</h1>
          <p style={{ margin: '6px 0 0', color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.7 }}>
            短信通道、平台凭据和发送闸门都在这里配置。国内通道(腾讯云/阿里云)只能发已报备的签名与模板，
            国际通道(Twilio)才能发自由文案；发送时按收件号码的国家码自动挑通道。
          </p>
        </div>
        <button type="button" onClick={() => { void load(); void loadDeliveries(); }} style={{ ...input, cursor: 'pointer' }}>{loading ? '刷新中…' : '刷新'}</button>
      </div>

      <EffectiveNotice effective={effective} />

      <div style={{ ...card, padding: 16 }}>
        <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 16, fontWeight: 800 }}>{admin ? '全局开关' : '我的开关'}</h2>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, marginTop: 12 }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
            <input type="checkbox" checked={settings?.sending_enabled !== false} onChange={(e) => void saveSettings({ sending_enabled: e.target.checked })} /> 允许真实发送短信
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
            <input type="checkbox" checked={settings?.receipt_enabled !== false} onChange={(e) => void saveSettings({ receipt_enabled: e.target.checked })} /> 接收平台回执与上行
          </label>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13, minWidth: 220 }}>
            <span>通道调度策略</span>
            <UiSelect style={input} value={settings?.sender_selection_policy || 'default_only'}
                      onChange={(val) => void saveSettings({ sender_selection_policy: val })}
                      options={[
                        { value: 'default_only', label: '默认通道优先' },
                        { value: 'round_robin', label: '多通道轮询' },
                      ]} />
            <span style={fieldHint}>先按号码国家码筛出可发通道，再按该策略选一个。</span>
          </label>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13, minWidth: 180 }}>
            <span>每日发送上限</span>
            <input style={input} type="number" min={0} defaultValue={settings?.daily_send_cap ?? 0}
                   onBlur={(e) => {
                     const next = Number(e.target.value) || 0;
                     if (next !== (settings?.daily_send_cap ?? 0)) void saveSettings({ daily_send_cap: next });
                   }} />
            <span style={fieldHint}>0 = 不限。超出后当天的短信直接拦截，不会静默丢弃。</span>
          </label>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13, minWidth: 180 }}>
            <span>同号码冷却天数</span>
            <input style={input} type="number" min={0} defaultValue={settings?.per_contact_cooldown_days ?? 0}
                   onBlur={(e) => {
                     const next = Number(e.target.value) || 0;
                     if (next !== (settings?.per_contact_cooldown_days ?? 0)) void saveSettings({ per_contact_cooldown_days: next });
                   }} />
            <span style={fieldHint}>0 = 不限。短信投诉成本比邮件高得多，建议至少留几天。</span>
          </label>
        </div>
      </div>

      <AccountList
        accounts={admin ? accounts : scopedAccounts}
        presets={providerPresets}
        options={providerOptions}
        admin={admin}
        defaultingId={defaultingId}
        disablingId={disablingId}
        onCreate={openCreateAccount}
        onEdit={editAccount}
        onDisable={(a) => void disableAccount(a)}
        onSetDefault={(a) => void setDefaultAccount(a)}
        onTest={(a) => void openTestDialog(a)}
        onManageTemplates={(a) => void openTemplates(a)}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
        <StatCard label="已发出" value={stats?.sent ?? 0} hint={`已送达 ${stats?.delivered ?? 0}`} />
        <StatCard label="失败" value={stats?.failed ?? 0} hint={`拦截 ${stats?.rejected ?? 0}`} />
        <StatCard label="无可发号码" value={stats?.no_recipient ?? 0} />
        <StatCard label="计费条数" value={stats?.segments ?? 0} />
        <StatCard label="互动" value={stats?.click_count ?? 0} hint={`回复 ${stats?.reply_count ?? 0} · 退订 ${stats?.opted_out ?? 0}`} />
      </div>

      <div style={{ ...card, padding: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <UiSelect style={input} value={status} onChange={setStatus}
                  options={[
                    { value: '', label: '全部状态' },
                    { value: 'sending', label: '发送中' },
                    { value: 'sent', label: '已发出' },
                    { value: 'delivered', label: '已送达' },
                    { value: 'failed', label: '失败' },
                    { value: 'rejected', label: '已拦截' },
                    { value: 'disabled', label: '已关闭' },
                    { value: 'no_recipient', label: '无可发号码' },
                  ]} />
        <input style={{ ...input, minWidth: 220 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索企业、号码、正文" />
      </div>

      <SmsDeliveryTable deliveries={deliveries} onViewContent={setViewingDelivery} />
      <Pagination
        className="mt-3"
        page={deliveryPage}
        pageSize={deliveryPageSize}
        total={deliveryTotal}
        onPageChange={setDeliveryPage}
        onPageSizeChange={size => { setDeliveryPageSize(size); setDeliveryPage(1); }}
      />

      <Drawer
        open={accountDrawerOpen}
        title={editingId ? '编辑短信通道' : '新增短信通道'}
        subtitle="选平台、填签名与凭据；密钥只会加密保存，不会回显。"
        onClose={closeAccountDrawer}
      >
        <AccountForm
          value={form}
          preset={currentPreset}
          presets={providerPresets}
          options={providerOptions}
          editingId={editingId}
          busy={saving}
          onChange={setForm}
          onPickProvider={pickProvider}
          onSubmit={() => void submitAccount()}
          onCancel={closeAccountDrawer}
        />
      </Drawer>

      <Drawer
        open={templateAccount !== null}
        title={`已报备模板 · ${templateAccount?.display_name || templateAccount?.provider_label || ''}`}
        subtitle="国内通道只能发这些模板；正文以平台审核通过的版本为准，这里维护的是本地登记与变量槽位。"
        onClose={() => { if (!savingTemplate) { setTemplateAccount(null); setTemplates([]); setEditingTemplateId(null); setTemplateForm(emptyTemplate); } }}
      >
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ ...card, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={th}>模板</th><th style={th}>用途/审核</th><th style={th}>操作</th></tr></thead>
              <tbody>
                {templates.map(t => (
                  <tr key={t.id}>
                    <td style={td}>
                      <div style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{t.name || t.template_code}</div>
                      <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{t.template_code} · {t.param_slots?.length || 0} 个变量</div>
                    </td>
                    <td style={td}>{purposeLabel(t.purpose)}<div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{approvalLabel(t.approved_status)}{t.enabled ? '' : ' · 已停用'}</div></td>
                    <td style={td}>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <ActionButton variant="secondary" style={{ padding: '6px 10px', fontSize: 12 }} onClick={() => editTemplate(t)}>编辑</ActionButton>
                        <ActionButton variant="danger" style={{ padding: '6px 10px', fontSize: 12 }} disabled={!t.enabled} onClick={() => void disableTemplate(t)}>{t.enabled ? '停用' : '已停用'}</ActionButton>
                      </div>
                    </td>
                  </tr>
                ))}
                {!templates.length && <tr><td style={td} colSpan={3}>还没有登记模板。先去平台控制台报备，再把模板 ID 填到下面。</td></tr>}
              </tbody>
            </table>
          </div>

          <div>
            <h3 style={{ margin: '0 0 10px', color: 'var(--text-primary)', fontSize: 14, fontWeight: 800 }}>{editingTemplateId ? '编辑模板' : '新增模板'}</h3>
            <TemplateForm
              value={templateForm}
              busy={savingTemplate}
              editingId={editingTemplateId}
              onChange={setTemplateForm}
              onSubmit={() => void submitTemplate()}
              onCancel={() => { setEditingTemplateId(null); setTemplateForm(emptyTemplate); }}
            />
          </div>
        </div>
      </Drawer>

      <TestSmsDialog
        account={testingAccount}
        templates={testTemplates}
        value={testForm}
        busy={testing}
        onChange={setTestForm}
        onClose={() => { if (!testing) setTestingAccount(null); }}
        onSubmit={() => void submitTestSms()}
      />

      <SmsDeliveryContentDialog delivery={viewingDelivery} onClose={() => setViewingDelivery(null)} />
    </div>
  );
}

export default SalesAgentSmsManager;
