# GEO Readiness Checker —— 部署与维护指南

> 本文档描述 **www.vigilath.com** 当前线上的真实部署方式，面向运维发布流程。
> 开发环境请参考文末「开发流程」章节。

## 一、线上架构

线上一台 EC2（Ubuntu）承载整个站点，按三层组织：

| 层 | 组件 | 位置 / 端口 |
|---|---|---|
| 入口 | nginx | `/etc/nginx/nginx.conf` 内 `server_name www.vigilath.com` 块，listen 80 |
| 后端 | FastAPI + uvicorn | systemd 服务 `geo-checker.service`，监听 `127.0.0.1:8070` |
| 前端 | Vite 构建的 SPA 静态产物 | `/var/www/html/www.vigilath.com/`（属主 `www-data:www-data`） |

HTTPS 在上游（Cloudflare）终止，这台机器的 nginx 只服务明文 80 端口。

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
- 所有 secret（OpenAI/Anthropic/Stripe/SMTP 等）通过 `backend/.env` 注入，不进 git
- 日志走 journald，查看：`sudo journalctl -u geo-checker.service -f`

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

    location / {
        try_files $uri $uri/ /index.html;
    }

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

## 二、系统依赖

- Ubuntu 22.04+
- Python 3.12（后端 venv 使用）
- Node.js 18+（前端构建）
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
cp backend/geo_checker.db backend/geo_checker.db.predeploy.$(date +%Y%m%d%H%M)
# 或用 sqlite3 dump:
# sqlite3 backend/geo_checker.db .dump > backend/geo_checker.db.predeploy.$(date +%Y%m%d%H%M).sql
```

备份文件命名模式已在 `.gitignore` 里忽略。

### 3. 同步后端依赖

只在 `pyproject.toml` / `requirements.txt` 变化时需要：

```bash
cd backend
.venv/bin/pip install -e .
# 如果上游加了新依赖,根据实际 pyproject 配置跑 pip/poetry
cd ..
```

脚本是幂等的：`ALTER TABLE ADD COLUMN` 会跳过已存在的列，`CREATE TABLE` 会跳过已存在的表。**但重新种子 `memberships` 的步骤会重置**——生产环境上线前建议先 `sqlite3 data/geo_checker.db .dump > backup.sql` 再执行。
### 4. 跑数据库 migration（如有新文件）

所有 migration 都是**幂等**的，重跑无副作用：

```bash
cd backend
.venv/bin/python migrations/001_membership_v2.py         # 5 档会员体系
.venv/bin/python migrations/002_membership_currency.py   # 会员币种字段
.venv/bin/python migrations/003_membership_plan_update.py
.venv/bin/python migrations/004_anonymous_check_usage.py # 匿名检测限流表
cd ..
```

**只需跑新增的那个**。当前最新是 `004`。

> 历史提醒：生产上曾出现过 `backend/geo_checker.db.broken.*` 这类文件，
> 是某次部署过程中 DB 状态异常的残留。跑 migration 前务必完成步骤 2 的备份。

### 5. 重启后端

```bash
sudo systemctl daemon-reload                 # 如 unit 文件被触碰过
sudo systemctl restart geo-checker.service
sudo systemctl status geo-checker.service    # 确认 active (running)
sudo journalctl -u geo-checker.service -n 50 # 快速扫一眼无报错
```

### 6. 构建前端

```bash
cd frontend
npm ci                 # 只在 package-lock.json 变化时需要
npm run build          # 产物落在 frontend/dist/
cd ..
```

### 7. 发布前端到 webroot

```bash
sudo rsync -a --delete frontend/dist/ /var/www/html/www.vigilath.com/
sudo chown -R www-data:www-data /var/www/html/www.vigilath.com/
```

`--delete` 会清掉 webroot 里不在新 dist 的旧文件，避免陈旧资源污染。如果
某些文件（比如早期手工放入的 `favicon`、`icons.svg`）本来就不来自 build，
需要保留的话不要加 `--delete`，或用 `--exclude` 保留。

### 8. 冒烟测试

```bash
curl -sI https://www.vigilath.com/                      # 200 + HSTS
curl -s  https://www.vigilath.com/robots.txt | head     # 文本,非 index.html
curl -s  https://www.vigilath.com/sitemap.xml | head    # XML
curl -sI https://www.vigilath.com/api/health 2>/dev/null || true  # 视实际健康端点而定
```

同时在浏览器：
- 首页打开，检查中/英切换正常
- 跑一次匿名 check（`/api/check/anonymous` 路径）
- 登录态跑一次完整 check

### 9. 上游缓存

如果 Cloudflare 启用了缓存策略，发布后需要手动 Purge 相应路径（尤其是
`/`、`/assets/*`、`/robots.txt`、`/sitemap.xml`、`/llms.txt`、`/llms-full.txt`、
`/humans.txt`）。

## 四、回滚

### 后端回滚

```bash
cd /home/ubuntu/Dev/geo
git log --oneline -10          # 找到上一个稳定 commit
git reset --hard <sha>
# 恢复 DB(必要时)
cp backend/geo_checker.db.predeploy.<timestamp> backend/geo_checker.db
sudo systemctl restart geo-checker.service
```

### 前端回滚

前端没有单独的版本仓。最快的办法是把 `git reset --hard` 后的代码重新
`npm run build` + rsync 一次。如果紧急，可以从之前的 `frontend/dist`
备份目录（手动 tar 过的话）恢复。建议发布前把旧 dist 打 tar：

```bash
sudo tar -czf /var/www/html/www.vigilath.com.$(date +%Y%m%d%H%M).tar.gz \
    -C /var/www/html www.vigilath.com
```

## 五、数据库 migration 历史

| 编号 | 作用 | 是否破坏性 |
|---|---|---|
| 001 | 会员体系 v2（5 档统一阶梯） | **重新 seed `memberships`/`user_memberships`**。生产跑之前必须备份。 |
| 002 | `memberships` 表新增 `currency` 字段，Stripe 支持按档位收币 | 幂等 ALTER |
| 003 | 会员方案更新 | 幂等 |
| 004 | 新增 `anonymous_check_usage` 表，匿名用户按 cookie 月度限流 3 次 | 幂等；还会把旧的 `ip_address` 列重命名为 `client_id` |

新增 API 端点（由 001 引入）：

| 端点 | 鉴权 | 行为 |
|---|---|---|
| `POST /api/check/anonymous` | 无 | 只跑 5 项免费检测 |
| `POST /api/check` | Bearer（可选） | 登录用户按档位跑对应类别并记录配额 |
| `GET /api/users/me/usage` | Bearer | `{quota, used, remaining, year_month}` |
| `POST /api/contact-sales` | 无 | 人工服务咨询表单落库到 `sales_leads` |
| `POST /api/subscribe` | Bearer | stub，后续接真实支付 |

## 六、开发流程（本地）

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
.venv/bin/pip install -e .
cp .env.example .env    # 填写本地 key
.venv/bin/python -m uvicorn geo.main:app --reload --port 8070
```

## 七、常见故障排除

### nginx 502 / 上游无响应

```bash
sudo systemctl status geo-checker.service
sudo journalctl -u geo-checker.service -n 200 --no-pager
ss -ltnp | grep 8070          # 确认 uvicorn 在监听
```

### 前端加载老版本

- Cloudflare 缓存：Purge Everything 或按路径 Purge
- 浏览器：Shift+F5 强刷
- 确认 `/var/www/html/www.vigilath.com/assets/` 下带 hash 的文件名和 dist 一致

### DB schema 不一致 / ORM 报错

```bash
# 检查实际表结构
sqlite3 backend/geo_checker.db ".schema <table_name>"
# 从 predeploy 备份恢复
cp backend/geo_checker.db.predeploy.<ts> backend/geo_checker.db
sudo systemctl restart geo-checker.service
```

### 本次部署过程中 DB 损坏

保留 `backend/geo_checker.db.broken.<ts>` 便于事后排查，然后从最新
`predeploy.<ts>` 恢复。

## 八、机器上的辅助文件位置

| 内容 | 路径 |
|---|---|
| 代码仓库 | `/home/ubuntu/Dev/geo/` |
| 后端 venv | `/home/ubuntu/Dev/geo/backend/.venv/` |
| 后端 .env | `/home/ubuntu/Dev/geo/backend/.env`（不进 git） |
| 生产 DB | `/home/ubuntu/Dev/geo/backend/geo_checker.db` |
| 前端源码 | `/home/ubuntu/Dev/geo/frontend/` |
| 前端构建产物 | `/home/ubuntu/Dev/geo/frontend/dist/` |
| 前端 webroot | `/var/www/html/www.vigilath.com/` |
| systemd 单元 | `/etc/systemd/system/geo-checker.service` |
| nginx 主配置 | `/etc/nginx/nginx.conf`（vigilath server 块内联其中） |

## 九、联系支持

部署或维护问题 → 找运维对接人。生产变更前建议在 #ops 频道同步一下。
