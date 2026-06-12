# AI 引擎对外服务 —— 总体设计文档

> 把"驱动真实 AI 引擎、拿到与网页一致的答案+联网引用"的能力,做成一个**多租户对外 API 平台**:
> 客户拿一个 Key,选引擎,得到该引擎**网页版的真实回答+引用**。
> 技术栈:**Python**(沿用现有 backend / telemetry / browser-service);平台层借鉴 LiteLLM / Sub2API 的设计。
>
> 版本:v1(2026-06) · 关联:`平台层建设-对标Sub2API差距分析.md`

---

## 1. 目标与原则

- **结果 = 网页**:卖的是"某 AI 网页版的真实回答 + 联网引用",不是模型 API 转发。对外参数是 `engine`(不是 `model`)。
- **精度控制**:账号、IP、用量都要精确——单账号日上限、**账号↔固定出口 IP 绑定、每 IP 不堆同平台账号**。
- **尽量全自动**:上传一次凭证(密码 / API Key)→ 自动登录、监控、掉线自动重登,无需人工反复干预。
- **Python 单栈**:不引入 Go;引擎非标准(PoW/引用),自研可控;平台层照 LiteLLM/Sub2API 的设计落 Python。

---

## 2. 总体架构

```
客户 → 对外网关 /v1 (telemetry-service, Python)
        · 租户 / API Key / 配额 / 限流 / 计费 / 后台
        · 调度中心(claim/result),内部舆情 + 外部 job 共池分时
              ↓ 派发
        worker (browser-service ×N)
        · 引擎层:每引擎一套"取回真实网页回答"的逻辑
              ├─ deepseek  纯 HTTP(token + wasm 算 PoW)✅ 已上线
              ├─ qwen/文心 纯 HTTP(逆内部接口,待做)
              ├─ doubao    ARK API(Key)
              └─ yuanbao   SearchPro RAG API(Key)
        · 账号池(engine_sessions):check-out/in、sticky、单账号日上限、出口绑定
        · 出口:账号→固定 IP(代理或本机),每 IP 不堆同平台
```

两层:**引擎层**(拿网页真实回答)+ **平台层**(网关/账号池/限流/计费/后台)。

---

## 3. 引擎层(与网页一致的真实回答)

| 引擎 | 接入 | 状态 | 要点 |
|---|---|---|---|
| **deepseek** | 网页内部 HTTP | ✅ 已上线(4 台 worker)| Bearer token(登录态)+ `wasmtime` 跑 deepseek 自己的 `sha3_wasm.wasm` 算 `DeepSeekHashV1` PoW → `/chat/completion`(`search_enabled=true`);流是 path-based,`response/content` 拼答案、`response/search_results` 是引用。详见 `deepseek_http.py` |
| **qwen** | 网页内部 HTTP | ⏳ 待逆 | 照 deepseek 模板逆 qianwen.com 内部接口(多半无 PoW)|
| **wenxin** | 网页内部 HTTP | ⏳ 待逆 | 逆 chat.baidu.com 内部接口 |
| **doubao** | ARK API(Key)| ✅ | 字节官方 API,无需登录 |
| **yuanbao** | SearchPro RAG(Key)| ✅ | 腾讯 SearchPro + 合成,无需登录 |

- 开关对齐网页:deepseek 只有 `thinking_enabled`(深度思考)/`search_enabled`(联网)两个开关,**不提供模型选择**(引擎=模型)。
- 技术印证:`deepseek4free`(开源)同样逆了 PoW+联网,路子一致、成熟可靠。

---

## 4. 账号池 + IP 精度控制

### 4.1 拓扑(每 IP 不同平台,1 账号/IP)
- **国内 worker 出口 IP**(每个):跑 1 deepseek + 1 qwen + 1 文心(三平台/IP)。
- **日本代理 IP**(每个):纯 deepseek(qwen/文心是国内号,走日本会异地风控)。
- 同平台不堆一个 IP;每账号粘定它的固定出口 IP(sticky)。

### 4.2 绑定字段(engine_sessions 扩列)
- `egress`:`host`(走本机/worker IP) / `proxy`(走代理 IP)/ None(按引擎默认)
- `preferred_worker_id`:本机型账号钉到固定 worker(=固定国内 IP)
- 代理型账号:proxy sticky `-session-acct<id>` → 固定一个代理 IP
- `captured_from_ip` / `captured_from_region`:记录账号实际出口

### 4.3 用量控制(限流)
- **单账号日上限** `used_today`/`used_date`(check-out 排除当天超额号,跨天归零)
- **每 IP 日上限**:由"每 IP 账号数 × 单账号上限"控住(或显式 per-IP 计数)
- **每租户每引擎日配额**(网关层,已有)+ **多窗口/RPM**(待补,借 LiteLLM)

---

## 5. 授权(4 种方式 + 全自动闭环)

| 方式 | 适用 | 自动化 |
|---|---|---|
| **① 密码自动** | deepseek、wenxin(有密码登录)、qwen(若有密码)| ⭐ 上传密码一次,登录+掉线重登全自动 |
| **② 扫码协助** | 只有扫码的引擎 | 🟡 管理台显示二维码 → 人扫一次 |
| **③ API Key** | doubao、yuanbao、海外 | ⭐ 填 Key |
| **④ 登录态上传** | 任意(兜底)| 扩展导出上传 |

### 全自动闭环
```
上传凭证(密码/Key,AES 加密存) → 自动 authorize(server 端登录抓登录态 / 校验 Key)
   → 入池 → 运行 → LOGIN_LOST 自动隔离 → 自动续期守护用存的密码自动重登 → 解隔离
   →(可选)定时探活,提前发现掉线
```
密码型上传一次后**账号自愈、永不用人管**。

---

## 6. 平台层(借鉴 LiteLLM,Python)

> LiteLLM(Python)有现成的 虚拟Key / 多窗口预算 / 限流 / 成本追踪 / team —— 直接照搬设计或直用。

要补(P1):
- **多窗口限流 + RPM**(每 Key / 每租户)+ **每账号并发槽**
- **账号状态机**:`rate_limited_until` / `unschedulable_until` / `priority` / `concurrency`;调度跳过超额/限流/熔断号
- **故障转移**:单号失败→排除→换号重试(而非整条失败)
- **代理(IP)管理**:proxy 表 + 健康检查 + 过期降级
- **用量分析 + 账号池看板**;**计费倍率**(租户折扣 + 账本快照)
- **账号管理台**:添加账号 + 4 种授权 + 状态/用量展示 + 掉线告警/自动重登

已有(P1 MVP):多租户 Key、每引擎日配额、credits 计费、账号池(sticky/隔离/日上限/出口)、调度中心、后台 CRUD。

不照搬:token 级计费(我们按"次")、OpenAI 格式转发、支付/用户门户(B2B 人工开通可省)。

---

## 7. 基于现有资产的部署规划

### 现有家底
| 引擎 | 可用会话 | 凭证 | 授权 |
|---|---|---|---|
| deepseek | 17 active | 10 密码 | 密码全自动 ✅ |
| qwen | 9 active | 无 | 扫码(现有会话)|
| wenxin | 8 active | 无(有密码登录)| 扫码/密码 |
| doubao | — | ARK Key | API ✅ |
| yuanbao | — | SearchPro Key | API ✅ |

**IP:8 个** = 5 国内 worker(vm01 `…110` / vm02 `…109` / ecs1-3)+ 3 日本(Bright Data)。

### 账号↔IP 分配
| IP | deepseek | qwen | wenxin |
|---|---|---|---|
| 5 国内 worker IP | 各 1(本机)| 各 1 | 各 1 |
| 3 日本 IP | 各 1(代理)| — | — |

→ deepseek 用 8(1/IP),qwen 5,wenxin 5,其余 standby 备用。

### 产能(单账号 ~25/天)
deepseek **200/天** · qwen **125** · wenxin **125** · doubao/yuanbao 按 API 量(付费)。

### 缺口
- qwen/文心**无密码** → 暂半自动(掉线手动重扫);补密码即转全自动(wenxin 已有密码登录入口;qwen 待确认)。
- 要更大产能 → 加 IP(国内代理,确认 country=CN)+ 加账号。

---

## 8. 开源参考

| 用途 | 项目 | 说明 |
|---|---|---|
| 引擎层(网页逆向)| `deepseek4free` / `gpt4free` | 印证 deepseek PoW+联网技术;但 qwen/文心**真中文网页**需自逆(g4f 的 Qwen 走 Together/HF,非通义网页)|
| 平台层(Python)| **LiteLLM** | 虚拟 Key / 多窗口预算 / 限流 / 成本,**最贴我们栈,首选参考/直用** |
| 平台层(Go)| Sub2API / one-api / new-api | 账号池→网关、计费、代理;Go,作设计参考 |
| 账号池+IP精度 | (无现成)| 网页登录态+IP风控+每IP不同平台是我们差异点,**自研** |

---

## 9. 落地路线

**阶段 A — 引擎层做齐**
- [x] deepseek 纯 HTTP(4 台 worker)
- [ ] qwen / 文心 切 HTTP(逆内部接口)

**阶段 B — 账号池 + IP 精度 + 授权(全自动)**
- [ ] engine_sessions 扩列(auth_type / 加密 credentials / egress / preferred_worker / 状态机字段)
- [ ] Authorizer(密码自动登录复用 deepseek;API Key 校验;扫码协助)
- [ ] 账号↔IP 绑定(按拓扑分配)+ 单账号/每IP日上限
- [ ] 自动续期守护(密码型掉线自动重登)+ 定时探活
- [ ] 账号管理台(上传/授权/状态/用量)

**阶段 C — 平台 P1(借 LiteLLM)**
- [ ] 多窗口+RPM 限流 + 每账号并发
- [ ] 故障转移换号重试
- [ ] 代理(IP)管理 + 健康检查
- [ ] 用量分析 + 账号池看板 + 计费倍率

**阶段 D — 商业化 P2(按需)**
- [ ] 自助充值(支付)、用户门户、分组/倍率

---

## 10. 关键技术备忘

- deepseek PoW **必须用它自己的 wasm 算**(`sha3_wasm.wasm` + wasmtime),手写算法各种变体均被 `INVALID_POW_RESPONSE` 拒;它改算法重下 wasm 即可。
- deepseek 登录态 **token 不绑 IP**(JP 抓的态在国内 IP 照用)→ 账号可自由分配到任意 IP。
- 国内引擎登录:deepseek 密码可自动;qwen/文心/元宝/豆包网页主要靠**扫码**;doubao/yuanbao 走 **API Key** 免登录。
- 出口:`DEEPSEEK_HTTP_MODE=1` 开 HTTP;`PROXY_ENGINES` + `ENGINE_PROXY_*` 控代理;`ENGINE_SESSION_DAILY_CAP_<ENGINE>` 单账号日上限。
