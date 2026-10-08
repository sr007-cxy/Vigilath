import { localizedHeaders, readApiError } from './apiError';

export interface SafeUser {
  id: number;
  masked_email: string | null;
  masked_phone: string | null;
  display_name: string | null;
}

export interface AgencyOption { id: number; name: string; status: 'active' | 'disabled' }
export interface AgencyOrganization extends AgencyOption {
  organization_type: 'agency';
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
  created_at?: string;
  updated_at?: string;
}
export interface AgencyMember {
  id: number;
  organization_id: number;
  user: SafeUser;
  member_role: 'admin' | 'operator';
  status: 'active' | 'removed';
  joined_at: string;
  removed_at?: string | null;
}
export interface AgencyEnterprise {
  grant_id: number;
  enterprise: SafeUser;
  status: 'active' | 'revoked';
}
export interface AgencyGrant {
  id: number;
  organization_id: number;
  enterprise: SafeUser;
  status: 'active' | 'revoked';
  granted_at: string;
  revoked_at?: string | null;
}
export interface AgencyAssignment {
  id: number;
  member_id: number;
  grant_id: number;
  status: 'active' | 'revoked';
  assigned_at?: string;
}
export interface AgencyMe {
  organization: AgencyOrganization;
  member: { id: number; member_role: 'admin' | 'operator'; status: string };
  capabilities: {
    manage_members: boolean;
    manage_assignments: boolean;
    access_all_grants: boolean;
  };
}
export interface AgencyAuditEvent {
  id: number;
  event_type: string;
  actor_user_id: number | null;
  enterprise_user_id: number | null;
  action: string;
  object_type: string;
  object_id: string | null;
  result: string;
  reason?: string | null;
  created_at: string;
}

type AgencyRequestInit = Omit<RequestInit, 'headers'> & {
  headers?: Record<string, string>;
};

async function request<T>(path: string, token: string, init: AgencyRequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init.headers || {}),
    }),
  });
  if (!response.ok) throw new Error(await readApiError(response, '代理商请求失败'));
  return response.json() as Promise<T>;
}

const json = (method: string, body?: unknown): AgencyRequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export const agencyApi = {
  me: (token: string) => request<AgencyMe>('/agency/me', token),
  enterprises: (token: string) => request<AgencyEnterprise[]>('/agency/enterprises', token),
  members: (token: string) => request<AgencyMember[]>('/agency/members', token),
  addOperator: (payload: { email?: string; phone?: string }, token: string) =>
    request<AgencyMember>('/agency/members', token, json('POST', payload)),
  removeMember: (memberId: number, token: string) =>
    request<{ id: number; status: string }>(`/agency/members/${memberId}`, token, json('DELETE')),
  assignments: (token: string) => request<AgencyAssignment[]>('/agency/assignments', token),
  assign: (memberId: number, grantId: number, token: string) =>
    request<AgencyAssignment>('/agency/assignments', token, json('POST', { member_id: memberId, grant_id: grantId })),
  revokeAssignment: (assignmentId: number, token: string) =>
    request<{ id: number; status: string }>(`/agency/assignments/${assignmentId}`, token, json('DELETE')),
  accountAgency: (token: string) => request<{
    agency: { id: number; name: string; status: string } | null;
    grant: { id: number; status: string; granted_at: string } | null;
  }>('/account/agency', token),

  options: (token: string) => request<AgencyOption[]>('/admin/agencies/options', token),
  listOrganizations: (token: string) => request<AgencyOrganization[]>('/admin/agencies', token),
  createOrganization: (payload: {
    name: string; first_admin_user_id: number; contact_name?: string;
    contact_email?: string; contact_phone?: string;
  }, token: string) => request<AgencyOrganization>('/admin/agencies', token, json('POST', payload)),
  setStatus: (organizationId: number, status: 'active' | 'disabled', token: string) =>
    request<AgencyOrganization>(`/admin/agencies/${organizationId}/status`, token, json('PATCH', { status })),
  platformMembers: (organizationId: number, token: string) =>
    request<AgencyMember[]>(`/admin/agencies/${organizationId}/members`, token),
  addPlatformMember: (organizationId: number, userId: number, memberRole: 'admin' | 'operator', token: string) =>
    request<AgencyMember>(`/admin/agencies/${organizationId}/members`, token, json('POST', {
      user_id: userId, member_role: memberRole,
    })),
  removePlatformMember: (organizationId: number, memberId: number, token: string) =>
    request<{ id: number; status: string }>(`/admin/agencies/${organizationId}/members/${memberId}`, token, json('DELETE')),
  grants: (organizationId: number, token: string) =>
    request<AgencyGrant[]>(`/admin/agencies/${organizationId}/grants`, token),
  eligible: (organizationId: number, identifier: { email?: string; phone?: string }, token: string) => {
    const params = new URLSearchParams();
    if (identifier.email) params.set('email', identifier.email);
    if (identifier.phone) params.set('phone', identifier.phone);
    return request<SafeUser[]>(`/admin/agencies/${organizationId}/eligible-enterprises?${params}`, token);
  },
  grant: (organizationId: number, enterpriseUserId: number, token: string) =>
    request<{ id: number; status: string }>(`/admin/agencies/${organizationId}/grants`, token,
      json('POST', { enterprise_user_id: enterpriseUserId })),
  revokeGrant: (organizationId: number, grantId: number, token: string) =>
    request<{ id: number; status: string }>(`/admin/agencies/${organizationId}/grants/${grantId}`, token, json('DELETE')),
  audit: (organizationId: number, token: string) =>
    request<AgencyAuditEvent[]>(`/admin/agencies/${organizationId}/audit-logs`, token),
};
