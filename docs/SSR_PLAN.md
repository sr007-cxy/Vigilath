# SSR 方案：Hybrid SSG + CSR（构建时预渲染）

## 背景

首页打开慢的根本原因：当前是纯 SPA，用户访问首页需要完成完整的 JS 下载链（React ~243KB + i18n ~42KB + 语言包 ~88KB + 路由 + 应用代码）→ 解析执行 → `initI18n()` → `createRoot().render()` 后才能看到真实内容。在此之前只有 `#app-boot-skeleton` 的 spinner。

**方案核心**：构建时用 `renderToString` 预渲染静态页面的 HTML，Nginx 直接返回带真实内容的 HTML。浏览器立即绘制首屏，JS 在后台 hydrate。**不需要 Node 运行时，不改变部署架构**。

## 方案对比

| 方案 | 迁移成本 | 运行时复杂度 | FCP 提升 | Docker 改动 | 结论 |
|---|---|---|---|---|---|
| Vite SSR（Node 运行时） | 高 | Node 服务器 | 所有页面 | 换容器类型 | 首页无动态数据，运行时 SSR 无额外收益 |
| React Router v7 Framework Mode | 极高 | Node 服务器 | 所有页面 | 换容器类型 | 本质 Remix 重写，Auth 需改 cookie-based |
| Full SSG（全量预渲染） | 中 | 静态文件 | 静态页面 | 无 | 可行但动态页面无法预渲染 |
| **Hybrid SSG + CSR** | **中** | **静态文件** | **关键页面** | **无** | **推荐：精确覆盖，增量推进** |

## 预渲染范围

### SSG 预渲染（无 auth、无动态数据、SEO 关键）

| 路由 | 组件 | 说明 |
|---|---|---|
| `/`、`/checker` | Home | 主要痛点 |
| `/geo-knowledge` | GeoKnowledge | 知识库 |
| `/geo-knowledge/metrics` | GeoKnowledgeMetrics | 指标说明 |
| `/products-services` | ProductsServices | 产品定价 |
| `/about` | About | 关于我们 |
| `/contact` | Contact | 联系我们 |
| `/privacy`、`/terms`、`/cookie-policy` | 法律页面 | 合规内容 |
| `/process`、`/pricing`、`/data` | Landing | 落地页变体 |

### 保持 CSR（auth-gated 或用户输入驱动）

- `/result`、`/advanced/:mode` — 依赖用户输入
- `/account/*`、`/dashboard/*` — 需要登录
- `/login`、`/register`、`/forgot-password` — 认证流程
- `/checkout/*` — 支付流程

## 实施步骤

### Step 1: 拆分入口文件

将 `src/main.tsx` 拆为两个入口：

#### `src/entry-client.tsx`（客户端入口）

```tsx
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
    hydrateRoot(root, app)       // 复用服务端 HTML
  } else {
    createRoot(root).render(app) // CSR 回退
  }
})
```

#### `src/entry-server.tsx`（构建时入口）

```tsx
import { renderToString } from 'react-dom/server'
import { StaticRouter } from 'react-router-dom/server'
import { HelmetProvider, HelmetServerState } from 'react-helmet-async'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppShell } from './AppShell'           // 从 App.tsx 抽取的无 Router 版本
import { initI18nServer } from './i18n/server'

export interface RenderResult {
  html: string
  helmet: HelmetServerState
}

export async function render(url: string, lang: 'en' | 'zh'): Promise<RenderResult> {
  await initI18nServer(lang)

  const helmetContext: { helmet?: HelmetServerState } = {}
  const queryClient = new QueryClient()

  const html = renderToString(
    <QueryClientProvider client={queryClient}>
      <HelmetProvider context={helmetContext}>
        <StaticRouter location={url}>
          <AppShell />
        </StaticRouter>
      </HelmetProvider>
    </QueryClientProvider>
  )

  return { html, helmet: helmetContext.helmet! }
}
```

**涉及文件**：
- `frontend/src/main.tsx` → 改为 `entry-client.tsx`
- 新建 `frontend/src/entry-server.tsx`

### Step 2: 重构 App.tsx

将 App.tsx 中 Router 以内的部分抽取为 `AppShell` 组件，使 client 和 server 可以各自包裹不同的 Router：

```tsx
// src/AppShell.tsx — 路由内容（Header + Routes + Footer）
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
                  {/* ...所有路由... */}
                </Routes>
              </Suspense>
              <Footer />
            </div>
          </Suspense>
        </TierModalProvider>
      </ContactModalProvider>
    </AuthProvider>
  )
}

// src/App.tsx — 客户端专用，包裹 BrowserRouter
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HelmetProvider>
        <Router>
          <AppShell />
        </Router>
      </HelmetProvider>
    </QueryClientProvider>
  )
}
```

**涉及文件**：
- `frontend/src/App.tsx` → 抽取 `AppShell`
- 新建 `frontend/src/AppShell.tsx`

### Step 3: i18n 服务端适配

在 `src/i18n/` 下新建 `server.ts`，提供同步初始化：

```tsx
// src/i18n/server.ts
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en/main.json'
import zh from './locales/zh/main.json'

const resources = { en: { main: en }, zh: { main: zh } }

export async function initI18nServer(lng: 'en' | 'zh') {
  if (i18n.isInitialized) {
    await i18n.changeLanguage(lng)
    return
  }
  await i18n.use(initReactI18next).init({
    lng,
    resources,
    defaultNS: 'main',
    interpolation: { escapeValue: false },
  })
}
```

**涉及文件**：新建 `frontend/src/i18n/server.ts`

### Step 4: AuthContext 服务端兼容

```tsx
// 在 AuthContext.tsx 的 useState 初始化器中
const [token, setToken] = useState<string | null>(() => {
  if (typeof window === 'undefined') return null  // ← 新增守卫
  return localStorage.getItem('token')
})
```

预渲染页面都是公开页面，服务端 token=null（未登录）是正确的。已登录用户 hydrate 后 Auth 状态自动更新。

**涉及文件**：`frontend/src/contexts/AuthContext.tsx`

### Step 5: 创建预渲染脚本

```tsx
// frontend/scripts/prerender.ts
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { join, dirname } from 'path'

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
]

async function prerender() {
  // 加载 SSR bundle
  const { render } = await import('../dist/server/entry-server.js')
  const distDir = join(process.cwd(), 'dist')
  const template = readFileSync(join(distDir, 'index.html'), 'utf-8')

  for (const route of PRERENDER_ROUTES) {
    const { html, helmet } = await render(route, 'en')

    let page = template
    // 注入预渲染内容，替换 boot skeleton
    page = page.replace(
      /<div id="root">[\s\S]*?<\/div>\s*<script/,
      `<div id="root" data-ssr="true">${html}</div>\n  <script`
    )
    // 注入 helmet meta 标签
    if (helmet) {
      page = page.replace('</head>', `${helmet.title.toString()}${helmet.meta.toString()}</head>`)
    }
    // 注入 SSR 语言标记
    page = page.replace(
      '<script type="module"',
      '<script>window.__SSR_LANG__="en"</script>\n  <script type="module"'
    )

    // 写入对应路由目录
    if (route === '/') {
      writeFileSync(join(distDir, 'index.html'), page)
    } else {
      const dir = join(distDir, route.slice(1))
      mkdirSync(dir, { recursive: true })
      writeFileSync(join(dir, 'index.html'), page)
    }

    console.log(`  Pre-rendered: ${route}`)
  }
}

prerender().catch(console.error)
```

**涉及文件**：新建 `frontend/scripts/prerender.ts`

### Step 6: 更新构建配置

#### vite.config.ts 添加 SSR 配置

```ts
// 在 defineConfig 中添加
ssr: {
  noExternal: ['react-helmet-async'],
},
```

#### package.json 更新 build 命令

```json
{
  "scripts": {
    "build": "tsc -b && vite build && vite build --ssr src/entry-server.tsx --outDir dist/server && node scripts/prerender.js",
    "build:client": "tsc -b && vite build",
    "build:ssr": "vite build --ssr src/entry-server.tsx --outDir dist/server",
    "prerender": "node dist/server/prerender.js"
  }
}
```

**涉及文件**：
- `frontend/vite.config.ts`
- `frontend/package.json`

### Step 7: Docker / Nginx — 无需改动

- **Dockerfile**：构建阶段已有 Node 18，预渲染在 `npm run build` 中完成，运行时仍是 `nginx:alpine`
- **nginx.conf**：`try_files $uri $uri/ /index.html` 已支持路由级 HTML
- **部署流程完全不变**

## Hydration Mismatch 风险与对策

| 风险点 | 对策 |
|---|---|
| AuthContext 读 localStorage | 服务端返回 null，客户端首次渲染也返回 null（useEffect 中再读），一致 |
| i18n 语言不匹配 | HTML 中嵌入 `window.__SSR_LANG__='en'`，客户端用此值初始化 |
| `useMembership()` hook | 默认值 = 未登录/free tier，服务端客户端一致 |
| `useLocation` / `useNavigate` | StaticRouter 提供正确 location，无 mismatch |
| Date/Random | Home.tsx 中未使用 |
| `lazy()` 组件 | 服务端用 `renderToString` 不支持 Suspense lazy — 预渲染的路由组件需改为同步 import |

## 验证清单

- [ ] `npm run build` 后检查 `dist/index.html` 包含真实首页 HTML（非 spinner）
- [ ] 本地 `npx serve dist` 后禁用 JS，验证首页内容可见
- [ ] Lighthouse 对比 FCP / LCP / TTI（预期 FCP 提升 1-3 秒）
- [ ] 开启 JS 后验证 hydration 无控制台报错、交互正常
- [ ] 验证 CSR 页面（/result, /account）不受影响
- [ ] 已登录用户访问预渲染页面，header 正常切换为用户名
- [ ] Docker 构建测试，确认镜像大小和启动时间无明显变化

## 改动文件汇总

| 文件 | 操作 |
|---|---|
| `frontend/src/main.tsx` | 重命名为 `entry-client.tsx`，加 hydrateRoot 逻辑 |
| `frontend/src/entry-server.tsx` | 新建 |
| `frontend/src/AppShell.tsx` | 新建，从 App.tsx 抽取 |
| `frontend/src/App.tsx` | 重构，使用 AppShell |
| `frontend/src/i18n/server.ts` | 新建，服务端 i18n 初始化 |
| `frontend/src/contexts/AuthContext.tsx` | 加 `typeof window` 守卫 |
| `frontend/scripts/prerender.ts` | 新建预渲染脚本 |
| `frontend/vite.config.ts` | 添加 SSR 配置 |
| `frontend/package.json` | 更新 build 命令 |
| `frontend/index.html` | 无需改动（预渲染脚本覆盖 dist 产物） |
| `frontend/Dockerfile` | 无需改动 |
| `frontend/nginx.conf` | 无需改动 |
