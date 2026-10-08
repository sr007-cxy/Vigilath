import type { StoredUser } from '../contexts/AuthContext';

/**
 * Display rules for account identity.
 *
 * A separate module instead of AuthContext: that file exports a Provider component, and adding
 * non-component exports would trigger react-refresh/only-export-components (two cases already exist in the repo).
 */

/**
 * Accounts registered via WeChat / phone number have no real email - users.email is NOT NULL + UNIQUE,
 * so a placeholder address is generated (backend user_service.WECHAT_EMAIL_DOMAIN).
 * This domain **must never appear in the UI**: users would think they have an email that does not exist,
 * and would use it for "Forgot password" (that subdomain receives no mail, so it vanishes without a trace).
 */
const PLACEHOLDER_EMAIL_DOMAIN = '@wx.vigilath.com';

export function isPlaceholderEmail(email?: string | null): boolean {
  return !!email && email.toLowerCase().endsWith(PLACEHOLDER_EMAIL_DOMAIN);
}

/** Masked phone number: 138****4521. Enough to recognize the account without exposing it fully to onlookers. */
export function maskPhone(phone?: string | null): string {
  if (!phone) return '';
  const d = phone.replace(/\D/g, '');
  return d.length >= 11 ? `${d.slice(0, 3)}****${d.slice(-4)}` : d;
}

/** Account name in the UI: WeChat nickname -> name -> masked phone -> real email. Placeholder emails are never shown. */
export function displayName(u: StoredUser | null | undefined): string {
  if (!u) return '';
  const nick = (u.wechat_nickname || u.name || '').trim();
  if (nick) return nick;
  if (u.phone) return maskPhone(u.phone);
  if (u.email && !isPlaceholderEmail(u.email)) return u.email;
  return '我的账号';
}
