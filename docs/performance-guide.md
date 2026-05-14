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

核心是 `generate_score()`，**顺序**跑 25 个 `check_*` 函数。耗时由外部 HTTP 响应速度主导，同一份代码跑不同站点差距极大:

**2026-04-17 实测（post-refactor 基线）**:

| URL | 总耗时 | Top 5 单项 |
|---|---|---|
| example.com（快站） | **5.0 s** | cross_platform 1.1s · technical_crawl 1.0s · trust_safety 0.7s · brand_entity_kg 0.6s · authority_trust 0.4s |
| baidu.com（慢站) | **124 s** | technical_crawl **42.1s** · authority_trust **16.7s** · well_known 12.4s · search_reg 9.6s · llms_txt 8.4s |

**慢站 top-10 耗时分布**（baidu.com,占总 95%）:

| 函数 | 耗时 | 占比 | 说明 |
|---|---|---|---|
| `check_technical_crawlability` | 42.1 s | 34% | 最坏 22 次 HTTP（canonical + redirect + curl HTTP/2 + 6 feed 探测 + 10 API 探测） |
| `check_authority_trust` | 16.7 s | 13% | bio 页 + 多个认证源串行 |
| `check_well_known` | 12.4 s | 10% | `/.well-known/*` 多路径探测 |
| `check_search_engine_registration` | 9.6 s | 8% | 搜索引擎验证文件 |
| `check_llms_txt` | 8.4 s | 7% | llms.txt + llms-full.txt |
| `check_robots_txt` | 7.4 s | 6% | 单一 /robots.txt |
| `check_trust_safety` | 6.2 s | 5% | **已优化**（原 55 s） |
| `check_url_normalization` | 6.2 s | 5% | HTTP/HTTPS + www 变体 |
| `check_sitemap` | 6.0 s | 5% | /sitemap.xml |
| `check_ai_optimization` | 4.2 s | 3% | meta 检查 |
| 其他 15 项合计 | <1.5 s | <1% | |

**关键洞察**:
1. 每一项都是 HTTP fetch 类,baidu 的"慢"源自目标服务器响应慢（每个 404 都要 2-7 秒）
2. 25 个 check **互相独立**,串行跑是最大的结构浪费 —— 见 §4.1 顶层并发
3. 单个 check 内部的并行化收益有限,真正的银弹是 **所有 check 一起并发**

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
sudo journalctl -u geo.service --since "10 minutes ago" | grep 'geo.timing'

# 只看 HTTP 层汇总
sudo journalctl -u geo.service | grep 'geo.timing:http'

# 列出 > 5 s 的慢检测
sudo journalctl -u geo.service | grep 'geo.timing' | \
  awk -F'elapsed_ms=' '$2+0 > 5000'

# 按 check_ 名聚合最大耗时
sudo journalctl -u geo.service --since "1 hour ago" | grep 'geo.timing:func=' | \
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

### 4.1 顶层 `generate_score` 顺序执行 25 个 check（**P0 头号**）

**位置**：`backend/geo_checker/orchestrate.py::_run_checks`

`generate_score` 串行调用 25 个 `check_*`,总时 = Σ 所有 check。baidu 实测 124 s,其中最慢单个 check 42 s。

**修复**：改 `ThreadPoolExecutor(max_workers=10)` 一次性 submit 所有 check。依赖:
- `check_sitemap` 必须先跑,其返回的 `sitemap_urls` 传给 `check_multi_page`
- 其余 24 个互相独立,全并发

**前置**：`state._scores` 加 `threading.Lock`（其 `+=` 累加非线程安全）。`_geo_checker_lock` 在顶层并发后仍保留,但实际作用下降（每请求内部已并发,请求间由 threadpool 隔离）。

**预期收益**:baidu 124 s → **~42 s**（-66%）,example 5 s → **~1.5 s**（-70%）。

### 4.2 `check_technical_crawlability` 内部 22 次 HTTP 串行（**P0**）

**位置**：`backend/geo_checker/checks.py::check_technical_crawlability`

最坏 22 次 HTTP（canonical 解析 + redirect test + curl HTTP/2 探测 + 6 feed 路径 + 10 API 路径）。baidu 实测 **42 s** —— 顶层并发后仍是单请求里的最慢 check,决定总时底线。

**修复**：内部 4 组独立调用（redirect / curl / feed probe / api probe）拆成 `ThreadPoolExecutor`。

**预期收益**:该 check 42 s → **~10 s**。配合 §4.1,baidu 总时进一步到 ~17 s。

### 4.3 `/visibility` 结构性重（**P0**）

**位置**：`backend/geo_checker/modes/visibility.py`（`STABILITY_RUNS=3` + `max_workers=8`）

90 次 OpenRouter 调用 / 8 并发 → 自估 180 s。撞 axios 300 s 超时的概率不低。独立于默认路径,单独评估。

**修复**：
- `STABILITY_RUNS` 3 → 1：调用数 90 → 30,时间 180 s → 60 s
- `max_workers` 8 → 16：批次数减半

### 4.4 `_geo_checker_lock` 串行 + 模块全局态（P1,§4.1 前置）

**位置**：`backend/geo/services/geo_checker.py::_geo_checker_lock`

核心 package 用模块级全局态（`state._scores` / `state._page_cache` / `state.SHOW_FIX`）+ `redirect_stdout` 改 `sys.stdout`,所以必须加锁。单 worker 内请求完全串行,4 workers → 理论并发上限 4。

**修复(分两步)**：
1. **局部锁**（§4.1 前置,2 h）:给 `_scores` 和 `_page_cache` 的读写各自加 `threading.Lock`,允许多个 check 同时跑。`redirect_stdout` 改成 `advanced_runners._ThreadLocalStdout` 的同款线程局部代理
2. **彻底去全局态**（大重构,后期）:挪成 `ContextVar` / 显式参数

### 4.5 `check_authority_trust` 内部串行（P1）

**位置**：`backend/geo_checker/checks.py::check_authority_trust`

bio 页抓取 + 多个认证源（Medium / Substack / Forbes / HBR / arxiv / ORCID / Google Scholar）顺序访问。baidu 实测 **16.7 s**。顶层并发后仍是 top-2,但不再独立影响总时（被 §4.2 覆盖）。

**修复**:`ThreadPoolExecutor(max_workers=5)` 内部并行。

### 4.6 `_run_or_raise` 错误码语义失真（P1）

**位置**：`backend/geo/api/advanced.py:64-75`

```python
except RuntimeError as e:
    raise AppException(status_code=503, message=str(e))
```

`RuntimeError` 被笼统映射成 503,实际覆盖三种故障:

1. API key 缺失/无效（真正的 503）
2. 上游 AI 全失败（应是 502）
3. 目标 URL 抓不到（应是 400/502）

前端永远只看到 `"Failed to run advanced check"`,无法区分。

**修复**：定义专用异常类（`UpstreamAIError` / `TargetUnreachableError` / `MissingApiKeyError`）,分别映射 502 / 400 / 503。

### 4.7 `/citation` 顺序 + sleep（P1）

`modes/citation.py` 5-7 条 Perplexity query 串行 + `time.sleep(1)` 间隔 + 429 重试 `time.sleep(5)`。**修复**:改并发 + exponential backoff。预期 60 s → 15 s。

### 4.8 缓存不跨 worker（P2）

**位置**：`backend/geo_checker/state.py::_page_cache`（进程内 dict）

每个 uvicorn worker 一份缓存,同一 URL 被不同 worker 打到会重复抓。重启缓存全丢。

**修复**：迁 Redis / SQLite,跨 worker 共享 + 进程重启保留。

### 4.9 前端无耗时预估（P2）

用户不知道要等多久,焦虑 → 返回键。由于 #3 前端改造已生效（Home→Result + AbortController）,499 问题已解决,本项仅为体验优化。

**修复**：`CheckProgress` 按 URL 历史耗时数据（从计时日志反写回 DB）做动态预估。

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
| 502 | 后端未响应 | `systemctl status geo.service` |
| 429 | 匿名配额用尽 | 正常业务返回 |

### 5.2 症状：某次检测明显变慢

```bash
# 找到对应请求
sudo journalctl -u geo.service --since "<时刻>" | grep 'geo.timing' | head -50

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
systemctl status geo.service
free -m
ss -ltnp | grep 8070

# upstream AI 情况
sudo journalctl -u geo.service | grep -E 'http_[45][0-9][0-9]' | tail
```

---

## 6. 优化路线图（2026-04-17 重排）

按"投入/收益"排序,每项可独立部署。**前 3 项做完 baidu 这类慢站从 124s 降到 17s 级别**。

| # | 阶段 | 动作 | 预期收益 | 改动量 |
|---|---|---|---|---|
| 1 | **顶层并发化** | `_run_checks` 改 ThreadPoolExecutor + `_scores` / `_page_cache` 加锁 | **baidu 124 s → 42 s**, example 5 s → 1.5 s | 100-150 行 |
| 2 | **`check_technical_crawlability`** 内部并发 | redirect/curl/feed/api 4 组并行 | baidu 42 s → 10 s, 配合 #1 总时 17 s | 50-80 行 |
| 3 | **`/visibility`** 参数收敛 | `STABILITY_RUNS` 3→1, `max_workers` 8→16 | visibility 180 s → 40-60 s | < 10 行 |
| 4 | `check_authority_trust` 内部并发 | bio + 认证源并行 | 默认 check 再 -8 s(配合 #1 贡献显现) | 30-50 行 |
| 5 | `/citation` 主循环改并发 | Perplexity 5-7 query 并发 + backoff | 60 s → 15 s | < 30 行 |
| 6 | `_run_or_raise` 分异常类型映射 | 可观测性 + 前端精确提示 | 不降速,降告警噪声 | < 50 行 |
| 7 | `_page_cache` 迁 Redis | 二次检测 < 1 s,进程重启保留 | 需运维 | |
| 8 | 核心去全局态 | 彻底移除 `_geo_checker_lock` | 单 worker 真并发(目前被 #1 的局部锁方案覆盖 90%) | 重构(大) |

### 已完成的(从路线图移除,避免歧义)

- ~~`check_cross_platform` 并发化~~ ✓ `7610d4b` / `e8b4b04`,baidu 3.8 s → 0.8 s
- ~~`check_trust_safety` 23 URL 并发~~ ✓ `e8b4b04`,baidu 55 s → 6.2 s
- ~~Home 提交改 navigate + AbortController~~ ✓ `e036bcb`,UX 型 499 清零
- ~~geo_checker 单文件 → package~~ ✓ `693aa6d`,3 份副本 → 1
- ~~SQLite 5 pragma 优化~~ ✓ `64970a2`

### 完成 P0(#1-3)后的预期基线

| URL | 当前(post-refactor) | P0 完成后 |
|---|---|---|
| example.com | 5.0 s | **1.5 s** |
| baidu.com | 124 s | **17-25 s** |
| 极端慢站 | 数百秒 | < 60 s |
| /visibility | 180 s+ | < 60 s |

---

## 7. 关键文件速查

### 后端 FastAPI 层

| 文件 | 作用 |
|---|---|
| `backend/geo/main.py` | FastAPI app 入口 + HTTP 计时中间件 |
| `backend/geo/api/geo.py` | `/check` / `/check/anonymous` / SSE 路由 |
| `backend/geo/api/advanced.py` | 所有 `/check/advanced/*` 路由 + `_run_or_raise` |
| `backend/geo/services/geo_checker.py` | 默认路径封装 + `_geo_checker_lock` |
| `backend/geo/services/advanced_runners.py` | 高级路径封装(原生 package import,不再 importlib) |
| `backend/geo/utils/timing.py` | 计时装饰器与 context manager |
| `backend/geo/database.py` | SQLAlchemy engine + SQLite pragma tuning |

### 核心引擎 Package(2026-04-17 重构后,单一来源)

`backend/geo_checker/` 一个 package,不再有单文件副本:

| 文件 | 行数 | 作用 |
|---|---|---|
| `__init__.py` | 140 | 公共 API re-export |
| `__main__.py` | 175 | CLI 入口(`python -m geo_checker`) |
| `constants.py` | 47 | AI_BOTS / AI_CRAWLERS / 色码 |
| `state.py` | 67 | SHOW_FIX / _scores / _page_cache(**将加锁**) |
| `io.py` | 77 | fetch / get_soup / get_text_content |
| `output.py` | 738 | emit_check / emit_fix / print shim / _ZH 翻译表 |
| `checks.py` | 2958 | 25 个 check_*(核心检测逻辑) |
| `orchestrate.py` | 185 | **CHECK_REGISTRY + generate_score**(即将顶层并发化) |
| `ai.py` | 275 | 5 个 _query_* AI 引擎 + 分析辅助 |
| `reports.py` | 172 | JSON / HTML 报告生成(CLI-only) |
| `modes/` | 8 个文件 | compare / crawl_check / crawl_test / aeo / authority_audit / citation / visibility / entity |

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
| `/etc/systemd/system/geo.service` | 后端 systemd unit（`--workers 4`） |
| `/etc/nginx/nginx.conf` | nginx，内含 `server_name www.vigilath.com` |
| `/var/log/nginx/access.log` | 访问日志 |
| `sudo journalctl -u geo.service` | 后端日志（含 `geo.timing`） |

---

## 8. 历史性能调查报告

| 文件 | 主题 |
|---|---|
| [`performance-report-2026-04-16.md`](./performance-report-2026-04-16.md) | UX 型 499 根因 + worker 1→4 扩容 + SSE 现状 |
| [`performance-report-2026-04-17.md`](./performance-report-2026-04-17.md) | 真实耗时实测（baidu 166 s） + 瓶颈细化 + 计时日志上线 |
