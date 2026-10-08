import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi } from '../services/authApi';
import { wechatAuthApi } from '../services/wechatAuthApi';
import { WechatQrLogin } from '../components/WechatQrLogin';
import { PhoneLogin } from '../components/PhoneLogin';
import { useAuth } from '../contexts/AuthContext';
import { PageHead } from '../components/PageHead';

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'wechat' | 'phone' | 'email'>('email');
  const [wechatEnabled, setWechatEnabled] = useState(false);
  const [phoneEnabled, setPhoneEnabled] = useState(false);
  const [emailRegisterEnabled, setEmailRegisterEnabled] = useState(false);
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { setToken, setUser } = useAuth();

  // Channel probing. The default tab lands on "the path that can register": email sign-up is off, so QR scan comes first,
  // then phone number; only if neither is enabled fall back to email (then only existing accounts can log in).
  useEffect(() => {
    let alive = true;
    void wechatAuthApi.config().then((cfg) => {
      if (!alive) return;
      setWechatEnabled(cfg.wechat_login_enabled);
      setPhoneEnabled(cfg.phone_login_enabled);
      setEmailRegisterEnabled(cfg.email_register_enabled);
      if (cfg.wechat_login_enabled) setTab('wechat');
      else if (cfg.phone_login_enabled) setTab('phone');
    });
    return () => { alive = false; };
  }, []);

  // Landing after a successful QR scan / phone binding, sharing the write logic with email login
  const handleAuthenticated = useCallback(async (accessToken: string) => {
    try {
      setUser(await authApi.getCurrentUser(accessToken));
    } catch {
      /* A /me failure shouldn't block login; the Header will fill it in on the next refresh */
    }
    setToken(accessToken);
    navigate('/');
  }, [navigate, setToken, setUser]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      const response = await authApi.login(email, password);
      try {
        const me = await authApi.getCurrentUser(response.access_token);
        setUser(me);
      } catch {
        setUser({ email });
      }
      setToken(response.access_token);  // Sync all consumers via AuthContext
      // SPA navigation to home; AuthProvider already broadcast the token update, so no full-page refresh via window.location
      // is needed (the old version relied on a full refresh to work around state desync)
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid-background">
      <PageHead titleKey="pageMeta.login.title" descriptionKey="pageMeta.login.description" />
      {/* Background glow effect */}
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-md mx-auto animate-fade-in">
          <div className="bg-card border border-border rounded-2xl p-8">
            <div className="text-center">
              <h2 className="mt-6 text-3xl font-bold text-primary">
                {t('login.title')}
              </h2>
              <p className="mt-2 text-sm text-secondary">
                {t('login.subtitle')}
                {emailRegisterEnabled ? (
                  <>
                    {' '}
                    <Link
                      to="/register"
                      className="font-medium text-accent-primary hover:text-primary transition-colors duration-200"
                    >
                      {t('login.createAccount')}
                    </Link>
                  </>
                ) : null}
              </p>
            </div>

            {(wechatEnabled || phoneEnabled) && (
              <div
                className="mt-6 grid gap-1 p-1 rounded-lg border border-border"
                style={{ gridTemplateColumns: `repeat(${1 + (wechatEnabled ? 1 : 0) + (phoneEnabled ? 1 : 0)}, minmax(0, 1fr))` }}
              >
                {wechatEnabled && (
                  <button
                    type="button"
                    onClick={() => setTab('wechat')}
                    className={`py-2 text-sm font-medium rounded-md transition-colors ${
                      tab === 'wechat' ? 'bg-accent-primary text-white' : 'text-secondary hover:text-primary'
                    }`}
                  >
                    {t('wechatLogin.tab')}
                  </button>
                )}
                {phoneEnabled && (
                  <button
                    type="button"
                    onClick={() => setTab('phone')}
                    className={`py-2 text-sm font-medium rounded-md transition-colors ${
                      tab === 'phone' ? 'bg-accent-primary text-white' : 'text-secondary hover:text-primary'
                    }`}
                  >
                    {t('phoneLogin.tab')}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setTab('email')}
                  className={`py-2 text-sm font-medium rounded-md transition-colors ${
                    tab === 'email' ? 'bg-accent-primary text-white' : 'text-secondary hover:text-primary'
                  }`}
                >
                  {t('wechatLogin.emailTab')}
                </button>
              </div>
            )}

            {wechatEnabled && tab === 'wechat' ? (
              <div className="mt-6">
                <WechatQrLogin onAuthenticated={handleAuthenticated} />
              </div>
            ) : phoneEnabled && tab === 'phone' ? (
              <div className="mt-6">
                <PhoneLogin onAuthenticated={handleAuthenticated} />
              </div>
            ) : (

            <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
              {error && (
                <div className="bg-danger-bg border border-danger-border text-danger-fg p-4 rounded-md text-sm">
                  {error}
                </div>
              )}

              <div className="space-y-4">
                <div>
                  <label htmlFor="email" className="block text-sm font-medium text-secondary mb-1">
                    {t('login.email')}
                  </label>
                  <input
                    id="email"
                    name="email"
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                    placeholder={t('login.emailPlaceholder')}
                  />
                </div>
                <div>
                  <label htmlFor="password" className="block text-sm font-medium text-secondary mb-1">
                    {t('login.password')}
                  </label>
                  <div className="relative">
                    <input
                      id="password"
                      name="password"
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
              </div>

              <div className="flex items-center justify-between">
                <div className="flex items-center">
                  <input
                    id="remember-me"
                    name="remember-me"
                    type="checkbox"
                    className="h-4 w-4 text-accent-primary focus:ring-accent-primary border-border rounded"
                  />
                  <label htmlFor="remember-me" className="ml-2 block text-sm text-secondary">
                    {t('login.rememberMe')}
                  </label>
                </div>

                <div className="text-sm">
                  <Link
                    to="/forgot-password"
                    className="font-medium text-accent-primary hover:text-primary transition-colors duration-200"
                  >
                    {t('login.forgotPassword')}
                  </Link>
                </div>
              </div>

              <div>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="btn-primary w-full justify-center !py-3 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isLoading ? (
                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                  ) : null}
                  {isLoading ? t('login.loading') : t('login.button')}
                </button>
              </div>
            </form>
            )}

            {!emailRegisterEnabled && (
              <p className="mt-6 text-center text-xs text-muted">
                {phoneEnabled && !wechatEnabled
                  ? t('phoneLogin.emailRegisterClosed')
                  : t('wechatLogin.emailRegisterClosed')}
              </p>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
