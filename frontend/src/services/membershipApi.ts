// Define types locally to avoid import issues
export type TierType = 'saas' | 'service';

export interface Membership {
  id: number;
  slug: string;
  name: string;
  price: number;
  period: string;
  description: string;
  popular: boolean;
  features: string[];
  not_included: string[];
  tier_type: TierType;
  monthly_check_quota: number; // 0 = unlimited
  allowed_check_categories: string[] | null; // null = all 23
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

export interface MembershipUpgradeResponse extends UserMembership {}

export interface UsageResponse {
  quota: number;       // 0 = unlimited
  used: number;
  remaining: number;   // -1 = unlimited
  year_month: string;
}

export interface ContactSalesPayload {
  name: string;
  email: string;
  website?: string;
  tier_slug?: string;
  message?: string;
}

export interface SubscribeResponse {
  status: 'pending' | 'active';
  message: string;
  tier_slug: string;
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

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    return body?.detail || body?.message || fallback;
  } catch {
    return fallback;
  }
}

class MembershipApi {
  private baseUrl: string;

  constructor() {
    this.baseUrl = '/api';
  }

  async getMemberships(): Promise<Membership[]> {
    const response = await fetch(`${this.baseUrl}/memberships`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Failed to get memberships');
    }
    return response.json();
  }

  async getUserMembership(token: string): Promise<UserMembership> {
    const response = await fetch(`${this.baseUrl}/user-membership`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        await readErrorMessage(response, 'Failed to get user membership'),
      );
    }
    return response.json();
  }

  async upgradeMembership(token: string, newMembershipId: number): Promise<MembershipUpgradeResponse> {
    const response = await fetch(`${this.baseUrl}/upgrade-membership`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ new_membership_id: newMembershipId }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Failed to upgrade membership');
    }
    return response.json();
  }

  async cancelMembership(token: string): Promise<{ message: string }> {
    const response = await fetch(`${this.baseUrl}/cancel-membership`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Failed to cancel membership');
    }
    return response.json();
  }

  async getUsage(token: string): Promise<UsageResponse> {
    const response = await fetch(`${this.baseUrl}/users/me/usage`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Failed to get usage');
    }
    return response.json();
  }

  async submitContactForm(payload: ContactSalesPayload): Promise<{ message: string; lead_id: number }> {
    const response = await fetch(`${this.baseUrl}/contact-sales`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Failed to submit contact form');
    }
    return response.json();
  }

  async subscribe(token: string, slug: string): Promise<SubscribeResponse> {
    const response = await fetch(`${this.baseUrl}/subscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ slug }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Failed to subscribe');
    }
    return response.json();
  }
}

export const membershipApi = new MembershipApi();
