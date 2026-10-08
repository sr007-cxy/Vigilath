// Account pool tab: table first; adding accounts goes into a right drawer; one filter row.
//
// The previous version stacked above the table: a full "Add account" card (title + long hint + three-row form) + account pool card header + three-segment health bar
// + filter bar + offline distribution chip row -- the table got pushed far down, while the three authorization paths, used only a few times a day, were always visible.
// Now:
//   · "Add account" goes into a Drawer (right drawer), where Segmented switches password / SMS / QR scan, showing one at a time.
//     The Drawer is level 1, and the SMS / QR scan Modals are level 2 on top of the drawer; after the flow the drawer stays open,
//     so the authorization result (authMsg) is visible and the new account entering the pool can be seen right away
//   · Health bar removed -- its three numbers exactly duplicate the status pill counts
//   · Offline distribution chip row removed -- folded into the engine dropdown option text "engine (offline n)"; selecting it + "Quarantined" = the old shortcut
//   · List page skeleton: filters shrink-0 · table flex-1 min-h-0 inner scrolling · pagination shrink-0 pinned to the bottom
// The three authorization paths only changed placement; not a single control or handler was touched -- they were verified on physical devices.
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { EngineSessionPage, EngineSessionRow, EngineProxy } from '../../../services/aiTelemetryApi';
import { fmtTime } from '../../../utils/datetime';
import { engineLabel, canonicalEngines } from '../../../constants/engines';
import {
  TableShell, StickyHead, EmptyRow, Badge, Button, Input, UiSelect, PillTabs, uiPrompt,
} from '../../../components/ui';
import { Menu } from '../../../components/ui/Menu';
import { FilterBar, FilterBarPrimary, FilterField, Segmented } from '../../../components/ui/FilterBar';
import { FILTER_CONTROL_CLASS, FILTER_CONTROL_STYLE } from '../../../components/ui/filterControls';
import { Drawer } from '../../../components/overlays/Drawer';
import { Pagination } from '../../../components/Pagination';
import { PHONE_COUNTRIES } from '../../../components/phoneCountries';

export const SESSION_PAGE_SIZE = 10;

type SessStatus = '' | 'active' | 'quarantined' | 'expired';
type AuthMethod = 'password' | 'sms' | 'qr';

export interface AuthProps {
  authForm: { engine: string; identifier: string; password: string };
  setAuthForm: (fn: (f: { engine: string; identifier: string; password: string }) => { engine: string; identifier: string; password: string }) => void;
  authBusy: boolean;
  authMsg: string | null;
  doAuthorize: () => void;
  smsEngine: string; setSmsEngine: (v: string) => void;
  smsCc: string; smsPhone: string;
  setSmsCc: (v: string) => void; setSmsPhone: (v: string) => void;
  startSms: () => void;
  qrEngine: string; setQrEngine: (v: string) => void; startQr: () => void;
}

export interface PoolProps {
  sessions: EngineSessionPage | null;
  page: number; setPage: (p: number) => void;
  engine: string; setEngine: (e: string) => void;
  status: SessStatus; setStatus: (s: SessStatus) => void;
  proxies: EngineProxy[];
  patchSession: (id: number, patch: { disabled?: boolean; daily_cap?: number; priority?: number; proxy_id?: number }) => void;
  bindProxyId: (s: EngineSessionRow, proxyId: number) => void;
}

export function AccountsTab({ auth, pool }: { auth: AuthProps; pool: PoolProps }) {
  const { t } = useTranslation();
  const { sessions } = pool;
  const grand = sessions?.grand_total ?? 0;
  const active = sessions?.active_total ?? 0;
  const quar = sessions?.quarantined_total ?? 0;
  const other = Math.max(0, grand - active - quar);
  const [addOpen, setAddOpen] = useState(false);
  const [method, setMethod] = useState<AuthMethod>('password');

  const setStatusAndReset = (s: SessStatus) => { pool.setStatus(s); pool.setPage(1); };
  const setEngineAndReset = (e: string) => { pool.setEngine(e); pool.setPage(1); };

  // Engine dropdown: offline distribution folded into the option text. It used to be a separate chip row; now selecting "some engine (offline n)" + "Quarantined"
  // is the old shortcut filter, without taking up a row.
  const engineOptions = useMemo(() => {
    const byEng = sessions?.quarantined_by_engine ?? {};
    return [
      { value: '', label: t('workbench.adminWorkers.filterAllEngines', { defaultValue: '全部引擎' }) },
      ...canonicalEngines(sessions?.engines ?? []).map(e => ({
        value: e,
        label: byEng[e]
          ? t('workbench.adminWorkers.engineQuarOpt', { defaultValue: '{{engine}}(掉线 {{n}})', engine: engineLabel(e), n: byEng[e] })
          : engineLabel(e),
      })),
    ];
  }, [sessions, t]);

  return (
    <>
      <div className="h-full flex flex-col gap-3">
        {/* ── The only filter row: status pills · engine dropdown · add account ── */}
        <header className="shrink-0">
          <FilterBar>
            <FilterBarPrimary right={<>
              <FilterField label={t('workbench.adminWorkers.colEngine', { defaultValue: '引擎' })}>
                <UiSelect value={pool.engine} onChange={setEngineAndReset}
                          className={FILTER_CONTROL_CLASS} style={FILTER_CONTROL_STYLE} options={engineOptions} />
              </FilterField>
              <Button size="sm" variant="solid" onClick={() => setAddOpen(true)}>
                {t('workbench.adminWorkers.addAccount', { defaultValue: '添加账号' })}
              </Button>
            </>}>
              <PillTabs value={pool.status} onChange={setStatusAndReset} items={[
                { value: '', label: t('workbench.adminWorkers.filterAll', { defaultValue: '全部' }), count: grand },
                { value: 'active', label: t('workbench.adminWorkers.statusActive', { defaultValue: '可用' }), count: active },
                { value: 'quarantined', label: t('workbench.adminWorkers.statusQuarantined', { defaultValue: '已隔离' }), count: quar },
                { value: 'expired', label: t('workbench.adminWorkers.statusExpired', { defaultValue: '已过期' }), count: other },
              ]} />
            </FilterBarPrimary>
          </FilterBar>
        </header>

        {/* ── Table: the only layer taking the remaining height; key={page} scrolls back to top on page change ── */}
        <div className="flex-1 min-h-0" key={pool.page}>
          <TableShell maxHeight={false} className="h-full">
            <table className="w-full text-xs">
              <StickyHead>
                <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colEngine', { defaultValue: '引擎' })}</th>
                <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colLabel', { defaultValue: '标识' })}</th>
                <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colStatus', { defaultValue: '状态' })}</th>
                <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminWorkers.colUseCaptcha', { defaultValue: '用量 / 验证码' })}</th>
                <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{t('workbench.adminWorkers.colLastUsed', { defaultValue: '最后使用' })}</th>
                <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colExpires', { defaultValue: '过期' })}</th>
                <th className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{t('workbench.adminWorkers.colQuota', { defaultValue: '今日 / 上限' })}</th>
                <th className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{t('workbench.adminWorkers.colExitIp', { defaultValue: '出口 IP' })}</th>
                <th className="px-3 py-2.5 text-left font-medium">{t('workbench.adminWorkers.colProxy', { defaultValue: '代理' })}</th>
                <th className="px-3 py-2.5 text-right font-medium">{t('workbench.adminWorkers.colActions', { defaultValue: '操作' })}</th>
              </StickyHead>
              {(sessions?.items ?? []).length === 0 ? (
                <EmptyRow colSpan={10}
                  title={(pool.engine || pool.status)
                    ? t('workbench.adminWorkers.noFilterMatch', { defaultValue: '没有匹配当前筛选的账号' })
                    : t('workbench.adminWorkers.noSessions', { defaultValue: '账号池为空' })}
                  hint={(pool.engine || pool.status) ? undefined
                    : t('workbench.adminWorkers.noSessionsHint', { defaultValue: '点右上「添加账号」,或用浏览器扩展上传登录态。' })} />
              ) : (
                <tbody>
                  {(sessions?.items ?? []).map(s => <SessionRow key={s.id} s={s} pool={pool} />)}
                </tbody>
              )}
            </table>
          </TableShell>
        </div>

        <Pagination className="shrink-0" page={pool.page} pageSize={SESSION_PAGE_SIZE}
                    total={sessions?.total ?? 0} onPageChange={pool.setPage} />
      </div>

      {/* ── Add account drawer: the three paths show one at a time ── */}
      <Drawer open={addOpen} onClose={() => setAddOpen(false)} widthClass="w-full md:w-[480px]"
              header={
                <div>
                  <div className="text-sm font-medium text-primary">{t('workbench.adminWorkers.addAccount', { defaultValue: '添加账号' })}</div>
                  <div className="text-[11px] text-muted">{t('workbench.adminWorkers.addAccountHint', { defaultValue: '登录态入池后,跑批会从池里领号。' })}</div>
                </div>
              }
              footer={
                <div className="p-3 flex justify-end">
                  <Button size="sm" variant="ghost" onClick={() => setAddOpen(false)}>{t('workbench.adminWorkers.close', { defaultValue: '关闭' })}</Button>
                </div>
              }>
        <Segmented value={method} onChange={setMethod} options={[
          { value: 'password', label: t('workbench.adminWorkers.authByPassword', { defaultValue: '密码' }) },
          { value: 'sms', label: t('workbench.adminWorkers.authBySms', { defaultValue: '短信' }) },
          { value: 'qr', label: t('workbench.adminWorkers.authByQr', { defaultValue: '扫码' }) },
        ]} />

        {method === 'password' && (
          <AuthBlock hint={t('workbench.adminWorkers.authByPasswordHint', { defaultValue: '服务端自动登录抓登录态入池;deepseek 全自动,wenxin 可能撞验证码需人工' })}>
            <UiSelect value={auth.authForm.engine} onChange={v => auth.setAuthForm(f => ({ ...f, engine: v }))} className="w-full"
                      options={[
                        { value: 'deepseek', label: engineLabel('deepseek') },
                        { value: 'wenxin', label: `${engineLabel('wenxin')}(可能撞验证码)` },
                      ]} />
            <Input size="sm" value={auth.authForm.identifier} placeholder="账号(邮箱/手机号)"
                   onChange={v => auth.setAuthForm(f => ({ ...f, identifier: v }))} />
            <Input size="sm" type="password" value={auth.authForm.password} placeholder="密码"
                   onChange={v => auth.setAuthForm(f => ({ ...f, password: v }))} />
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted">{auth.authMsg}</span>
              <Button size="sm" variant="solid" loading={auth.authBusy}
                      disabled={!auth.authForm.identifier || !auth.authForm.password} onClick={auth.doAuthorize}>
                {t('workbench.adminWorkers.authorize', { defaultValue: '授权' })}
              </Button>
            </div>
          </AuthBlock>
        )}

        {method === 'sms' && (
          <AuthBlock hint={t('workbench.adminWorkers.authBySmsHint', { defaultValue: '通义千问 / Kimi 没有密码登录,走手机验证码:发码后把短信里的码填进弹窗;Kimi 发码前有滑块,弹窗里会提示走 VNC' })}>
            <UiSelect value={auth.smsEngine} onChange={auth.setSmsEngine} className="w-full"
                      options={[
                        { value: 'qwen', label: engineLabel('qwen') },
                        // kimi only has phone verification code login; there's always a slider before sending the code, and the modal prompts to use VNC
                        { value: 'kimi', label: engineLabel('kimi') },
                      ]} />
            <UiSelect value={auth.smsCc} onChange={auth.setSmsCc} className="w-full"
                      options={PHONE_COUNTRIES.map(c => ({ value: c.code, label: `${c.flag} +${c.code} ${c.name}` }))} />
            <Input size="sm" value={auth.smsPhone} placeholder="手机号(不含区号)" onChange={auth.setSmsPhone} />
            <div className="flex justify-end">
              <Button size="sm" variant="solid" disabled={auth.smsPhone.replace(/\D/g, '').length < 6} onClick={auth.startSms}>
                {t('workbench.adminWorkers.smsSend', { defaultValue: '发送验证码' })}
              </Button>
            </div>
          </AuthBlock>
        )}

        {method === 'qr' && (
          <AuthBlock hint={t('workbench.adminWorkers.authByQrHint', { defaultValue: '用手机 App 扫弹窗里的二维码并确认登录' })}>
            <UiSelect value={auth.qrEngine} onChange={auth.setQrEngine} className="w-full"
                      options={['wenxin', 'yuanbao', 'qwen', 'doubao'].map(e => ({ value: e, label: engineLabel(e) }))} />
            <div className="flex justify-end">
              <Button size="sm" variant="solid" onClick={auth.startQr}>
                {t('workbench.adminWorkers.qrStart', { defaultValue: '扫码授权' })}
              </Button>
            </div>
          </AuthBlock>
        )}
      </Drawer>
    </>
  );
}

function AuthBlock({ hint, children }: { hint: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 mt-3">
      <p className="text-[11px] text-muted">{hint}</p>
      {children}
    </div>
  );
}

function SessionRow({ s, pool }: { s: EngineSessionRow; pool: PoolProps }) {
  const { t } = useTranslation();
  const fmt = (d: string | null) => d ? fmtTime(d, false) : '—';
  const tone = s.status === 'active' ? 'success' : s.status === 'quarantined' ? 'danger' : 'neutral';
  const statusText = s.status === 'active' ? t('workbench.adminWorkers.statusActive', { defaultValue: '可用' })
    : s.status === 'expired' ? t('workbench.adminWorkers.statusExpired', { defaultValue: '已过期' })
    : s.status === 'quarantined' ? t('workbench.adminWorkers.statusQuarantined', { defaultValue: '已隔离' })
    : s.status;
  const bound = !!(s.proxy_id || s.proxy_override);

  return (
    <tr style={{ borderTop: '1px solid var(--border-color)' }}>
      <td className="px-3 py-2.5 text-primary whitespace-nowrap">{engineLabel(s.engine)}</td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <span className="text-secondary">{s.account_handle || s.label || `#${s.id}`}</span>
        {s.account_handle && s.label && <span className="text-[10px] text-muted ml-1">({s.label})</span>}
        {s.auth_type && <span className="text-[10px] text-muted ml-1">· {s.auth_type}</span>}
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <Badge tone={tone} title={s.last_fail_type && s.status !== 'active' ? s.last_fail_type : undefined}>{statusText}</Badge>
        {s.disabled && <Badge tone="neutral" className="ml-1">{t('workbench.adminWorkers.sessDisabled', { defaultValue: '已停用' })}</Badge>}
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">
        {s.use_count}<span className="text-muted"> / {s.captcha_count}</span>
      </td>
      <td className="px-3 py-2.5 text-secondary tabular-nums whitespace-nowrap">{fmt(s.last_used_at)}</td>
      <td className="px-3 py-2.5 text-secondary tabular-nums whitespace-nowrap">{fmt(s.expires_at)}</td>
      <td className="px-3 py-2.5 text-right tabular-nums text-secondary whitespace-nowrap"
          title={s.daily_cap != null ? t('workbench.adminWorkers.capOverridden', { defaultValue: '此账号单独设了上限' }) : undefined}>
        {/* Always "used today / limit", aligned with the queue table's used/cap */}
        {s.used_today ?? 0} / {s.effective_cap === 0 ? t('workbench.adminWorkers.unlimited', { defaultValue: '不限' }) : (s.effective_cap ?? 0)}
        {s.daily_cap != null && <span className="ml-1" style={{ color: 'var(--warn-fg)' }}>●</span>}
      </td>
      <td className="px-3 py-2.5 text-secondary tabular-nums whitespace-nowrap">
        {s.exit_ip || '—'}
        {s.rate_limited && <Badge tone="warn" className="ml-1">{t('workbench.adminWorkers.cooling', { defaultValue: '冷却中' })}</Badge>}
      </td>
      <td className="px-3 py-2.5">
        {/* The proxy dropdown is always shown -- it's what ops change most often; bound ones are highlighted */}
        <UiSelect
          value={s.proxy_id != null ? String(s.proxy_id) : ''}
          title={s.proxy_override || t('workbench.adminWorkers.proxySelectTip', { defaultValue: '从代理库选出口代理' })}
          onChange={val => pool.bindProxyId(s, val === '' ? -1 : parseInt(val, 10))}
          className={FILTER_CONTROL_CLASS}
          style={{ ...FILTER_CONTROL_STYLE, ...(bound ? { borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' } : {}) }}
          options={[
            { value: '', label: s.proxy_override
              ? t('workbench.adminWorkers.proxyLegacyStr', { defaultValue: '(裸串)' })
              : t('workbench.adminWorkers.proxyNone', { defaultValue: '(不绑代理)' }) },
            ...pool.proxies.filter(p => !p.disabled).map(p => ({ value: String(p.id), label: p.label })),
          ]} />
      </td>
      <td className="px-3 py-2.5 text-right">
        <Menu label="···" triggerClassName="px-2 py-1 rounded-md text-sm" triggerStyle={{ background: 'var(--bg-tertiary)' }}
          items={[
            { label: s.disabled
                ? t('workbench.adminWorkers.actEnable', { defaultValue: '启用' })
                : t('workbench.adminWorkers.actDisable', { defaultValue: '停用' }),
              onClick: () => pool.patchSession(s.id, { disabled: !s.disabled }) },
            { label: t('workbench.adminWorkers.actCap', { defaultValue: '改日上限' }),
              hint: t('workbench.adminWorkers.actCapHint', { defaultValue: `当前 ${s.daily_cap != null ? s.daily_cap : '跟随引擎默认'}` }),
              onClick: async () => {
                const v = await uiPrompt(t('workbench.adminWorkers.promptCap', { defaultValue: '单账号日上限(空=用引擎默认,0=不限)' }),
                                         { defaultValue: s.daily_cap != null ? String(s.daily_cap) : '' });
                if (v === null) return;
                const n = v.trim() === '' ? -1 : parseInt(v, 10);
                if (!Number.isNaN(n)) pool.patchSession(s.id, { daily_cap: n });
              } },
            { label: t('workbench.adminWorkers.actPriority', { defaultValue: '改优先级' }),
              hint: t('workbench.adminWorkers.actPriorityHint', { defaultValue: `当前 ${s.priority ?? 50},低值优先` }),
              onClick: async () => {
                const v = await uiPrompt(t('workbench.adminWorkers.promptPriority', { defaultValue: '优先级(0-100,低值优先)' }),
                                         { defaultValue: String(s.priority ?? 50) });
                if (v === null) return;
                const n = parseInt(v, 10);
                if (!Number.isNaN(n)) pool.patchSession(s.id, { priority: n });
              } },
          ]} />
      </td>
    </tr>
  );
}
