import { readApiError, localizedHeaders } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

// Define types locally to avoid import issues
interface User {
  id: number;
  email: string;
  name?: string | null;
  is_active: boolean;
  is_admin?: boolean;
  role?: string;
  /** Has a value for WeChat/phone accounts; UI display identity goes through AuthContext.displayName() */
  phone?: string | null;
  wechat_nickname?: string | null;
}

interface LoginResponse {
  access_token: string;
  token_type: string;
}

interface RegisterResponse {
  id: number;
  email: string;
  name?: string | null;
  is_active: boolean;
}

interface ForgotPasswordResponse {
  message: string;
  reset_token?: string;
}

interface ResetPasswordResponse {
  message: string;
}

class AuthApi {
  private baseUrl: string;

  constructor() {
    this.baseUrl = `${API_BASE}/auth`;
  }

  async login(email: string, password: string): Promise<LoginResponse> {
    email = email.trim();
    const response = await fetch(`${this.baseUrl}/login`, {
      method: 'POST',
      headers: localizedHeaders({
        'Content-Type': 'application/x-www-form-urlencoded',
      }),
      body: new URLSearchParams({
        username: email,
        password: password,
      }),
    });

    if (!response.ok) {
      throw new Error(await readApiError(response, 'Login failed'));
    }

    return response.json();
  }

  /**
   * Create account. Public email sign-up is closed (backend EMAIL_REGISTER_ENABLED=false), so anonymous calls always
   * get 403; creating accounts on behalf of clients in the workbench must carry an admin token, which the backend uses to allow it.
   */
  async register(email: string, password: string, name?: string, token?: string): Promise<RegisterResponse> {
    email = email.trim();   // Copy-pasting often brings spaces, which the backend EmailStr rejects
    const response = await fetch(`${this.baseUrl}/register`, {
      method: 'POST',
      headers: localizedHeaders(
        token
          ? { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
          : { 'Content-Type': 'application/json' },
      ),
      body: JSON.stringify(name ? { email, password, name } : { email, password }),
    });

    if (!response.ok) {
      throw new Error(await readApiError(response, 'Registration failed'));
    }

    return response.json();
  }

  async getCurrentUser(token: string): Promise<User> {
    const response = await fetch(`${this.baseUrl}/me`, {
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
    });

    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to get user information'));
    }

    return response.json();
  }

  async forgotPassword(email: string): Promise<ForgotPasswordResponse> {
    const response = await fetch(`${this.baseUrl}/forgot-password`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ email }),
    });

    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to send password reset email'));
    }

    return response.json();
  }

  async resetPassword(token: string, newPassword: string): Promise<ResetPasswordResponse> {
    const response = await fetch(`${this.baseUrl}/reset-password`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ token, new_password: newPassword }),
    });

    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to reset password'));
    }

    return response.json();
  }
}

export const authApi = new AuthApi();
