import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi } from '../services/authApi';
import { oauthApi } from '../services/oauthApi';

// 扩展Window接口
declare global {
  interface Window {
    gapi?: any;
    fbAsyncInit?: () => void;
    FB?: any;
  }
}

// 模拟环境变量
const env = {
  REACT_APP_GOOGLE_CLIENT_ID: 'test-client-id',
  REACT_APP_FACEBOOK_APP_ID: 'test-app-id'
};

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { t } = useTranslation();

  // 加载Google登录SDK
  useEffect(() => {
    // 加载Google登录SDK
    const loadGoogleSDK = () => {
      // 检查是否已经加载了Google SDK
      if (document.getElementById('google-signin-sdk')) return;
      
      // 创建script标签加载Google SDK
      const script = document.createElement('script');
      script.id = 'google-signin-sdk';
      script.src = 'https://apis.google.com/js/platform.js';
      script.async = true;
      script.defer = true;
      script.onload = () => {
        if (window.gapi) {
          window.gapi.load('auth2', () => {
            if (window.gapi.auth2) {
              window.gapi.auth2.init({
            client_id: env.REACT_APP_GOOGLE_CLIENT_ID,
            scope: 'profile email'
          });
            }
          });
        }
      };
      
      document.body.appendChild(script);
    };

    // 加载Facebook登录SDK
    const loadFacebookSDK = () => {
      window.fbAsyncInit = function() {
        window.FB.init({
          appId: env.REACT_APP_FACEBOOK_APP_ID,
          cookie: true,
          xfbml: true,
          version: 'v18.0'
        });
      };

      (function(d, s, id) {
        const fjs = d.getElementsByTagName(s)[0];
        if (d.getElementById(id)) return;
        const js = d.createElement(s) as HTMLScriptElement; js.id = id;
        js.src = "https://connect.facebook.net/en_US/sdk.js";
        if (fjs.parentNode) {
          fjs.parentNode.insertBefore(js, fjs);
        }
      }(document, 'script', 'facebook-jssdk'));
    };

    loadGoogleSDK();
    loadFacebookSDK();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      const response = await authApi.login(email, password);
      // 存储token到localStorage
      localStorage.setItem('token', response.access_token);
      localStorage.setItem('user', JSON.stringify({ email }));
      // 输出本地存储中的用户信息
      console.log('Login: localStorage.user:', localStorage.getItem('user'));
      // 登录成功，跳转到首页
      setTimeout(() => {
        window.location.href = '/'; // 跳转到首页
      }, 500); // 延迟500毫秒，确保本地存储已经更新
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setIsLoading(false);
    }
  };

  const handleGoogleLogin = async () => {
    setIsLoading(true);
    setError('');

    try {
      if (!window.gapi || !window.gapi.auth2) {
        throw new Error('Google SDK not loaded');
      }

      const auth2 = window.gapi.auth2.getAuthInstance();
      const googleUser = await auth2.signIn();
      const idToken = googleUser.getAuthResponse().id_token;

      const response = await oauthApi.googleLogin(idToken);
      // 存储token到localStorage
      localStorage.setItem('token', response.access_token);
      localStorage.setItem('user', JSON.stringify({ email: googleUser.getBasicProfile().getEmail() }));
      // 登录成功，跳转到首页
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Google login failed');
    } finally {
      setIsLoading(false);
    }
  };

  interface FacebookLoginResponse {
    authResponse?: {
      accessToken: string;
    };
  }

  interface FacebookUserInfo {
    email: string;
  }

  const handleFacebookLogin = async () => {
    setIsLoading(true);
    setError('');

    try {
      if (!window.FB) {
        throw new Error('Facebook SDK not loaded');
      }

      const response = await new Promise<FacebookLoginResponse>((resolve, reject) => {
        window.FB.login((response: FacebookLoginResponse) => {
          if (response.authResponse) {
            resolve(response);
          } else {
            reject(new Error('Facebook login cancelled'));
          }
        }, { scope: 'email' });
      });

      const accessToken = response.authResponse?.accessToken;
      if (!accessToken) {
        throw new Error('Failed to get Facebook access token');
      }
      const fbResponse = await oauthApi.facebookLogin(accessToken);
      // 存储token到localStorage
      localStorage.setItem('token', fbResponse.access_token);
      // 获取用户信息
      window.FB.api('/me', { fields: 'email' }, (userInfo: FacebookUserInfo) => {
        localStorage.setItem('user', JSON.stringify({ email: userInfo.email }));
      });
      // 登录成功，跳转到首页
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Facebook login failed');
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
                {t('login.title')}
              </h2>
              <p className="mt-2 text-sm text-secondary">
                {t('login.subtitle')}{' '}
                <Link
                  to="/register"
                  className="font-medium text-accent-primary hover:text-primary transition-colors duration-200"
                >
                  {t('login.createAccount')}
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
                  <input
                    id="password"
                    name="password"
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg bg-card text-primary placeholder-muted focus:outline-none transition-colors duration-200"
                    placeholder={t('login.passwordPlaceholder')}
                  />
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

            {/* Third-party login options */}
            <div className="mt-6">
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-border"></div>
                </div>
                <div className="relative flex justify-center">
                  <span className="px-3 bg-card text-secondary text-sm">
                    或使用
                  </span>
                </div>
              </div>

              <div className="mt-6 grid grid-cols-2 gap-3">
                <button
                  onClick={() => handleGoogleLogin()}
                  disabled={isLoading}
                  className="flex items-center justify-center gap-2 px-4 py-3 rounded-lg font-medium transition-all duration-300 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-color)'
                  }}
                >
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-8.653 0-3.44-1.68-6.453-4.08-8.573l-2.053 2.053c1.907 2.107 2.96 4.907 2.96 8.04s-1.053 5.933-2.96 8.04c-1.907 2.107-4.613 3.467-8.04 3.467-5.893 0-10.667-4.773-10.667-10.667S6.587 1.333 12.48 1.333c3.653 0 6.813 1.44 8.84 3.84l2.813-2.813C21.267 2.413 17.52 0 12.48 0c-7.733 0-14 6.267-14 14s6.267 14 14 14c3.52 0 6.607-1.173 9.093-3.36 2.4-2.187 3.907-5.147 3.907-8.64s-1.507-6.453-3.907-8.64c-2.507-2.187-5.607-3.36-9.12-3.36z" />
                  </svg>
                  <span>Google</span>
                </button>
                <button
                  onClick={() => handleFacebookLogin()}
                  disabled={isLoading}
                  className="flex items-center justify-center gap-2 px-4 py-3 rounded-lg font-medium transition-all duration-300 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-color)'
                  }}
                >
                  <svg className="w-5 h-5 text-blue-600" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M9 8h-3v4h3v12h5v-12h3.642l.358-4h-4v-1.667c0-.955.192-1.333 1.115-1.333h2.885v-5h-3.808c-3.596 0-5.192 1.583-5.192 4.615v3.385z" />
                  </svg>
                  <span>Facebook</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
