# GEO Readiness Checker 性能报告（二）

**日期**: 2026-04-17
**服务地址**: www.vigilath.com
**后端进程**: uvicorn geo.main:app
**运行路径**: /home/ubuntu/Dev/geo/backend
**监听端口**: 127.0.0.1:8070

> 本文是 [`performance-report-2026-04-16.md`](./performance-report-2026-04-16.md) 的后续。昨天那份报告聚焦于 "用户导航离开造成 499" 的 UX 侧根因；本文在此基础上补充后端真实耗时的实测数据、对瓶颈做细化排序，并落地一套持续可用的计时日志基础设施。

---

## 1. 后端真实耗时实测与新瓶颈

昨天的结论假设默认 check 耗时约 25-30s，今天通过 `journalctl` 逐请求对齐时间戳后发现这个数字被**严重低估**，以下是补充事实与修正。

### 1.1 实测耗时（直接来自后端日志）

| 目标 | 后端起止 | 真实耗时 | nginx 上对应状态 |
|---|---|---|---|
| https://baidu.com | `01:51:33 Starting` → `01:54:19 completed` | **166 秒** | 客户端 20s 时断开，记 499；后端仍空跑 146s |

**关键洞察**：默认 check 对"弱社交/跨境访问受阻"类站点（baidu、zh.*、cn.* 等）可以跑到 **2-3 分钟**，远超前端 axios 300s 超时线的安全余量。

### 1.2 新的首要瓶颈：`check_cross_platform` 串行外链探测

**位置**：`geo_checker.py:2784`

```python
for plat_name, plat_info in platforms_to_probe.items():  # 最多 10 个平台
    for probe_url in plat_info["probe_urls"]:            # 每平台 1-2 个 URL
        r = requests.get(probe_url, timeout=8, ...)      # 串行
```

**平台清单**：X / LinkedIn / YouTube / GitHub / Reddit / Facebook / Instagram / Medium / TikTok / Quora。

**问题**：

- 所有 probe 串行执行，互不依赖却没用并发
- us-east-1 EC2 访问部分平台会被 rate-limit 或 TCP 慢启动，单次平台可以 **hang 满 8s**
- 最坏情况 10 × 8s = **80s 仅此一个 check**
- 对"没有社交矩阵"的站点影响最大（zh-CN 站、小众工具站）

**修复建议**：整段改为 `ThreadPoolExecutor(max_workers=10)` 并发探测。改动量 <20 行，纯后端。

### 1.3 其他被重新确认的瓶颈

全文件 `grep 'requests\.' geo_checker.py | wc -l` = **58 次** HTTP 调用，几乎全串行。另外两个次级外调热点：

| 函数 | 行号 | 串行调用 |
|---|---|---|
| `check_brand_entity_kg` | `geo_checker.py:1538+` | Wikipedia search → Wikipedia backlinks → Wikidata search 共 3 次外调 |
| `check_authority_trust` | `geo_checker.py:1336+` | bio 页抓取 + 多个认证源 |

### 1.4 `/check/advanced/visibility` 的结构性重量（不变）

再次确认参数：

- `engines` = 3（Perplexity / ChatGPT / Claude via OpenRouter）
- `all_queries` ≈ 10
- `STABILITY_RUNS` = 3（`geo_checker.py:5900`）
- → **每请求 ~90 次 OpenRouter 调用**
- `ThreadPoolExecutor(max_workers=8)`（`geo_checker.py:5951`）→ 12 批次
- 代码自估 `Estimated time: ~{total_api_calls * 2}s` = **~180s**
- 前端 axios 300s 超时 → 单次慢响应即触发 499

近两天 nginx 上 `/visibility` 出现 3 次 499，**全都发生在此路径**。

### 1.5 错误映射搅在一起（新发现）

`backend/geo/api/advanced.py:64-75` 的 `_run_or_raise` 把 `RuntimeError` 一律映射成 **503**，但它实际覆盖三种完全不同的故障：

1. API key 缺失/无效（真正的 503）
2. 上游 AI 调用全失败（应是 502）
3. **目标 URL 抓不到**（应是 400/502）

特别是第 3 条：`aeo_visibility`（`geo_checker.py:3821`）在 `get_soup(url)` 失败时 **bare return**，随后 `_silent_call`（`advanced_runners.py:89-93`）因返回值不是 dict 抛 `RuntimeError`，最终被包成 503。这解释了昨天 14:09 的两次 `/aeo` 503 **并非 API key 问题**，而是目标站点抓取失败。

### 1.6 当前 499 分布细化

近两天 nginx log 统计：

| 端点 | 次数 | 本质原因 | 对应修复 |
|---|---|---|---|
| `/api/check/anonymous` | 11 | 用户按返回键 / 刷新（UX） | 见 04-16 报告 7.1 前端改造 |
| `/api/check` | 2 | 同上（登录态） | 同上 |
| `/api/check/advanced/visibility` | 3 | 后端真的跑 >300s | 1.4 精简参数 |

### 1.7 按收益重排的修复清单

| 优先级 | 动作 | 预期效果 | 涉及文件 |
|---|---|---|---|
| **P0** | `check_cross_platform` 改 `ThreadPoolExecutor(max_workers=10)` | 默认 check **~120s → ~30s** | `geo_checker.py:2784` |
| **P0** | 前端触发改到 Result 页 + `AbortController`（04-16 报告 7.1 方案） | 消除所有 UX 型 499 | `Home.tsx` / `Result.tsx` |
| **P0** | `/visibility` 的 `STABILITY_RUNS` 3 → 1，`max_workers` 8 → 16 | visibility **~180s → ~40s** | `geo_checker.py:5900, 5951` |
| P1 | `check_brand_entity_kg` 三个 Wiki 调用并行 | -5 to -10s | `geo_checker.py:1538` |
| P1 | `/citation` 顺序循环改并发 | ~60s → ~15s | `geo_checker.py:5310` |
| P1 | `_run_or_raise` 按异常细类型映射 400/502/503 | 可观测性 + 前端可区分提示 | `backend/geo/api/advanced.py:64` |
| P2 | `_page_cache` 迁 Redis，跨 worker 共享 | 二次检测 <1s；重启保留 | `geo_checker.py:201` |
| P2 | 去掉 `_geo_checker_lock`：把模块级全局态（`_scores` / `_page_cache` / `SHOW_FIX`）挪为 `ContextVar` 或函数参数 | 单 worker 内可真正并发 | `geo_checker.py`（重构） |

### 1.8 本次没动的事项

- 后端 systemd 配置（保持 `--workers 4`）
- nginx 超时（保持 `proxy_read_timeout 600s`）
- 前端 axios 超时（保持 300s）
- 数据库（本次仅前端发布 + 后端重启，未触碰 DB）

---

## 2. 新增 check 耗时日志基础设施

为验证上面的瓶颈假设并持续追踪线上耗时分布，新增了一套轻量的计时日志基础设施，所有结果走 Python logger `geo.timing`，默认随 journald 存到 `geo-checker.service` 日志。

### 2.1 改动清单

| 文件 | 内容 |
|---|---|
| `backend/geo/utils/timing.py`（新建） | `time_block(label)` 上下文管理器、`instrument_checks(module)` / `instrument_funcs(module, names)` 模块级 monkey-patch 装饰器。幂等、带 `_geo_timed` 哨兵防重复包装。 |
| `backend/geo/main.py` | 新增 HTTP 中间件：仅对 `/api/check*` 记录 `method / path / status / elapsed_ms`。所有响应挂上 `X-Process-Time` 头（毫秒）。 |
| `backend/geo/services/geo_checker.py` | 模块加载时对 `geo_checker.__main__` 上所有 `check_*` 函数和 `generate_score` 包一层计时；`run_geo_check` 内的 `generate_score` 调用外再套 `time_block("default_check url=…")`。 |
| `backend/geo/services/advanced_runners.py` | 对根目录 `geo_checker.py`（通过 importlib 加载的 `geo_checker_core`）同样 patch 所有 `check_*` + 7 个顶层 runner（`compare_urls` / `crawl_test` / `authority_audit` / `citation_check` / `ai_visibility` / `entity_audit` / `aeo_visibility` / `generate_score`）。`_silent_call` 内加 `time_block("advanced:<fn>")`。 |

### 2.2 日志格式

```
INFO geo.timing:func=check_cross_platform elapsed_ms=2519
INFO geo.timing:func=generate_score elapsed_ms=10539
INFO geo.timing:block=default_check url=https://httpbin.org elapsed_ms=10539
INFO geo.timing:block=advanced:ai_visibility elapsed_ms=185320
INFO geo.timing:http method=POST path=/api/check/anonymous status=200 elapsed_ms=10556
```

- `func=…` — 单个 `check_*` 或顶层 runner 的实际耗时
- `block=default_check url=…` / `block=advanced:<fn>` — 端到端的检测逻辑耗时（不含 HTTP 开销）
- `http …` — nginx 到响应返回的总时长，含 FastAPI 中间件、序列化、网络

### 2.3 首次验证结果（httpbin.org，02:18 UTC）

| 指标 | 值 |
|---|---|
| `http /api/check/anonymous` | **10556ms**（HTTP 200） |
| `block=default_check` | **10539ms**（去掉 HTTP 层的 17ms 开销） |
| `func=generate_score` | 10539ms |
| 单步 Top 3 | `check_trust_safety` 2975ms / `check_cross_platform` 2519ms / `check_technical_crawlability` 1565ms |

httpbin.org 属于"干净"站点，社交探测大多秒级失败。真实瓶颈需要对 baidu.com、zh-CN 站点等跑一次才能完全暴露，但 **`check_cross_platform` 已稳坐前二**，符合 1.2 节的假设。

### 2.4 使用方式

```bash
# 看最近所有 check 耗时
sudo journalctl -u geo-checker.service --since "10 minutes ago" | grep 'geo.timing'

# 只看 HTTP 层汇总
sudo journalctl -u geo-checker.service | grep 'geo.timing:http'

# 找到 5s 以上的慢检测
sudo journalctl -u geo-checker.service | grep 'geo.timing' | awk -F'elapsed_ms=' '{print $2, $0}' | awk '$1 > 5000'
```

浏览器侧也可以直接从 `Network` 面板读 `X-Process-Time` 响应头做初筛，无需登录服务器。

### 2.5 性能开销

每个 `check_*` 调用多加一次 `time.monotonic()` + 1 行 info 日志 ≈ 微秒级；每请求约 25 行额外日志，journald 本地写入不成问题。HTTP 中间件只对 `/api/check*` 生效，其他 endpoint（`/api/auth/me`、`/api/user-membership` 等高频轮询）不会产生日志噪声。

---

## 3. 下一步

当前线上已经能产出完整耗时数据。下一步的工作顺序按 1.7 的优先级推进：

1. **`check_cross_platform` 并发化**（P0，改动最小，收益最大）
2. **前端触发改到 Result 页 + AbortController**（P0，消除 UX 型 499）
3. **`/visibility` 参数收敛**（P0，把 `STABILITY_RUNS` 3→1 是一行改动）

在动手前先挂一两天计时日志，拿到真实用户流量的 `func=` 和 `block=` 分布，让后续优化有对比基线。

---

## 4. Post-refactor 基线 + review(当天晚些时候)

package 重构、check_trust_safety / check_cross_platform 并发化、SQLite pragma 优化全部 ship 后,做了一次完整性能 review,结论颠覆了上午的优先级排序。

### 4.1 实测对比

| URL | 当天上午(refactor 前) | 当天晚些(refactor 后) | 变化 |
|---|---|---|---|
| example.com | 10.5 s | **5.0 s** | -52% |
| baidu.com | 166 s | **124 s** | -25% |

**已验证有效的修复**:
- `check_trust_safety`: 55 s → **6.3 s**(-48 s)
- `check_cross_platform`: 3.8 s → 0.8 s

### 4.2 发现 refactor 引入的可观测性 bug(已修复)

重构后第一次压测 baidu.com,计时日志里**只看到 `generate_score` 一项**,25 个 `check_*` 全部不见。

**根因**:`orchestrate.py` 模块加载时 `from .checks import check_https, ...`,在自己的 `__dict__` 里建立了本地绑定。`CHECK_REGISTRY` 的 lambda `lambda url, sm: check_https(url)` 的名字解析走 orchestrate 的 globals —— **不是** `_gc_checks` 那份。而 `instrument_checks(_gc_checks)` 只包装了 `_gc_checks.__dict__`。

**修复**(`f1495f2`):额外调 `instrument_checks(_gc_orchestrate)`,让 orchestrate 的本地绑定也被包装。两处 services(geo_checker.py + advanced_runners.py)都要加。

**经验**:Python 的 `from foo import bar` 是把 `bar` **拷贝到当前模块的命名空间**,不是引用代理。monkey-patch 源模块的属性不会改变其他模块里的本地副本。

### 4.3 baidu.com 完整 top-10(post-refactor)

```
42118 ms  check_technical_crawlability     34%  ← 新头号
16667 ms  check_authority_trust            13%
12377 ms  check_well_known                 10%
 9627 ms  check_search_engine_registration  8%
 8431 ms  check_llms_txt                    7%
 7394 ms  check_robots_txt                  6%
 6205 ms  check_trust_safety                5%  (已优化)
 6181 ms  check_url_normalization           5%
 5991 ms  check_sitemap                     5%
 4244 ms  check_ai_optimization             3%
 其余 15 项                                 <1%
```

Top-10 合计 95% 总时。每一项都是 HTTP fetch 类 —— baidu 对每个路径都慢 5-10 秒。

### 4.4 洞察:真正的银弹是顶层并发

25 个 `check_*` **互相独立**(只有 `check_multi_page` 依赖 `check_sitemap` 的返回)。串行跑是最大的结构浪费。

并发化的理论收益:
- baidu 124 s → **~42 s**(bound by 最慢的 `check_technical_crawlability`)
- example 5 s → **~1.5 s**

这比**任何单个 check 的内部优化收益都大**。上午我优化了 `check_cross_platform`(80 s 最坏 → 3.8 s),但它占 baidu 总时只有 2%。**顶层不并发,局部优化的收益天花板就定死了**。

### 4.5 被事实打脸的 2 个原假设

| 原假设 | 实测 | 修正 |
|---|---|---|
| `check_cross_platform` 是头号瓶颈(80 s 最坏) | 0.8 s | 上午改完就不再是问题 |
| `check_brand_entity_kg` 是 P1(Wiki 串行) | **0.44 s** | 根本不慢 —— close #4,不值得投入 |

两个错误都源自**代码阅读的最坏情况估算**,没先看计时日志。

### 4.6 issue 重排

经本次 review 更新 `issue_list.md`:

- **新增 #14**:顶层 `generate_score` 并发化 —— **P0 头号**
- **#13** 从 P1 升级到 P0:post-refactor 稳定 42 s,是顶层并发后的单请求最慢 check
- **#11** 从 P0 降级到 P1:顶层并发后不再独立影响总时
- **close #4**:实测 440 ms 不慢

详见 `docs/performance-guide.md` §6 路线图 + `docs/issue_list.md`。

### 4.7 结论

今天完成的 5 项硬交付(package 重构 / trust_safety / cross_platform / SQLite pragma / 计时日志 bug 修复)**整理了房间,但真正的性能银弹还没打出**。

下一批(P0):
1. 顶层并发化 `generate_score`(#14,4-6 h)
2. `check_technical_crawlability` 内部并发(#13,1-2 h)
3. `/visibility` 参数收敛(#2,10 min)

做完预期 baidu 124 s → **17-25 s**,example 5 s → **1.5 s**。
