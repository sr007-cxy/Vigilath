import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { Pagination } from '../../components/Pagination';
import { toast } from '../../components/ui/toast';
import { UiSelect } from '../../components/ui/Select';
import {
  fetchSalesAgentSmsDeliveries,
  fetchSalesAgentSmsStats,
  type SalesAgentSmsDelivery,
  type SalesAgentSmsStats,
} from '../../services/salesAgentApi';
import { SmsDeliveryContentDialog, SmsDeliveryTable } from './SalesAgentSmsManager';

// Read-only page symmetric with "Email records": ops/regular users only have records-page permission in the workbench; the config page is for admins.
// Without this page, non-admins could see what emails were sent but not what SMS were sent.

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

export function SalesAgentSmsRecords({ admin = false }: { admin?: boolean }) {
  const [deliveries, setDeliveries] = useState<SalesAgentSmsDelivery[]>([]);
  const [stats, setStats] = useState<SalesAgentSmsStats | null>(null);
  const [viewingDelivery, setViewingDelivery] = useState<SalesAgentSmsDelivery | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [messageType, setMessageType] = useState('');
  const [query, setQuery] = useState('');
  const [deliveryTotal, setDeliveryTotal] = useState(0);
  const [deliveryPage, setDeliveryPage] = useState(1);
  const [deliveryPageSize, setDeliveryPageSize] = useState(50);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, st] = await Promise.all([
        fetchSalesAgentSmsDeliveries({ status, message_type: messageType, q: query, limit: deliveryPageSize, offset: (deliveryPage - 1) * deliveryPageSize }, admin),
        fetchSalesAgentSmsStats({}, admin),
      ]);
      setDeliveries(d.deliveries || []);
      setDeliveryTotal(d.total || 0);
      setStats(st);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '短信发送记录加载失败');
    } finally {
      setLoading(false);
    }
  }, [admin, deliveryPage, deliveryPageSize, messageType, query, status]);

  useEffect(() => { setDeliveryPage(1); }, [status, messageType, query]);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="space-y-5" style={{ padding: admin ? '20px 24px' : 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 24, fontWeight: 900 }}>短信发送记录</h1>
          <p style={{ margin: '6px 0 0', color: 'var(--text-secondary)', fontSize: 13 }}>
            单独查看 Sales Agent 短信草稿、真实下发、运营商回执、短链点击与退订记录；通道、签名模板和发送开关去「短信配置」。
          </p>
        </div>
        <button type="button" onClick={() => void load()} style={{ ...input, cursor: 'pointer' }}>{loading ? '刷新中…' : '刷新'}</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
        {/* Sent and carrier-confirmed delivered are two different things, shown separately; billed segments go in the hint slot so the bill reconciles */}
        <StatCard label="已发送" value={stats?.sent ?? 0} hint={`已送达 ${stats?.delivered ?? 0} · 计费 ${stats?.segments ?? 0} 条`} />
        <StatCard label="发送失败" value={stats?.failed ?? 0} hint={`本地拦截 ${stats?.rejected ?? 0}`} />
        <StatCard label="无可发号码" value={stats?.no_recipient ?? 0} hint={`发送关闭 ${stats?.disabled ?? 0}`} />
        <StatCard label="互动" value={stats?.click_count ?? 0} hint={`回复 ${stats?.reply_count ?? 0} · 退订 ${stats?.opted_out ?? 0}`} />
      </div>

      <div style={{ ...card, padding: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <UiSelect style={input} value={status} onChange={setStatus}
                  options={[
                    { value: '', label: '全部状态' },
                    { value: 'draft', label: '草稿' },
                    { value: 'sending', label: '发送中' },
                    { value: 'sent', label: '已发送' },
                    { value: 'delivered', label: '已送达' },
                    { value: 'failed', label: '发送失败' },
                    { value: 'rejected', label: '已拦截' },
                    { value: 'disabled', label: '已关闭' },
                    { value: 'no_recipient', label: '无可发号码' },
                  ]} />
        <UiSelect style={input} value={messageType} onChange={setMessageType}
                  options={[
                    { value: '', label: '全部类型' },
                    { value: 'outreach', label: '触达' },
                    { value: 'followup', label: '跟进' },
                    { value: 'test', label: '测试' },
                  ]} />
        <input style={{ ...input, minWidth: 220 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索企业、号码、正文" />
      </div>

      {loading && <div style={{ ...card, padding: 14, color: 'var(--text-secondary)' }}>短信发送记录加载中…</div>}
      <SmsDeliveryTable deliveries={deliveries} onViewContent={setViewingDelivery} />
      <Pagination
        className="mt-3"
        page={deliveryPage}
        pageSize={deliveryPageSize}
        total={deliveryTotal}
        onPageChange={setDeliveryPage}
        onPageSizeChange={size => { setDeliveryPageSize(size); setDeliveryPage(1); }}
      />

      <SmsDeliveryContentDialog delivery={viewingDelivery} onClose={() => setViewingDelivery(null)} />
    </div>
  );
}

export default SalesAgentSmsRecords;
