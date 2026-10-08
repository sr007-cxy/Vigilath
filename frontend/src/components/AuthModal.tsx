import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi } from '../services/authApi';
import { wechatAuthApi } from '../services/wechatAuthApi';
import { WechatQrLogin } from './WechatQrLogin';
import { PhoneLogin } from './PhoneLogin';
import { useAuth } from '../contexts/AuthContext';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  defaultTab?: 'login' | 'register' | 'phone' | 'wechat';
  onSuccess?: () => void;
}

export function AuthModal({ isOpen, onClose, defaultTab = 'login', onSuccess }: AuthModalProps) {
  const [tab, setTab] = useState<'login' | 'register' | 'wechat' | 'phone'>(defaultTab);
  const [wechatEnabled, setWechatEnabled] = useState(false);
  const [phoneEnabled, setPhoneEnabled] = useState(false);
  // Config probing is async (~100ms). If the user has already clicked a tab by then, don't change their choice.
  const tabTouchedRef = useRef(false);
  const pickTab = (next: 'login' | 'register' | 'wechat' | 'phone') => {
    tabTouchedRef.current = true;
    setTab(next);
  };
  const [emailRegisterEnabled, setEmailRegisterEnabled] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [showForgotPassword, setShowForgotPassword] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');
  const [forgotMessage, setForgotMessage] = useState('');
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { setToken, setUser } = useAuth();
  const modalRef = useRef<HTMLDivElement>(null);

  // On open, first land on the tab the caller asked for. The Provider stays mounted, and useState(defaultTab)
  // only evaluates once -- without this, opening a second time with a different defaultTab would stay on the previous tab.
  useEffect(() => {
    if (isOpen) setTab(defaultTab);
  }, [isOpen, defaultTab]);

  // Probe the channel switches again. **When email registration is off, always land on QR code**, not just for the register tab.
  //
  // All 19 gates on the site call openAuthModal('login'), while email registration is already closed with a 403 --
  // new users can only come from WeChat QR scanning and can't fill in that email/password form. It used to switch only when
  // cur === 'register', effectively sending every new user stopped by a gate into a dead end.
  // Existing email users are unaffected: right below the QR panel there's a one-click "Have an account? Log in" to switch back.
  useEffect(() => {
    if (!isOpen) return;
    let alive = true;
    tabTouchedRef.current = false;   // re-evaluate on every open
    void wechatAuthApi.config().then((cfg) => {
      if (!alive) return;
      setWechatEnabled(cfg.wechat_login_enabled);
      setPhoneEnabled(cfg.phone_login_enabled);
      setEmailRegisterEnabled(cfg.email_register_enabled);
      // WeChat QR scanning is the default entry (product decision): it's the main channel for both login and registration,
      // and email/phone keep one-click switches in the tab bar and at the bottom.
      // tabTouchedRef is necessary -- this callback arrives after the user's click, and without the check it would override their choice.
      setTab((cur) => {
        if (tabTouchedRef.current) return cur;
        if (cur === 'login' && cfg.wechat_login_enabled) return 'wechat';
        if (cur === 'register' && !cfg.email_register_enabled) {
          if (cfg.wechat_login_enabled) return 'wechat';
          if (cfg.phone_login_enabled) return 'phone';
        }
        return cur;
      });
    });
    return () => { alive = false; };
  }, [isOpen]);

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = 'unset';
    }
    return () => {
      document.body.style.overflow = 'unset';
    };
  }, [isOpen]);

  const handleClickOutside = (event: MouseEvent) => {
    if (modalRef.current && !modalRef.current.contains(event.target as Node)) {
      closeAll();
    }
  };

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  // After a successful login, backfill the user profile into AuthContext (synced to localStorage).
  // Header reads is_admin from context to decide whether to render the "Workbench" entry; writing only email
  // would hide the entry from admins; so try /me first, and on failure fall back to writing only email.
  const persistUser = async (accessToken: string, fallbackEmail: string) => {
    try {
      const me = await authApi.getCurrentUser(accessToken);
      setUser(me);
      return me;
    } catch {
      setUser({ email: fallbackEmail });
      return null;
    }
  };
  // QR scan / phone verification code share the same landing logic
  const handleWechatAuthenticated = async (accessToken: string) => {
    const me = await persistUser(accessToken, email);
    setToken(accessToken);
    onClose();
    if (me?.role === 'tenant') {
      navigate('/gateway-portal');
    } else if (onSuccess) {
      onSuccess();
    }
    // Without onSuccess, **stay in place**. AuthModal is an overlay; kicking the user back to the home page after login
    // throws away the report/pricing page they were looking at -- and the moment this dialog pops is exactly when intent is strongest.
  };

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      const response = await authApi.login(email, password);
      const me = await persistUser(response.access_token, email);
      setToken(response.access_token);
      onClose();
      if (me?.role === 'tenant') {
        // Gateway tenant login goes straight to the tenant portal
        navigate('/gateway-portal');
      } else if (onSuccess) {
        onSuccess();
      }
      // Same as above: stay in place by default, don't go to the home page
    } catch (err) {
      setError(err instanceof Error ? err.message : t('login.failed'));
    } finally {
      setIsLoading(false);
    }
  };

  const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    if (password !== confirmPassword) {
      setError(t('register.passwordMismatch'));
      setIsLoading(false);
      return;
    }

    try {
      await authApi.register(email, password);
      const loginRes = await authApi.login(email, password);
      await persistUser(loginRes.access_token, email);
      setToken(loginRes.access_token);
      onClose();
      if (onSuccess) {
        onSuccess();
      } else {
        navigate('/account/membership');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('register.failed'));
    } finally {
      setIsLoading(false);
    }
  };

  const handleForgotPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      await authApi.forgotPassword(forgotEmail);
      setForgotMessage(t('forgotPassword.success'));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('forgotPassword.failed'));
    } finally {
      setIsLoading(false);
    }
  };

  const closeAll = () => {
    onClose();
    setTimeout(() => {
      setTab(defaultTab);
      setEmail('');
      setPassword('');
      setConfirmPassword('');
      setShowPassword(false);
      setShowConfirmPassword(false);
      setError('');
      setShowForgotPassword(false);
      setForgotEmail('');
      setForgotMessage('');
    }, 300);
  };

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={closeAll}
        style={{ background: 'rgba(0, 0, 0, 0.6)', backdropFilter: 'blur(4px)' }}
      />

      <div
        ref={modalRef}
        className="relative w-full max-w-md bg-card border border-border rounded-2xl shadow-2xl overflow-hidden animate-fade-in-up"
        style={{
          background: 'var(--bg-card)',
          borderColor: 'var(--border-color)',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)'
        }}
      >
        <button
          onClick={closeAll}
          className="absolute top-4 right-4 p-2 rounded-full hover:bg-muted transition-colors z-10"
          style={{
            color: 'var(--text-secondary)',
            background: 'rgba(255, 255, 255, 0.05)',
            border: '1px solid var(--border-color)'
          }}
        >
          <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="p-8">
          {tab === 'login' && (
            <>
              {showForgotPassword ? (
                <div className="animate-fade-in">
                  <h2 className="text-2xl font-bold text-primary mb-2">{t('forgotPassword.title')}</h2>
                  <p className="text-sm text-secondary mb-6">{t('forgotPassword.subtitle')}</p>

                  {forgotMessage && (
                    <div className="bg-green-900/30 border border-green-800/50 text-green-300 p-4 rounded-md text-sm mb-4">
                      {forgotMessage}
                    </div>
                  )}

                  <form onSubmit={handleForgotPassword} className="space-y-4">
                    <div>
                      <label htmlFor="forgot-email" className="block text-sm font-medium text-secondary mb-1">
                        {t('login.email')}
                      </label>
                      <input
                        id="forgot-email"
                        type="email"
                        required
                        value={forgotEmail}
                        onChange={(e) => setForgotEmail(e.target.value)}
                        className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                        placeholder={t('login.emailPlaceholder')}
                      />
                    </div>

                    {error && (
                      <div className="bg-danger-bg border border-danger-border text-danger-fg p-3 rounded-md text-sm">
                        {error}
                      </div>
                    )}

                    <div className="flex gap-3">
                      <button
                        type="button"
                        onClick={() => {
                          setError('');
                          setShowForgotPassword(false);
                        }}
                        className="flex-1 px-4 py-3 rounded-lg font-semibold transition-colors duration-200"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-secondary)'
                        }}
                      >
                        {t('common.cancel')}
                      </button>
                      <button
                        type="submit"
                        disabled={isLoading}
                        className="flex-1 px-4 py-3 rounded-lg font-semibold transition-colors duration-200 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                        style={{
                          background: 'var(--accent-primary)',
                          color: 'white'
                        }}
                      >
                        {isLoading ? (
                          <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                          </svg>
                        ) : null}
                        {t('forgotPassword.button')}
                      </button>
                    </div>
                  </form>
                </div>
              ) : (
                <div className="animate-fade-in">
                  <h2 className="text-2xl font-bold text-primary mb-2">{t('login.title')}</h2>
                  <p className="text-sm text-secondary mb-6">{t('login.subtitle')}</p>

                  <form onSubmit={handleLoginSubmit} className="space-y-4">
                    <div>
                      <label htmlFor="login-email" className="block text-sm font-medium text-secondary mb-1">
                        {t('login.email')}
                      </label>
                      <input
                        id="login-email"
                        type="email"
                        required
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                        placeholder={t('login.emailPlaceholder')}
                      />
                    </div>

                    <div>
                      <label htmlFor="login-password" className="block text-sm font-medium text-secondary mb-1">
                        {t('login.password')}
                      </label>
                      <div className="relative">
                        <input
                          id="login-password"
                          type={showPassword ? 'text' : 'password'}
                          required
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          className="w-full px-4 py-3 pr-12 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                          placeholder={t('login.passwordPlaceholder')}
                        />
                        <button
                          type="button"
                          onClick={() => setShowPassword(!showPassword)}
                          className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-secondary hover:text-primary transition-colors"
                          tabIndex={-1}
                        >
                          {showPassword ? (
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.878 9.878L6.59 6.59m7.532 7.532l3.29 3.29M3 3l18 18" />
                            </svg>
                          ) : (
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                            </svg>
                          )}
                        </button>
                      </div>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex items-center">
                        <input
                          id="remember-me"
                          type="checkbox"
                          className="h-4 w-4 text-accent-primary focus:ring-accent-primary border-border rounded"
                        />
                        <label htmlFor="remember-me" className="ml-2 block text-sm text-secondary">
                          {t('login.rememberMe')}
                        </label>
                      </div>

                      <button
                        type="button"
                        onClick={() => {
                          setError('');
                          setShowForgotPassword(true);
                        }}
                        className="text-sm font-medium transition-colors duration-200"
                        style={{ color: 'var(--accent-primary)' }}
                      >
                        {t('login.forgotPassword')}
                      </button>
                    </div>

                    {error && (
                      <div className="bg-danger-bg border border-danger-border text-danger-fg p-3 rounded-md text-sm">
                        {error}
                      </div>
                    )}

                    <button
                      type="submit"
                      disabled={isLoading}
                      className="w-full px-4 py-3 rounded-lg font-semibold transition-colors duration-200 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      style={{
                        background: 'var(--accent-primary)',
                        color: 'white'
                      }}
                    >
                      {isLoading ? (
                        <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                      ) : null}
                      {isLoading ? t('login.loading') : t('login.button')}
                    </button>
                  </form>

                  {(emailRegisterEnabled || wechatEnabled || phoneEnabled) && (
                    <div className="mt-6 text-center text-sm text-secondary">
                      {t('login.noAccount')}{' '}
                      <button
                        onClick={() => {
                          setError('');
                          pickTab(emailRegisterEnabled ? 'register' : wechatEnabled ? 'wechat' : 'phone');
                        }}
                        className="font-medium transition-colors duration-200"
                        style={{ color: 'var(--accent-primary)' }}
                      >
                        {emailRegisterEnabled
                          ? t('login.register')
                          : wechatEnabled
                            ? t('wechatLogin.tab')
                            : t('phoneLogin.tab')}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {tab === 'phone' && (
            <div className="animate-fade-in">
              <h2 className="text-2xl font-bold text-primary mb-2">{t('phoneLogin.tab')}</h2>
              <p className="text-sm text-secondary mb-6">{t('phoneLogin.modalSubtitle')}</p>

              <PhoneLogin compact onAuthenticated={handleWechatAuthenticated} />

              <div className="mt-6 text-center text-sm text-secondary">
                {t('register.hasAccount')}{' '}
                <button
                  onClick={() => {
                    setError('');
                    pickTab('login');
                  }}
                  className="font-medium transition-colors duration-200"
                  style={{ color: 'var(--accent-primary)' }}
                >
                  {t('register.login')}
                </button>
              </div>
            </div>
          )}

          {tab === 'wechat' && (
            <div className="animate-fade-in">
              <h2 className="text-2xl font-bold text-primary mb-2">{t('wechatLogin.tab')}</h2>
              <p className="text-sm text-secondary mb-6">{t('wechatLogin.modalSubtitle')}</p>

              <WechatQrLogin compact onAuthenticated={handleWechatAuthenticated} />

              {/* QR scanning is the default entry; other methods must have an exit here, or people who only want phone/email have no way forward */}
              <div className="mt-6 pt-4 border-t border-border text-center text-sm text-secondary">
                {t('wechatLogin.otherWays')}
                {phoneEnabled && (
                  <>
                    {' '}
                    <button
                      onClick={() => { setError(''); pickTab('phone'); }}
                      className="font-medium transition-colors duration-200"
                      style={{ color: 'var(--accent-primary)' }}
                    >
                      {t('phoneLogin.tab')}
                    </button>
                    <span className="mx-1 text-muted">·</span>
                  </>
                )}
                <button
                  onClick={() => { setError(''); pickTab('login'); }}
                  className="font-medium transition-colors duration-200"
                  style={{ color: 'var(--accent-primary)' }}
                >
                  {t('wechatLogin.emailTab')}
                </button>
              </div>
            </div>
          )}

          {tab === 'register' && emailRegisterEnabled && (
            <div className="animate-fade-in">
              <h2 className="text-2xl font-bold text-primary mb-2">{t('register.title')}</h2>
              <p className="text-sm text-secondary mb-6">{t('register.subtitle')}</p>

              <form onSubmit={handleRegisterSubmit} className="space-y-4">
                <div>
                  <label htmlFor="register-email" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.email')}
                  </label>
                  <input
                    id="register-email"
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                    placeholder={t('register.emailPlaceholder')}
                  />
                </div>

                <div>
                  <label htmlFor="register-password" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.password')}
                  </label>
                  <div className="relative">
                    <input
                      id="register-password"
                      type={showPassword ? 'text' : 'password'}
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full px-4 py-3 pr-12 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                      placeholder={t('register.passwordPlaceholder')}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-secondary hover:text-primary transition-colors"
                      tabIndex={-1}
                    >
                      {showPassword ? (
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.878 9.878L6.59 6.59m7.532 7.532l3.29 3.29M3 3l18 18" />
                        </svg>
                      ) : (
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                        </svg>
                      )}
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-secondary">
                    {t('register.passwordHint')}
                  </p>
                </div>

                <div>
                  <label htmlFor="register-confirm-password" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.confirmPassword')}
                  </label>
                  <div className="relative">
                    <input
                      id="register-confirm-password"
                      type={showConfirmPassword ? 'text' : 'password'}
                      required
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className="w-full px-4 py-3 pr-12 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                      placeholder={t('register.confirmPasswordPlaceholder')}
                    />
                    <button
                      type="button"
                      onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-secondary hover:text-primary transition-colors"
                      tabIndex={-1}
                    >
                      {showConfirmPassword ? (
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.878 9.878L6.59 6.59m7.532 7.532l3.29 3.29M3 3l18 18" />
                        </svg>
                      ) : (
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>

                {error && (
                  <div className="bg-danger-bg border border-danger-border text-danger-fg p-3 rounded-md text-sm">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={isLoading}
                  className="w-full px-4 py-3 rounded-lg font-semibold transition-colors duration-200 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    background: 'var(--accent-primary)',
                    color: 'white'
                  }}
                >
                  {isLoading ? (
                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                  ) : null}
                  {isLoading ? t('register.loading') : t('register.button')}
                </button>
              </form>

              <div className="mt-6 text-center text-sm text-secondary">
                {t('register.hasAccount')}{' '}
                <button
                  onClick={() => {
                    setError('');
                    pickTab('login');
                  }}
                  className="font-medium transition-colors duration-200"
                  style={{ color: 'var(--accent-primary)' }}
                >
                  {t('register.login')}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
