# GEO Checker — Issue List

> 本文是 issue 处理文档(单文件、不接 GitHub Issues)。与 `ENHANCEMENT.md` 并列:
> - `ENHANCEMENT.md` — **功能增量**("要做什么新功能")
> - `issue_list.md`(本文)— **问题与处理**("发现什么、怎么修、验收标准")
>
> 每个 issue 自成一节,包含分级、根因、处理方案摘要、验收标准;
> 详细实施代码与回滚见 `docs/性能处理方案.md`(性能类)或对应 commit。
>
> **阅读顺序**:先看 [0. 整体状态](#0-整体状态)快速了解,再看具体章节。

---

## 0. 整体状态

### 0.1 当前在跟进(Open)

| ID | 优先级 | 领域 | 标题 | 预期收益 |
|---|---|---|---|---|
| [#1](#1-check_cross_platform-串行探测-10-个社交平台) | **P0** | backend | `check_cross_platform` 串行探测 | 默认 check 120 s → 30 s |
| [#2](#2-visibility-90-次-openrouter-调用8-并发) | **P0** | backend | `/visibility` 90 次 AI 调用 | visibility 180 s → 40–60 s |
| [#3](#3-前端检测仍在-home-页触发用户返回刷新即断连) | **P0** | frontend | 前端检测触发改到 Result 页 | UX 型 499 清零 |
| [#4](#4-check_brand_entity_kg-wiki-调用串行) | P1 | backend | `check_brand_entity_kg` Wiki 串行 | -5 到 -10 s |
| [#5](#5-check_authority_trust-认证源串行) | P1 | backend | `check_authority_trust` 认证源串行 | -3 到 -5 s |
| [#6](#6-citation-主循环顺序执行--timesleep) | P1 | backend | `/citation` 主循环顺序执行 | 60 s → 15 s |
| [#7](#7-_run_or_raise-三种故障混映射-503) | P1 | backend | `_run_or_raise` 错误码混淆 | 可观测性提升 |
| [#8](#8-_page_cache-不跨-worker进程重启丢失) | P2 | backend/infra | `_page_cache` 跨 worker 共享 | 二次检测 < 1 s |
| [#9](#9-_geo_checker_lock-全局串行) | P2 | backend | 去 `_geo_checker_lock` | 单 worker 真并发 |

### 0.2 近期已关闭(Closed)

| ID | 关闭 commit | 标题 |
|---|---|---|
| [#R1](#r1-usdc-支付钱包爆栈) | `b9306e4` | USDC 支付钱包爆栈 `Maximum call stack size exceeded` |
| [#R2](#r2-后端缺少-per-check-耗时可观测性) | `da3a8d9` | 后端缺少 per-check 耗时可观测性 |
| [#R3](#r3-性能文档膨胀难以区分事件与参考) | `fae9152` / `c97dd49` / `c8466ee` | 性能文档体系重构 |
| [#R4](#r4-uvicorn-单-worker-并发上限) | 2026-04-16 | uvicorn 单 worker 并发上限 |

---

## Open — P0 当前批次

### #1 `check_cross_platform` 串行探测 10 个社交平台

- **Priority**: P0
- **Status**: Open
- **Area**: backend(核心引擎)

**症状**:默认 check 对"社交矩阵弱"的站点(baidu / zh-CN 工具站)实测能跑到 2–3 分钟。journald 取样 baidu.com 为 166 秒。

**根因**:`check_cross_platform` 顺序探测 10 个社交平台(X / LinkedIn / YouTube / GitHub / Reddit / Facebook / Instagram / Medium / TikTok / Quora),每个 `timeout=8 s`,最坏 80 s 只花在这一个 check。

**涉及文件**:
- `geo_checker.py:2784`(根,高级路径)
- `geo_checker/__main__.py:1851`(默认 API 路径)
- **两份都要改,改动保持一致**

**处理方案**:
- 把主循环包成 `ThreadPoolExecutor(max_workers=10)`,每个平台独立提交 `_probe_single()`
- `_probe_single` 内不 `print`(避免多线程输出交错),把展示逻辑放主线程按字典遍历
- 并发数上限 = 10(平台数),不需要更高

**验收**:
- 计时日志 `func=check_cross_platform elapsed_ms` 稳定 < 10000 ms
- 对 baidu.com 跑一次,`block=default_check elapsed_ms` 从 ~120 s 降到 < 40 s
- 各平台"是否找到"的字段与改前一致(用一个已知多平台入驻的品牌对照)

**详细实施**:见 [`docs/性能处理方案.md §1`](./docs/性能处理方案.md)

---

### #2 `/visibility` 90 次 OpenRouter 调用,8 并发

- **Priority**: P0
- **Status**: Open
- **Area**: backend(高级引擎)

**症状**:近两天 nginx 上 `/api/check/advanced/visibility` 出现 3 次 499,全部是响应超过 axios 300 s 超时。代码自估 180 s,上游 AI 波动时很容易撞顶。

**根因**:`total_api_calls = len(all_queries) × len(engines) × STABILITY_RUNS = 10 × 3 × 3 = 90`。并发度只有 8 → 12 批次 → 最坏 12 × 45s(单次 AI 超时)= 540s。

**涉及文件**:
- `geo_checker.py:5900`(`STABILITY_RUNS = 3`)
- `geo_checker.py:5951`(`ThreadPoolExecutor(max_workers=8)`)

**处理方案**:
- `STABILITY_RUNS` 3 → 1(取消每 query 跑三次求平均,调用数 90 → 30)
- `max_workers` 8 → 16(批次数减半)
- 先发 1,盯 3 天评分稳定性,P95 偏差 > 10 分再考虑回到 `STABILITY_RUNS = 2`

**验收**:
- `block=advanced:ai_visibility elapsed_ms` 稳定 < 60 s
- `/visibility` 的 499 次数降到 0
- 同一 URL 连续跑 3 次的 AI Visibility Score 偏差 ≤ 5 分

**详细实施**:见 [`docs/性能处理方案.md §2`](./docs/性能处理方案.md)

---

### #3 前端检测仍在 Home 页触发,用户返回/刷新即断连

- **Priority**: P0
- **Status**: Open
- **Area**: frontend

**症状**:近两天 nginx 16 次 499 里,**13 次**来自 `/api/check/anonymous` 或 `/api/check`。journald 对应后端检测一路跑完,用户浏览器早就断开。

**根因**:`Home.tsx` 发 axios POST 后,CheckProgress 组件挂在 Home 页;用户等不及(25 s+)按浏览器返回或刷新 → Home 卸载 → 请求取消 → nginx 记 499。后端无感知,继续空跑到完成。

**涉及文件**:
- `frontend/src/pages/Home.tsx`(提交后不再调 API,直接 navigate)
- `frontend/src/pages/Result.tsx`(mount 时根据 `location.state` 发起检测)
- `frontend/src/services/geoApi.ts`(加 `signal?: AbortSignal` 参数)

**处理方案**:
- Home 提交 → `navigate('/result', { state: { url } })`,用户立即进入目标页
- Result mount 时 `useEffect` 检测 `state.url` 且无 `state.result` → 起 `AbortController` 调 API
- 返回/卸载时 `controller.abort()`,后端仍会收到请求(无法从浏览器层取消),但前端不再认为是异常
- `state.result` 在 API 返回后用 `navigate('.', { state, replace: true })` 写回,刷新也不重复检测

**验收**:
- 用户在 Result 页按返回键:axios `CanceledError` 被捕获,不再弹 "Failed to run GEO check"
- nginx 上 `/api/check(anonymous)` 的 499 次数降到 0
- 深链直接访问 `/result` 无 `state`:自动 redirect 到 `/`

**详细实施**:见 [`docs/性能处理方案.md §3`](./docs/性能处理方案.md)

---

## Open — P1 下一批次

### #4 `check_brand_entity_kg` Wiki 调用串行

- **Priority**: P1
- **Status**: Open
- **Area**: backend

**根因**:`geo_checker.py:1538` 附近,Wikipedia search / Wikipedia backlinks / Wikidata search 三个外调串行。

**处理方案**:`ThreadPoolExecutor(max_workers=3)` 并发三个调用,主线程汇总结果。

**验收**:`func=check_brand_entity_kg elapsed_ms` 从 400–600 ms 降到 150–250 ms。

---

### #5 `check_authority_trust` 认证源串行

- **Priority**: P1
- **Status**: Open
- **Area**: backend

**根因**:`geo_checker.py:1336` 附近,bio 页抓取 + 多个认证源(Medium / Substack / Forbes / HBR / arxiv / ORCID / Google Scholar)顺序访问。

**处理方案**:同 #4,`ThreadPoolExecutor(max_workers=5)` 并发。

**验收**:`func=check_authority_trust elapsed_ms` 从 400–1000 ms 降到 200–400 ms。

---

### #6 `/citation` 主循环顺序执行 + `time.sleep()`

- **Priority**: P1
- **Status**: Open
- **Area**: backend

**根因**:`geo_checker.py:5310` 起,5–7 条 Perplexity query 顺序跑 + 每条间 `time.sleep(1)` + 429 重试时 `time.sleep(5)`。上游偶发慢一次就累加到 60 s+;上游全失败直接抛 `RuntimeError → 503`。

**处理方案**:
- 主循环改 `ThreadPoolExecutor(max_workers=min(7, len(queries)))`
- 保留 429 处理,但把 `time.sleep(5)` 改成 exponential backoff(1s / 2s / 4s),避免占满 worker
- 去掉 query 间的 `time.sleep(1)`(原本是防 rate-limit,并发时 OpenRouter 的 60 RPM 免费档仍然安全)

**验收**:`block=advanced:citation_check elapsed_ms` 从 30 000–60 000 ms 降到 10 000–20 000 ms。

---

### #7 `_run_or_raise` 三种故障混映射 503

- **Priority**: P1
- **Status**: Open
- **Area**: backend

**症状**:前端在任何失败场景下都看到 "Failed to run advanced check",无法区分"请稍后再试"(上游暂挂)与"请检查输入"(目标 URL 错误)。

**根因**:`backend/geo/api/advanced.py:64-75` 的 `except RuntimeError` 把下列三种故障笼统映射到 503:
1. API key 缺失 / 无效(真正的 503)
2. 上游 AI 全失败(应是 502 Bad Gateway)
3. 目标 URL 抓不到(`aeo_visibility` 的 bare return → `advanced_runners._silent_call` → RuntimeError)(应是 400 Bad Request)

**处理方案**:
- 新建 `backend/geo/utils/errors.py`,定义三类 `MissingApiKeyError` / `UpstreamAIError` / `TargetUnreachableError`
- `geo_checker.py` 中原先 `raise RuntimeError(...)` 的地方改抛对应类(需要改 `_load_geo_checker_core` 注入)
- `_run_or_raise` 按类型分别映射 503 / 502 / 400
- 前端 `ApiError.status` 已有,`CheckoutPending` 和 `Advanced` 页按 status 给出具体文案

**验收**:
- 目标 URL 填错(`https://this-does-not-exist.example`),端点返回 **400**
- 临时撤 `OPENROUTER_API_KEY`,端点返回 **503**
- 上游全挂(模拟),端点返回 **502**
- 前端 "Failed to run ..." 文案按场景分化

---

## Open — P2 重构型待评估

### #8 `_page_cache` 不跨 worker,进程重启丢失

- **Priority**: P2
- **Status**: Open(设计评审中)
- **Area**: backend + infra

**根因**:`geo_checker.py:201` 的 `_page_cache` 是进程内 dict,4 workers 各一份,重启全丢。同一 URL 被不同 worker 打到时会重复全量抓取。

**处理方向**:迁 Redis 做共享缓存。key = URL,value = gzipped body + status + 关键 headers,TTL 1 h。

**前置依赖**:需要运维在 EC2 上起 Redis(或用 managed),约定大 body 的截断策略(> 1 MB 丢弃),避免 thundering herd(miss 时加 SingleFlight 锁)。

---

### #9 `_geo_checker_lock` 全局串行

- **Priority**: P2
- **Status**: Open(设计评审中,依赖 #7)
- **Area**: backend(重构)

**根因**:`backend/geo/services/geo_checker.py:26` 的 `threading.Lock` 把单 worker 内的请求完全串行化。锁本身合理(核心 `geo_checker.py` 用了 `_scores` / `_page_cache` / `SHOW_FIX` 等模块级全局态 + `redirect_stdout` 改 `sys.stdout`),后果是 4 workers = 理论并发上限 4。

**处理方向**:把核心文件的模块级状态挪成 `contextvars.ContextVar` 或函数显式参数;`redirect_stdout` 改成线程局部代理(`advanced_runners.py` 里的 `_ThreadLocalStdout` 已有范式)。

**前置依赖**:必须先完成 #7,让异常路径可观测,否则重构过程中的 regression 很难定位。

---

## Closed

### #R1 USDC 支付钱包爆栈

- **Closed**: `b9306e4`(2026-04-17)
- **Area**: frontend

**症状**:USDC 支付按下后弹出 `could not coalesce error (error={ "message": "Maximum call stack size exceeded" }, payload={ "id": 2, "jsonrpc": "2.0", "method": "eth_requestAccounts" ... })`。

**根因**:多钱包扩展共存(MetaMask + Phantom / Coinbase / OKX 等),彼此用 Proxy / getter 在 `window.ethereum` 上互相 wrap。ethers v6 `BrowserProvider.send('eth_requestAccounts')` 的 provider 能力探测会递归穿透这条 proxy 链,V8 栈溢出被包成 `code=UNKNOWN_ERROR`。

**修复**:
- `frontend/src/pages/CheckoutPending.tsx` 新增 `pickInjectedProvider()`:
  1. EIP-6963 `requestProvider` 事件,每个钱包自报 provider,优先 `io.metamask`
  2. 回退 `window.ethereum.providers[]` 数组,优先 `isMetaMask && !isBraveWallet`
  3. 最后兜底裸 `window.ethereum`
- `eth_requestAccounts` 的初始握手改走钱包原生 `ethereum.request()`,**绕开** ethers 的递归路径
- 握手成功后再用 `new BrowserProvider(ethereum)` 做后续切链/签名

**经验**:
- 多钱包生态下 ethers v6 直接传 `window.ethereum` 已经不安全,EIP-6963 应该成为默认路径
- 首次握手用原生 `request` 是稳妥范式,后续其他 web3 场景沿用

---

### #R2 后端缺少 per-check 耗时可观测性

- **Closed**: `da3a8d9`(2026-04-17)
- **Area**: backend(基础设施)

**症状**:排查"哪个 check 慢"只能靠在 geo_checker.py 里手动塞 `time.time()`,改一次测一次,不持续。

**修复**:
- 新增 `backend/geo/utils/timing.py`:`time_block` 上下文管理器 + `instrument_checks` / `instrument_funcs` 幂等 monkey-patch
- `backend/geo/main.py` 加 HTTP 中间件,对 `/api/check*` 记 `method / path / status / elapsed_ms`,所有响应挂 `X-Process-Time` 头
- `backend/geo/services/geo_checker.py` 对 `geo_checker.__main__` 的 25 个 `check_*` + `generate_score` 装饰
- `backend/geo/services/advanced_runners.py` 对根 `geo_checker.py` 的 25 个 `check_*` + 7 个 runner 装饰
- 日志走 `geo.timing` logger,落到 journald

**使用方式**:见 [`docs/performance-guide.md §3`](./docs/performance-guide.md)

---

### #R3 性能文档膨胀,难以区分事件与参考

- **Closed**: `fae9152` / `c97dd49` / `c8466ee`(2026-04-17)
- **Area**: docs

**症状**:单个 `performance-report-2026-04-16.md` 既要做事件记录又要承载反查参考,越写越长。

**修复**:拆成四份,角色各异:

| 文件 | 角色 |
|---|---|
| `docs/performance-guide.md` | 常驻参考(架构/耗时分布/诊断 playbook) |
| `docs/performance-report-2026-04-16.md` | 04-16 事件快照(UX 型 499) |
| `docs/performance-report-2026-04-17.md` | 04-17 事件快照(baidu 166 s + 计时日志上线) |
| `docs/性能处理方案.md` | 执行计划(P0–P2 代码骨架 / 验收 / 回滚) |

本文 `issue_list.md` 是第五份,充当索引 + 简版 handling 说明。

---

### #R4 uvicorn 单 worker 并发上限

- **Closed**: 2026-04-16
- **Area**: infra

**症状**:单 worker 时,一个慢检测会让后续请求全堆积。

**修复**:`/etc/systemd/system/geo-checker.service` 的 `ExecStart` 加 `--workers 4`。

**备注**:此修复**只是缓解**——并发上限从 1 提到 4,但单 worker 内仍由 `_geo_checker_lock` 串行,根本解决见 [#9]。

---

## 附录 A:Issue 生命周期约定

### A.1 新增

- 在对应优先级段落下新增小节,ID 按时间顺序递增(`#1, #2, ...`),不跳号、不复用
- 必填字段:**Priority / Status / Area / 症状 / 根因 / 处理方案摘要 / 验收**
- 详细实施(代码骨架、回滚)放 `docs/性能处理方案.md` 或对应 ad-hoc 文档,本表只链接

### A.2 进行中

- 状态改为 `In Progress`,在 `Status` 行补 `owner: <name>` 和 `branch: <branch-name>`
- 如有 PR,追加 `PR: <url>`

### A.3 关闭

- 把小节**整体**搬到 "Closed" 段,按倒序排列(最新的在最上)
- `Status` 替换成 `Closed: <commit-sha>(<date>)`
- 补一段 `经验`(1–3 行),记录踩过的坑或复用范式;重要的关闭项同步到 commit message

### A.4 优先级变更

- 只改 Priority 行,不移动位置,不重新编号
- 在小节末尾加一行 `Priority changed from <P_old> to <P_new> on <date>: <reason>`

---

## 附录 B:模板

### B.1 新增 issue 骨架

```markdown
### #N 标题(名词短语,动词+宾语)

- **Priority**: P0 / P1 / P2
- **Status**: Open
- **Area**: backend / frontend / infra / docs

**症状**:用户侧看到什么 / 日志里观察到什么。

**根因**:系统里为什么会这样,指向具体文件与行号。

**处理方案**:
- 关键动作 1
- 关键动作 2
- 关键动作 3

**验收**:
- 可量化指标 1
- 可量化指标 2

**详细实施**:(若存在)`docs/xxx.md §N`
```

### B.2 关闭条目追加模板

```markdown
### #RN 标题

- **Closed**: `<sha>`(<date>)
- **Area**: ...

**症状** / **根因** / **修复** / **经验**(4 段,每段 2–6 行)
```
