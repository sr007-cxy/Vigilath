import { readApiError, localizedHeaders } from './apiError';

const API_BASE = '/api';

export interface BalanceOut {
  balance_cents: number;
  granted_cents: number;
  recharged_cents: number;
  spent_cents: number;
  price_batch_cell_cents: number;
  price_content_doc_cents: number;
  price_sentiment_once_cents: number;
  price_sentiment_daily_cents: number;
  /** Per GEO strategy optimization plan */
  price_solution_cents: number;
  topup_min_cents: number;
  topup_max_cents: number;
  /** Billing start date for monitoring batch runs (Beijing date). null = still in the shadow period, not billed yet. */
  batch_billing_start_date: string | null;
}

export interface LedgerItem {
  id: number;
  created_at: string;
  direction: 'credit' | 'debit';
  kind: string;
  amount_cents: number;
  balance_after_cents: number;
  topic_id: number | null;
  note: Record<string, unknown> | null;
}

export interface LedgerList {
  items: LedgerItem[];
  total: number;
  page: number;
  size: number;
}

export interface TopupCreated {
  payment_id: number;
  code_url: string;
  amount_cny: number;
  out_trade_no: string;
  purpose: string;
}

function auth(token: string) {
  return localizedHeaders({
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  });
}

export const balanceApi = {
  async get(token: string): Promise<BalanceOut> {
    const r = await fetch(`${API_BASE}/balance`, { headers: auth(token) });
    if (!r.ok) throw new Error(await readApiError(r, '读取余额失败'));
    return r.json();
  },

  async ledger(token: string, page = 1, size = 20): Promise<LedgerList> {
    const qs = new URLSearchParams({ page: String(page), size: String(size) });
    const r = await fetch(`${API_BASE}/balance/ledger?${qs}`, { headers: auth(token) });
    if (!r.ok) throw new Error(await readApiError(r, '读取流水失败'));
    return r.json();
  },

  /** Top-ups go through WeChat QR. The callback routes credits by purpose and doesn't activate any plan. */
  async createTopup(token: string, amountCents: number): Promise<TopupCreated> {
    const r = await fetch(`${API_BASE}/payment/wechat/topup/create`, {
      method: 'POST',
      headers: auth(token),
      body: JSON.stringify({ amount_cents: amountCents }),
    });
    if (!r.ok) throw new Error(await readApiError(r, '创建充值订单失败'));
    return r.json();
  },
};

/** Real-money amounts (what's paid on top-up, min/max limits). */
export const yuan = (cents: number | null | undefined): string =>
  `¥${((cents ?? 0) / 100).toFixed(2)}`;

/** Credits display. **1 yuan = 100 credits**, and the credit amount = the DB's `*_cents` value itself (1:1, not divided by 100).
 *  So a 39.9-yuan membership = 3,990 credits. To change the conversion, change only this one place. */
export const points = (cents: number | null | undefined): string =>
  `${Math.round(cents ?? 0).toLocaleString('zh-CN')} 积分`;
