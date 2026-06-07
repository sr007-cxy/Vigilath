# 对外开放设计:GEO 优化 Agent「小龙虾」嵌入层

> v0.1 · 2026-06-07 · 配合 `最佳方案-GEO优化Agent.md`(产品决策)、`实现设计-Agent.md`(Pydantic AI 落地)。
> 本文设计**对外封装层**:把已有的「账号级 GEO agent」开放给外部网站/系统嵌入与对接。

---

## 0. 前提与假设(看完先圈错)

**现状系统已具备(不重造)**:

- **多租户**:数据天然按账号隔离,工具经 `ctx.deps.account_id` 取数,`account_id` 后端注入、绝不接受前端传值。
- **账号级 agent**:`resolve_account(current_user) → AgentDeps(account_id=...)`;一个账号 = 一只小龙虾。
- **鉴权**:JWT(`SECRET_KEY` + `user_service`),`geo/agent/auth.py`。
- **对话能力**:`/api/agent/chat`(SSE)、多轮记忆、结构化卡片、向量 RAG —— 已上线 vm02。

**本设计只补现状缺的薄薄一层**:

1. **可嵌入**:这只 agent 现在只活在我们自己网页,要能贴进**外部站点/系统**。
2. **跨域安全鉴权**:外部浏览器里**不能**出现长期 JWT / client secret。
3. **稳定对外契约 + 用量护栏**:版本化 API、CORS 白名单、每账号配额、审计。

**默认决策(可推翻)**:

| 项 | 默认取值 | 备注 |
|---|---|---|
| 账号 / 租户 | **复用现有账号体系**,「领号」= 后台/接口开一个现有账号 | 不造新租户模型 |
| 嵌入形态 | **嵌入挂件(script/iframe)+ 无头 API 双轨**,同一个后端 | 挂件给零前端合作方,API 给要定制的 |
| 鉴权 | **服务端换签(token exchange)**:client_secret 留服务器,浏览器只拿短时 scoped token | 见 §3 |
| 能力边界 | **读 + 诊断 / 产稿 / 规划**;**真实发布对外层默认关闭**(沿用 `AGENT_ALLOW_EXTERNAL_PUBLISH` 护栏) | 发布仍只内部触发 |
| 部署 | 复用 `geo-agent:8010` 独立 service,nginx 加 `/api/agent/v1/*` + CORS | 不新开服务 |

---

## 1. 整体形态

```
       外部对接方(合作方网站 / 自己的系统 / 别的小龙虾)
                    │
        ┌───────────┴────────────┐
        ▼(轻)                   ▼(重)
  嵌入挂件 script/iframe      无头 API + SDK
  (我们托管 UI,一贴即用)     (对方自建 UI / 在自己 agent 里调)
        │                        │
        └───────────┬────────────┘
                    ▼  携带「短时 embed token」
        ┌──────────────────────────────────┐
        │  对外网关层(本设计新增,薄)       │
        │   · CORS 白名单(按 client 注册 origin)
        │   · embed token 校验 → 解出 account_id
        │   · 每账号速率/配额护栏 + 审计      │
        └───────────────┬──────────────────┘
                        ▼  account_id(后端注入)
        ┌──────────────────────────────────┐
        │  现有账号级 Agent(原样复用)        │
        │   resolve_account → AgentDeps      │
        │   agent.run() 工具循环 / 卡片 / RAG │
        └──────────────────────────────────┘
```

---

## 2. 对接档位

主用法 = **对方的 AI agent(它们的小龙虾)链接我们的 GEO agent**(agent-to-agent);人用聊天窗只是附带场景。

**A. Agent-to-Agent(主,对方小龙虾链入)** —— 详见 §13:

| 封装 | 对方怎么用 | 谁编排 | 适用 |
|---|---|---|---|
| **Skill 包(推荐)** | skill 放进它 skills 目录,agent 即"会"GEO 优化 | 对方 agent(读 SKILL.md 自决何时用) | 小龙虾 / Claude 等支持 skill 的 agent |
| **MCP Server** | 挂载我们 MCP endpoint,工具自动发现 | 对方 LLM 逐个调工具 | 支持 MCP 的 agent |
| **/v1/chat 黑盒工具** | 注册 `ask_geo_expert(问题)`,内部调我们 /v1/chat | 我们的 agent | 只想"问 GEO 专家",不管细粒度 |

**B. 人用形态(附带)**:嵌入挂件 `<script>`(复用 `AgentChatWidget`)/ 无头 API + SDK —— 见 §7、§11。

> 所有形态 **共用同一后端 + 同一个 1 年期 token 鉴权**,只是封装壳不同;account_id 一律从 token 解出、后端注入。

---

## 3. 鉴权:服务端换签(核心安全设计)

**已拍板(2026-06-07)**:**不做换签,直接发长期 token**——领号即发一个**有效期 1 年的账号 token**(API-key 风格),`Authorization: Bearer <token>` 直接用。account_id + 能力 scope 烤进 token,服务端校验。

```
领号(后台) ── 一次性发给合作方 ──▶ { token(1年), account_id, caps }
                                        │ token = 机密,等同 API key
                                        ▼
   合作方服务器 / 自建 UI ── Bearer <token> 调 /v1/chat(SSE) ──▶ Vigilath 网关
   合作方服务器 / 自建 UI ◀── delta / cards / done ─────────────  校验 token(验签+查启用)
                                                                 → account_id 后端注入 → agent.run()
```

**要点**:

- **token 是机密(等同 API key)**:理想用法是**服务器到服务器**(合作方后端直调)。
- **浏览器挂件场景别裸贴 token 进前端 HTML**:让合作方后端**代理转发**对话请求(token 留服务器),或退而求其次接受"该域名内任何人可用此账号"的风险。
- `account_id` 由 token 解出、后端注入,**调用方无法指定别的账号**。
- **能力 scope 烤进 token**(read / write),签发时定、校验侧只收不放。

> ⚠️ **长期 token 的代价**:不能再靠"短过期"做软吊销。**每次校验必须查一下 client/token 是否仍启用**(否则泄漏后一年内拦不住),见 §12.4 / §12.5。

---

## 4. 对外 API 契约(版本化稳定面)

前缀 `/api/agent/v1/`,对外契约一旦发布只增不改(破坏性变更升 v2)。

| 端点 | 调用方 | 用途 |
|---|---|---|
| `POST /chat`(SSE) | 合作方后端 / 自建 UI(Bearer token) | 对话(delta + cards + done),复用现有逻辑 |
| `POST /reset` | 同上 | 清账号会话历史 |
| `GET /data/coverage`、`/data/today`、`/data/report` … | 同上 | 只读数据(给不想走对话的 B 档,直接取卡片数据) |
| `GET /meta/capabilities` | 同上 | 当前 token 能做什么(读/写/发布开关),驱动前端按钮可见性 |

> 不再有换签端点(`/embed/session` 已去掉)。token 在**领号时一次性发放**,直接 Bearer 调用。

> `/data/*` 是把现有卡片工具(`get_query_coverage` 等)直接暴露成只读 REST,**绕过 LLM**,省 token、低延迟,给"只要数字不要对话"的集成。

---

## 5. 能力边界(对外收敛)

| 能力类 | 对外开放? | 控制点 |
|---|---|---|
| 只读查询(被搜到/诊断/增长/竞品) | ✅ 开放 | — |
| 诊断 / 产稿 / 规划(写,但留在账号内) | ✅ 开放(client 可配关) | `usage_guardrail_check` + 配额 |
| **真实对外发布 `publish_drafts`** | ❌ **对外层默认关闭** | `AGENT_ALLOW_EXTERNAL_PUBLISH` 护栏;发布只内部触发 |
| 引擎选择 / 调度 / 频率 | ❌ 平台固定 | 现状已不暴露给模型/用户 |

**用量护栏(对外新增,非计费)**:每账号 token/步数/跑批频率上限 + 速率限制,防外部刷爆;命中上限返回明确错误而非静默。

---

## 6. 安全硬化清单(对外暴露才需要)

- **CORS 白名单**:按 client 注册的 origin 放行,非白名单拒绝。
- **短时 token + 续签**:泄漏窗口 ≤10min;不下发可长期使用的凭证到浏览器。
- **每账号速率限制 + 配额**:防滥用、防爬。
- **审计日志**:`client_id / account_id / 端点 / 工具 / 时间`,可追溯谁代表哪个账号做了什么。
- **account_id 永不可由前端指定**:仅从 token 解出(现状即如此,对外层再加一道断言)。
- **错误不泄漏内部**:对外错误体收敛,不回栈/不回内部表名。

---

## 7. 嵌入 SDK(A 档体验)

```html
<!-- 合作方页面贴这一段即可 -->
<script src="https://geo.vigilath.com/embed/agent.js"
        data-token="emb_..."></script>           <!-- 浏览器直连(③);或 data-token-endpoint 指向自己后端代理(②) -->
```

- `data-token`:浏览器直连(形态③,token 裸露,见 §11.2 风险);要 token 不进浏览器则用 `data-token-endpoint` 指向**合作方后端代理**(形态②)。
- JS API(自建 UI):`Vigilath.Agent.init({ token })`、`.open()` / `.send(text)`、`on('cards' | 'delta' | 'done')`。
- 挂件 UI **复用现有 `AgentChatWidget`**(浮窗 + 卡片 + 主题变量),打包成 UMD/iife 单文件托管。

---

## 8. 模块改动(在现有 `geo/agent/` 上加,不重写)

```
backend/geo/agent/
  embed/                ← 新增:对外薄层
    tokens.py           签发(领号时)+ 校验(每请求,验签+查 enabled)1 年期 token
    deps.py             embed token → AgentDeps(account_id, 能力 scope)
    quota.py            每账号速率/配额护栏 + 审计写入
  api.py                + /v1/chat(token 改走 embed)、/v1/data/*、/v1/meta/capabilities
frontend/
  embed/agent.js        ← 新增:UMD 打包的嵌入挂件 + SDK(复用 AgentChatWidget)
```

**新增表**:

- `agent_tokens`:`tid / account_id / caps(read|write) / origins[] / enabled / expires_at`(token 元数据 + 吊销开关)
- `agent_embed_audit`:`tid / account_id / endpoint / tool / ts`(审计)

> 复用:account / user 体系、`ai_telemetry_*`、`agent_conversations`、`agent_materials` 全不动。

---

## 9. 落地顺序

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P0 安全地基 ✅(2026-06-07 已上线 vm02)** | 1 年期 token 签发/校验(查 enabled)+ CORS + `/v1/{chat,reset,data/*,meta}` + 领号 CLI + skill 包 | 对外可安全调 `/v1/*`,已端到端验证 |
| **P1 嵌入挂件** | `agent.js`(UMD,复用 Widget,`data-token`)+ 后端代理示例 | 合作方贴 script 即用 |
| **P2 只读数据面** | `/v1/data/*` + `/v1/meta/capabilities` | 自建 UI 不走 LLM 也能取数 |
| **P3(可选)MCP** | 工具层加 MCP adapter | 别的 AI agent 自动发现调用 GEO 工具 |

---

## 10. 待你拍板的点

1. **「领号」入口**:后台手动给合作方开(B2B 白标)/ 接口自助批量开 / 终端用户自助注册 —— 默认按"后台手动开"设计。
2. **一个 token 对一个还是一批账号**:默认一对一(一 token = 一账号);若合作方下游有很多终端用户各要独立账号,则每个子账号发各自的 token。
3. **写能力是否对外**:默认开"诊断/产稿/规划"、关"真实发布";若连产稿都想先关、只读上线,P0 即可纯只读。

---

## 11. 接入设计(端到端,可照做)

### 11.0 一句话总览

> 合作方领号拿到一个 **1 年期 token**(机密,等同 API key)→ 用 `Authorization: Bearer <token>` 调 `/v1/chat`(对话)或 `/v1/data/*`(取数)。**无换签、无续期**。token 是机密,server-to-server 直接用;浏览器场景由合作方后端代理转发。

### 11.1 接入前:领号,产出 token

后台为账号开通后,**一次性**产出(明文只展示一次):

```
token:     emb_eyJ...        # 1 年期 JWT,机密,等同 API key
account_id: 1000025          # 这只小龙虾代表哪个账号
caps:      ["read","write"]  # 能力 scope(write 是否含诊断/产稿)
origins:   ["https://partner.com"]   # 仅浏览器直连时校验的 CORS 白名单
```

### 11.2 对接形态(三选一,按对接方能力)

| 形态 | 谁调 `/v1/*` | token 放哪 | 适用 |
|---|---|---|---|
| **① server-to-server(最稳)** | 合作方后端 | 合作方服务器 | 对方有后端,理想用法 |
| **② 后端代理 + 挂件 UI** | 合作方后端转发 | 合作方服务器 | 要现成聊天窗 UI,又不想 token 进浏览器 |
| **③ 浏览器直连(最省事,有风险)** | 浏览器 | 浏览器(裸露) | 内部工具 / 可接受"该页面任何人可用此账号" |

> ②③ 的差别只在"token 放服务器还是浏览器"。要 UI 又要安全 → 选 ②(后端代理 + 我们的挂件);纯省事 → ③。

### 11.3 两条具体 HTTP 契约(就这两个)

**(a) 对话 —— SSE,沿用现有 /chat**

```
POST /api/agent/v1/chat
Authorization: Bearer emb_...
Content-Type: application/json

{ "message": "今天投放效果怎么样?" }

→ 200 text/event-stream
data: {"delta":"我来帮您查询"}
data: {"delta":"今天的投放..."}
data: {"cards":[{"tool":"get_today_effect","data":{...138...}}]}
data: {"done":true}
```

**(b) 只读取数 —— 不走 LLM,直接拿卡片 JSON**

```
GET /api/agent/v1/data/today
Authorization: Bearer emb_...

→ 200  {"today_new_hits":0,"cumulative_hit_queries":138,"monitored_queries":180,...}
```

### 11.4 形态 ① / ② 示例:合作方后端调 / 代理转发

```js
// 合作方 Node 后端:token 留在服务器,直接调(①)或代理浏览器请求(②)
const r = await fetch('https://geo.vigilath.com/api/agent/v1/chat', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${VIGILATH_TOKEN}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: userInput }),
});
// 形态②:把这个 SSE 流原样透传回自己前端的挂件即可
```

### 11.5 形态 ③ 示例:浏览器直接贴 script(最省事)

```html
<!-- token 直接写进 data 属性(注意:该页面任何人可见,等于把此账号能力开放给页面访客) -->
<script src="https://geo.vigilath.com/embed/agent.js"
        data-token="emb_..."></script>
```

挂件自动出现(浮窗 + 卡片,复用现有 `AgentChatWidget`)。自建 UI 用 SDK:

```js
import { VigilathAgent } from '@vigilath/agent-sdk';
const agent = VigilathAgent.init({ token: 'emb_...' });   // 或 ②:token: () => fetch('/my-proxy')
await agent.send('帮我看竞品对比', { onDelta, onCards, onDone });
const today = await agent.data('today');                  // GET /v1/data/today
```

### 11.6 错误约定(对外收敛,不泄内部)

| code | 含义 | 对接方处理 |
|---|---|---|
| `401 invalid_token` | 验签失败 / 已过期 / 已禁用 | 检查 token;到期联系我们重发 |
| `403 origin_forbidden` | (浏览器直连)Origin 不在白名单 | 联系我们加白名单 |
| `403 capability_denied` | 调了未授权能力(如发布) | 不该出现该入口 |
| `429 quota_exceeded` | 账号配额 / 限速 | 退避重试,展示"稍后再试" |

### 11.7 接入自检清单(给对接方)

- [ ] token 当机密保管(选 ①/② 时不进浏览器;选 ③ 已知风险)。
- [ ] (浏览器直连)页面域名已加入我们 `origins` 白名单。
- [ ] 写能力按需:只看数据的集成用只读 token(caps 只 `read`)。
- [ ] 只需数字的场景用 `/v1/data/*`,省 token、低延迟。
- [ ] token 临近 1 年到期前安排重发轮换。

---

## 12. Token 设计(直接长期 token,已拍板)

**模型(2026-06-07 拍板)**:领号时**一次性发一个有效期 1 年的账号 token**,`Authorization: Bearer <token>` 直接调用,**不做换签**。token 等同 API key,是机密。

### 12.1 为什么独立一类 token、独立密钥

不复用登录 JWT:登录 JWT 是给真实登录用户的全权凭证;这个是给**外部、代表某账号、能力收敛**的对接凭证。**独立签名密钥 `AGENT_EMBED_SECRET`**,与登录 `SECRET_KEY` 隔离 + `aud` 硬区分,互不可冒用、爆炸半径分开。

### 12.2 结构(JWT,HS256)

```json
{
  "iss": "vigilath-agent",
  "aud": "agent-embed",          // 强制比对,防止与登录 JWT 互相冒用
  "sub": "1000025",              // account_id —— 唯一账号真相,后端注入工具,调用方改不了
  "tid": "tok_7f3a...",          // token id,审计 + 吊销表主键
  "caps": ["read","write"],      // 能力 scope,端点/工具层强制(签发时定,校验侧只收不放)
  "iat": 1749283200,
  "exp": 1780819200              // iat + 1 年
}
```

### 12.3 签发(领号时,一次性)

1. 后台为账号建一条 `agent_tokens` 记录:`tid / account_id / caps / origins / enabled=true / expires_at(+1年)`。
2. 用 `AGENT_EMBED_SECRET` 签 JWT(载荷如上),**明文只展示一次**给合作方,库里只留 `tid` 与元数据(JWT 本身可不存)。
3. token = 机密,提示合作方按 API key 妥善保管。

### 12.4 校验(/v1/chat、/v1/data/* 每次请求)

> ⚠️ 长期 token **必须查库/缓存**,不能纯无状态(否则泄漏后一年内拦不住)。

1. 验签(`AGENT_EMBED_SECRET`)+ `aud=="agent-embed"` + `exp` 未过期。
2. **查 `agent_tokens[tid]`:`enabled=true`?**(吊销开关;带短 TTL 缓存,避免每请求打库)
3. `account_id = int(sub)` → 注入 `AgentDeps`(**绝不读请求体里的任何 account 字段**)。
4. 端点/工具按 `caps` 拦截:写类工具要求 `"write" in caps`,否则 `403 capability_denied`;发布类**无论 caps 都被 `AGENT_ALLOW_EXTERNAL_PUBLISH` 护栏挡**。
5.(浏览器直连时)`Origin` 复核在白名单。

### 12.5 吊销与轮换

- **吊销**:后台 `agent_tokens[tid].enabled=false` → 缓存 TTL(默认 60s)过后即全局失效。被盗用就禁这条,不影响其它账号。
- **轮换**:同账号可并存新旧两个 token 一段时间,合作方切换后禁旧的;1 年到期前提醒续发。
- **泄漏应对**:因为是机密 API key,泄漏=立即禁该 `tid` 重发;这也是"别把 token 裸贴进浏览器前端"的原因(浏览器场景让合作方后端代理转发)。

### 12.6 关键不变量(写进代码断言)

- `account_id` **只来自 token 的 `sub`**,任何端点都不接受请求体/query 里的 account 参数。
- `caps` 只能在签发时收窄,**校验侧不放大**。
- embed token 与登录 JWT **靠 `aud` 硬隔离**,互不可冒用。
- 真实发布**不受 token caps 控制**,只受平台内部护栏 —— 对外层无论如何拿不到发布。
- 校验**必查 enabled**(长期 token 的吊销命门)。

---

## 13. Agent-to-Agent:对方小龙虾链接本 agent(主用法)

> 场景:**对方运行自己的 AI agent(小龙虾),让它具备"GEO 优化"能力 —— 即链接到我们这只 GEO agent**。三种封装,共用同一 1 年 token + 同一后端,按对方 agent 框架能力选。

### 13.1 封装①:Skill 包(推荐 —— 最贴合"小龙虾"生态)

**思路**:把"会用 Vigilath GEO agent"打包成一个 **skill**,对方把它放进自己 agent 的 skills 目录,drop-in 即用。skill 是分发壳,底层调我们的 `/v1/chat`(或 `/v1/data/*`)。

**包结构**:

```
vigilath-geo/                      ← 解压进对方 agent 的 skills/ 目录
  SKILL.md                         ← 能力描述 + 何时用 + 调用说明(给对方 agent 读)
  scripts/
    geo_client.py                  ← 轻客户端:封装 Bearer token + 调 /v1/chat、/v1/data/*
  README.md                        ← 人读:如何配 token
```

**`SKILL.md`(关键是"何时用",让对方 agent 自决)**:

```markdown
---
name: vigilath-geo
description: 查询/优化品牌在 AI 搜索引擎(ChatGPT/Perplexity/DeepSeek/豆包等)的可见性与被引用率。
  当用户问"被搜到几个/AI 可见性/GEO/AEO/竞品在 AI 里的对比/怎么提升被引用"时使用。
---

# Vigilath GEO 优化

## 何时用
- 用户问 AI 搜索可见性、被搜到/命中、诊断、竞品 AI 对比、产稿优化建议时。

## 怎么用
- 对话式:`python scripts/geo_client.py chat "今天投放效果?"` → 返回答案 + 结构化卡片(JSON)。
- 只取数:`python scripts/geo_client.py data today` → 直接拿命中数字,不走 LLM、更快。

## 鉴权
- 读环境变量 `VIGILATH_AGENT_TOKEN`(1 年期 token,由 Vigilath 领号发放)。绝不硬编码。
```

**`geo_client.py`(轻客户端,~40 行)**:

```python
import os, sys, json, requests
BASE = os.environ.get("VIGILATH_BASE", "https://geo.vigilath.com/api/agent/v1")
TOK  = os.environ["VIGILATH_AGENT_TOKEN"]          # 1 年期 token,从环境读
H    = {"Authorization": f"Bearer {TOK}"}

def chat(msg):
    r = requests.post(f"{BASE}/chat", headers=H, json={"message": msg}, stream=True, timeout=120)
    for line in r.iter_lines():
        if line and line.startswith(b"data: "):
            print(json.loads(line[6:]))            # delta / cards / done

def data(name):
    print(requests.get(f"{BASE}/data/{name}", headers=H, timeout=30).json())

{"chat": lambda: chat(sys.argv[2]), "data": lambda: data(sys.argv[2])}[sys.argv[1]]()
```

对方 agent 读 `SKILL.md` 自己决定何时调,token 走环境变量。**我们要做的**:写好这个 skill 包 + 托管下载,领号时连 token 一起给。

### 13.2 封装②:MCP Server(工具级,LLM 自动发现)

**思路**:把 GEO 能力暴露成 **MCP 工具**,对方小龙虾作为 MCP Client 挂载,它的 LLM 自动发现并逐个调用。

- **Endpoint**:`https://geo.vigilath.com/api/agent/v1/mcp`(Streamable HTTP transport)。
- **鉴权**:连接头 `Authorization: Bearer <1年token>` → server 解出 account_id,所有工具在该账号 scope 内执行。
- **暴露工具**(现有工具的子集,按 caps):`get_query_coverage` / `get_today_effect` / `get_report` / `analyze_competitor` / `run_diagnosis` / `draft_article` / `ask_knowledge` …;`account_id` 不是工具参数(从 token 取),发布类不暴露。
- 对方小龙虾配置(示例):

```json
{ "mcpServers": { "vigilath-geo": {
    "url": "https://geo.vigilath.com/api/agent/v1/mcp",
    "headers": { "Authorization": "Bearer emb_..." } } } }
```

> 实现:在 `geo/agent/embed/` 加一个 MCP adapter,把已注册的 Pydantic AI 工具映射成 MCP 工具(共用 `AgentDeps`、同一 token 校验)。

### 13.3 封装③:/v1/chat 当黑盒"子 agent 工具"

**思路**:对方不想管细粒度工具,只在自己 agent 里注册**一个**工具 `ask_geo_expert(question)`,内部就是调我们 `/v1/chat`。我们这只 GEO agent 当它的"GEO 专家子 agent",自己跑完工具循环、回自然语言 + 卡片。

- 对方实现成本最低(一个工具函数 = §11.4 那段 fetch)。
- 编排权在**我们**这侧(我们的 LLM 决定调哪些内部工具)。

### 13.4 三者怎么选

| | Skill 包 | MCP | /v1/chat 黑盒 |
|---|---|---|---|
| 对方框架要求 | 支持 skill(小龙虾/Claude) | 支持 MCP | 任意(能发 HTTP) |
| 编排粒度 | skill 文档引导 + 客户端 | 细(对方 LLM 逐工具) | 粗(我们 LLM 包办) |
| 对方接入成本 | 低(drop-in) | 低(配 endpoint) | 最低(一个工具) |
| 我们工作量 | 写 skill 包 | 写 MCP adapter | 几乎为零(已有 /v1/chat) |

**建议**:先上**③(零额外开发,验证链路)**→ 配套**①Skill 包(主推,贴合小龙虾)**→ 需要细粒度工具编排再上 **②MCP**。三者底层同一 token、同一后端、同一护栏。
