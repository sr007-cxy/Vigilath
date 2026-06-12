# 平台层建设 —— 对标 Sub2API 的差距分析与计划

> 目标:把"AI 引擎账号池 → 对外 API 网关"做到 Sub2API 级别的平台成熟度。
> 结论:**用 Python 在现有栈上增量补全**(不引入 Go,理由见路线说明)。引擎层走自研 HTTP(deepseek 已通)。

---

## 0. 一句话现状

我们已经是 **P1 对外 MVP 可用**:多租户 Key、每引擎日配额、credits 计费框架、账号池(sticky/失败隔离/单账号日上限/出口绑定)、调度中心(claim/result/计量/webhook)、后台 CRUD,全部就位。**对标 Sub2API,缺的主要是:并发控制、多窗口/RPM 限流、更细的账号状态机+故障转移、代理管理、用量分析、(可选)支付与用户自助。**

---

## 1. 现状清单(已有)

| 模块 | 已实现 | 文件 |
|---|---|---|
| 对外网关 `/v1` | engines / jobs / usage / admin*;Bearer 鉴权;租户隔离;每引擎日配额(429+Retry-After);内容审核(黑名单+长度);credit 护栏 | `telemetry-service/app/gateway.py` |
| 数据模型 | JobORM / ApiTenantORM(credit_balance)/ ApiKeyORM(sha256)/ UsageLedgerORM / WorkerORM / DispatchTaskORM | `telemetry-service/app/storage.py` |
| 调度·计量 | claim(内部优先+外部 floor/share/reserve)、result(成功才扣 credits、ENGINE_PRICE)、webhook(HMAC,best-effort)、api_jobs_loop | `telemetry-service/app/dispatch.py` |
| 账号池 | check-out/in、sticky lease(账号↔worker)、FailureType 隔离策略、单账号日上限(used_today/used_date)、出口标记 egress、账号身份 account_id | `backend/geo/api/engine_sessions.py` |
| 后台 | 租户 CRUD / 充值占位 / 配额 / Key 发吊 / 调用流水 | `frontend/.../AdminGateway.tsx` |
| 引擎层 | deepseek 纯 HTTP(wasm PoW,含联网引用)；豆包 ARK / 元宝 RAG API；海外走 OpenRouter | `browser-service/app/deepseek_http.py` 等 |

---

## 2. 差距清单(对标 Sub2API,逐项)

图例:✅ 已有 ｜ 🟡 雏形/部分 ｜ ❌ 缺 ｜ 优先级 P0(商用必须)/P1(重要)/P2(锦上添花)

### 2.1 账号管理 —— 🟡 P1
- ✅ 账号表 + 状态(active/quarantined/expired)+ 失败隔离 + sticky + 出口绑定 + 身份识别。
- ❌ **更细的调度信号**:`rate_limited_at` / `rate_limit_reset_at`(429 冷却)、`overload_until`(过载)、`temp_unschedulable_until`、`schedulable`(临时排除,如换号中)、`priority`、`concurrency`。
- **加**:engine_sessions 增列 `priority` / `concurrency` / `rate_limited_until` / `unschedulable_until` / `unschedulable_reason`;check-out 过滤这些 + 按 priority 排序。

### 2.2 限流与并发 —— 🟡 P1(最该补)
- ✅ 每租户每引擎**日**配额;单账号日上限;dispatch 内外分时(floor/share)。
- ❌ **多窗口限流**(5h/1d/7d)、**RPM**(每分钟)、**并发控制**(每账号/每租户同时在跑数)。
- **加**:
  - API Key/租户级:`rpm_limit` + 多窗口(先做 1d + RPM,5h/7d 选配)。Postgres 计数 + 窗口起点;规模上来再上 Redis 令牌桶。
  - 每账号并发槽:小规模用 Postgres `inflight` 计数列 + check-out 时判 `< concurrency`;大规模换 Redis ZSET。

### 2.3 计费/用量 —— 🟡 P1
- ✅ UsageLedger(成功 job × 引擎价扣 credits)+ credit_balance。
- 🟡 **按"次"计费**(我们引擎无干净 token 数 → 按次计费合理,**不必照搬 Sub2API 的 token 级**)。
- ❌ **倍率**(租户/分组折扣)、**用量分析**(按租户/引擎/时间聚合、导出)、余额不足通知。
- **加**:ApiTenant 加 `rate_multiplier`;扣费时乘倍率并快照到 ledger;后台加用量聚合视图。

### 2.4 调度 —— 🟡 P1
- ✅ LRU(use_count/last_used)+ sticky lease + 跳过 quarantine。
- ❌ **跳过"超额/限流/过载"账号**、**故障转移重试**(一个号失败→排除→换号重试,而非整条失败)、调度指标。
- **加**:check-out 增"跳过 rate_limited/unschedulable/超额";引擎执行层加"失败换号重试 N 次"(deepseek HTTP 的 login_lost→换下一个 token)。

### 2.5 代理(IP)管理 —— 🟡 P1(和我们的 IP 池强相关)
- 🟡 账号已有 `egress`(proxy/host)+ 按账号粘定代理。
- ❌ **代理为一等公民**:proxy 表(host/port/auth/状态/过期/备选)、健康检查、过期降级(proxy/direct)、账号↔代理 1:N。
- **加**:新增 proxy 表 + 账号 `proxy_id`;后台管理代理;定时健康检查;过期/失败自动降级。

### 2.6 后台 / 监控 —— 🟡 P1
- ✅ 租户/Key/配额/流水 CRUD。
- ❌ **账号池看板**(各引擎 active/quarantined/今日用量/并发)、**用量分析**(KPI/趋势/导出)、**错误日志聚合**、**实时健康**。
- **加**:扩 AdminGateway:账号池看板 + 用量趋势 + 错误聚合。

### 2.7 支付 —— ❌ P2(看是否做自助充值)
- ✅ credit_balance + 手动 topUp。
- ❌ Stripe / 支付宝 / 微信 / 订单 / 退款 / 订阅。
- **决策**:若**人工开通+对公结算** → 维持手动充值即可,**不做**;若要**自助充值** → 再接支付(P2 单独立项)。

### 2.8 用户自助 / 分组 —— ❌ P2
- ✅ 租户(≈客户)+ Key。
- ❌ 终端**用户登录/注册/2FA/自助管 Key/查用量**、**分组(group)+ 分组倍率 + 模型路由**。
- **决策**:B2B 人工开通场景**不需要**;要做开发者自助门户再加(P2)。

### 2.9 其它 —— P2
- 内容审核规则(现为黑名单,够用)、审计日志、公告、错误透传规则、Key 的 IP 白/黑名单、Key 过期。按需补。

---

## 3. 落地路线(Python,分阶段)

> 原则:在现有 Python 栈增量加;能用 Postgres 先用 Postgres,QPS 上来再引 Redis;不引入 Go(我们是"固定引擎+自有平台",用不到 Go 的通用高并发,且引擎非标准、自研更可控)。

### 阶段 A —— 引擎层做齐(地基)
- [x] deepseek 纯 HTTP(wasm PoW + 联网引用)
- [ ] deepseek 铺满 4 台 worker
- [ ] qwen / 文心 同法切 HTTP(逆内部接口;多半无 PoW)

### 阶段 B —— 平台 P1(对标 Sub2API 核心)
1. **限流并发**:Key/租户多窗口(1d+RPM)+ 每账号并发槽(`concurrency`/`inflight`)。
2. **账号状态机 + 故障转移**:增 `rate_limited_until`/`unschedulable_until`/`priority`/`concurrency`;check-out 跳过 + 执行层失败换号重试。
3. **代理管理**:proxy 表 + 账号 `proxy_id` + 健康检查 + 过期降级 + 后台管理。
4. **用量分析 + 账号池看板**:后台加聚合视图、趋势、错误聚合。
5. **计费倍率**:租户 `rate_multiplier` + ledger 快照。

### 阶段 C —— 平台 P2(按商业模式选做)
- 自助充值(支付集成)、用户门户+登录、分组+模型路由、审计/公告/错误透传规则。

---

## 4. 复用 Sub2API 的"设计"清单(直接照搬到 Python)

- **账号状态机字段**:`rate_limited_at`/`rate_limit_reset_at`/`overload_until`/`temp_unschedulable_until`/`schedulable`/`priority`/`concurrency`。
- **多窗口限流**:每 Key 存 `usage_<w>` + `window_<w>_start`,请求前检查 `usage+cost > limit` 则 429,窗口超时归零。
- **并发槽**:`concurrency` 上限 + 在飞行计数;满了换号或排队。
- **调度分层**:sticky(会话/上次)→ 负载均衡(priority→LRU→并发感知,取 TopK 随机)。
- **故障转移**:软错误(429/超时)→ 标记冷却 + 排除 + 换号重试 N 次;硬错误(401)→ 标记 error/quarantine。
- **代理降级**:`fallback_mode` none/proxy(备选)/direct。
- **用量账本快照**:扣费时把倍率/单价快照进 ledger(防事后改价影响历史账单)。

---

## 5. 不照搬的部分(我们的差异)

- **token 级计费** → 我们按"次"计费(引擎无干净 token 数),更简单合理。
- **OpenAI 格式转发** → 我们自研引擎(PoW/路径流/引用),不套 OpenAI 上游模型。
- **Go** → Python 单栈(规模未到需要 Go)。
- **支付/用户门户** → B2B 人工开通可省;自助再加。
