# Playwright 浏览器引擎微服务拆分方案（修订版）

> **海外引擎部分已废弃 (2026-04-29)**：101 服务器上的 browser-service (global/海外引擎) 因 Cloudflare Turnstile 无法在服务器端通过，已下线清理。当前仅 103 服务器保留 CN 引擎的 browser-service。本方案中海外引擎相关内容不再适用。
>
> 基于 2026-04-27 代码实际状态重写 · 分支 `feature/playwright`

---

## Context

### 为什么重写方案

旧方案在 2026-04-27 编写时与代码快照基本吻合，但截至现在 **方案中描述的所有改动均未实施**：

- `services/browser-service/` 目录不存在
- `backend/browser_engine/client.py` 不存在
- `backend/browser_engine/engines/base.py` 仍然从 `api_engine.base` 重导出（循环依赖）
- `entity.py` 的 monkey-patching 仍然存在（:600-674）
- `competitive_intel.py` 仍然用 `asyncio.new_event_loop()`（:312, :344）
- `visibility.py` 仍然用 `asyncio.new_event_loop()`（:286）
- `docker-compose.yml` 只有 frontend + backend 两个服务
- httpx 已安装（venv 中有）但未列入 pyproject.toml
- Dockerfile 使用 Python 3.9 + poetry（旧方案假设 3.10-slim + pip）

本方案基于 **当前代码实际状态** 重写，修正与旧方案的偏差。

---

## 一、现状（代码实测）

### 文件清单与行数

```
backend/browser_engine/           5,124 行，17 个文件
├── browser.py                    245 行 — 单例 Playwright 浏览器
├── anti_detect.py                184 行 — 反检测
├── session_store.py               86 行 — Session 文件读写
├── video_store.py                 57 行 — 视频录制存储
├── xvfb.py                       66 行 — Xvfb 虚拟显示
├── __init__.py                     2 行 — 仅 docstring
└── engines/
    ├── base.py                     4 行 — from api_engine.base import ...
    ├── deepseek_browser.py       272 行
    ├── doubao_browser.py         887 行 ← 最大
    ├── qwen_browser.py           578 行
    ├── wenxin_browser.py         281 行
    ├── yuanbao_browser.py        624 行
    ├── chatgpt_browser.py        331 行
    ├── claude_browser.py         357 行
    ├── gemini_browser.py         384 行
    ├── grok_browser.py           358 行
    └── copilot_browser.py        408 行
```

### 7 个痛点（全部仍然存在）

| # | 痛点 | 证据 |
|---|------|------|
| 1 | 内存占用 | Chromium 300-500MB 在主 API 进程内 |
| 2 | 崩溃扩散 | 浏览器 SIGBUS/SIGSEGV → API 进程挂 |
| 3 | 并发瓶颈 | 单进程单浏览器实例，无法精确控制并发 |
| 4 | 网络冲突 | CN + Global 引擎混在一个进程 |
| 5 | 部署耦合 | 无法按地域独立部署 |
| 6 | monkey-patching | `entity.py`:612-657 临时替换 `get_browser()` |
| 7 | base.py 循环依赖 | `engines/base.py` 从 `api_engine.base` 重导出 |

### 5 个调用点

| 文件 | 行号 | 引擎 | 调用模式 |
|------|------|------|----------|
| `geo_checker/modes/competitive_intel.py` | 312, 344 | DeepSeek + 豆包 | `asyncio.new_event_loop()` + 直调适配器 |
| `geo_checker/modes/entity.py` | 612-674 | 全部 10 个 | monkey-patch `get_browser()` + `asyncio.gather()` |
| `geo_checker/modes/visibility.py` | 286 | DeepSeek | `asyncio.new_event_loop()` + 直调适配器 |
| `geo/api/advanced.py` | 200-226 | 全部 10 个 | `load_storage_state()` 直接读文件 |
| `geo/api/advanced.py` | 249-280 | 全部 10 个 | `FileResponse` 直接读本地 .webm 文件 |

---

## 二、目标架构

```
┌─────────────────────────────────────────────────────────────┐
│                    主服务 :8070                              │
│            backend/ (无 Playwright 依赖)                      │
│                                                              │
│   browser_engine/client.py ──── httpx ─────────┐            │
└─────────────────────────────────────────────────┼───────────┘
                                                   │
                          ┌────────────────────────┼──────────┐
                          │                        │          │
              ┌───────────▼──────────┐  ┌──────────▼─────────┐
              │ browser-cn  :8091    │  │ browser-global :8092│
              │ ENV REGION=cn        │  │ ENV REGION=global   │
              │ ENV ENGINE_LIST=     │  │ ENV ENGINE_LIST=    │
              │   deepseek,doubao,   │  │   chatgpt,claude,   │
              │   qwen,wenxin,       │  │   gemini,grok,      │
              │   yuanbao            │  │   copilot           │
              │                      │  │                     │
              │ 同一份代码            │  │ 同一份代码           │
              │ Semaphore(3)         │  │ Semaphore(3)        │
              └──────────────────────┘  └─────────────────────┘
```

### 核心设计原则

1. **一份代码、双实例部署** — 环境变量 `ENGINE_LIST` + `REGION` 区分
2. **HTTP 解耦** — 主服务通过 httpx 调用，不再 import Playwright
3. **故障隔离** — 浏览器崩溃不影响主 API 进程
4. **渐进式迁移** — Phase 0-3，每步可独立验证

---

## 三、服务 API 设计

两个实例暴露完全相同的 REST API。

### 端点一览

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/search` | 执行查询 |
| GET | `/engines` | 可用引擎列表 |
| GET | `/sessions` | 所有引擎状态 |
| GET | `/sessions/{engine}` | 单引擎状态 |
| PUT | `/sessions/{engine}` | 上传 session |
| DELETE | `/sessions/{engine}` | 清除 session |
| GET | `/snapshot/{engine}/{filename}` | 视频下载 |
| GET | `/health` | 健康检查 |

### 请求/响应格式

**POST /search**
```json
// 请求
{"engine": "deepseek", "query": "小米汽车怎么样"}

// 成功 (200)
{
  "engine": "DeepSeek", "query": "小米汽车怎么样",
  "answer": "...", "citations": [{"url": "...", "domain": "...", "title": "", "snippet": "", "position": 1}],
  "search_queries": [], "video_path": "/app/data/snapshots/deepseek/xxx.webm", "error": null
}

// 引擎级错误 (200, error 字段)
{"engine": "DeepSeek", "query": "...", "answer": "", "citations": [], "error": "timeout"}

// HTTP 错误: 400 参数错误 / 429 并发满 / 503 未就绪
```

**GET /engines**
```json
{"region": "cn", "engines": ["deepseek", "doubao", "qwen", "wenxin", "yuanbao"]}
```

**GET /health**
```json
{"status": "healthy", "browser_connected": true, "region": "cn", "engine_count": 5}
```

---

## 四、目录结构

### 浏览器服务（新建）

```
services/
└── browser-service/
    ├── pyproject.toml         # fastapi + uvicorn + playwright + playwright-stealth
    ├── Dockerfile             # python:3.10-slim + chromium + Xvfb
    └── app/
        ├── __init__.py
        ├── main.py            # FastAPI + ENV 驱动 region + engine_list + 信号量
        ├── models.py          # 自包含 Pydantic v2 模型（不依赖主服务）
        ├── browser.py         # ← 搬自 backend/browser_engine/browser.py
        ├── anti_detect.py     # ← 搬自 backend/browser_engine/anti_detect.py
        ├── session_store.py   # ← 搬自 backend/browser_engine/session_store.py
        ├── video_store.py     # ← 搬自 backend/browser_engine/video_store.py
        ├── xvfb.py            # ← 搬自 backend/browser_engine/xvfb.py
        └── engines/
            ├── __init__.py
            ├── base.py        # 自包含: EngineAdapter + EngineResult + Citation
            ├── deepseek_browser.py
            ├── doubao_browser.py
            ├── qwen_browser.py
            ├── wenxin_browser.py
            ├── yuanbao_browser.py
            ├── chatgpt_browser.py
            ├── claude_browser.py
            ├── gemini_browser.py
            ├── grok_browser.py
            └── copilot_browser.py
```

### 主服务精简

```
backend/browser_engine/
├── __init__.py          # 保持不变
└── client.py            # 新增: 统一路由入口（httpx）
```

搬迁后原 `backend/browser_engine/` 下的 `browser.py`、`anti_detect.py`、`session_store.py`、`video_store.py`、`xvfb.py`、`engines/` 全部删除（Phase 3 清理阶段）。

---

## 五、关键模块设计

### 5.1 `app/engines/base.py` — 自包含（解决循环依赖）

当前 `engines/base.py` 仅 4 行，从 `api_engine.base` 重导出。微服务化后需要自包含：

```python
# 从 backend/api_engine/base.py 搬入 EngineAdapter, EngineResult, Citation,
# extract_urls_from_text, extract_citations_from_json 的完整实现
# 不再有任何外部依赖
```

关键：需要从 `backend/api_engine/base.py` 复制 `EngineResult`、`Citation`、`EngineAdapter`、`extract_urls_from_text`、`extract_citations_from_json` 的完整实现代码。

### 5.2 `app/main.py` — ENV 驱动

```python
ENGINE_LIST = os.environ.get("ENGINE_LIST", "").split(",")
REGION = os.environ.get("REGION", "cn")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_QUERIES", "3"))
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# 动态加载 ENGINE_LIST 中列出的适配器
ENGINE_MODULE_MAP = {
    "deepseek": "app.engines.deepseek_browser",
    "doubao":   "app.engines.doubao_browser",
    # ... 10 个引擎
}
```

### 5.3 `app/models.py` — 自包含 Pydantic v2

不依赖主服务的任何模型，自己定义 SearchRequest/SearchResponse/SessionInfo/HealthResponse。

### 5.4 `backend/browser_engine/client.py` — 统一路由

```python
ENGINE_ROUTING = {
    "deepseek": "cn", "doubao": "cn", "qwen": "cn",
    "wenxin": "cn", "yuanbao": "cn",
    "chatgpt": "global", "claude": "global", "gemini": "global",
    "grok": "global", "copilot": "global",
}

async def search(engine, query) -> EngineResult: ...
def search_sync(engine, query) -> EngineResult: ...
async def is_available(engine) -> bool: ...
def has_session(engine) -> bool: ...
async def all_engines_status() -> dict: ...
```

---

## 六、调用方改动

### 6.1 `competitive_intel.py`（2 处改动）

```python
# 改前 (:312-316)
loop = asyncio.new_event_loop()
r = loop.run_until_complete(adapter.search(q["query"]))
loop.close()

# 改后
from browser_engine.client import search_sync
r = search_sync("deepseek", q["query"])
```

同理 `_run_doubao_queries()` (:344-348) 改为 `search_sync("doubao", ...)`。

### 6.2 `visibility.py`（1 处改动）

```python
# 改前 (:286-291)
loop = asyncio.new_event_loop()
loop.run_until_complete(_run_deepseek_queries())
loop.close()

# 改后
from browser_engine.client import search_sync
r = search_sync("deepseek", query)
```

### 6.3 `entity.py`（最复杂，消除 monkey-patching）

```python
# 改前 (:612-674): 创建独立 Playwright 实例 + monkey-patch get_browser()
import browser_engine.browser as _be
_be.get_browser = _our_browser
# ... asyncio.gather() ...
_be.get_browser = _orig_get_browser

# 改后: 纯 HTTP 调用，完全消除 Playwright import
from browser_engine.client import search_sync, has_session
from concurrent.futures import ThreadPoolExecutor, as_completed

# 激活检查
for _disp, _skey, _mod, _cls, _optional in _BROWSER_ENGINES:
    if has_session(_skey) or _optional:
        engines.append((_disp, f"__browser_{_skey}__"))

# 并行查询
with ThreadPoolExecutor(max_workers=5) as pool:
    futures = {pool.submit(search_sync, _skey, query): _disp
               for _disp, _skey, *_ in _active_browser_engines}
```

**收益**：删除 ~60 行 monkey-patching + 独立 Playwright 实例管理代码。

### 6.4 `advanced.py`（2 处改动）

```python
# 引擎状态 — 改前 (:200-226)
from browser_engine.session_store import load_storage_state
state = load_storage_state("deepseek")

# 改后
from browser_engine.client import all_engines_status
status = await all_engines_status()

# Snapshot 下载 — 改前 (:249-280)
FileResponse(path=str(filepath))

# 改后: httpx 反向代理到 browser-service
async with httpx.AsyncClient() as client:
    resp = await client.get(f"{base_url}/snapshot/{engine}/{filename}")
    return StreamingResponse(iter([resp.content]), media_type="video/webm")
```

---

## 七、Docker Compose（基于当前最小配置扩展）

当前 `docker-compose.yml` 只有 frontend + backend，需要扩展：

```yaml
services:
  frontend: ... # 不变

  backend:
    environment:
      - BROWSER_CN_URL=http://browser-service-cn:8091
      - BROWSER_GLOBAL_URL=http://browser-service-global:8092
    depends_on:
      browser-service-cn: { condition: service_healthy }
      browser-service-global: { condition: service_healthy }

  browser-service-cn:
    build: { context: ./services/browser-service }
    ports: ["8091:8091"]
    environment:
      - REGION=cn
      - ENGINE_LIST=deepseek,doubao,qwen,wenxin,yuanbao
      - MAX_CONCURRENT_QUERIES=3
      - PLAYWRIGHT_HEADLESS=1
    volumes:
      - browser_sessions_cn:/app/data/browser_sessions
      - snapshots_cn:/app/data/snapshots
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8091/health"]
      interval: 30s, timeout: 10s, retries: 3, start_period: 30s

  browser-service-global:
    build: { context: ./services/browser-service }  # 同一 Dockerfile
    ports: ["8092:8092"]
    environment:
      - REGION=global
      - ENGINE_LIST=chatgpt,claude,gemini,grok,copilot
      - MAX_CONCURRENT_QUERIES=3
      - PLAYWRIGHT_HEADLESS=1
    volumes:
      - browser_sessions_global:/app/data/browser_sessions
      - snapshots_global:/app/data/snapshots
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8092/health"]
      interval: 30s, timeout: 10s, retries: 3, start_period: 30s
```

### 开发简化模式

开发/测试环境可只部署一个实例：

```yaml
browser-service:
  environment:
    - REGION=all
    - ENGINE_LIST=deepseek,doubao,qwen,wenxin,yuanbao,chatgpt,claude,gemini,grok,copilot
    - MAX_CONCURRENT_QUERIES=5
```

主服务 `BROWSER_CN_URL` 和 `BROWSER_GLOBAL_URL` 指向同一地址。

---

## 八、依赖变更

### `backend/pyproject.toml`

```diff
+ httpx = "^0.27.0"
- # 如有 playwright 相关依赖，移除
```

### `services/browser-service/pyproject.toml`（新建）

```toml
[project]
name = "browser-service"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.104.1",
    "uvicorn[standard]>=0.24.0",
    "pydantic>=2.5.0",
    "playwright>=1.43.0",
    "playwright-stealth>=1.0.6",
]
```

---

## 九、实施步骤（3 Phase，逐步可验证）

### Phase 0: 准备基础模块（无破坏性）

| 步骤 | 操作 | 文件 |
|------|------|------|
| 0.1 | 读取 `backend/api_engine/base.py` 完整实现，编写自包含版本 | `services/browser-service/app/engines/base.py` |
| 0.2 | 编写 Pydantic v2 请求/响应模型 | `services/browser-service/app/models.py` |
| 0.3 | 创建目录结构 + `pyproject.toml` + `Dockerfile` | `services/browser-service/` |

**验证**: `from app.engines.base import EngineResult, Citation, EngineAdapter` 无外部依赖

### Phase 1: 创建浏览器服务

| 步骤 | 操作 | 文件 |
|------|------|------|
| 1.1 | 搬移 5 个基础设施模块（browser/anti_detect/session_store/video_store/xvfb） | `services/browser-service/app/` |
| 1.2 | 搬移 10 个引擎适配器 | `services/browser-service/app/engines/` |
| 1.3 | 编写 `main.py`（ENV 驱动 + 信号量 + 所有 API 端点） | `services/browser-service/app/main.py` |
| 1.4 | 编写 `Dockerfile`（python:3.10-slim + chromium + Xvfb + curl for healthcheck） | `services/browser-service/Dockerfile` |

**验证**:
```bash
cd services/browser-service
ENGINE_LIST=deepseek,doubao python -m uvicorn app.main:app --port 8091
curl http://localhost:8091/health
curl -X POST http://localhost:8091/search -d '{"engine":"deepseek","query":"test"}'
```

### Phase 2: 客户端库 + 调用方切换

| 步骤 | 操作 | 文件 |
|------|------|------|
| 2.1 | 添加 httpx 到 backend 依赖 | `backend/pyproject.toml` |
| 2.2 | 编写统一路由客户端 | `backend/browser_engine/client.py` |
| 2.3 | 改 `competitive_intel.py` | `backend/geo_checker/modes/competitive_intel.py` |
| 2.4 | 改 `visibility.py` | `backend/geo_checker/modes/visibility.py` |
| 2.5 | 改 `entity.py`（消除 monkey-patching） | `backend/geo_checker/modes/entity.py` |
| 2.6 | 改 `advanced.py`（引擎状态 + snapshot 反向代理） | `backend/geo/api/advanced.py` |

**验证**:
- 启动 browser-service-cn + browser-service-global
- 启动 backend
- 调用 competitive-intel / entity / visibility / snapshot 端点，确认功能正常
- 确认 entity.py 无 monkey-patching（代码审查）

### Phase 3: 集成清理

| 步骤 | 操作 | 文件 |
|------|------|------|
| 3.1 | 更新 `docker-compose.yml` | `/opt/geo/docker-compose.yml` |
| 3.2 | 改 `check_sessions.py` 调用两个实例 | `backend/scripts/check_sessions.py` |
| 3.3 | 删除 `backend/browser_engine/` 下已搬迁的文件（保留 `__init__.py` + `client.py`） | `backend/browser_engine/` |
| 3.4 | 更新 CLAUDE.md | `/opt/geo/CLAUDE.md` |
| 3.5 | 更新 `docs/playwright-微服务拆分方案.md` 为已完成状态 | `docs/` |

---

## 十、验证清单

### 功能验证

- [ ] `ENGINE_LIST=deepseek` 启动，`POST /search` 返回正确结果
- [ ] `ENGINE_LIST=all` 启动，10 个引擎均可查询
- [ ] 主服务 + 两个 browser-service，competitive intel 端点正常
- [ ] 主服务 + 两个 browser-service，entity 端点正常（10 引擎并行）
- [ ] entity.py 不再 import playwright / 不再有 monkey-patching
- [ ] `GET /snapshot/{engine}/{filename}` 通过主服务反向代理可下载

### 容错验证

- [ ] 停掉 browser-service-cn → 主服务不崩溃，国内引擎返回 error
- [ ] 停掉 browser-service-global → 主服务不崩溃，海外引擎返回 error
- [ ] 两个实例独立停启互不影响

### 并发验证

- [ ] 国内实例发 5 个请求，仅 3 个并行执行，其余排队
- [ ] 海外实例同上

### 运维验证

- [ ] `GET /sessions` 两个实例分别返回正确引擎状态
- [ ] `GET /engines` 返回正确的 region 和引擎列表
- [ ] 登录脚本 + PUT 上传 → session 刷新成功
- [ ] `docker-compose up --build` 全栈正常

---

## 十一、与旧方案的差异总结

| 维度 | 旧方案 | 本方案 |
|------|--------|--------|
| 代码基线 | 假设已实施部分 | 明确"零已实施"，从实际状态出发 |
| Python 版本 | 3.10-slim | 3.10-slim（旧 Dockerfile 用 3.9+poetry，新服务用 3.10+pip） |
| 依赖管理 | 未明确 httpx 状态 | httpx 已安装但未在 pyproject.toml，需显式添加 |
| Docker Compose | 从零设计 | 基于现有最小配置扩展，保留 frontend 服务 |
| 实施节奏 | Phase 0-4 | Phase 0-3（合并旧 Phase 3+4），更紧凑 |
| base.py 来源 | 未明确从哪搬 | 明确从 `backend/api_engine/base.py` 复制完整实现 |
