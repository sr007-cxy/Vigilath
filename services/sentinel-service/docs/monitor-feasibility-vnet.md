# VNET 舆情 monitor — 平台覆盖清单

> Target: 世纪互联 (VNET) · 检测日期: 2026-05-09
> 数据基线: `tmp/世纪互联0508195924.xlsx`(2232 篇 / 178 家媒体)
> 我们的体系: 68 个 platform 种子表 + 14 个 crawler + 4 个搜索引擎(`baidu / cnbing / ddg / sogou`)

## TL;DR

- **能跑全链路的:14 个平台 / 474 篇 / 21%**(今天就在用)
- **能搜不能爬的:7 个平台 / 922 篇 / 41%**(补 crawler 即转 ✅)
- **理论可达天花板:79%**(1756 / 2232 篇,搜索引擎宽松匹配)
- **真长尾:21%**(131 家小媒体,82 家在 Excel 里只出现 1 次,不投入)
- **最便宜下一步**:5 行 root-domain SEED patch 救 261 篇(`eastmoney.com / baidu.com / qq.com / 163.com / sina.cn`)

档位说明:✅ 能用 · 🟡 能搜不能爬 · 🟠 反爬/登录 · 🔴 不在种子表 · ⚫ 闭源/不可达

---

## 一表全览(25 个主要平台,按 Excel 体量降序)

| 平台 | Excel 篇 | 档 | 一句话原因 |
|---|---:|:---:|---|
| 微信公众号 | 552 | 🟡 | 搜狗 weixin 端点能搜,正文被 wappoc captcha 拦,需写 crawler |
| 今日头条 | 283 | 🟡 | 搜得到,正文是 SPA,需从 `__INITIAL_STATE__` 提 |
| 雪球 | 175 | ✅ | `crawler/xueqiu.py`,API + cookie |
| 新浪微博 | 165 | 🟠 | 搜得到 URL,正文需登录;m.weibo.cn 匿名 JSON 可救 |
| 东方财富股吧 | 97 | ✅ | `crawler/eastmoney.py`(注意:Excel 里 mguba 子域要靠 root domain SEED) |
| 新浪财经 / 网 | 80 | ✅ | `crawler/sina_finance.py` + `sina_stock_news.py` |
| 东方财富网 / 财富号 | 75 | ✅ | `eastmoney_news/announcement/research/industry` 4 crawler |
| 百家号 | 65 | 🔴 | `baidu.com` 不在种子表;DDG 实测能搜到 |
| 有驾 APP | 55 | 🔴 | 汽车站,与 IDC 业务无关,**误命中,不补** |
| 同花顺 | 49 | 🟡 | 种子表有 `10jqka`;正文 SSR,需写 crawler |
| UC 头条 | 45 | 🔴 | `uczzd.cn` 不在种子表;UC 主推 APP,搜索引擎索引浅 |
| 乙方宝招标 | 40 | 🔴 | 不在种子表;招标公告与品牌舆情弱相关 |
| 新浪新闻 APP | 40 | 🔴 | `sina.cn` 不在种子表(只有 `sina.com.cn`);加 root SEED 即救 |
| 格隆汇 | 37 | ✅ | `crawler/gelonghui.py` |
| 搜狐 | 35 | 🟡 | 种子表有;P2 实测 2/3 正文 OK,需写轻量 crawler |
| 网易 | 28 | 🔴 | `163.com` root 不在种子表(只 `money.163.com`)|
| 金投网 | 24 | 🔴 | 不在种子表;`m.cngold.org` |
| 小红书 | 17 | 🟠 | sign 反爬 + JS 渲染,必须 browser-service |
| 腾讯自选股 | 17 | 🔴 | `qq.com` root 不在种子表(只 `finance/mp/v.qq.com`)|
| Wind 资讯 APP | 17 | ⚫ | 付费金融终端,无 web 端 |
| 一点资讯 | 17 | 🔴 | `yidianzixun.com` 不在种子表 |
| 哔哩哔哩 | 16 | 🟡 | 搜狗实测命中;`api.bilibili.com/x/web-interface/view` 公开匿名,需写 crawler |
| 抖音 | 14 | 🟠 | SPA + WAF,文本字段稀疏,**ROI 低,放弃** |
| 中金财富 APP | 13 | ⚫ | 闭源 APP |
| 韭研公社 | 11 | 🔴 | 不在种子表 + 内容多需登录,**不补** |
| 大智慧 APP | 10 | ⚫ | 闭源 APP |
| 和讯 | 9 | 🟡 | 种子表有 `hexun`;正文在 `news.hexun.com` 子域,需写 crawler |
| Twitter / X | 0 | ⚫ | 全登录 + API 收费;Excel 中无样本 |
| 快手 | 0 | 🟠 | 同抖音,**放弃** |

剩余 ~150 家长尾(地方党报 / 政府站 / 行业垂媒 / IDC 圈媒等)— 见 §6 真长尾说明。

---

## §1 ✅ 能用的:14 个 crawler

每个都有专属 module + `/run-crawl-<name>` HTTP 端点,搜索 → 正文 → 入库全自动。

| # | crawler 文件 | 平台 | Excel 篇 |
|---:|---|---|---:|
| 1 | `xueqiu.py` | 雪球 | 175 |
| 2 | `eastmoney.py` | 东方财富股吧 | 97 |
| 3 | `eastmoney_news.py` | 东方财富资讯 | 75(下含)|
| 4 | `eastmoney_announcement.py` | 东方财富公告 | (上含)|
| 5 | `eastmoney_research.py` | 东方财富研报 | (上含)|
| 6 | `eastmoney_industry.py` | 东方财富行业 | (上含)|
| 7 | `sina_finance.py` | 新浪财经 | 80 |
| 8 | `sina_stock_news.py` | 新浪个股资讯 | (上含)|
| 9 | `gelonghui.py` | 格隆汇 | 37 |
| 10 | `cls_finance.py` | 财联社 | <5 |
| 11 | `wallstreetcn.py` | 华尔街见闻 | <5 |
| 12 | `yicai.py` | 第一财经 | <5 |
| 13 | `kr36.py` | 36 氪 | <5 |
| 14 | `baidu_tieba.py` | 百度贴吧 | 0(本 target)|

---

## §2 🟡 能搜不能爬:7 个平台 / 922 篇

> 都已在种子表 + monitor 能搜到 URL。**写 crawler 是 21% → 62% 的关键路径**。

| 平台 | Excel | 写 crawler 的难度 | 关键提示 |
|---|---:|:---:|---|
| **微信公众号** | **552** | 中 | 调 UA + Referer 防 wappoc captcha;生产 CN VM 跑 |
| **今日头条** | **283** | 中 | `www.toutiao.com/article/<id>` 或 `/w/<id>`,从 `__INITIAL_STATE__` JSON 提正文 |
| 同花顺 | 49 | 低 | `news.10jqka.com.cn/<id>.shtml` SSR 静态 |
| 搜狐 | 35 | 低 | `www.sohu.com/a/<id>`,`<article>` + `class="article"` |
| 网易 | 28 | 低 | `www.163.com/dy/article/<id>.html` SSR + UA 池 |
| 哔哩哔哩 | 16 | 低 | 调 `api.bilibili.com/x/web-interface/view?bvid=<bv>` + reply API |
| 和讯 | 9 | 低 | `news.hexun.com/<date>/<id>.html` SSR |

**模板**:抄 `crawler/eastmoney_news.py` 骨架,~80 行/个。

---

## §3 🟠 反爬墙 / 需登录:4 个平台 / 196 篇

> 普通 HTTP 拿不到,需 `services/browser-service` + 登录 profile。**ROI 中低**。

| 平台 | Excel | 卡点 | 出路 |
|---|---:|---|---|
| 新浪微博 | 165 | 桌面 weibo.com 强登录;搜得到 URL 但正文返登录页 | **m.weibo.cn 匿名 JSON 端点可拿正文**,~半天工作量 |
| 小红书 | 17 | sign 反爬 + JS 渲染 + 风控 | 必须 browser-service,**ROI 低,缓做** |
| 抖音 | 14 | SPA + WAF;文本字段稀疏 | 视频站,文本舆情价值低,**放弃** |
| 快手 | 0 | 同抖音 | Excel 0 样本,**放弃** |

---

## §4 🔴 不在种子表:11 个平台 / ~317 篇 / 分两类

### 4.1 子域问题 — 改 5 行 SEED 即救 ~261 篇

种子表里现有的"具体子域"过细,改成 root domain 后搜索引擎 site: 自动覆盖兄弟子域:

| 媒体 | Excel | 实际 domain | 种子里只有 |
|---|---:|---|---|
| 东方财富网股吧(移动)| 97 | mguba.eastmoney.com | guba.eastmoney.com |
| 百度 APP / 百家号 | 65 | baijiahao.baidu.com | tieba/zhidao/news.baidu.com |
| 东方财富财富号 | 40 | caifuhao.eastmoney.com | guba.eastmoney.com |
| 新浪新闻 APP | 40 | k.sina.cn | finance.sina.com.cn |
| 网易 | 28 | www.163.com | money.163.com |
| 腾讯自选股 / 新闻 / 视频号 | 22 | gu/news/channels.qq.com | finance/mp/v.qq.com |

**SEED patch**(贴到 `backend/migrations/010_sentiment_platforms.py:113`,rerun migration):

```python
# ── 2026-05-09 子域放宽 ──
("eastmoney_root", "eastmoney.com",  "finance",  "mainland", "东方财富(全站)",  "Eastmoney All",  21),
("baidu_root",     "baidu.com",      "news",     "mainland", "百度系(全站)",    "Baidu All",      11),
("sina_cn",        "sina.cn",        "news",     "mainland", "新浪移动",        "Sina .cn",       31),
("163_root",       "163.com",        "news",     "mainland", "网易(全站)",      "Netease All",    71),
("qq_root",        "qq.com",         "news",     "mainland", "腾讯系(全站)",    "Tencent All",    61),
```

⚠️ 副作用:`qq.com` 也会带出游戏/邮箱/QQ 空间;LLM plan 按 target 关键词过滤后杂讯有限,但要观察一周。

### 4.2 完全不在体系内的独立平台 — 选择性补

| 平台 | Excel | 建议 | 理由 |
|---|---:|---|---|
| UC 头条 (uczzd.cn) | 45 | 🟡 观望 | UC 主推 APP,搜索引擎索引浅,加 row 试一周 |
| 乙方宝 (yfbzb.com) | 40 | 🟡 按需 | 招标公告,P2 实测正文 3/3 OK;只在监测采购口风时打开 |
| 一点资讯 (yidianzixun.com) | 17 | ✅ 加 | 新闻聚合,索引正常 |
| 金投网 (cngold.org) | 24 | ✅ 加 | 财经垂媒 |
| 韭研公社 (jiuyangongshe.com) | 11 | ❌ 不加 | 小众社区 + 多需登录,ROI 不够 |
| 有驾 (yoojia.com) | 55 | ❌ 不加 | 汽车站,误命中 |

---

## §5 ⚫ 闭源 / 不可达:4 个平台 / 40 篇

| 平台 | Excel | 原因 |
|---|---:|---|
| Wind 资讯 APP | 17 | 付费金融终端,无 web 端 |
| 中金财富 APP | 13 | 闭源 APP |
| 大智慧 APP | 10 | 闭源 APP |
| Twitter / X | 0 | 全登录 + API 收费;Excel 0 样本 |

不投入,接受不覆盖。

---

## §6 真长尾:131 家 / 476 篇 / 21%

剩下 131 家媒体的特点:

- **82 家在 Excel 里只出现 1 次** — 加进 platform 的 ROI 极低
- **40 家出现 2-5 次** — 共 110 篇
- 大头是:省级党报(齐鲁晚报 / 河北经济日报 ...)、政府站(.gov.cn 系)、行业垂媒(idcquan / cctime / 北极星 / 通信网)、人民日报系移动 APP、ZAKER 等聚合 APP

**结论**:79% 是搜索引擎理论天花板;追这 21% 长尾收益 / 投入比太低,**接受不覆盖**。

---

## §7 其他平台详解(非主流,按分类一家一家说)

> 主流 8 家(微信/头条/微博/B 站/小红书/抖音/快手/Twitter)在前面表里已说清楚;这里覆盖剩下 ~17 家"其他"平台 — **金融类、综合门户、闭源 APP**。每家给 *当前状态 + 为啥 + 怎么搞*。

### 7.1 金融平台(13 家)

#### ✅ 已能跑(6 家 / ~480 篇)

- **雪球(xueqiu.com,175 篇)** — 用 `crawler/xueqiu.py`,API 调用前先 GET 主页拿 `xq_a_token` cookie,然后调讨论流接口。生产 CN VM 上稳定,本次 monitor 跑 inserted 10 条。
- **东方财富股吧(guba.eastmoney.com,97 篇)** — `crawler/eastmoney.py`,直接调 guba 公开 API,无需 auth。本次 monitor inserted 6 条。
- **东方财富网 / 财富号 / 研报 / 公告(75 篇)** — 4 个专属 crawler(`eastmoney_news/announcement/research/industry`)分别走不同搜索 API。注意:Excel 里 mguba/caifuhao 子域文章是同一公司,**§4.1 加 `eastmoney.com` root 后这些子域也会被搜索引擎 site: 自动覆盖**。
- **新浪财经(80 篇)** — `crawler/sina_finance.py` + `sina_stock_news.py` 两个 crawler 分别打综合财经和个股资讯。
- **格隆汇(37 篇)** — `crawler/gelonghui.py`,公开搜索 API。
- **财联社 / 华尔街见闻 / 第一财经 / 36 氪(共 <20 篇)** — 各自 crawler;本 target 体量不大,但已覆盖。

#### 🟡 缺 crawler(2 家 / 58 篇)

- **同花顺(10jqka.com.cn,49 篇)** — 种子表已有 `10jqka.com.cn`,搜索引擎能 site: 命中,但**正文 crawler 没写**。`news.10jqka.com.cn/<id>.shtml` 是 SSR 静态页,生产 CN VM 直连可拿(本地 EC2 IP 实测 verification 墙)。**写一个 ~80 行 crawler 即可入库**。
- **和讯(hexun.com,9 篇)** — 种子表有 `hexun.com`,但根域返回 988 字节空壳,**真正文都在 `news.hexun.com` 子域**。需要 crawler 显式打 news 子域 + 解析 SSR HTML。

#### 🔴 不在种子表(2 家 / 17+24 = 41 篇)

- **腾讯自选股(gu.qq.com,17 篇)** — 种子表只有 `finance.qq.com / mp.weixin.qq.com / v.qq.com`,**没有 `qq.com` root**,所以 `site:gu.qq.com` 不会被 LLM plan 生成。**§4.1 patch 加 `qq.com` root 后自动覆盖**(也带出 news.qq.com / channels.weixin.qq.com 共 22 篇)。
- **金投网(cngold.org,24 篇)** — 完全独立财经垂媒,种子表没收。简单加一行 SEED 即可,搜索引擎索引正常。

#### ⚫ 闭源 APP(3 家 / 40 篇)

- **Wind 资讯金融终端(snap.windin.com,17 篇)** — 付费金融数据终端,内容封闭在 APP / 客户端,**无 web 端公开接口**,只能买 Wind API(¥几十万/年)。**接受不覆盖**。
- **中金财富 APP(web.ciccwm.com,13 篇)** — 中金证券客户端,需 APP 登录。**接受不覆盖**。
- **大智慧 APP(share.dzh.com.cn,10 篇)** — 行情终端 APP,同上。**接受不覆盖**。

### 7.2 综合资讯门户(8 家)

> 这块**最大的痛点是子域问题**:种子表给的 domain 太具体,父域兄弟子域被漏掉。§4.1 一个 SEED root domain 调整可救一大半。

#### 🔴 子域问题(5 家 / ~213 篇,加 root 即转 🟡)

- **新浪网 + 新浪新闻 APP(sina.com.cn / k.sina.cn,共 120 篇)** — 桌面 `sina.com.cn` 有,但移动 `k.sina.cn` 走 `.cn` 短域不命中。**§4.1 加 `sina.cn` root** 救 40 篇移动版。
- **网易(www.163.com,28 篇)** — 种子表只有 `money.163.com` 财经子域;综合新闻 `www.163.com` 不命中。**加 `163.com` root** 救 28 篇。但即使加完还是 🟡:综合 163 没专属 crawler,正文需写。
- **百家号(baijiahao.baidu.com,40 篇)+ 百度 APP(mbd.baidu.com,25 篇)** — 种子表有 tieba/zhidao/news.baidu.com,**没有 `baidu.com` root**。**加完后 65 篇可搜到 URL**;但百度系强反爬(redirect to wappass),正文还是要单独搞。

#### 🟡 缺 crawler(2 家 / 63 篇)

- **搜狐(sohu.com,35 篇)** — 种子表已有,**P2 实测 2/3 正文 OK**(SSR 静态),写一个轻量 crawler 即可,~50 行。
- **网易**(见上,加 root 后归这里)

#### 🔴 完全不在种子表(3 家)

- **UC 头条(uczzd.cn / a.mp.uc.cn,共 75 篇)** — 阿里 UC 的内容平台。**搜索引擎索引浅**(UC 自家有 robots 限制 + 主推 APP),即使加种子表估计 site: 命中也少。**建议**:先加一行 SEED 试一周,看 `data.by_source` 实际命中数,<10 就关掉。
- **一点资讯(yidianzixun.com,17 篇)** — 主流新闻聚合,索引正常。**直接加种子表**,正文 SSR 简单,后续再补轻量 crawler。
- **凤凰网(ifeng.com,6 篇)** — 种子表已有,本数据集体量小;不专门做。

### 7.3 行业垂媒 / 招标 / 误命中(3 类)

- **乙方宝招标(yfbzb.com,40 篇)** — IT 招标公告。**P2 实测 3/3 正文 OK**,但**与品牌舆情弱相关**(发布的是机房 / 服务器采购公告)。**只在做"采购信号 / 项目中标"监测时打开**,不进默认种子表。
- **韭研公社(jiuyangongshe.com,11 篇)** — 小众股民社区。Web 端有内容但部分需登录,**搜索引擎索引也浅**。**ROI 低,不补**。
- **有驾(yoojia.com,55 篇)** — **汽车类网站**,Excel 中是因 IDC 关键词撞车误命中(如"AIDC" 撞 "汽车 IDC"),与 VNET 业务无关。**不补,不应该出现在 target 范围**。

### 7.4 长尾 131 家 / 476 篇

见 §6。简单说:省级党报、政府站、行业垂媒(idcquan / cctime / 北极星)、各种 APP 镜像。**80% 在 Excel 里只出现 1 次**,加 platform 的边际收益等于 0。**接受不覆盖**。

### 7.5 其他平台一句话总结

| 类别 | 平台数 | Excel 篇 | 当前能用 | 解锁路径 |
|---|---:|---:|---|---|
| 金融 ✅ | 6 | ~480 | 全 OK | 已经在用 |
| 金融 🟡 | 2 | 58 | 缺 crawler | 各 ~80 行 |
| 金融 🔴 子域 | 1 | 22 | 加 `qq.com` root | §4.1 patch |
| 金融 🔴 独立 | 1 | 24 | 加 `cngold.org` | 1 行 SEED |
| 金融 ⚫ | 3 | 40 | 不可达(闭源 APP)| 接受 |
| 门户 🔴 子域 | 5 | 213 | 加 root domain | §4.1 patch |
| 门户 🟡 | 2 | 63 | 缺 crawler | 各 ~50 行 |
| 门户 🔴 独立 | 3 | 92 | 选择性补 | 看 ROI |
| 招标/误命中/小众 | 3 | 106 | 多数不补 | 与品牌舆情弱相关 |
| **小计(非主流)** | **26** | **~1098** | **6 ✅ + 18 待补 + 2 不补** | |

---

## §8 媒体 vs 平台(概念附录)

第三方舆情服务按"信息源条目"算"媒体":同公司不同站 / 不同 APP 各算 1 家。
我们按"搜索引擎 site: 单位"算"平台":1 个 root domain 一个 platform。

**多对一**:1 个 platform 通常承载多家"媒体"。

| 我们的 platform | Excel 里对应"媒体"个数 / 篇 |
|---|---|
| `mp.weixin.qq.com` | 1 家 / 552 篇 |
| `hexun.com` | 3 家(网/股票/财经)/ 11 篇 |
| `sina.com.cn` | 2 家(新浪网+股市汇)/ 81 篇 |
| `sohu.com` | 2 家(搜狐+APP)/ 46 篇 |

这就是为什么"178 家媒体" ≠ "178 个 platform"。所有覆盖率数字按**文章数**算,不按"媒体数"。

---

## §9 怎么用 monitor 临时跑某个不在种子表的平台

不动种子表,直接 POST(等价 `--media media.txt`):

```bash
curl -X POST http://127.0.0.1:8090/run-monitor -H 'Content-Type: application/json' -d '{
  "account_id": 1,
  "target": "世纪互联",
  "ticker": "vnet",
  "aliases": ["VNET", "21Vianet"],
  "media_allowlist": ["uczzd.cn", "yfbzb.com", "yidianzixun.com"],
  "force_all": true,
  "engines": ["sogou", "ddg", "baidu", "cnbing"]
}'
```

返回的 `data.by_source` 字段会按 domain 统计命中数 — **跑一次就知道每个 domain 实际能搜到几条**,作为"加不加种子表"的决策依据。

---

## §10 数据源 / 关键文件

| 文件 | 说明 |
|---|---|
| `tmp/世纪互联0508195924.xlsx` | 第三方舆情服务导出基线(2232 篇 / 178 家媒体)|
| `tmp/probe_crawl_results.json` | P2 正文探针(199 域 × ≤3 样本) |
| `tmp/probe_p1_sogou.json` | P1 搜索探针(搜狗,4 个平台命中)|
| `services/sentinel-service/search/sogou.py` | 本轮新增的搜狗引擎(已接入 monitor)|
| `services/sentinel-service/crawler/*.py` | 14 个正文 crawler |
| `backend/migrations/010_sentiment_platforms.py` | platform 种子表(SEED 列表 68 条)|

---

## §11 行动建议(优先级降序)

1. **15 分钟** — 加 5 行 root-domain SEED(§4.1 patch)→ 救 261 篇 / 多覆盖 12 家媒体
2. **30 分钟** — 加 2 行 SEED(yidianzixun + 金投网)→ 救 41 篇
3. **2-3 天** — 写 7 个 crawler(§2 列表)→ 21% → 62%
4. **半天** — 微博 m.weibo.cn 匿名版 → 165 篇
5. **可选** — 把本地 `search/sogou.py` git push + vm02 redeploy(目前 VM 上还跑旧 3 引擎,见 `tmp/vnet_run.log`)
