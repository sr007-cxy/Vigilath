/**
 * AuthContext — 全局唯一的 JWT token 状态源。
 *
 * 背景(bug 修复):原来每个 `useMembership()` 消费者有自己的 React state,
 * 初始化时从 localStorage 读一次,之后不再同步。结果:
 *   - <TierModal /> 在 App 根节点常驻,从应用启动就 mount
 *   - 用户注册后 localStorage 写入新 token
 *   - Register.tsx 不调 refresh(甚至不用 useMembership)
 *   - TierModal 的 token state 永远卡在 null → isLoggedIn=false
 *   - 用户选套餐 → 再次弹登录框
 *
 * 新的设计:
 *   - token 只在 AuthProvider 一个地方维护 React state
 *   - 所有 useMembership() / useAuth() 通过 Context 消费,单一真相
 *   - 写 token 一律走 `setToken(x)`,它负责更新 state + 同步 localStorage
 *   - AuthProvider 还挂 `storage` 事件监听,跨 tab 登录/退出也能同步
 *
 * 注意:user 对象(仅展示用的 email)继续用 localStorage 裸写,因为没有
 * 组件对其做响应式渲染。等哪天 header 要显示用户名再纳入 Context。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

interface AuthContextValue {
  /** 当前 JWT,未登录时为 null。 */
  token: string | null;
  /** 为 !!token 的便捷别名。 */
  isLoggedIn: boolean;
  /** 登录/注册成功后写入 token。传 null 表示退出。会同步 localStorage。 */
  setToken: (t: string | null) => void;
  /** 清空所有登录痕迹(token + user)。等价于 setToken(null) + 清 user。 */
  clearAuth: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

const TOKEN_KEY = 'token';
const USER_KEY = 'user';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      // 服务端渲染 / 浏览器禁用 localStorage 时落回 null
      return null;
    }
  });

  const setToken = useCallback((t: string | null) => {
    try {
      if (t) {
        localStorage.setItem(TOKEN_KEY, t);
      } else {
        localStorage.removeItem(TOKEN_KEY);
      }
    } catch {
      // 存储失败不能让业务挂掉,state 仍然更新
    }
    setTokenState(t);
  }, []);

  const clearAuth = useCallback(() => {
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    } catch {
      // ignore
    }
    setTokenState(null);
  }, []);

  // 跨 tab 同步:用户在 A tab 登录,B tab 也要看到。
  // 浏览器 `storage` 事件只在**其他 tab**触发,所以不会和本 tab 的
  // setToken 形成循环。
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === TOKEN_KEY) {
        setTokenState(e.newValue);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      isLoggedIn: !!token,
      setToken,
      clearAuth,
    }),
    [token, setToken, clearAuth],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within <AuthProvider>. Wrap your app in AuthProvider at the top level.');
  }
  return ctx;
}
