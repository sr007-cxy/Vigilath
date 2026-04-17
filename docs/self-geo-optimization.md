# GApex 自身 GEO 优化清单

> 本文档记录项目自身网站的 GEO 优化配置，方便后续维护和更新。

## 品牌与联系信息规范

| 项目 | 统一值 |
|------|--------|
| 产品品牌 | **GApex** |
| 产品口号 | GApex — Unified GEO+AEO for Global AI Visibility |
| 域名 | www.vigilath.com |
| 联系邮箱 | support@zen7.com |
| 安全邮箱 | security@zen7.com |
| X / Twitter | [@zen7_labs](https://x.com/zen7_labs) |
| Discord | [discord.gg/mJVEVXyxD5](https://discord.gg/mJVEVXyxD5) |

> **维护提示**：修改品牌名或联系方式时，需同步更新下方"涉及文件"中列出的所有文件。

---

## 检测类别数量

当前：**25 个类别**

> **维护提示**：每次新增/删除检测类别后，需全局搜索 `25 categories`、`25-category`、`25 dimensions` 并替换为新数字。涉及前端静态文件、后端注释、文档等。

---

## 涉及文件清单

### 前端静态文件 (`frontend/`)

| 文件 | 包含内容 | 修改时需注意 |
|------|----------|-------------|
| `index.html` | meta description, OG tags, Twitter Card, JSON-LD (Organization/WebSite/FAQPage), SEO skeleton, Search Console 验证 | 品牌名、类别数、邮箱、Twitter handle、sameAs 数组 |
| `public/robots.txt` | AI bot 允许规则, Sitemap 引用 | 新增 AI bot 时添加 Allow 规则 |
| `public/llms.txt` | 产品摘要版 — 口号、核心页面、FAQ、社区、联系 | 品牌名、类别数、FAQ 内容、社区链接 |
| `public/llms-full.txt` | 产品详细版 — 完整 GEO 说明、所有 FAQ | 同上，FAQ 更详细 |
| `public/sitemap.xml` | 所有公开页面 URL + lastmod | 新增页面时添加 `<url>` 条目，更新 lastmod 日期 |
| `public/humans.txt` | 团队信息、技术栈 | 品牌名、联系邮箱 |

### .well-known 目录 (`frontend/public/.well-known/`)

| 文件 | 包含内容 | 修改时需注意 |
|------|----------|-------------|
| `llms.txt` | 精简版 llms.txt（标准发现路径） | 与 `public/llms.txt` 保持同步 |
| `ai-plugin.json` | OpenAI 插件清单 | 品牌名、类别数、联系邮箱 |
| `openai.yaml` | OpenAPI 3.0 规范 | 品牌名、联系邮箱 |
| `security.txt` | 安全联系信息 | 安全邮箱、过期日期 |
| `gpc.json` | Global Privacy Control | 一般不需改动 |

### 后端与文档

| 文件 | 包含内容 | 修改时需注意 |
|------|----------|-------------|
| `CLAUDE.md` | 项目概览给 AI 助手 | 类别数 |
| `README.md` | 项目 README | 类别数 |
| `backend/geo/api/geo.py` | API 路由注释 | 类别数（注释中） |
| `backend/geo/services/geo_checker.py` | 检测服务注释 | 类别数（注释中） |
| `moltspay-server/moltspay.services.json` | 支付服务描述 | 类别数 |
| `docs/moltspay-integration-plan.md` | 支付集成方案 | 类别数 |

### React 组件（一般不需改动）

| 文件 | 说明 |
|------|------|
| `frontend/src/components/Footer.tsx` | 社交链接、联系邮箱、法律页面链接 |
| `frontend/src/components/Header.tsx` | 导航栏 |

---

## Search Console 验证

| 平台 | 状态 | 位置 |
|------|------|------|
| Google Search Console | 占位符（`REPLACE_WITH_GOOGLE_CODE`） | `index.html` `<meta name="google-site-verification">` |
| Bing Webmaster Tools | HTML 注释占位符 | `index.html` `<!-- <meta name="msvalidate.01"> -->` |

> **操作步骤**：获取验证码后，在 `frontend/index.html` 中替换对应占位符。

---

## sitemap.xml 维护

- 每次新增前端路由页面时，在 `frontend/public/sitemap.xml` 添加对应 `<url>` 条目
- 内容有重大更新时，更新对应 URL 的 `<lastmod>` 日期
- 当前收录页面：`/`, `/checker`, `/geo-knowledge`, `/products-services`, `/membership`, `/pricing`, `/process`, `/data`, `/about`, `/contact`, `/privacy`, `/terms`, `/cookie-policy`

---

## llms.txt 维护

三个 llms.txt 文件需保持一致（详略程度不同）：

| 文件 | 定位 | 详细程度 |
|------|------|---------|
| `public/llms.txt` | 主版本 | 中等 — 产品摘要 + FAQ + 社区 + 联系 |
| `public/llms-full.txt` | 详细版 | 完整 — 含 GEO 定义、全部检测类别、详细 FAQ |
| `public/.well-known/llms.txt` | 标准发现路径 | 精简 — 页面列表 + 简短 FAQ |

> **维护提示**：修改任一版本后，检查另外两个是否需要同步。

---

## 常用维护命令

```bash
# 检查是否有遗漏的旧类别数引用
grep -r "23 categor\|23-categor\|23 dimension" frontend/ backend/ docs/ CLAUDE.md README.md

# 检查品牌一致性
grep -r "Vigilath" frontend/

# 检查联系邮箱一致性
grep -r "contact@gapex\|contact@vigilath" frontend/
```
