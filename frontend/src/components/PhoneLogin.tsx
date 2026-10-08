import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { phoneAuthApi } from '../services/phoneAuthApi';
import { wechatAuthApi } from '../services/wechatAuthApi';
import { DEFAULT_PHONE_COUNTRY, PHONE_COUNTRIES, localNumberValid, toApiPhone } from './phoneCountries';

// When WeChat silent login identifies a "new user (has openid, no phone bound)", WechatSilentAuth stores the binding
// credential here. If present, go through WeChat binding -- linking the openid to the phone number so the next silent login is zero-click;
// if absent, it's a regular phone login/registration. **Without linking the openid, they'd have to rebind every time; see h5Session.**
function wxBindToken(): string {
  try { return sessionStorage.getItem('wx_bind_token') || ''; } catch { return ''; }
}

interface Props {
  /** After getting the JWT, the parent component writes AuthContext + navigates */
  onAuthenticated: (accessToken: string) => void | Promise<void>;
  compact?: boolean;
}

/**
 * Phone number + SMS verification code login/registration.
 *
 * There's only one entry: if the number isn't registered, the backend creates the account on the spot. So the copy always says "log in/register",
 * without making users first decide which one they are.
 */
export function PhoneLogin({ onAuthenticated, compact = false }: Props) {
  const { t } = useTranslation();
  // Country/area code (selectable like the Qwen login page, default +86). phone stores the **local number** without the area code;
  // the form sent to the backend is decided by toApiPhone: bare 11 digits for China, E.164 for other countries.
  const [country, setCountry] = useState(DEFAULT_PHONE_COUNTRY);
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [countdown, setCountdown] = useState(0);
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [sentHint, setSentHint] = useState(false);

  useEffect(() => {
    if (countdown <= 0) return;
    const id = window.setTimeout(() => setCountdown((n) => n - 1), 1000);
    return () => window.clearTimeout(id);
  }, [countdown]);

  const phoneValid = localNumberValid(country, phone);
  const apiPhone = toApiPhone(country, phone);

  const handleSend = async () => {
    setError('');
    setSending(true);
    try {
      const bt = wxBindToken();
      const res = bt
        ? await wechatAuthApi.sendSmsCode(bt, apiPhone)
        : await phoneAuthApi.sendLoginCode(apiPhone);
      setCountdown(res.resend_after || 60);
      setSentHint(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('phoneLogin.sendFailed'));
    } finally {
      setSending(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const bt = wxBindToken();
      const res = bt
        ? await wechatAuthApi.bindPhone(bt, apiPhone, code)   // link the openid
        : await phoneAuthApi.login(apiPhone, code);
      if (bt) { try { sessionStorage.removeItem('wx_bind_token'); } catch { /* noop */ } }
      await onAuthenticated(res.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('phoneLogin.failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={compact ? '' : 'py-1'}>
      {error && (
        <div className="mb-4 bg-danger-bg border border-danger-border text-danger-fg p-3 rounded-md text-sm">
          {error}
        </div>
      )}

      <form className="space-y-4" onSubmit={handleSubmit}>
        <div>
          <label htmlFor="pl-phone" className="block text-sm font-medium text-secondary mb-1">
            {t('phoneLogin.phone')}
          </label>
          <div className="flex gap-2">
            <select
              aria-label={t('phoneLogin.country', { defaultValue: '国家/地区' })}
              value={country.code}
              onChange={(e) => {
                const next = PHONE_COUNTRIES.find((c) => c.code === e.target.value) || DEFAULT_PHONE_COUNTRY;
                setCountry(next);
                setPhone((p) => p.slice(0, next.len[1]));
              }}
              className="shrink-0 px-2 py-3 rounded-lg bg-card text-primary focus:outline-none transition-colors duration-200"
              style={{ maxWidth: 150 }}
            >
              {PHONE_COUNTRIES.map((c) => (
                <option key={c.code} value={c.code}>{c.flag} {c.name} +{c.code}</option>
              ))}
            </select>
            <input
              id="pl-phone"
              type="tel"
              inputMode="numeric"
              autoComplete="tel-national"
              maxLength={country.len[1]}
              required
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ''))}
              className="flex-1 min-w-0 px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
              placeholder={country.code === '86'
                ? t('phoneLogin.phonePlaceholder')
                : t('phoneLogin.phonePlaceholderIntl', { defaultValue: '请输入手机号(不含区号)' })}
            />
          </div>
        </div>

        <div>
          <label htmlFor="pl-code" className="block text-sm font-medium text-secondary mb-1">
            {t('phoneLogin.code')}
          </label>
          <div className="flex gap-2">
            <input
              id="pl-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              className="flex-1 min-w-0 px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
              placeholder={t('phoneLogin.codePlaceholder')}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={!phoneValid || countdown > 0 || sending}
              className="whitespace-nowrap px-4 py-3 rounded-lg text-sm font-medium border border-border text-accent-primary hover:text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {countdown > 0
                ? t('phoneLogin.resendIn', { seconds: countdown })
                : sending
                  ? t('phoneLogin.sending')
                  : t('phoneLogin.sendCode')}
            </button>
          </div>
          {sentHint && countdown > 0 && (
            <p className="mt-2 text-xs text-secondary">{t('phoneLogin.sentHint')}</p>
          )}
        </div>

        <button
          type="submit"
          disabled={submitting || !phoneValid || code.length < 4}
          className="btn-primary w-full justify-center !py-3 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting ? t('phoneLogin.submitting') : t('phoneLogin.submit')}
        </button>

        <p className="text-xs text-muted text-center">{t('phoneLogin.newUserHint')}</p>
      </form>
    </div>
  );
}
