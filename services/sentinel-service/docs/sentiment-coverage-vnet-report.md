# 世纪互联 (VNET) — 现有 monitor 覆盖率诊断报告

> 数据源:`tmp/世纪互联0508195924.xlsx`(2232 篇 / 178 家媒体 / 10 类)
> 探针时间:2026-05-08
> 探针脚本:`tmp/probe_crawl_coverage.py`(P2 正文)+ `tmp/probe_p1_sogou.py`(P1 搜索)
> 输出 JSON:`tmp/probe_crawl_results.json` + `tmp/probe_p1_sogou.json`

## 0. 本轮新增产物(2026-05-08 16:30 update)

- **新增搜索引擎 `services/sentinel-service/search/sogou.py`**:免费、免 key、纯 HTTP scrape;支持 `www.sogou.com/web` 通用搜索 + `weixin.sogou.com/weixin` 微信公众号专属端点(`channel="auto"` 时自动路由)
- **接入 pipeline**:`search/pipeline.py:DEFAULT_ENGINES` 加入 `"sogou"`;`service.py:MonitorRequest.engines` 默认列表加入;`/run-monitor` 端点无需改动即生效
- **P1 实测验证**:从 EC2 出口跑搜狗,**1016 篇高优先级 Excel 文章对应的 4 个平台均确认能搜到 site: 结果**(微信 / 头条 / 微博 / B 站,各 10+ hits / 查询)— 解决了之前 monitor 对这几个平台"只能搜到 snippet 不能拿正文"中**搜索那一半的可靠性问题**

## 一、TL;DR(优先级一表)

按用户优先级:**主流 8 家先列,再列金融、综合**。

档位定义:

- ✅ **A 全通**:种子表里有 + monitor 能搜到链接 + 正文可直接 GET
- 🟡 **B 链接 OK / 正文需 crawler**:能搜到 URL,但通用 fetch 拿不到正文(需专属 crawler 处理 cookie/UA/JS)
- 🟠 **C 反爬严重 / 需登录态**:URL 拿到了也是反爬墙,需 browser-service + 登录 profile
- 🔴 **D 站不在册**:domain 不在 `sentiment_platforms`,monitor 根本不会生成 `site:` 查询
- ⚫ **E 闭源 / 不可达**:APP-only / 付费终端 / 全登录

| # | 平台 | Excel 篇数 | 种子表 | 已有 crawler | **P1 搜狗实测** | 正文 GET 测试 | **档位** | 主要卡点 |
|---|---|---:|:---:|:---:|:---:|:---:|:---:|---|
| **— 主流 8 家 —** ||||||||
| 1 | 微信公众号 | **552** | ✅(weixin) | ❌ | **✓ 10 hits**(weixin.sogou) | 0/3 → 重定向到 captcha | 🟡 B | **搜狗 weixin 端点完美命中**;mp.weixin 对非 CN IP+无指纹直接走 wappoc_appmsgcaptcha;CN VM + 合理 UA 可绕,需新写 crawler |
| 2 | 今日头条 | **283** | ✅(toutiao) | ❌ | **✓ 10 hits** | 0/3 → SPA shell | 🟡 B | `www.toutiao.com/w/<id>` 是 SPA,正文在 hydration JSON 里;需写 crawler 解析 `__INITIAL_STATE__` |
| 3 | 新浪微博 | **165** | ✅(weibo) | ❌ | **✓ 10 hits** | 0/3 → 9–26KB 登录卡点页 | 🟡 B | **搜狗实测搜得到 weibo.com 链接**;桌面 weibo.com 强登录态;m.weibo.cn 公开 JSON 端点可不登录拿正文 |
| 4 | 哔哩哔哩 | 16 | ✅(bilibili) | ❌ | **✓ 10 hits** | 0/3 → 200KB SPA / 412 | 🟡 B | 视频页 SPA,但 `api.bilibili.com/x/web-interface/view` 公开匿名可调,正文 + 简介可拿 |
| 5 | 小红书 | 17 | ✅(xiaohongshu) | ❌ | ⏸ 反爬限流 | 0/3 → 514KB 验证墙 | 🟠 C | SSR 仅 30%,主体在 `__INITIAL_STATE__` + sign 反爬;必须 browser-service |
| 6 | 抖音 | 14 | ✅(douyin) | ❌ | ⏸ 反爬限流 | 0/3 → SPA shell 72KB | 🟠 C | 视频文本字段稀疏,SPA + WAF;ROI 极低 |
| 7 | 快手 | 0 | ✅(kuaishou) | ❌ | ⏸ 反爬限流 | 0/2 → 超时 / 验证墙 | 🟠 C | Excel 里 0 样本;同抖音 |
| 8 | Twitter / X | 0 | ✅(twitter) | ❌ | — | 无样本 | ⚫ E | x.com 全登录;Excel 中 VNET 无样本 |
| **— 金融 —** ||||||||
| 9 | 雪球 | **175** | ✅ | ✅ `crawler/xueqiu.py` | 0/3(本地)/ 生产 OK | ✅ A | 已覆盖。本地 fetch 看似失败是因为我们的内容 marker 没匹配雪球的 class 名(实际页面 106KB SSR 完整) |
| 10 | 东方财富股吧 | 97 | ✅(guba.eastmoney) | ✅ `eastmoney.py` | 0/3(本地反爬) | ✅ A | 已覆盖 |
| 11 | 东方财富网 / 财富号 | 75 | ✅(guba.eastmoney) | ✅ `eastmoney_news.py` | caifuhao 0/3 反爬 | ✅ A* | `eastmoney_news.py` 走的是搜索 API,与 caifuhao SSR 页不同;子域 `caifuhao.eastmoney.com` 个人号文章可能漏 |
| 12 | 新浪财经 | 80 | ✅(sina_finance) | ✅ `sina_finance.py / sina_stock_news.py` | **1/3 OK** | ✅ A | 已覆盖。其他 2 条失败是 `view` 路由跳转 |
| 13 | 同花顺 | 49 | ✅(ths) | ❌ | 0/3 → verification | 🟡 B | `news.10jqka.com.cn/<id>.shtml` SSR 静态(本地反爬,CN OK);`t.10jqka.com.cn` 个股是 401 接口 |
| 14 | 格隆汇 | 37 | ✅ | ✅ `gelonghui.py` | 0/3(本地反爬) | ✅ A | 已覆盖,page 350KB,有 article 标记但被 marker 误判 |
| 15 | 搜狐 | 35 | ✅(sohu) | ❌ | **2/3 OK** | 🟡 B | SSR 静态,正文在 `class="article"`;**新增轻量 crawler 即可** |
| 16 | 网易 | 28 | ✅(money.163) | ❌ | 0/3(robot 标记)| 🟡 B | `www.163.com/dy/article/<id>.html` SSR;有 robots 检测,需要 UA 池 |
| 17 | 腾讯自选股 | 17 | ❌(只有 finance.qq.com) | ❌ | 0/3 → 641 字节空壳 | 🔴 D | gu.qq.com 不在种子表 → monitor 不查;且页面是动态 SPA |
| 18 | 一点资讯 | 17 | ❌ | ❌ | 0/3 → robot 标记 | 🔴 D | yidianzixun.com 不在种子表 |
| 19 | 韭研公社 | 11 | ❌ | ❌ | 0/3 → 38–62KB 登录提示 | 🔴 D | jiuyangongshe.com 不在种子表 + 大量内容需登录 |
| 20 | 和讯 | 9 | ✅(hexun) | ❌ | 0/3 → 988 字节空壳 | 🟡 B | hexun.com 在种子表;但 fetch 返回极小,可能根域跳转;需写 crawler 走具体 news.hexun.com 子域 |
| **— 综合 / 门户 —** ||||||||
| 21 | 百家号 | ~40 | ❌(只有 news.baidu.com) | ❌ | 0/3 → wappass.baidu 验证 | 🔴 D | baijiahao.baidu.com 不在种子表;且百度系强反爬,需要绕过 wappass |
| 22 | 百度 APP / 百家号移动 | ~25 | ❌ | ❌ | 0/3 → 1488 字节验证 | 🔴 D | mbd.baidu.com 不在种子表 |
| 23 | UC 头条 | 75 | ❌ | ❌ | 0/3 → 23KB 但带 404 标记 | 🔴 D | uczzd.cn / a.mp.uc.cn / m.uczzd.cn 都不在种子表 |
| 24 | 乙方宝招标 | 40 | ❌ | ❌ | **3/3 OK** | 🔴 D / 可补 | yfbzb.com 不在种子表;**正文可拿,但内容是招标公告,与品牌舆情弱相关**;按需补 |
| 25 | 有驾 | 55 | ❌ | ❌ | 0/3 → 401 | 🔴 D | yoojia.com 是汽车站,与 IDC 无关 → 误命中,**不补** |
| **— APP 闭源 —** ||||||||
| 26 | Wind 资讯 APP | 17 | ❌ | ❌ | — | ⚫ E | 付费金融终端,无 web 端 |
| 27 | 中金财富 APP | 13 | ❌ | ❌ | — | ⚫ E | 闭源 APP |
| 28 | 大智慧 APP | 10 | ❌ | ❌ | — | ⚫ E | 闭源 APP |

**汇总(按 Excel 体量加权)**:

| 档位 | 平台数 | Excel 文章数 | 占比 |
|---|---:|---:|---:|
| ✅ A 已覆盖 | 6(雪球 / 东财 / 新浪 / 格隆汇 / + 已有 crawler) | ~474 | **~21%** |
| 🟡 B 能搜不能爬,加 crawler 即可 | 6(微信 / 头条 / B站 / 同花顺 / 搜狐 / 网易 / 和讯) | ~922 | **~41%** |
| 🟠 C 反爬严重 / 登录态 | 4(微博 / 抖音 / 快手 / 小红书) | ~196 | ~9% |
| 🔴 D 站不在种子表 | 7(百家号 / 百度 APP / UC头条 / 腾讯自选股 / 一点 / 韭研 / 乙方宝 / 有驾) | ~280 | ~13% |
| ⚫ E 闭源 / 不可达 | 4(Twitter / Wind / 中金 / 大智慧) | ~40 | <2% |
| 其他长尾(178 家中剩余 ~150 家)| ~150 | ~320 | ~14% |

**关键结论**:

> 当前 monitor 流程下,**对世纪互联这份 Excel 的"实际可入库覆盖率"约 21%**(已有 crawler 的 6 个平台 / ~474 篇)。
> 把 🟡 B 档(7 个平台 / ~922 篇)加上去,通过新增 crawler 一次性可冲到 **~62%**。
> 剩下 ~38% 中:🟠 C 档(196 篇)需要 browser-service + 登录态投入,ROI 中低;🔴 D 档(280 篇)只需补 5 行种子表 SQL 即可让 monitor 至少能搜到 URL — **最便宜的产出**。
>
> **2026-05-08 update**:把 `sogou` 加入 monitor 默认引擎后,P1(搜索)层对微信 / 头条 / 微博 / B 站这 4 大社交内容平台的命中已从"0(DDG 限流 / baidu CAPTCHA)"提升到"每平台 10+ hits / 查询",**1016 篇 Excel 文章对应的 site: 查询从此可靠**。剩余 21 个平台的 P1 实测因 EC2 出口被搜狗 antispider 拦截未能完成,**生产 CN VM(vm02)用 SOGOU_COOKIE + CN IP 跑应能解锁全部**。

---

## 二、方法 & 重要 caveat

**P2(正文可拿性)探针**:对每个平台从 Excel 抽 3 条真实 URL,`requests.get` + 标准 Chrome UA + follow_redirects,看 status / final_url / 内容标志(`<article>` / `class~content` 等)/ 反爬标志(captcha / verification / robot 等)。

**重要局限 — 探针在 US AWS EC2(184.73.129.180)运行**:

1. **大量 mainland 站点对非 CN IP 触发反爬墙**(微信 mp / 百家号 / 雪球 / 东财 caifuhao / 同花顺 / 网易 等返回的不是真正的 404,而是 wappass / captcha 验证页)。这些站从 CN VM(测试环境 vm02 = 172.80.40.102)跑大概率能拿到。
2. **本地 fetch 失败 ≠ 平台 unreachable**;需结合"是否有专属 crawler"判断。已有 `crawler/xueqiu.py / eastmoney.py / sina_finance.py / gelonghui.py` 等 13 个 crawler 是基于生产 CN VM 的真实跑通经验写的,本地 fetch 失败但 sentinel-service 的 crawler 在生产侧能跑。
3. **`<article>` / `class~"content"` 等 marker 偏严**,会把"页面正文确实存在但 class 名不匹配"的站误判为 ✗。比如雪球(106KB 完整 SSR)、格隆汇(350KB 带 `<article>` 但 marker 漏判)。所以"档位"是综合"种子表 / 已有 crawler / fetch 结果 / 平台架构常识"四个口径给的,不仅看 fetch。

**P1(链接搜索)实测**:加入 `search/sogou.py` 后跑 25 个优先级平台:
- ✅ **4 个完成**:微信公众号 / 今日头条 / 新浪微博 / 哔哩哔哩,每个 10 hits
- ⏸ **21 个被 sogou antispider 拦截**:从 EC2 出口连续请求 4-5 次后所有后续请求 302 到 `/antispider/?m=1`,需 CAPTCHA 解锁。这些平台的"P1 是否能搜"用静态推断替代:domain 是否在 `sentiment_platforms` 种子表 + 是否在主流搜索引擎索引中
- 其他引擎(baidu / cnbing / ddg)在 EC2 出口同样不可用(baidu 验证页 / cnbing CAPTCHA / ddg 限流);**生产 CN VM 配 BAIDU_COOKIE + ddg-proxy + (建议追加)SOGOU_COOKIE 后这三个引擎都正常**

---

## 三、行动建议(三档优先级)

### P0 — 补种子表(零代码,15 分钟,~280 篇覆盖)

仅插行,不写 crawler。让 monitor 至少能搜到这些站的 URL。

```sql
-- 加到 backend/migrations/010_sentiment_platforms.py 的 SEED 列表后,rerun 即可:
INSERT INTO sentiment_platforms (code, domain, category, region, name_zh, name_en, sort_order, enabled) VALUES
  ('baijiahao',   'baijiahao.baidu.com', 'news',    'mainland', '百家号',     'Baijiahao',   12, 1),
  ('baidu_app',   'mbd.baidu.com',       'news',    'mainland', '百度APP',    'Baidu App',   13, 1),
  ('uc_zd',       'uczzd.cn',            'news',    'mainland', 'UC头条',     'UC Toutiao',  14, 1),
  ('yidianzixun', 'yidianzixun.com',     'news',    'mainland', '一点资讯',   'Yidianzixun', 15, 1),
  ('caifuhao',    'caifuhao.eastmoney.com', 'finance', 'mainland', '东财财富号','Caifuhao',  21, 1),
  ('qq_stock',    'gu.qq.com',           'finance', 'mainland', '腾讯自选股','QQ Stock',     22, 1);
```

不补:**有驾 (yoojia.com)** —— Excel 55 条但全是误命中(汽车 IDC 关键词撞车);**Wind / 中金 / 大智慧 / 韭研公社** —— 闭源或登录强,补了也搜不到。

### P1 — 写 7 个 crawler(中等工作量,~922 篇覆盖)

按 Excel 体量降序写,每个 crawler 模板抄 `crawler/eastmoney_news.py`(最干净的骨架):

| 优先级 | crawler 名 | 目标 URL pattern | Excel 体量 | 难度 |
|:---:|---|---|---:|:---:|
| 1 | `weixin_mp.py` | `mp.weixin.qq.com/s?__biz=…` | 552 | 中(需 UA / Referer 调优防 wappoc) |
| 2 | `toutiao.py` | `toutiao.com/article/<id>` `toutiao.com/w/<id>` | 283 | 中(SPA,从 `__INITIAL_STATE__` 提取) |
| 3 | `ths_news.py` | `news.10jqka.com.cn/<id>.shtml` | 49 | 低(SSR 静态) |
| 4 | `sohu.py` | `www.sohu.com/a/<id>` | 35 | **低**(本地实测 2/3 OK) |
| 5 | `netease_news.py` | `www.163.com/dy/article/<id>.html` | 28 | 低 |
| 6 | `bilibili.py` | `api.bilibili.com/x/web-interface/view?bvid=<bv>` | 16 | 低(API 公开) |
| 7 | `hexun.py` | `news.hexun.com/<date>/<id>.html` | 9 | 低 |

每个 crawler 完成后:在 `services/sentinel-service/service.py:401` 后照 `/run-crawl-eastmoney-news` 抄一个 endpoint;在 `backend/geo/services/sentiment_pipeline.py:119` 的 ThreadPool 任务列表注册;`backend/geo/services/sentinel_client.py` 加对应的 `crawl_<platform>()`。

### P2 — 看 ROI 决定(~196 篇 / 4 个平台)

🟠 C 档,用 `services/browser-service` + 登录 profile:

| 平台 | Excel 篇数 | 建议 |
|---|---:|---|
| 微博 (m.weibo.cn) | 165 | **先做匿名版**:`m.weibo.cn/comments/hotflow?id=<mid>` 公开 JSON,不需登录;长期再做 browser-service 登录版拿转发树 |
| 小红书 | 17 | ROI 不够,**缓做**;若未来有消费品牌 target 再统一开 browser engine |
| 抖音 / 快手 | 14 / 0 | 短视频文本字段稀疏,**放弃** |

### 不做(⚫ E)

Twitter / Wind / 中金财富 / 大智慧 / 有驾 — 闭源、付费、误命中,**不投入**。

---

## 四、关键文件一览(实施时改这些)

| 改动类型 | 文件 |
|---|---|
| **(已完成)搜狗引擎** | `services/sentinel-service/search/sogou.py`(新)+ `search/__init__.py` 导出 + `search/pipeline.py:DEFAULT_ENGINES` 加 `"sogou"` + `service.py:203` 默认 engines 加 `"sogou"` |
| **P0 种子表扩容** | `backend/migrations/010_sentiment_platforms.py:37–113`(SEED 追加 6 行) |
| **P1 新 crawler** | `services/sentinel-service/crawler/<name>.py`(模板:`crawler/eastmoney_news.py`) |
| **P1 sentinel HTTP** | `services/sentinel-service/service.py:401+`(模板:`@app.post("/run-crawl-eastmoney-news")`) |
| **P1 backend client** | `backend/geo/services/sentinel_client.py`(加 `crawl_<name>()` 方法) |
| **P1 并行调度** | `backend/geo/services/sentiment_pipeline.py:119–175`(ThreadPool 任务列表注册) |

不动:`geo_checker.py`(根)、`archive/geo_checker_v1_baseline.py`(只读冻结)、`services/sentinel-service/search/plan.py:25`(legacy fallback,主表已在 DB)。

---

## 五、本探针未覆盖到的(下一轮要做的事)

1. **真实 P1 探针**:从 CN VM(vm02)上跑 `monitor` 的实际命中数,与本表 Excel 数比对 — 才是"sentinel 实际能搜到多少"的真值
2. **从 vm02 重跑 P2**:让本地 fetch 失败的(雪球 / 东财 caifuhao / 同花顺 等)用 CN IP + cookie 重测,把"伪 ✗"洗成 ✓
3. **长尾 ~150 家本地媒体诊断**:Excel 178 家里去掉本表 28 家剩 150 家,大多是省级 / 市级 / 行业垂媒(idcquan / cctime / 苏州.gov / 北极星 / 国资委 等),覆盖率本身没意义,**按 target 行业 IDC 相关度筛选 top 20 再补种子表**即可


