import { useAuth } from '../contexts/AuthContext';
import { useAgencyContext } from '../contexts/AgencyContext';

export function AgencyEnterpriseSwitcher() {
  const { isAgency, role } = useAuth();
  const { loading, error, me, enterprises, enterpriseUserId, selectEnterprise, managedAgency } = useAgencyContext();
  if (!isAgency && !(role === 'user' && managedAgency)) return null;
  if (!isAgency && managedAgency) {
    return (
      <div className="mb-4 rounded-lg p-3 text-xs"
           style={{ background: 'var(--warning-bg)', color: 'var(--warning-fg)', border: '1px solid var(--warning-border)' }}>
        当前企业由「{managedAgency.name}」代理，企业业务数据为只读；账户安全资料仍可正常修改。
      </div>
    );
  }
  return (
    <div className="mb-4 rounded-lg p-3 flex items-center gap-3 flex-wrap"
         style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <div className="min-w-0">
        <div className="text-[11px] text-muted">当前代理组织</div>
        <div className="text-sm font-medium text-primary truncate">{me?.organization.name || '—'}</div>
      </div>
      <label className="ml-auto text-xs text-secondary flex items-center gap-2">
        代理企业
        <select className="px-2 py-1.5 rounded-md text-xs" value={enterpriseUserId ?? ''}
          disabled={loading || enterprises.length === 0}
          onChange={e => selectEnterprise(Number(e.target.value) || null)}
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
          {enterprises.length === 0 && <option value="">暂无授权企业</option>}
          {enterprises.map(row => (
            <option key={row.enterprise.id} value={row.enterprise.id}>
              {row.enterprise.display_name || row.enterprise.masked_email || `#${row.enterprise.id}`}
            </option>
          ))}
        </select>
      </label>
      {error && <span className="w-full text-xs" style={{ color: 'var(--danger-fg)' }}>{error}</span>}
    </div>
  );
}
