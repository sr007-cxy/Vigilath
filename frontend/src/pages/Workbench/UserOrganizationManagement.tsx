import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { agencyApi, type AgencyAssignment, type AgencyAuditEvent, type AgencyEnterprise,
  type AgencyGrant, type AgencyMember, type AgencyOption, type AgencyOrganization } from '../../services/agencyApi';
import { AdminUsers } from './AdminUsers';
import {
  Badge, Button, Card, EmptyRow, Field, Input, Modal, PageHeader, PillTabs, StickyHead, TableShell, UiSelect,
} from '../../components/ui';

type Tab = 'accounts' | 'organizations' | 'grants' | 'audit' | 'members' | 'assignments';

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-input)', border: '1px solid var(--border-color)',
};

function StatusBadge({ status }: { status: string }) {
  const tone = status === 'active' || status === 'success'
    ? 'success'
    : status === 'disabled' || status === 'revoked' || status === 'failed'
      ? 'danger'
      : 'neutral';
  return <Badge tone={tone}>{status}</Badge>;
}

function ErrorNotice({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="text-xs" role="alert" style={{ color: 'var(--danger-fg)' }}>{error}</p>;
}

function PlatformAgencyPanel({ tab, canManage }: { tab: Tab; canManage: boolean }) {
  const token = localStorage.getItem('token') || '';
  const [options, setOptions] = useState<AgencyOption[]>([]);
  const [organizations, setOrganizations] = useState<AgencyOrganization[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [grants, setGrants] = useState<AgencyGrant[]>([]);
  const [members, setMembers] = useState<AgencyMember[]>([]);
  const [audit, setAudit] = useState<AgencyAuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [firstAdmin, setFirstAdmin] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [identifier, setIdentifier] = useState('');

  const reloadOptions = useCallback(async () => {
    try {
      const rows = await agencyApi.options(token);
      setOptions(rows);
      setSelected(prev => prev ?? rows[0]?.id ?? null);
      if (canManage) setOrganizations(await agencyApi.listOrganizations(token));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [canManage, token]);

  useEffect(() => { void reloadOptions(); }, [reloadOptions]);
  useEffect(() => {
    if (!selected) return;
    if (tab === 'grants') agencyApi.grants(selected, token).then(setGrants).catch(e => setError(String(e)));
    if (tab === 'audit') agencyApi.audit(selected, token).then(setAudit).catch(e => setError(String(e)));
    if (tab === 'organizations' && canManage) {
      agencyApi.platformMembers(selected, token).then(setMembers).catch(e => setError(String(e)));
    }
  }, [canManage, selected, tab, token]);

  const organizationSelect = (
    <UiSelect
      className="min-w-[220px] text-xs px-2.5 py-1.5 rounded-lg"
      style={selectStyle}
      value={String(selected ?? '')}
      onChange={value => setSelected(Number(value))}
      placeholder="选择代理组织"
      options={options.map(row => ({ value: String(row.id), label: `${row.name}（${row.status}）` }))}
    />
  );

  const createOrganization = async () => {
    if (creating || !name.trim() || !firstAdmin.trim()) return;
    setCreating(true);
    try {
      const created = await agencyApi.createOrganization(
        { name: name.trim(), first_admin_user_id: Number(firstAdmin) }, token,
      );
      setSelected(created.id);
      setName('');
      setFirstAdmin('');
      setCreateOpen(false);
      await reloadOptions();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const closeCreate = () => {
    if (creating) return;
    setCreateOpen(false);
    setName('');
    setFirstAdmin('');
  };

  if (tab === 'organizations') {
    return (
      <div className="space-y-4">
        <Card padded={false} className="p-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div>
              <h2 className="text-sm font-semibold text-primary">代理组织</h2>
              <p className="text-xs text-muted mt-1">选择组织后可查看成员或进行异常处置。</p>
            </div>
            <div className="ml-auto flex items-center gap-2 flex-wrap">
              {organizationSelect}
              <Button size="sm" variant="solid" onClick={() => { setError(null); setCreateOpen(true); }}>
                添加代理组织
              </Button>
            </div>
          </div>
        </Card>

        <Modal
          open={createOpen}
          onClose={closeCreate}
          maxWidthClass="max-w-lg"
          header={<h2 className="text-base font-semibold text-primary">创建代理组织</h2>}
          footer={<div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" disabled={creating} onClick={closeCreate}>取消</Button>
            <Button size="sm" variant="solid" loading={creating} disabled={!name.trim() || !firstAdmin.trim()}
              onClick={() => { void createOrganization(); }}>创建组织</Button>
          </div>}
        >
          <p className="text-xs text-muted">首个管理员须是已激活的普通用户；平台管理员仅负责发起创建。</p>
          <ErrorNotice error={error} />
          <Field label="组织名称" required>
            <Input size="sm" autoFocus value={name} onChange={setName} placeholder="例如：华北代理服务商" />
          </Field>
          <Field label="首个管理员用户 ID" required hint="填写普通用户的 ID">
            <Input size="sm" inputMode="numeric" value={firstAdmin} onChange={setFirstAdmin} placeholder="普通用户的 ID"
              onKeyDown={event => { if (event.key === 'Enter') void createOrganization(); }} />
          </Field>
        </Modal>

        <TableShell maxHeight={false}>
          <table className="w-full text-xs">
            <StickyHead>
              <th className="text-left px-3 py-2">组织</th>
              <th className="text-left px-3 py-2">状态</th>
              <th className="text-right px-3 py-2">操作</th>
            </StickyHead>
            {organizations.length === 0 ? <EmptyRow colSpan={3} title="暂无代理组织" hint="创建组织后会显示在这里。" /> : (
              <tbody>
                {organizations.map(row => (
                  <tr key={row.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                    <td className="px-3 py-2.5 text-primary"><span className="text-muted mr-1">#{row.id}</span>{row.name}</td>
                    <td className="px-3 py-2.5"><StatusBadge status={row.status} /></td>
                    <td className="px-3 py-2.5 text-right">
                      <Button size="sm" variant={row.status === 'active' ? 'danger' : 'outline'} onClick={async () => {
                        await agencyApi.setStatus(row.id, row.status === 'active' ? 'disabled' : 'active', token);
                        await reloadOptions();
                      }}>{row.status === 'active' ? '停用' : '启用'}</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
        </TableShell>

        {selected && (
          <Card as="section" padded={false}>
            <div className="px-3 py-2 text-sm font-medium" style={{ borderBottom: '1px solid var(--border-color)' }}>成员（平台异常处置）</div>
            <TableShell maxHeight={false} className="border-0 rounded-none">
              <table className="w-full text-xs">
                <StickyHead><th className="text-left px-3 py-2">用户</th><th className="text-left px-3 py-2">成员角色</th><th className="text-left px-3 py-2">状态</th></StickyHead>
                {members.length === 0 ? <EmptyRow colSpan={3} title="暂无成员" /> : <tbody>{members.map(row => (
                  <tr key={row.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                    <td className="px-3 py-2.5">{row.user.display_name}</td><td className="px-3 py-2.5">{row.member_role}</td>
                    <td className="px-3 py-2.5"><StatusBadge status={row.status} /></td>
                  </tr>
                ))}</tbody>}
              </table>
            </TableShell>
          </Card>
        )}
        <ErrorNotice error={error} />
      </div>
    );
  }

  if (tab === 'grants') {
    return (
      <div className="space-y-4">
        <Card padded={false} className="p-3">
          <div className="flex items-end gap-2 flex-wrap">
            <Field label="代理组织" className="w-[240px]">{organizationSelect}</Field>
            <Field label="企业账号" hint="精确填写邮箱或手机号" className="w-[260px]">
              <Input size="sm" value={identifier} onChange={setIdentifier} placeholder="name@example.com 或手机号" />
            </Field>
            <Button size="sm" variant="solid" disabled={!selected || !identifier.trim()} onClick={async () => {
              if (!selected) return;
              try {
                const query = identifier.includes('@') ? { email: identifier.trim() } : { phone: identifier.trim() };
                const found = await agencyApi.eligible(selected, query, token);
                if (!found[0]) throw new Error('未找到可授权企业');
                await agencyApi.grant(selected, found[0].id, token);
                setIdentifier('');
                setGrants(await agencyApi.grants(selected, token));
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              }
            }}>授权企业</Button>
          </div>
        </Card>
        <TableShell maxHeight={false}>
          <table className="w-full text-xs">
            <StickyHead><th className="text-left px-3 py-2">企业</th><th className="text-left px-3 py-2">联系方式</th><th className="text-left px-3 py-2">状态</th><th className="text-right px-3 py-2">操作</th></StickyHead>
            {grants.length === 0 ? <EmptyRow colSpan={4} title="暂无企业授权" hint="选择组织并授权企业后会显示在这里。" /> : <tbody>{grants.map(row => (
              <tr key={row.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                <td className="px-3 py-2.5 text-primary">{row.enterprise.display_name || `#${row.enterprise.id}`}</td>
                <td className="px-3 py-2.5 text-muted">{row.enterprise.masked_email || row.enterprise.masked_phone}</td>
                <td className="px-3 py-2.5"><StatusBadge status={row.status} /></td>
                <td className="px-3 py-2.5 text-right">{row.status === 'active' && <Button size="sm" variant="danger" onClick={async () => {
                  if (!selected) return;
                  await agencyApi.revokeGrant(selected, row.id, token);
                  setGrants(await agencyApi.grants(selected, token));
                }}>撤销</Button>}</td>
              </tr>
            ))}</tbody>}
          </table>
        </TableShell>
        <ErrorNotice error={error} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card padded={false} className="p-3"><div className="flex items-center gap-2 flex-wrap">{organizationSelect}</div></Card>
      <TableShell>
        <table className="w-full text-xs">
          <StickyHead><th className="text-left px-3 py-2">事件</th><th className="text-left px-3 py-2">对象</th><th className="text-left px-3 py-2">结果</th><th className="text-right px-3 py-2">时间</th></StickyHead>
          {audit.length === 0 ? <EmptyRow colSpan={4} title="暂无审计记录" hint="组织操作完成后会在这里留下审计记录。" /> : <tbody>{audit.map(row => (
            <tr key={row.id} style={{ borderTop: '1px solid var(--border-color)' }}>
              <td className="px-3 py-2.5">{row.event_type}</td><td className="px-3 py-2.5">{row.object_type} #{row.object_id}</td>
              <td className="px-3 py-2.5"><StatusBadge status={row.result} /></td>
              <td className="px-3 py-2.5 text-right text-muted tabular-nums">{new Date(row.created_at).toLocaleString()}</td>
            </tr>
          ))}</tbody>}
        </table>
      </TableShell>
      <ErrorNotice error={error} />
    </div>
  );
}

function AgencyAdminPanel({ tab }: { tab: Tab }) {
  const token = localStorage.getItem('token') || '';
  const [members, setMembers] = useState<AgencyMember[]>([]);
  const [enterprises, setEnterprises] = useState<AgencyEnterprise[]>([]);
  const [assignments, setAssignments] = useState<AgencyAssignment[]>([]);
  const [identifier, setIdentifier] = useState('');
  const [memberId, setMemberId] = useState('');
  const [grantId, setGrantId] = useState('');
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [m, e, a] = await Promise.all([agencyApi.members(token), agencyApi.enterprises(token), agencyApi.assignments(token)]);
      setMembers(m); setEnterprises(e); setAssignments(a); setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [token]);

  // reload's first state change happens after the async request completes, not as a synchronous effect cascade.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void reload(); }, [reload]);

  if (tab === 'members') {
    return (
      <div className="space-y-4">
        <Card padded={false} className="p-3">
          <div className="flex items-end gap-2 flex-wrap">
            <Field label="操作员账号" hint="精确填写邮箱或手机号" className="w-[260px]">
              <Input size="sm" value={identifier} onChange={setIdentifier} placeholder="name@example.com 或手机号" />
            </Field>
            <Button size="sm" variant="solid" disabled={!identifier.trim()} onClick={async () => {
              try {
                await agencyApi.addOperator(identifier.includes('@') ? { email: identifier.trim() } : { phone: identifier.trim() }, token);
                setIdentifier('');
                await reload();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              }
            }}>添加操作员</Button>
          </div>
        </Card>
        <TableShell maxHeight={false}>
          <table className="w-full text-xs">
            <StickyHead><th className="text-left px-3 py-2">用户</th><th className="text-left px-3 py-2">联系方式</th><th className="text-left px-3 py-2">组织角色</th><th className="text-right px-3 py-2">操作</th></StickyHead>
            {members.length === 0 ? <EmptyRow colSpan={4} title="暂无组织成员" hint="添加普通用户作为操作员后会显示在这里。" /> : <tbody>{members.map(row => (
              <tr key={row.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                <td className="px-3 py-2.5 text-primary">{row.user.display_name}</td>
                <td className="px-3 py-2.5 text-muted">{row.user.masked_email || row.user.masked_phone}</td>
                <td className="px-3 py-2.5"><StatusBadge status={row.member_role} /></td>
                <td className="px-3 py-2.5 text-right">{row.member_role !== 'admin' && <Button size="sm" variant="danger" onClick={async () => {
                  await agencyApi.removeMember(row.id, token);
                  await reload();
                }}>移除</Button>}</td>
              </tr>
            ))}</tbody>}
          </table>
        </TableShell>
        <ErrorNotice error={error} />
      </div>
    );
  }

  const operators = members.filter(row => row.member_role === 'operator' && row.status === 'active');
  return (
    <div className="space-y-4">
      <Card padded={false} className="p-3">
        <div className="flex items-end gap-2 flex-wrap">
          <Field label="操作员" className="w-[220px]">
            <UiSelect className="text-xs px-2.5 py-1.5 rounded-lg" style={selectStyle} value={memberId} onChange={setMemberId}
              placeholder="选择操作员" options={operators.map(row => ({ value: String(row.id), label: row.user.display_name }))} />
          </Field>
          <Field label="企业" className="w-[220px]">
            <UiSelect className="text-xs px-2.5 py-1.5 rounded-lg" style={selectStyle} value={grantId} onChange={setGrantId}
              placeholder="选择企业" options={enterprises.map(row => ({ value: String(row.grant_id), label: row.enterprise.display_name }))} />
          </Field>
          <Button size="sm" variant="solid" disabled={!memberId || !grantId} onClick={async () => {
            try {
              await agencyApi.assign(Number(memberId), Number(grantId), token);
              await reload();
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            }
          }}>分派</Button>
        </div>
      </Card>
      <TableShell maxHeight={false}>
        <table className="w-full text-xs">
          <StickyHead><th className="text-left px-3 py-2">成员</th><th className="text-left px-3 py-2">企业授权</th><th className="text-left px-3 py-2">状态</th><th className="text-right px-3 py-2">操作</th></StickyHead>
          {assignments.length === 0 ? <EmptyRow colSpan={4} title="暂无企业分派" hint="选择操作员和企业后即可建立分派。" /> : <tbody>{assignments.map(row => (
            <tr key={row.id} style={{ borderTop: '1px solid var(--border-color)' }}>
              <td className="px-3 py-2.5">成员 #{row.member_id}</td><td className="px-3 py-2.5">授权 #{row.grant_id}</td>
              <td className="px-3 py-2.5"><StatusBadge status={row.status} /></td>
              <td className="px-3 py-2.5 text-right"><Button size="sm" variant="danger" onClick={async () => {
                await agencyApi.revokeAssignment(row.id, token);
                await reload();
              }}>撤销</Button></td>
            </tr>
          ))}</tbody>}
        </table>
      </TableShell>
      <ErrorNotice error={error} />
    </div>
  );
}

export function UserOrganizationManagement() {
  const { role } = useAuth();
  const tabs = useMemo<{ key: Tab; label: string }[]>(() => role === 'admin' ? [
    { key: 'accounts', label: '用户账号' }, { key: 'organizations', label: '代理组织' },
    { key: 'grants', label: '企业授权' }, { key: 'audit', label: '审计' },
  ] : role === 'operator' ? [
    { key: 'grants', label: '企业授权' }, { key: 'audit', label: '审计' },
  ] : [
    { key: 'members', label: '本组织成员' }, { key: 'assignments', label: '企业分派' },
  ], [role]);
  const [tab, setTab] = useState<Tab>(tabs[0].key);
  useEffect(() => { setTab(tabs[0].key); }, [tabs]);

  return (
    <div>
      <PageHeader
        title="用户与组织管理"
        hint="按当前角色调用对应权限范围的接口；不会先加载全量数据再由前端隐藏。"
      />
      <Card padded={false} className="p-1.5 mb-4">
        <PillTabs value={tab} onChange={setTab}
          items={tabs.map(item => ({ value: item.key, label: item.label }))} />
      </Card>
      <section className="space-y-4">
        {tab === 'accounts'
          ? <AdminUsers embedded />
          : role === 'agency_admin'
            ? <AgencyAdminPanel tab={tab} />
            : <PlatformAgencyPanel tab={tab} canManage={role === 'admin'} />}
      </section>
    </div>
  );
}
