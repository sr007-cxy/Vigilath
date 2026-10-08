// User detail drawer (admin) -- opened by clicking "Details" in the user management list.
//
// Three tabs, matching the three questions to answer when looking at an account in the admin:
//   Profile    who they are, what role, how much is left on the account (GET /admin/users/{id})
//   Payments   what they've paid (GET /admin/users/{id}/payments)
//   Audits     what audits they've run (GET /admin/users/{id}/detections)
//
// The two detail tabs use **server-side pagination**, unlike the list page's frontend pagination -- payments/audits are logs that grow linearly
// over time and can't all be fetched at once and sliced in memory.
//
// Requests are sent only when switching to a tab (lazy loading), so viewing only the profile doesn't fire two extra queries.
//
// Switching users relies on the parent passing key={userId} to remount entirely, rather than resetting each setState in an effect --
// one less risk of "added new state but forgot to reset it", and the previous user's payment/audit data never flashes for a frame.
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Drawer } from '../../components/overlays/Drawer';
import { Pagination } from '../../components/Pagination';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { points } from '../../services/balanceApi';
import { fmtTime } from '../../utils/datetime';
import {
  adminUsersApi,
  type AdminCreditKind,
  type AdminDetectionItem,
  type AdminPaymentItem,
  type AdminUserDetail,
} from '../../services/adminUsersApi';

type Tab = 'info' | 'payments' | 'detections';

const cell: React.CSSProperties = { borderTop: '1px solid var(--border-color)' };

// Real-money order amounts: usd / cny are stored mixed in the DB; the symbol follows each row, don't unify into one
const money = (cents: number, currency: string) =>
  `${(currency || 'usd').toLowerCase() === 'cny' ? '¥' : '$'}${(cents / 100).toFixed(2)}`;

const STATUS_COLOR: Record<string, string> = {
  paid: '#22c55e', pending: '#eab308', expired: '#6b7280', canceled: '#ef4444',
};

export function AdminUserDetailDrawer({
  userId, onClose,
}: {
  userId: number | null;
  onClose: () => void;
}) {
  const token = localStorage.getItem('token') || '';
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('info');
  const [detail, setDetail] = useState<AdminUserDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creditKind, setCreditKind] = useState<AdminCreditKind | null>(null);
  const [creditAmount, setCreditAmount] = useState('');
  const [creditReason, setCreditReason] = useState('');
  const [creditSubmitting, setCreditSubmitting] = useState(false);
  const [creditErr, setCreditErr] = useState<string | null>(null);

  const [pays, setPays] = useState<{ items: AdminPaymentItem[]; total: number } | null>(null);
  const [payPage, setPayPage] = useState(1);
  const [paySize, setPaySize] = useState(10);
  const [payLoading, setPayLoading] = useState(false);

  const [dets, setDets] = useState<{ items: AdminDetectionItem[]; total: number } | null>(null);
  const [detPage, setDetPage] = useState(1);
  const [detSize, setDetSize] = useState(10);
  const [detLoading, setDetLoading] = useState(false);

  const loadDetail = useCallback(() => {
    if (userId == null) return;
    setLoading(true);
    adminUsersApi.detail(userId, token)
      .then(setDetail)
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [userId, token]);

  useEffect(() => { loadDetail(); }, [loadDetail]);

  const loadPays = useCallback(() => {
    if (userId == null) return;
    setPayLoading(true);
    adminUsersApi.payments(userId, token, payPage, paySize)
      .then(r => setPays({ items: r.items, total: r.total }))
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setPayLoading(false));
  }, [userId, token, payPage, paySize]);

  const loadDets = useCallback(() => {
    if (userId == null) return;
    setDetLoading(true);
    adminUsersApi.detections(userId, token, detPage, detSize)
      .then(r => setDets({ items: r.items, total: r.total }))
      .catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setDetLoading(false));
  }, [userId, token, detPage, detSize]);

  useEffect(() => { if (tab === 'payments') loadPays(); }, [tab, loadPays]);
  useEffect(() => { if (tab === 'detections') loadDets(); }, [tab, loadDets]);

  const openCredit = (kind: AdminCreditKind) => {
    setCreditKind(kind);
    setCreditAmount('');
    setCreditReason('');
    setCreditErr(null);
  };

  const submitCredit = async () => {
    if (userId == null || creditKind == null) return;
    const amount = Number(creditAmount);
    const reason = creditReason.trim();
    if (!Number.isSafeInteger(amount) || amount <= 0) {
      setCreditErr(t('workbench.users.detail.creditAmountInvalid', {
        defaultValue: '请输入大于 0 的整数积分',
      }));
      return;
    }
    if (!reason) {
      setCreditErr(t('workbench.users.detail.creditReasonRequired', {
        defaultValue: '请填写操作原因',
      }));
      return;
    }

    const action = creditKind === 'recharge'
      ? t('workbench.users.detail.creditRecharge', { defaultValue: '充值积分' })
      : t('workbench.users.detail.creditGrant', { defaultValue: '赠送积分' });
    const confirmed = await uiConfirm(
      t('workbench.users.detail.creditConfirm', {
        defaultValue: '确认给 {{user}} {{action}} {{amount}}？\n原因：{{reason}}',
        user: detail?.email || `#${userId}`,
        action,
        amount: points(amount),
        reason,
      }),
      { title: action, okText: t('common.confirm', { defaultValue: '确认' }) },
    );
    if (!confirmed) return;

    setCreditSubmitting(true);
    setCreditErr(null);
    try {
      await adminUsersApi.creditBalance(userId, amount, creditKind, reason, token);
      const refreshed = await adminUsersApi.detail(userId, token);
      setDetail(refreshed);
      setCreditKind(null);
      toast.success(t('workbench.users.detail.creditSuccess', {
        defaultValue: '{{action}}成功，当前余额 {{balance}}',
        action,
        balance: points(refreshed.balance.balance_cents),
      }));
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setCreditErr(message);
      toast.error(message);
    } finally {
      setCreditSubmitting(false);
    }
  };

  if (userId == null) return null;

  const tabBtn = (key: Tab, label: string, badge?: number) => (
    <button key={key} type="button" onClick={() => setTab(key)}
            className="text-xs px-3 py-1.5 rounded-md"
            style={tab === key
              ? { background: 'var(--accent-primary)', color: '#fff' }
              : { background: 'var(--bg-input)', color: 'var(--text-secondary)',
                  border: '1px solid var(--border-color)' }}>
      {label}
      {badge != null && <span className="ml-1 tabular-nums opacity-70">{badge}</span>}
    </button>
  );

  const row = (label: string, value: React.ReactNode) => (
    <div className="flex gap-3 py-1.5" style={cell}>
      <span className="text-muted shrink-0" style={{ width: 96 }}>{label}</span>
      <span className="text-primary break-all">{value}</span>
    </div>
  );

  const dash = t('workbench.users.detail.none', { defaultValue: '—' });

  return (
    <Drawer
      open
      onClose={onClose}
      widthClass="w-full md:w-[62%] max-w-3xl"
      header={
        <>
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-primary truncate">
              {detail?.name || detail?.email || `#${userId}`}
            </h2>
            <p className="text-xs text-muted mt-0.5 truncate">
              {t('workbench.users.detail.subtitle', { defaultValue: '用户详情' })}
              {' · ID '}{userId}
            </p>
          </div>
          <button type="button" onClick={onClose}
                  className="text-xs px-2 py-1 rounded-md shrink-0"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            {t('workbench.users.close', { defaultValue: '关闭' })}
          </button>
        </>
      }
    >
      <div className="flex items-center gap-1.5 flex-wrap">
        {tabBtn('info', t('workbench.users.detail.tabInfo', { defaultValue: '用户信息' }))}
        {tabBtn('payments', t('workbench.users.detail.tabPayments', { defaultValue: '支付记录' }),
                detail?.payment_count)}
        {tabBtn('detections', t('workbench.users.detail.tabDetections', { defaultValue: '检测记录' }),
                detail?.detection_count)}
      </div>

      {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

      {tab === 'info' && (
        loading || !detail ? (
          <div className="text-xs text-muted text-center py-10">
            {t('common.loading', { defaultValue: '加载中…' })}
          </div>
        ) : (
          <div className="space-y-3 text-xs">
            <section className="rounded-lg p-3"
                     style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <h3 className="text-primary font-medium mb-1">
                {t('workbench.users.detail.basic', { defaultValue: '基础资料' })}
              </h3>
              {row(t('workbench.users.colEmail', { defaultValue: '邮箱' }), detail.email)}
              {row(t('workbench.users.colName', { defaultValue: '名称' }), detail.name || dash)}
              {row(t('workbench.users.detail.phone', { defaultValue: '手机号' }),
                   detail.phone || dash)}
              {row(t('workbench.users.detail.wechat', { defaultValue: '微信' }),
                   detail.wechat_bound
                     ? `${detail.wechat_nickname || t('workbench.users.detail.wechatBound', { defaultValue: '已绑定' })}` +
                       (detail.wechat_subscribed
                         ? ''
                         : t('workbench.users.detail.wechatUnsub', { defaultValue: '(已取关)' }))
                     : dash)}
              {row(t('workbench.users.colRole', { defaultValue: '角色' }), detail.role)}
              {row(t('workbench.users.colStatus', { defaultValue: '状态' }),
                   <span style={{ color: detail.is_active ? 'var(--success-fg)' : 'var(--danger-fg)' }}>
                     {detail.is_active
                       ? t('workbench.users.active', { defaultValue: '正常' })
                       : t('workbench.users.inactive', { defaultValue: '已停用' })}
                   </span>)}
              {/* Old accounts registered before 2026-08-26 don't have this time in the DB, so only — can be shown */}
              {row(t('workbench.users.detail.registeredAt', { defaultValue: '注册时间' }),
                   detail.created_at ? fmtTime(detail.created_at) : dash)}
              {row(t('workbench.users.colTenant', { defaultValue: '绑定租户' }),
                   detail.tenant_name || dash)}
            </section>

            <section className="rounded-lg p-3"
                     style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <h3 className="text-primary font-medium mb-1">
                {t('workbench.users.detail.membership', { defaultValue: '会员与积分' })}
              </h3>
              {row(t('workbench.users.colMembership', { defaultValue: '会员等级' }),
                   detail.membership_name
                     || t('workbench.users.detail.freeTier', { defaultValue: '免费档' }))}
              {row(t('workbench.users.detail.period', { defaultValue: '有效期' }),
                   detail.membership_end_date
                     ? `${detail.membership_start_date ? fmtTime(detail.membership_start_date, false) : ''}`
                       + ` → ${fmtTime(detail.membership_end_date, false)}`
                     : dash)}
              {row(t('workbench.users.detail.balance', { defaultValue: '积分余额' }),
                   detail.balance.available ? points(detail.balance.balance_cents)
                     : t('workbench.users.detail.balanceNA', { defaultValue: '暂不可用' }))}
              {detail.balance.available && row(
                t('workbench.users.detail.balanceBreakdown', { defaultValue: '积分明细' }),
                t('workbench.users.detail.balanceBreakdownVal', {
                  defaultValue: '赠送 {{g}} · 充值 {{r}} · 已消费 {{s}}',
                  g: points(detail.balance.granted_cents),
                  r: points(detail.balance.recharged_cents),
                  s: points(detail.balance.spent_cents),
                }))}
              {detail.balance.available && (
                <div className="pt-3 mt-2" style={cell}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <button type="button" onClick={() => openCredit('recharge')}
                            className="px-3 py-1.5 rounded-md text-xs text-white"
                            style={{ background: 'var(--accent-primary)' }}>
                      {t('workbench.users.detail.creditRecharge', { defaultValue: '充值积分' })}
                    </button>
                    <button type="button" onClick={() => openCredit('grant')}
                            className="px-3 py-1.5 rounded-md text-xs"
                            style={{ color: 'var(--accent-primary)', border: '1px solid var(--accent-primary)' }}>
                      {t('workbench.users.detail.creditGrant', { defaultValue: '赠送积分' })}
                    </button>
                    <span className="text-muted">
                      {t('workbench.users.detail.creditHint', { defaultValue: '操作将写入积分流水' })}
                    </span>
                  </div>

                  {creditKind && (
                    <div className="mt-3 rounded-lg p-3 space-y-3"
                         style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
                      <div className="flex items-center justify-between gap-3">
                        <strong className="text-primary font-medium">
                          {creditKind === 'recharge'
                            ? t('workbench.users.detail.creditRecharge', { defaultValue: '充值积分' })
                            : t('workbench.users.detail.creditGrant', { defaultValue: '赠送积分' })}
                        </strong>
                        <button type="button" disabled={creditSubmitting}
                                onClick={() => setCreditKind(null)}
                                className="text-muted hover:text-primary text-base leading-none"
                                aria-label={t('common.close', { defaultValue: '关闭' })}>×</button>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-[minmax(0,160px)_1fr]">
                        <label className="block text-secondary">
                          <span className="block mb-1">
                            {t('workbench.users.detail.creditAmount', { defaultValue: '积分数量' })} *
                          </span>
                          <input type="number" min="1" step="1" inputMode="numeric"
                                 autoFocus value={creditAmount}
                                 onChange={e => setCreditAmount(e.target.value)}
                                 disabled={creditSubmitting}
                                 placeholder="1000"
                                 className="w-full px-2 py-1.5 rounded-md text-xs text-primary"
                                 style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }} />
                        </label>
                        <label className="block text-secondary">
                          <span className="block mb-1">
                            {t('workbench.users.detail.creditReason', { defaultValue: '操作原因' })} *
                          </span>
                          <input type="text" value={creditReason}
                                 onChange={e => setCreditReason(e.target.value)}
                                 onKeyDown={e => { if (e.key === 'Enter') void submitCredit(); }}
                                 disabled={creditSubmitting}
                                 placeholder={t('workbench.users.detail.creditReasonPlaceholder', {
                                   defaultValue: '例如：线下收款、活动赠送',
                                 })}
                                 className="w-full px-2 py-1.5 rounded-md text-xs text-primary"
                                 style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }} />
                        </label>
                      </div>
                      {creditErr && <div style={{ color: 'var(--danger-fg)' }}>{creditErr}</div>}
                      <div className="flex justify-end gap-2">
                        <button type="button" disabled={creditSubmitting}
                                onClick={() => setCreditKind(null)}
                                className="px-3 py-1.5 rounded-md text-xs"
                                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
                          {t('common.cancel', { defaultValue: '取消' })}
                        </button>
                        <button type="button" disabled={creditSubmitting}
                                onClick={() => void submitCredit()}
                                className="px-3 py-1.5 rounded-md text-xs text-white"
                                style={{ background: 'var(--accent-primary)', opacity: creditSubmitting ? 0.55 : 1 }}>
                          {creditSubmitting
                            ? t('common.submitting', { defaultValue: '提交中…' })
                            : t('common.confirm', { defaultValue: '确认入账' })}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </section>

            <section className="rounded-lg p-3"
                     style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <h3 className="text-primary font-medium mb-1">
                {t('workbench.users.detail.usage', { defaultValue: '使用与付费汇总' })}
              </h3>
              {row(t('workbench.users.detail.detCount', { defaultValue: '检测次数' }),
                   `${detail.detection_count}`)}
              {row(t('workbench.users.detail.detLast', { defaultValue: '最近检测' }),
                   detail.detection_last_at ? fmtTime(detail.detection_last_at) : dash)}
              {row(t('workbench.users.detail.payCount', { defaultValue: '订单数' }),
                   t('workbench.users.detail.payCountVal', {
                     defaultValue: '{{paid}} 笔已支付 / 共 {{total}} 笔',
                     paid: detail.payment_paid_count, total: detail.payment_count }))}
              {/* usd / cny listed separately -- adding them together gives a number without a unit */}
              {row(t('workbench.users.detail.paidTotal', { defaultValue: '累计支付' }),
                   Object.keys(detail.payment_paid_cents).length
                     ? Object.entries(detail.payment_paid_cents)
                         .map(([cur, c]) => money(c, cur)).join(' + ')
                     : dash)}
              {row(t('workbench.users.detail.payLast', { defaultValue: '最近支付' }),
                   detail.payment_last_paid_at ? fmtTime(detail.payment_last_paid_at) : dash)}
            </section>
          </div>
        )
      )}

      {tab === 'payments' && (
        <div className="rounded-lg overflow-hidden"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          {payLoading && !pays ? (
            <div className="text-xs text-muted text-center py-10">
              {t('common.loading', { defaultValue: '加载中…' })}
            </div>
          ) : (
            <div className="overflow-auto" style={{ maxHeight: 'clamp(360px, 62vh, 720px)' }}>
              <table className="w-full text-xs">
                <thead className="text-muted sticky top-0 z-10" style={{ background: 'var(--bg-card)', boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
                  <tr>
                    <th className="text-left px-3 py-2">#</th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.payItem', { defaultValue: '项目' })}
                    </th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.payAmount', { defaultValue: '金额' })}
                    </th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.payChannel', { defaultValue: '渠道' })}
                    </th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.colStatus', { defaultValue: '状态' })}
                    </th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.payTime', { defaultValue: '时间' })}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(pays?.items ?? []).map(p => (
                    <tr key={p.id} style={cell}>
                      <td className="px-3 py-2 text-muted tabular-nums">{p.id}</td>
                      <td className="px-3 py-2 text-primary">
                        {p.purpose === 'topup'
                          ? t('workbench.users.detail.purposeTopup', { defaultValue: '积分充值' })
                          : p.membership_name
                            || t('workbench.users.detail.purposeMembership', { defaultValue: '会员' })}
                      </td>
                      <td className="px-3 py-2 text-primary tabular-nums">
                        {money(p.amount_cents, p.currency)}
                      </td>
                      <td className="px-3 py-2 text-muted">{p.provider}</td>
                      <td className="px-3 py-2">
                        <span style={{ color: STATUS_COLOR[p.status] || 'var(--text-secondary)' }}>
                          {p.status}
                        </span>
                      </td>
                      {/* Paid ones show the payment received time, unpaid ones the order time */}
                      <td className="px-3 py-2 text-muted tabular-nums">
                        {fmtTime(p.completed_at || p.created_at)}
                      </td>
                    </tr>
                  ))}
                  {pays && pays.items.length === 0 && (
                    <tr><td colSpan={6} className="px-3 py-6 text-center text-muted">
                      {t('workbench.users.detail.noPayments', { defaultValue: '没有支付记录' })}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          {pays && (
            <div className="px-3 py-2 border-t" style={{ borderColor: 'var(--border-color)' }}>
              <Pagination page={payPage} pageSize={paySize} total={pays.total}
                          onPageChange={setPayPage}
                          onPageSizeChange={s => { setPaySize(s); setPayPage(1); }} />
            </div>
          )}
        </div>
      )}

      {tab === 'detections' && (
        <div className="rounded-lg overflow-hidden"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          {detLoading && !dets ? (
            <div className="text-xs text-muted text-center py-10">
              {t('common.loading', { defaultValue: '加载中…' })}
            </div>
          ) : (
            <div className="overflow-auto" style={{ maxHeight: 'clamp(360px, 62vh, 720px)' }}>
              <table className="w-full text-xs">
                <thead className="text-muted sticky top-0 z-10" style={{ background: 'var(--bg-card)', boxShadow: 'inset 0 -1px 0 var(--border-color)' }}>
                  <tr>
                    <th className="text-left px-3 py-2">#</th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.detUrl', { defaultValue: '站点' })}
                    </th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.detScore', { defaultValue: '得分' })}
                    </th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.detMode', { defaultValue: '模式' })}
                    </th>
                    <th className="text-left px-3 py-2">
                      {t('workbench.users.detail.detTime', { defaultValue: '时间' })}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(dets?.items ?? []).map(d => (
                    <tr key={d.id} style={{ ...cell, opacity: d.deleted_at ? 0.55 : 1 }}>
                      <td className="px-3 py-2 text-muted tabular-nums">{d.id}</td>
                      <td className="px-3 py-2 text-primary break-all">
                        {d.url}
                        {/* Records the user deleted are still listed in the admin; mark them so they aren't mistaken as still present */}
                        {d.deleted_at && (
                          <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded"
                                style={{ background: 'var(--bg-input)', color: 'var(--text-muted)' }}>
                            {t('workbench.users.detail.detDeleted', { defaultValue: '已删除' })}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-primary tabular-nums">
                        {d.score == null ? dash : `${d.score}${d.grade ? ` / ${d.grade}` : ''}`}
                      </td>
                      <td className="px-3 py-2 text-muted">
                        {d.mode === 'advanced'
                          ? t('workbench.users.detail.modeAdvanced', { defaultValue: '进阶' })
                          : t('workbench.users.detail.modeFree', { defaultValue: '基础' })}
                      </td>
                      <td className="px-3 py-2 text-muted tabular-nums">{fmtTime(d.created_at)}</td>
                    </tr>
                  ))}
                  {dets && dets.items.length === 0 && (
                    <tr><td colSpan={5} className="px-3 py-6 text-center text-muted">
                      {t('workbench.users.detail.noDetections', { defaultValue: '没有检测记录' })}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          {dets && (
            <div className="px-3 py-2 border-t" style={{ borderColor: 'var(--border-color)' }}>
              <Pagination page={detPage} pageSize={detSize} total={dets.total}
                          onPageChange={setDetPage}
                          onPageSizeChange={s => { setDetSize(s); setDetPage(1); }} />
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
