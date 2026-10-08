import { readApiError, localizedHeaders } from './apiError';

const API_BASE = '/api';

export const paymentApi = {
  async createWechatPaySession(
    token: string,
    slug: string,
    scene: 'native' | 'h5' = 'native',
    redirectUrl?: string,
  ): Promise<WechatPayCreateResponse> {
    const response = await fetch(`${API_BASE}/payment/wechat/create`, {
      method: 'POST',
      headers: localizedHeaders({
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      }),
      body: JSON.stringify({ slug, scene, redirect_url: redirectUrl }),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to create WeChat Pay order'));
    }
    return response.json();
  },

  async getWechatJsapiConfig(
    redirectUri: string,
    state = '',
  ): Promise<WechatJsapiConfigResponse> {
    const qs = new URLSearchParams({ redirect_uri: redirectUri, state });
    const response = await fetch(`${API_BASE}/payment/wechat/jsapi/config?${qs}`, {
      headers: localizedHeaders({}),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to read WeChat config'));
    }
    return response.json();
  },

  async createWechatJsapiSession(
    token: string,
    slug: string,
    code: string,
  ): Promise<WechatJsapiCreateResponse> {
    const response = await fetch(`${API_BASE}/payment/wechat/jsapi/create`, {
      method: 'POST',
      headers: localizedHeaders({
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      }),
      body: JSON.stringify({ slug, code }),
    });
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to create WeChat payment'));
    }
    return response.json();
  },

  async getWechatPayStatus(
    token: string,
    paymentId: number,
  ): Promise<WechatPayStatusResponse> {
    const response = await fetch(
      `${API_BASE}/payment/wechat/status/${paymentId}`,
      {
        headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      },
    );
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to check WeChat payment status'));
    }
    return response.json();
  },

  async cancelWechatPaySession(
    token: string,
    paymentId: number,
  ): Promise<void> {
    const response = await fetch(
      `${API_BASE}/payment/wechat/cancel/${paymentId}`,
      {
        method: 'POST',
        headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      },
    );
    if (!response.ok) {
      throw new Error(await readApiError(response, 'Failed to cancel WeChat payment'));
    }
  },

};
export interface WechatPayCreateResponse {
  payment_id: number;
  /** Native QR order: the QR code content. Not returned when scene='h5' and H5 is available. */
  code_url?: string;
  /** H5 order: the URL to redirect to that launches the WeChat app. Returned only when scene='h5' and the merchant has H5 payment enabled. */
  h5_url?: string;
  /** H5 was requested but the merchant hasn't enabled it, so the backend fell back to a QR order -- the frontend switches guidance text accordingly. */
  fallback?: 'h5_not_enabled';
  amount_cny: number;
  out_trade_no: string;
}

export interface WechatJsapiConfigResponse {
  /** false = the backend hasn't configured the official account AppSecret, JSAPI can't work, and the frontend should fall back to the QR code */
  configured: boolean;
  /** Official account web authorization URL; send the user there to exchange for a code */
  oauth_url: string;
}

/** The set of signed params required by WeixinJSBridge.invoke('getBrandWCPayRequest', ...) */
export interface WechatJsapiPayParams {
  appId: string;
  timeStamp: string;
  nonceStr: string;
  package: string;
  signType: string;
  paySign: string;
}

export interface WechatJsapiCreateResponse {
  payment_id: number;
  amount_cny: number;
  out_trade_no: string;
  /** Returned when JSAPI works normally; absent when falling back to a QR order */
  pay_params?: WechatJsapiPayParams;
  /** QR code content, returned only on fallback */
  code_url?: string;
  fallback?: 'jsapi_not_configured';
}

export interface WechatPayStatusResponse {
  payment_id: number;
  status: string;
  completed_at: string | null;
}
