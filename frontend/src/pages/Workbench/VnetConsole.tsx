// /vnet-console -- login entry for the collection network admin console (<worker-center-host>/admin).
//
// That admin console has no user system and only recognizes a single GATEWAY_ADMIN_TOKEN; opening it directly shows a "paste the token" page.
// To "log in with a Vigilath account", that side's login page sends people here:
//   Not logged in -> open this site's login overlay; after login stay in place -> get the JWT
//   Logged in -> GET /admin/vnet-console (backend checks admin) -> URL with the key -> full-page redirect there
// Non-admins get 403 and stay on this page with the reason explained, no loop.
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthModal } from '../../contexts/AuthModalContext';
import { aiTelemetryApi } from '../../services/aiTelemetryApi';
import { Button } from '../../components/ui';
import { PageHead } from '../../components/PageHead';

export function VnetConsole() {
  const { t } = useTranslation();
  const { openAuthModal } = useAuthModal();
  const [err, setErr] = useState<string | null>(null);

  const go = useCallback(async () => {
    const token = localStorage.getItem('token') || '';
    setErr(null);
    try {
      const { url } = await aiTelemetryApi.adminVnetConsole(token);
      window.location.replace(url);
    } catch (e) {
      setErr((e as Error)?.message || '进入采集网络管理台失败');
    }
  }, []);

  useEffect(() => {
    if (localStorage.getItem('token')) { void go(); return; }
    // onSuccess: after login stay on this page, then redirect
    openAuthModal('login', { onSuccess: () => void go() });
  }, [go, openAuthModal]);

  return (
    <div className="min-h-[60vh] flex items-center justify-center p-6">
      <PageHead titleKey="vnetConsole.title" titleFallback="采集网络管理台" />
      <div className="text-center max-w-md">
        <h1 className="text-lg font-semibold mb-2">{t('vnetConsole.title', { defaultValue: '采集网络管理台' })}</h1>
        {err ? (
          <>
            <p className="text-sm text-muted mb-4">{err}</p>
            <p className="text-xs text-muted mb-4">
              {t('vnetConsole.hint', { defaultValue: '需要用 Vigilath 的 admin 账号登录;不是 admin 请联系管理员。' })}
            </p>
            <div className="flex gap-2 justify-center">
              <Button size="sm" variant="solid" onClick={() => void go()}>{t('common.retry', { defaultValue: '重试' })}</Button>
              <Button size="sm" variant="outline" onClick={() => openAuthModal('login', { onSuccess: () => void go() })}>
                {t('vnetConsole.switchAccount', { defaultValue: '换个账号登录' })}
              </Button>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted">{t('vnetConsole.entering', { defaultValue: '正在用 Vigilath 账号进入…' })}</p>
        )}
      </div>
    </div>
  );
}
