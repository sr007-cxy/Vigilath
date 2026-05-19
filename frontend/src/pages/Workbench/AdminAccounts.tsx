import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { aiTelemetryApi, type AdminAccount } from '../../services/aiTelemetryApi';

export function AdminAccounts() {
  const token = localStorage.getItem('token') || '';
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    aiTelemetryApi.adminListAccounts(token)
      .then(setAccounts)
      .catch(e => setErr(e?.message || '加载失败'))
      .finally(() => setLoading(false));
  }, [token]);

  return (
    <div className="max-w-[1100px] mx-auto p-6">
      <h1 className="text-xl font-semibold text-primary mb-4">账户管理</h1>
      <p className="text-xs text-muted mb-4">
        替每个账户配置监测主题、种子提示词、扩展提示。普通用户不能编辑这些字段。
      </p>
      {loading ? (
        <div className="text-xs text-muted text-center py-10">加载中…</div>
      ) : err ? (
        <div className="text-xs text-red-500">{err}</div>
      ) : (
        <div className="rounded-lg overflow-hidden" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted">
                <th className="text-left px-3 py-2">ID</th>
                <th className="text-left px-3 py-2">Email</th>
                <th className="text-left px-3 py-2">名称</th>
                <th className="text-right px-3 py-2">主题数</th>
                <th className="text-left px-3 py-2">扩展提示</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {accounts.map(a => (
                <tr key={a.id} className="border-t" style={{ borderColor: 'var(--border-color)' }}>
                  <td className="px-3 py-2 text-muted tabular-nums">{a.id}</td>
                  <td className="px-3 py-2 text-primary">{a.email}{a.is_admin && <span className="ml-1 text-[10px] text-amber-500">admin</span>}</td>
                  <td className="px-3 py-2 text-secondary">{a.name || '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.topic_count}</td>
                  <td className="px-3 py-2">
                    {a.has_prompt_extension ? (
                      <span className="text-green-500">✓ 已配</span>
                    ) : (
                      <span className="text-muted">未配</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {a.topic_count > 0 ? (
                      <Link to={`/workbench/accounts/${a.id}/topics`} className="text-accent hover:underline">
                        配置主题 →
                      </Link>
                    ) : (
                      <span className="text-muted">无主题</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
