// My credits -- credit balance / top-up / transactions.
//
// Externally it's always called "credits", while the DB still stores "fen" -- **1 yuan = 100 credits**, values 1:1 (a 39.9 yuan
// membership = 3,990 credits). Conversion and copy are all in balanceApi's points(); don't write your own per page.
//
// Before this page existed, when the backend returned 402 for insufficient balance it sent users to /account/balance, a route that
// didn't exist at all -- clicking it gave a 404. So it isn't just "a new page", it's the landing spot for that 402 guidance.
//
// Top-up reuses the membership payment flow: the same payment_sessions, the same status polling endpoint, just
// purpose='topup', which on callback credits the balance instead of activating a plan. The QR code block is copied from CheckoutPending.
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { QRCodeSVG } from 'qrcode.react';
import { fmtTime } from '../../utils/datetime';
import { paymentApi } from '../../services/paymentApi';
import {
  balanceApi, points, yuan,
  type BalanceOut, type LedgerItem,
} from '../../services/balanceApi';
import { toast } from '../../components/ui/toast';

const PAGE_SIZE = 20;

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};
const borderBottomStyle: React.CSSProperties = {
  borderBottom: '1px solid var(--border-color)',
};

// Quick tiers (in fen). Custom amounts can still be entered; min/max limits come from the backend, not hardcoded in the frontend.
const PRESETS = [5000, 10000, 30000, 50000];

// Transaction type -> human-readable. The backend kind is an English constant that users can't understand if rendered directly.
const KIND_LABELS: Record<string, string> = {
  grant: '注册赠送',
  recharge: '充值',
  refund: '退款',
  adjust: '人工调账',
  batch: '监测跑批',
  // One-click full diagnosis charges per "prompt x engine" Q&A count, at the same unit price as batch runs, but it **is not a batch run** --
  // while it was recorded as batch, users saw "monitoring batch run" in this table but couldn't find
  // any batch run record in task monitoring (batch runs themselves were still in a free shadow period). Separate categories make the books match.
  full_diagnosis: '一键全面诊断',
  solution: 'GEO策略优化方案',
  content_doc: '文章生成',
  content_image: '配图',
  compliance: '合规审核',
  publish_media: '付费投放',
  retest: '被引复测',
  sentiment_once: '舆情快检',
  sentiment_daily: '舆情日常监控',
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] || kind;
}

// Supplementary note for a transaction row. Keys in note differ by category; only pick the few users can understand.
function noteText(row: LedgerItem): string {
  const n = row.note || {};
  const media = typeof n.media === 'string' ? n.media : '';
  if (media) return media;
  const cells = typeof n.cells === 'number' ? n.cells : null;
  if (cells !== null) return `${cells} 次引擎提问`;
  const docs = typeof n.docs === 'number' ? n.docs : null;
  if (docs !== null) return `${docs} 篇`;
  const reason = typeof n.reason === 'string' ? n.reason : '';
  return reason;
}

export function BalanceTab() {
  const { t } = useTranslation();
  const [bal, setBal] = useState<BalanceOut | null>(null);
  const [items, setItems] = useState<LedgerItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Top-up dialog
  const [topupOpen, setTopupOpen] = useState(false);
  const [amountYuan, setAmountYuan] = useState('100');
  const [codeUrl, setCodeUrl] = useState('');
  const [paymentId, setPaymentId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);

  const token = localStorage.getItem('token') || '';

  const load = useCallback(async (targetPage: number) => {
    if (!token) return;
    setLoading(true);
    setError('');
    try {
      const [b, l] = await Promise.all([
        balanceApi.get(token),
        balanceApi.ledger(token, targetPage, PAGE_SIZE),
      ]);
      setBal(b);
      setItems(l.items);
      setTotal(l.total);
      setPage(l.page);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.errors.loadFailed', '加载失败'));
    } finally {
      setLoading(false);
    }
  }, [t, token]);

  useEffect(() => { load(1); }, [load]);

  // After the QR code is shown, poll until credited. Top-up and plan orders share payment_sessions; the status endpoint is reused as-is.
  useEffect(() => {
    if (!paymentId || !codeUrl) return;
    let stop = false;
    const timer = setInterval(async () => {
      try {
        const s = await paymentApi.getWechatPayStatus(token, paymentId);
        if (stop) return;
        if (s.status === 'paid') {
          clearInterval(timer);
          setTopupOpen(false);
          setCodeUrl('');
          setPaymentId(null);
          toast.success('充值已到账');
          load(1);
        } else if (s.status === 'expired' || s.status === 'failed') {
          clearInterval(timer);
          setCodeUrl('');
          setPaymentId(null);
          toast.error('这笔充值已失效,请重新发起');
        }
      } catch {
        // Polling failures don't interrupt the user -- retry next round
      }
    }, 3000);
    return () => { stop = true; clearInterval(timer); };
  }, [paymentId, codeUrl, token, load]);

  const startTopup = async () => {
    if (!bal) return;
    const cents = Math.round(Number(amountYuan) * 100);
    if (!Number.isFinite(cents) || cents < bal.topup_min_cents) {
      toast.error(`最低充值 ${yuan(bal.topup_min_cents)}`);
      return;
    }
    if (cents > bal.topup_max_cents) {
      toast.error(`单笔最高 ${yuan(bal.topup_max_cents)},更大金额请联系我们对公转账`);
      return;
    }
    setCreating(true);
    try {
      const out = await balanceApi.createTopup(token, cents);
      setCodeUrl(out.code_url);
      setPaymentId(out.payment_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '创建充值订单失败');
    } finally {
      setCreating(false);
    }
  };

  const closeTopup = () => {
    setTopupOpen(false);
    setCodeUrl('');
    setPaymentId(null);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      {/* Balance header card */}
      <div className="rounded-2xl p-6" style={cardStyle}>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm text-secondary mb-1">
              {t('account.balance.current', '积分余额')}
            </p>
            <p className="text-4xl font-bold text-primary tabular-nums">
              {bal ? points(bal.balance_cents) : '—'}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setTopupOpen(true)}
            className="px-5 py-2.5 rounded-lg font-semibold text-sm text-white transition-colors"
            style={{ background: 'var(--accent-primary)' }}
          >
            {t('account.balance.topup', '充值')}
          </button>
        </div>

        {/* Metric definition change notice -- numbers went x100 overnight; without explaining it, it'd be taken as a billing error */}
        <p className="text-xs mt-4" style={{ color: 'var(--text-muted)' }}>
          {t('account.balance.unitNotice',
             '积分口径已调整为 1 元 = 100 积分,余额数字相应 ×100,购买力不变。')}
        </p>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mt-6">
          {[
            [t('account.balance.granted', '赠送积分'), bal?.granted_cents],
            [t('account.balance.recharged', '累计充值'), bal?.recharged_cents],
            [t('account.balance.spent', '累计消费'), bal?.spent_cents],
          ].map(([label, cents]) => (
            <div key={String(label)}>
              <p className="text-xs text-secondary mb-1">{label}</p>
              <p className="text-lg font-semibold text-primary tabular-nums">
                {bal ? points(cents as number) : '—'}
              </p>
            </div>
          ))}
        </div>

        {bal && (
          <div
            className="mt-6 pt-4 text-xs text-secondary leading-relaxed"
            style={{ borderTop: '1px solid var(--border-color)' }}
          >
            <p className="mb-1">
              {t('account.balance.priceHint', '当前单价')}:
              监测每次引擎提问 {points(bal.price_batch_cell_cents)}
              {' · '}文章生成每篇 {points(bal.price_content_doc_cents)}
              {bal.price_sentiment_once_cents > 0
                && ` · 舆情快检每单 ${points(bal.price_sentiment_once_cents)}`}
              {bal.price_sentiment_daily_cents > 0
                && ` · 舆情日常监控每天 ${points(bal.price_sentiment_daily_cents)}`}
            </p>
            <p>
              {bal.batch_billing_start_date
                ? t('account.balance.settleHintLive',
                    '付费投放按实际报价即时扣费,失败全额退回;监测跑批自 {{date}} 起按次日结算,此前的用量不计费。',
                    { date: bal.batch_billing_start_date })
                : t('account.balance.settleHintPreview',
                    '付费投放按实际报价即时扣费,失败全额退回;监测跑批目前为预览期,暂不计费,开始计费前会提前告知。')}
            </p>
          </div>
        )}
      </div>

      {/* Transactions */}
      <div className="rounded-2xl overflow-hidden" style={cardStyle}>
        <div className="px-6 py-4 flex items-center justify-between" style={borderBottomStyle}>
          <h2 className="text-lg font-bold text-primary">
            {t('account.balance.ledger', '积分明细')}
          </h2>
          <span className="text-sm text-secondary">
            {t('account.balance.ledgerTotal', { n: total, defaultValue: '共 {{n}} 条' })}
          </span>
        </div>

        {loading ? (
          <div className="p-10 text-center">
            <div
              className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 mx-auto"
              style={{ borderColor: 'var(--accent-primary)' }}
            />
          </div>
        ) : error ? (
          <div className="p-6 text-sm" style={{ color: '#ff006e' }}>{error}</div>
        ) : items.length === 0 ? (
          <div className="p-10 text-center">
            <p className="text-secondary">
              {t('account.balance.ledgerEmpty', '还没有任何流水')}
            </p>
          </div>
        ) : (
          <div className="divide-y" style={{ borderColor: 'var(--border-color)' }}>
            {items.map((row) => {
              const isCredit = row.direction === 'credit';
              const note = noteText(row);
              return (
                <div
                  key={row.id}
                  className="px-6 py-3.5 flex items-center justify-between gap-4"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-primary truncate">
                      {kindLabel(row.kind)}
                      {note && <span className="text-secondary font-normal"> · {note}</span>}
                    </p>
                    <p className="text-xs text-secondary mt-0.5">{fmtTime(row.created_at)}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p
                      className="text-sm font-semibold tabular-nums"
                      style={{ color: isCredit ? 'var(--success-fg)' : 'var(--text-primary)' }}
                    >
                      {isCredit ? '+' : '−'}{points(row.amount_cents)}
                    </p>
                    <p className="text-xs text-secondary mt-0.5 tabular-nums">
                      {t('account.balance.after', '余额')} {points(row.balance_after_cents)}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {totalPages > 1 && (
          <div
            className="px-6 py-3 flex items-center justify-between"
            style={{ borderTop: '1px solid var(--border-color)' }}
          >
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => load(page - 1)}
              className="text-sm text-secondary disabled:opacity-40"
            >
              {t('common.prev', '上一页')}
            </button>
            <span className="text-sm text-secondary tabular-nums">{page} / {totalPages}</span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => load(page + 1)}
              className="text-sm text-secondary disabled:opacity-40"
            >
              {t('common.next', '下一页')}
            </button>
          </div>
        )}
      </div>

      {/* Top-up dialog */}
      {topupOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.6)' }}
          onClick={closeTopup}
        >
          <div
            className="w-full max-w-md rounded-2xl p-6"
            style={cardStyle}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-bold text-primary mb-4">
              {t('account.balance.topupTitle', '充值积分')}
            </h3>

            {codeUrl ? (
              <div className="flex flex-col items-center gap-4">
                <p className="text-sm text-primary">
                  {t('account.balance.scanToPay', '请用微信扫码支付')}
                </p>
                <div className="bg-white p-3 rounded-lg">
                  <QRCodeSVG value={codeUrl} size={200} />
                </div>
                <p className="text-xs text-secondary text-center">
                  {t('account.balance.pollingHint',
                     '支付完成后本页会自动到账,无需刷新。手机上请长按二维码识别。')}
                </p>
                <button
                  type="button"
                  onClick={closeTopup}
                  className="text-sm text-secondary"
                >
                  {t('common.cancel', '取消')}
                </button>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-4 gap-2 mb-4">
                  {PRESETS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => setAmountYuan(String(c / 100))}
                      className="py-2 rounded-lg text-sm font-medium transition-colors"
                      style={{
                        border: `1px solid ${
                          Math.round(Number(amountYuan) * 100) === c
                            ? 'var(--accent-primary)' : 'var(--border-color)'
                        }`,
                        color: 'var(--text-primary)',
                      }}
                    >
                      ¥{c / 100}
                    </button>
                  ))}
                </div>

                <label className="block text-xs text-secondary mb-1">
                  {t('account.balance.customAmount', '自定义金额(元,1 元 = 100 积分)')}
                </label>
                <input
                  type="number"
                  value={amountYuan}
                  min={bal ? bal.topup_min_cents / 100 : 10}
                  max={bal ? bal.topup_max_cents / 100 : 5000}
                  onChange={(e) => setAmountYuan(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg text-primary mb-1"
                  style={{ background: 'var(--bg-input, transparent)', border: '1px solid var(--border-color)' }}
                />
                {bal && (
                  <p className="text-xs text-secondary mb-4">
                    {yuan(bal.topup_min_cents)} 起充,单笔最高 {yuan(bal.topup_max_cents)}
                    {' · '}支付 ¥1 到账 100 积分
                  </p>
                )}

                <div className="flex gap-3">
                  <button
                    type="button"
                    onClick={closeTopup}
                    className="flex-1 py-2.5 rounded-lg text-sm"
                    style={{ border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}
                  >
                    {t('common.cancel', '取消')}
                  </button>
                  <button
                    type="button"
                    disabled={creating}
                    onClick={startTopup}
                    className="flex-1 py-2.5 rounded-lg text-sm font-semibold text-white disabled:opacity-50"
                    style={{ background: 'var(--accent-primary)' }}
                  >
                    {creating
                      ? t('common.loading', '处理中…')
                      : t('account.balance.getQr', '生成付款码')}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default BalanceTab;
