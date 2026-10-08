import { readApiError, localizedHeaders } from './apiError';

// Define types locally to avoid import issues
export type TierType = 'saas' | 'service';

export interface Membership {
  id: number;
  slug: string;
  name: string;
  price: number;
  currency: string;
  period: string;
  description: string;
  popular: boolean;
  /** This tier is currently "granted on login" (marked by the backend per LOGIN_GRANTED_TIER); the product page shows "Free for a limited time" accordingly */
  login_granted?: boolean;
  features: string[];
  not_included: string[];
  tier_type: TierType;
  monthly_check_quota: number; // 0 = unlimited
  allowed_check_categories: string[] | null; // null = all 25
  features_json: Record<string, any> | null;
  display_order: number;
  created_at: string;
  updated_at: string;
}

export interface UserMembership {
  id: number;
  user_id: number;
  membership_id: number;
  start_date: string;
  end_date: string;
  is_active: boolean;
}

export interface UsageResponse {
  quota: number;       // 0 = unlimited
  used: number;
  remaining: number;   // -1 = unlimited
  year_month: string;
  /** Daily cap: the same across all three tiers, a capacity guardrail rather than a tier benefit -- monthly "unlimited" is bound by it too. 0 = unlimited. */
  daily_quota: number;
  daily_used: number;
  daily_remaining: number;  // -1 = unlimited
  bj_date: string;
}

export interface ContactSalesPayload {
  name: string;
  email: string;
  website?: string;
  tier_slug?: string;
  message?: string;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  cny: '¥',
  usd: '$',
  eur: '€',
  gbp: '£',
  jpy: '¥',
  hkd: 'HK$',
};

/** Best-effort symbol lookup; falls back to upper-cased ISO code + space. */
export function currencySymbol(currency: string | undefined): string {
  if (!currency) return '';
  const key = currency.toLowerCase();
  return CURRENCY_SYMBOLS[key] ?? `${currency.toUpperCase()} `;
}

/**
 * Render a tier price with the right currency symbol.
 *
 * - SaaS tiers: read `tier.currency` (the backend has already converted to RMB under REGION=cn).
 * - Service tiers: only the scale tier, with custom quotes -- don't read
 *   `features_json.price_range_usd` from the DB (it stores the English "Get a Demo"),
 *   output the Chinese copy directly.
 */
export function formatTierPrice(tier: Membership): string {
  if (tier.tier_type === 'service') {
    return '联系销售定制';
  }
  // Use thousand separators for whole-dollar prices (e.g. 2500 → "2,500"),
  // but preserve decimals for fractional prices (e.g. 9.99).
  const priceStr = Number.isInteger(tier.price)
    ? tier.price.toLocaleString('en-US')
    : String(tier.price);
  return `${currencySymbol(tier.currency)}${priceStr}`;
}

/**
 * How many credits this tier grants. **1 yuan = 100 credits**, grant = amount actually paid (no bonus; the margin is in the credit unit price).
 *
 * Computed only for RMB-priced tiers -- credits are denominated in RMB, and the USD site's receipts need exchange rate conversion, which only
 * the backend knows (see services/membership_billing.py); the frontend doesn't guess. Returns null = don't show this line.
 */
export function grantPoints(tier: { price: number; currency?: string }): number | null {
  if (!tier || tier.price <= 0) return null;
  if ((tier.currency || '').toLowerCase() !== 'cny') return null;
  return Math.round(tier.price * 100);
}

/**
 * Error thrown by membershipApi calls. Preserves HTTP status so callers
 * (notably useMembership) can distinguish an expired token (401) from a
 * generic network/parse failure.
 */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

class MembershipApi {
  private baseUrl: string;

  constructor() {
    this.baseUrl = '/api';
  }

  async getMemberships(): Promise<Membership[]> {
    const response = await fetch(`${this.baseUrl}/memberships`, {
      headers: localizedHeaders(),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to get memberships'));
    }
    return response.json();
  }

  async getUserMembership(token: string): Promise<UserMembership> {
    const response = await fetch(`${this.baseUrl}/user-membership`, {
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        await readApiError(response, 'Failed to get user membership'),
      );
    }
    return response.json();
  }

  async cancelMembership(token: string): Promise<{ message: string }> {
    const response = await fetch(`${this.baseUrl}/cancel-membership`, {
      method: 'POST',
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to cancel membership'));
    }
    return response.json();
  }

  async getUsage(token: string): Promise<UsageResponse> {
    const response = await fetch(`${this.baseUrl}/users/me/usage`, {
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to get usage'));
    }
    return response.json();
  }

  async submitContactForm(payload: ContactSalesPayload): Promise<{ message: string; lead_id: number }> {
    const response = await fetch(`${this.baseUrl}/contact-sales`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to submit contact form'));
    }
    return response.json();
  }

}

export const membershipApi = new MembershipApi();
