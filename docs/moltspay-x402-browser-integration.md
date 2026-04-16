# MoltsPay x402 浏览器集成方案

## 一、x402 协议核心机制

x402 使用 **EIP-3009 `transferWithAuthorization`** 实现 gasless 支付：
- 客户端（钱包）只做**签名**（`signTypedData`），不发送链上交易，不消耗 gas
- 签名发给 MoltsPayServer → 转交 CDP Facilitator → Facilitator 在链上执行转账并代付 gas

这意味着 **MetaMask 可以直接参与**：MetaMask 支持 `eth_signTypedData_v4`（EIP-712），正好是 EIP-3009 需要的签名方式。

## 二、完整流程

```
浏览器 (MetaMask)              FastAPI                MoltsPayServer        CDP Facilitator
    │                            │                        │                      │
    │  1. 选择 USDC 支付          │                        │                      │
    │  2. 创建订单               │                        │                      │
    │───────────────────────────▶│                        │                      │
    │                            │  3. 返回 payment_id     │                      │
    │◀───────────────────────────│                        │                      │
    │                            │                        │                      │
    │  4. POST /pay/execute (无 X-Payment header)          │                      │
    │─────────────────────────────────────────────────────▶│                      │
    │  5. 返回 402 + x-payment-required (Base64 JSON)      │                      │
    │◀─────────────────────────────────────────────────────│                      │
    │                            │                        │                      │
    │  6. 解析 payment requirements                        │                      │
    │     提取: payTo, amount, network, extra              │                      │
    │                            │                        │                      │
    │  7. 构造 EIP-3009 TypedData:                        │                      │
    │     domain: {name:"USD Coin", version:"2",           │                      │
    │              chainId:8453, verifyingContract:USDC}    │                      │
    │     types: TransferWithAuthorization                  │                      │
    │     value: {from, to:payTo, value, validAfter,       │                      │
    │             validBefore, nonce}                       │                      │
    │                            │                        │                      │
    │  8. MetaMask 弹出签名确认                             │                      │
    │     eth_signTypedData_v4                              │                      │
    │     (用户确认 — 不消耗 gas)                            │                      │
    │                            │                        │                      │
    │  9. 构造 X-Payment header (Base64 JSON):             │                      │
    │     { x402Version, scheme, network,                   │                      │
    │       payload: {authorization, signature},            │                      │
    │       accepted: {scheme, network, asset, amount,      │                      │
    │                  payTo, maxTimeoutSeconds, extra} }    │                      │
    │                            │                        │                      │
    │  10. POST /pay/execute + X-Payment header            │                      │
    │─────────────────────────────────────────────────────▶│                      │
    │                            │                        │ 11. 验证签名          │
    │                            │                        │─────────────────────▶│
    │                            │                        │ 12. 链上执行转账      │
    │                            │                        │    (代付 gas)         │
    │                            │                        │◀─────────────────────│
    │                            │                        │                      │
    │                            │  13. MoltsPayServer     │                      │
    │                            │  回调 /fulfill          │                      │
    │                            │◀───────────────────────│                      │
    │                            │  14. 激活会员           │                      │
    │                            │                        │                      │
    │  15. 返回 200 + result     │                        │                      │
    │◀─────────────────────────────────────────────────────│                      │
    │                            │                        │                      │
    │  16. 跳转成功页             │                        │                      │
```

## 三、前端实现（关键代码）

### 3.1 获取 402 Payment Requirements

```typescript
// Step 1: POST without X-Payment → get 402
const res = await fetch('https://www.vigilath.com/pay/execute', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    service: 'membership-detector',
    params: { user_id: 5, membership_slug: 'pro' },
    chain: 'base',
  }),
});

// res.status === 402
const requirementsB64 = res.headers.get('x-payment-required');
const requirements = JSON.parse(atob(requirementsB64));
// requirements 是数组，找 network === 'eip155:8453' 的那条
```

### 3.2 EIP-3009 签名（MetaMask）

```typescript
import { BrowserProvider, randomBytes, hexlify } from 'ethers';

const provider = new BrowserProvider(window.ethereum);
const signer = await provider.getSigner();
const from = await signer.getAddress();

const req = requirements.find(r => r.network === 'eip155:8453');
const payTo = req.payTo || req.resource;
const amount = req.amount || req.maxAmountRequired;
const extra = req.extra || { name: 'USD Coin', version: '2' };

const authorization = {
  from,
  to: payTo,
  value: amount,  // 已经是 6 位小数的字符串
  validAfter: '0',
  validBefore: String(Math.floor(Date.now() / 1000) + 3600),
  nonce: hexlify(randomBytes(32)),
};

const domain = {
  name: extra.name || 'USD Coin',
  version: extra.version || '2',
  chainId: 8453,
  verifyingContract: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
};

const types = {
  TransferWithAuthorization: [
    { name: 'from', type: 'address' },
    { name: 'to', type: 'address' },
    { name: 'value', type: 'uint256' },
    { name: 'validAfter', type: 'uint256' },
    { name: 'validBefore', type: 'uint256' },
    { name: 'nonce', type: 'bytes32' },
  ],
};

// MetaMask 弹出签名确认 — 用户只签名，不消耗 gas
const signature = await signer.signTypedData(domain, types, authorization);
```

### 3.3 发送带签名的请求

```typescript
const payload = {
  x402Version: 1,
  scheme: 'exact',
  network: 'eip155:8453',
  payload: { authorization, signature },
  accepted: {
    scheme: 'exact',
    network: 'eip155:8453',
    asset: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    amount,
    payTo,
    maxTimeoutSeconds: 300,
    extra,
  },
};

const paymentHeader = btoa(JSON.stringify(payload));

const paidRes = await fetch('https://www.vigilath.com/pay/execute', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-Payment': paymentHeader,
  },
  body: JSON.stringify({
    service: 'membership-detector',
    params: { user_id: 5, membership_slug: 'pro' },
    chain: 'base',
  }),
});

// paidRes.status === 200 → 支付成功，MoltsPayServer 已回调 /fulfill
```

## 四、与当前直接转账方案的对比

| 对比项 | 直接转账（当前） | x402 签名（本方案） |
|---|---|---|
| 用户操作 | 确认一笔转账交易 | 确认一个签名（更轻） |
| Gas 费 | 用户支付（~$0.001） | Facilitator 代付（用户 0 gas） |
| 链上验证 | 后端 RPC 查 receipt | MoltsPayServer 自动处理 |
| 安全性 | 直接转账 | EIP-3009 签名 + Facilitator 验证 |
| 实现复杂度 | 低 | 中（需要构造 EIP-712 TypedData） |

## 五、涉及修改

| 文件 | 改动 |
|---|---|
| `frontend/src/pages/CheckoutPending.tsx` | USDC 支付改为 x402 签名流程 |
| `frontend/src/services/paymentApi.ts` | 移除 verifyMoltsPayTx，不再需要后端验证 |
| `backend/geo/api/moltspay_payment.py` | 可移除 `/verify` 端点，`/fulfill` 仍保留 |

## 六、前端完整流程代码

```
1. 用户点 "Connect Wallet & Pay"
2. 连接 MetaMask + 切换到 Base
3. POST /pay/execute (无 X-Payment) → 得到 402 + requirements
4. 解析 requirements → 构造 EIP-3009 TypedData
5. MetaMask signTypedData → 得到 signature（不消耗 gas）
6. POST /pay/execute + X-Payment header → 200 成功
7. MoltsPayServer skill callback → POST /api/payment/moltspay/fulfill
8. 前端轮询 /api/payment/moltspay/status → paid → 跳转成功页
```

## 七、注意事项

- 用户钱包需要在 Base 链上有足够的 USDC 余额
- 用户**不需要 ETH**（签名不消耗 gas）
- MetaMask 签名弹窗会显示转账详情（金额、收款地址），用户可以验证
- EIP-3009 签名有有效期（validBefore），默认 1 小时
