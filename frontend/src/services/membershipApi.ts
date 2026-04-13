// Define types locally to avoid import issues
interface Membership {
  id: number;
  name: string;
  price: number;
  period: string;
  description: string;
  popular: boolean;
  features: string[];
  not_included: string[];
  created_at: string;
  updated_at: string;
}

interface UserMembership {
  id: number;
  user_id: number;
  membership_id: number;
  start_date: string;
  end_date: string;
  is_active: boolean;
}

interface MembershipUpgradeResponse {
  id: number;
  user_id: number;
  membership_id: number;
  start_date: string;
  end_date: string;
  is_active: boolean;
}

class MembershipApi {
  private baseUrl: string;

  constructor() {
    this.baseUrl = 'http://localhost:8002/api';
  }

  async getMemberships(): Promise<Membership[]> {
    const response = await fetch(`${this.baseUrl}/memberships`);

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to get memberships');
    }

    return response.json();
  }

  async getUserMembership(token: string): Promise<UserMembership> {
    const response = await fetch(`${this.baseUrl}/user-membership`, {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to get user membership');
    }

    return response.json();
  }

  async upgradeMembership(token: string, newMembershipId: number): Promise<MembershipUpgradeResponse> {
    // 从token中获取用户信息或从本地存储中获取
    // 简化处理，后端会从token中获取用户ID
    const response = await fetch(`${this.baseUrl}/upgrade-membership`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ new_membership_id: newMembershipId }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to upgrade membership');
    }

    return response.json();
  }

  async cancelMembership(token: string): Promise<{ message: string }> {
    const response = await fetch(`${this.baseUrl}/cancel-membership`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to cancel membership');
    }

    return response.json();
  }
}

export const membershipApi = new MembershipApi();