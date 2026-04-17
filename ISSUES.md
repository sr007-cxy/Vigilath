# GEO Checker — Issue List

> 这份文件与 `ENHANCEMENT.md` 并列:
> - `ENHANCEMENT.md` 记录**功能增量**("要做什么新功能")
> - `ISSUES.md`(本文)记录**问题 / 缺陷 / 优化**("要修什么、改什么")
>
> 详细分析在 `docs/performance-guide.md`,执行方案在 `docs/性能处理方案.md`,
> 历史调查在 `docs/performance-report-YYYY-MM-DD.md`。本文只做索引。

---

## 开放中(Open)

### P0 — 当前批次

#### [#1] `check_cross_platform` 串行探测 10 个社交平台

- **Status**: Open
- **Area**: backend(核心引擎)
- **Symptom**: 默认 check 对"社交矩阵弱"站点可跑到 2–3 分钟;实测 baidu.com 166 s
- **Files**: `geo_checker.py:2784`(根)、`geo_checker/__main__.py:1851`(默认路径)—— 两份都要改
- **Plan**: [docs/性能处理方案.md §1](./docs/性能处理方案.md)
- **Expected**: 默认 check ~120 s → ~30 s

#### [#2] `/visibility` 90 次 OpenRouter 调用,8 并发

- **Status**: Open
- **Area**: backend(高级引擎)
- **Symptom**: 自估耗时 180 s,接近前端 axios 300 s 超时线,多次触发 499
- **Files**: `geo_checker.py:5900`(`STABILITY_RUNS`)、`geo_checker.py:5951`(`max_workers`)
- **Plan**: [docs/性能处理方案.md §2](./docs/性能处理方案.md)
- **Expected**: visibility 180 s → 40–60 s

#### [#3] 前端检测触发仍在 Home 页,用户返回/刷新中断请求

- **Status**: Open
- **Area**: frontend
- **Symptom**: 近两天 16 次 499 里,13 次源自 `/api/check(anonymous)`,全部是用户导航离开
- **Files**: `frontend/src/pages/Home.tsx`、`frontend/src/pages/Result.tsx`、`frontend/src/services/geoApi.ts`
- **Plan**: [docs/性能处理方案.md §3](./docs/性能处理方案.md)
- **Expected**: UX 型 499 清零;后端不再空跑

### P1 — 下一批次

#### [#4] `check_brand_entity_kg` 三次 Wikipedia/Wikidata 调用串行

- **Status**: Open
- **Area**: backend
- **Files**: `geo_checker.py:1538` 附近
- **Plan**: [docs/性能处理方案.md §4](./docs/性能处理方案.md)
- **Expected**: -5 to -10 s

#### [#5] `check_authority_trust` bio + 认证源仍串行

- **Status**: Open
- **Area**: backend
- **Files**: `geo_checker.py:1336` 附近
- **Plan**: [docs/性能处理方案.md §5](./docs/性能处理方案.md)
- **Expected**: -3 to -5 s

#### [#6] `/citation` 主循环顺序执行 + `time.sleep(1)` 间隔

- **Status**: Open
- **Area**: backend
- **Files**: `geo_checker.py:5310` 起
- **Plan**: [docs/性能处理方案.md §6](./docs/性能处理方案.md)
- **Expected**: 60 s → 15 s

#### [#7] `_run_or_raise` 把三种不同故障一律映射成 503

- **Status**: Open
- **Area**: backend
- **Symptom**: "API key 缺失 / 上游 AI 挂 / 目标站抓不到"全返 503,前端看到的都是 "Failed to run advanced check"
- **Files**: `backend/geo/api/advanced.py:64-75`;涉及 `geo_checker.py` 多处 `raise RuntimeError`
- **Plan**: [docs/性能处理方案.md §7](./docs/性能处理方案.md)
- **Expected**: 可观测性提升;前端能按状态码精确提示

### P2 — 重构型待评估

#### [#8] `_page_cache` 是进程内 dict,4 workers 各一份,重启丢失

- **Status**: Open(需设计评审)
- **Area**: backend / infra
- **Files**: `geo_checker.py:201`
- **Plan**: [docs/性能处理方案.md §8](./docs/性能处理方案.md)
- **Note**: 需要运维配合部署 Redis

#### [#9] `_geo_checker_lock` 全局串行,单 worker 并发上限 = 1

- **Status**: Open(需设计评审)
- **Area**: backend(重构)
- **Files**: `backend/geo/services/geo_checker.py:26`,根源在核心 `geo_checker.py` 的模块级全局态
- **Plan**: [docs/性能处理方案.md §9](./docs/性能处理方案.md)
- **Note**: 依赖 #7 完成后再开工,错误可观测性是重构的前置条件

---

## 近期已关闭(Recently Closed)

#### [#R1] USDC 支付钱包爆栈 `Maximum call stack size exceeded`

- **Status**: Closed in `b9306e4` (2026-04-17)
- **Area**: frontend
- **Root cause**: 多钱包扩展同时注入 `window.ethereum`,彼此 Proxy wrap;ethers v6 `BrowserProvider.send('eth_requestAccounts')` 探测底层 provider 时递归穿透这条链,栈溢出
- **Fix**: `CheckoutPending.tsx` 加 `pickInjectedProvider()`(EIP-6963 + `providers[]` + 兜底),握手阶段用 `ethereum.request()` 绕开 ethers
- **Deployed**: 2026-04-17

#### [#R2] 后端缺少 per-check 耗时可观测性

- **Status**: Closed in `da3a8d9` (2026-04-17)
- **Area**: backend(基础设施)
- **Fix**: 新增 `backend/geo/utils/timing.py`;HTTP 中间件记录 `/api/check*` 耗时 + `X-Process-Time` 头;monkey-patch 默认/高级两条路径的所有 `check_*` 和 runner
- **使用方式**: 见 [docs/performance-guide.md §3](./docs/performance-guide.md)

#### [#R3] 性能报告单一文件膨胀,难以区分事件与参考

- **Status**: Closed in `fae9152`、`c97dd49`、`c8466ee` (2026-04-17)
- **Area**: docs
- **Fix**: 拆成四份,角色不同:
  - `docs/performance-guide.md` — 常驻参考
  - `docs/performance-report-2026-04-16.md` — 04-16 事件快照(UX 型 499 根因)
  - `docs/performance-report-2026-04-17.md` — 04-17 事件快照(baidu 166s 实测 + 计时日志上线)
  - `docs/性能处理方案.md` — 执行计划(P0–P2 各步的改动 / 验收 / 回滚)

#### [#R4] `_page_cache` 跨 worker 独立浪费重复抓取(纳入 #8,**未完成**)

虽然在 04-16 报告里被列为"已识别",但真正的修复方案归类到 P2 重构,见 [#8]。**此条不算关闭**,仅用于说明已纳入跟踪。

#### [#R5] uvicorn 单 worker 瓶颈

- **Status**: Closed(2026-04-16)
- **Area**: infra
- **Fix**: `/etc/systemd/system/geo-checker.service` 的 `ExecStart` 加 `--workers 4`
- **Note**: 只是缓解并发上限到 4,没有解决"单 worker 内串行"的根因(见 [#9])

---

## 文档索引

| 文档 | 用途 |
|---|---|
| `ENHANCEMENT.md`(根) | 功能增量跟踪(22 项,大多已 DONE) |
| `ISSUES.md`(本文) | 问题 / 缺陷 / 优化项索引 |
| `docs/performance-guide.md` | 系统性能常驻参考 |
| `docs/performance-report-*.md` | 事件快照(冻结) |
| `docs/性能处理方案.md` | 性能优化执行计划 |
| `docs/deployment-guide.md` | 生产部署与回滚 |
| `docs/系统处理方案.md` | 499 UX 修复方案(他人 WIP) |

---

## 约定

- **新增 issue**:追加到对应优先级下,按发现顺序分配 `#N`,不跳号
- **关闭 issue**:保留条目,状态改成 `Closed in <commit>`,移到"Recently Closed"段;原位置删除
- **引用 GitHub PR**:如果做出相关 PR,在 `Status` 行补 `PR: GT-oliver/geo#<num>`
- **优先级变更**:改 P 级即可,不必重新编号
