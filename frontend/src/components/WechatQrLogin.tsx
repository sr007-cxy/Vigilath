import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { wechatAuthApi, type WechatQrStatus } from '../services/wechatAuthApi';

interface Props {
  /** After getting the JWT, the parent component writes AuthContext + navigates */
  onAuthenticated: (accessToken: string) => void | Promise<void>;
  /** Compact mode: used in dialogs, removes outer padding */
  compact?: boolean;
}

const POLL_INTERVAL_MS = 1500;

/**
 * WeChat QR login panel.
 *
 * Two stages: (1) show code + poll (2) (new users) bind phone number.
 * People who haven't followed the official account only see the follow page when scanning, and the backend receives no event -- so the "follow" step
 * needs no extra frontend validation; the hint copy just needs to make it clear.
 */
export function WechatQrLogin({ onAuthenticated, compact = false }: Props) {
  const { t } = useTranslation();
  const [qrUrl, setQrUrl] = useState('');
  const [scene, setScene] = useState('');
  const [status, setStatus] = useState<WechatQrStatus['status'] | 'loading' | 'error'>('loading');
  const [error, setError] = useState('');

  // Phone binding stage
  const [bindToken, setBindToken] = useState('');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [countdown, setCountdown] = useState(0);
  const [sending, setSending] = useState(false);
  const [binding, setBinding] = useState(false);

  const timerRef = useRef<number | null>(null);
  // After the component unmounts, polling callbacks may still be in flight; use a ref to block setState on the unmounted component
  const aliveRef = useRef(true);
  // Callbacks go in refs: if the parent doesn't wrap them in useCallback (AuthModal doesn't), putting them in the dependency
  // array would rebuild the polling effect on every render, repeatedly clearing setTimeout so it never fires.
  const onAuthRef = useRef(onAuthenticated);
  onAuthRef.current = onAuthenticated;

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const loadQrcode = useCallback(async () => {
    stopPolling();
    setStatus('loading');
    setError('');
    setBindToken('');
    setCode('');
    try {
      const qr = await wechatAuthApi.createQrcode();
      if (!aliveRef.current) return;
      setQrUrl(qr.qr_url);
      setScene(qr.scene);
      setStatus('pending');
    } catch (err) {
      if (!aliveRef.current) return;
      setStatus('error');
      setError(err instanceof Error ? err.message : t('wechatLogin.qrFailed'));
    }
  }, [stopPolling, t]);

  useEffect(() => {
    aliveRef.current = true;
    void loadQrcode();
    return () => {
      aliveRef.current = false;
      stopPolling();
    };
    // loadQrcode only depends on stable references; running once on first mount is enough
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll QR scan status
  useEffect(() => {
    if (status !== 'pending' || !scene) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const res = await wechatAuthApi.pollQrcode(scene);
        if (cancelled || !aliveRef.current) return;
        if (res.status === 'confirmed' && res.access_token) {
          setStatus('confirmed');
          await onAuthRef.current(res.access_token);
          return;
        }
        if (res.status === 'need_phone' && res.bind_token) {
          setBindToken(res.bind_token);
          setStatus('need_phone');
          return;
        }
        if (res.status === 'expired' || res.status === 'consumed') {
          setStatus('expired');
          return;
        }
      } catch {
        // A single polling failure (network jitter) doesn't interrupt the flow; retry on the next tick
      }
      if (!cancelled && aliveRef.current) {
        timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
      }
    };

    timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [status, scene, stopPolling]);

  // Resend countdown
  useEffect(() => {
    if (countdown <= 0) return;
    const id = window.setTimeout(() => setCountdown((n) => n - 1), 1000);
    return () => window.clearTimeout(id);
  }, [countdown]);

  const handleSendCode = async () => {
    setError('');
    setSending(true);
    try {
      const res = await wechatAuthApi.sendSmsCode(bindToken, phone);
      setCountdown(res.resend_after || 60);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('wechatLogin.sendCodeFailed'));
    } finally {
      setSending(false);
    }
  };

  const handleBind = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setBinding(true);
    try {
      const res = await wechatAuthApi.bindPhone(bindToken, phone, code);
      await onAuthRef.current(res.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('wechatLogin.bindFailed'));
    } finally {
      setBinding(false);
    }
  };

  const phoneValid = /^1[3-9]\d{9}$/.test(phone.replace(/\D/g, ''));

  // ── Stage 2: bind phone number ───────────────────────────────────────────────
  if (status === 'need_phone') {
    return (
      <div className={compact ? '' : 'py-2'}>
        <p className="text-sm font-medium text-primary">{t('wechatLogin.bindTitle')}</p>
        <p className="mt-1 text-xs text-secondary">{t('wechatLogin.bindSubtitle')}</p>

        {error && (
          <div className="mt-4 bg-danger-bg border border-danger-border text-danger-fg p-3 rounded-md text-sm">
            {error}
          </div>
        )}

        <form className="mt-4 space-y-4" onSubmit={handleBind}>
          <div>
            <label htmlFor="wx-phone" className="block text-sm font-medium text-secondary mb-1">
              {t('wechatLogin.phone')}
            </label>
            <input
              id="wx-phone"
              type="tel"
              inputMode="numeric"
              autoComplete="tel"
              maxLength={11}
              required
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ''))}
              className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
              placeholder={t('wechatLogin.phonePlaceholder')}
            />
          </div>

          <div>
            <label htmlFor="wx-code" className="block text-sm font-medium text-secondary mb-1">
              {t('wechatLogin.code')}
            </label>
            <div className="flex gap-2">
              <input
                id="wx-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                className="flex-1 min-w-0 px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                placeholder={t('wechatLogin.codePlaceholder')}
              />
              <button
                type="button"
                onClick={handleSendCode}
                disabled={!phoneValid || countdown > 0 || sending}
                className="whitespace-nowrap px-4 py-3 rounded-lg text-sm font-medium border border-border text-accent-primary hover:text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {countdown > 0
                  ? t('wechatLogin.resendIn', { seconds: countdown })
                  : sending
                    ? t('wechatLogin.sending')
                    : t('wechatLogin.sendCode')}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={binding || !phoneValid || code.length < 4}
            className="btn-primary w-full justify-center !py-3 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {binding ? t('wechatLogin.binding') : t('wechatLogin.bindButton')}
          </button>
        </form>
      </div>
    );
  }

  // ── Stage 1: QR scan ────────────────────────────────────────────────────
  return (
    <div className={compact ? 'text-center' : 'text-center py-2'}>
      <div className="relative mx-auto w-48 h-48 rounded-xl bg-white border border-border overflow-hidden flex items-center justify-center">
        {qrUrl && status !== 'loading' && status !== 'error' ? (
          <img src={qrUrl} alt={t('wechatLogin.qrAlt')} className="w-full h-full object-contain" />
        ) : (
          <span className="text-xs text-muted">
            {status === 'error' ? t('wechatLogin.qrFailed') : t('wechatLogin.qrLoading')}
          </span>
        )}

        {(status === 'expired' || status === 'error') && (
          <button
            type="button"
            onClick={loadQrcode}
            className="absolute inset-0 bg-black/70 text-white text-sm font-medium flex flex-col items-center justify-center gap-2"
          >
            <span>{status === 'expired' ? t('wechatLogin.expired') : t('wechatLogin.qrFailed')}</span>
            <span className="underline">{t('wechatLogin.refresh')}</span>
          </button>
        )}

        {status === 'confirmed' && (
          <div className="absolute inset-0 bg-black/70 text-white text-sm font-medium flex items-center justify-center">
            {t('wechatLogin.confirmed')}
          </div>
        )}
      </div>

      <p className="mt-4 text-sm font-medium text-primary">{t('wechatLogin.scanTitle')}</p>
      <p className="mt-1 text-xs text-secondary">{t('wechatLogin.scanHint')}</p>

      {error && status === 'error' && (
        <p className="mt-3 text-xs text-red-500">{error}</p>
      )}
    </div>
  );
}
