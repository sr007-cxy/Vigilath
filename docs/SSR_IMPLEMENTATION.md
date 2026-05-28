# Hybrid SSG + CSR 实施技术文档

> 基于 `docs/SSR_PLAN.md` 方案，结合实际代码审计后的完整实施指南。
> 本文档修正了原方案中 React Router v7 API 变化、i18n 实际文件结构、
> 遗漏的 SSR 不兼容点等问题。

## 一、背景与目标

### 当前问题

纯 SPA 架构，用户访问首页需完成完整 JS 下载链：

```
React ~243KB + i18n ~42KB + 语言包 ~88KB + 路由 + 应用代码
  → 解析执行 → initI18n() → createRoot().render()
  → 这之前只有 #app-boot-skeleton 的 spinner
```

- **FCP（首次内容绘制）~2000ms**
- AI 爬虫（GPTBot / ClaudeBot / PerplexityBot 等不执行 JS）看到的是空壳 + SEO skeleton

### 目标

- 13 个静态路由 **FCP < 300ms**（用户进入即看到完整 DOM）
- **0 hydration mismatch**（React hydrate 不清空 DOM、不重渲染）
- **SEO 收益**：AI 爬虫直接抓到真实页面 DOM + helmet 动态 meta 标签
- **部署架构不变**：不需要 Node 运行时，nginx 继续托管静态文件

### 非目标

- 不做运行时 SSR（不启 Node 服务器）
- 不迁移 Next.js / Remix
- 不做已登录用户/非默认主题用户的 0 跳变

## 二、当前技术栈

| 技术 | 版本 | 备注 |
|---|---|---|
| React | 19.2.4 | |
| react-router-dom | 7.14.0 | v7 API，StaticRouter 从 `react-router` 导入 |
| react-helmet-async | 3.0.0 | 支持 `HelmetServerState` |
| i18next | 26.0.4 | 动态 import 加载语言包（TS 文件，非 JSON） |
| @tanstack/react-query | 5.96.2 | |
| Vite | 8.0.4 | |
| TypeScript | 6.0.2 | |
| 部署 | nginx:alpine | 静态文件托管 |

## 三、预渲染范围

### SSG 预渲染（13 个路由）

| 路由 | 组件 | 当前 import 方式 | 需改动 |
|---|---|---|---|
| `/` | Home | eager | 无 |
| `/checker` | Home | eager | 无 |
| `/geo-knowledge` | GeoKnowledge | `lazy()` | 改为同步 import |
| `/geo-knowledge/metrics` | GeoKnowledgeMetrics | `lazy()` | 改为同步 import |
| `/products-services` | ProductsServices | `lazy()` | 改为同步 import |
| `/about` | About | `lazy()` | 改为同步 import |
| `/contact` | Contact | `lazy()` | 改为同步 import |
| `/privacy` | PrivacyPolicy | `lazy()` | 改为同步 import |
| `/terms` | TermsOfUse | `lazy()` | 改为同步 import |
| `/cookie-policy` | CookiePolicy | `lazy()` | 改为同步 import |
| `/process` | Landing | `lazy()` | 改为同步 import |
| `/pricing` | Landing | `lazy()` | 改为同步 import |
| `/data` | Landing | `lazy()` | 改为同步 import |

> 注意：所有页面组件均为 **named export**（`export function Xxx`），非 default export。

### 保持 CSR（继续 lazy）

| 路由 | 原因 |
|---|---|
| `/result`、`/advanced/:mode` | 依赖用户输入 |
| `/account/*`、`/dashboard/*` | 需要登录 |
| `/login`、`/register`、`/forgot-password` | 认证流程 |
| `/checkout/*` | 支付流程 |

## 四、SSR 不兼容代码审计

### 阻断级（必须修复，否则 renderToString 崩溃或 hydration mismatch）

#### 4.1 `src/contexts/AuthContext.tsx:49-56`

**问题**：useState 初始值直接读 localStorage

```tsx
// 当前代码
const [token, setTokenState] = useState<string | null>(() => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
});
```

**修复**：加 `typeof window` 守卫

```tsx
// 修复后
const [token, setTokenState] = useState<string | null>(() => {
  if (typeof window === 'undefined') return null;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
});
```

**影响分析**：预渲染页面都是公开页面，服务端 token=null 是正确的。已登录用户 hydrate 后，跨 tab storage 事件监听（已有 useEffect）+ 本 tab 的 setToken 调用会正常更新状态。**无行为变化**。

---

#### 4.2 `src/components/Header.tsx:24-38`

**问题**：useState 初始值直接读 localStorage + console.log

```tsx
// 当前代码
const [user, setUser] = useState<string | null>(() => {
  try {
    const storedUser = localStorage.getItem('user');
    console.log('Header initial user:', storedUser);
    if (storedUser) {
      const parsedUser = JSON.parse(storedUser);
      console.log('Header initial parsedUser:', parsedUser);
      return parsedUser.email;
    }
  } catch (error) {
    console.error('Error parsing user from localStorage:', error);
    localStorage.removeItem('user');
  }
  return null;
});
```

**修复**：加 `typeof window` 守卫，顺便删除 debug console.log

```tsx
// 修复后
const [user, setUser] = useState<string | null>(() => {
  if (typeof window === 'undefined') return null;
  try {
    const storedUser = localStorage.getItem('user');
    if (storedUser) {
      const parsedUser = JSON.parse(storedUser);
      return parsedUser.email;
    }
  } catch {
    localStorage.removeItem('user');
  }
  return null;
});
```

**另外**：Header.tsx:130 有一个裸露的 `console.log('Header user state:', user);` 也应删除。

**影响分析**：服务端渲染为"未登录"态，与大多数首次访问用户一致。已登录用户 hydrate 后 1 秒内由已有的 `setInterval(loadUserFromLocalStorage, 1000)` 更新。**Header 按钮会短暂闪一下**（可接受）。

---

#### 4.3 `src/components/ThemeToggle.tsx:8-27`

**问题**：useState 初始值读 localStorage + 调用 `document.body.setAttribute`

```tsx
// 当前代码
const applyTheme = (t: Theme) => {
  document.body.setAttribute('data-theme', t);
  document.body.classList.remove('light-mode');
};

const getInitialTheme = (): Theme => {
  const saved = localStorage.getItem('theme');
  if (saved === 'peec' || saved === 'light' || saved === 'dark') {
    return saved;
  }
  return 'peec';
};

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    const value = getInitialTheme();
    applyTheme(value);    // ← 服务端没有 document.body!
    return value;
  });
```

**修复**：守卫 + 延迟到 useEffect

```tsx
// 修复后
const applyTheme = (t: Theme) => {
  if (typeof document !== 'undefined') {
    document.body.setAttribute('data-theme', t);
    document.body.classList.remove('light-mode');
  }
};

const getInitialTheme = (): Theme => {
  if (typeof window === 'undefined') return 'peec';
  const saved = localStorage.getItem('theme');
  if (saved === 'peec' || saved === 'light' || saved === 'dark') {
    return saved;
  }
  return 'peec';
};

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>('peec');

  // 客户端挂载后从 localStorage 读取真实主题
  useEffect(() => {
    const saved = getInitialTheme();
    setTheme(saved);
    applyTheme(saved);
  }, []);
```

**影响分析**：切过 dark/light 主题的用户首渲染显示 peec 主题，~10ms 后切换。**颜色短暂闪烁**（可接受，多数用户使用默认主题）。

---

#### 4.4 SSG 路由的 React.lazy() — `src/App.tsx:17-47`

**问题**：`renderToString` 不支持 `React.lazy()` + `Suspense`。所有 SSG 路由组件当前都是 lazy import。

```tsx
// 当前代码 — 这些在服务端 renderToString 时会失败
const Landing = lazy(() => import('./pages/Landing').then(m => ({ default: m.Landing })));
const Contact = lazy(() => import('./pages/Contact').then(m => ({ default: m.Contact })));
const GeoKnowledge = lazy(() => import('./pages/GeoKnowledge').then(m => ({ default: m.GeoKnowledge })));
// ...等 9 个 SSG 路由
```

**修复**：在 `AppShell.tsx` 中，SSG 路由的组件改为同步 import，CSR 路由保持 lazy。

```tsx
// AppShell.tsx — SSG 路由同步 import
import { Home } from './pages/Home';
import { Landing } from './pages/Landing';
import { Contact } from './pages/Contact';
import { GeoKnowledge } from './pages/GeoKnowledge';
import { GeoKnowledgeMetrics } from './pages/GeoKnowledgeMetrics';
import { ProductsServices } from './pages/ProductsServices';
import { About } from './pages/About';
import { PrivacyPolicy } from './pages/PrivacyPolicy';
import { TermsOfUse } from './pages/TermsOfUse';
import { CookiePolicy } from './pages/CookiePolicy';

// CSR 路由保持 lazy（不影响 SSG）
const Result = lazy(() => import('./pages/Result').then(m => ({ default: m.Result })));
const Advanced = lazy(() => import('./pages/Advanced').then(m => ({ default: m.Advanced })));
// ...其他 CSR 路由
```

**影响分析**：SSG 路由组件不再 code-split，会增加主 bundle 体积。但这些都是静态页面组件，体积较小。CSR 路由（Result、Account、Dashboard 等较重的组件）仍然 lazy，不影响首屏加载。

### 安全级（已有守卫或仅在 event handler 中使用）

| 文件 | 代码 | 状态 |
|---|---|---|
| `i18n/index.ts:13` | `localStorage.getItem('i18nextLng')` | 已有 try/catch，且服务端用独立的 `i18n/server.ts` 初始化，不走此路径 |
| `i18n/index.ts:18-19` | `typeof navigator !== 'undefined'` | 已有守卫 ✅ |
| `Landing.tsx:17` | `document.getElementById(id)` | 在 `scrollToSection` 函数内，仅由 click handler 调用，服务端不触发 ✅ |
| `Header.tsx:94-100` | `document.body.style.overflow` | 在 useEffect 内 ✅ |
| `Header.tsx:107-120` | `document.addEventListener('mousedown')` | 在 useEffect 内 ✅ |
| `AuthModal.tsx:33-57` | `document` 操作 | 在 useEffect 内 ✅ |

## 五、实施步骤（含完整代码）

### Step 1: 拆分入口文件

#### 1.1 新建 `src/entry-client.tsx`（客户端入口）

```tsx
// src/entry-client.tsx
import { StrictMode } from 'react'
import { hydrateRoot, createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { initI18n } from './i18n'

initI18n().then(() => {
  const root = document.getElementById('root')!
  const isSSR = root.hasAttribute('data-ssr')

  const app = (
    <StrictMode>
      <App />
    </StrictMode>
  )

  if (isSSR) {
    // dist/index.html 被预渲染过 → hydrate 复用已有 DOM
    hydrateRoot(root, app)
  } else {
    // dev 模式 或 未预渲染的 CSR 页面 → 普通渲染
    createRoot(root).render(app)
  }
})
```

#### 1.2 更新 `index.html` 的 script 入口

```html
<!-- 改动前 -->
<script type="module" src="/src/main.tsx"></script>

<!-- 改动后 -->
<script type="module" src="/src/entry-client.tsx"></script>
```

#### 1.3 保留 `src/main.tsx`

`main.tsx` 不删除，保持原样，避免影响其他可能的引用。
后续确认无引用后可删除。或者直接将 `main.tsx` 内容替换为 re-export：

```tsx
// src/main.tsx — 兼容入口，实际工作交给 entry-client
export {} // 空文件，由 entry-client.tsx 取代
```

**涉及文件**：
- 新建 `frontend/src/entry-client.tsx`
- 修改 `frontend/index.html`（script src）
- 保留 `frontend/src/main.tsx`（可选删除）

---

### Step 2: 重构 App.tsx — 抽取 AppShell

#### 2.1 新建 `src/AppShell.tsx`

从 `App.tsx` 抽取 Router **内部**的所有内容（Header + Routes + Footer + Providers），
使 client 和 server 可以各自包裹不同类型的 Router。

```tsx
// src/AppShell.tsx
import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Header } from './components/Header';
import { Footer } from './components/Footer';
import { ContactModalProvider } from './components/ContactModalContext';
import { ContactModal } from './components/ContactModal';
import { TierModalProvider } from './components/TierModalContext';
import { TierModal } from './components/TierModal';
import { AuthProvider } from './contexts/AuthContext';

// ── SSG 路由：同步 import（renderToString 不支持 lazy） ──
import { Home } from './pages/Home';
import { Landing } from './pages/Landing';
import { Contact } from './pages/Contact';
import { GeoKnowledge } from './pages/GeoKnowledge';
import { GeoKnowledgeMetrics } from './pages/GeoKnowledgeMetrics';
import { ProductsServices } from './pages/ProductsServices';
import { About } from './pages/About';
import { PrivacyPolicy } from './pages/PrivacyPolicy';
import { TermsOfUse } from './pages/TermsOfUse';
import { CookiePolicy } from './pages/CookiePolicy';

// ── CSR 路由：保持 lazy（auth-gated 或用户输入驱动） ──
const Result = lazy(() => import('./pages/Result').then(m => ({ default: m.Result })));
const Advanced = lazy(() => import('./pages/Advanced').then(m => ({ default: m.Advanced })));
const Login = lazy(() => import('./pages/Login').then(m => ({ default: m.Login })));
const Register = lazy(() => import('./pages/Register').then(m => ({ default: m.Register })));
const ForgotPassword = lazy(() => import('./pages/ForgotPassword').then(m => ({ default: m.ForgotPassword })));
const CheckoutSuccess = lazy(() => import('./pages/CheckoutSuccess').then(m => ({ default: m.CheckoutSuccess })));
const CheckoutCancel = lazy(() => import('./pages/CheckoutCancel').then(m => ({ default: m.CheckoutCancel })));
const CheckoutPending = lazy(() => import('./pages/CheckoutPending').then(m => ({ default: m.CheckoutPending })));
const AccountLayout = lazy(() => import('./pages/Account/AccountLayout').then(m => ({ default: m.AccountLayout })));
const ProfileTab = lazy(() => import('./pages/Account/ProfileTab').then(m => ({ default: m.ProfileTab })));
const MembershipTab = lazy(() => import('./pages/Account/MembershipTab').then(m => ({ default: m.MembershipTab })));
const UsageTab = lazy(() => import('./pages/Account/UsageTab').then(m => ({ default: m.UsageTab })));
const HistoryTab = lazy(() => import('./pages/Account/HistoryTab').then(m => ({ default: m.HistoryTab })));
const PaymentsTab = lazy(() => import('./pages/Account/PaymentsTab').then(m => ({ default: m.PaymentsTab })));
const DashboardLayout = lazy(() => import('./pages/Dashboard/DashboardLayout').then(m => ({ default: m.DashboardLayout })));
const DashboardHome = lazy(() => import('./pages/Dashboard/DashboardHome').then(m => ({ default: m.DashboardHome })));
const Compose = lazy(() => import('./pages/Dashboard/Compose').then(m => ({ default: m.Compose })));
const DashboardInbox = lazy(() => import('./pages/Dashboard/Inbox').then(m => ({ default: m.Inbox })));
const DashboardPosts = lazy(() => import('./pages/Dashboard/Posts').then(m => ({ default: m.Posts })));
const DashboardStats = lazy(() => import('./pages/Dashboard/Stats').then(m => ({ default: m.Stats })));
const PolicyEditor = lazy(() => import('./pages/Dashboard/PolicyEditor').then(m => ({ default: m.PolicyEditor })));
const PlatformAccounts = lazy(() => import('./pages/Dashboard/PlatformAccounts').then(m => ({ default: m.PlatformAccounts })));

function PageLoader() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <div
        className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2"
        style={{ borderColor: 'var(--accent-primary)' }}
      />
    </div>
  );
}

export function AppShell() {
  return (
    <AuthProvider>
      <ContactModalProvider>
        <TierModalProvider>
          <Suspense fallback={<PageLoader />}>
            <Header />
            <ContactModal />
            <TierModal />
            <div className="pt-16 min-h-screen flex flex-col">
              <Suspense fallback={<PageLoader />}>
                <Routes>
                  {/* ── SSG 路由 ── */}
                  <Route path="/" element={<Home />} />
                  <Route path="/checker" element={<Home />} />
                  <Route path="/geo-knowledge" element={<GeoKnowledge />} />
                  <Route path="/geo-knowledge/metrics" element={<GeoKnowledgeMetrics />} />
                  <Route path="/products-services" element={<ProductsServices />} />
                  <Route path="/about" element={<About />} />
                  <Route path="/process" element={<Landing />} />
                  <Route path="/pricing" element={<Landing />} />
                  <Route path="/data" element={<Landing />} />
                  <Route path="/contact" element={<Contact />} />
                  <Route path="/privacy" element={<PrivacyPolicy />} />
                  <Route path="/terms" element={<TermsOfUse />} />
                  <Route path="/cookie-policy" element={<CookiePolicy />} />

                  {/* ── CSR 路由 ── */}
                  <Route path="/result" element={<Result />} />
                  <Route path="/advanced/:mode" element={<Advanced />} />
                  <Route path="/login" element={<Login />} />
                  <Route path="/register" element={<Register />} />
                  <Route path="/forgot-password" element={<ForgotPassword />} />
                  <Route path="/checkout/pending" element={<CheckoutPending />} />
                  <Route path="/checkout/success" element={<CheckoutSuccess />} />
                  <Route path="/checkout/cancel" element={<CheckoutCancel />} />
                  <Route path="/account" element={<AccountLayout />}>
                    <Route index element={<Navigate to="profile" replace />} />
                    <Route path="profile" element={<ProfileTab />} />
                    <Route path="membership" element={<MembershipTab />} />
                    <Route path="usage" element={<UsageTab />} />
                    <Route path="history" element={<HistoryTab />} />
                    <Route path="payments" element={<PaymentsTab />} />
                  </Route>
                  <Route path="/dashboard" element={<DashboardLayout />}>
                    <Route index element={<DashboardHome />} />
                    <Route path="compose" element={<Compose />} />
                    <Route path="inbox" element={<DashboardInbox />} />
                    <Route path="posts" element={<DashboardPosts />} />
                    <Route path="stats" element={<DashboardStats />} />
                    <Route path="policy" element={<PolicyEditor />} />
                    <Route path="accounts" element={<PlatformAccounts />} />
                  </Route>
                </Routes>
              </Suspense>
              <Footer />
            </div>
          </Suspense>
        </TierModalProvider>
      </ContactModalProvider>
    </AuthProvider>
  );
}
```

#### 2.2 简化 `src/App.tsx`（客户端专用，包裹 BrowserRouter）

```tsx
// src/App.tsx
import { BrowserRouter as Router } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HelmetProvider } from 'react-helmet-async';
import { AppShell } from './AppShell';

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HelmetProvider>
        <Router>
          <AppShell />
        </Router>
      </HelmetProvider>
    </QueryClientProvider>
  );
}

export default App;
```

**涉及文件**：
- 新建 `frontend/src/AppShell.tsx`
- 重构 `frontend/src/App.tsx`

---

### Step 3: 创建 entry-server.tsx + i18n/server.ts

#### 3.1 新建 `src/i18n/server.ts`

当前项目的 i18n 语言包是 **TypeScript 文件**（非 JSON），使用 3 个 namespace（main / result / knowledge）。
服务端只需 main namespace（SSG 页面不使用 result / knowledge namespace）。

```tsx
// src/i18n/server.ts
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './en';
import zh from './zh';

export async function initI18nServer(lng: 'en' | 'zh') {
  if (i18n.isInitialized) {
    await i18n.changeLanguage(lng);
    return;
  }
  await i18n.use(initReactI18next).init({
    lng,
    resources: {
      en: { translation: en },
      zh: { translation: zh },
    },
    defaultNS: 'translation',
    interpolation: { escapeValue: false },
  });
}
```

> 注意：客户端 `i18n/index.ts` 使用 `addResourceBundle` 动态加载，defaultNS 是 `'translation'`。
> 服务端必须用相同的 NS 名（`'translation'`）以确保 `useTranslation()` 的 key 路径一致。

#### 3.2 新建 `src/entry-server.tsx`

```tsx
// src/entry-server.tsx
import { renderToString } from 'react-dom/server';
import { StaticRouter } from 'react-router';  // ← v7: 从 react-router 导入，不是 react-router-dom/server
import { HelmetProvider } from 'react-helmet-async';
import type { HelmetServerState } from 'react-helmet-async';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from './AppShell';
import { initI18nServer } from './i18n/server';

export interface RenderResult {
  html: string;
  helmet: HelmetServerState;
}

export async function render(url: string, lang: 'en' | 'zh'): Promise<RenderResult> {
  await initI18nServer(lang);

  const helmetContext: { helmet?: HelmetServerState } = {};
  const queryClient = new QueryClient();

  const html = renderToString(
    <QueryClientProvider client={queryClient}>
      <HelmetProvider context={helmetContext}>
        <StaticRouter location={url}>
          <AppShell />
        </StaticRouter>
      </HelmetProvider>
    </QueryClientProvider>
  );

  return { html, helmet: helmetContext.helmet! };
}
```

**与原 SSR_PLAN.md 的差异**：
- `StaticRouter` 从 `react-router` 导入（v7），不是 `react-router-dom/server`（v6）
- i18n 语言包是 TS 文件，`import en from './en'` 而非 JSON

**涉及文件**：
- 新建 `frontend/src/entry-server.tsx`
- 新建 `frontend/src/i18n/server.ts`

---

### Step 4: 修复 SSR 不兼容

按上面第四章审计结果修复 3 个文件：

| 文件 | 修改点 |
|---|---|
| `src/contexts/AuthContext.tsx:49` | 加 `typeof window === 'undefined'` 守卫 |
| `src/components/Header.tsx:24` | 加 `typeof window === 'undefined'` 守卫 + 删 console.log（:28, :30, :130） |
| `src/components/ThemeToggle.tsx:8,13,23` | applyTheme 加 document 守卫，getInitialTheme 加 window 守卫，useState 改为固定 `'peec'` + useEffect 初始化 |

具体代码见第四章的每个修复方案。

**涉及文件**：
- `frontend/src/contexts/AuthContext.tsx`
- `frontend/src/components/Header.tsx`
- `frontend/src/components/ThemeToggle.tsx`

---

### Step 5: 创建预渲染脚本

```js
// frontend/scripts/prerender.mjs
import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));

const PRERENDER_ROUTES = [
  '/',
  '/checker',
  '/geo-knowledge',
  '/geo-knowledge/metrics',
  '/products-services',
  '/about',
  '/contact',
  '/privacy',
  '/terms',
  '/cookie-policy',
  '/process',
  '/pricing',
  '/data',
];

async function prerender() {
  // 加载 Vite SSR build 产物
  const { render } = await import('../dist/server/entry-server.js');
  const distDir = join(__dirname, '..', 'dist');
  const template = readFileSync(join(distDir, 'index.html'), 'utf-8');

  console.log('Pre-rendering SSG routes...\n');

  for (const route of PRERENDER_ROUTES) {
    const { html, helmet } = await render(route, 'en');

    let page = template;

    // 1. 注入预渲染内容，替换 boot skeleton + SEO skeleton
    //    用 data-ssr 标记让客户端知道该走 hydrateRoot
    page = page.replace(
      /<div id="root">[\s\S]*?<\/div>(\s*<script)/,
      `<div id="root" data-ssr="true">${html}</div>$1`
    );

    // 2. 注入 helmet meta 标签（动态 title / description）
    if (helmet) {
      const helmetHead = [
        helmet.title.toString(),
        helmet.meta.toString(),
        helmet.link.toString(),
      ].filter(Boolean).join('\n    ');

      if (helmetHead) {
        page = page.replace('</head>', `    ${helmetHead}\n  </head>`);
      }
    }

    // 3. 注入 SSR 语言标记，客户端用此值初始化 i18n 以避免 mismatch
    page = page.replace(
      '<script type="module"',
      '<script>window.__SSR_LANG__="en"</script>\n  <script type="module"'
    );

    // 4. 写入对应路由目录
    if (route === '/') {
      writeFileSync(join(distDir, 'index.html'), page);
      console.log(`  ✓ ${route} → dist/index.html`);
    } else {
      const dir = join(distDir, route.slice(1));
      mkdirSync(dir, { recursive: true });
      writeFileSync(join(dir, 'index.html'), page);
      console.log(`  ✓ ${route} → dist${route}/index.html`);
    }
  }

  console.log(`\nDone! Pre-rendered ${PRERENDER_ROUTES.length} routes.`);
}

prerender().catch((err) => {
  console.error('Pre-render failed:', err);
  process.exit(1);
});
```

**注意**：
- `/checker` 和 `/` 使用相同组件 Home，但 helmet 输出的 title/description 不同（由 PageHead 组件 + i18n key 决定）
- 预渲染脚本产出的 HTML 中保留了原有的 `<head>` 静态 SEO 元数据 + helmet 动态注入的元数据
- `vite.config.ts` 中现有的 `seoPages()` 插件会被预渲染覆盖（两者都往 dist/ 写文件，prerender 后执行所以胜出）

**涉及文件**：新建 `frontend/scripts/prerender.mjs`

---

### Step 6: 更新构建配置

#### 6.1 `vite.config.ts` — 添加 SSR 配置

```ts
// 在 defineConfig 中添加
ssr: {
  // react-helmet-async 内部有 CJS 引用，需要 Vite 处理
  noExternal: ['react-helmet-async'],
},
```

另外需要把 `build.rollupOptions.input` 指向 `entry-client.tsx`（如果 vite 默认读 index.html 中的 script src 则无需额外配置，因为 Step 1 已改了 index.html）。

#### 6.2 `package.json` — 更新 build 命令

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build && vite build --ssr src/entry-server.tsx --outDir dist/server && node scripts/prerender.mjs",
    "build:client": "tsc -b && vite build",
    "build:server": "vite build --ssr src/entry-server.tsx --outDir dist/server",
    "prerender": "node scripts/prerender.mjs",
    "lint": "eslint .",
    "preview": "vite preview"
  }
}
```

构建流程：
1. `tsc -b` — 类型检查
2. `vite build` — 构建客户端 bundle → `dist/`
3. `vite build --ssr` — 构建服务端 bundle → `dist/server/`
4. `node scripts/prerender.mjs` — 加载服务端 bundle，渲染 13 个路由，写回 `dist/`

#### 6.3 TypeScript 配置

`entry-server.tsx` 导入了 `react-dom/server`（服务端 API），当前 `tsconfig.app.json` 的 `lib` 包含 `DOM`，可以编译通过。但如果严格区分，可以不动 tsconfig（Vite SSR build 有自己的 resolve 逻辑）。

**涉及文件**：
- `frontend/vite.config.ts`
- `frontend/package.json`

---

### Step 7: Docker / Nginx — 无需改动

- **Dockerfile**：构建阶段已有 Node，`npm run build` 新命令会自动执行 SSR build + prerender
- **nginx.conf**：`try_files $uri $uri/ /index.html` 已支持路由级目录下的 `index.html`
  - 访问 `/geo-knowledge` → 先尝试 `dist/geo-knowledge/index.html`（预渲染版）✅
  - 访问 `/result` → 没有对应目录，fallback 到 `dist/index.html`（CSR）✅
- **部署流程完全不变**

## 六、与现有 seoPages 插件的关系

当前 `vite.config.ts` 中有一个 `seoPages()` 插件，它在 `closeBundle` 阶段：
- 为 7 个路由（geo-knowledge、products-services、about、privacy、terms、contact、membership）
  生成独立的 `index.html`，替换 `<title>`、`<meta>`、`<article>` 中的 SEO skeleton 内容

SSR 预渲染**完全覆盖** seoPages 的功能：
- 预渲染的 HTML 包含**真实 React 组件渲染出的完整 DOM**，不是 skeleton
- Helmet 动态注入每个路由的 `<title>` 和 `<meta description>`
- AI 爬虫看到的内容与真实用户体验一致

**建议**：SSR 方案上线验证后，**移除 seoPages 插件**。过渡期可保留（prerender 后执行会覆盖 seoPages 的产物，不冲突）。

## 七、Hydration Mismatch 风险矩阵

| 风险点 | 服务端输出 | 客户端首次渲染 | 是否 match | 对策 |
|---|---|---|---|---|
| AuthContext token | `null` | `null`（守卫后） → useEffect 读 localStorage | ✅ match | 守卫 |
| Header user | `null` | `null`（守卫后） → setInterval 1s 后更新 | ✅ match | 守卫 |
| ThemeToggle | `'peec'` | `'peec'`（固定） → useEffect 后切换 | ✅ match | 固定默认值 |
| i18n 语言 | `'en'`（server 固定） | `'en'`（`window.__SSR_LANG__`） | ✅ match | 语言标记 |
| `useLocation` | StaticRouter 提供正确 path | BrowserRouter 提供正确 path | ✅ match | 自动 |
| lazy() CSR 组件 | 不渲染（SSG 不访问 CSR 路由） | lazy 正常工作 | N/A | 不涉及 |
| Date / Math.random | 未使用 | 未使用 | N/A | 不涉及 |

## 八、改动文件汇总

| 文件 | 操作 | 改动大小 |
|---|---|---|
| `frontend/src/entry-client.tsx` | **新建** | ~25 行 |
| `frontend/src/entry-server.tsx` | **新建** | ~35 行 |
| `frontend/src/AppShell.tsx` | **新建**，从 App.tsx 抽取 | ~120 行 |
| `frontend/src/i18n/server.ts` | **新建** | ~20 行 |
| `frontend/scripts/prerender.mjs` | **新建** | ~60 行 |
| `frontend/src/App.tsx` | **重构**，使用 AppShell | 缩减至 ~20 行 |
| `frontend/src/contexts/AuthContext.tsx` | **修改** 1 行（加守卫） | 极小 |
| `frontend/src/components/Header.tsx` | **修改** 守卫 + 删 console.log | 小 |
| `frontend/src/components/ThemeToggle.tsx` | **修改** 守卫 + useEffect | 小 |
| `frontend/index.html` | **修改** script src | 1 行 |
| `frontend/vite.config.ts` | **修改** 加 ssr 配置 | 3 行 |
| `frontend/package.json` | **修改** build 命令 | 4 行 |
| `frontend/src/main.tsx` | **保留或删除** | 不改 |
| `frontend/Dockerfile` | 无需改动 | — |
| `frontend/nginx.conf` | 无需改动 | — |

## 九、验证清单

### 构建验证

- [ ] `npm run build` 全流程通过（tsc → client build → server build → prerender）
- [ ] `dist/index.html` 包含真实首页 HTML（非 spinner），有 `data-ssr="true"`
- [ ] `dist/geo-knowledge/index.html` 等 12 个子目录都有预渲染 HTML
- [ ] 预渲染 HTML 中 helmet 注入了正确的 `<title>` 和 `<meta description>`

### 功能验证

- [ ] `npx serve dist` → 禁用 JS → 所有 13 个预渲染页面内容可见
- [ ] 开启 JS → **Console 无 hydration 警告**
- [ ] 所有交互正常（导航、语言切换、主题切换、表单提交）
- [ ] CSR 页面（/result、/account、/dashboard）不受影响
- [ ] 已登录用户访问预渲染页面，Header 正常显示用户名

### 性能验证

| 指标 | 当前 | 目标 |
|---|---|---|
| FCP | ~2000ms | **< 300ms** |
| LCP | ~2500ms | **< 500ms** |
| TTI | ~3000ms | 不变（JS 加载时间相同） |

### SEO 验证

```bash
# 部署后
curl -s https://www.vigilath.com/ | grep -c '<div id="root" data-ssr'
# 应输出 1

curl -s https://www.vigilath.com/geo-knowledge/ | grep '<title>'
# 应输出 GEO Knowledge Base 的标题，不是默认的 Vigilath 标题
```

## 十、回滚方案

所有改动集中在一个分支。回滚：

```bash
# 方法 1：git revert
git revert <ssr-commit-sha>
npm run build     # 退化为纯 CSR build（entry-client 的 createRoot fallback）
sudo rsync -a --delete frontend/dist/ /var/www/html/www.vigilath.com/

# 方法 2：恢复 index.html 的 script src 为 main.tsx
# 然后 npm run build（不执行 SSR build + prerender 步骤）
```

`entry-client.tsx` 的 hydrate/create 判断自带 fallback：
- 如果 `dist/index.html` 没有 `data-ssr` 属性 → 自动走 `createRoot`
- 所以即便 prerender 脚本失败，client build 仍会产出可用的 dist（退化为当前行为）

## 十一、后续优化（本次不做）

1. **中文版预渲染**：prerender 脚本遍历 `['en', 'zh']`，生成 `/zh/` 前缀路由，nginx 按 `Accept-Language` 路由
2. **移除 seoPages 插件**：SSR 上线后验证爬虫抓取正常，再删除 vite.config.ts 中的 seoPages 函数
3. **Critical CSS inline**：把首屏 CSS 内联进 `<head>`，省一次 CSS 下载
4. **更多路由 SSG**：如果新增静态页面，只需在 `PRERENDER_ROUTES` 数组里加一行
