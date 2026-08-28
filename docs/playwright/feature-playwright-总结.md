# `feature/playwright` 分支工作总结

> **分支**: `feature/playwright` → squash merge 回 `develop` (`78bce7f`)
> **时间跨度**: 2026-04-17 → 2026-05-04(~17 天)
> **Commit 数**: 71 个原始 commit(含约 13 个 IDE 自动 commit)
> **文件变更**: 162 文件 / **+34,541 / -5,296 行**
> **分支起点**: `d53d26a`(后被发现是 develop 上误传 `.env.bak` + `stats.html` 的"坏"commit,本分支合并时已剔除)

---

## 📊 一句话总结

以 Playwright 浏览器自动化为核心,新增 5 个国内 + 3 个国际 AI 引擎的微服务化接入,叠加完整的 anti-detection / CAPTCHA 工程,并基于此能力新增 **Competitive Intel(引用源分析)** 功能模块;伴随 entity 审计 **22min → 2min** 的性能优化、相关文档与 SEO/性能基础设施的若干改进。

---

## 一、主线:Playwright 浏览器引擎微服务

把"AI 引擎查询"从纯 API 调用扩展到**浏览器自动化**,绕过国内大模型平台没有公开 API 的限制。

### 关键 commit
- `feat(playwright): add Playwright browser engine integration and microservice architecture proposal`
- `feat: implement Playwright browser engine microservice architecture`
- `feat(browser): cross-machine CF session reuse via Linux-pinned profile + persistent Chrome context`
- `feat(browser): add profile parameter to create_stealth_page and create_headed_page for consistent device identity`
- `chore(backend/browser_engine): sync browser.py and anti_detect.py from services/`

### 产物
- `services/browser-service/` —— 独立 FastAPI 微服务,跑 Playwright + Chromium
- `backend/browser_engine/client.py` —— 后端 HTTP 客户端,通过 `/api/browser-{region}/*` 调用微服务
- 生产部署在 `test.example.com:12080`,nginx 反向代理 `/api/browser-cn/*` → cn 实例

---

## 二、5 个国内 AI 引擎接入(主战场)

每个引擎独立 adapter + 网络抓包 + 引用提取 + session 持久化:

| 引擎 | 关键 commit |
|---|---|
| **DeepSeek** | `feat(deepseek): 改进响应等待机制,内容稳定性轮询替代按钮文本检测` |
| **Qwen 通义千问** | `feat: 增加 QwenBrowserAdapter 的网络抓包,优化引用提取,调整 API 超时` + 多次 cookie/token 续期 |
| **豆包 Doubao** | `fix(doubao): detect second CAPTCHA after query submit` / `enhance second CAPTCHA detection` / `hash full canvas dataURL in fingerprint probe` + 登录脚本 |
| **元宝 Yuanbao** | `feat: add Yuanbao browser adapter for automated web search` + session 管理脚本 |
| **文心一言 Wenxin** | (混在 stealth/profile 工作里) |

---

## 三、Anti-Detection 工程化(为反爬虫)

### 关键 commit
- `fix(browser): comprehensive anti-detection rewrite and video capture fixes`
- `refactor(anti_detect, browser): enhance profile consistency for anti-detection measures`
- `feat(doubao_browser): add fingerprint diagnostics for user agent and platform logging`
- `feat: 集成 playwright-stealth,启用全部 5 个浏览器引擎`
- `debug: add /debug/env endpoint to check Xvfb/DISPLAY status`
- `fix: 设置 PLAYWRIGHT_BROWSERS_PATH 共享路径,解决 root 用户无法找到浏览器的问题`

### 涉及面
- fingerprint 一致性(canvas、UA、platform)
- Xvfb 虚拟桌面 headless
- Chrome `user_data_dir` 持久化(跨机复用 CF session)
- CAPTCHA 自动检测重试
- 视频录制做反爬证据

---

## 四、3 个国际 AI 引擎接入(ChatGPT / Gemini / Grok)

### 关键 commit
- `Add browser session profile for ChatGPT with Linux user agent details`
- `feat(browser-service): make chatgpt / gemini / grok adapters fail fast on missing session`
- `fix(scripts): apply runtime stealth profile to chatgpt / gemini / grok login flows`
- `feat: Add scripts for session upload and manual login, enhance browser stealth`
- `Add browser session management and fingerprint verification scripts`
- `Add session management scripts for various AI platforms`

### 部署现状
- 国际线在 develop 合回时**只接 cn 实例**
- global 实例(`browser-global.example.com:8091`)**未部署**
- `BROWSER_GLOBAL_URL` 留空

---

## 五、Competitive Intel / 引用源分析(新功能模块)

完全新增的"竞品情报 / 引用源追溯"功能。

### 关键 commit
- `feat: add competitive intelligence engine with citation tracing, source preference, and competitor insights`
- `feat: add competitive intelligence pages and data handling`
- `feat: 添加每个引擎的详细情感和框架分析,更新相关接口和前端展示`
- `Refactor Competitive Intelligence module: 抽 SourceTracePanel / SourcePreferencePanel,共享 source analysis types`
- `feat: 更新 SourceAnalysisPanel 和 SourcePreferencePanel 以支持过滤和统计功能`
- `feat(source-analysis): enhance engine ordering and add popularity ranking`
- `demo: 引用源分析模型名称替换为海外主流模型(静态演示用)` + `revert: 恢复真实模型名称`(产品决策反复)

### 产物
- 后端:`backend/geo_checker/analyzers/{source_trace,source_preference,source_classify}.py`
- 前端:`frontend/src/components/source-analysis/{SourceAnalysisPanel,SourcePreferencePanel,SourceTracePanel}.tsx`

---

## 六、Entity 审计性能优化

| Commit | 效果 |
|---|---|
| `perf: 实体审计 Playwright 从 22min 降到 2min` ⭐ | **11x 提升** |
| `perf: 优化实体审计速度 — 90s 查询超时 + 减少引擎等待时间` | 进一步压尾延迟 |
| `fix: 引擎状态 API 补充全部 5 个 Playwright 引擎` | `/engines/status` 完整暴露 |

---

## 七、文档 / 方案

- `docs: 添加 Playwright 浏览器引擎接入现状文档` → `docs/playwright/playwright-引擎接入现状.md`
- `docs: 添加 Playwright 引擎验证结果`
- `feat: 添加 Dashboard 功能实现方案文档`(后续 fix 修正)
- `feat: 添加 Hybrid SSG + CSR 方案文档,优化首页加载性能`
- `feat: 更新文档以反映 Playwright 浏览器服务的下线及 API 模式切换`

---

## 八、其他工程改进

- `feat: add SEO pages plugin to generate route-specific HTML files with unique metadata` —— 多路由独立 metadata SEO
- `Add fix package localization and update Result component`
- `feat: add deploy-test.sh to .gitignore`
- `style: 更新错误提示样式,改善可读性和一致性`
- `Merge branch 'develop' into feature/dashboard`(中途同步)
- `chore(merge): 吸收 develop,剔除误传的 .env.bak 与 stats.html`(最后一次合并)

---

## 📁 改动按目录归类

| 目录 | 文件数 | 说明 |
|---|---:|---|
| `backend/browser_engine/` | 19 | 后端调微服务的客户端代码 |
| `backend/scripts/` | 18 | session 上传 / 登录 / 健康检查脚本 |
| `services/browser-service/` | 14 | Playwright 微服务全套 |
| `frontend/src/pages/` | 11 | Result / Advanced 页改动 |
| `frontend/src/(其他)` | 9 | 引用源分析组件等 |
| `backend/geo/(API/services)` | 9 | advanced.py / advanced_runners.py |
| `backend/data/browser_sessions/` | 7 | 5 引擎 session 存档 |
| `docs/` | 6 | 方案 / 验证 / 文档 |
| `backend/geo_checker/analyzers/` | 5 | source_trace / source_preference 等 |
| `frontend/src/i18n/` | 4 | 中英文文案 |
| `backend/geo_checker/modes/` | 3 | entity / visibility / competitive_intel 模式集成 |

---

## ⚠️ 历史教训(下次拉分支时避免)

### 1. IDE 自动 commit 噪音
71 个 commit 中 **13 个**是 IDE 自动 commit:
- `Refactor code structure for improved readability and maintainability` × 11
- `Implement feature X to enhance user experience and fix bug Y in module Z` × 2

这些消息**完全描述不出实际改动**,看 `git log` 几乎读不出在干嘛,bisect 时极痛苦。

**建议**:关掉 IDE 自动 commit,或要求自己手写每条 commit message。

### 2. squash 合并损失粒度
71 个原始 commit 在 develop 上变成 **1 个 squash commit `78bce7f`**。
- 看 develop history 的人**完全看不到这 17 天的演进**
- 后续若想 revert 单个改动 → 只能手动 patch

**建议**:大型 feature 分支保留 merge commit(`--no-ff`),让 develop history 至少能看到一个"merge marker",原始 commit 也保留。

### 3. develop 上的 `d53d26a` 误传敏感文件
develop 上的 commit `d53d26a "Refactor code structure..."` 实际包含:
- `backend/.env.bak.20260417-104249`(**含 secrets**,49 行)
- `frontend/stats.html`(rolldown bundle visualizer 产物,4950 行)

**已处理**:本分支合并 develop 时通过 `.gitignore` 兜底剔除(`backend/.env.bak*` + `frontend/stats.html` 加进忽略规则),两者均未进 `feature/playwright` 的 HEAD tree。

**仍存在的隐患**:`.env.bak` 的 secrets 已经在 develop 历史里。建议:
- 上 develop `git revert d53d26a` 删掉那次 commit 的内容(保留 commit 但内容空)
- 或者更彻底:**直接换掉 .env 里所有出现过的 API key**(API_KEY rotation),把暴露的 key 作废

### 4. 同步 develop 时机
本分支两次 merge develop:
- `4f2bc04 Merge branch 'develop' into feature/dashboard`(中途)
- `65ff139 chore(merge): 吸收 develop,剔除误传的 .env.bak 与 stats.html`(收尾前)

**建议**:长 feature 分支应每周至少 rebase / merge 一次 develop,避免最后大规模冲突。

---

## 🔗 相关文档

- [Playwright 微服务拆分方案](./playwright-微服务拆分方案.md)
- [Playwright 引擎接入现状](./playwright-引擎接入现状.md)
- [Playwright Cloudflare 引擎登录方案](./playwright-cloudflare-引擎登录方案.md)
