import { useAgencyContext } from '../../contexts/AgencyContext';

export function AgencyEnterprises() {
  const { loading, error, enterprises, enterpriseUserId, selectEnterprise } = useAgencyContext();
  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold text-primary">授权企业</h1>
      <p className="text-xs text-secondary mt-1 mb-4">这里只显示当前代理组织实际可访问的企业；操作员仅显示本人分派。</p>
      {error && <div className="text-xs mb-3" style={{ color: 'var(--danger-fg)' }}>{error}</div>}
      {loading ? <div className="text-xs text-muted py-8">加载中…</div> : (
        <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border-color)' }}>
          <table className="w-full text-sm">
            <thead style={{ background: 'var(--bg-secondary)' }}><tr>
              <th className="text-left p-3">企业</th><th className="text-left p-3">账号</th>
              <th className="text-left p-3">授权状态</th><th className="p-3" />
            </tr></thead>
            <tbody>
              {enterprises.map(row => <tr key={row.grant_id} style={{ borderTop: '1px solid var(--border-color)' }}>
                <td className="p-3 text-primary">{row.enterprise.display_name || `企业 #${row.enterprise.id}`}</td>
                <td className="p-3 text-secondary">{row.enterprise.masked_email || row.enterprise.masked_phone || '—'}</td>
                <td className="p-3">{row.status}</td>
                <td className="p-3 text-right"><button className="text-accent hover:underline"
                  onClick={() => selectEnterprise(row.enterprise.id)}>
                  {enterpriseUserId === row.enterprise.id ? '当前企业' : '切换到此企业'}
                </button></td>
              </tr>)}
              {enterprises.length === 0 && <tr><td colSpan={4} className="p-8 text-center text-muted">暂无可访问企业</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
