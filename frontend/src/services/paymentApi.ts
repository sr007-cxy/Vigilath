const API_BASE = 'http://localhost:8070/api';

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

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    return body?.detail || body?.message || fallback;
  } catch {
    return fallback;
  }
}

export const paymentApi = {
  async createStripeCheckoutSession(
    token: string,
    slug: string,
    locale: string = 'en',
  ): Promise<CreateCheckoutSessionResponse> {
    const response = await fetch(`${API_BASE}/payment/stripe/create-checkout-session`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ slug, locale }),
    });
    if (!response.ok) {
      throw new Error(await readError(response, 'Failed to create checkout session'));
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
        headers: { Authorization: `Bearer ${token}` },
      },
    );
    if (!response.ok) {
      throw new Error(await readError(response, 'Failed to read checkout status'));
    }
    return response.json();
  },
};

/**
 * Returns true if the current UI language should route subscriptions through
 * Stripe (i.e. the user is an English-locale / overseas visitor).
 */
export function shouldUseStripe(lang: string | undefined): boolean {
  if (!lang) return false;
  return lang.toLowerCase().startsWith('en');
}
