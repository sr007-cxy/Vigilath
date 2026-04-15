import { useEffect, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi } from '../../services/authApi';

interface StoredUser {
  id: number;
  email: string;
  name?: string | null;
  is_active: boolean;
}

export function AccountLayout() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [user, setUser] = useState<StoredUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      navigate('/login', { state: { from: '/account' } });
      return;
    }
    let cancelled = false;
    authApi
      .getCurrentUser(token)
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        localStorage.setItem('user', JSON.stringify(u));
      })
      .catch(() => {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        navigate('/login', { state: { from: '/account' } });
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    navigate('/');
  };

  const menu = [
    { to: '/account/profile', label: t('account.menu.profile', '个人资料') },
    { to: '/account/membership', label: t('account.menu.membership', '会员信息') },
    { to: '/account/usage', label: t('account.menu.usage', '使用情况') },
    { to: '/account/history', label: t('account.menu.history', '检测记录') },
  ];

  if (loading) {
    return (
      <div className="min-h-screen grid-background flex items-center justify-center">
        <div
          className="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2"
          style={{ borderColor: 'var(--accent-primary)' }}
        />
      </div>
    );
  }

  return (
    <div className="min-h-screen grid-background relative">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8 md:py-10 relative z-10">
        <div className="grid grid-cols-1 md:grid-cols-[240px_1fr] gap-6">
          <aside className="md:sticky md:top-24 md:self-start">
            <div
              className="rounded-2xl p-5"
              style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-color)',
              }}
            >
              <div
                className="flex items-center gap-3 pb-4 border-b"
                style={{ borderColor: 'var(--border-color)' }}
              >
                <div
                  className="w-11 h-11 rounded-full flex items-center justify-center text-white font-semibold shrink-0"
                  style={{ background: 'var(--accent-gradient)' }}
                >
                  {user?.email?.[0]?.toUpperCase() ?? 'U'}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold truncate text-primary">
                    {user?.email}
                  </p>
                  <p className="text-xs text-secondary">
                    {t('account.layout.subtitle', '账户信息')}
                  </p>
                </div>
              </div>
              <nav className="mt-4 flex md:flex-col gap-1 overflow-x-auto md:overflow-visible">
                {menu.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className="px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors"
                    style={({ isActive }) =>
                      isActive
                        ? {
                            background: 'var(--bg-tertiary)',
                            color: 'var(--accent-primary)',
                          }
                        : { color: 'var(--text-secondary)' }
                    }
                  >
                    {item.label}
                  </NavLink>
                ))}
              </nav>
              <button
                onClick={handleLogout}
                className="hidden md:block mt-5 w-full px-3 py-2 text-sm font-medium rounded-lg transition-colors"
                style={{
                  color: '#f43f5e',
                  border: '1px solid rgba(244, 63, 94, 0.35)',
                  background: 'transparent',
                }}
              >
                {t('account.layout.logout', '退出登录')}
              </button>
            </div>
          </aside>

          <main className="min-w-0">
            <Outlet context={{ user }} />
          </main>
        </div>
      </div>
    </div>
  );
}
