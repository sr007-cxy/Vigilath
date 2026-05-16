// 工作台(admin 专属)— 独立 sidebar,内只含「审核」.
// 非 admin 用户进入这里会被预检踢回 /dashboard.
//
// 结构跟 DashboardLayout 同源,但:
// - tenant 标题用 dashboard.nav.adminWorkbench(「工作台」)
// - 不显示 email
// - sidebar 只一项「审核」
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useEffect, useState } from 'react';
import { authApi } from '../../services/authApi';
import { useAuthModal } from '../../contexts/AuthModalContext';
import { PageHead } from '../../components/PageHead';

interface StoredUser {
  id: number;
  email: string;
  name?: string | null;
  is_active: boolean;
  is_admin?: boolean;
}

const sidebarItems = [
  { to: '/workbench/review', end: false, icon: 'review', labelKey: 'nav.adminReview' },
  { to: '/workbench/content-review', end: false, icon: 'content', labelKey: 'nav.adminContentReview' },
] as const;

function SidebarIcon({ name }: { name: string }) {
  if (name === 'review') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    );
  }
  if (name === 'content') {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
      </svg>
    );
  }
  return null;
}

export function WorkbenchLayout() {
  const navigate = useNavigate();
  const { openAuthModal } = useAuthModal();
  const { t } = useTranslation();
  const [user, setUser] = useState<StoredUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      openAuthModal('login');
      navigate('/', { replace: true });
      return;
    }
    let cancelled = false;
    authApi
      .getCurrentUser(token)
      .then((u) => {
        if (cancelled) return;
        if (!u.is_admin) {
          // 非 admin 进 /workbench 直接踢回 /dashboard
          navigate('/dashboard', { replace: true });
          return;
        }
        setUser(u);
        localStorage.setItem('user', JSON.stringify(u));
      })
      .catch(() => {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        openAuthModal('login');
        navigate('/', { replace: true });
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [navigate, openAuthModal]);

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

  if (!user) return null;

  return (
    <div className="min-h-[calc(100vh-5rem)] grid-background relative">
      <PageHead
        titleKey="dashboard.nav.adminWorkbench"
        descriptionKey="dashboard.nav.adminWorkbench"
      />
      <div className="bg-glow bg-glow-1" />
      <div className="bg-glow bg-glow-2" />
      <div className="bg-glow bg-glow-3" />

      <div className="md:flex relative z-10">
        <aside
          className="md:w-56 md:shrink-0 md:sticky md:top-16 md:h-[calc(100vh-4rem)] md:flex md:flex-col md:border-r"
          style={{
            borderColor: 'var(--border-color)',
            background: 'var(--bg-card)',
          }}
        >
          <div
            className="hidden md:flex items-center gap-2 p-4 border-b"
            style={{ borderColor: 'var(--border-color)' }}
          >
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold"
              style={{ background: '#ef4444', color: '#fff' }}
            >
              🛡
            </div>
            <p className="text-sm font-semibold truncate text-primary">
              {t('dashboard.nav.adminWorkbench')}
            </p>
          </div>

          <nav className="p-2 md:p-3 flex md:flex-col gap-0.5 overflow-x-auto md:overflow-visible md:flex-1 scrollbar-hide">
            {sidebarItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className="px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors flex items-center gap-2"
                style={({ isActive }) =>
                  isActive
                    ? {
                        background: 'var(--bg-tertiary)',
                        color: 'var(--accent-primary)',
                      }
                    : { color: 'var(--text-secondary)' }
                }
              >
                <SidebarIcon name={item.icon} />
                {t(item.labelKey)}
              </NavLink>
            ))}
          </nav>
        </aside>

        <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 md:py-8">
          <div className="max-w-6xl mx-auto">
            <Outlet context={{ user }} />
          </div>
        </main>
      </div>
    </div>
  );
}
