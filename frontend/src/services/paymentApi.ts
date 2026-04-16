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

  async createMoltsPaySession(
    token: string,
    slug: string,
  ): Promise<MoltsPayCreateResponse> {
    const response = await fetch(`${API_BASE}/payment/moltspay/create`, {
      method: 'POST',
      headers: localizedHeaders({
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      }),
      body: JSON.stringify({ slug }),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to create USDC payment'));
    }
    return response.json();
  },

  async getMoltsPayStatus(
    token: string,
    paymentId: number,
  ): Promise<MoltsPayStatusResponse> {
    const response = await fetch(
      `${API_BASE}/payment/moltspay/status/${paymentId}`,
      {
        headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      },
    );
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to check USDC payment status'));
    }
    return response.json();
  },
};

export interface MoltsPayCreateResponse {
  payment_id: number;
  amount_usdc: number;
  chain: string;
  service_id: string;
  wallet_address: string;
  status: string;
}

export interface MoltsPayStatusResponse {
  payment_id: number;
  status: string;
  tx_hash: string | null;
  completed_at: string | null;
}
