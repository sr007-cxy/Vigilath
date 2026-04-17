# GEO Readiness Checker 性能报告

**日期**: 2026-04-16  
**服务地址**: www.vigilath.com  
**后端进程**: uvicorn geo.main:app  
**运行路径**: /home/ubuntu/Dev/geo/backend  
**监听端口**: 127.0.0.1:8070  
**反向代理**: nginx → proxy_pass http://127.0.0.1:8070/api/

---

## 1. 故障现象

用户在前端执行 GEO 检测时，频繁出现 **"Failed to run GEO check"** 错误提示，无法获取检测结果。

---

## 2. 日志分析

### 2.1 nginx access log 关键时间线

| 时间 (UTC) | 端点 | 状态码 | 说明 |
|---|---|---|---|
| 14:48:38 | /api/check/anonymous | 200 | 正常返回 |
| 14:48:41 | /api/check/anonymous | 200 | 正常返回（缓存命中） |
| 14:48:44 | /api/check/anonymous | **429** | 触发匿名用户限流 |
| 14:54:40 | /api/check/anonymous | **499** | 客户端在响应前断开连接 |
| 14:55:00 | /api/check/anonymous | **499** | 同上 |
| 14:55:08 | /api/check/anonymous | **499** | 同上 |
| 14:55:27 | /api/check/anonymous | **499** | 同上 |
| 14:55:41 | /api/check/anonymous | **499** | 同上 |
| 14:55:56 | /api/check/anonymous | **499** | 同上 |

**499** 为 nginx 特有状态码，表示客户端在服务器完成响应前主动断开了 TCP 连接。

### 2.2 增加 worker 后仍复现 499

将 uvicorn worker 从 1 增至 4 后，问题依旧：

| 时间 (UTC) | 请求 | 状态码 | 说明 |
|---|---|---|---|
| 15:31:07 | GET / | 200 | 用户打开首页 |
| 15:31:16 | POST /api/check/anonymous | 200 | 检测成功（9s），跳转 Result 页 |
| 15:31:17 | GET /assets/Result-*.js | 200 | Result 页懒加载 JS 资源 |
| 15:31:34 | POST /api/check/anonymous | 200 | 在 Result 页 rerun 检测，成功 |
| **15:31:37** | **GET /** | **200** | **用户按返回键，回到首页** |
| 15:31:48 | POST /api/check/anonymous | **499** | 新检测发起，用户再次离开 |
| 15:31:52 | POST /api/check/anonymous | **499** | 重试 |
| 15:31:54 | POST /api/check/anonymous | **499** | 重试 |
| **15:32:07** | **GET /** | **200** | **用户又按返回键** |
| 15:32:16 | POST /api/check/anonymous | **499** | 再次失败 |

**关键证据**：每一轮 499 之前都有一个 `GET /`（首页），说明用户在检测进行中**按了浏览器返回键或刷新**，浏览器导航离开时自动断开了未完成的 HTTP 连接。

### 2.3 AEO 高级检测端点 503

| 时间 (UTC) | 端点 | 状态码 |
|---|---|---|
| 14:09:06 | /api/check/advanced/aeo | **503** |
| 14:09:23 | /api/check/advanced/aeo | **503** |

503 由 `backend/geo/api/advanced.py:71` 的 `_run_or_raise` 捕获 `RuntimeError` 抛出，原因为 API key 缺失或无效。

### 2.4 nginx error log

无 GEO 相关错误。error.log 中全部为 Zen7/Home 项目的 directory index forbidden 及外部安全扫描请求（JNDI 注入探测），与 GEO 服务无关。

---

## 3. 根因分析

### 3.1 核心问题：用户导航离开导致请求中断（前端）

**这是 499 的根本原因，优先级最高。**

**调用链路**:

```
用户提交 URL → Home.tsx 发起 axios POST → 等待 25-30s → 用户按返回键/刷新
                                                           ↓
                                              浏览器导航到新页面，断开 TCP 连接
                                                           ↓
                                              nginx 记录 499，后端丢弃响应
                                                           ↓
                                              Home 组件重新挂载，isLoading 重置为 false
                                                           ↓
                                              用户再次提交 → 循环重复
```

**关键代码** (`frontend/src/pages/Home.tsx:91-106`):

```javascript
geoApi
  .checkGeo({ url: formattedUrl })
  .then((result) => {
    navigate('/result', { state: { result } });
  })
  .catch((err) => {
    setError(err.message);           // ← 用户看到 "Failed to run GEO check"
  })
  .finally(() => {
    setIsLoading(false);
  });
```

**问题**：
- 按钮在 loading 时已 disabled (`Home.tsx:157`)，但**浏览器返回键/刷新不受控制**
- 没有使用 `AbortController` 管理请求生命周期
- 没有 `beforeunload` 事件拦截导航离开
- 组件卸载后重新挂载，`isLoading` 重置为 `false`，丢失检测进度

**CheckProgress 组件分析** (`components/result/CheckProgress.tsx`):

CheckProgress 是纯前端模拟进度组件，与服务端**没有任何实时通信**：

- 组件挂载后通过 `Date.now()` 计时，每 250ms 更新一次 `elapsed` 状态
- 每个阶段的时长是硬编码的权重数组（`STAGE_WEIGHTS`），default 模式共 7 个阶段、34 秒走完
- 进度条上限锁在 95%，不会到 100%
- 当 API 响应返回后 `isLoading` 变为 `false`，组件直接卸载；如果 API 比预估慢（>34s），最后一个阶段停住显示 "almost done"
- 它是全屏遮罩 overlay（`fixed inset-0 z-50`），可以遮挡页面内的点击，**但无法拦截浏览器返回键**——用户按返回键时 React Router 直接卸载整个 Home 组件（连带 CheckProgress），浏览器断开连接 → 499

**结论**：CheckProgress 的 UI 反馈已经足够好（阶段步骤、环形进度条、耗时计时），但它的问题不在于展示，而在于**它挂载在 Home 页面上**。用户按返回键后，Home 卸载 → CheckProgress 消失 → 请求中断。解决方案是将检测逻辑移至 Result 页面执行（详见第 6.1 节）。

### 3.2 次要问题：后端串行锁（已缓解）

**调用链路**:

```
浏览器 → nginx → uvicorn → asyncio.to_thread(run_geo_check) → _geo_checker_lock
```

**关键代码** (`backend/geo/services/geo_checker.py:82`):

```python
_geo_checker_lock = threading.Lock()   # 全局锁，同一时刻仅允许 1 个检测

with _geo_checker_lock:
    _gc_module.generate_score(url, ...)
```

**影响**：
- 未缓存 URL 的检测耗时约 **25-30 秒**（实测 news.ycombinator.com 耗时 25.7s）
- 全局 `threading.Lock` 将同一 worker 内的请求串行化
- 用户 499 断开后，后端仍在持锁执行直到完成，浪费资源

**已采取措施**：uvicorn worker 从 1 增至 4，可并行处理 4 个检测。但这只缓解了并发瓶颈，未解决用户导航离开的根本问题。

### 3.3 次要问题：AEO 端点 API key 配置

`/api/check/advanced/aeo` 返回 503，源自 `_run_or_raise` 中的 `RuntimeError` 异常捕获。该异常在 AI API key 缺失或无效时触发。需检查 `.env` 中相关 key 的配置状态。

### 3.4 架构瓶颈汇总

| 瓶颈 | 现状 | 影响 | 状态 |
|---|---|---|---|
| 用户导航离开中断请求 | 无拦截机制 | **499 的根本原因** | 待修复 |
| 前端无耗时预估提示 | 仅 loading 动画 | 用户焦虑，触发返回/刷新 | 待修复 |
| 前端无 AbortController | 组件卸载不取消请求 | 幽灵请求占用后端资源 | 待修复 |
| uvicorn worker 数 | 4 个 | 可并行 4 个检测 | **已修复** |
| `_geo_checker_lock` | 全局 threading.Lock | 同一 worker 内串行 | 建议优化 |
| 单次检测耗时（未缓存） | ~25-30s | 用户体验差 | 待优化 |
| 缓存策略 | 内存缓存，TTL 1h，各 worker 独立 | 重启丢失，worker 间不共享 | 建议优化 |

---

## 4. 服务状态

### 4.1 服务器资源

| 项目 | 值 |
|---|---|
| CPU | 4 核 Neoverse-N1，负载 0.04，空闲 ~97% |
| 内存总计 | 15,762 MB |
| 内存已用 | 3,793 MB |
| 内存可用 | 11,968 MB |
| Swap | 无 |

### 4.2 常驻服务

| 服务 | 端口 | 内存 | systemd 服务 |
|---|---|---|---|
| GEO backend (4 workers) | 8070 | ~126 MB × 4 (初始，峰值 ~750 MB/worker) | geo-checker.service |
| PetBuddy backend | 8081 | 475 MB | petbuddy-backend.service |
| Seller backend | 8010 | 128 MB | seller-backend.service |
| ChatGptApp (Next.js) | 3000 | 206 MB | chatgptapp.service |
| MySQL | 3306 | 373 MB | - |
| ~~Vigilath.com backend~~ | ~~8082~~ | ~~487 MB~~ | ~~vigilath-backend.service~~ (已停用) |

> Vigilath.com backend (端口 8082) 经确认无 nginx 路由指向，属于闲置进程，已于本次排查中 stop + disable，释放 487 MB 内存。

### 4.3 GEO 服务检测

| 检测项 | 结果 |
|---|---|
| 进程存活 | Master + 4 Workers，运行中 |
| Worker 初始内存 | ~126 MB/worker |
| 缓存命中响应 | 0.01s (200 OK) |
| 未缓存 URL 响应 | ~25.7s (200 OK) |
| SQLite 数据库 | 可读写，无锁定 |
| 外网连通性 | 正常 |

---

## 5. 已完成的优化

| 操作 | 效果 |
|---|---|
| uvicorn --workers 1 → 4 | 并行处理能力从 1 提升至 4 |
| 停用闲置 vigilath-backend.service | 释放 487 MB 内存 |

---

## 6. SSE 流式端点现状

后端和前端均已实现 SSE 流式检测，但**当前未被使用**：

| 组件 | 状态 | 文件 |
|---|---|---|
| 后端 `GET /api/geo/stream` | 已实现 | `backend/geo/api/geo.py:350` |
| 前端 `geoApi.runGeoCheckStream()` | 已实现 | `frontend/src/services/geoApi.ts:118` |
| Home.tsx / Result.tsx 调用 | **未使用，仍走一次性 POST** | `frontend/src/pages/Home.tsx:91` |

**后端 SSE 实现方式**：
- `asyncio.create_task` 异步执行检测任务，与 SSE 连接解耦
- 每 2 秒 yield 一次进度事件，客户端断开不影响后台任务继续执行
- 任务通过内存 `tasks` 字典跟踪状态（pending → running → completed/failed）
- 进度同样为模拟递增（每次 +5%，上限 90%），非真实检测阶段进度

**前端 SSE 实现方式**：
- 使用浏览器原生 `EventSource` 接收 SSE 事件
- 监听 `status` 事件，在 completed/failed 时关闭连接
- 返回 cleanup 函数 `() => eventSource.close()`

**当前限制**：
- `EventSource` 不支持自定义 header，无法传递 Bearer token
- 登录用户走 SSE 只能降级为 free tier
- 要支持认证用户需改用 `fetch` streaming 或 query token 方案

---

## 7. 待修复项

### 7.1 前端：将检测逻辑移至 Result 页面 (优先级: 高)

**这是当前 "Failed to run GEO check" 的根本解决方案。**

**核心思路**：保留 CheckProgress 的完整用户体验（阶段步骤、环形进度条、耗时计时），只改变它**挂载的位置**——从 Home 页移到 Result 页。

**当前流程**：
```
Home 提交 URL → Home 发起 POST 请求 → Home 显示 CheckProgress（等 25-30s）
                                        ↓ 用户按返回键
                                     Home 卸载 → CheckProgress 消失 → 请求中断 → 499
```

**改后流程**：
```
Home 提交 URL → 立即 navigate('/result', { state: { url } })
                  ↓
               Result 页挂载 → 检测到有 url 无 result → 发起 POST 请求
                  ↓
               Result 页显示 CheckProgress（用户已在目标页，无动机按返回键）
                  ↓
               API 返回 → CheckProgress 卸载 → 渲染检测结果
```

**改动点**：

1. **`Home.tsx`**：提交后不再调 API，直接 `navigate('/result', { state: { url: formattedUrl } })`
2. **`Result.tsx`**：`useEffect` 检测到 `location.state` 中有 `url` 但无 `result` 时，调用 `geoApi.checkGeo()` 发起检测，期间显示 CheckProgress
3. **`CheckProgress.tsx`**：无需修改，保持原有的模拟进度 UI 体验

**优势**：
- CheckProgress 的视觉体验完全保留（阶段动画、环形进度、耗时提示）
- 用户在 Result 页等待，按返回键 = 主动放弃（合理行为）
- Result 页已有 `rerunLoading` 和 CheckProgress 的使用先例，基础设施齐全
- 改动量小：仅涉及 Home.tsx 和 Result.tsx 两个文件，CheckProgress 不动

### 7.2 后端：锁 timeout (优先级: 中)

- `_geo_checker_lock` 增加 timeout：`lock.acquire(timeout=60)` 超时后返回 503 而非无限等待
- 涉及文件：`backend/geo/services/geo_checker.py:82`

### 7.3 缓存：跨 worker 共享 (优先级: 中)

- 当前每个 worker 进程有独立的 `_cache`，同一 URL 可能被不同 worker 重复检测
- 考虑迁移至 Redis，实现跨 worker 缓存共享，且进程重启后缓存不丢失

### 7.4 AEO 端点 (优先级: 中)

- 检查 `.env` 中 AI API key 配置（OPENAI_API_KEY / PERPLEXITY_API_KEY / ANTHROPIC_API_KEY）
- 对 API key 缺失场景在前端给出明确提示，而非通用错误信息

---

## 8. 参考文件

| 文件 | 说明 |
|---|---|
| `frontend/src/pages/Home.tsx:91` | 前端检测触发逻辑，499 根因所在 |
| `frontend/src/components/result/CheckProgress.tsx` | 纯前端模拟进度组件 |
| `frontend/src/services/geoApi.ts:14` | axios timeout 配置（300s） |
| `frontend/src/services/geoApi.ts:118` | SSE 流式检测客户端（已实现未使用） |
| `backend/geo/services/geo_checker.py:82` | 全局锁 `_geo_checker_lock` 定义及使用 |
| `backend/geo/api/geo.py:256` | `/check/anonymous` 路由，调用 `asyncio.to_thread` |
| `backend/geo/api/geo.py:350` | `/geo/stream` SSE 端点（已实现未使用） |
| `backend/geo/api/advanced.py:64-71` | `_run_or_raise`，503 错误来源 |
| `/etc/nginx/nginx.conf` | nginx 反向代理配置 |
| `/var/log/nginx/access.log` | nginx 访问日志 |
| `/etc/systemd/system/geo-checker.service` | systemd 服务配置（已更新为 --workers 4） |


---

## 9. 后续

2026-04-17 的深度分析、真实耗时实测、瓶颈排序和新增的计时日志基础设施已整理为独立文件：[`performance-report-2026-04-17.md`](./performance-report-2026-04-17.md)。
