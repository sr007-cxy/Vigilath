// User role management (admin only) — list users, change roles, bind/unbind gateway tenants, view a single account's details.
// Role changes take effect immediately (the backend checks the DB on every request, no re-login needed).
// The list backend returns everything at once; search / role / status filters + pagination are all done on the frontend (tens to hundreds is enough).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { adminUsersApi, type AdminUserItem } from '../../services/adminUsersApi';
import { aiTelemetryApi, type GatewayTenant } from '../../services/aiTelemetryApi';
import { membershipApi, type Membership } from '../../services/membershipApi';
import { useAuth } from '../../contexts/AuthContext';
import { Pagination } from '../../components/Pagination';
import { AdminUserDetailDrawer } from './AdminUserDetailDrawer';
// Unified UI component layer; see docs/03-系统设计/前端UI规范.md for the conventions
import {
  Button, Badge, Card, PageHeader, Input, Field, PillTabs, UiSelect, uiConfirm,
  StickyHead, SkeletonRows, EmptyRow,
} from '../../components/ui';

const card: React.CSSProperties = { background: 'var(--bg-card)', border: '1px solid var(--border-color)' };
const fld: React.CSSProperties = { ...card, fontSize: 12 };

const DIRECT_ROLES = ['admin', 'operator', 'maintainer', 'user', 'tenant'] as const;
const ROLES = [...DIRECT_ROLES, 'agency_admin', 'agency_operator'] as const;
const isAgencyRole = (role: string) => role === 'agency_admin' || role === 'agency_operator';
type RoleFilter = 'all' | (typeof ROLES)[number];
type StatusFilter = 'all' | 'active' | 'inactive';

export function AdminUsers({ embedded = false }: { embedded?: boolean }) {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const { user: me } = useAuth();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [tenants, setTenants] = useState<GatewayTenant[]>([]);
  const [tiers, setTiers] = useState<Membership[]>([]);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);   // Page size, user-selectable
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  // Detail drawer: profile / payment records / audit records, all data fetched by the drawer itself
  const [detailId, setDetailId] = useState<number | null>(null);

  const reload = useCallback(() => {
    Promise.all([
      adminUsersApi.list(token),
      aiTelemetryApi.adminGatewayTenants(token),
      membershipApi.getMemberships(),
    ])
      .then(([us, ts, ms]) => { setUsers(us); setTenants(ts.tenants); setTiers(ms); setErr(null); })
      .catch(e => setErr(e?.message || 'failed'))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  const roleLabel = (r: string) => ({
    admin: t('workbench.users.roleAdmin', { defaultValue: '管理员' }),
    operator: t('workbench.users.roleOperator', { defaultValue: '运营' }),
    maintainer: t('workbench.users.roleMaintainer', { defaultValue: '维护角色' }),
    user: t('workbench.users.roleUser', { defaultValue: '普通用户' }),
    tenant: t('workbench.users.roleTenant', { defaultValue: '网关租户' }),
    agency_admin: t('workbench.users.roleAgencyAdmin', { defaultValue: '代理商管理员' }),
    agency_operator: t('workbench.users.roleAgencyOperator', { defaultValue: '代理商操作员' }),
  } as Record<string, string>)[r] || r;

  // Plain-text password banner: shared by create / reset password, echoed only once (with copy)
  const [pwdInfo, setPwdInfo] = useState<{ email: string; password: string; isNew: boolean } | null>(null);
  const resetPassword = async (u: AdminUserItem) => {
    if (!(await uiConfirm(`确认重置「${u.email}」的密码?旧密码将立即失效。`, { danger: true }))) return;
    setBusyId(u.id);
    try {
      const out = await adminUsersApi.resetPassword(u.id, token);
      setPwdInfo({ email: out.email, password: out.new_password, isNew: false });
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  // Create user: the admin opens the account directly with a chosen role; leave the password empty for the server to generate a random one, echoed in plain text once
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ email: '', name: '', role: 'user' as string, password: '' });
  const [saving, setSaving] = useState(false);
  const resetForm = () => setForm({ email: '', name: '', role: 'user', password: '' });
  const submitCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    try {
      const out = await adminUsersApi.create({
        email: form.email.trim(),
        name: form.name.trim() || null,
        role: form.role,
        password: form.password.trim() || null,
      }, token);
      setPwdInfo({ email: out.user.email, password: out.initial_password, isNew: true });
      setErr(null);
      resetForm();
      setCreating(false);
      reload();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setSaving(false);
    }
  };

  // Set membership tier: free tier = cancel paid membership; paid tiers get a validity period by cycle (monthly = 30 days), setting the same tier again extends it
  const freeTier = tiers.find(m => m.slug === 'free');
  const changeMembership = async (u: AdminUserItem, tierIdRaw: string) => {
    const tierId = Number(tierIdRaw);
    const currentId = u.membership_id ?? freeTier?.id ?? null;
    if (tierId === currentId) return;
    const tier = tiers.find(m => m.id === tierId);
    if (!tier) return;
    const msg = tier.slug === 'free'
      ? t('workbench.users.membershipConfirmFree', {
          defaultValue: '确认取消「{{email}}」的付费会员,回落到免费档?', email: u.email })
      : t('workbench.users.membershipConfirmSet', {
          defaultValue: '确认将「{{email}}」的会员设为「{{name}}」?有效期按档位周期生成(月付 30 天),同档重复设置会顺延到期日。',
          email: u.email, name: tier.name });
    if (!(await uiConfirm(msg))) return;
    setBusyId(u.id);
    try {
      await adminUsersApi.updateMembership(u.id, tierId, token);
      setErr(null);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const changeRole = async (u: AdminUserItem, role: string) => {
    if (role === u.role) return;
    setBusyId(u.id);
    try {
      await adminUsersApi.updateRole(u.id, role, token);
      setErr(null);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  // Bind tenant: PATCH the gateway tenant's user_id (the binding's single source of truth is the tenant table)
  const changeTenant = async (u: AdminUserItem, tenantIdRaw: string) => {
    const nextTenantId = tenantIdRaw === '' ? null : Number(tenantIdRaw);
    if (nextTenantId === (u.tenant_id ?? null)) return;
    setBusyId(u.id);
    try {
      if (u.tenant_id != null) {
        await aiTelemetryApi.adminGatewayUpdateTenant(u.tenant_id, { unbind_user: true }, token);
      }
      if (nextTenantId != null) {
        await aiTelemetryApi.adminGatewayUpdateTenant(nextTenantId, { user_id: u.id }, token);
      }
      setErr(null);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      reload();
    } finally {
      setBusyId(null);
    }
  };

  // Filter by search + status first (role tab counts are based on this set, so counts change when searching/switching status)
  const base = useMemo(() => {
    const kw = search.trim().toLowerCase();
    return users.filter(u => {
      if (kw && !(`${u.email} ${u.name || ''}`.toLowerCase().includes(kw))) return false;
      if (statusFilter === 'active' && !u.is_active) return false;
      if (statusFilter === 'inactive' && u.is_active) return false;
      return true;
    });
  }, [users, search, statusFilter]);

  const roleCounts = useMemo(() => {
    const m: Record<string, number> = { all: base.length };
    for (const r of ROLES) m[r] = 0;
    for (const u of base) m[u.role] = (m[u.role] || 0) + 1;
    return m;
  }, [base]);

  const filtered = useMemo(
    () => (roleFilter === 'all' ? base : base.filter(u => u.role === roleFilter)),
    [base, roleFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  useEffect(() => { if (page > totalPages) setPage(totalPages); }, [page, totalPages]);
  const pagedRows = filtered.slice((page - 1) * pageSize, page * pageSize);

  // Any filter / page size change -> back to page one
  useEffect(() => { setPage(1); }, [search, roleFilter, statusFilter, pageSize]);

  const roleTabItems = [
    { value: 'all' as RoleFilter, label: t('workbench.users.filterAll', { defaultValue: '全部' }), count: roleCounts.all ?? 0 },
    ...ROLES.map(r => ({ value: r as RoleFilter, label: roleLabel(r), count: roleCounts[r] ?? 0 })),
  ];

  return (
    <div className={embedded ? '' : 'max-w-[1400px] mx-auto p-6'}>
      <PageHeader
        title={t('workbench.users.heading', { defaultValue: '用户角色管理' })}
        hint={t('workbench.users.subtitle', { defaultValue: '指派角色:管理员看全部;运营可用工作台五个模块(项目进度、画像、文案复审、监测报告、跑批结果);网关租户登录后只进租户门户。' })}
        actions={
          <Button size="sm" variant={creating ? 'outline' : 'solid'}
                  onClick={() => { setCreating(v => !v); setErr(null); }}>
            {creating
              ? t('workbench.users.cancel', { defaultValue: '取消' })
              : t('workbench.users.newUser', { defaultValue: '新建用户' })}
          </Button>
        }
      />

      {err && <div className="text-xs mb-3" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</div>}
      {pwdInfo && (
        <div className="rounded-lg p-3 mb-3 text-xs flex items-center gap-2 flex-wrap"
             style={{ background: 'var(--success-bg)', color: 'var(--success-fg)',
                      border: '1px solid var(--success-border)' }}>
          <span>
            「{pwdInfo.email}」{pwdInfo.isNew
              ? t('workbench.users.initialPwd', { defaultValue: '初始密码' })
              : t('workbench.users.newPwd', { defaultValue: '新密码' })}
            :<b className="font-mono select-all">{pwdInfo.password}</b>
            {t('workbench.users.pwdOnce', { defaultValue: '(仅本次显示,请立即转交用户)' })}
          </span>
          <Button size="sm" variant="ghost"
                  style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}
                  onClick={() => { navigator.clipboard?.writeText(pwdInfo.password); }}>
            {t('workbench.users.copy', { defaultValue: '复制' })}
          </Button>
          <Button size="sm" onClick={() => setPwdInfo(null)}>
            {t('workbench.users.close', { defaultValue: '关闭' })}
          </Button>
        </div>
      )}

      {creating && (
        <Card as="section" padded={false} className="p-3 mb-3">
          <form onSubmit={submitCreate}>
          <div className="flex items-end gap-2 flex-wrap">
            <Field label={t('workbench.users.colEmail', { defaultValue: '邮箱' })} required className="w-[240px]">
              <Input type="email" required size="sm" value={form.email}
                     onChange={val => setForm(f => ({ ...f, email: val }))}
                     placeholder="user@example.com" />
            </Field>
            <Field label={t('workbench.users.colName', { defaultValue: '名称' })} className="w-[160px]">
              <Input size="sm" value={form.name}
                     onChange={val => setForm(f => ({ ...f, name: val }))}
                     placeholder={t('workbench.users.optional', { defaultValue: '选填' })} />
            </Field>
            <Field label={t('workbench.users.colRole', { defaultValue: '角色' })} className="w-[130px]">
              <UiSelect className="px-2.5 py-1.5 rounded-lg text-primary text-xs w-full" style={fld}
                        value={form.role} onChange={val => setForm(f => ({ ...f, role: val }))}
                        options={DIRECT_ROLES.map(r => ({ value: r, label: roleLabel(r) }))} />
            </Field>
            <Field label={t('workbench.users.initialPwd', { defaultValue: '初始密码' })} className="w-[200px]">
              <Input size="sm" value={form.password}
                     onChange={val => setForm(f => ({ ...f, password: val }))}
                     placeholder={t('workbench.users.pwdPlaceholder', { defaultValue: '留空自动生成(至少 8 位)' })} />
            </Field>
            <Button type="submit" size="sm" variant="solid" loading={saving}>
              {saving
                ? t('workbench.users.creating', { defaultValue: '创建中…' })
                : t('workbench.users.create', { defaultValue: '创建' })}
            </Button>
          </div>
          <p className="text-[11px] text-muted mt-2">
            {t('workbench.users.createHint', { defaultValue: '创建后可在下方列表里改角色、绑定网关租户、设会员等级。初始密码明文只显示一次。' })}
          </p>
          </form>
        </Card>
      )}

      {/* Role filter tabs (with counts) + status filter */}
      <PillTabs
        className="mb-3"
        value={roleFilter}
        onChange={setRoleFilter}
        items={roleTabItems}
        right={
          <UiSelect className="text-xs px-2 py-1 rounded-md text-secondary"
                    style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
                    value={statusFilter} onChange={val => setStatusFilter(val as StatusFilter)}
                    options={[
                      { value: 'all', label: t('workbench.users.statusAll', { defaultValue: '全部状态' }) },
                      { value: 'active', label: t('workbench.users.active', { defaultValue: '正常' }) },
                      { value: 'inactive', label: t('workbench.users.inactive', { defaultValue: '已停用' }) },
                    ]} />
        }
      />

      <div className="rounded-lg overflow-hidden" style={card}>
        <div className="px-3 py-2 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border-color)' }}>
          <Input size="sm" className="w-[260px]"
                 placeholder={t('workbench.users.searchPlaceholder', { defaultValue: '按邮箱或名称搜索' })}
                 value={search} onChange={setSearch} />
          {search && (
            <Button size="sm" variant="ghost" onClick={() => setSearch('')}>
              {t('workbench.users.clear', { defaultValue: '清空' })}
            </Button>
          )}
          <span className="text-xs text-muted ml-auto tabular-nums">
            {t('workbench.users.showing', { defaultValue: '筛出 {{n}} / 共 {{total}}', n: filtered.length, total: users.length })}
          </span>
        </div>
        {/* The table scrolls itself: on narrow screens it doesn't give the whole page a horizontal scrollbar, and column names stay visible after the header scrolls out of view */}
        <div className="overflow-auto" style={{ maxHeight: 'clamp(360px, 62vh, 720px)' }}>
          <table className="w-full text-xs">
            <StickyHead>
                <th className="text-left px-3 py-2">#</th>
                <th className="text-left px-3 py-2">{t('workbench.users.colEmail', { defaultValue: '邮箱' })}</th>
                <th className="text-left px-3 py-2">{t('workbench.users.colName', { defaultValue: '名称' })}</th>
                <th className="text-left px-3 py-2">{t('workbench.users.colRole', { defaultValue: '角色' })}</th>
                <th className="text-left px-3 py-2">{t('workbench.users.colTenant', { defaultValue: '绑定租户' })}</th>
                <th className="text-left px-3 py-2">{t('workbench.users.colMembership', { defaultValue: '会员等级' })}</th>
                <th className="text-left px-3 py-2">{t('workbench.users.colStatus', { defaultValue: '状态' })}</th>
                <th className="text-left px-3 py-2">{t('workbench.users.colActions', { defaultValue: '操作' })}</th>
            </StickyHead>
            {loading ? <SkeletonRows rows={8} cols={8} /> : filtered.length === 0 ? (
              <EmptyRow
                colSpan={8}
                title={t('workbench.users.empty', { defaultValue: '没有匹配的账号' })}
                hint={t('workbench.users.emptyHint', { defaultValue: '换个关键词,或把角色 / 状态筛选放回「全部」再看。' })}
                action={(search || roleFilter !== 'all' || statusFilter !== 'all') ? (
                  <Button size="sm"
                          onClick={() => { setSearch(''); setRoleFilter('all'); setStatusFilter('all'); setPage(1); }}>
                    {t('workbench.users.resetFilters', { defaultValue: '清空筛选条件' })}
                  </Button>
                ) : undefined}
              />
            ) : (
            <tbody>
              {pagedRows.map(u => {
                const isSelf = me?.id === u.id;
                const organizationManaged = isAgencyRole(u.role);
                // Tenants selectable for this user: those bound to nobody + the one currently bound to them
                const options = tenants.filter(tt => tt.user_id == null || tt.user_id === u.id);
                return (
                  <tr key={u.id} style={{ borderTop: '1px solid var(--border-color)', opacity: busyId === u.id ? 0.5 : 1 }}>
                    <td className="px-3 py-2 text-muted tabular-nums">{u.id}</td>
                    <td className="px-3 py-2 text-primary">
                      <button type="button" onClick={() => setDetailId(u.id)}
                              className="hover:underline" style={{ color: 'inherit' }}>
                        {u.email}
                      </button>
                      {isSelf && (
                        <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded"
                              style={{ background: 'var(--bg-input)', color: 'var(--text-muted)' }}>
                          {t('workbench.users.selfTag', { defaultValue: '我' })}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted">{u.name || '—'}</td>
                    <td className="px-3 py-2">
                      <UiSelect className="px-2 py-1 rounded text-primary" style={fld}
                                value={u.role} disabled={isSelf || organizationManaged || busyId === u.id}
                                title={organizationManaged
                                  ? t('workbench.users.agencyRoleLocked', { defaultValue: '代理角色由组织成员关系统一管理' })
                                  : isSelf
                                    ? t('workbench.users.selfLocked', { defaultValue: '不能修改自己的角色' })
                                    : undefined}
                                onChange={val => changeRole(u, val)}
                                options={(organizationManaged ? ROLES : DIRECT_ROLES)
                                  .map(r => ({ value: r, label: roleLabel(r) }))} />
                    </td>
                    <td className="px-3 py-2">
                      {u.role === 'tenant' || u.tenant_id != null ? (
                        <UiSelect className="px-2 py-1 rounded text-primary" style={fld}
                                  value={u.tenant_id != null ? String(u.tenant_id) : ''} disabled={busyId === u.id}
                                  onChange={val => changeTenant(u, val)}
                                  options={[
                                    { value: '', label: t('workbench.users.noTenant', { defaultValue: '未绑定' }) },
                                    ...options.map(tt => ({ value: String(tt.id), label: tt.name })),
                                  ]} />
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <UiSelect className="px-2 py-1 rounded text-primary" style={fld}
                                value={String(u.membership_id ?? freeTier?.id ?? '')}
                                disabled={busyId === u.id || tiers.length === 0}
                                onChange={val => changeMembership(u, val)}
                                options={tiers.map(m => ({ value: String(m.id), label: m.name }))} />
                      {u.membership_end_date && (
                        <div className="text-[10px] text-muted mt-0.5 tabular-nums">
                          {t('workbench.users.membershipUntil', {
                            defaultValue: '有效至 {{date}}',
                            date: new Date(u.membership_end_date).toLocaleDateString() })}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={u.is_active ? 'success' : 'danger'}>
                        {u.is_active
                          ? t('workbench.users.active', { defaultValue: '正常' })
                          : t('workbench.users.inactive', { defaultValue: '已停用' })}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <Button size="sm" onClick={() => setDetailId(u.id)}>
                          {t('workbench.users.detailBtn', { defaultValue: '详情' })}
                        </Button>
                        <Button size="sm" loading={busyId === u.id} onClick={() => resetPassword(u)}>
                          {t('workbench.users.resetPwd', { defaultValue: '重置密码' })}
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
            )}
          </table>
        </div>
        {!loading && (
          <div className="px-3 py-2 border-t" style={{ borderColor: 'var(--border-color)' }}>
            <Pagination page={page} pageSize={pageSize} total={filtered.length}
              onPageChange={setPage} onPageSizeChange={setPageSize} />
          </div>
        )}
      </div>

      {/* key = remount entirely when switching users, so the drawer needn't reset state via individual setState calls */}
      <AdminUserDetailDrawer key={detailId ?? 'none'} userId={detailId}
                             onClose={() => setDetailId(null)} />
    </div>
  );
}
