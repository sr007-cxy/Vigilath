# Cloudflare CDN 接入方案

> 目标:把 `www.vigilath.com` 和 `vigilath.com` 接入 Cloudflare,拿到全球 edge
> 缓存、Brotli、隐藏 origin IP、修复 apex HTTPS→HTTP 降级 bug。面向执行者,
> 分阶段、可回滚。

## 一、现状与目标

### 当前架构(未接 CF)

```
用户  ─→  www.vigilath.com  (DNS → AWS ALB, us-east-1)
              ↓  TLS 终止(ACM 证书)
           AWS ALB
              ↓  私网明文
           EC2 nginx:80  ─→  FastAPI / 静态 SPA / MoltsPayServer

用户  ─→  vigilath.com  (DNS → GoDaddy Forwarding IP)
              ↓  TLS 终止(GoDaddy 证书)
           GoDaddy 转发服务  ─→  301 到 http://www.vigilath.com  ← ❌ HTTP 硬编码
```

**痛点:**

- 所有静态资源都从 EC2 us-east-1 回源,中国/亚太用户首屏慢
- 没有 Brotli(之前评估过装 `libnginx-mod-http-brotli` 改 nginx)
- apex 域名 301 把 HTTPS 降级为 HTTP,根因是 GoDaddy Forwarding 的 target 硬编码 `http://`,**我们无法在 AWS 侧修**
- AWS ALB IP 公开,一旦被探测到存在被绕过 CF 直攻的可能(目前没 CF,还没这个风险,但接 CF 后如果不限 IP,就是漏洞)

### 目标架构

```
用户  ─→  CF Edge(250+ 城市)
              ↓  TLS 终止(CF 证书)
              ↓  缓存 /assets/* · 动态 /api/* 回源
           AWS ALB(SG 限 CF IP 段)
              ↓  私网明文
           EC2 nginx:80  ─→  FastAPI / 静态 SPA / MoltsPayServer

apex vigilath.com  ─→  CF Edge  ─→  CF Redirect Rule  ─→  301 → https://www.vigilath.com
```

**收益:**

| 项 | 收益 |
|---|---|
| 静态资源命中 edge | 亚太用户首屏 TTFB 300-500ms → 50ms 以内 |
| 自动 Brotli 压缩 | 比 gzip 再省约 15%,不用改 nginx |
| 隐藏 origin IP | ALB SG 限 CF IP 后,直连 origin 被挡 |
| 免费 HTTP/3 + TLS 1.3 | 延迟进一步降低 |
| 基础 DDoS 防护 | Free plan 就含 L3-L7 基础保护 |
| apex 降级 bug 一并修 | 用 CF Redirect Rule 替代 GoDaddy Forwarding |

## 二、前置条件

### 决策清单

在开始前确认以下 3 件事:

1. **Cloudflare 账号**:用哪个邮箱注册?推荐用团队共享邮箱,避免某人离职后 lock out
2. **Plan 级别**:**Free plan 足够**(流量、规则数、缓存大小都够)。Pro($25/月)主要多 Image Resizing + WAF 预设规则,Business($250/月)主要是 Bypass Cache on Cookie + 优先支持。**本方案全按 Free 写**,不够用再升
3. **访问权限**:
   - GoDaddy 账号(改 NS)
   - AWS 控制台(改 ALB Security Group)—— 仅 Phase 4 加固需要,前面 3 阶段不用

### 现网基础信息(提前记下)

```
域名:                  vigilath.com
当前 DNS 托管:          GoDaddy
当前 AWS ALB 主机名:    vigilath-alb-01-654513483.us-east-1.elb.amazonaws.com
当前 ACM 证书 SAN:      www.vigilath.com, vigilath.com(apex 已覆盖,接 CF 后不用重签)
apex 当前 A 记录:       3.33.251.168, 15.197.225.128(GoDaddy Forwarding)
www 当前 DNS:           CNAME → ALB hostname
EC2 nginx server_name:  www.vigilath.com
```

### 时间预算

- Phase 0:30 分钟(CF 账号 + 登记域)
- Phase 1:10 分钟改 NS + **24-48 小时传播窗口**(期间无感知)
- Phase 2:1 小时(开 proxy + Page Rules + 冒烟)
- Phase 3:30 分钟(apex redirect 配置 + GoDaddy 删 Forwarding)
- Phase 4:1 小时(加固,可选)

**总体建议跨 2 天做**:Day 1 做 0 + 1,Day 2 做 2 + 3 + 4。

## 三、Phase 0 — CF 账号与域名登记

**目标**:在 CF 准备好所有配置,但**还不生效**。零线上影响。

### 步骤

1. 打开 https://dash.cloudflare.com/sign-up,用决定好的邮箱注册
2. 控制台点 **Add a Site**,输入 `vigilath.com`,选 **Free** plan
3. CF 会扫描现有 DNS,列出它发现的记录。**这一步只是读 GoDaddy,不改任何东西**
4. CF 给你两个 nameserver,形如:

   ```
   alice.ns.cloudflare.com
   bob.ns.cloudflare.com
   ```

   **记下这两个值,Phase 1 要用。**

5. 在 CF 的 DNS 面板里,核对它抄过来的记录:

   | Type | Name | Value | Proxy |
   |---|---|---|---|
   | CNAME | `www` | `vigilath-alb-01-654513483.us-east-1.elb.amazonaws.com` | **灰云(DNS only)** |
   | A | `@` (或 `vigilath.com`) | `3.33.251.168` | **灰云** |
   | A | `@` | `15.197.225.128` | **灰云** |

   **关键:全部保持灰云(DNS only),不要点成黄云(proxied)**。Phase 2 才切 proxy。

6. 如果有其他记录(MX / TXT / _dmarc 等),核对无误,保留

7. 在 GoDaddy DNS 面板里,把 `vigilath.com` 及其子域的 TTL 全部**降到 60 秒**。这是为了让 Phase 1 的 NS 变更传播更快。等 Phase 4 收尾后再恢复默认(3600)

### 验收

- CF 控制台显示 "Pending Nameserver Update" 状态
- 域名当前还是走 GoDaddy,线上**零变化**
- `dig vigilath.com NS` 仍返回 GoDaddy 的 NS

## 四、Phase 1 — NS 迁移

**目标**:把域名的权威 NS 从 GoDaddy 换到 CF。DNS 解析结果和 Phase 0 时抄的一模一样,所以**用户无感**。

### 步骤

1. 登录 GoDaddy → My Products → Domains → `vigilath.com` → **Nameservers** → Change
2. 选 "Enter my own nameservers",填入 Phase 0 CF 给你的两个 NS:

   ```
   alice.ns.cloudflare.com
   bob.ns.cloudflare.com
   ```

3. Save。GoDaddy 会提示 "changes may take 24-48 hours to propagate",确认

### 验证(每半小时查一次,直到全部 NS 都变)

```bash
# 不同递归 DNS 看到的 NS 应该都变成 CF 的
dig @8.8.8.8 vigilath.com NS +short          # Google
dig @1.1.1.1 vigilath.com NS +short          # Cloudflare
dig @223.5.5.5 vigilath.com NS +short        # 阿里 DNS(中国视角)
dig @119.29.29.29 vigilath.com NS +short     # 腾讯 DNS
```

全部返回 `alice.ns.cloudflare.com` + `bob.ns.cloudflare.com` 后,继续 Phase 2。

CF 控制台会在 NS 切换生效后自动把域名状态变为 **Active**,右上角出现小绿勾。

### 线上冒烟(应当全绿,因为只是换 NS)

```bash
curl -sI https://www.vigilath.com/            # 200
curl -sI https://www.vigilath.com/api/check/anonymous -X POST \
  -H 'Content-Type: application/json' -d '{"url":"https://moltspay.com"}'
curl -sI https://vigilath.com/                # 301 到 http://... (apex bug 仍在,Phase 3 修)
```

### 回滚

在 GoDaddy 把 NS 改回原 GoDaddy 自己的 NS。传播同样需要 24-48h。

## 五、Phase 2 — www 开 CF Proxy + 缓存规则

**目标**:把 `www.vigilath.com` 的流量真正走 CF 代理。静态资源开始在 edge 被缓存。

### 5.1 TLS 模式:Full (strict)

**必须第一步做**,否则 CF 可能用弱加密回源。

1. CF 控制台 → SSL/TLS → Overview
2. 选 **Full (strict)** —— CF 到 origin 强制 HTTPS + 证书有效性校验
3. 我们 AWS ALB 上装的是 ACM 证书,SAN 含 `www.vigilath.com` + `vigilath.com`,都在有效期内,**不会踩坑**

> ❌ 不要选 Flexible(CF 到 origin 是明文)
> ❌ 不要选 Full(不校验 origin 证书,中间人可以伪造)
> ✅ Full (strict)

### 5.2 Page Rules(免费版最多 3 条)

CF 控制台 → Rules → Page Rules → Create Page Rule。

**注意顺序:Page Rules 是从上往下匹配,第一条匹配就停**。下面的顺序很重要。

| 顺序 | URL 匹配 | 设置 | 目的 |
|---|---|---|---|
| 1 | `www.vigilath.com/api/*` | Cache Level: Bypass | 后端 API **绝不**缓存,否则用户看到别人的检测结果 |
| 2 | `www.vigilath.com/pay/*` | Cache Level: Bypass | MoltsPay 支付路径不能缓存 |
| 3 | `www.vigilath.com/assets/*` | Cache Level: Cache Everything;Edge Cache TTL: 1 month;Browser Cache TTL: 1 month | vite 带 hash 命名,每次构建换名,可以放心长缓存 |

> `/index.html` 和 `/*.html` 默认走 CF 的 "Standard" cache,走 CF 自己对 `Cache-Control` 的解析。nginx 没给 HTML 设 Cache-Control,CF 默认很短 TTL,发布后几分钟内就刷新。

免费版只有 3 条 Page Rule,上面正好用完。以后需要更细粒度(比如特定路径禁用 CF 的 Rocket Loader),要升 Pro 或用 Cache Rules(新功能,免费版有少量配额)。

### 5.3 切 www 为 proxied(关键一步)

1. CF 控制台 → DNS → 找 `www` 的那条 CNAME
2. 点旁边的**云朵图标**,从**灰色(DNS only)变成橙色(Proxied)**
3. 保存后**几秒内生效**

### 5.4 冒烟(浏览器 + curl 双验证)

```bash
# 1. 首页 200,响应头应出现 CF 标志
curl -sI https://www.vigilath.com/ | grep -iE '^(HTTP|server|cf-|x-)'
# 期望:看到 cf-ray, cf-cache-status, server: cloudflare

# 2. 静态资源命中缓存(第二次访问应当返回 HIT)
curl -sI https://www.vigilath.com/assets/index-XXXXX.css | grep -i cf-cache
curl -sI https://www.vigilath.com/assets/index-XXXXX.css | grep -i cf-cache
# 第一次可能 MISS,第二次 HIT

# 3. API 不缓存,每次 DYNAMIC
curl -sI -X POST https://www.vigilath.com/api/check/anonymous \
  -H 'Content-Type: application/json' -d '{"url":"https://example.com"}' \
  | grep -i cf-cache
# 期望:cf-cache-status: DYNAMIC 或 BYPASS

# 4. MoltsPay 健康
curl -sI https://www.vigilath.com/pay/health | grep -i cf-cache
# 期望:DYNAMIC / BYPASS
```

浏览器端:

- ✅ 打开首页正常,切语言正常
- ✅ 输入 URL 跑一次检测,能出结果
- ✅ 登录、Checkout、USDC 支付流程完整跑一次
- ✅ DevTools Network 面板里,静态资源响应头含 `cf-cache-status: HIT`

### 5.5 回滚(出任何问题立刻做)

- CF → DNS → `www` 那条 CNAME → 云朵**改回灰色**(DNS only)
- 秒级生效,CF 退出流量路径,行为回到 Phase 1 末尾状态

## 六、Phase 3 — apex 域名修 301 降级 bug

**目标**:`https://vigilath.com/` 直接 301 到 `https://www.vigilath.com/`,不再降级到 HTTP,也不再依赖 GoDaddy Forwarding。

### 6.1 改 apex 的 DNS 记录

1. CF 控制台 → DNS → 找 `@` (apex) 的两条 A 记录(`3.33.251.168` / `15.197.225.128`,GoDaddy Forwarding IP)
2. **删除**这两条
3. 新增一条 **CNAME**:
   - Type: CNAME
   - Name: `@`(apex)
   - Target: `vigilath-alb-01-654513483.us-east-1.elb.amazonaws.com`(就是 www 指的那个 ALB)
   - Proxy: **橙色(Proxied)**

   > CF 支持 apex CNAME(用 CNAME Flattening 绕开 DNS 标准限制),GoDaddy 和大部分传统 DNS 不支持。这是 CF 的一个杀手锏。

### 6.2 加 Redirect Rule(代替 GoDaddy Forwarding)

CF 控制台 → Rules → Redirect Rules → Create Rule。

```
Rule name:     apex to www
If incoming requests match:
  Field:       Hostname
  Operator:    equals
  Value:       vigilath.com

Then:
  Type:                Dynamic
  Expression:          concat("https://www.vigilath.com", http.request.uri.path)
  Status code:         301
  Preserve query string: ✅
```

> 注意 target 协议**强制 `https://`**,这是整个 Phase 3 的核心目的。

### 6.3 验证

```bash
curl -sI https://vigilath.com/
# 期望:HTTP/2 301, location: https://www.vigilath.com/

curl -sI -L https://vigilath.com/some/path?q=1
# 期望:最终 200,url_effective 应为 https://www.vigilath.com/some/path?q=1
#       且过程中无 http:// 跳转
```

### 6.4 删除 GoDaddy Forwarding

一旦 Phase 3 生效,GoDaddy 的 Forwarding 配置就**不再被任何请求命中**(DNS 已不指向它)。但仍建议主动删掉,避免未来误触:

1. GoDaddy → My Products → Domains → vigilath.com → **Forwarding**
2. 删除 Domain Forwarding 配置

### 6.5 回滚

CF → Redirect Rules → 删 `apex to www` 规则
CF → DNS → apex 记录改回之前的 A 记录(灰云),保留一份就好。
GoDaddy Forwarding 如果已删,可以重新配置。

## 七、Phase 4 — 加固(可选,但推荐)

### 7.1 限制 ALB 安全组只放 CF IP

**为什么**:接了 CF proxy 后,用户请求只应该从 CF IP 过来。如果 ALB 接受任意 IP,攻击者探到 origin IP 后可以直连绕过 CF。

**CF IP 段官方地址**:https://www.cloudflare.com/ips/

```
# IPv4
173.245.48.0/20
103.21.244.0/22
103.22.200.0/22
103.31.4.0/22
141.101.64.0/18
108.162.192.0/18
190.93.240.0/20
188.114.96.0/20
197.234.240.0/22
198.41.128.0/17
162.158.0.0/15
104.16.0.0/13
104.24.0.0/14
172.64.0.0/13
131.0.72.0/22
```

**操作**(AWS 控制台):

1. EC2 → Security Groups → 找 ALB 关联的 SG
2. Inbound rules → 当前应该有一条 `0.0.0.0/0 → HTTPS:443`
3. 删掉那条,新增 15 条(每个 CF IP 段一条),Port HTTPS:443
4. Save

**⚠️ 操作顺序要对**:先加新规则,确认能访问,再删 `0.0.0.0/0`。不然中间有几秒所有流量被挡。

### 7.2 发布脚本加 Purge

发布 `index.html` 后立刻 Purge,否则 CF 缓存的旧 HTML 还会继续返回给用户几分钟。

1. CF 控制台 → My Profile → API Tokens → Create Token
   - Template: **Custom token**
   - Permissions: `Zone → Cache Purge → Purge`(仅这一个权限)
   - Zone Resources: Include → Specific zone → `vigilath.com`
2. 保存 token(只显示一次!),写进服务器 `/home/ubuntu/.cloudflare-token`(权限 600)
3. 发布脚本末尾加:

```bash
# 从 CF 控制台 Overview 页能看到 Zone ID
ZONE_ID="YOUR_ZONE_ID"
CF_TOKEN=$(cat /home/ubuntu/.cloudflare-token)

curl -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/purge_cache" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"files":["https://www.vigilath.com/","https://www.vigilath.com/index.html"]}'
```

把这段放在 `deployment-guide.md` 发布流程的第 8 步 rsync 之后。

### 7.3 开一键好处(几秒点完)

CF 控制台 → Speed / SSL-TLS 里:

- **Always Use HTTPS**:ON(HTTP 请求自动升 HTTPS)
- **Automatic HTTPS Rewrites**:ON(HTML 里 `http://` 链接自动改 `https://`)
- **HSTS**:ON,max-age 1 year,includeSubDomains。**慎用**:一旦开,浏览器缓存期内所有子域必须 HTTPS
- **TLS 1.3**:ON(默认就是)
- **0-RTT**:ON(TLS 恢复省一次往返)
- **HTTP/3 (QUIC)**:ON(免费版就有)
- **Brotli**:ON(自动替代 gzip)
- **Rocket Loader**:**OFF**。它会把 JS 重排序,可能破坏 React 挂载。不值得冒险
- **Auto Minify**:**OFF**。vite 已经 minify 过,重复 minify 收益几乎为零,还可能破 sourcemap

### 7.4 监控

- CF Analytics 标签页:看 cache hit ratio(目标 > 80%)、bandwidth saved、threat count
- 如发现 cache hit ratio 低于 50%,检查 Page Rules 是不是配错、`/assets/*` 有没有被意外 bypass
- EC2 侧观察:nginx 出流量应显著下降,可以用 `nginx -V` 加 stub_status 看连接数

## 八、已知陷阱清单

| 陷阱 | 症状 | 处理 |
|---|---|---|
| 把 `/api/*` 配成缓存 | 用户看到别人的检测结果、别人的账户数据、登录态异常 | Phase 2 必须先配 Bypass 规则,再 verify 无 HIT 响应 |
| Stripe webhook 被 CF 拦 | 支付成功但会员没激活 | CF Firewall Rules 里加 Stripe 官方 IP 白名单(https://stripe.com/files/ips/ips_webhooks.txt),或 webhook 路径 Bypass All |
| Rocket Loader 打碎 React | 页面空白,Console 报 `ReactDOM.render` 相关错 | Speed → Optimization → Rocket Loader: **OFF** |
| Bot Fight Mode 把 Claude 屏蔽 | 付费功能 `/api/check` 收到的 AI 爬虫流量被 CF 当机器人拦 | Security → Bots → Bot Fight Mode: 初期 **OFF**,要开也要排除 `/api/*` |
| 本机 curl 测 CF 命中率不准 | edge 节点不同,HIT/MISS 看起来随机 | 用同一节点多次访问,或直接信任 CF Analytics |
| `index.html` 长缓存 | 发布后旧版本持续几分钟/几小时 | 要么 nginx 给 index.html 加 `Cache-Control: no-cache`,要么发布时 Purge |
| 传播期出现 mixed state | 一部分用户走 CF,一部分走旧 DNS | 传播期是 Phase 1 预期行为,通过 `dig @不同 resolver` 观察进度即可 |
| CF 不支持 WebSocket(Free) | SSE 可以,WebSocket 不稳 | 本项目目前没用 WebSocket,`/geo/stream` 是 SSE,正常 |

## 九、回滚总览

| 阶段 | 回滚动作 | 生效时间 |
|---|---|---|
| Phase 0 | 无需回滚(未改任何东西) | — |
| Phase 1 | GoDaddy NS 改回自己的 | 24-48h |
| Phase 2 | CF DNS 把 www 的云朵改回灰色 | 秒级 |
| Phase 3 | CF 删 Redirect Rule + apex CNAME 改回原 A 记录 | 秒级 |
| Phase 4.1(SG 限 IP) | SG 改回 `0.0.0.0/0 HTTPS:443` | 秒级 |
| Phase 4.2(purge 脚本) | 部署脚本去掉那段 curl | 下次发布生效 |

**最坏情况(彻底回滚):**

1. CF → DNS → 所有记录云朵改回灰色
2. GoDaddy → NS 改回 GoDaddy 自己的 NS
3. GoDaddy → Forwarding 恢复(如果 Phase 3 删过)
4. AWS ALB SG 恢复 `0.0.0.0/0`
5. 等 24-48h NS 传播

期间可能有少数用户体验抖动,但没有数据损失。

## 十、执行记录模板

每跑完一个 Phase 在此记录,方便追溯:

```
Phase 0  done: YYYY-MM-DD HH:MM, operator=xxx
Phase 1  done: YYYY-MM-DD HH:MM, NS propagation verified=xxx
Phase 2  done: YYYY-MM-DD HH:MM, smoke test=ok
Phase 3  done: YYYY-MM-DD HH:MM, apex 301 verified no http:// 降级
Phase 4  done: YYYY-MM-DD HH:MM, AWS SG 限 CF IP=done/deferred
```

## 十一、参考

- CF IP ranges(给 AWS SG 用):https://www.cloudflare.com/ips/
- CF 文档 - SSL/TLS 模式:https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/
- CF 文档 - Page Rules:https://developers.cloudflare.com/rules/page-rules/
- CF 文档 - Redirect Rules:https://developers.cloudflare.com/rules/url-forwarding/single-redirects/
- CF API - Purge:https://developers.cloudflare.com/api/operations/zone-purge
- 本项目部署基准:`docs/deployment-guide.md`
- apex 301 bug 背景:`docs/deployment-guide.md` §「已知问题:apex 301 降级到 HTTP」
