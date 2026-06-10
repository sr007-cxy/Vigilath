# Vigilath AI 引擎查询 API — 调用文档

给定 `(引擎, 问题)`,驱动真实 AI 引擎(网页版 / 官方 API)返回**它实际给出的答案 + 引用来源**。
异步任务模型:下单拿 `job_id`,轮询或 webhook 取结果。

---

## 1. 基址与鉴权

| 项 | 值 |
|---|---|
| 基址 | `https://geo.sr007.com/v1` |
| 鉴权 | 请求头 `Authorization: Bearer sk-live-xxxx` |
| 内容类型 | `Content-Type: application/json`(POST) |

> API Key(`sk-live-…`)由平台方在后台为你的租户创建,**仅创建时明文返回一次**,请妥善保存。
> 鉴权失败返回 `401`。

---

## 2. 引擎

| engine | 名称 | 通道 | 说明 |
|---|---|---|---|
| `yuanbao` | 腾讯元宝 | 官方 API(网页提示词 元宝搜索 + 合成) + 网页版(真实浏览器)| 稳定,~60s,带真实引用 |
| `doubao` | 字节豆包 | 官方 API(网页提示词 元宝搜索 + 合成) + 网页版(真实浏览器) | 稳定,~60s,带引用 |
| `deepseek` | DeepSeek | 网页版(真实浏览器) | 反映网页版真实回答;受日额度约束 |
| `qwen` | 阿里通义千问 | 网页版 | 同上 |
| `wenxin` | 百度文心一言 | 网页版 | 同上 |

> 每个租户有**引擎白名单**;调用白名单外的引擎返回 `403`。
> 网页版引擎受**每日额度**约束,高峰可能排队;API 引擎(元宝/豆包)更稳定。

---

## 3. 接口

### 3.1 查询可用引擎与配额
```
GET /v1/engines
```
```json
{
  "tier": "pro",
  "engines": [
    {"engine": "deepseek", "daily_quota": 20, "used_today": 3, "remaining": 17},
    {"engine": "yuanbao",  "daily_quota": 20, "used_today": 0, "remaining": 20}
  ]
}
```

### 3.2 下单(异步)
```
POST /v1/jobs
```
请求体:
| 字段 | 必填 | 说明 |
|---|---|---|
| `engine` | 是 | 引擎 id(见上表) |
| `query` | 是 | 要问的问题(≤2000 字) |
| `idempotency_key` | 否 | 幂等键;同 key 重复提交只建一条、防重复扣费 |
| `callback_url` | 否 | 任务完成时平台 POST 结果到此地址(免轮询) |

```bash
curl -X POST https://geo.sr007.com/v1/jobs \
  -H "Authorization: Bearer sk-live-xxxx" \
  -H "Content-Type: application/json" \
  -d '{"engine":"yuanbao","query":"威吉力是做什么的?","idempotency_key":"req-001"}'
```
响应:
```json
{"job_id": 123, "status": "queued"}
```

### 3.3 取结果
```
GET /v1/jobs/{job_id}
```
```json
{
  "job_id": 123,
  "engine": "yuanbao",
  "query": "威吉力是做什么的?",
  "status": "done",
  "answer": "……AI 的完整回答……",
  "citations": [
    {"url": "https://...", "domain": "example.com", "title": "...", "snippet": "...", "position": 1}
  ],
  "source_url": null,
  "video_url": null,
  "error": null,
  "created_at": "2026-06-10T03:00:00",
  "finished_at": "2026-06-10T03:00:15"
}
```
`status` 取值:`queued`(排队) → `claimed`(执行中) → `done`(完成) / `failed`(失败,见 `error`)。

### 3.4 查用量与余额
```
GET /v1/usage
```
```json
{"credit_balance": 480, "billable_total": 20, "credits_spent_total": 20, "today_by_engine": {"yuanbao": 5}}
```

---

## 4. 完整示例

### curl(下单 + 轮询)
```bash
BASE=https://geo.sr007.com/v1
KEY=sk-live-xxxx

JID=$(curl -s -X POST $BASE/jobs -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"engine":"yuanbao","query":"小米SU7怎么样"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

while true; do
  R=$(curl -s $BASE/jobs/$JID -H "Authorization: Bearer $KEY")
  ST=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  [ "$ST" = done -o "$ST" = failed ] && { echo "$R"; break; }
  sleep 5
done
```

### Python
```python
import time, requests

BASE = "https://geo.sr007.com/v1"
H = {"Authorization": "Bearer sk-live-xxxx"}

job = requests.post(f"{BASE}/jobs", headers=H,
                    json={"engine": "yuanbao", "query": "小米SU7怎么样"}).json()
jid = job["job_id"]

while True:
    r = requests.get(f"{BASE}/jobs/{jid}", headers=H).json()
    if r["status"] in ("done", "failed"):
        print(r["answer"], r["citations"])
        break
    time.sleep(5)
```

---

## 5. Webhook 回调(可选,免轮询)

下单时带 `callback_url`,任务完成后平台 `POST` 结果到该地址:
```json
{"job_id":123,"status":"done","engine":"yuanbao","answer":"...","citations":[...],"video_url":null,"error":null}
```
请求头带 `X-Signature: sha256=<hmac>`,用双方约定的密钥对 body 做 HMAC-SHA256 校验,防伪造。

---

## 6. 错误码

| HTTP | 含义 | 处理 |
|---|---|---|
| `401` | API Key 缺失/无效 | 检查 `Authorization` 头 |
| `403` | 引擎不在你的白名单 | 联系平台开通该引擎 |
| `402` | 额度(credits)不足 | 充值 |
| `422` | 问题被内容审核拦截 | 调整问题内容 |
| `429` | 当日配额已满 | 看响应头 `Retry-After`,次日或加配额后再试 |
| `404` | job 不存在 / 非本租户 | 检查 job_id |
| `405` | 方法错误 | `/v1/jobs` 用 POST,`/v1/jobs/{id}` 用 GET |

---

## 7. 计量与配额

- **配额**:每租户**每引擎每日**有调用上限(`GET /v1/engines` 看 `remaining`);失败的调用不计配额。
- **计费**:每条**成功** job 按引擎单价扣 credits(失败不计费);余额见 `GET /v1/usage`。
- **时延参考**:元宝 ~60s、豆包 ~60s、网页版引擎数十秒~数分钟(取决于排队)。下单是异步的,务必轮询或用 webhook。

---
