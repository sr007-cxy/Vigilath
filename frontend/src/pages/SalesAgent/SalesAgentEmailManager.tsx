import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { toast } from '../../components/ui/toast';
import { EmailProviderIcon } from './EmailProviderIcon';
import {
  createSalesAgentEmailAccount,
  disableSalesAgentEmailAccount,
  fetchSalesAgentEmailAccounts,
  fetchSalesAgentEmailSettings,
  testSalesAgentEmailAccount,
  updateSalesAgentEmailAccount,
  updateSalesAgentEmailSettings,
  type SalesAgentEmailAccount,
  type SalesAgentEmailAccountInput,
  type SalesAgentEmailDelivery,
  type SalesAgentEmailEffectiveSettings,
  type SalesAgentEmailProviderOption,
  type SalesAgentEmailProviderPreset,
  type SalesAgentEmailSetting,
} from '../../services/salesAgentApi';
import { UiSelect } from '../../components/ui/Select';
import { uiConfirm } from '../../components/ui/dialogs';

const card: CSSProperties = { background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 16 };
const input: CSSProperties = { background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)', borderRadius: 8, padding: '8px 10px' };
const fieldHint: CSSProperties = { color: 'var(--text-muted)', fontSize: 11, lineHeight: 1.5 };
const th: CSSProperties = { textAlign: 'left', padding: '9px 10px', color: 'var(--text-secondary)', fontSize: 12, whiteSpace: 'nowrap' };
const td: CSSProperties = { padding: '9px 10px', borderTop: '1px solid var(--border-color)', verticalAlign: 'top', fontSize: 13 };

const emptyAccount: SalesAgentEmailAccountInput = {
  display_name: '',
  provider_type: 'smtp',
  api_provider: '',
  preset_key: '',
  enabled: true,
  is_default: false,
  from_email: '',
  from_name: '',
  reply_to_email: '',
  smtp_host: '',
  smtp_port: 465,
  smtp_security: 'ssl',
  smtp_username: '',
  smtp_secret: '',
  api_key: '',
  api_base_url: '',
};

/** Providers shown first in the picker; the rest go under "More providers". */
const FEATURED_PRESET_KEYS = ['netease_163', 'netease_126', 'qq_mail', 'tencent_exmail', 'aliyun_mail', 'gmail', 'outlook', 'feishu'];
const FALLBACK_PRESET_KEYS = ['custom_smtp', 'resend_api'];

const securityLabel: Record<string, string> = { ssl: 'SSL', starttls: 'STARTTLS', none: '无加密' };

function emailDomain(value: string) {
  const text = String(value || '').trim().toLowerCase();
  return (text.includes('@') ? text.split('@').pop() || '' : text).replace(/^\.+|\.+$/g, '');
}

type DetectedPreset = { preset: SalesAgentEmailProviderPreset; guessed: boolean } | null;

/** Identify the provider by the sending mailbox's domain. Same rules as backend detect_email_provider_preset; kept local to fill in while typing without requests. */
function detectPreset(email: string, presets: SalesAgentEmailProviderPreset[]): DetectedPreset {
  const domain = emailDomain(email);
  if (!domain || !domain.includes('.')) return null;
  const hit = presets.find(p => (p.domains || []).some(d => String(d).toLowerCase() === domain));
  if (hit) return { preset: hit, guessed: false };
  // Unlisted domains guess smtp.<domain>:465 SSL by general rule -- a wrong guess just means the user edits one line in advanced settings.
  // guessed=true is passed all the way to the UI: the hint bar becomes "please confirm", and the provider chip must not be highlighted as "user-selected".
  return {
    guessed: true,
    preset: {
      key: 'custom_smtp',
      label: `${domain}(推测)`,
      provider_type: 'smtp',
      api_provider: '',
      domains: [],
      smtp_host: `smtp.${domain}`,
      smtp_port: 465,
      smtp_security: 'ssl',
      alt_ports: [],
      secret_label: '邮箱密码或授权码',
      hint: `没有收录 ${domain},已按通用规则推测为 smtp.${domain}:465 SSL,请确认后再测试发送。`,
      help_url: '',
    },
  };
}

function presetByKey(key: string, presets: SalesAgentEmailProviderPreset[]) {
  return presets.find(p => p.key === key) || null;
}

/** Apply a preset to the form values. The provider was explicitly chosen, so host/port/encryption are overwritten too. */
function applyPreset(value: SalesAgentEmailAccountInput, preset: SalesAgentEmailProviderPreset): SalesAgentEmailAccountInput {
  if (preset.provider_type === 'api') {
    return { ...value, preset_key: preset.key, provider_type: 'api', api_provider: preset.api_provider || 'resend' };
  }
  const base = { ...value, preset_key: preset.key, provider_type: 'smtp', api_provider: '', smtp_username: value.smtp_username || value.from_email || '' };
  // "Custom SMTP" has no server address to fill, so keep what the user already typed -- overwriting with empty would wipe it for them
  if (!preset.smtp_host) return base;
  return { ...base, smtp_host: preset.smtp_host, smtp_port: preset.smtp_port || 465, smtp_security: preset.smtp_security || 'ssl' };
}

function fmtTime(v?: string | null) {
  return v ? v.replace('T', ' ').slice(0, 19) : '—';
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    sent: '成功', failed: '失败', disabled: '已关闭', no_recipient: '无收件人', sending: '发送中', draft: '草稿',
  };
  return map[status] || status || '—';
}

function messageTypeLabel(v: string) {
  const map: Record<string, string> = { report_email: '报告邮件', general_outreach: '普通触达', follow_up: '跟进', test: '测试' };
  return map[v] || v || '—';
}

function providerLabel(a: SalesAgentEmailAccount | SalesAgentEmailAccountInput) {
  return a.provider_type === 'api' ? (a.api_provider || 'API') : 'SMTP';
}

/**
 * Accounts in the list may not have a stored preset_key (old data, or originally filled by a guess);
 * in that case identify again by the sending mailbox domain, and fall back to the generic envelope only if unrecognized.
 */
function iconKeyForAccount(a: SalesAgentEmailAccount, presets: SalesAgentEmailProviderPreset[]) {
  if (a.preset_key) return a.preset_key;
  if (a.provider_type === 'api') return a.api_provider ? `${a.api_provider}_api` : 'custom_smtp';
  return detectPreset(a.from_email || '', presets)?.preset.key || 'custom_smtp';
}

function senderPolicyLabel(policy?: string) {
  return policy === 'round_robin' ? '多发件箱轮询' : '默认发件箱优先';
}

function EffectiveNotice({ effective }: { effective: SalesAgentEmailEffectiveSettings | null }) {
  if (!effective) return null;
  const reasons = effective.disabled_reasons || [];
  const ok = effective.sending_enabled;
  return (
    <div style={{ ...card, padding: 14, borderColor: ok ? 'var(--border-color)' : '#d97706' }}>
      <div style={{ fontWeight: 800, color: ok ? 'var(--text-primary)' : '#b45309' }}>
        {ok ? '当前真实发送已开启' : '当前真实发送已关闭'}
      </div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4, lineHeight: 1.7 }}>
        {ok
          ? `有效发件箱:${effective.account?.from_email || '未配置'} · 来源:${effective.account_source === 'global' ? '全局默认' : '当前账号'} · 调度策略:${senderPolicyLabel(effective.sender_selection_policy)}`
          : (reasons.length ? reasons.join('；') : '未配置可用发件箱或发送开关已关闭。')}
        <br />阅读追踪:{effective.tracking_enabled ? '开启(打开像素 + 报告访问)' : '关闭'}
      </div>
    </div>
  );
}

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
      <aside style={{ position: 'relative', width: 'min(560px, 100vw)', height: '100%', background: 'var(--bg-card)', borderLeft: '1px solid var(--border-color)', boxShadow: '-24px 0 48px rgba(15, 23, 42, 0.18)', overflowY: 'auto' }}>
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

function TestEmailDialog({
  account,
  value,
  busy,
  onChange,
  onClose,
  onSubmit,
}: {
  account: SalesAgentEmailAccount | null;
  value: { to_email: string; subject: string; content: string };
  busy: boolean;
  onChange: (next: { to_email: string; subject: string; content: string }) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  if (!account) return null;
  const set = (patch: Partial<typeof value>) => onChange({ ...value, ...patch });
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 70, display: 'grid', placeItems: 'center', padding: 18 }}>
      <button type="button" aria-label="关闭测试弹窗" disabled={busy} onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15, 23, 42, 0.45)', border: 'none', cursor: busy ? 'wait' : 'pointer' }} />
      <div style={{ position: 'relative', width: 'min(520px, 100%)', ...card, boxShadow: '0 24px 70px rgba(15, 23, 42, 0.24)', overflow: 'hidden' }}>
        <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border-color)', background: 'linear-gradient(135deg, rgba(99,102,241,.14), rgba(16,185,129,.10))' }}>
          <div style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 900 }}>发送测试邮件</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 6 }}>
            发件箱:{account.display_name || account.from_email} · {providerLabel(account)}
          </div>
        </div>
        <div style={{ padding: 20, display: 'grid', gap: 14 }}>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 12 }}>
            测试收件邮箱
            <input style={input} value={value.to_email} onChange={(e) => set({ to_email: e.target.value })} placeholder="留空则发送到当前登录账号邮箱" />
          </label>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 12 }}>
            邮件主题
            <input style={input} value={value.subject} onChange={(e) => set({ subject: e.target.value })} placeholder="留空使用默认测试主题" />
          </label>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 12 }}>
            邮件内容
            <textarea style={{ ...input, minHeight: 120, resize: 'vertical', lineHeight: 1.6 }} value={value.content} onChange={(e) => set({ content: e.target.value })} placeholder="留空使用默认测试内容" />
          </label>
          <div style={{ color: 'var(--text-muted)', fontSize: 12, lineHeight: 1.6 }}>
            测试邮件只用于验证当前发件箱配置；授权码/API Key 不会回显，也不会写入发送记录里的明文信息。
          </div>
        </div>
        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border-color)', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <ActionButton variant="ghost" disabled={busy} onClick={onClose}>取消</ActionButton>
          <ActionButton variant="success" busy={busy} onClick={onSubmit}>发送测试</ActionButton>
        </div>
      </div>
    </div>
  );
}

export function DeliveryContentDialog({ delivery, onClose }: { delivery: SalesAgentEmailDelivery | null; onClose: () => void }) {
  if (!delivery) return null;
  const content = delivery.content || delivery.content_preview || '暂无可查看正文。';
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 70, display: 'grid', placeItems: 'center', padding: 18 }}>
      <button type="button" aria-label="关闭邮件内容弹窗" onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15, 23, 42, 0.45)', border: 'none', cursor: 'pointer' }} />
      <div style={{ position: 'relative', width: 'min(720px, 100%)', maxHeight: '88vh', ...card, boxShadow: '0 24px 70px rgba(15, 23, 42, 0.24)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border-color)', background: 'linear-gradient(135deg, rgba(59,130,246,.12), rgba(99,102,241,.10))' }}>
          <div style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 900 }}>邮件内容</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 6 }}>{messageTypeLabel(delivery.message_type)} · {statusLabel(delivery.status)} · {fmtTime(delivery.sent_at || delivery.failed_at || delivery.created_at)}</div>
        </div>
        <div style={{ padding: 20, display: 'grid', gap: 12, overflowY: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: '8px 12px', fontSize: 13, lineHeight: 1.6 }}>
            <div style={{ color: 'var(--text-muted)' }}>主题</div><div style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{delivery.subject || '—'}</div>
            <div style={{ color: 'var(--text-muted)' }}>收件人</div><div style={{ color: 'var(--text-primary)' }}>{delivery.recipient_email || '—'}{delivery.recipient_name ? ` (${delivery.recipient_name})` : ''}</div>
            <div style={{ color: 'var(--text-muted)' }}>发件人</div><div style={{ color: 'var(--text-primary)' }}>{delivery.from_email || '—'}{delivery.from_name ? ` (${delivery.from_name})` : ''}</div>
            <div style={{ color: 'var(--text-muted)' }}>阅读</div><div style={{ color: 'var(--text-primary)' }}>{delivery.read_at ? `已读 ${fmtTime(delivery.read_at)}` : '未读'} · 打开 {delivery.open_count || 0} · 报告 {delivery.report_visit_count || 0}</div>
            {delivery.failure_reason && <><div style={{ color: 'var(--text-muted)' }}>失败原因</div><div style={{ color: '#b91c1c' }}>{delivery.failure_reason}</div></>}
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

function ProviderChip({ iconKey, label, active, onClick }: { iconKey: string; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        ...input,
        display: 'flex',
        alignItems: 'center',
        gap: 9,
        minHeight: 46,
        padding: '8px 12px',
        cursor: 'pointer',
        fontSize: 13,
        fontWeight: active ? 800 : 600,
        textAlign: 'left',
        lineHeight: 1.35,
        background: active ? 'rgba(99, 102, 241, 0.12)' : 'var(--bg-input)',
        borderColor: active ? 'var(--accent-primary)' : 'var(--border-color)',
        color: active ? 'var(--accent-primary)' : 'var(--text-primary)',
      }}
    >
      <EmailProviderIcon iconKey={iconKey} />
      <span>{label}</span>
    </button>
  );
}

function ProviderPicker({
  presets,
  activeKey,
  onPick,
}: {
  presets: SalesAgentEmailProviderPreset[];
  activeKey: string;
  onPick: (preset: SalesAgentEmailProviderPreset) => void;
}) {
  const featured = FEATURED_PRESET_KEYS.map(k => presetByKey(k, presets)).filter(Boolean) as SalesAgentEmailProviderPreset[];
  const fallback = FALLBACK_PRESET_KEYS.map(k => presetByKey(k, presets)).filter(Boolean) as SalesAgentEmailProviderPreset[];
  const shown = new Set([...featured, ...fallback].map(p => p.key));
  const more = presets.filter(p => !shown.has(p.key));
  // When the selected provider is one tucked under "More", expand it by default, otherwise users can't see what they picked
  const activeInMore = more.some(p => p.key === activeKey);
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 700 }}>选择邮箱服务商</span>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(148px, 1fr))', gap: 8 }}>
        {[...featured, ...fallback].map(p => (
          <ProviderChip key={p.key} iconKey={p.key} label={p.label} active={p.key === activeKey} onClick={() => onPick(p)} />
        ))}
      </div>
      {more.length > 0 && (
        <details open={activeInMore}>
          <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text-secondary)', padding: '4px 0' }}>
            更多服务商（{more.length}）
          </summary>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(148px, 1fr))', gap: 8, marginTop: 8 }}>
            {more.map(p => (
              <ProviderChip key={p.key} iconKey={p.key} label={p.label} active={p.key === activeKey} onClick={() => onPick(p)} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function PresetHint({ preset, guessed }: { preset: SalesAgentEmailProviderPreset | null; guessed: boolean }) {
  if (!preset || !preset.hint) return null;
  return (
    <div style={{
      border: `1px solid ${guessed ? '#d97706' : 'var(--border-color)'}`,
      background: guessed ? 'rgba(217, 119, 6, 0.08)' : 'rgba(99, 102, 241, 0.06)',
      borderRadius: 10,
      padding: '10px 12px',
      fontSize: 12,
      lineHeight: 1.7,
      color: 'var(--text-secondary)',
      display: 'flex',
      gap: 10,
    }}>
      <EmailProviderIcon iconKey={guessed ? 'custom_smtp' : preset.key} size={22} style={{ marginTop: 2 }} />
      <div>
        <span style={{ fontWeight: 800, color: guessed ? '#b45309' : 'var(--text-primary)' }}>{preset.label}</span>
        <br />{preset.hint}
        {preset.help_url && (
          <>
            {' '}
            <a href={preset.help_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-primary)', fontWeight: 700 }}>查看官方说明 →</a>
          </>
        )}
      </div>
    </div>
  );
}

function AccountForm({
  value,
  providerPresets,
  editingId,
  busy,
  onChange,
  onSubmit,
  onCancel,
}: {
  value: SalesAgentEmailAccountInput;
  providerPresets: SalesAgentEmailProviderPreset[];
  editingId: number | null;
  busy: boolean;
  onChange: (next: SalesAgentEmailAccountInput) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  // Once the user manually changes server/port/encryption, auto-detection no longer touches them -- a "smart" revert is more annoying than not filling at all.
  // Clicking a provider chip is an explicit action, and only then is a reset allowed.
  const [advancedDirty, setAdvancedDirty] = useState(false);
  const set = (patch: SalesAgentEmailAccountInput) => onChange({ ...value, ...patch });
  const setAdvanced = (patch: SalesAgentEmailAccountInput) => {
    setAdvancedDirty(true);
    onChange({ ...value, ...patch });
  };

  const isApi = value.provider_type === 'api';
  const chosen = presetByKey(value.preset_key || '', providerPresets);
  const detected = chosen ? null : detectPreset(value.from_email || '', providerPresets);
  const activePreset = chosen || detected?.preset || null;
  const guessed = !!detected?.guessed;
  const secretLabel = activePreset?.secret_label || '授权码/密码';

  const pickPreset = (preset: SalesAgentEmailProviderPreset) => {
    setAdvancedDirty(false);
    onChange(applyPreset(value, preset));
  };

  const changeFromEmail = (from_email: string) => {
    const next: SalesAgentEmailAccountInput = { ...value, from_email };
    // The username follows the sending mailbox by default (the SMTP username is almost always the mailbox itself)
    if (!advancedDirty) next.smtp_username = from_email;
    const hit = detectPreset(from_email, providerPresets);
    if (!advancedDirty && !isApi && hit) {
      next.smtp_host = hit.preset.smtp_host;
      next.smtp_port = hit.preset.smtp_port || 465;
      next.smtp_security = hit.preset.smtp_security || 'ssl';
      // Only truly matched presets count; guessed ones aren't written to preset_key (the backend doesn't record them either)
      next.preset_key = hit.guessed ? '' : hit.preset.key;
    }
    onChange(next);
  };

  const advancedSummary = isApi
    ? `${value.api_provider || 'API'} · ${value.api_base_url || '默认地址'}`
    : `${value.smtp_host || '未填写'}${value.smtp_port ? `:${value.smtp_port}` : ''} · ${securityLabel[value.smtp_security || 'ssl'] || value.smtp_security}`;

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <h3 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 15, fontWeight: 800 }}>{editingId ? '编辑发件箱' : '新增发件箱'}</h3>
          <p style={{ margin: '4px 0 0', color: 'var(--text-secondary)', fontSize: 12 }}>选服务商后 SMTP 配置自动填好;密码/授权码/API Key 只会加密保存,不会回显。</p>
        </div>
        {editingId && <ActionButton variant="ghost" onClick={onCancel}>取消编辑</ActionButton>}
      </div>

      <ProviderPicker presets={providerPresets} activeKey={value.preset_key || ''} onPick={pickPreset} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 12 }}>
        <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
          发件邮箱
          <input style={input} value={value.from_email || ''} onChange={(e) => changeFromEmail(e.target.value)} placeholder="sales@163.com" />
        </label>
        {!isApi ? (
          <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
            {secretLabel}
            <input style={input} type="password" value={value.smtp_secret || ''} onChange={(e) => set({ smtp_secret: e.target.value })} placeholder={editingId ? '留空=不修改' : secretLabel} />
          </label>
        ) : (
          <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
            {secretLabel}
            <input style={input} type="password" value={value.api_key || ''} onChange={(e) => set({ api_key: e.target.value })} placeholder={editingId ? '留空=不修改' : secretLabel} />
          </label>
        )}
        <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
          发件人名称
          <input style={input} value={value.from_name || ''} onChange={(e) => set({ from_name: e.target.value })} placeholder="公司/团队名" />
          <span style={fieldHint}>收件人在收件箱里看到的名字，会写进邮件的 From 头</span>
        </label>
        <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
          回复邮箱
          <input style={input} value={value.reply_to_email || ''} onChange={(e) => set({ reply_to_email: e.target.value })} placeholder="留空=发件邮箱" />
          <span style={fieldHint}>收件人点「回复」时的收信地址</span>
        </label>
        <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
          备注名
          <input style={input} value={value.display_name || ''} onChange={(e) => set({ display_name: e.target.value })} placeholder="留空=发件邮箱" />
          <span style={fieldHint}>只用来在下面的发件箱列表里认人，收件人看不到</span>
        </label>
      </div>

      <PresetHint preset={activePreset} guessed={guessed} />

      <details style={{ border: '1px solid var(--border-color)', borderRadius: 10, padding: '10px 12px' }}>
        <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text-secondary)' }}>
          高级设置 · <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{advancedSummary}</span>
          {!advancedDirty && activePreset && !isApi && <span style={{ color: 'var(--text-muted)' }}>（已自动填写）</span>}
        </summary>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 12, marginTop: 12 }}>
          {!isApi ? (
            <>
              <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
                SMTP 服务器
                <input style={input} value={value.smtp_host || ''} onChange={(e) => setAdvanced({ smtp_host: e.target.value })} placeholder="smtp.example.com" />
              </label>
              <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
                SMTP 端口
                <input style={input} type="number" value={value.smtp_port || 465} onChange={(e) => setAdvanced({ smtp_port: Number(e.target.value) || 465 })} />
              </label>
              <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
                安全模式
                <UiSelect style={input} value={value.smtp_security || 'ssl'}
                          onChange={(val) => setAdvanced({ smtp_security: val })}
                          options={[
                            { value: 'ssl', label: 'SSL' },
                            { value: 'starttls', label: 'STARTTLS' },
                            { value: 'none', label: '无' },
                          ]} />
              </label>
              <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
                SMTP 用户名
                <input style={input} value={value.smtp_username || ''} onChange={(e) => setAdvanced({ smtp_username: e.target.value })} placeholder="通常为发件邮箱" />
              </label>
            </>
          ) : (
            <label style={{ display: 'grid', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
              API 地址
              <input style={input} value={value.api_base_url || ''} onChange={(e) => setAdvanced({ api_base_url: e.target.value })} placeholder="Resend 可留空" />
            </label>
          )}
        </div>
        {!isApi && !!activePreset?.alt_ports?.length && (
          <div style={{ color: 'var(--text-muted)', fontSize: 11, lineHeight: 1.6, marginTop: 10 }}>
            {activePreset.label} 备用端口:{activePreset.alt_ports.map(a => `${a.smtp_port}/${securityLabel[a.smtp_security] || a.smtp_security}`).join('、')}（465 被网络封锁时可以换)
          </div>
        )}
      </details>

      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={value.enabled !== false} onChange={(e) => set({ enabled: e.target.checked })} /> 启用
        </label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={!!value.is_default} onChange={(e) => set({ is_default: e.target.checked })} /> 设为默认
        </label>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <ActionButton variant="primary" busy={busy} onClick={onSubmit} style={{ padding: '10px 18px' }}>
          {busy ? '保存中…' : (editingId ? '保存发件箱' : '新增发件箱')}
        </ActionButton>
      </div>
    </div>
  );
}

function AccountList({
  accounts,
  providerPresets,
  admin,
  defaultingId,
  disablingId,
  onCreate,
  onEdit,
  onDisable,
  onSetDefault,
  onTest,
}: {
  accounts: SalesAgentEmailAccount[];
  providerPresets: SalesAgentEmailProviderPreset[];
  admin: boolean;
  defaultingId: number | null;
  disablingId: number | null;
  onCreate: () => void;
  onEdit: (a: SalesAgentEmailAccount) => void;
  onDisable: (a: SalesAgentEmailAccount) => void;
  onSetDefault: (a: SalesAgentEmailAccount) => void;
  onTest: (a: SalesAgentEmailAccount) => void;
}) {
  return (
    <div style={{ ...card, overflow: 'hidden' }}>
      <div style={{ padding: 14, display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 16, fontWeight: 800 }}>{admin ? '全局/用户发件箱' : '我的发件箱'}</h2>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{accounts.length} 个发件箱</span>
        </div>
        <ActionButton variant="primary" onClick={onCreate}>新增发件箱</ActionButton>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>备注名</th><th style={th}>类型</th><th style={th}>发件邮箱</th><th style={th}>状态</th><th style={th}>测试</th><th style={th}>操作</th></tr></thead>
          <tbody>
            {accounts.map(a => (
              <tr key={a.id}>
                <td style={td}>
                  <div style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{a.display_name || a.from_email}</div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{a.scope}{a.user_id ? ` · 用户 ${a.user_id}` : ''}{a.is_default ? ' · 默认' : ''}</div>
                </td>
                <td style={td}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <EmailProviderIcon iconKey={iconKeyForAccount(a, providerPresets)} size={22} />
                    <span style={{ color: 'var(--text-primary)' }}>{a.preset_label || providerLabel(a)}</span>
                  </div>
                  {a.provider_type !== 'api' && a.smtp_host && (
                    <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 4 }}>{a.smtp_host}:{a.smtp_port} · {securityLabel[a.smtp_security || 'ssl'] || a.smtp_security}</div>
                  )}
                </td>
                <td style={td}>{a.from_email}<div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{a.reply_to_email ? `回复:${a.reply_to_email}` : ''}</div></td>
                <td style={td}>{a.enabled ? '启用' : '停用'} · {a.has_smtp_secret || a.has_api_key ? '密钥已保存' : '未保存密钥'}</td>
                <td style={td}>{a.last_test_status ? `${statusLabel(a.last_test_status)} ${fmtTime(a.last_tested_at)}` : '未测试'}{a.last_test_error && <div style={{ color: '#b91c1c', fontSize: 12 }}>{a.last_test_error}</div>}</td>
                <td style={td}>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <ActionButton variant="secondary" onClick={() => onEdit(a)}>编辑</ActionButton>
                    <ActionButton variant="success" busy={defaultingId === a.id} disabled={a.is_default} onClick={() => onSetDefault(a)}>{a.is_default ? '已默认' : '设默认'}</ActionButton>
                    <ActionButton variant="primary" onClick={() => onTest(a)}>测试</ActionButton>
                    <ActionButton variant="danger" busy={disablingId === a.id} disabled={!a.enabled} onClick={() => onDisable(a)}>{a.enabled ? '停用' : '已停用'}</ActionButton>
                  </div>
                </td>
              </tr>
            ))}
            {!accounts.length && <tr><td style={td} colSpan={6}>暂无发件箱,点击右上角新增。</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function DeliveryTable({ deliveries, onViewContent }: { deliveries: SalesAgentEmailDelivery[]; onViewContent: (delivery: SalesAgentEmailDelivery) => void }) {
  return (
    <div style={{ ...card, overflow: 'hidden' }}>
      <div style={{ padding: 14 }}>
        <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 16, fontWeight: 800 }}>发送记录</h2>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
          <thead><tr><th style={th}>企业/用户</th><th style={th}>类型</th><th style={th}>收件人</th><th style={th}>发件邮箱</th><th style={th}>状态</th><th style={th}>阅读</th><th style={th}>时间</th><th style={th}>失败原因</th><th style={{ ...th, width: 96 }}>内容</th></tr></thead>
          <tbody>
            {deliveries.map(d => (
              <tr key={d.id}>
                <td style={td}><div style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{d.company_name || d.company?.name || '—'}</div><div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{d.user_email || `用户 ${d.user_id}`}</div></td>
                <td style={td}>{messageTypeLabel(d.message_type)}</td>
                <td style={td}>{d.recipient_email || '—'}<div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{d.recipient_name || ''}</div></td>
                <td style={td}>{d.from_email || '—'}</td>
                <td style={td}>{statusLabel(d.status)}</td>
                <td style={td}>{d.read_at ? '已读' : '未读'}<div style={{ color: 'var(--text-muted)', fontSize: 12 }}>打开 {d.open_count || 0} · 报告 {d.report_visit_count || 0}</div></td>
                <td style={td}>{fmtTime(d.sent_at || d.failed_at || d.created_at)}</td>
                <td style={{ ...td, maxWidth: 240 }}>{d.failure_reason || '—'}</td>
                <td style={{ ...td, whiteSpace: 'nowrap', width: 96 }}><ActionButton variant="secondary" style={{ minWidth: 76, padding: '7px 10px' }} onClick={() => onViewContent(d)}>查看内容</ActionButton></td>
              </tr>
            ))}
            {!deliveries.length && <tr><td style={td} colSpan={9}>暂无发送记录。</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SalesAgentEmailManager({ admin = false }: { admin?: boolean }) {
  const [settings, setSettings] = useState<SalesAgentEmailSetting | null>(null);
  const [effective, setEffective] = useState<SalesAgentEmailEffectiveSettings | null>(null);
  const [providerOptions, setProviderOptions] = useState<SalesAgentEmailProviderOption[]>([]);
  const [providerPresets, setProviderPresets] = useState<SalesAgentEmailProviderPreset[]>([]);
  const [accounts, setAccounts] = useState<SalesAgentEmailAccount[]>([]);
  const [form, setForm] = useState<SalesAgentEmailAccountInput>(emptyAccount);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [accountDrawerOpen, setAccountDrawerOpen] = useState(false);
  const [testingAccount, setTestingAccount] = useState<SalesAgentEmailAccount | null>(null);
  const [testForm, setTestForm] = useState({ to_email: '', subject: '', content: '' });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [defaultingId, setDefaultingId] = useState<number | null>(null);
  const [disablingId, setDisablingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const pageTitle = admin ? '邮件配置管理' : '邮件配置';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, a] = await Promise.all([
        fetchSalesAgentEmailSettings(admin),
        fetchSalesAgentEmailAccounts(admin, admin ? 'all' : 'user'),
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

  useEffect(() => { void load(); }, [load]);

  const scopedAccounts = useMemo(() => admin ? accounts.filter(a => a.scope === 'global') : accounts, [accounts, admin]);

  // API-kind presets follow provider_options' available flag -- SendGrid / Alibaba Cloud DM aren't integrated yet,
  // and listing them in the picker would only make people fill everything in to be rejected by the backend.
  const usablePresets = useMemo(() => providerPresets.filter(p => {
    if (p.provider_type !== 'api') return true;
    const option = providerOptions.find(o => o.provider_type === 'api' && (o.api_provider || '') === (p.api_provider || ''));
    return option ? option.available : true;
  }), [providerOptions, providerPresets]);

  const saveSettings = async (patch: Partial<SalesAgentEmailSetting>) => {
    try {
      const next = await updateSalesAgentEmailSettings({
        sending_enabled: settings?.sending_enabled ?? true,
        tracking_enabled: settings?.tracking_enabled ?? true,
        default_account_id: settings?.default_account_id ?? null,
        sender_selection_policy: settings?.sender_selection_policy || 'default_only',
        ...patch,
      }, admin);
      setSettings(next.settings);
      setEffective(next.effective);
      toast.success('邮箱设置已保存');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败');
    }
  };

  const openCreateAccount = () => {
    setEditingId(null);
    setForm(emptyAccount);
    setAccountDrawerOpen(true);
  };

  const closeAccountDrawer = () => {
    if (saving) return;
    setAccountDrawerOpen(false);
    setEditingId(null);
    setForm(emptyAccount);
  };

  const submitAccount = async () => {
    setSaving(true);
    try {
      if (editingId) await updateSalesAgentEmailAccount(editingId, form, admin);
      else await createSalesAgentEmailAccount(form, admin);
      toast.success('发件箱已保存');
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

  const editAccount = (a: SalesAgentEmailAccount) => {
    setEditingId(a.id);
    setAccountDrawerOpen(true);
    setForm({
      display_name: a.display_name,
      provider_type: a.provider_type,
      api_provider: a.api_provider || '',
      preset_key: a.preset_key || '',
      enabled: a.enabled,
      is_default: a.is_default,
      from_email: a.from_email,
      from_name: a.from_name || '',
      reply_to_email: a.reply_to_email || '',
      smtp_host: a.smtp_host || '',
      smtp_port: a.smtp_port || 465,
      smtp_security: a.smtp_security || 'ssl',
      smtp_username: a.smtp_username || '',
      smtp_secret: '',
      api_key: '',
      api_base_url: a.api_base_url || '',
    });
  };

  const disableAccount = async (a: SalesAgentEmailAccount) => {
    if (!(await uiConfirm(`确认停用发件箱 ${a.display_name || a.from_email}?`, { danger: true }))) return;
    setDisablingId(a.id);
    try {
      await disableSalesAgentEmailAccount(a.id, admin);
      toast.success('已停用');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '停用失败');
    } finally {
      setDisablingId(null);
    }
  };

  const setDefaultAccount = async (a: SalesAgentEmailAccount) => {
    setDefaultingId(a.id);
    try {
      await updateSalesAgentEmailAccount(a.id, { is_default: true }, admin);
      await updateSalesAgentEmailSettings({
        sending_enabled: settings?.sending_enabled ?? true,
        tracking_enabled: settings?.tracking_enabled ?? true,
        default_account_id: a.id,
        sender_selection_policy: settings?.sender_selection_policy || 'default_only',
      }, admin);
      toast.success('默认发件箱已更新');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '设置默认失败');
    } finally {
      setDefaultingId(null);
    }
  };

  const openTestDialog = (a: SalesAgentEmailAccount) => {
    setTestingAccount(a);
    setTestForm({
      to_email: '',
      subject: '邮件配置发件箱测试',
      content: `这是一封来自 ${a.display_name || a.from_email} 的测试邮件,用于确认邮件配置中的发件箱可用。`,
    });
  };

  const closeTestDialog = () => {
    if (testing) return;
    setTestingAccount(null);
  };

  const submitTestEmail = async () => {
    if (!testingAccount) return;
    setTesting(true);
    try {
      const r = await testSalesAgentEmailAccount(testingAccount.id, testForm, admin);
      if (r.ok) {
        toast.success('测试邮件已发送');
        setTestingAccount(null);
      } else {
        toast.error(r.error || r.delivery?.failure_reason || '测试失败');
      }
      await load();
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
          <p style={{ margin: '6px 0 0', color: 'var(--text-secondary)', fontSize: 13 }}>
            发件箱、SMTP 授权码/API Key 和发送开关都在数据库里配置；发送记录已独立放到「邮件发送记录」模块。
          </p>
        </div>
        <button type="button" onClick={() => void load()} style={{ ...input, cursor: 'pointer' }}>{loading ? '刷新中…' : '刷新'}</button>
      </div>

      <EffectiveNotice effective={effective} />

      <div style={{ ...card, padding: 16 }}>
        <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 16, fontWeight: 800 }}>{admin ? '全局开关' : '我的开关'}</h2>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, marginTop: 12 }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
            <input type="checkbox" checked={settings?.sending_enabled !== false} onChange={(e) => void saveSettings({ sending_enabled: e.target.checked })} /> 允许真实发送邮件
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
            <input type="checkbox" checked={settings?.tracking_enabled !== false} onChange={(e) => void saveSettings({ tracking_enabled: e.target.checked })} /> 开启阅读追踪
          </label>
          <label style={{ display: 'grid', gap: 6, color: 'var(--text-secondary)', fontSize: 13, minWidth: 220 }}>
            <span>发件调度策略</span>
            <UiSelect style={input} value={settings?.sender_selection_policy || 'default_only'}
                      onChange={(val) => void saveSettings({ sender_selection_policy: val })}
                      options={[
                        { value: 'default_only', label: '默认发件箱优先' },
                        { value: 'round_robin', label: '多发件箱轮询' },
                      ]} />
            <span style={{ color: 'var(--text-muted)', fontSize: 11, lineHeight: 1.5 }}>配置多个发件箱时按该策略选择实际发件箱；仍需逐封邮件草稿人工确认后才发送。</span>
          </label>
        </div>
      </div>

      <AccountList
        accounts={admin ? accounts : scopedAccounts}
        providerPresets={usablePresets}
        admin={admin}
        defaultingId={defaultingId}
        disablingId={disablingId}
        onCreate={openCreateAccount}
        onEdit={editAccount}
        onDisable={(a) => void disableAccount(a)}
        onSetDefault={(a) => void setDefaultAccount(a)}
        onTest={openTestDialog}
      />

      <Drawer
        open={accountDrawerOpen}
        title={editingId ? '编辑发件箱' : '新增发件箱'}
        subtitle="配置 SMTP 邮箱或 API 服务商；密码/授权码/API Key 只会加密保存，不会回显。"
        onClose={closeAccountDrawer}
      >
        <AccountForm
          value={form}
          providerPresets={usablePresets}
          editingId={editingId}
          busy={saving}
          onChange={setForm}
          onSubmit={() => void submitAccount()}
          onCancel={closeAccountDrawer}
        />
      </Drawer>

      <TestEmailDialog
        account={testingAccount}
        value={testForm}
        busy={testing}
        onChange={setTestForm}
        onClose={closeTestDialog}
        onSubmit={() => void submitTestEmail()}
      />

    </div>
  );
}
