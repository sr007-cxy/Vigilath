# GEO Readiness Checker 性能指南

> 这是一份**常驻参考文档**，描述系统的性能特征、瓶颈分布、诊断工具和优化方向。不记录具体事件，事件与排查过程见 `performance-report-YYYY-MM-DD.md` 系列。

---

## 1. 架构速览

一次检测请求完整经过的四层：

```
浏览器(axios, timeout=300s)
   ↓ HTTPS (Cloudflare terminate)
nginx :80 (proxy_read_timeout=600s, proxy_buffering=off)
   ↓
FastAPI :8070 (uvicorn --workers=4)
   ↓ asyncio.to_thread → threadpool(40 threads)
geo_checker 核心逻辑
   ↓ 串行/并发 HTTP 调用
外部 HTTP（目标站点 + 社交平台 + Wikipedia + OpenRouter AI）
```

关键超时阶梯（从外到内）：

| 层 | 超时 | 文件 |
|---|---|---|
| axios（客户端） | **300 s** | `frontend/src/services/geoApi.ts:14` |
| nginx `proxy_read_timeout` | 600 s | `/etc/nginx/nginx.conf` |
| 单次 AI 调用 | 45 s | `geo_checker.py:5514` 等 |
| 单次页面 fetch | 15 s | `geo_checker.py:201` |
| 社交平台探测 | 8 s × 10 | `geo_checker.py:2788` |

**最短木板** = axios 300 s。后端必须保证 P99 耗时 < 300 s，否则会出现 nginx 499（客户端主动断开）。

---

## 2. 耗时去向

### 2.1 默认 `/api/check` / `/api/check/anonymous`

核心是 `generate_score()`，顺序跑 25 个 `check_*` 函数。典型耗时（本文撰写时 `httpbin.org` 数据）：

| 阶段 | 典型耗时 | 说明 |
|---|---|---|
| `check_trust_safety` | 2.5–3.0 s | 抓多条 trust/verify 页 |
| `check_cross_platform` | 2.5–80 s | 10 个社交平台串行 probe（单个 hang 可达 8 s） |
| `check_technical_crawlability` | 1.0–2.0 s | redirect / canonical 抓取 |
| `check_authority_trust` | 0.4–1.0 s | bio 页 + 多认证源 |
| `check_brand_entity_kg` | 0.4–0.6 s | Wikipedia/Wikidata |
| 其他 20 个 check | 累计 1–3 s | 各自 < 500 ms |

**典型总时**：10–30 s（"干净"站点），**异常总时**：120–180 s（社交矩阵空、外链探测大量超时）。

### 2.2 `/api/check/advanced/visibility`

最重的端点，结构性耗时：

```
total_api_calls = len(all_queries) * len(engines) * STABILITY_RUNS
                = 10            * 3            * 3
                = 90 次 OpenRouter 调用
```

并发度 `ThreadPoolExecutor(max_workers=8)`，代码自估 `~180 s`，实测与上游 AI 响应速度强相关：

| 情况 | 耗时 |
|---|---|
| 上游顺畅 | 40–90 s |
| 上游偶发慢 | 120–200 s |
| 上游部分超时 | 200–400 s（**可能撞 axios 300 s → 499**） |

### 2.3 `/api/check/advanced/citation`

5–7 条 Perplexity query **串行** + 每条间 `time.sleep(1)` + 429 重试 `time.sleep(5)`。典型 30–60 s，上游全失败时 `RuntimeError → 503`。

### 2.4 `/api/check/advanced/aeo`（免费路径）

单页静态分析，无外调 AI。成功时 < 5 s。若抓不到目标 URL → bare return → `RuntimeError → 503`（错误码不精准，见 4.6）。

### 2.5 其他高级端点

| 端点 | 典型耗时 | 主要外调 |
|---|---|---|
| `/compare` | 10–30 s × n URL | 多站并行跑 `run_silent` |
| `/crawl-test` | < 3 s | robots.txt 抓取 |
| `/authority` | 5–15 s | Wikipedia/Wikidata/GitHub/npm/PyPI |
| `/entity` | 30–90 s | OpenRouter 多引擎查询 |

---

## 3. 计时日志

### 3.1 日志格式

后端启动后自动记录，走 Python logger `geo.timing`，落到 journald：

```
INFO geo.timing:func=check_cross_platform elapsed_ms=2519
INFO geo.timing:func=generate_score elapsed_ms=10539
INFO geo.timing:block=default_check url=https://example.com elapsed_ms=10539
INFO geo.timing:block=advanced:ai_visibility elapsed_ms=185320
INFO geo.timing:http method=POST path=/api/check/anonymous status=200 elapsed_ms=10556
```

- **`func=…`** — 单个 `check_*` 或顶层 runner
- **`block=…`** — 整段业务逻辑（不含 HTTP 开销）
- **`http …`** — FastAPI 入口到响应返回

所有响应还会挂 `X-Process-Time: <毫秒数>` 头，浏览器 Network 面板直接能看到。

### 3.2 常用查询命令

```bash
# 近 10 分钟所有 check 耗时
sudo journalctl -u geo-checker.service --since "10 minutes ago" | grep 'geo.timing'

# 只看 HTTP 层汇总
sudo journalctl -u geo-checker.service | grep 'geo.timing:http'

# 列出 > 5 s 的慢检测
sudo journalctl -u geo-checker.service | grep 'geo.timing' | \
  awk -F'elapsed_ms=' '$2+0 > 5000'

# 按 check_ 名聚合最大耗时
sudo journalctl -u geo-checker.service --since "1 hour ago" | grep 'geo.timing:func=' | \
  awk -F'[= ]' '{print $4, $6}' | sort | awk '{a[$1]=($2>a[$1])?$2:a[$1]} END{for(k in a)print a[k], k}' | sort -n
```

### 3.3 基础设施位置

| 文件 | 职责 |
|---|---|
| `backend/geo/utils/timing.py` | `time_block` / `instrument_checks` / `instrument_funcs` |
| `backend/geo/main.py` | `/api/check*` HTTP 中间件 + `X-Process-Time` 头 |
| `backend/geo/services/geo_checker.py` | 默认路径 monkey-patch |
| `backend/geo/services/advanced_runners.py` | 高级路径 monkey-patch |

装饰器幂等（`_geo_timed` 哨兵），重复 import 不会双重包装。

---

## 4. 当前已知瓶颈（按优先级）

### 4.1 `check_cross_platform` 串行外链探测（P0）

**位置**：`geo_checker.py:2784`

10 个社交平台（X / LinkedIn / YouTube / GitHub / Reddit / Facebook / Instagram / Medium / TikTok / Quora）串行 probe，每个 `timeout=8 s`。最坏 80 s 仅此一个 check。

**修复**：整段改 `ThreadPoolExecutor(max_workers=10)`，<20 行改动。预期默认 check 120 s → 30 s。

### 4.2 前端无 AbortController（P0）

**位置**：`frontend/src/pages/Home.tsx:91`

用户在 Home 页等待期间按浏览器返回键 / 刷新时，请求被浏览器中断，nginx 记 499。后端继续空跑到完成，浪费资源。

**修复**：
- 提交后立即 `navigate('/result', { state: { url } })`，在 Result 页触发 API
- 使用 `AbortController` 绑定组件卸载生命周期
- 详细方案见 `performance-report-2026-04-16.md` 第 7.1 节

### 4.3 `/visibility` 结构性重（P0）

**位置**：`geo_checker.py:5900`（`STABILITY_RUNS=3`）、`geo_checker.py:5951`（`max_workers=8`）

90 次 OpenRouter 调用 / 8 并发 → 自估 180 s。撞 axios 300 s 的概率不低。

**修复**：
- `STABILITY_RUNS` 3 → 1：调用数 90 → 30，时间 180 s → 60 s
- `max_workers` 8 → 16：批次数减半

### 4.4 外调串行未并发（P1）

| 函数 | 串行外调数 | 潜在并发收益 |
|---|---|---|
| `check_brand_entity_kg` | 3（Wikipedia search + backlinks + Wikidata） | 省 5–10 s |
| `check_authority_trust` | 多个认证源 | 省 3–5 s |
| `/citation` 主循环 | 5–7 条 Perplexity | 60 s → 15 s |

都是直接 `ThreadPoolExecutor` 包一下即可。

### 4.5 `_geo_checker_lock` 串行（P1）

**位置**：`backend/geo/services/geo_checker.py:26`

核心 `geo_checker.py` 用模块级全局态（`_scores` / `_page_cache` / `SHOW_FIX`）+ `redirect_stdout` 改 `sys.stdout`，所以必须加锁。结果是单 worker 内请求完全串行，4 workers → 理论并发上限 4。

**修复**（重构型）：把模块级全局态挪成 `ContextVar` 或函数参数，`redirect_stdout` 用线程局部代理（`advanced_runners.py` 已经用 `_ThreadLocalStdout` 做到了这点）。去锁后单 worker 可支持 threadpool 内真正的并发。

### 4.6 `_run_or_raise` 错误码语义失真（P1）

**位置**：`backend/geo/api/advanced.py:64-75`

```python
except RuntimeError as e:
    raise AppException(status_code=503, message=str(e))
```

`RuntimeError` 被笼统映射成 503，但实际覆盖三种故障：

1. API key 缺失/无效（真正的 503）
2. 上游 AI 全失败（应是 502）
3. 目标 URL 抓不到（应是 400/502）

前端永远只看到 `"Failed to run advanced check"`，无法区分。

**修复**：定义专用异常类（`UpstreamAIError` / `TargetUnreachableError` / `MissingApiKeyError`），分别映射 502 / 400 / 503。

### 4.7 缓存不跨 worker（P2）

**位置**：`geo_checker.py:201`（`_page_cache` 是进程内 dict）

每个 uvicorn worker 一份缓存，同一 URL 被不同 worker 打到会重复抓。重启缓存全丢。

**修复**：迁 Redis / SQLite，跨 worker 共享 + 进程重启保留。

### 4.8 前端无耗时预估（P2）

用户不知道要等多久，焦虑 → 返回键 → 499。

**修复**：`CheckProgress` 组件已经有阶段动画，但时长是硬编码。可以根据 URL 的历史耗时数据（从计时日志反写回 DB）做动态预估。

---

## 5. 性能诊断 Playbook

### 5.1 症状：前端 "Failed to run ..." 

**第一步**：看 nginx 返回的真实状态码（前端兜底文案盖掉了）。

```bash
sudo grep -E 'POST /api/check' /var/log/nginx/access.log | tail -20
```

| 状态码 | 含义 | 下一步 |
|---|---|---|
| 499 | 客户端提前断开 | 看 `X-Process-Time` 是否快到 300 s（axios 超时）；排查是否 UX 型（用户按返回键） |
| 503 | RuntimeError | 看 journalctl 区分三种情况（4.6 节） |
| 500 | 其他异常 | 看 `advanced:<fn>` 和 `func=` 日志 |
| 502 | 后端未响应 | `systemctl status geo-checker.service` |
| 429 | 匿名配额用尽 | 正常业务返回 |

### 5.2 症状：某次检测明显变慢

```bash
# 找到对应请求
sudo journalctl -u geo-checker.service --since "<时刻>" | grep 'geo.timing' | head -50

# 看每个 check 的耗时，定位具体哪个热点
# 典型答案：check_cross_platform 超过 10 s → 某平台 probe 超时
```

### 5.3 症状：某端点频繁 499

```bash
# 统计各端点 499 次数
sudo grep -E '" 499' /var/log/nginx/access.log | awk '{print $7}' | sort | uniq -c | sort -rn

# 对于同一端点，看 journalctl 里对应 `block=` 的实际耗时
# - 实际 > 300 s → 后端太慢（优化）
# - 实际 < 300 s 但 nginx 记 499 → 用户中途断开（UX 问题）
```

### 5.4 症状：全线变慢

```bash
# 资源占用
ps aux | grep geo-checker
free -m
ss -ltnp | grep 8070

# upstream AI 情况
sudo journalctl -u geo-checker.service | grep -E 'http_[45][0-9][0-9]' | tail
```

---

## 6. 优化路线图

按实施顺序列出，每项都能独立部署：

| 阶段 | 动作 | 预期收益 | 改动量 |
|---|---|---|---|
| 1 | `check_cross_platform` 改 ThreadPool(10) | 默认 check 120 s → 30 s | < 20 行 |
| 2 | `/visibility`: `STABILITY_RUNS` 3→1, workers 8→16 | visibility 180 s → 40–60 s | < 10 行 |
| 3 | Home 提交直接 navigate Result + AbortController | 消除 UX 型 499 | 前端 ~50 行 |
| 4 | `check_brand_entity_kg` / `check_authority_trust` 并发化 | 默认 check -5 到 -10 s | 30–50 行 |
| 5 | `/citation` 主循环改并发 | 60 s → 15 s | < 30 行 |
| 6 | `_run_or_raise` 分异常类型映射 | 可观测性；前端精确提示 | < 50 行 |
| 7 | `_page_cache` 迁 Redis | 二次检测 < 1 s；重启保留 | 需运维 |
| 8 | 核心去全局态 → 去 `_geo_checker_lock` | 单 worker 真并发 | 重构（大） |

完成 1-3 后，默认 check 和 visibility 都应稳定在 < 60 s，498/499/503 异常率应降到可忽略。

---

## 7. 关键文件速查

### 后端

| 文件 | 作用 |
|---|---|
| `backend/geo/main.py` | FastAPI app 入口 + HTTP 计时中间件 |
| `backend/geo/api/geo.py` | `/check` / `/check/anonymous` / SSE 路由 |
| `backend/geo/api/advanced.py` | 所有 `/check/advanced/*` 路由 + `_run_or_raise` |
| `backend/geo/services/geo_checker.py` | 默认路径封装 + `_geo_checker_lock` |
| `backend/geo/services/advanced_runners.py` | 高级路径封装 + 按文件路径加载根 `geo_checker.py` |
| `backend/geo/utils/timing.py` | 计时装饰器与 context manager |

### 核心引擎（两份）

| 文件 | 用途 |
|---|---|
| `geo_checker/__main__.py` | 默认路径使用（`geo_checker.__main__`） |
| `geo_checker.py`（项目根） | 高级路径使用（`importlib` 加载） |

**注意**：这两份 **不是同步的**，`geo_checker.py` 是最新完整版，`__main__.py` 是历史残留。重构时优先合并。

### 前端

| 文件 | 作用 |
|---|---|
| `frontend/src/services/geoApi.ts` | axios 配置、各端点调用 |
| `frontend/src/pages/Home.tsx` | 检测触发页（当前仍在 Home 发 API，UX 型 499 根因） |
| `frontend/src/pages/Result.tsx` | 结果页，rerun 也在此 |
| `frontend/src/components/result/CheckProgress.tsx` | 纯前端模拟进度条 |

### 运维

| 路径 | 作用 |
|---|---|
| `/etc/systemd/system/geo-checker.service` | 后端 systemd unit（`--workers 4`） |
| `/etc/nginx/nginx.conf` | nginx，内含 `server_name www.vigilath.com` |
| `/var/log/nginx/access.log` | 访问日志 |
| `sudo journalctl -u geo-checker.service` | 后端日志（含 `geo.timing`） |

---

## 8. 历史性能调查报告

| 文件 | 主题 |
|---|---|
| [`performance-report-2026-04-16.md`](./performance-report-2026-04-16.md) | UX 型 499 根因 + worker 1→4 扩容 + SSE 现状 |
| [`performance-report-2026-04-17.md`](./performance-report-2026-04-17.md) | 真实耗时实测（baidu 166 s） + 瓶颈细化 + 计时日志上线 |
