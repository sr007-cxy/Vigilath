# Playwright 浏览器引擎接入现状

> 最后更新: 2026-04-29 · 分支 `feature/playwright`
>
> **2026-04-29 更新**：101 服务器上的 Playwright Browser Service (global/海外引擎) 已下线清理。原因：ChatGPT / Gemini / Grok 受 Cloudflare Turnstile 保护，在 AWS 服务器环境下无法稳定通过（IP 信誉低 + 跨机器 fingerprint 复用失败）。海外引擎改走官方 API（OpenAI / Anthropic / Perplexity）。103 服务器的 CN browser-service 继续运行。

## 一、背景

部分 AI 引擎的 API 不支持联网搜索，或 API 返回的引用源数量/质量不如网页版。Playwright 层通过浏览器自动化模拟用户操作，获取与网页版一致的结果。所有浏览器引擎适配器实现统一的 `EngineAdapter` 接口，上层分析器无需关心底层是 API 还是 Playwright。

## 二、已接入引擎（10 个）

### 1. DeepSeek

| 项目 | 说明 |
|------|------|
| 适配器 | `DeepSeekBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/deepseek_browser.py` |
| 目标 URL | `https://chat.deepseek.com/` |
| 登录脚本 | `backend/scripts/deepseek_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- 自动开启"智能搜索"开关（`div.ds-toggle-button` 定位 + class 激活态检测）
- 弹窗/cookie 横幅自动关闭（接受全部/Accept All）
- 流式回答稳定性检测（轮询 Stop/停止 按钮消失）
- 多路径引用提取（`.ds-markdown a[href]` + 来源卡片 + 裸 URL）
- Session 持久化
- Debug probe（按钮快照 + 关键词计数 + iframe/shadow root 检测 + HTML dump）

**登录方式**: 交互式浏览器登录 / JWT token 导入 / storage_state JSON 导入

### 2. 豆包（Doubao）

| 项目 | 说明 |
|------|------|
| 适配器 | `DoubaoBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/doubao_browser.py` |
| 目标 URL | `https://www.doubao.com/chat/` |
| 登录脚本 | `backend/scripts/doubao_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- 2026-04 UI 适配：新技能栏（快速/超能模式/PPT生成/图像生成/帮我写作/更多）
- "深入研究"模式：通过 更多 → 深入研究 路径激活（:text-is 精确匹配避免误点）
- Xvfb 自动集成：CAPTCHA 检测后自动启动 Xvfb 虚拟显示，切换到 headed 模式重试
- CAPTCHA 智能等待：Xvfb 模式 60s / 真实显示器 180s 超时
- 网络抓包：拦截 `/api/` 响应体，提取结构化引用 URL
- 弹窗自动关闭（我知道了/同意/接受全部/下载电脑版等）
- 6 层引用提取（网络抓包 → 参考来源区域 → 来源卡片 → assistant 链接 → 全页链接 → 裸 URL）
- 字节系域名过滤（doubao.com / bytedance.com / volcengine.com 等 19 个域名）
- 模拟人类浏览行为（鼠标移动 + 点击 + 滚动）
- Debug probe + body HTML dump

**登录方式**: 交互式浏览器登录 / JWT token 导入 / storage_state JSON 导入 / DevTools 导出格式

### 3. 通义千问（Qwen）

| 项目 | 说明 |
|------|------|
| 适配器 | `QwenBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/qwen_browser.py` |
| 目标 URL | `https://chat.qwen.ai/` |
| 登录脚本 | `backend/scripts/qwen_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- 网络抓包：拦截 API 响应体提取 URL（异步 `_capture_body`）
- 83 条中文源名称 → 实际域名映射（如"知乎"→ zhihu.com、"澎湃新闻"→ thepaper.cn）
- 来源抽屉交互：5 种策略点击"+N"卡片展开隐藏引用源（pw_click → js_click → js_dispatch）
- 6 层引用提取策略（网络抓包 → DOM hostnames → 抽屉链接 → assistant 原生链接 → 内联 hostname 合成 → 裸 URL）
- `.response-message-content` 精确答案提取（排除 thinking 状态卡噪声）
- 人类打字模拟（逐字符延迟）
- 流式回答稳定性检测（动画 class 消失 + 文本稳定）
- Debug probe + body HTML dump

**登录方式**: 交互式浏览器登录（二维码/手机验证码）/ storage_state JSON 导入

### 4. 文心一言（Wenxin）

| 项目 | 说明 |
|------|------|
| 适配器 | `WenxinBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/wenxin_browser.py` |
| 目标 URL | `https://chat.baidu.com/` |
| 登录脚本 | `backend/scripts/wenxin_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- 参考芯片展开（"参考 N 个网页"多策略点击展开）
- 嵌入式 JSON URL 提取：从页面 HTML 中解析 `linkTitle` 关联的 URL
- 多选择器回答文本提取（`.cosd-markdown-content` 等 8 个选择器）
- 流式输出检测（`.cosd-markdown-content-typingall` 消失）
- 百度系域名过滤
- 弹窗自动关闭
- Debug probe

**登录方式**: 交互式浏览器登录 / storage_state JSON 导入

### 5. 元宝（Yuanbao）

| 项目 | 说明 |
|------|------|
| 适配器 | `YuanbaoBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/yuanbao_browser.py` |
| 目标 URL | `https://yuanbao.tencent.com/chat?searchType=network` |
| 登录脚本 | `backend/scripts/yuanbao_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- Next.js + TDesign DOM 适配（`.agent-chat__bubble--ai`、`.hyc-common-markdown`）
- 联网搜索: `dt-button-id="internet_search"` 元素激活（4 种策略 fallback）
- sessionStorage 强制搜索模式 (`YB_ASK_AI_SEARCH_TYPE_NETWORK`)
- 网络抓包：拦截 `/api/chat` 响应体提取 URL
- 7 层引用提取（网络抓包 → ref list 展开 → ref card → ref list items → ref drawer → assistant 链接 → 裸 URL）
- 登录墙检测（data-placeholder 检查 "登录" 关键词）
- 新对话按钮自动点击（避免上下文污染）
- TDesign 弹窗自动关闭（暂不登录/稍后再说等）
- Debug probe

**登录方式**: 交互式浏览器登录 / storage_state JSON 导入

### 6. ChatGPT

| 项目 | 说明 |
|------|------|
| 适配器 | `ChatGPTBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/chatgpt_browser.py` |
| 目标 URL | `https://chatgpt.com/` |
| 登录脚本 | 无独立脚本（通过手动导入 session） |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- Next.js DOM 适配（`#prompt-textarea`、`[data-testid="send-button"]`、`[data-message-author-role="assistant"]`）
- 网络抓包：拦截 API 响应体提取结构化引用
- 双策略响应等待（stop button 消失 + 文本稳定性）
- 4 层引用提取（网络抓包 JSON → 来源/参考区域链接 → 内联 assistant 链接 → 裸 URL）
- OpenAI 域名过滤（chatgpt.com / openai.com / oaistatic.com 等）
- 弹窗自动关闭（中英文）
- Debug probe

**登录方式**: 手动导入 storage_state JSON

### 7. Claude

| 项目 | 说明 |
|------|------|
| 适配器 | `ClaudeBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/claude_browser.py` |
| 目标 URL | `https://claude.ai/new` |
| 登录脚本 | `backend/scripts/claude_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- ProseMirror 编辑器适配（`div.ProseMirror[contenteditable='true']`）
- fill() + type() 双模式输入（兼容 ProseMirror）
- 网络抓包：拦截 API 响应体提取结构化引用
- 双策略响应等待（stop button 消失 + 文本稳定性）
- 4 层引用提取（网络抓包 JSON → 来源/引用卡片 → 内联链接 → 裸 URL）
- Anthropic 域名过滤（claude.ai / anthropic.com / statsig 等）
- 弹窗自动关闭
- Debug probe

**登录方式**: 交互式浏览器登录 / storage_state JSON 导入

### 8. Google Gemini

| 项目 | 说明 |
|------|------|
| 适配器 | `GeminiBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/gemini_browser.py` |
| 目标 URL | `https://gemini.google.com/app` |
| 登录脚本 | `backend/scripts/gemini_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- Angular + Material DOM 适配（`textarea[aria-label*='prompt']`、`mat-icon-button`）
- 网络抓包：拦截 API 响应体提取结构化引用
- 双策略响应等待（stop/cancel button 消失 + 文本稳定性）
- 4 层引用提取（网络抓包 JSON → 来源/引用卡片 → 内联链接 → 裸 URL）
- Google 域名过滤（google.com / gstatic.com / googleapis.com 等 11 个域名）
- "New chat" 自动点击（避免上下文污染）
- 弹窗自动关闭
- Debug probe

**登录方式**: 交互式浏览器登录 / storage_state JSON 导入

### 9. Grok

| 项目 | 说明 |
|------|------|
| 适配器 | `GrokBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/grok_browser.py` |
| 目标 URL | `https://grok.com/` |
| 登录脚本 | `backend/scripts/grok_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- 网络抓包：拦截 API 响应体提取结构化引用
- 双策略响应等待（generating/streaming 指示器消失 + 文本稳定性）
- 4 层引用提取（网络抓包 JSON → 来源/引用卡片 → 内联链接 → 裸 URL）
- X/Twitter 域名过滤（grok.com / x.com / twitter.com / t.co 等 11 个域名）
- 弹窗自动关闭
- Debug probe

**登录方式**: 交互式浏览器登录（X/Twitter 账号）/ storage_state JSON 导入

### 10. Microsoft Copilot

| 项目 | 说明 |
|------|------|
| 适配器 | `CopilotBrowserAdapter` |
| 文件 | `backend/browser_engine/engines/copilot_browser.py` |
| 目标 URL | `https://copilot.microsoft.com/` |
| 登录脚本 | `backend/scripts/copilot_login.py` |
| 视频录制 | 支持 (`GEO_RECORD_VIDEO=1`) |

**核心功能**:
- 匿名模式可用（无需登录，功能受限但基本搜索可用）
- 网络抓包：拦截 Bing API 响应体提取结构化引用
- CAPTCHA (FunCaptcha) 检测与错误返回
- 双策略响应等待（stop/cancel button 消失 + 文本稳定性）
- 4 层引用提取（网络抓包 JSON → 来源/引用/attribution 卡片 → 内联链接 → 裸 URL）
- Microsoft/Bing 域名过滤（microsoft.com / bing.com / msn.com 等 17 个域名）
- 多策略弹窗关闭（Accept / Got it / Start chatting 等）
- Debug probe

**登录方式**: 无需登录（匿名可用）/ Microsoft 账号登录可获得完整功能

## 三、共享基础设施

### Playwright 生命周期 (`backend/browser_engine/browser.py`)

- **单例浏览器实例**：全局共享，避免重复启动
- `create_stealth_page(engine_name, *, locale, timezone_id, record_video)` — 创建带反检测的页面上下文，加载已有 session，可选视频录制
- `create_headed_page(engine_name)` — 创建有头浏览器页面（用于 CAPTCHA 手动处理）
- `save_page_session(engine_name, ctx)` — 保存当前上下文的 cookies + localStorage
- `human_delay(min_s, max_s)` — 随机人类延迟
- `human_type(page, selector, text)` — 逐字符打字模拟
- `human_move_mouse / human_click / human_scroll / simulate_browsing` — 人类行为模拟

### 反检测 (`backend/browser_engine/anti_detect.py`)

- `navigator.webdriver = false` — 屏蔽自动化标记
- Canvas / WebGL 指纹随机化
- User-Agent 真实 Chrome 版本轮换（Chrome 134/135）
- viewport / timezone / language 匹配地区设置
- `playwright-stealth` 集成（优先使用，自有 `apply_stealth()` 作为 fallback）

### Session 管理 (`backend/browser_engine/session_store.py`)

- 基于 Playwright 标准 `storage_state` 格式
- 存储 cookies + localStorage
- 支持 merge（增量更新）
- 存储位置: `backend/data/browser_sessions/{engine}.json`

### 视频录制 (`backend/browser_engine/video_store.py`)

- `GEO_RECORD_VIDEO=1` 环境变量控制开关
- 视频保存到 `backend/data/snapshots/{engine}/{date}_{hash}.webm`
- `get_video_path(page)` 获取录制文件路径
- `cleanup_old_snapshots(max_age_days=30)` 自动清理旧文件
- 所有 10 个引擎适配器均已集成

### Xvfb 虚拟显示 (`backend/browser_engine/xvfb.py`)

- `start_xvfb(display=":99")` — 启动虚拟 X 显示服务器
- `stop_xvfb()` — 停止 Xvfb 进程
- `atexit` 自动清理
- 用于服务器环境下的 headed 浏览器模式（绕过 CAPTCHA）

### 品牌排名提取 (`backend/geo_checker/analyzers/brand_ranking.py`)

- `extract_brand_ranking(answer, brand)` — 从 AI 回答中提取品牌排名位置
- 支持编号列表（1. 2. 3.）、粗体项（\*\*Brand\*\*）、中文序数（一二三）
- 排名标签：#1、#2、#3、Top 5、Top 10、Mentioned、Not mentioned
- `aggregate_rankings(results, brand)` — 跨引擎聚合排名数据
- 已集成到 competitive_intel 模式

## 四、与方案文档对照

原始方案（`docs/竞争情报功能方案.md`）计划 6 个 Playwright 引擎：

| 方案引擎 | 当前状态 |
|---------|---------|
| DeepSeek | **已完成** |
| 豆包 | **已完成**（含 Xvfb CAPTCHA 自动处理） |
| 文心一言 | **已完成** |
| 元宝（腾讯） | **已完成** |
| Claude (Anthropic) | **已完成** |
| Mistral (Le Chat) | 未实现（优先级低，Copilot 已覆盖欧美市场） |

额外实现（方案外新增）:

| 引擎 | 状态 |
|------|------|
| 通义千问 | **已完成**（原方案走 API，增加了 Playwright 适配器） |
| ChatGPT | **已完成**（方案外新增） |
| Google Gemini | **已完成**（方案外新增） |
| Grok (xAI) | **已完成**（方案外新增） |
| Microsoft Copilot | **已完成**（方案外新增，匿名可用） |

**总计**: 已完成 10 个 Playwright 引擎，剩余 1 个（Mistral）因优先级低暂未实现。

## 五、验证结果

### 第六轮：全引擎状态（2026-04-27）

| 引擎 | Session | 回答 | 引用数 | 引用质量 | 耗时 | 状态 |
|------|---------|------|--------|---------|------|------|
| DeepSeek | 有效 | 195 字 | 4 | 中（jingjiribao/ifeng/eastmoney/yiche） | 12s | **可用** |
| 元宝（腾讯） | 有效 | 790 字 | 7 | 中高（toutiao/stcn/xiaomiev/ycwb/ifeng/yoojia） | 26s | **可用** |
| 文心一言 | 有效 | 1065 字 | 17 | 高（bilibili/zhihu/163/sina/donews/xiaomiev，标题完整） | 133s | **可用，引用质量最佳** |
| 通义千问 | 有效 | 1613→733 字 | 32 | 高（wikipedia/baike/sohu/autohome/sina/huanqiu/yicai） | 45-179s | **可用** |
| 豆包 | 有效 | 1027 字 | ~15 | 中（baike/xiaomiev/sina/toutiao/autohome） | 16-31s | **可用**（Xvfb 自动绕 CAPTCHA） |
| ChatGPT | 需导入 | — | — | — | — | **需登录** |
| Claude | 需导入 | — | — | — | — | **需登录** |
| Gemini | 需导入 | — | — | — | — | **需登录** |
| Grok | 需导入 | — | — | — | — | **需登录** |
| Copilot | 无需登录 | — | — | — | — | **匿名可用** |

### 本次更新（2026-04-27）

**1. 新增 5 个海外引擎适配器**
- **ChatGPT**: `chatgpt_browser.py` — Next.js DOM，网络抓包，4 层引用
- **Claude**: `claude_browser.py` — ProseMirror 编辑器，网络抓包，4 层引用
- **Gemini**: `gemini_browser.py` — Angular + Material DOM，网络抓包，4 层引用
- **Grok**: `grok_browser.py` — 网络抓包，4 层引用，X/Twitter 域名过滤
- **Copilot**: `copilot_browser.py` — 匿名可用，Bing API 抓包，CAPTCHA 检测，17 个 Microsoft 域名过滤

**2. 豆包（Doubao）UI 大改 + CAPTCHA 自动处理**
- UI 变更: 旧"联网搜索"/"AI 搜索"按钮已移除，新 UI 为技能栏（快速/超能模式/更多→深入研究）
- 选择器: 使用 `:text-is('更多')` 精确匹配避免误点父容器
- CAPTCHA: 新增 `backend/browser_engine/xvfb.py` — 自动启动 Xvfb 虚拟显示 + headed 浏览器重试
- 结果: CAPTCHA 自动绕过成功率大幅提升

**3. 全引擎视频录制**
- `backend/browser_engine/video_store.py` — 视频存储管理
- 所有 10 个引擎适配器均已集成 `GEO_RECORD_VIDEO=1` 开关
- 视频保存为 `.webm` 格式到 `data/snapshots/{engine}/`
- API 端点: `GET /check/advanced/snapshot/{engine}/{filename}` 提供下载

**4. 品牌排名提取**
- `backend/geo_checker/analyzers/brand_ranking.py` — 排名位置提取 + 跨引擎聚合
- 支持编号列表、粗体项、中文序数、段落扫描
- 已集成到 competitive_intel 模式响应（`brand_ranking` 字段）

**5. 修复 ChatGPT 适配器变量名 bug**
- `captured_urls` → `captured_bodies`（未定义变量导致运行时报错）

### 结论

- **生产可用（国内）**: 文心一言（17 条，引用质量最佳）、通义千问（32 条，量大面广）、元宝（7 条，快速稳定）
- **生产可用（海外）**: Copilot（匿名可用，Bing 搜索后端）
- **基本可用**: DeepSeek（4 条，引用偏少但有效）、豆包（Xvfb 自动绕 CAPTCHA，引用丰富）
- **已改为 API 模式（海外）**: ChatGPT、Claude、Gemini、Grok — Cloudflare Turnstile 在服务器端无法通过，改走官方 API
- **待实现**: Mistral (Le Chat)（优先级低）

## 六、通用工作流

```
1. 登录: python backend/scripts/{engine}_login.py
   → 保存 session 到 backend/data/browser_sessions/{engine}.json

2. 调用: EngineAdapter.search(query)
   → create_stealth_page() 加载 session（可选 record_video=True）
   → 导航到聊天页面
   → 开启联网搜索开关
   → 输入查询并提交
   → 等待流式回答完成
   → 多路径提取引用
   → save_page_session() 保存 session
   → 返回 EngineResult（answer + citations + video_path）

3. 可用性检查: EngineAdapter.is_available()
   → 创建隐身上下文验证 session 有效性

4. 视频录制: GEO_RECORD_VIDEO=1
   → 视频保存到 data/snapshots/{engine}/{date}_{hash}.webm
   → API 下载: GET /check/advanced/snapshot/{engine}/{filename}
```
