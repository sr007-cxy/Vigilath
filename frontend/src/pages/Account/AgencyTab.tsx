import { useEffect, useState } from 'react';
import { agencyApi } from '../../services/agencyApi';

export function AgencyTab() {
  const token = localStorage.getItem('token') || '';
  const [data, setData] = useState<Awaited<ReturnType<typeof agencyApi.accountAgency>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    agencyApi.accountAgency(token).then(setData)
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, [token]);
  return (
    <section>
      <h1 className="text-xl font-semibold text-primary">我的代理商</h1>
      <p className="text-xs text-secondary mt-1 mb-5">这里只读展示当前代理关系；授权、撤销和成员分派由平台及代理组织管理。</p>
      {error && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{error}</div>}
      {!data && !error ? <div className="text-xs text-muted">加载中…</div> : data?.agency ? (
        <div className="rounded-xl p-5 space-y-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <div><span className="text-xs text-muted">代理组织</span><div className="font-medium text-primary">{data.agency.name}</div></div>
          <div className="flex gap-8 text-sm"><div><span className="text-xs text-muted block">组织状态</span>{data.agency.status}</div>
            <div><span className="text-xs text-muted block">授权状态</span>{data.grant?.status || '—'}</div></div>
          <div className="text-xs text-secondary">有效代理期间，企业业务数据只读；密码、绑定方式和联系方式不受影响。</div>
        </div>
      ) : <div className="rounded-xl p-5 text-sm text-muted" style={{ border: '1px solid var(--border-color)' }}>当前没有有效代理关系。</div>}
    </section>
  );
}
