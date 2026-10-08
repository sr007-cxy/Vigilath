import { readApiError, localizedHeaders } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

/**
 * Phone number + SMS verification code login/sign-up.
 *
 * The backend doesn't distinguish login from sign-up: an unregistered number gets an account on the spot. So the frontend has just one entry too,
 * and must no longer give users the "should I click login or sign up" dilemma.
 */
class PhoneAuthApi {
  private baseUrl = `${API_BASE}/auth`;

  async sendLoginCode(phone: string): Promise<{ resend_after: number }> {
    const res = await fetch(`${this.baseUrl}/sms/send-login`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ phone }),
    });
    if (!res.ok) throw new Error(await readApiError(res, '验证码发送失败'));
    return res.json();
  }

  async login(phone: string, code: string): Promise<{ access_token: string }> {
    const res = await fetch(`${this.baseUrl}/phone-login`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ phone, code }),
    });
    if (!res.ok) throw new Error(await readApiError(res, '登录失败'));
    return res.json();
  }
}

export const phoneAuthApi = new PhoneAuthApi();
