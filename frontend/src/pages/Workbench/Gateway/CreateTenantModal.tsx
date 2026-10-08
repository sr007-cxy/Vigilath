// Create tenant -- this used to be an inline expanding form card at the top of the page, with 9 bare <input>s (including checkbox / radio).
// Now a Modal: form fields use Field + Input + UiSelect, the engine allowlist uses UiMultiSelect,
// and the three-way account opening choice uses Segmented. Form state lives in this component; on successful creation the api_key is handed back.
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { aiTelemetryApi } from '../../../services/aiTelemetryApi';
import { adminUsersApi, type AdminUserItem } from '../../../services/adminUsersApi';
import { ENGINE_KEYS, engineLabel } from '../../../constants/engines';
import { Modal, Button, Input, Field, UiSelect, UiMultiSelect } from '../../../components/ui';
import { Segmented } from '../../../components/ui/FilterBar';
import { KIND_OPTIONS, tierLabel } from './labels';

type AccountMode = 'none' | 'new' | 'existing';

export function CreateTenantModal({
  open, onClose, onCreated, token,
}: {
  open: boolean;
  onClose: () => void;
  /** Created successfully: hand the show-once api_key back to the shell for display */
  onCreated: (apiKey: string) => void;
  token: string;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [tier, setTier] = useState('free');
  const [kind, setKind] = useState('customer');   // Purpose: external customer / own system / integration testing
  const [engines, setEngines] = useState<string[]>([]);
  const [quota, setQuota] = useState('20');
  // Account opening: none = don't bind / new = open a new platform account / existing = bind an existing account
  const [accountMode, setAccountMode] = useState<AccountMode>('none');
  const [acctEmail, setAcctEmail] = useState('');
  const [acctPassword, setAcctPassword] = useState('');
  const [acctUserId, setAcctUserId] = useState('');
  const [allUsers, setAllUsers] = useState<AdminUserItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Fetch the user list once on open (for the bind-existing-account dropdown; only bindable roles listed)
  useEffect(() => {
    if (!open) return;
    adminUsersApi.list(token)
      .then(us => setAllUsers(us.filter(u => (u.role === 'user' || u.role === 'tenant') && !u.tenant_id)))
      .catch(() => setAllUsers([]));
  }, [open, token]);

  const reset = () => {
    setName(''); setTier('free'); setKind('customer'); setEngines([]); setQuota('20');
    setAccountMode('none'); setAcctEmail(''); setAcctPassword(''); setAcctUserId(''); setErr(null);
  };

  const validate = (): string | null => {
    if (!name.trim()) return t('workbench.adminGateway.nameRequired', { defaultValue: '租户名称必填' });
    if (accountMode === 'new' && (!acctEmail.trim() || acctPassword.length < 6)) {
      return t('workbench.adminGateway.accountNewInvalid', { defaultValue: '新开账号需要邮箱和至少 6 位初始密码' });
    }
    if (accountMode === 'existing' && !acctUserId) {
      return t('workbench.adminGateway.accountExistingInvalid', { defaultValue: '请选择要绑定的账号' });
    }
    return null;
  };

  const create = async () => {
    const v = validate();
    if (v) { setErr(v); return; }
    setBusy(true); setErr(null);
    try {
      const r = await aiTelemetryApi.adminGatewayCreateTenant({
        name: name.trim(), tier, kind, engines, daily_quota_default: parseInt(quota, 10) || 0,
        ...(accountMode === 'new' ? { email: acctEmail.trim(), password: acctPassword } : {}),
        ...(accountMode === 'existing' && acctUserId ? { user_id: Number(acctUserId) } : {}),
      }, token);
      onCreated(r.api_key);
      reset();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <Modal open={open} onClose={onClose} maxWidthClass="max-w-xl"
           header={<span className="text-sm font-medium text-primary">{t('workbench.adminGateway.newTenant', { defaultValue: '新建租户' })}</span>}
           footer={
             <div className="flex items-center justify-between gap-3">
               <span className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</span>
               <div className="flex gap-2">
                 <Button size="sm" variant="ghost" onClick={onClose}>{t('common.cancel', { defaultValue: '取消' })}</Button>
                 <Button size="sm" variant="solid" loading={busy} onClick={create}>{t('common.create', { defaultValue: '创建' })}</Button>
               </div>
             </div>
           }>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('workbench.adminGateway.colName', { defaultValue: '租户名称' })} required>
          <Input size="sm" value={name} onChange={setName} autoFocus />
        </Field>
        <Field label={t('workbench.adminGateway.colTier', { defaultValue: '档位' })}>
          <UiSelect value={tier} onChange={setTier} className="w-full"
                    options={[{ value: 'free', label: tierLabel(t, 'free') }, { value: 'pro', label: tierLabel(t, 'pro') }]} />
        </Field>
        <Field label={t('workbench.adminGateway.colKind', { defaultValue: '用途' })}>
          <UiSelect value={kind} onChange={setKind} className="w-full" options={KIND_OPTIONS(t)} />
        </Field>
        <Field label={t('workbench.adminGateway.colQuota', { defaultValue: '每引擎默认日配额' })}>
          <Input size="sm" inputMode="numeric" value={quota} onChange={setQuota} />
        </Field>
        <div className="col-span-2">
          <Field label={t('workbench.adminGateway.colEngines', { defaultValue: '引擎白名单' })}
                 hint={t('workbench.adminGateway.enginesHint', { defaultValue: '不选 = 全放开' })}>
            <UiMultiSelect values={engines} onChange={setEngines} className="w-full"
                           allLabel={t('workbench.adminGateway.allEngines', { defaultValue: '全部' })}
                           options={ENGINE_KEYS.map(e => ({ value: e, label: engineLabel(e) }))} />
          </Field>
        </div>
        <div className="col-span-2">
          <Field label={t('workbench.adminGateway.accountMode', { defaultValue: '门户登录账号' })}
                 hint={t('workbench.adminGateway.accountModeHint', { defaultValue: '登录后可自助查调用记录 / 余额' })}>
            <Segmented value={accountMode} onChange={setAccountMode} options={[
              { value: 'none', label: t('workbench.adminGateway.accountModeNone', { defaultValue: '暂不开通' }) },
              { value: 'new', label: t('workbench.adminGateway.accountModeNew', { defaultValue: '新开账号' }) },
              { value: 'existing', label: t('workbench.adminGateway.accountModeExisting', { defaultValue: '绑定已有账号' }) },
            ]} />
          </Field>
        </div>
        {accountMode === 'new' && (
          <>
            <Field label={t('workbench.adminGateway.accountEmail', { defaultValue: '登录邮箱' })} required>
              <Input size="sm" type="email" value={acctEmail} onChange={setAcctEmail} />
            </Field>
            <Field label={t('workbench.adminGateway.accountPassword', { defaultValue: '初始密码' })}
                   hint={t('workbench.adminGateway.accountPasswordHint', { defaultValue: '至少 6 位' })} required>
              <Input size="sm" value={acctPassword} onChange={setAcctPassword} />
            </Field>
          </>
        )}
        {accountMode === 'existing' && (
          <div className="col-span-2">
            <Field label={t('workbench.adminGateway.accountPick', { defaultValue: '选择账号' })} required>
              <UiSelect value={acctUserId} onChange={setAcctUserId} className="w-full" searchable
                        options={[
                          { value: '', label: t('workbench.adminGateway.accountPickPlaceholder', { defaultValue: '选择账号…' }) },
                          ...allUsers.map(u => ({ value: String(u.id), label: u.email })),
                        ]} />
            </Field>
          </div>
        )}
      </div>
    </Modal>
  );
}
