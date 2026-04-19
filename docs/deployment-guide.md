# GEO Readiness Checker —— 部署与维护指南

> 本文档描述 **www.vigilath.com** 当前线上的真实部署方式，面向运维发布流程。
> 开发环境请参考文末「开发流程」章节。

## 一、线上架构

线上一台 EC2（Ubuntu）承载整个站点，按五层组织：

| 层 | 组件 | 位置 / 端口 |
|---|---|---|
| DNS / 转发 | GoDaddy Domain Forwarding（apex）+ Route 53 / DNS 别名（www） | `vigilath.com` → GoDaddy 301；`www.vigilath.com` → AWS ALB |
| 边缘 TLS | AWS Application Load Balancer（ALB） | `vigilath-alb-01-654513483.us-east-1.elb.amazonaws.com`，TLS 终止（ACM 证书：`www.vigilath.com` + SAN `vigilath.com`） |
| 入口 | nginx | `/etc/nginx/nginx.conf` 内 `server_name www.vigilath.com` 块，listen 80（ALB 内网回源） |
| 后端 | FastAPI + uvicorn | systemd 服务 `geo-checker.service`，监听 `127.0.0.1:8070` |
| 支付 | MoltsPayServer (Node.js) | systemd 服务 `moltspay.service`，监听 `127.0.0.1:3010` |
| 前端 | Vite 构建的 SPA 静态产物 | `/var/www/html/www.vigilath.com/`（属主 `www-data:www-data`） |

**流量路径**：

- `https://www.vigilath.com/` → AWS ALB 终止 TLS（ACM 证书）→ 明文回源 EC2:80 → nginx → FastAPI / 静态资源
- `https://vigilath.com/`（apex）→ **GoDaddy Domain Forwarding** 返回 301 → 落到 `www.vigilath.com`（见「已知问题」章节）

本机 nginx 只监听 80，ALB → EC2 之间走私网明文。**没有 Cloudflare**（历史文档曾这样写，已失效）。

### 已知问题：apex 301 降级到 HTTP

`https://vigilath.com/` 的第一跳 301 的 `Location` 是 **http://www.vigilath.com**（明文），本地 nginx 会再 301 把它升回 HTTPS，但浏览器地址栏会瞬闪一下 `http://`。

证据：

- apex DNS 指向 `3.33.251.168 / 15.197.225.128`（GoDaddy 在 AWS 上托管的转发服务）
- apex HTTPS 证书 issuer 是 **GoDaddy**（不是 ACM），与 ALB 上的 ACM 证书完全不同链路
- 301 响应头含 `server: ip-10-*.ec2.internal` + ALB 风格 `x-request-id`，明显是 GoDaddy 内部转发服务

修复位置：**GoDaddy 控制台** → My Products → Domains → vigilath.com → Forwarding → Target URL 从 `http://www.vigilath.com` 改成 `https://www.vigilath.com`。

（更彻底的做法：删掉 GoDaddy forwarding，让 apex DNS 直接 Alias 到 ALB，在 ALB 里配 listener rule 做 apex → www 的 HTTPS 301，这样转发行为由我们自己控制。）

### 后端 systemd 单元

`/etc/systemd/system/geo-checker.service` 关键字段：

```ini
[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/Dev/geo/backend
EnvironmentFile=/home/ubuntu/Dev/geo/backend/.env
ExecStart=/home/ubuntu/Dev/geo/backend/.venv/bin/python -m uvicorn geo.main:app --host 127.0.0.1 --port 8070
Restart=always
RestartSec=3
```

- 解释器与依赖都来自 `backend/.venv`
- 所有 secret（OpenAI/Anthropic/Stripe/MoltsPay/SMTP 等）通过 `backend/.env` 注入，不进 git
- 日志走 journald，查看：`sudo journalctl -u geo-checker.service -f`

### MoltsPayServer systemd 单元

`/etc/systemd/system/moltspay.service` 关键字段：

```ini
[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/Dev/geo/moltspay-server
ExecStart=/usr/bin/node index.mjs
Restart=always
RestartSec=3
Environment=NODE_ENV=production
```

- 基于 x402 协议处理 USDC 链上支付（Base 链）
- CDP 凭证位于 `~/.moltspay/.env`（CDP_API_KEY_ID / CDP_API_KEY_SECRET）
- 收款钱包：`0xb8d6f2441e8f8dfB6288A74Cf73804cDd0484E0C`
- 支付成功后回调 FastAPI `POST /api/payment/moltspay/fulfill`（仅限 localhost）
- 日志查看：`sudo journalctl -u moltspay.service -f`

### Nginx server 块要点

位于 `/etc/nginx/nginx.conf` 内联（**不在** `sites-enabled/`），主要规则：

```nginx
server {
    listen 80;
    server_name www.vigilath.com;

    root /var/www/html/www.vigilath.com;
    index index.html;

    # 安全头(HSTS / X-Frame-Options / Referrer-Policy 等)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    # ... 其他 headers 省略,详见实际 conf

    # 让 GEO / AI 发现类静态文件走真实文件,而不是 SPA 回退
    location = /robots.txt      { try_files $uri =404; default_type text/plain; }
    location = /sitemap.xml     { try_files $uri =404; default_type application/xml; }
    location = /llms.txt        { try_files $uri =404; default_type text/plain; }
    location = /llms-full.txt   { try_files $uri =404; default_type text/plain; }
    location = /humans.txt      { try_files $uri =404; default_type text/plain; }
    location ^~ /.well-known/   { try_files $uri =404; }

    # /assets/* 带 hash 命名,vite 构建每次出新名,可安全长缓存
    # 回头访问者直接从浏览器缓存读,不重新下载
    location ^~ /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable" always;
        try_files $uri =404;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

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

    # FastAPI 后端 API
    location /api/ {
        proxy_pass http://127.0.0.1:8070/api/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
```

改 nginx 后的操作：`sudo nginx -t && sudo systemctl reload nginx`。

### Nginx 性能配置(http 块,全站生效)

`/etc/nginx/nginx.conf` 顶层 `http { }` 里已开启 gzip(对文本/JS/CSS/SVG/JSON
等压缩 ~70%),中国移动网用户首屏受益明显:

```nginx
gzip on;
gzip_vary on;
gzip_proxied any;
gzip_comp_level 6;
gzip_min_length 1024;
gzip_http_version 1.1;
gzip_types
    text/plain text/css text/xml text/javascript
    application/javascript application/json
    application/xml application/xml+rss
    image/svg+xml;
```

不要改回默认值(默认只压 `text/html`,JS/CSS 会裸传输)。

## 二、系统依赖

- Ubuntu 22.04+
- Python 3.12（后端 venv 使用）
- Node.js 18+（前端构建 + MoltsPayServer）
- nginx
- systemd
- `sqlite3` CLI（用于 DB 备份）

## 三、发布流程（日常升级）

从 `develop` 分支拉取最新代码后按顺序执行：

### 1. 进入仓库并拉代码

```bash
cd /home/ubuntu/Dev/geo
git fetch origin
git status              # 确认工作树干净
git pull --ff-only origin develop
```

### 2. 备份数据库（强制）

```bash
cp backend/data/geo_checker.db backend/data/geo_checker.db.predeploy.$(date +%Y%m%d%H%M)
```

备份文件命名模式已在 `.gitignore` 里忽略。

### 3. 同步后端依赖

只在 `pyproject.toml` / `requirements.txt` 变化时需要：

```bash
cd backend
uv pip install -e .
cd ..
```

> 注意：后端 venv 使用 `uv` 管理依赖，不用 `pip`。

### 4. 跑数据库 migration（如有新文件）

所有 migration 都是**幂等**的，重跑无副作用：

```bash
cd backend
.venv/bin/python migrations/001_membership_v2.py
.venv/bin/python migrations/002_membership_currency.py
.venv/bin/python migrations/003_membership_plan_update.py
.venv/bin/python migrations/004_anonymous_check_usage.py
.venv/bin/python migrations/005_detection_records.py
.venv/bin/python migrations/006_moltspay_payment.py
cd ..
```

**只需跑新增的那个**。当前最新是 `006`。

### 5. 重启后端

```bash
sudo systemctl daemon-reload                 # 如 unit 文件被触碰过
sudo systemctl restart geo-checker.service
sudo systemctl status geo-checker.service    # 确认 active (running)
sudo journalctl -u geo-checker.service -n 50 # 快速扫一眼无报错
```

### 6. 重启 MoltsPayServer（如有变更）

```bash
cd moltspay-server
npm install                                  # 只在 package.json 变化时需要
cd ..
sudo systemctl restart moltspay.service
sudo systemctl status moltspay.service
```

### 7. 构建前端

```bash
cd frontend
npm ci                 # 只在 package-lock.json 变化时需要
npm run build          # 产物落在 frontend/dist/
cd ..
```

### 8. 发布前端到 webroot

```bash
sudo rsync -a --delete frontend/dist/ /var/www/html/www.vigilath.com/
sudo chown -R www-data:www-data /var/www/html/www.vigilath.com/
```

### 9. 冒烟测试

```bash
curl -sI https://www.vigilath.com/                      # 200 + HSTS
curl -s  https://www.vigilath.com/robots.txt | head     # 文本,非 index.html
curl -s  https://www.vigilath.com/sitemap.xml | head    # XML
curl -s  https://www.vigilath.com/pay/health | head     # MoltsPayServer 健康
```

同时在浏览器：
- 首页打开，检查中/英切换正常
- 跑一次检测
- 登录态跑一次完整 check
- 进入 Checkout 页面确认 Stripe / USDC 两种支付方式可见

### 10. 上游缓存

**当前未接入任何 CDN**(没有 Cloudflare / CloudFront)。ALB 本身不缓存应用响应,所有请求穿透到 EC2 nginx。缓存策略完全由 nginx 的 `Cache-Control` 头和浏览器自身控制:

- `/assets/*` 文件名带 hash(如 `index-5o6adLEE.js`),vite 每次构建出新名,
  与上一版自动错开;配合 nginx 的 `Cache-Control: public, immutable, max-age=1y`,
  旧资源继续被老客户端缓存,新资源靠新 HTML 的新引用自动拉取。
- `index.html`、`robots.txt`、`sitemap.xml`、`llms.txt` 等非 hash 文件:浏览器按默认策略缓存,发布后通常几分钟内刷新生效;不必额外操作。

如果确实需要强制验证本机改动:浏览器 `Shift+F5` / 无痕窗口 / `curl -H 'Cache-Control: no-cache'`。未来接入 Cloudflare 或 CloudFront 后再补 Purge 章节。

## 四、回滚

### 后端回滚

```bash
cd /home/ubuntu/Dev/geo
git log --oneline -10          # 找到上一个稳定 commit
git reset --hard <sha>
# 恢复 DB(必要时)
cp backend/data/geo_checker.db.predeploy.<timestamp> backend/data/geo_checker.db
sudo systemctl restart geo-checker.service
```

### MoltsPayServer 回滚

```bash
cd /home/ubuntu/Dev/geo
git log --oneline -10
git reset --hard <sha>
cd moltspay-server && npm install
sudo systemctl restart moltspay.service
```

### 前端回滚

前端没有单独的版本仓。最快的办法是把 `git reset --hard` 后的代码重新
`npm run build` + rsync 一次。

## 五、数据库 migration 历史

| 编号 | 作用 | 是否破坏性 |
|---|---|---|
| 001 | 会员体系 v2（5 档统一阶梯） | **重新 seed `memberships`/`user_memberships`**。生产跑之前必须备份。 |
| 002 | `memberships` 表新增 `currency` 字段 | 幂等 ALTER |
| 003 | 会员方案更新 | 幂等 |
| 004 | 新增 `anonymous_check_usage` 表 | 幂等 |
| 005 | 新增 `detection_records` 表 | 幂等 |
| 006 | `payment_sessions` 新增 MoltsPay 字段（provider/chain/tx_hash/wallet_address） | 幂等 ALTER |

### 主要 API 端点

| 端点 | 鉴权 | 行为 |
|---|---|---|
| `POST /api/check/anonymous` | 无 | 只跑 5 项免费检测 |
| `POST /api/check` | Bearer（可选） | 登录用户按档位跑对应类别并记录配额 |
| `GET /api/users/me/usage` | Bearer | `{quota, used, remaining, year_month}` |
| `POST /api/contact-sales` | 无 | 人工服务咨询表单落库到 `sales_leads` |
| `POST /api/payment/stripe/create-checkout-session` | Bearer | 创建 Stripe Checkout Session |
| `POST /api/payment/stripe/webhook` | Stripe Sig | Stripe 支付回调 |
| `GET /api/payment/stripe/session/{id}` | Bearer | 安全网：轮询 Stripe session 状态 |
| `POST /api/payment/moltspay/create` | Bearer | 创建 USDC 支付订单 |
| `GET /api/payment/moltspay/status/{id}` | Bearer | 轮询 USDC 支付状态 |
| `POST /api/payment/moltspay/fulfill` | 仅 localhost | MoltsPayServer 回调激活会员 |
| `GET /api/account/payments` | Bearer | 支付记录列表 |
| `GET /api/account/detections` | Bearer | 检测记录列表 |

## 六、支付系统

### Stripe（信用卡）

- Checkout Session 模式，用户跳转 Stripe 托管页面
- Webhook (`/api/payment/stripe/webhook`) + 安全网轮询双保险
- 配置项在 `backend/.env`：`STRIPE_SECRET_KEY`、`STRIPE_PUBLISHABLE_KEY`、`STRIPE_WEBHOOK_SECRET`
- Webhook endpoint 在 Stripe Dashboard: `https://www.vigilath.com/api/payment/stripe/webhook`
- 支付成功自动发送收据邮件（`receipt_email`）
- 防重扣款：复用未过期的 pending session

### MoltsPay（USDC / Base 链）

- 基于 x402 协议，MoltsPayServer 处理签名验证和链上结算
- 用户免 gas（CDP facilitator 代付）
- 收款钱包：`0xb8d6f2441e8f8dfB6288A74Cf73804cDd0484E0C`
- CDP 凭证位于 `~/.moltspay/.env`
- 服务清单：`moltspay-server/moltspay.services.json`（3 个会员方案）
- 支付成功后 MoltsPayServer 回调 FastAPI `/fulfill` 激活会员
- 防重提交：同一用户同一方案复用 30 分钟内的 pending 订单

### 续费逻辑

- 续费同一方案：`end_date = 旧到期日 + 30天`（未用完天数保留）
- 升级不同方案或已过期：`end_date = 今天 + 30天`

## 七、开发流程（本地）

### 前端

```bash
cd frontend
npm install
npm run dev             # Vite dev server
npm run build           # 生产构建
```

`VITE_API_URL` 默认指向 `http://localhost:8070/api`，可以在 `.env.local` 覆盖。

### 后端

```bash
cd backend
python3.12 -m venv .venv
uv pip install -e .
cp .env.example .env    # 填写本地 key
.venv/bin/python -m uvicorn geo.main:app --reload --port 8070
```

### MoltsPayServer

```bash
cd moltspay-server
npm install
node index.mjs          # 本地启动，监听 :3010
```

## 八、常见故障排除

### nginx 502 / 上游无响应

```bash
sudo systemctl status geo-checker.service
sudo journalctl -u geo-checker.service -n 200 --no-pager
ss -ltnp | grep 8070          # 确认 uvicorn 在监听
```

### MoltsPayServer 不可用

```bash
sudo systemctl status moltspay.service
sudo journalctl -u moltspay.service -n 100 --no-pager
ss -ltnp | grep 3010          # 确认 node 在监听
curl -s http://localhost:3010/health
```

### 前端加载老版本

- 浏览器:Shift+F5 强刷 / 无痕窗口验证
- 确认 `/var/www/html/www.vigilath.com/assets/` 下带 hash 的文件名和 dist 一致
- 若已接入 CDN(目前没有):按该 CDN 的 Purge 操作刷新

### DB schema 不一致 / ORM 报错

```bash
# 检查实际表结构
sqlite3 backend/data/geo_checker.db ".schema <table_name>"
# 从 predeploy 备份恢复
cp backend/data/geo_checker.db.predeploy.<ts> backend/data/geo_checker.db
sudo systemctl restart geo-checker.service
```

### Stripe 支付未激活会员

1. 检查 `STRIPE_WEBHOOK_SECRET` 是否配置
2. 查看 Stripe Dashboard webhook 日志
3. 检查 `payment_sessions` 表对应记录状态
4. 安全网轮询是否触发（success 页面 → `/api/payment/stripe/session/{id}`）

### USDC 支付未激活会员

1. 检查 MoltsPayServer 日志：`sudo journalctl -u moltspay.service -n 100`
2. 检查 `/api/payment/moltspay/fulfill` 是否被调用
3. 确认 `payment_sessions` 中对应记录的 `provider=moltspay` 和 `status`

## 九、机器上的辅助文件位置

| 内容 | 路径 |
|---|---|
| 代码仓库 | `/home/ubuntu/Dev/geo/` |
| 后端 venv | `/home/ubuntu/Dev/geo/backend/.venv/` |
| 后端 .env | `/home/ubuntu/Dev/geo/backend/.env`（不进 git） |
| 生产 DB | `/home/ubuntu/Dev/geo/backend/data/geo_checker.db` |
| MoltsPayServer | `/home/ubuntu/Dev/geo/moltspay-server/` |
| MoltsPay CDP 凭证 | `~/.moltspay/.env`（不进 git） |
| 前端源码 | `/home/ubuntu/Dev/geo/frontend/` |
| 前端构建产物 | `/home/ubuntu/Dev/geo/frontend/dist/` |
| 前端 webroot | `/var/www/html/www.vigilath.com/` |
| systemd 单元（后端） | `/etc/systemd/system/geo-checker.service` |
| systemd 单元（MoltsPay） | `/etc/systemd/system/moltspay.service` |
| nginx 主配置 | `/etc/nginx/nginx.conf`（vigilath server 块内联其中） |

## 十、联系支持

- 技术支持邮箱：support@zen7.com
- X (Twitter)：https://x.com/zen7_labs
- Discord：https://discord.gg/mJVEVXyxD5
