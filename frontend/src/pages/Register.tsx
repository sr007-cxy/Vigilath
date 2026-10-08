import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi } from '../services/authApi';
import { wechatAuthApi } from '../services/wechatAuthApi';
import { WechatQrLogin } from '../components/WechatQrLogin';
import { PhoneLogin } from '../components/PhoneLogin';
import { PaymentModal } from '../components/PaymentModal';
import { useAuth } from '../contexts/AuthContext';
import { PageHead } from '../components/PageHead';

export function Register() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [terms, setTerms] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [paymentToken, setPaymentToken] = useState<string | null>(null);
  // null = not probed yet; don't render the form yet, to avoid it flashing and being replaced
  const [emailRegisterEnabled, setEmailRegisterEnabled] = useState<boolean | null>(null);
  const [wechatEnabled, setWechatEnabled] = useState(false);
  const [phoneEnabled, setPhoneEnabled] = useState(false);
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { setToken, setUser } = useAuth();

  useEffect(() => {
    let alive = true;
    void wechatAuthApi.config().then((cfg) => {
      if (!alive) return;
      setEmailRegisterEnabled(cfg.email_register_enabled);
      setWechatEnabled(cfg.wechat_login_enabled);
      setPhoneEnabled(cfg.phone_login_enabled);
    });
    return () => { alive = false; };
  }, []);

  const handleAuthenticated = useCallback(async (accessToken: string) => {
    try {
      setUser(await authApi.getCurrentUser(accessToken));
    } catch {
      /* A /me failure doesn't block login */
    }
    setToken(accessToken);
    navigate('/');
  }, [navigate, setToken, setUser]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    if (password !== confirmPassword) {
      setIsLoading(false);
      setError(t('register.error'));
      return;
    }

    if (!terms) {
      setIsLoading(false);
      setError(t('register.termsRequired'));
      return;
    }

    try {
      await authApi.register(email, password);
      const loginRes = await authApi.login(email, password);
      setToken(loginRes.access_token);  // Broadcast to all useAuth / useMembership
      setPaymentToken(loginRes.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('register.failed'));
    } finally {
      setIsLoading(false);
    }
  };

  const handlePaymentSuccess = () => {
    setPaymentToken(null);
    navigate('/account/membership');
  };

  const handlePaymentClose = () => {
    setPaymentToken(null);
    navigate('/');
  };

  // Still probing (null): render nothing. Falling through would flash an email form whose submit is bound to 403,
  // and users might already be filling it in when the whole page gets replaced -- the comment above always promised this, it just wasn't implemented.
  if (emailRegisterEnabled === null) {
    return (
      <div className="min-h-screen grid-background flex items-center justify-center">
        <div
          className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2"
          style={{ borderColor: 'var(--accent-primary)' }}
        />
      </div>
    );
  }

  // Email sign-up disabled: replace the whole page with WeChat QR sign-up instead of rendering a form whose submit is bound to 403
  if (emailRegisterEnabled === false) {
    return (
      <div className="min-h-screen grid-background">
        <PageHead titleKey="pageMeta.register.title" descriptionKey="pageMeta.register.description" />
        <div className="bg-glow bg-glow-1"></div>
        <div className="bg-glow bg-glow-2"></div>
        <div className="bg-glow bg-glow-3"></div>
        <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
          <div className="w-full max-w-md mx-auto animate-fade-in">
            <div className="bg-card border border-border rounded-2xl p-8">
              <div className="text-center">
                <h2 className="text-3xl font-bold text-primary">
                  {t('wechatLogin.registerClosedTitle')}
                </h2>
                <p className="mt-2 text-sm text-secondary">
                  {wechatEnabled
                    ? t('wechatLogin.registerClosedSubtitle')
                    : t('phoneLogin.registerClosedSubtitle')}
                </p>
              </div>

              {wechatEnabled ? (
                <div className="mt-8">
                  <WechatQrLogin onAuthenticated={handleAuthenticated} />
                </div>
              ) : phoneEnabled ? (
                <div className="mt-8">
                  <PhoneLogin onAuthenticated={handleAuthenticated} />
                </div>
              ) : (
                <p className="mt-8 text-center text-sm text-secondary">
                  {t('wechatLogin.registerClosedNoChannel')}
                </p>
              )}

              <p className="mt-8 text-center text-sm text-secondary">
                <Link
                  to="/login"
                  className="font-medium text-accent-primary hover:text-primary transition-colors duration-200"
                >
                  {t('register.loginAccount')}
                </Link>
              </p>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen grid-background">
      <PageHead titleKey="pageMeta.register.title" descriptionKey="pageMeta.register.description" />
      {/* Background glow effect */}
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-md mx-auto animate-fade-in">
          <div className="bg-card border border-border rounded-2xl p-8">
            <div className="text-center">
              <h2 className="mt-6 text-3xl font-bold text-primary">
                {t('register.title')}
              </h2>
              <p className="mt-2 text-sm text-secondary">
                {t('register.subtitle')}{' '}
                <Link
                  to="/login"
                  className="font-medium text-accent-primary hover:text-primary transition-colors duration-200"
                >
                  {t('register.loginAccount')}
                </Link>
              </p>
            </div>

            <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
              {error && (
                <div className="bg-red-900 border border-red-800 text-red-300 p-4 rounded-md text-sm">
                  {error}
                </div>
              )}

              <div className="space-y-4">
                <div>
                  <label htmlFor="email" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.email')}
                  </label>
                  <input
                    id="email"
                    name="email"
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                    placeholder={t('register.emailPlaceholder')}
                  />
                </div>
                <div>
                  <label htmlFor="password" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.password')}
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
                </div>
                <div>
                  <label htmlFor="confirm-password" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.confirmPassword')}
                  </label>
                  <div className="relative">
                    <input
                      id="confirm-password"
                      name="confirm-password"
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
              </div>

              <div className="flex items-center">
                <input
                  id="terms"
                  name="terms"
                  type="checkbox"
                  required
                  checked={terms}
                  onChange={(e) => setTerms(e.target.checked)}
                  className="h-4 w-4 text-accent-primary focus:ring-accent-primary border-border rounded"
                />
                <label htmlFor="terms" className="ml-2 block text-sm text-secondary">
                  {t('register.terms')}
                </label>
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
                  {isLoading ? t('register.loading') : t('register.button')}
                </button>
              </div>
            </form>

          </div>
        </div>
      </main>

      {paymentToken && (
        <PaymentModal
          token={paymentToken}
          onClose={handlePaymentClose}
          onSuccess={handlePaymentSuccess}
        />
      )}
    </div>
  );
}
