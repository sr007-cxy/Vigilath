import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { Pagination } from '../../components/Pagination';
import { toast } from '../../components/ui/toast';
import { UiSelect } from '../../components/ui/Select';
import {
  fetchSalesAgentEmailDeliveries,
  fetchSalesAgentEmailStats,
  type SalesAgentEmailDelivery,
  type SalesAgentEmailStats,
} from '../../services/salesAgentApi';
import { DeliveryContentDialog, DeliveryTable } from './SalesAgentEmailManager';

const card: CSSProperties = { background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 16 };
const input: CSSProperties = { background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)', borderRadius: 8, padding: '8px 10px' };

function StatCard({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div style={{ ...card, padding: 14, minWidth: 130 }}>
      <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{label}</div>
      <div style={{ color: 'var(--text-primary)', fontSize: 24, fontWeight: 800, lineHeight: 1.2 }}>{value}</div>
      {hint && <div style={{ color: 'var(--text-secondary)', fontSize: 11, marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

export function SalesAgentEmailRecords({ admin = false }: { admin?: boolean }) {
  const [deliveries, setDeliveries] = useState<SalesAgentEmailDelivery[]>([]);
  const [stats, setStats] = useState<SalesAgentEmailStats | null>(null);
  const [viewingDelivery, setViewingDelivery] = useState<SalesAgentEmailDelivery | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [read, setRead] = useState('');
  const [query, setQuery] = useState('');
  const [deliveryTotal, setDeliveryTotal] = useState(0);
  const [deliveryPage, setDeliveryPage] = useState(1);
  const [deliveryPageSize, setDeliveryPageSize] = useState(50);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, st] = await Promise.all([
        fetchSalesAgentEmailDeliveries({ status, read, q: query, limit: deliveryPageSize, offset: (deliveryPage - 1) * deliveryPageSize }, admin),
        fetchSalesAgentEmailStats({}, admin),
      ]);
      setDeliveries(d.deliveries || []);
      setDeliveryTotal(d.total || 0);
      setStats(st);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '发送记录加载失败');
    } finally {
      setLoading(false);
    }
  }, [admin, deliveryPage, deliveryPageSize, query, read, status]);

  useEffect(() => { setDeliveryPage(1); }, [status, read, query]);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="space-y-5" style={{ padding: admin ? '20px 24px' : 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 24, fontWeight: 900 }}>邮件发送记录</h1>
          <p style={{ margin: '6px 0 0', color: 'var(--text-secondary)', fontSize: 13 }}>
            单独查看 Sales Agent 邮件草稿、真实发送、打开像素和报告访问记录；发件箱和发送开关去「邮件配置」。
          </p>
        </div>
        <button type="button" onClick={() => void load()} style={{ ...input, cursor: 'pointer' }}>{loading ? '刷新中…' : '刷新'}</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
        <StatCard label="发送成功" value={stats?.sent ?? 0} />
        <StatCard label="发送失败" value={stats?.failed ?? 0} />
        <StatCard label="无收件人" value={stats?.no_recipient ?? 0} />
        <StatCard label="已阅读" value={stats?.read ?? 0} hint={`打开 ${stats?.open_count ?? 0} · 报告 ${stats?.report_visit_count ?? 0}`} />
      </div>

      <div style={{ ...card, padding: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <UiSelect style={input} value={status} onChange={setStatus}
                  options={[
                    { value: '', label: '全部状态' },
                    { value: 'draft', label: '草稿' },
                    { value: 'sent', label: '成功' },
                    { value: 'failed', label: '失败' },
                    { value: 'disabled', label: '已关闭' },
                    { value: 'no_recipient', label: '无收件人' },
                  ]} />
        <UiSelect style={input} value={read} onChange={setRead}
                  options={[
                    { value: '', label: '全部阅读状态' },
                    { value: 'read', label: '已阅读' },
                    { value: 'unread', label: '未阅读' },
                  ]} />
        <input style={{ ...input, minWidth: 220 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索企业、邮箱、主题" />
      </div>

      {loading && <div style={{ ...card, padding: 14, color: 'var(--text-secondary)' }}>发送记录加载中…</div>}
      <DeliveryTable deliveries={deliveries} onViewContent={setViewingDelivery} />
      <Pagination
        className="mt-3"
        page={deliveryPage}
        pageSize={deliveryPageSize}
        total={deliveryTotal}
        onPageChange={setDeliveryPage}
        onPageSizeChange={size => { setDeliveryPageSize(size); setDeliveryPage(1); }}
      />

      <DeliveryContentDialog delivery={viewingDelivery} onClose={() => setViewingDelivery(null)} />
    </div>
  );
}

export default SalesAgentEmailRecords;
