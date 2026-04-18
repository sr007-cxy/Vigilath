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
| [#13](#13-check_technical_crawlability-可达-44-s) | **P0** | backend | `check_technical_crawlability` 内部 22 次 HTTP 串行 | **baidu 36 s → 10 s** |
| [#2](#2-visibility-90-次-openrouter-调用8-并发) | **P0** | backend | `/visibility` 90 次 AI 调用 | visibility 180 s → 40–60 s |
| [#9](#9-_geo_checker_lock-全局串行) | P2 | backend | 去 `_geo_checker_lock`(#14 局部锁已覆盖 90%) | 跨请求真并发 |
| [#11](#11-check_authority_trust-16-秒耗时) | P1 | backend | `check_authority_trust` 内部并发 | 顶层并发后收益有限 |
| [#5](#5-check_authority_trust-认证源串行) | —— | —— | (merged into #11) | —— |
| [#6](#6-citation-主循环顺序执行--timesleep) | P1 | backend | `/citation` 主循环顺序执行 | 60 s → 15 s |
| [#7](#7-_run_or_raise-三种故障混映射-503) | P1 | backend | `_run_or_raise` 错误码混淆 | 可观测性提升 |
| [#8](#8-_page_cache-不跨-worker进程重启丢失) | P2 | backend/infra | `_page_cache` 跨 worker 共享 | 二次检测 < 1 s |
| [#15](#15-ogimage--twitterimage-指向-faviconsvg社交分享卡片降级) | P2 | frontend/SEO | `og:image` / `twitter:image` 指向 favicon,社交分享卡片降级 | X/Facebook/LinkedIn 大图预览 |

### 0.2 近期已关闭(Closed)

| ID | 关闭 commit | 标题 |
|---|---|---|
| [#14](#14-顶层-generate_score-25-个-check-并发化) | (本次 commit) | **P0 头号**:顶层并发化,baidu 124 s → 36.8 s(-70%) |
| [#4](#4-check_brand_entity_kg-wiki-调用串行) | 无 commit,实测关闭 | 实测 440 ms 完全不慢,无需优化 |
| [#12](#12-核心引擎文件分叉-3-份-→-1-份-package) | `693aa6d` | P0:geo_checker 重构成 package,3 份副本合 1 |
| [#10](#10-check_trust_safety-23-url-并行化) | `e8b4b04` | P0:check_trust_safety 23 URL 并发 batch(-48 s) |
| [#3](#3-前端检测仍在-home-页触发用户返回刷新即断连) | `e036bcb` | P0:前端检测触发改到 Result 页 + AbortController |
| [#1](#1-check_cross_platform-串行探测-收益不及预期) | `7610d4b` / `e8b4b04` | P0:check_cross_platform 并发化(原改漏了 backend 副本,已补) |
| [#R1](#r1-usdc-支付钱包爆栈) | `b9306e4` | USDC 支付钱包爆栈 `Maximum call stack size exceeded` |
| [#R2](#r2-后端缺少-per-check-耗时可观测性) | `da3a8d9` | 后端缺少 per-check 耗时可观测性 |
| [#R3](#r3-性能文档膨胀难以区分事件与参考) | `fae9152` / `c97dd49` / `c8466ee` | 性能文档体系重构 |
| [#R4](#r4-uvicorn-单-worker-并发上限) | 2026-04-16 | uvicorn 单 worker 并发上限 |

---

## Open — P0 当前批次


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


### #13 `check_technical_crawlability` 内部 22 次 HTTP 串行

- **Priority**: **P0**(2026-04-17 review 从 P1 升级 —— 顶层并发后它是单请求最慢 check,直接决定 baidu 总时底线)
- **Status**: Open
- **Area**: backend

**症状**:baidu.com 2026-04-17 post-refactor 稳定 42 s(占总时 34%)。

**根因**(已核实,`backend/geo_checker/checks.py::check_technical_crawlability`):

内部最坏 22 次 HTTP 调用:
1. `get_soup(base_url)` 1 次
2. `fetch(canonical_url)` 若 canonical 不同 1 次
3. `requests.get(..., allow_redirects=False)` + 若重定向再 `allow_redirects=True` 共 2 次
4. `subprocess.run(curl -sI --http2 ...)` 1 次(timeout=10)
5. 6 个 feed 路径 `/feed`、`/feed.xml`、`/rss.xml`、`/atom.xml`、`/rss`、`/blog/feed` 串行 fetch,break on first match
6. `fetch(feed_url, timeout=8)` 抓 feed 内容 1 次
7. 10 个 API 路径(`/api`、`/graphql`、`/openapi.json` 等)串行 fetch,break after 3 matches

baidu 对不存在的路径平均 2 秒返回 404,22 × 2 = 44 s。

**处理方案**:4 组独立调用(redirect / curl / feed / api)拆成 `ThreadPoolExecutor(max_workers=4)`,每组内部也可进一步 flatten 并发(feed 6 路径 + api 10 路径 = 16 并发 fetch)。

**验收**:baidu `func=check_technical_crawlability elapsed_ms` < 10 000 ms。配合 #14 顶层并发,baidu 总时降到 17-25 s。

---

### #11 `check_authority_trust` 16 秒耗时

- **Priority**: P1(2026-04-17 review 从 P0 降级 —— 顶层并发 #14 完成后,它不再独立影响总时)
- **Status**: Open
- **Area**: backend

**症状**:journald 计时日志显示 baidu.com 检测中 `check_authority_trust` 耗时 **16 667 ms**,占总时 13%(post-refactor 数据)。

**根因**:bio 页(`/about`、`/about-us`、`/team` 等)+ 多个外部认证源(Medium、Substack、Forbes、HBR、arxiv、ORCID、Google Scholar)串行探测。与原 P1 #5 同根。

**涉及文件**:
- `backend/geo_checker/checks.py::check_authority_trust`

**处理方案**:并发化 `ThreadPoolExecutor(max_workers=5)`。

**降级原因**:#14 顶层并发后,这个 check 与 #13 `check_technical_crawlability` 一起并发跑;#13 稳定 42 s > #11 的 17 s,所以 #11 不再是总时决定因素。优先级应让位给 #13 的内部并行化。

**验收**:`func=check_authority_trust elapsed_ms` 稳定 < 5 000 ms(baidu)。

---

## Open — P1 下一批次


### #5 `check_authority_trust` 认证源串行

**Merged into [#11](#11-check_authority_trust-16-秒耗时)** —— baidu 实测后发现耗时 16 s 远高于当初估算,升级为 P0。

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

### #15 `og:image` / `twitter:image` 指向 `favicon.svg`,社交分享卡片降级

- **Priority**: P2
- **Status**: Open(待设计资产)
- **Area**: frontend / SEO / 增长

**症状**:在 X / Facebook / LinkedIn 粘 `https://www.vigilath.com/` 分享,预览卡片没有大图或显示很小的模糊 logo,无法触发 `twitter:card=summary_large_image` 的大图布局。

**根因**:`frontend/index.html:31` 的 `og:image` 和 `frontend/index.html:41` 的 `twitter:image` 都指向 `https://www.vigilath.com/favicon.svg`。favicon 是 32×32 的 SVG 站点图标,不是分享卡片资产:
- X `summary_large_image` 要求 PNG/JPG,推荐 1200×628(或 2:1)
- Facebook/LinkedIn OG 规范最小 600×315,推荐 1200×630,SVG 支持不稳定
- 其余 OG 结构(type/site_name/title/description/url/locale + twitter:card/site/creator/title/description)都齐全,**唯一短板是 image**

**连带副作用**:SPA 全部路由返回同一份 `index.html`,所以 OG 是全站一套。未来做差异化分享(`/checker`、`/geo-knowledge/xxx`)需要 SSR / prerender 或 `react-helmet-async`,目前用同一张通用图够用。

**处理方向**:
1. 产出 1200×630 社交卡片图(logo + slogan "GApex — Unified GEO+AEO for Global AI Visibility"),PNG 或 JPG,< 1 MB。
2. 放 `frontend/public/og-image.png`,构建时自动进 webroot。
3. `index.html` 两处 `og:image` / `twitter:image` → `https://www.vigilath.com/og-image.png`。
4. 用 Facebook Sharing Debugger、X Card Validator、LinkedIn Post Inspector 分别抓一次确认大图卡片生效。

**前置依赖**:需要一张设计稿(外部资产,不在代码里)。接入是 10 分钟的事。

**验收标准**:
- 三家平台的 debugger 均识别为 `summary_large_image` / large-preview,且大图正确渲染
- HTML 源码 `og:image` 指向一个 1200×630 的 PNG/JPG URL(不再是 SVG favicon)

---

## Closed

### #14 顶层 `generate_score` 25 个 check 并发化

- **Closed**: (本次 commit)(2026-04-17)
- **Area**: backend(核心引擎)

**症状**:baidu.com 默认 check 实测 124 秒,25 个 `check_*` 全部串行,总时 = Σ 所有 check。

**根因**:`orchestrate.py::_run_checks` 的 for 循环顺序调用 25 个 check。`state._scores` / `state._page_cache` 是模块级全局 dict,直接并发会数据串扰。

**修复**(4 个文件):

1. `backend/geo_checker/state.py`
   - `_scores` 加 `threading.Lock`(`track_score` 并发写)
   - `_page_cache` 加 `threading.Lock`(和 `io.fetch` 配合)
   - `reset_state` 写加锁

2. `backend/geo_checker/io.py::fetch`
   - 两阶段锁:read 加锁 → 释放 → HTTP I/O → 写回加锁
   - 锁内不做网络 I/O,avoid thread hog
   - 最坏情况两线程同 miss 同一 URL → 重复 fetch 一次(无正确性问题)

3. `backend/geo_checker/orchestrate.py::_run_checks`
   - `check_sitemap` 先跑(是 `check_multi_page` 的入参)
   - 其余 24 个用 `ThreadPoolExecutor(max_workers=10)` 并发
   - **每个 check 有独立 local buf**,finish 后用 flush_lock 一次性原子 append 到主 buf
   - 这样 `--- Category ---` 头 + 该 check 的所有行保持连续,`parse_geo_output` 不会把检测结果归错 category

4. `backend/geo/services/geo_checker.py`
   - 去掉 `redirect_stdout(buf)`(不安全,改 process-wide sys.stdout)
   - 换成 `advanced_runners._stdout_proxy._state.buf = buf`(threading.local)
   - `_geo_checker_lock` **保留**(跨请求串行 fallback)

**实测**(baidu.com):

| 指标 | 改前 | 改后 |
|---|---|---|
| baidu.com 默认 check 总时 | 124 s | **36.8 s**(**-70%**) |
| example.com 默认 check 总时 | 5.0 s | **2.9 s**(-42%) |
| 最慢单 check(`check_technical_crawlability`) | 42 s | 36 s |
| 同一 URL 两次检测 score 一致 | ✓ | ✓ |
| category 归属正确(无交叉污染) | n/a | ✓(0 leak) |
| 2 个并发请求互不串扰 | n/a | ✓ |

**经验**:
- 第一版用 `pool.initializer` 把 parent buf 传给 worker 线程 —— 失败。parser 是行级的,并发写同一 buf 会让 `--- Category ---` 头和 check 行混乱,导致检测结果归错 category(Trust & Safety 的结果跑到 Brand Entity 下)。
- 第二版**每个 check task 用独立 local buf,finish 后 flush_lock 下整块 append** —— 成功。这是保持 line-based parser 正确性的唯一干净方案。
- `_geo_checker_lock` 先不去,下次做 #9 重构时再移除。

---

### #4 `check_brand_entity_kg` Wiki 调用串行(实测不慢,无需优化)

- **Closed**: 无 commit(2026-04-17 post-refactor review 实测关闭)
- **Area**: backend

**症状**:假设 Wikipedia search + Wikipedia backlinks + Wikidata search 三个串行外调会慢。

**实测**:post-refactor baidu.com 基线 `check_brand_entity_kg elapsed_ms=440`,example.com 571 ms。**完全不在 top-10 瓶颈之列**。

**结论**:原估算是基于"串行必然慢"的先验,实际 Wikipedia / Wikidata API 响应非常快(< 200 ms 单次),三次串行累加只有 ~500 ms。不值得投入工时。

**经验**:**先看计时日志再列 issue**。上次我把 #4 列为 P1 是基于代码阅读,没用数据验证。这次 post-refactor review 补上了这个教训 —— #R2 的计时日志基础设施已经就位,issue 立项前应该先 `grep 'geo.timing:func=<name>'` 看一眼。

---

### #12 核心引擎文件分叉:3 份 → 1 份 package

- **Closed**: branch `refactor/geo-checker-package`(2026-04-17)—— 待 merge 到 develop
- **Area**: infra(重大重构)

**症状**:根 `/geo_checker.py`(8065 行)+ `/geo_checker/__main__.py`(4311 行,orphan)+ `/backend/geo_checker/__main__.py`(4341 行)三份核心副本并存。改代码容易漏文件,合 upstream 困难。

**根因**:fork 自 `Yaqing2023/GEO`(纯 CLI 工具),我们在 fork 里 fork 了一份加 i18n 后变成 backend 副本(C),根文件(A)继续镜像 upstream。orphan(B)是 `pyproject.toml` 的 CLI entry 指向的半同步版本。

**修复**(7 阶段,每阶段独立 commit):

1. **阶段 0**(`pre-refactor-package-2026-04-17` tag):切 `refactor/geo-checker-package` 分支
2. **阶段 1**(`[commit sha]`):抽 4 个低风险模块到 `backend/geo_checker/`
   - `constants.py` / `state.py` / `io.py` / `output.py`
3. **阶段 2**(`31adcc5`):抽 25 个 `check_*` 到 `checks.py`(2958 行)
4. **阶段 3**(`[sha]`):抽 `orchestrate.py` + 7 个 mode 文件到 `modes/` + `ai.py`
5. **阶段 4**(`e3d9eae`):抽 `reports.py` + `output.py` 追加 _ZH 翻译表
6. **阶段 5**(`fe202b5`):重写 `__main__.py` 为 CLI 入口 + `__init__.py` re-export
7. **阶段 6**(`5fad751`):切换 `backend/geo/services/{geo_checker,advanced_runners}.py` 到新 package
8. **阶段 7**(本 commit):删根 `/geo_checker.py`、`pyproject.toml` 改 `packages.find where=["backend"]`,package version 1.0.0 → 2.0.0

**结果**:
- 3 份核心代码 → **1 份** `backend/geo_checker/` package(12 文件,每份 100-2900 行)
- i18n 风格统一为 `emit_check` / `emit_fix`(upstream 的 `_ZH` 翻译表作为 CLI 中文模式的补充保留)
- `advanced_runners` 不再 `spec_from_file_location`,直接 `from geo_checker.modes import ...`
- `pip install -e .` 的 CLI entry 指向完整版
- **回归冒烟通过**:default check + advanced/aeo(200)+ advanced 付费 4 端点(402 tier gate,正确)+ `python -m geo_checker` 可用

**经验**:
- fork 项目的单文件 CLI + web 化后天然会分叉;最终不得不 package 化
- 一个干净的 i18n 层(`emit_check` 带 key marker)是前后端分离的关键
- 分 7 阶段 / 独立 commit 的重构策略,任何一步出错都能 revert 到上一阶段
- 发现 2 个隐藏遗漏:`_pad` 在 4 个 mode 里缺导入;`flesch_kincaid_grade` 在 aeo 缺导入。`python -c "from geo_checker import *"` 测试无法发现这种问题,只有实际 HTTP 调用才能暴露 —— 下次重构要准备一份"每个 mode 跑一次最小请求"的脚本

---

### #10 `check_trust_safety` 23 URL 并行化

- **Closed**: `e8b4b04`(2026-04-17)
- **Area**: backend(核心引擎)

**症状**:baidu.com 检测中 `check_trust_safety` 单函数 **54 713 ms**,占 `generate_score` 总时 33%。

**根因**:4 个 trust page 类别(privacy / terms / contact / legal),每类 5-6 个候选路径,**4 个 `_probe()` 串行**,每个内部再**顺序**探测候选。合计最多 23 次串行 HTTP(每个 `timeout=6`)。baidu 对不存在路径平均返回 2.4 s,23 × 2.4 = 55 s。

**修复**:一次并发 batch `ThreadPoolExecutor(max_workers=10)` 把 23 个候选全部 fetch 完,主线程按原顺序 `_first_match()` 从结果 dict 挑第一个 200 + `len > 500`。
- `/geo_checker.py:1620` 和 `/backend/geo_checker/__main__.py:1646` 两份都改
- 借助 `_page_cache`(进程内 dict),GIL 保证并发写安全,最坏是重复 fetch 一次

**实测(baidu.com)**:
- 修复前:`check_trust_safety elapsed_ms=54713`
- 修复后:`check_trust_safety elapsed_ms=6311`(**-89%,净省 48 秒**)
- 总时:166 s → 130 s(其他 check 被同轮网络抖动拖慢,净省 36 s 落在该函数本身)

**经验**:
- 扁平化并发 > 分层并发。方案 A 的"4 个 `_probe()` 并行"只能降到 14 s,方案 B 的"全 23 并行"降到 3 s——后者多吃的 HTTP 请求在 baidu 这种慢站点是纯赚。
- 本次又暴露了 [#12] 三文件不同步的坑:编码时必须记得两份一起改,否则默认路径没生效。

---

### #1 `check_cross_platform` 串行探测(收益不及预期)

- **Closed**: `7610d4b`(2026-04-17)
- **Area**: backend(核心引擎)

**症状**:原估最坏 80 s 在此单函数上(10 平台 × 8 s timeout)。

**根因**:`check_cross_platform` 对未在 on-page 链接里检测到的社交平台做 probe,串行 GET。

**修复**:抽出 `_probe_platform(plat_name, plat_info)` 纯函数(无 print / 无共享写),由 `ThreadPoolExecutor(max_workers=min(10, len(platforms_to_probe)))` 并发提交,主线程按 `as_completed` 汇总到 `probed` dict。根 `geo_checker.py:2784` + 影子 `geo_checker/__main__.py:1951` 两份同时改。

**实测(baidu.com)**:
- 修复前:` check_cross_platform` 预估 30–80 s(最坏情况)
- 修复后:`check_cross_platform elapsed_ms=3785` —— 约 3.8 s
- 但 `generate_score elapsed_ms=165871` —— **总时几乎不变**

**经验**(记下来避免再犯):
- 修前的"80 s"估算是代码阅读的最坏情况。baidu 的大多数平台 probe 其实早期就 RST/404 返回,从没真到 8 s timeout。**估算应该基于计时日志,不是代码**。
- cross_platform 只占 baidu 总时 2%。真正头号瓶颈是 `check_trust_safety`(33%)和 `check_authority_trust`(9%)—— 见新建的 [#10](#10-check_trust_safety-耗时-55-秒总时的-33) / [#11](#11-check_authority_trust-16-秒耗时)。
- 改动本身仍然保留:代码更干净,且对"社交矩阵空 + 所有 probe 都打满 timeout"这种最坏情况仍然有效。只是不再作为"默认 check 变快"的卖点宣传。
- 教训:**先用计时日志看实际 top-N 再动手**,这次是反着来的。

---

### #3 前端检测仍在 Home 页触发,用户返回/刷新即断连

- **Closed**: `e036bcb`(2026-04-17)
- **Area**: frontend

**症状**:近两天 nginx 16 次 499 里 13 次来自 `/api/check(anonymous)`,全部是用户在 Home 等待期间按返回键 / 刷新导致。axios 请求被浏览器中断,nginx 记 499,后端 continues 空跑到检测完成(baidu 实测 166 s)。

**根因**:`Home.tsx` 的 submit handler 直接调 `geoApi.checkGeo()` 并在当前页渲染 `CheckProgress`。当前页就是 Home,用户本能会想回到"更显眼的位置"(点输入框改 URL / 点 logo 回首页 / 按浏览器返回),一旦导航离开,Home 卸载、axios 请求中断。

**修复**:
- `Home.tsx`:submit 仅做 URL 校验,合法就 `navigate('/result', { state: { pendingUrl } })`,本地不再管 loading / 错误 / quota。把 `CheckProgress`、`useState(isLoading)`、`useState(quotaExceeded)`、`geoApi` 导入等都移除。
- `Result.tsx`:
  - 在 `navState` 上加 `pendingUrl?: string`
  - 新增 `useEffect` 一处:`pendingUrl && !result` 时起 `AbortController`,调 `geoApi.checkGeo(..., signal)`,成功后 `navigate('.', { state: { result }, replace: true })`;cleanup `controller.abort()`
  - 另一处 `useEffect`:直达 `/result` 无任何 state 时 `navigate('/', replace: true)` 回兜底
  - `rerunUrl` 初始值加入 `pendingUrl` 兜底,检测中输入框保持有值
  - 复用 `rerunLoading` / `rerunError` / `rerunQuotaExceeded` 状态,`CheckProgress` 的 overlay 自动显示,错误 band 共用
- `geoApi.ts`:`runGeoCheck` / `checkGeo` / `runAdvancedCheck` 三个方法都加 `signal?: AbortSignal` 参数,透传到 axios

**经验**:
- `navigate` 立即返回,不等 API — 这是"页面生命周期 = 请求生命周期"的反模式避坑
- `AbortController` + `signal.aborted` 是 axios v1 官方认可的取消方式,比 CancelToken 好
- `history.replaceState` 写回 result 后,F5 / 前进后退不会触发重新检测,但 React Router 仍然重渲染(location object identity 变了)
- 前端无法从浏览器取消已发出的 HTTP 请求,后端一定会跑完一次 — 真正消灭"空跑"要靠后端加请求取消感知,超出本 issue 范围

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
