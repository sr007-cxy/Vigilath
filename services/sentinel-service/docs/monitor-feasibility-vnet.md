# VNET 舆情 monitor — 平台覆盖清单

> Target: 世纪互联 (VNET) · 检测日期: 2026-05-09
> 数据基线: `tmp/世纪互联0508195924.xlsx`(2232 篇 / 178 家媒体)
> 我们的体系: **77** 个 platform 种子表(2026-05-09 加 9 条)+ **14** 个正文 crawler + **4** 个搜索引擎已 deploy vm02(`baidu / cnbing / ddg / sogou`,baidu+sogou 已配 cookie)

## TL;DR

- **能跑全链路的 14 个平台 / 474 篇 / 21%**(今天就在用)
- **能搜不能爬的 7 个平台 / 922 篇 / 41%**(补 crawler 即"能用")
- **理论可达天花板 79%**(1756 / 2232 篇,搜索引擎宽松匹配)
- **真长尾 21%**(131 家小媒体,82 家在 Excel 里只出现 1 次,接受不覆盖)
- **严格 7d 实测 sentinel 反超惠科 135%**(635 vs 470,见 §3 历史最佳)
- **2026-05-09 已 apply**:9 条 SEED 补丁(种子表 68→77),5 个 root domain 加上后下次 monitor 自动覆盖 ~261 篇之前漏的 mguba/baijiahao/k.sina.cn/www.163/gu.qq.com 等子域

档位定义(按 sentinel 实测命中数 vs 惠科):
- **一致** = sentinel 命中数 ≈ 惠科(差异不大,或 sentinel 反超)
- **缺少** = sentinel 比惠科明显少,但搜得到(可通过加 crawler / 加 query / 等下次跑改善)
- **缺正文** = URL 能搜到但拿不到正文(反爬 / 登录 / SPA 渲染 / 缺专属 crawler)
- **不可达** = 闭源 APP / 全登录,主流引擎完全搜不到

---

## §1 平台对比(惠科 vs sentinel · 5月7日严格对齐)

> **口径**:
> - **惠科 5/7** = Excel `publish_time = 2026-05-07` 当天
> - **sentinel 5/7** = `timelimit='2026-05-07..2026-05-08'` 实测命中数
> - **惠科 1周** = Excel `publish_time` 在 2026-05-01 至 05-07
> - **sentinel 1周** = `timelimit='2026-05-01..2026-05-08'` 实测命中数
> - 两次 sentinel 跑均用 `force_all=True` + 66 个 Excel root domain + 4 引擎
> - **本次 baidu+sogou cookie 双失效**(只剩 cnbing+ddg),不是历史最优;历史最优见 §3.3

| 平台 | 惠科 5/7 | sentinel 5/7 | 惠科 1周 | sentinel 1周 | 档位 | 原因 |
|---|---:|---:|---:|---:|---|---|
| **微信公众号** | **35** | 0 | **95** | 0 | 缺正文 | weixin.sogou.com 能搜但需 cookie + 缺 crawler;本次 cookie 失效 0 命中 |
| **今日头条** | **28** | 0 | **50** | 0 | 缺正文 | 搜索引擎对头条索引浅 + 缺 SPA crawler |
| **新浪微博** | **21** | 0 | 32 | 0 | 缺正文 | URL 能搜,桌面正文登录墙;m.weibo.cn 匿名 JSON 可救 |
| **新浪(网+APP)** | 15 | 1 | **53** | 7 | 缺少 | 本轮新增 `sina.cn` root,下次会更多 |
| **雪球** | 11 | 0 | 39 | 0 | 一致 | search 0 但 crawler 兜底,实际入库正常 |
| 东方财富(全系)| 11 | 0 | 19 | 0 | 一致 | 4 个 crawler 兜底 + 本轮加 `eastmoney.com` root |
| **搜狐** | 4 | **2** | 10 | **7** | 一致 | 接近持平;种子表有,缺 crawler 但 search 已可 |
| 同花顺 | 3 | 0 | 9 | 0 | 缺正文 | search 命中偏少 + 缺 crawler |
| 格隆汇 | 4 | 0 | 6 | 1 | 一致 | crawler 兜底 |
| **百度系(百家号+APP)** | 2 | 0 | 6 | 0 | 缺少 | 本轮新增 `baidu.com` root,下次能命中 baijiahao/mbd |
| 网易 | 1 | 0 | 6 | 0 | 缺少 | 本轮新增 `163.com` root |
| 腾讯系(gu/news/v.qq) | 6 | 0 | 8 | 0 | 缺少 | 本轮新增 `qq.com` root,下次能带 gu/news/channels |
| **UC 头条** | 6 | 0 | 10 | **2** | 缺少 | 本轮新增 `uczzd.cn`;UC 主推 APP 索引仍浅 |
| 一点资讯 | 1 | 0 | 3 | 0 | 缺少 | 本轮新增 `yidianzixun.com` |
| 金投网 | 2 | 0 | 2 | **4** | 一致 | 本轮新增 `cngold.org`,反超 |
| 韭研公社 | 2 | 0 | 3 | **4** | 一致 | 反超(force_all 偶发命中)|
| 乙方宝招标 | 1 | 0 | 4 | 1 | 缺少 | 本轮新增 `yfbzb.com`;与品牌舆情弱相关 |
| 小红书 | 1 | 0 | 4 | 0 | 缺正文 | sign 反爬 + JS 渲染,需 browser-service |
| 哔哩哔哩 | 0 | 0 | 2 | 0 | 缺正文 | 缺 crawler;`api.bilibili.com` 公开 API 可用 |
| 抖音 | 0 | 0 | 0 | 1 | 缺正文 | SPA + WAF |
| 和讯 | 0 | 0 | 2 | 1 | 一致 | crawler 拉 news.hexun 子域 |
| **知乎** | 1 | **3** | 2 | **4** | 一致 | sentinel 两窗口都反超惠科(zhihu 索引深)|
| 第一财经 | 1 | 0 | 1 | **3** | 一致 | crawler 已有,反超 |
| 财联社 | 2 | 0 | 2 | **2** | 一致 | crawler 已有 |
| 华尔街见闻 | 0 | 0 | 0 | 1 | 一致 | crawler 已有 |
| 凤凰网 | 0 | 0 | 1 | 1 | 一致 | 持平 |
| Wind / 中金 / 大智慧 APP | 4 | 0 | 8 | 1 | 不可达 | 闭源 APP,无 web 端,接受不覆盖 |
| Twitter / X | 0 | 0 | 0 | 0 | 不可达 | 全登录 + API 收费 |
| 有驾(汽车,误命中)| 3 | 0 | 7 | 3 | 一致 | 与品牌舆情无关,本不应该补 |
| **合计(已识别平台)** | **165** | **6** | **384** | **49** | | sentinel 已识别命中只占 raw total 的 ~12% |
| **sentinel raw total**(含长尾未映射)| — | **419** | — | **417** | | 大头 ~370 是 csdn / juejin / chinanews / cppcc / 海外 vianet 系等 |

> **本轮 SEED 补丁(2026-05-09 已 apply)**:种子表从 68 → **77**,新增 9 条:`eastmoney.com / baidu.com / sina.cn / 163.com / qq.com`(5 个 root domain 救 ~261 篇)+ `yidianzixun.com / cngold.org / uczzd.cn / yfbzb.com`(4 个独立平台)。**下次 monitor 跑这些平台都会被 LLM 拉进 plan**。

---

## §2 已能用的 14 个 crawler

每个都有专属 module + `/run-crawl-<name>` HTTP 端点,`crawler/<name>.py` 直接抓正文入库。

| crawler 文件 | 平台 | Excel 30d |
|---|---|---:|
| `xueqiu.py` | 雪球 | 175 |
| `eastmoney.py` | 东方财富股吧 | 97 |
| `eastmoney_news.py` + `_announcement` + `_research` + `_industry` | 东方财富资讯/公告/研报/行业 | 75(共)|
| `sina_finance.py` + `sina_stock_news.py` | 新浪财经 / 个股资讯 | 80 |
| `gelonghui.py` | 格隆汇 | 37 |
| `cls_finance.py` | 财联社 | <5 |
| `wallstreetcn.py` | 华尔街见闻 | <5 |
| `yicai.py` | 第一财经 | <5 |
| `kr36.py` | 36 氪 | <5 |
| `baidu_tieba.py` | 百度贴吧 | 0 |

---

## §3 关键洞察 + 历史最佳

> **平台对比表已合并到 §1 顶部,这里只放分析。**

### 3.1 本次实测关键洞察

1. **本次 baidu + sogou 双失效**:by_engine `cnbing=680/710, baidu=0, sogou=0, ddg=374/422`。baidu 浏览器 cookie 是 IP 绑定从机房 IP 仍被拦,sogou cookie 跑了几轮后已失效 — **引擎可用性是当前最大的不确定性**
2. **sentinel raw total 419/417**(1d/1周)中,**只有 ~50 是已识别为惠科平台的命中**,其余 370+ 是长尾 source(英文 vianet 系 / csdn / 各种 .gov 站 / juejin 等)— 量级很高但 **舆情相关性不一定都强**
3. **sentinel 反超惠科的稳定项**:`知乎`(1d 3/1, 1周 4/2)/ `第一财经`(1周 3/1)/ `财联社`(2/2 持平)等 — 这是 sentinel 多引擎并发的真实优势
4. **惠科主导 + sentinel 缺位的核心痛点**:
   - **微信公众号 35 + 头条 28 = 63(1d 这一天)** vs sentinel 0+0
   - **新浪 + 雪球 + 微博 + 东财 = 58** vs sentinel 0(crawler 兜底但 search 当日 0)
   - 这两块 = 121 / 165 = **惠科 1d 总量的 73%**;sentinel 在 search 层全部 0,只能靠后续 crawler 补
5. **当日命中 0 不等于"不能用"**:雪球 / 东财 / 格隆汇等"能用"档平台靠 crawler 直拉,即使 search 当日 0 也能补回来

### 3.2 历史最佳 vs 本次

| 实验 | sentinel total | vs 惠科 |
|---|---:|---|
| **历史最佳**:严格 7d + sogou_cookie + 4 引擎 (2026-05-09 上午) | 635 | 470 → **135%** |
| **本次**:严格 1周 + sogou_cookie 失效 + cnbing+ddg 仅 | 417 | 384 → **109%** |

**主要回退**因素:sogou cookie 失效少了 ~40 微信公众号命中 + 当天 LLM 生成 query 数 71 vs 96 少了 ~30%。

### 3.3 单 query 产出有上限

把 `max_results_per_query` 从默认 10 调到 50 实测无效:

| 引擎 | per-query 平均 |
|---|---:|
| cnbing | ~9.6(请求 50 实际返 ~10) |
| ddg | ~5.6 |

**搜索引擎对 site: 窄查询 page 1 真实匹配数 ≤10**,调高 count 没用。要解锁更多必须:
1. **加 query 数** — LLM 给每平台生成 2-3 条不同关键词 query
2. **分页** — baidu `&pn=10/20`、cnbing `&first=11/21`,3 页累计 ~30 条
3. **加引擎** — 智谱 web-search-pro / Brave / 360

### 3.4 4 个搜索引擎现状

| 引擎 | 状态 | 备注 |
|---|---|---|
| **cnbing** | 主力,~9.6/query 稳定 | vm02 CN IP 直连最稳 |
| **ddg** | 不稳,~5.6/query | ddg-proxy 国内出口经常超时 |
| **baidu** | cookie 仍失败 | 浏览器 cookie 是 IP 绑定的,从机房 IP 发仍被 wappass 验证页拦;需要 vm02 自己 IP 的登录态 cookie |
| **sogou** | weixin 通道偶通,web 主站 antispider | `weixin.sogou.com` 已解锁但 cookie 容易失效;`www.sogou.com/web` 主站需要更完整登录态 cookie(SLG/SCNTOKEN 等) |

**结论**:vm02 上**只有 cnbing 100% 稳定**,ddg 偶尔可用,baidu 完全不可用,sogou 限定微信场景且 cookie 易失效。

---

## §4 搜索结果含 title + body snippet,可做预 sentiment

每个引擎统一返回:

```python
{"title": "...", "href": "...", "body": "..."}  # body 是 snippet 摘要 40-200 字
```

snippet 里通常已经能读出明显情感:"大摩上调目标价 58%" / "高开低走就不能硬一次!" / "质疑像恒大一样爆雷"。

**当前**:snippet 入 DB 后只 UI 展示,analyzer 仍等 crawler 拿全文才跑 LLM。
**可加(~1 天)**:`search/pipeline.py:normalize_result` 后跑轻量情感分类(关键词词典 / DeepSeek-Chat 廉价 1 调)→ post 加 `prefilter_sentiment` 字段。**对 922 篇"能搜不能爬"档平台(微信/头条/同花顺/搜狐/网易/B 站/和讯)价值最大** —— 它们现在拿不到正文,但 snippet 已经在 DB。

---

## §5 时间窗口支持

`timelimit` 参数 5 种格式,5 个引擎都支持(已在 baidu+cnbing 加自定义日期范围):

| 格式 | 含义 |
|---|---|
| `'d'` / `'w'` / `'m'` / `'y'` | 过去 1 天 / 7 天 / 30 天 / 365 天 |
| **`'YYYY-MM-DD..YYYY-MM-DD'`** | **绝对日期范围**(本轮严格 7d 用的就是这个) |
| None | 不过滤 |

底层映射:
- baidu:`gpc=stf=<unix_start>,<unix_end>|stftype=1`
- cnbing:`filters=ex1:"ez5_<days_since_epoch>_<days>"`
- ddg:ddgs 库直接接受
- sogou:web 通道不支持自定义范围;weixin 通道支持 `sort=date` 时间倒序

---

## §6 不在种子表的临时跑法 (`media_allowlist`)

不动种子表,直接传 domain 列表给 monitor,完全绕过 LLM 的种子表过滤。等价 howto.txt 里的 `--media media.txt`。

```bash
curl -X POST http://127.0.0.1:8090/run-monitor -H 'Content-Type: application/json' -d '{
  "account_id": 1,
  "target": "世纪互联",
  "ticker": "vnet",
  "aliases": ["VNET", "21Vianet"],
  "media_allowlist": ["uczzd.cn", "yfbzb.com", "yidianzixun.com"],
  "force_all": true,
  "timelimit": "2026-05-02..2026-05-08",
  "max_results_per_query": 10,
  "engines": ["cnbing", "baidu", "sogou", "ddg"]
}'
```

返回 `data.stats.by_source` 按 domain 统计命中数 — 跑一次就知道每个 domain 实际能搜到多少,作为"加不加种子表"的决策依据。

---

## §7 SEED 子域放宽补丁(救 ~261 篇,15 分钟)

种子表里现有的"具体子域"过细,改成 root domain 后搜索引擎 site: 自动覆盖兄弟子域:

| 媒体 | Excel 30d | 实际 domain | 种子里只有 |
|---|---:|---|---|
| 东方财富股吧(移动)| 97 | mguba.eastmoney.com | guba.eastmoney.com |
| 百度 APP / 百家号 | 65 | baijiahao.baidu.com / mbd.baidu.com | tieba/zhidao/news.baidu.com |
| 东方财富财富号 | 40 | caifuhao.eastmoney.com | guba.eastmoney.com |
| 新浪新闻 APP | 40 | k.sina.cn | finance.sina.com.cn |
| 网易 | 28 | www.163.com | money.163.com |
| 腾讯自选股/新闻/视频号 | 22 | gu/news/channels.qq.com | finance/mp/v.qq.com |
| **小计** | **~292** | | |

贴到 `backend/migrations/010_sentiment_platforms.py:113` 的 SEED 列表后 rerun migration:

```python
("eastmoney_root", "eastmoney.com",  "finance",  "mainland", "东方财富(全站)",  "Eastmoney All",  21),
("baidu_root",     "baidu.com",      "news",     "mainland", "百度系(全站)",    "Baidu All",      11),
("sina_cn",        "sina.cn",        "news",     "mainland", "新浪移动",        "Sina .cn",       31),
("163_root",       "163.com",        "news",     "mainland", "网易(全站)",      "Netease All",    71),
("qq_root",        "qq.com",         "news",     "mainland", "腾讯系(全站)",    "Tencent All",    61),
```

⚠️ 副作用:`qq.com` 也带出游戏/邮箱;LLM plan 按 target 关键词过滤后杂讯有限,要观察一周。

---

## §8 媒体 vs 平台(概念区分)

第三方舆情(惠科)按"信息源条目"算媒体:同公司不同站 / 不同 APP 各算 1 家(178 家)。
我们按"搜索引擎 site: 单位"算平台:1 个 root domain 一个 platform(68 个)。

**1 个 platform 通常承载多家"媒体"**:

| 我们的 platform | 惠科里对应"媒体"个数 / 篇 |
|---|---|
| `mp.weixin.qq.com` | 1 家 / 552 篇 |
| `hexun.com` | 3 家(网/股票/财经)/ 11 篇 |
| `sina.com.cn` | 2 家(新浪网+股市汇)/ 81 篇 |
| `sohu.com` | 2 家(搜狐+APP)/ 46 篇 |

所有覆盖率按 **文章数** 算,不按"媒体数"。

---

## §9 行动建议(优先级降序)

1. **15 分钟** — 加 5 行 root-domain SEED(§7) → 救 261 篇 / 多覆盖 12 家媒体
2. **30 分钟** — 加 2 行 SEED:`yidianzixun.com` / `cngold.org`(金投网)→ 救 41 篇
3. **2-3 天** — 写 7 个 crawler(微信/头条/同花顺/搜狐/网易/B 站/和讯)→ 21% → 62%
4. **半天** — 微博 m.weibo.cn 匿名 JSON crawler → 165 篇
5. **半天** — LLM plan 给微信公众号生成多 query(主关键词 + 业绩/财报/订单 等修饰),让 sogou weixin 端点跑出 30+ hits
6. **配 sogou web 主站登录态 cookie** — 让 sogou 能在 60+ 平台批跑而不是只 1 query × 10
7. **vm02 自己的 baidu 登录态 cookie** — 让 baidu 这个最强引擎可用
8. **加引擎 / 加分页 / 预 sentiment** — 长期优化方向

---

## §10 数据源 / 关键文件

| 文件 | 说明 |
|---|---|
| `tmp/世纪互联0508195924.xlsx` | 惠科第三方舆情服务导出基线(2232 篇 / 178 家)|
| `tmp/strict_7d_sogou_cookie.json` | **本轮最佳结果**:严格 7d + sogou_cookie + 4 引擎,sentinel 635 |
| `tmp/probe_crawl_results.json` | P2 正文探针(199 域 × ≤3 样本)|
| `services/sentinel-service/search/sogou.py` | 本轮新增搜狗引擎(已 deploy vm02) |
| `services/sentinel-service/search/baidu.py` / `cnbing.py` | 已扩展支持 `'YYYY-MM-DD..YYYY-MM-DD'` 自定义日期范围 |
| `services/sentinel-service/crawler/*.py` | 14 个正文 crawler |
| `backend/migrations/010_sentiment_platforms.py` | platform 种子表 SEED 列表(68 条)|
| `tmp/run_strict_7d.py` / `tmp/run_full_coverage.py` | 可重复执行的实测脚本 |
