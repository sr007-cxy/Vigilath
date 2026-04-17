# MoltsPay 支付集成开发计划（Node.js 方案）

> 在 GApex 会员支付中增加 MoltsPay（USDC 加密货币）支付选项，与现有 Stripe（信用卡）并行。

## 一、背景

- 当前支付：仅支持 Stripe（信用卡），流程为 Checkout Session → Stripe 托管页面 → Webhook/安全网回调 → 激活会员
- 目标：新增 MoltsPay（基于 x402 协议的 USDC 链上支付）
- MoltsPay 仓库：https://github.com/Yaqing2023/moltspay

## 二、已确认项

| 项目 | 确认结果 |
|---|---|
| 环境 | **mainnet**（生产环境） |
| 链 | **Base**（gas 最低，确认 ~2s） |
| 收款钱包 | `0xb8d6f2441e8f8dfB6288A74Cf73804cDd0484E0C` |
| 集成方式 | **MoltsPayServer (Node.js)** 微服务 + FastAPI HTTP 调用 |
| gas 费 | **平台承担**（通过 CDP facilitator，gasless） |
| 支付币种 | USDC（1:1 锚定 USD，无汇率风险） |

## 三、技术方案选型

### 为什么选 Node.js MoltsPayServer :3010（而非 Python SDK）

| 对比项 | Python SDK | Node.js MoltsPayServer |
|---|---|---|
| 角色 | 客户端（发起支付） | 服务端（接收支付） |
| x402 协议 | 需自己实现服务端逻辑 | 内置完整实现 |
| 链上验证 | 需自己写 RPC 查询代码 | 自动验证签名+链上结算 |
| 私钥管理 | 需要处理 | 不需要（CDP facilitator 处理） |
| 工作量 | ~17h | ~12h |

**结论**：Node.js 方案省掉了最复杂的链上验证部分，MoltsPayServer 已封装好 x402 协议。

## 四、整体架构

```
                          www.vigilath.com (nginx :80)
                                    │
                    ┌───────────────┼──────────────────┐
                    │               │                  │
               /api/*          /pay/*             /* (静态)
                    │               │                  │
                    ▼               ▼                  ▼
            FastAPI (uvicorn)  MoltsPayServer    前端 SPA
            127.0.0.1:8070    127.0.0.1:3010     /var/www/html/
                    │               │
                    │    HTTP 调用   │
                    │◀─────────────▶│
                    │               │
                    ▼               ▼
             payment_sessions   x402 协议
             (SQLite)           链上结算 (Base)
```

### 组件职责

| 组件 | 端口 | 职责 |
|---|---|---|
| **nginx** | :80 | 入口路由，反代 `/api/` → FastAPI，`/pay/` → MoltsPayServer |
| **FastAPI** | :8070 | 业务逻辑：创建订单、管理会员、记录支付 |
| **MoltsPayServer** | :3010 | x402 协议处理：接收签名、验证支付、链上结算 |
| **前端 SPA** | 静态 | 支付方式选择、USDC 支付交互 |

## 五、支付流程（详细）

```
用户                    前端 SPA              FastAPI              MoltsPayServer       Base 链
 │                        │                     │                      │                  │
 │ 1. 点击"USDC支付"       │                     │                      │                  │
 │───────────────────────▶│                     │                      │                  │
 │                        │ 2. POST /api/payment│/moltspay/create      │                  │
 │                        │────────────────────▶│                      │                  │
 │                        │                     │ 3. 创建 pending 订单  │                  │
 │                        │                     │ 返回 {payment_id,    │                  │
 │                        │                     │  amount, pay_url}    │                  │
 │                        │ 4. 返回支付信息       │                      │                  │
 │                        │◀────────────────────│                      │                  │
 │ 5. 展示支付面板         │                     │                      │                  │
 │◀───────────────────────│                     │                      │                  │
 │                        │                     │                      │                  │
 │ 6. 用户钱包调用 pay_url │                     │                      │                  │
 │────────────────────────────────────────────────────────────────────▶│                  │
 │                        │                     │                      │ 7. 返回 402       │
 │                        │                     │                      │◀─────────────────│
 │ 8. 钱包签名（免gas）    │                     │                      │                  │
 │────────────────────────────────────────────────────────────────────▶│                  │
 │                        │                     │                      │ 9. 验证签名       │
 │                        │                     │                      │─────────────────▶│
 │                        │                     │                      │ 10. 链上结算      │
 │                        │                     │                      │◀─────────────────│
 │                        │                     │                      │ 11. 返回 200 成功 │
 │◀───────────────────────────────────────────────────────────────────│                  │
 │                        │                     │                      │                  │
 │                        │ 12. 前端轮询状态      │                      │                  │
 │                        │────────────────────▶│ 13. 查询 MoltsPayServer 回调/状态        │
 │                        │                     │─────────────────────▶│                  │
 │                        │                     │ 14. 确认 paid         │                  │
 │                        │                     │ 激活会员              │                  │
 │                        │◀────────────────────│                      │                  │
 │ 15. 跳转成功页          │                     │                      │                  │
 │◀───────────────────────│                     │                      │                  │
```

## 六、MoltsPayServer 配置

### 6.1 服务清单 `moltspay.services.json`

将 GApex 会员方案注册为 MoltsPay 服务：

```json
{
  "$schema": "https://moltspay.com/schemas/services.json",
  "provider": {
    "name": "GApex GEO Readiness Checker",
    "description": "GEO detection and optimization platform",
    "wallet": "0xb8d6f2441e8f8dfB6288A74Cf73804cDd0484E0C",
    "chains": ["base"]
  },
  "services": [
    {
      "id": "membership-detector",
      "name": "Detector Membership (Monthly)",
      "description": "Full 25-category GEO check, 20 checks/month",
      "function": "purchaseMembership",
      "price": 9.99,
      "currency": "USDC",
      "input": {
        "user_id": { "type": "number", "required": true },
        "membership_slug": { "type": "string", "required": true }
      },
      "output": {
        "success": { "type": "boolean" },
        "payment_id": { "type": "string" }
      }
    },
    {
      "id": "membership-starter",
      "name": "Starter Membership (Monthly)",
      "description": "Unlimited checks + optimization suggestions",
      "function": "purchaseMembership",
      "price": 999,
      "currency": "USDC",
      "input": {
        "user_id": { "type": "number", "required": true },
        "membership_slug": { "type": "string", "required": true }
      },
      "output": {
        "success": { "type": "boolean" },
        "payment_id": { "type": "string" }
      }
    },
    {
      "id": "membership-growth",
      "name": "Growth Membership (Monthly)",
      "description": "Unlimited checks + optimization + paid placement",
      "function": "purchaseMembership",
      "price": 2500,
      "currency": "USDC",
      "input": {
        "user_id": { "type": "number", "required": true },
        "membership_slug": { "type": "string", "required": true }
      },
      "output": {
        "success": { "type": "boolean" },
        "payment_id": { "type": "string" }
      }
    }
  ]
}
```

### 6.2 服务端实现 `index.js`

```javascript
import { MoltsPayServer } from 'moltspay/server';

const server = new MoltsPayServer('./moltspay.services.json');

// 支付成功后的回调：通知 FastAPI 激活会员
server.skill('purchaseMembership', async (params) => {
  const { user_id, membership_slug } = params;

  // 调用 FastAPI 内部端点激活会员
  const res = await fetch('http://127.0.0.1:8070/api/payment/moltspay/fulfill', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id, membership_slug }),
  });

  const data = await res.json();
  return { success: data.success, payment_id: data.payment_id };
});

server.listen(3010);
console.log('MoltsPayServer listening on :3010');
```

### 6.3 systemd 服务 `/etc/systemd/system/moltspay.service`

```ini
[Unit]
Description=MoltsPay x402 Payment Server
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/Dev/geo/moltspay-server
ExecStart=/usr/bin/node index.js
Restart=always
RestartSec=3
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

### 6.4 nginx 新增 location

在 `www.vigilath.com` server 块中添加：

```nginx
# MoltsPay x402 支付端点
location /pay/ {
    proxy_pass http://127.0.0.1:3010/;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 60s;
}
```

### 6.5 CDP 凭证

MoltsPayServer 使用 Coinbase Developer Platform (CDP) 作为 facilitator 处理 Base 链结算：

```bash
# ~/.moltspay/.env
CDP_API_KEY_ID=<从 portal.cdp.coinbase.com 获取>
CDP_API_KEY_SECRET=<从 portal.cdp.coinbase.com 获取>
```

需要在 https://portal.cdp.coinbase.com/ 注册获取。

## 七、FastAPI 后端变更

### 7.1 数据库 migration（`backend/migrations/006_moltspay_payment.py`）

```python
"""幂等 migration：为 payment_sessions 添加 MoltsPay 支持字段"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'geo_checker.db')

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(payment_sessions)")}
    if 'provider' not in existing:
        cursor.execute("ALTER TABLE payment_sessions ADD COLUMN provider TEXT NOT NULL DEFAULT 'stripe'")
    if 'chain' not in existing:
        cursor.execute("ALTER TABLE payment_sessions ADD COLUMN chain TEXT")
    if 'tx_hash' not in existing:
        cursor.execute("ALTER TABLE payment_sessions ADD COLUMN tx_hash TEXT")
    if 'wallet_address' not in existing:
        cursor.execute("ALTER TABLE payment_sessions ADD COLUMN wallet_address TEXT")
    conn.commit()
    conn.close()
    print("Migration 006 complete.")

if __name__ == '__main__':
    migrate()
```

### 7.2 ORM 模型更新（`backend/geo/models/payment.py`）

新增字段：

```python
provider = Column(String, nullable=False, default="stripe")    # 'stripe' | 'moltspay'
chain = Column(String, nullable=True)                          # 'base'
tx_hash = Column(String, nullable=True)                        # 链上交易哈希
wallet_address = Column(String, nullable=True)                 # 收款钱包地址
```

### 7.3 新增 API 路由（`backend/geo/api/moltspay_payment.py`）

| 端点 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/payment/moltspay/create` | POST | Bearer | 创建 USDC 支付订单，返回 pay_url |
| `/api/payment/moltspay/status/{payment_id}` | GET | Bearer | 前端轮询支付状态 |
| `/api/payment/moltspay/fulfill` | POST | 内网 | MoltsPayServer 回调，激活会员 |

**`POST /api/payment/moltspay/create`**

```python
# 请求
{ "slug": "pro" }  # 会员方案 slug

# 逻辑
1. 查会员方案价格
2. 防重：复用未过期的 pending 订单（同 Stripe 逻辑）
3. 创建 payment_session (provider=moltspay, status=pending)
4. 构造 pay_url: https://www.vigilath.com/pay/membership-detector
   (指向 MoltsPayServer，附带 user_id 和 membership_slug)

# 响应
{
  "payment_id": 15,
  "amount_usdc": 9.99,
  "chain": "base",
  "pay_url": "https://www.vigilath.com/pay/membership-detector?user_id=5&membership_slug=pro",
  "wallet_address": "0xb8d6...4E0C"
}
```

**`POST /api/payment/moltspay/fulfill`**（仅内网访问）

```python
# MoltsPayServer 支付成功后回调
{ "user_id": 5, "membership_slug": "pro" }

# 逻辑
1. 找到该用户的 pending moltspay session
2. 标记 paid + completed_at
3. 调用 membership_service.upgrade_membership()
4. 返回 { success: true, payment_id: 15 }
```

### 7.4 .env 配置

```env
# MoltsPay
MOLTSPAY_ENABLED=true
MOLTSPAY_WALLET_ADDRESS=0xb8d6f2441e8f8dfB6288A74Cf73804cDd0484E0C
MOLTSPAY_CHAIN=base
MOLTSPAY_SERVER_URL=http://127.0.0.1:3010
```

### 7.5 fulfill 端点安全

`/api/payment/moltspay/fulfill` 只允许内网调用（MoltsPayServer → FastAPI）：

```python
@router.post("/moltspay/fulfill")
async def moltspay_fulfill(request: Request, body: FulfillRequest):
    # 仅允许 localhost 调用
    client_ip = request.client.host
    if client_ip not in ("127.0.0.1", "::1"):
        raise AppException(status_code=403, message="Forbidden")
    # ... 激活会员逻辑
```

## 八、前端变更

### 8.1 CheckoutPending 页面改造

```
┌─────────────────────────────────────────┐
│           订阅方案：Detector             │
│           金额：$9.99                    │
│                                         │
│   ┌──────────────┐ ┌────────────────┐   │
│   │  💳 信用卡    │ │  💰 USDC       │   │
│   │   (Stripe)   │ │  (Base Chain)  │   │
│   └──────────────┘ └────────────────┘   │
│                                         │
│  ── 选择 Stripe ──                      │
│  [ 跳转到 Stripe 支付 ]                  │
│                                         │
│  ── 选择 USDC ──                        │
│  金额: 9.99 USDC (Base)                 │
│  状态: 等待支付...                       │
│                                         │
│  请使用支持 x402 协议的钱包完成支付：     │
│  • MoltsPay CLI: moltspay pay ...       │
│  • 或任何兼容 x402 的钱包客户端          │
│                                         │
│  ⏳ 正在等待链上确认...                   │
│  [取消]                                  │
└─────────────────────────────────────────┘
```

### 8.2 涉及前端文件

| 文件 | 改动 |
|---|---|
| `CheckoutPending.tsx` | 增加支付方式选择 + USDC 支付面板 + 轮询状态 |
| `CheckoutSuccess.tsx` | 支持 MoltsPay 成功状态（显示 tx_hash） |
| `PaymentsTab.tsx` | 显示 provider 列（Stripe / USDC） |
| `paymentApi.ts` | 新增 `createMoltsPaySession()`, `getMoltsPayStatus()` |
| `en.ts` / `zh.ts` | USDC 支付相关翻译 |

### 8.3 支付记录展示

| 时间 | 方案 | 金额 | 支付方式 | 状态 | 完成时间 |
|---|---|---|---|---|---|
| 04-16 10:00 | Detector | $9.99 | Stripe | Paid | 04-16 10:01 |
| 04-16 11:00 | Detector | 9.99 USDC | Base USDC | Paid | 04-16 11:00 |

## 九、文件清单

### 新增文件

| 文件 | 说明 |
|---|---|
| `moltspay-server/package.json` | MoltsPayServer Node 项目 |
| `moltspay-server/index.js` | 服务端实现 |
| `moltspay-server/moltspay.services.json` | 服务清单 |
| `/etc/systemd/system/moltspay.service` | systemd 服务 |
| `backend/migrations/006_moltspay_payment.py` | 数据库 migration |
| `backend/geo/api/moltspay_payment.py` | FastAPI 路由 |
| `backend/geo/services/moltspay_service.py` | 业务逻辑 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `backend/geo/models/payment.py` | ORM 新增字段 |
| `backend/geo/main.py` | 注册新路由 |
| `backend/.env` | MoltsPay 配置 |
| `/etc/nginx/nginx.conf` | 新增 `/pay/` location |
| `frontend/src/pages/CheckoutPending.tsx` | 支付方式选择 |
| `frontend/src/pages/CheckoutSuccess.tsx` | USDC 成功展示 |
| `frontend/src/pages/Account/PaymentsTab.tsx` | provider 列 |
| `frontend/src/services/paymentApi.ts` | MoltsPay API |
| `frontend/src/i18n/en.ts` | 英文翻译 |
| `frontend/src/i18n/zh.ts` | 中文翻译 |

## 十、安全措施

1. **x402 协议内置验证**：MoltsPayServer 自动验证签名和链上结算
2. **fulfill 端点限内网**：仅允许 127.0.0.1 调用，防止外部伪造
3. **防重提交**：同一用户同一方案复用未过期的 pending 订单
4. **支付超时**：30 分钟未完成自动标记 expired
5. **nginx 限流**：`/pay/` 端点可配 rate limit 防止滥用

## 十一、部署步骤

```bash
# 1. 安装 MoltsPayServer
mkdir -p /home/ubuntu/Dev/geo/moltspay-server
cd /home/ubuntu/Dev/geo/moltspay-server
npm init -y
npm install moltspay

# 2. 创建 moltspay.services.json 和 index.js（见第六节）

# 3. 配置 CDP 凭证
mkdir -p ~/.moltspay
# 写入 CDP_API_KEY_ID 和 CDP_API_KEY_SECRET

# 4. 创建 systemd 服务
sudo cp moltspay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable moltspay.service
sudo systemctl start moltspay.service

# 5. 更新 nginx（新增 /pay/ location）
sudo nginx -t && sudo systemctl reload nginx

# 6. 运行数据库 migration
cd /home/ubuntu/Dev/geo/backend
.venv/bin/python migrations/006_moltspay_payment.py

# 7. 更新 .env（新增 MOLTSPAY_* 配置）

# 8. 重启 FastAPI
sudo systemctl restart geo-checker.service

# 9. 构建前端
cd /home/ubuntu/Dev/geo/frontend
npm run build
sudo rsync -a --delete dist/ /var/www/html/www.vigilath.com/
sudo chown -R www-data:www-data /var/www/html/www.vigilath.com/

# 10. 冒烟测试
curl -sI https://www.vigilath.com/pay/  # 应返回 MoltsPayServer 响应
```

## 十二、风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| USDC 不可逆 | 退款需手动链上转账 | 记录 tx_hash，退款时手动操作 |
| CDP 凭证泄露 | 可能被滥用 | 限制凭证权限，定期轮换 |
| MoltsPayServer 宕机 | USDC 支付不可用 | systemd 自动重启 + 前端降级提示 |
| 网络拥堵 | 确认延迟 | Base 通常 <2s，设 60s 超时 |
| 汇率偏差 | USDC 轻微脱锚 | 1:1 定价，风险极低 |

## 十三、开发排期

| # | 任务 | 预估 |
|---|---|---|
| 1 | MoltsPayServer 搭建 + 配置 | 2h |
| 2 | systemd + nginx 配置 | 1h |
| 3 | 数据库 migration + ORM 更新 | 1h |
| 4 | FastAPI 路由 + 业务逻辑 | 3h |
| 5 | 前端支付方式选择 + USDC 面板 | 4h |
| 6 | 支付记录 provider 展示 | 1h |
| 7 | i18n 翻译 | 0.5h |
| 8 | 集成测试 | 2h |
| 9 | 部署上线 | 1h |
| | **合计** | **~15h (2-3 天)** |

## 十四、前置条件

- [ ] 注册 Coinbase Developer Platform，获取 CDP_API_KEY_ID 和 CDP_API_KEY_SECRET
- [ ] 确认收款钱包 `0xb8d6...4E0C` 在 Base 链上可接收 USDC
