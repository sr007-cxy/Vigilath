# SSG 预渲染首页技术方案

> 目标:让 `/` 首屏 FCP 从 ~2s 降到 <200ms,用户体验"秒开"且 0 跳变。
> 范围:**只针对 Home 页(`/` 和 `/checker`)**,其它路由保持 CSR。

## 一、目标与非目标

### 目标

- **首屏 FCP(First Contentful Paint)<200ms**(用户进入即看到完整 Hero)
- **0 视觉跳变**(React hydrate 不清空 DOM、不重渲染)
- **SEO 收益**:AI 爬虫(GPTBot / ClaudeBot 等不执行 JS)能抓到真实 Home DOM
- **改动最小**:不做全量 SSR 改造,只解决 Home 一页

### 非目标

- **不做**其它路由(Result / Account / Checkout / Landing 等)的预渲染
- **不做**真 Node SSR(不迁移 Next.js / Vite SSR Node 模式)
- **不追求**已登录用户/非默认主题用户的 0 跳变(多数用户是游客 + 默认,优化这部分)

## 二、方案选型

### 选路线 B:Puppeteer 快照 + `hydrateRoot`

构建期启动 headless Chromium 访问本地 preview server,等 Hero 渲染完毕,
把 `document.documentElement.outerHTML` 写回 `dist/index.html`。
运行期前端加载已填充的 HTML,React 用 `hydrateRoot()` 接管已有 DOM(不清空)。

| 维度 | 路线 A(createRoot) | **路线 B(hydrateRoot,选定)** | 路线 C(真 SSR) |
|---|---|---|---|
| FCP | 50-100ms | 50-100ms | 同 |
| hydrate 后跳变 | 有(React clear+rerender) | **无** | 无 |
| 代码改动 | 零 | 小(5 处) | 大(迁移 Next.js) |
| 工时 | 1-2h | **4-6h** | 1-2d |

路线 A 不满足"完全一致"要求,淘汰。路线 C 工程量过大,淘汰。

### 为什么不用 `vite-plugin-ssr` / `vike` / `vite-ssg` 之类开箱插件?

这些都假设 app 已经是 SSR-friendly(`window`/`document` 引用都有 guard)。
当前 Home 的依赖链里有多处 `useState(() => localStorage.xxx)`、`document.body.setAttribute`
同步副作用 —— 直接用这些插件反而要改更多代码。

Puppeteer 快照方案在**真实 Chromium 里跑预渲染**,`window` / `localStorage` / `document`
全都有,和浏览器运行时一致,改动量最小。

## 三、实施步骤

### 步骤 1:安装构建依赖

```bash
cd frontend
npm install --save-dev puppeteer
```

puppeteer 自带 Chromium,不需要系统装 Chrome。

### 步骤 2:新增构建脚本 `frontend/scripts/prerender.mjs`

职责:
- `vite build` 完成后启动
- 起一个临时 static server 托管 `frontend/dist/`
- 打开 puppeteer,设 `localStorage.i18nextLng = 'en'` 锁定语言
- 访问 `http://localhost:{port}/`
- 等待 `.hero` 元素出现 + `networkidle0`
- 抓取 `document.documentElement.outerHTML`,加上原有的 `<!doctype html>` 头
- 写回 `frontend/dist/index.html`
- 关掉 server 和浏览器

### 步骤 3:改 `frontend/package.json`

```json
"scripts": {
  "build": "tsc -b && vite build && node scripts/prerender.mjs"
}
```

### 步骤 4:改动 5 处 useState 初始值模式

所有改动都是同一种模式:**不在 useState 初始值里读 localStorage 或调 document**,
改成 `useState(安全默认)` + `useEffect` 里加载真实值。

#### 4.1 `src/main.tsx`

```tsx
// 改动前
createRoot(document.getElementById('root')!).render(<App />);

// 改动后
const root = document.getElementById('root')!;
if (root.hasChildNodes()) {
  // dist/index.html 被预渲染过,走 hydrate
  hydrateRoot(root, <StrictMode><App /></StrictMode>);
} else {
  // dev 模式或未预渲染,走普通渲染
  createRoot(root).render(<StrictMode><App /></StrictMode>);
}
```

#### 4.2 `src/contexts/AuthContext.tsx:49-56`

```tsx
// 改动前
const [token, setTokenState] = useState<string | null>(() => {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
});

// 改动后
const [token, setTokenState] = useState<string | null>(null);
useEffect(() => {
  try {
    const t = localStorage.getItem(TOKEN_KEY);
    if (t) setTokenState(t);
  } catch { /* noop */ }
}, []);
```

**副作用**:已登录用户首渲染为"未登录"态,~10ms 后 useEffect 跑完切换为登录态。
Header 会短暂闪登录/注册按钮,然后变成用户名。

#### 4.3 `src/components/Header.tsx:23-37`

同上模式:`useState(null)` + `useEffect` 读 localStorage。另外删掉 :124 的 `console.log`。

#### 4.4 `src/components/ThemeToggle.tsx:13-27`

现在的代码在 useState 初始值里**同步调 `document.body.setAttribute`**,
SSR/hydrate 模式下会触发 hydration mismatch。

```tsx
// 改动后
const [theme, setTheme] = useState<Theme>('peec');  // 预渲染固定为 peec

useEffect(() => {
  const value = getInitialTheme();  // 从 localStorage 读真实值
  setTheme(value);
  applyTheme(value);
}, []);
```

**副作用**:切过 light/dark 主题的用户首渲染显示 peec 主题,~10ms 后切换。颜色短暂闪烁。

#### 4.5 i18next 语言锁定

`src/i18n/index.ts` 的 `fallbackLng` 从 `'zh'` 改为 `'en'`,和预渲染一致。
(或者在 prerender 脚本里 `localStorage.setItem('i18nextLng', 'en')`。)

### 步骤 5:nginx 侧调整(可选)

如果未来要给中文用户也做 0 跳变:
- 生成 `/` 的英文版和 `/zh/` 的中文版两份 HTML
- nginx 按 `Accept-Language` 头做 redirect

**本次不做**,统一英文预渲染。

### 步骤 6:验证

本地:

```bash
cd frontend
npm run build
# dist/index.html 应从 14KB → ~40-60KB
npm run preview
# 打开 http://localhost:4173/
# - DevTools Network: HTML 已含完整 Hero DOM
# - DevTools Console: 无 hydration 警告
# - 用 Performance tab 录制:FCP < 200ms
```

线上冒烟:

```bash
# 部署后
curl -s --compressed https://www.vigilath.com/ | grep -c 'home.slogan.cta\|Be discovered\|被全球发现'
# 应该看到 slogan 文本直接在 HTML 里
```

浏览器验证:
- 未登录 + EN 浏览器:Hero 秒开,完全无跳变
- 未登录 + ZH 浏览器:Hero 秒开,~10ms 后语言切换到中文(预期内的小跳)
- 已登录用户:Hero 秒开,Header 右上角按钮闪一下变用户名(预期内的小跳)
- 切过 dark 主题用户:Hero 秒开,~10ms 后颜色切到 dark(预期内的小跳)

### 步骤 7:部署

按 `docs/deployment-guide.md` 第三章流程:
- commit + push `feat/ssg-home` 分支
- 合并到 develop
- `cd frontend && npm run build`(会触发 prerender)
- `sudo rsync -a --delete frontend/dist/ /var/www/html/www.vigilath.com/`

## 四、回滚

所有改动集中在 **1 个 commit**(或 1 个 PR)。回滚:

```bash
git revert <ssg-commit-sha>
npm run build
sudo rsync -a --delete frontend/dist/ /var/www/html/www.vigilath.com/
```

`main.tsx` 的 hydrate/create 判断自带 fallback:如果 `dist/index.html` 没预渲染,
自动走 createRoot —— 所以即便 prerender 脚本失败,build 仍会产出可用的 dist
(退化成当前行为)。

## 五、已知风险与副作用

| 风险 | 发生条件 | 影响 | 缓解 |
|---|---|---|---|
| hydration mismatch 警告 | 任何 DOM 差异 | React 回退到 clear+render,失去 SSG 收益 | 逐一修 useState 初始值 |
| 登录态 Header 闪烁 | 已登录用户 | Header 按钮闪一下 | 接受(回头客少数) |
| 主题闪烁 | 切过 light/dark 的用户 | 颜色跳一次 | 接受(多数是默认主题) |
| 语言闪烁 | 中文浏览器 | 英→中跳一次 | 未来可做 `/zh/` 双版本 |
| `fallbackLng` 从 zh 改 en | i18n 有未翻译的 key | 英文用户可能看到原始 key | 已有 en 翻译覆盖齐全,低风险 |
| 构建时间增加 10-20s | 每次 build | CI 慢一点 | 接受 |
| puppeteer 下载 Chromium ~150MB | 首次 npm install | 磁盘占用 | 接受(CI 可缓存) |
| prerender 脚本在 CI 环境跑不起来 | 容器缺系统库 | build 失败 | 用 `puppeteer` 带的独立 Chromium 或加 sandbox 参数 |

## 六、工时估算

| 步骤 | 工时 |
|---|---|
| 装 puppeteer + 写 prerender 脚本 | 1.0h |
| 改 main.tsx hydrate 逻辑 | 0.5h |
| 改 5 处 useState 初始值(Header / ThemeToggle / AuthContext) | 1.5h |
| i18next 语言锁定 | 0.3h |
| 本地调试 hydration 警告 | 1.0h |
| 部署 + 线上验证 | 0.5h |
| 文档更新(`deployment-guide.md` 补 prerender 说明) | 0.3h |
| **合计** | **~5h** |

## 七、成功指标

部署后,从中国移动 4G(或用 DevTools Throttling "Slow 4G")实测:

| 指标 | 当前 | 目标 |
|---|---|---|
| FCP(First Contentful Paint)| ~2000ms | **<300ms** |
| LCP(Largest Contentful Paint)| ~2500ms | **<500ms** |
| 首屏完整显示耗时 | ~2500ms | **<500ms** |
| hydrate 后 DOM mutation 次数 | 几百次(clear+render) | **0-10 次** |

如果 FCP 没有显著下降,说明 prerender 没生效(检查 `dist/index.html` 是否含 Hero DOM);
如果 hydration 警告出现,说明还有未修的 useState 同步副作用,逐一排查。

## 八、后续可做的扩展

本次做完后,如果效果好,可按需扩展:

1. **其它常驻路由做预渲染**:`/about`、`/geo-knowledge`、`/products-services`、`/pricing` 等
   静态内容页,工时 ~1h/页
2. **中文版预渲染**:生成 `/zh/` 版本 HTML,nginx 按 Accept-Language 路由
3. **Critical CSS inline**:把首屏用到的 CSS 直接内联进 `<head>`,再省一次 CSS 下载
4. **组件级 code-split 细化**:Home 现在是 eager 加载,可以把 Advanced Detection 区域拆成 lazy
