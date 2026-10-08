import { readApiError, localizedHeaders } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface WechatAuthConfig {
  wechat_login_enabled: boolean;
  sms_enabled: boolean;
  /** Phone verification code login. True only when the SMS channel is actually available */
  phone_login_enabled: boolean;
  email_register_enabled: boolean;
  /** H5 single-device channel (on-site promotion landing page). False when the landing page base URL isn't configured, in which case **don't redirect to authorization** */
  wechat_h5_enabled?: boolean;
}

/**
 * User identification result of H5 silent authorization.
 *
 * subscribed is tri-state: true = following / false = not following / null = **couldn't fetch**.
 * null doesn't mean not following -- the backend checks follow status with the global access_token, which is subject to the official account's IP allowlist,
 * so it's null when the deployment machine isn't allowlisted. **Don't show a "you're not following yet" hint for null**,
 * otherwise it misreports to a room full of people who are already following.
 */
export interface WechatH5Session {
  status: 'authorized' | 'need_phone';
  access_token?: string;
  bind_token?: string;
  subscribed: boolean | null;
}

export interface WechatQrcode {
  scene: string;
  qr_url: string;
  expires_in: number;
}

/** Polling result. Carries bind_token when need_phone, access_token when confirmed. */
export interface WechatQrStatus {
  status: 'pending' | 'need_phone' | 'confirmed' | 'expired' | 'consumed';
  bind_token?: string;
  access_token?: string;
  token_type?: string;
}

class WechatAuthApi {
  private baseUrl = `${API_BASE}/auth`;

  /** Channel switches. If unavailable, treat as off -- better to show only email login than render a QR box that's bound to fail. */
  async config(): Promise<WechatAuthConfig> {
    try {
      const res = await fetch(`${this.baseUrl}/wechat/config`, {
        headers: localizedHeaders(),
      });
      if (!res.ok) throw new Error('config unavailable');
      return res.json();
    } catch {
      return {
        wechat_login_enabled: false,
        sms_enabled: false,
        phone_login_enabled: false,
        email_register_enabled: false,
        wechat_h5_enabled: false,
      };
    }
  }

  /**
   * Get the silent authorization redirect URL.
   *
   * redirect_uri is assembled by the **backend** (preventing open redirects); the frontend can only pass params back via state.
   * **Don't redirect** when enabled=false -- redirecting to authorization in a browser outside WeChat shows users WeChat's
   * "please open the link in the WeChat client" error page, effectively a blank screen.
   */
  async h5Authorize(path = '', state = ''): Promise<{ enabled: boolean; oauth_url: string }> {
    try {
      const qs = `path=${encodeURIComponent(path)}&state=${encodeURIComponent(state)}`;
      const res = await fetch(
        `${this.baseUrl}/wechat/h5/authorize?${qs}`,
        { headers: localizedHeaders() },
      );
      if (!res.ok) throw new Error('authorize unavailable');
      return res.json();
    } catch {
      // If unavailable, treat as off: fall back to phone verification code login rather than sending users into a redirect bound to fail
      return { enabled: false, oauth_url: '' };
    }
  }

  /** Exchange the web authorization code for an identity. A code can only be used once, and the frontend must erase it from the address bar afterwards. */
  async h5Session(code: string): Promise<WechatH5Session> {
    const res = await fetch(`${this.baseUrl}/wechat/h5/session`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ code }),
    });
    if (!res.ok) throw new Error(await readApiError(res, '微信授权失败'));
    return res.json();
  }

  async createQrcode(): Promise<WechatQrcode> {
    const res = await fetch(`${this.baseUrl}/wechat/qrcode`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: '{}',
    });
    if (!res.ok) throw new Error(await readApiError(res, '获取登录二维码失败'));
    return res.json();
  }

  async pollQrcode(scene: string): Promise<WechatQrStatus> {
    const res = await fetch(`${this.baseUrl}/wechat/qrcode/${encodeURIComponent(scene)}`, {
      headers: localizedHeaders(),
    });
    if (res.status === 404) return { status: 'expired' };
    if (!res.ok) throw new Error(await readApiError(res, '登录状态查询失败'));
    return res.json();
  }

  async sendSmsCode(bindToken: string, phone: string): Promise<{ resend_after: number }> {
    const res = await fetch(`${this.baseUrl}/sms/send`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ bind_token: bindToken, phone }),
    });
    if (!res.ok) throw new Error(await readApiError(res, '验证码发送失败'));
    return res.json();
  }

  async bindPhone(bindToken: string, phone: string, code: string): Promise<{ access_token: string }> {
    const res = await fetch(`${this.baseUrl}/wechat/bind-phone`, {
      method: 'POST',
      headers: localizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ bind_token: bindToken, phone, code }),
    });
    if (!res.ok) throw new Error(await readApiError(res, '绑定手机号失败'));
    return res.json();
  }
}

export const wechatAuthApi = new WechatAuthApi();
