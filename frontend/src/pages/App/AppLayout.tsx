// Client workspace -- the sidebar shell is isomorphic with the admin workbench (components/nav/SidebarShell.tsx),
// only the menu source fed in differs (constants/userNav.ts).
//
// This layer does only three things: login check, role gating, and handing the menu source to the shell.
// How it looks, collapses and scrolls is all in SidebarShell.
import { useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { authApi } from '../../services/authApi';
import { useAuthModal } from '../../contexts/AuthModalContext';
import { useAuth, resolveRole } from '../../contexts/AuthContext';
import { SidebarShell } from '../../components/nav/SidebarShell';
import { isAppRole, visibleUserGroups, firstAppPath } from '../../constants/userNav';
import { isOpsRole, firstOpsPath } from '../../constants/opsNav';

export function AppLayout() {
  const navigate = useNavigate();
  const { openAuthModal } = useAuthModal();
  const { user, setUser, clearAuth } = useAuth();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      // New users identified by WeChat silent login (have openid, phone not yet bound) go straight to the phone tab to bind --
      // the default login tab auto-switches to the QR scan, and in mobile WeChat you can't scan your own screen; a dead end.
      let hasWxBind = false;
      try { hasWxBind = !!sessionStorage.getItem('wx_bind_token'); } catch { /* noop */ }
      openAuthModal(hasWxBind ? 'phone' : 'login');
      navigate('/', { replace: true });
      return;
    }
    let cancelled = false;
    authApi
      .getCurrentUser(token)
      .then(u => { if (!cancelled) setUser(u); })
      .catch(() => {
        clearAuth();
        openAuthModal('login');
        navigate('/', { replace: true });
      })
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [navigate, openAuthModal, setUser, clearAuth]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2"
             style={{ borderColor: 'var(--accent-primary)' }} />
      </div>
    );
  }

  if (!user) return null;

  const role = resolveRole(user);
  // Pure ops roles (maintainer) have no client view; send them back to the admin workbench.
  if (!isAppRole(role)) {
    return <Navigate to={isOpsRole(role) ? (firstOpsPath(role) || '/') : '/'} replace />;
  }

  return (
    <SidebarShell
      groups={visibleUserGroups(role)}
      titleKey="nav.appTitle"
      storageKey="app.nav"
      context={{ user }}
    />
  );
}

/** Bare /app path -> the role's first visible menu (for clients, "Topic profile"). */
export function AppIndexRedirect() {
  const { user } = useAuth();
  const role = user ? resolveRole(user) : '';
  return <Navigate to={firstAppPath(role) || '/app/topics'} replace />;
}
