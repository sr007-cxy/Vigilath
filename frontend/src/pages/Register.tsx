import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi } from '../services/authApi';

export function Register() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [terms, setTerms] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { t } = useTranslation();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    // 验证密码
    if (password !== confirmPassword) {
      setIsLoading(false);
      setError(t('register.error'));
      return;
    }

    // 验证是否同意服务条款
    if (!terms) {
      setIsLoading(false);
      setError('请同意服务条款和隐私政策');
      return;
    }

    try {
      await authApi.register(name, email, password);
      // 注册成功，跳转到登录页
      navigate('/login');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid-background">
      {/* 背景发光效果 */}
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
                  <label htmlFor="name" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.name')}
                  </label>
                  <input
                    id="name"
                    name="name"
                    type="text"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                    placeholder={t('register.namePlaceholder')}
                  />
                </div>
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
                  <input
                    id="password"
                    name="password"
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                    placeholder={t('register.passwordPlaceholder')}
                  />
                </div>
                <div>
                  <label htmlFor="confirm-password" className="block text-sm font-medium text-secondary mb-1">
                    {t('register.confirmPassword')}
                  </label>
                  <input
                    id="confirm-password"
                    name="confirm-password"
                    type="password"
                    required
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                    placeholder={t('register.confirmPasswordPlaceholder')}
                  />
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
                  className="w-full bg-accent-primary hover:bg-accent-primary/80 text-white font-semibold py-3 rounded-lg transition-colors duration-300 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
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
    </div>
  );
}
