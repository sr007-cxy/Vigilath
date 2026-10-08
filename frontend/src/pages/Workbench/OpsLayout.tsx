// Admin workbench (admin / operator / maintainer) -- the sidebar shell is isomorphic with the client workspace
// (components/nav/SidebarShell.tsx), only the menu source fed in differs (constants/opsNav.ts).
//
// The three client items (topics / content library / optimization tasks) have moved to /app and no longer appear here.
// This layer only does: login check, role gating, route gating, and handing the menu source to the shell.
import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useLocation } from 'react-router-dom';
import { authApi } from '../../services/authApi';
import { useAuthModal } from '../../contexts/AuthModalContext';
import { useAuth, resolveRole } from '../../contexts/AuthContext';
import { PageHead } from '../../components/PageHead';
import { SidebarShell } from '../../components/nav/SidebarShell';
import { canAccessOpsPath, visibleOpsGroups, firstOpsPath, isOpsRole } from '../../constants/opsNav';

export function OpsLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { openAuthModal } = useAuthModal();
  const { user, setUser, clearAuth } = useAuth();
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
      .then(u => {
        if (cancelled) return;
        const role = resolveRole(u);
        // The admin workbench is only open to admin / operator / maintainer. Clients and agencies go to /app,
        // tenants are redirected to /gateway-portal by NonTenantOnly.
        if (!isOpsRole(role)) {
          navigate('/app', { replace: true });
          return;
        }
        setUser(u);
      })
      .catch(() => {
        clearAuth();
        openAuthModal('login');
        navigate('/', { replace: true });
      })
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [navigate, openAuthModal, setUser, clearAuth]);

  // Direct route gating -- derived from the menu source, not a second hand-written allowlist
  useEffect(() => {
    if (!user) return;
    const role = resolveRole(user);
    if (!canAccessOpsPath(role, location.pathname)) {
      navigate(firstOpsPath(role) || '/app', { replace: true });
    }
  }, [location.pathname, navigate, user]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2"
             style={{ borderColor: 'var(--accent-primary)' }} />
      </div>
    );
  }

  if (!user) return null;

  return (
    <>
      <PageHead titleKey="nav.opsTitle" descriptionKey="nav.opsTitle" />
      <SidebarShell
        groups={visibleOpsGroups(resolveRole(user))}
        titleKey="nav.opsTitle"
        storageKey="ops.nav"
        context={{ user }}
      />
    </>
  );
}

/** /ops landing page: admin/operator -> project progress; maintainer can't see it -> redirected to batch runs. */
export function OpsIndexRedirect() {
  const { user } = useAuth();
  const role = user ? resolveRole(user) : '';
  return <Navigate to={firstOpsPath(role) || '/app'} replace />;
}
