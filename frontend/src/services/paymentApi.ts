import { readApiError, localizedHeaders } from './apiError';

const API_BASE = '/api';

export interface CreateCheckoutSessionResponse {
  session_id: string;
  checkout_url: string;
}

export interface StripeSessionStatus {
  session_id: string;
  payment_status: string | null;
  status: 'pending' | 'paid' | 'expired' | 'failed' | 'unknown';
  membership_id: number | null;
  fulfilled_now: boolean;
}

export const paymentApi = {
  async createStripeCheckoutSession(
    token: string,
    slug: string,
    locale: string = 'en',
  ): Promise<CreateCheckoutSessionResponse> {
    const response = await fetch(`${API_BASE}/payment/stripe/create-checkout-session`, {
      method: 'POST',
      headers: localizedHeaders({
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      }),
      body: JSON.stringify({ slug, locale }),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to create checkout session'));
    }
    return response.json();
  },

  async getStripeSessionStatus(
    token: string,
    sessionId: string,
  ): Promise<StripeSessionStatus> {
    const response = await fetch(
      `${API_BASE}/payment/stripe/session/${encodeURIComponent(sessionId)}`,
      {
        headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      },
    );
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to read checkout status'));
    }
    return response.json();
  },
};

/**
 * All SaaS-tier subscriptions are currently routed through Stripe Checkout
 * regardless of UI language. Domestic providers (Alipay / WeChat Pay) are
 * not yet integrated, so the legacy English-only gating has been removed.
 */
export function shouldUseStripe(_lang: string | undefined): boolean {
  return true;
}
