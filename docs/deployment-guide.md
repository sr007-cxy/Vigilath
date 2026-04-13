# GEO Readiness Checker - 部署与维护指南

## 系统要求

- Docker 和 Docker Compose
- Node.js 18+（用于前端开发）
- Python 3.9+（用于后端开发）

## 部署方法

### 使用 Docker Compose 部署

1. **克隆代码库**

   ```bash
   git clone https://github.com/yourusername/geo-checker-website.git
   cd geo-checker-website
   ```

2. **构建并运行容器**

   ```bash
   docker-compose up --build -d
   ```

3. **访问网站**

   打开浏览器并访问 `http://localhost:3000`。

### 环境变量

#### 前端环境变量

- `VITE_API_URL`：后端 API 的 URL，默认为 `http://localhost:8000/api`

#### 后端环境变量

- `PYTHONUNBUFFERED`：设置为 1 以确保日志正确输出

## 数据库迁移

### 001 — 会员体系 v2（5 档统一阶梯）

本迁移把旧的 4 档 SaaS 会员（¥0 / ¥99 / ¥299 / ¥999）重构为 5 档统一阶梯（免费会员 / 检测会员 / Starter / Growth / Scale），并为检测分级、配额、人工服务咨询等能力建立表结构。

**影响**：

- `memberships` 表新增 5 列：`slug`、`tier_type`、`monthly_check_quota`、`allowed_check_categories`、`features_json`、`display_order`
- 新建 `user_check_usage` 表（按用户 + 年月追踪每月检测次数）
- 新建 `sales_leads` 表（人工服务咨询表单落库）
- **清空** `memberships` 和 `user_memberships` 表并重新种子化 5 档（开发环境行为；生产环境执行前请确认数据影响）

**运行**：

```bash
cd backend
python -m migrations.001_membership_v2
```

脚本是幂等的：`ALTER TABLE ADD COLUMN` 会跳过已存在的列，`CREATE TABLE` 会跳过已存在的表。**但重新种子 `memberships` 的步骤会重置**——生产环境上线前建议先 `sqlite3 geo_checker.db .dump > backup.sql` 再执行。

**新增的 API 端点**：

| 端点 | 鉴权 | 行为 |
|---|---|---|
| `POST /api/check/anonymous` | 无 | 只跑 5 项免费检测，返回 `tier='free'`，本期无限流 |
| `POST /api/check` | Bearer（可选） | 登录用户按档位跑对应类别并记录配额；无 token 等同于 anonymous |
| `GET /api/users/me/usage` | Bearer | 返回 `{quota, used, remaining, year_month}` |
| `POST /api/contact-sales` | 无 | 提交人工服务咨询表单，写入 `sales_leads` 表 |
| `POST /api/subscribe` | Bearer | **stub**：返回 pending 状态，后续接入真实支付 provider 时替换 |

旧 `POST /api/geo` 作为 `/api/check/anonymous` 的别名保留（向下兼容），行为改为只跑 5 项。

## 开发流程

### 前端开发

1. **进入前端目录**

   ```bash
   cd frontend
   ```

2. **安装依赖**

   ```bash
   npm install
   ```

3. **启动开发服务器**

   ```bash
   npm run dev
   ```

4. **构建生产版本**

   ```bash
   npm run build
   ```

### 后端开发

1. **进入后端目录**

   ```bash
   cd backend
   ```

2. **安装依赖**

   ```bash
   poetry install
   ```

3. **启动开发服务器**

   ```bash
   poetry run uvicorn app.main:app --reload
   ```

## 维护指南

### 日志管理

- **前端日志**：通过 Docker 日志查看
  ```bash
  docker-compose logs frontend
  ```

- **后端日志**：通过 Docker 日志查看
  ```bash
  docker-compose logs backend
  ```

### 性能监控

- 监控容器资源使用情况
  ```bash
  docker stats
  ```

- 监控 API 响应时间和错误率

### 安全维护

- 定期更新依赖包以修复安全漏洞
- 监控异常访问模式
- 确保 HTTPS 配置正确

### 备份策略

- 定期备份代码库
- 备份配置文件

## 故障排除

### 常见问题

1. **前端无法连接到后端**

   - 检查 `VITE_API_URL` 环境变量是否正确设置
   - 确保后端服务正在运行
   - 检查网络连接和防火墙设置

2. **测试超时**

   - 检查目标网站是否可访问
   - 增加后端的超时设置
   - 优化测试执行速度

3. **部署失败**

   - 检查 Docker 守护进程是否运行
   - 检查端口是否被占用
   - 查看 Docker 构建日志以获取详细错误信息

### 错误处理

- 前端错误：在浏览器控制台查看错误信息
- 后端错误：查看后端日志
- Docker 错误：查看 Docker 构建和运行日志

## 升级指南

1. **拉取最新代码**

   ```bash
   git pull
   ```

2. **重新构建和运行容器**

   ```bash
   docker-compose down
   docker-compose up --build -d
   ```

3. **验证升级**

   - 访问网站并运行测试
   - 检查所有功能是否正常

## 扩展建议

- **添加更多检查项**：扩展 GEO Readiness Checker 脚本以包含更多 GEO 相关检查
- **添加用户认证**：允许用户创建账户并保存测试历史
- **添加批量测试**：允许用户一次测试多个网站
- **添加 API 接口**：为其他系统提供 GEO 测试 API
- **添加定期测试**：定期自动测试网站并发送报告

## 联系支持

如果您遇到任何部署或维护问题，请联系技术支持团队。
