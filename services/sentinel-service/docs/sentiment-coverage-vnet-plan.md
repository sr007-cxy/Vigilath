# 世纪互联 Excel × 现有 monitor 覆盖率诊断

## Context

用户拿到第三方舆情服务导出的 `tmp/世纪互联0508195924.xlsx`(2232 篇 / 178 家媒体 / 10 类),想**验证我们当前的 sentinel-service monitor 流程对这些媒体的实际覆盖能力**。

**本计划只做诊断,不写新代码、不新增 crawler**。产出是一张"按平台/媒体维度的对照表",回答 3 个问题:
1. 当前 monitor **能不能搜到该媒体的文章链接**(search 命中)
2. 命中的链接**能不能进一步拿到正文**(crawl 命中)
3. 拿不到的,**根因是什么**(平台不在种子表 / 搜索引擎索引浅 / 登录墙 / WAF / 闭源 APP)

按用户优先级:**先跑主流(微信 / 微博 / 抖音 / 快手 / 小红书 / Twitter / 哔哩哔哩 / 今日头条),再跑其他金融 + 综合媒体**。

---

## 一、当前 monitor 长什么样(基线认知)

实际入口 = sentinel-service `POST /run-monitor`(`services/sentinel-service/service.py:293`),由 backend `sentiment_pipeline.py:run_pipeline_for_account` 调度;接受 `target / ticker / media_allowlist[] / engines[] / timelimit`。流程:

```
plan.py(LLM 生成 site:domain 查询)
  → search/pipeline.py(Baidu + cnbing + DDG 三引擎并发)
  → 入 sentinel-service SQLite posts 表(URL/title/snippet)
  → backend 侧 ThreadPool 并行调用 13 个 crawler 拿正文
  → analyzer/brief
```

**已有正文 crawler(13 个)**:`xueqiu / eastmoney(股吧) / eastmoney_news / eastmoney_announcement / eastmoney_research / eastmoney_industry / sina_finance / sina_stock_news / cls_finance / gelonghui / wallstreetcn / yicai / kr36 / baidu_tieba`。

**只有 search 命中、无正文 crawler 的平台**:其余所有平台(微信 / 微博 / 抖音 / 小红书 / B 站 / 头条 / 同花顺 / 搜狐 / 网易 / 百家号 / UC / 一点 / 腾讯自选股 / 和讯 / 知乎 / 贴吧 / 海外 …)。

**完全不在 `sentiment_platforms` 种子表的(连 site: 查询都不会生成)**:UC 头条、一点资讯、金投网、韭研公社、Wind、中金财富、大智慧、有驾、乙方宝。

---

## 二、跑诊断的方法(三个口径同时对照)

对一个候选媒体清单,跑 3 种 probe,看每一档命中了什么:

| Probe | 怎么跑 | 输出 |
|---|---|---|
| **P1 search** | `POST /run-monitor`,`media_allowlist=[domain]`,timelimit=`m`(放宽到 30 天),`max_results_per_query=20` | 落入 SQLite 的 URL+title+snippet 行数 |
| **P2 crawl** | 对 P1 命中的 URL,如平台有 crawler,触发对应 `/run-crawl-<platform>`;无 crawler 则跳过 | 入 `posts` 表带正文的行数 |
| **P3 直查** | 没在种子表的媒体,直接 `curl 'https://www.baidu.com/s?wd=site:<domain> 世纪互联'` 看 SERP 命中 | 验证"搜索引擎到底能不能索引这个站" |

**每条媒体的诊断分类**:

- ✅ **A 全通**:P1 ≥1 且 P2 ≥1
- 🟡 **B 仅链接**:P1 ≥1 但 P2 = 0(无 crawler 或 crawler 失败)
- 🟠 **C 浅索引**:P1 = 0 但 P3 ≥1(种子表里有,但搜索引擎对该站索引太浅)
- 🔴 **D 站不在册**:P1 = 0 且未在种子表(plan.py 不会生成 site: 查询)
- ⚫ **E 不可达**:P1 = P3 = 0(闭源 APP / 登录墙 / WAF 全封)

---

## 三、第一轮:主流(8 家),逐家诊断

按用户指定顺序跑,每家单独一次 `/run-monitor`(`media_allowlist=[domain]`)避免互相污染。

| 媒体 | Excel 篇数 | domain | 在种子表 | 有 crawler | **预测档位** | 卡点 / 解释 |
|---|---:|---|:---:|:---:|:---:|---|
| **微信公众号** | 552 | mp.weixin.qq.com | ✅ | ❌ | 🟡 B | 搜狗/百度对 mp 索引深;链接拿得到,**正文需新写 crawler** |
| **新浪微博** | 165 | weibo.com | ✅ | ❌ | 🟡/🟠 B/C | weibo.com 大部分内容登录后可见,搜索引擎索引浅;预计 P1 命中数远低于 Excel 165 |
| **抖音** | 14 | douyin.com | ✅ | ❌ | 🟠 C | 视频站文本字段稀疏,搜索引擎索引接近零;P1 多半 0 |
| **快手** | 0 | kuaishou.com | ✅ | ❌ | 🟠 C | 数据集中无样本,验证"是否有任何命中"作为基线 |
| **小红书** | 17 | xiaohongshu.com | ✅ | ❌ | 🟡 B | 有 SERP 命中,正文页 SSR 部分 + JS sign 反爬 |
| **Twitter / X** | 0 | twitter.com | ✅ | ❌ | ⚫ E | 国内出口 + DDG-proxy 可命中;但 x.com 全登录态,正文拿不到 |
| **哔哩哔哩** | 16 | bilibili.com | ✅ | ❌ | 🟡 B | search 能拿到 `/video/BV*` URL;正文(视频简介+评论)需新 crawler |
| **今日头条** | 283 | toutiao.com | ✅ | ❌ | 🟡 B | search 命中正常;`www.toutiao.com/article/<id>` SSR,正文拿得到但目前**无 crawler** |

**第一轮交付**:这 8 家的 P1/P2/P3 实测数 vs Excel 数对照表 + 每家档位结论。

---

## 四、第二轮:其他金融 + 综合媒体(按 Excel 体量降序)

| 媒体 | Excel 篇数 | domain | 在种子表 | 有 crawler | **预测档位** |
|---|---:|---|:---:|:---:|:---:|
| 雪球 | 175 | xueqiu.com | ✅ | ✅ | ✅ A |
| 东方财富股吧 | 97 | guba.eastmoney.com | ✅ | ✅ | ✅ A |
| 新浪 系 | ~120 | finance.sina.com.cn / k.sina.cn / news.sina.com.cn | ✅(主域) | ✅(部分) | 🟡/✅ 部分 A、移动子域 B |
| 百度系(百家号) | ~130 | baijiahao.baidu.com / mbd.baidu.com | ❌(只有 news.baidu.com) | ❌ | 🔴 D — 种子表只放了 news.baidu.com,百家号站不在册 |
| 有驾 APP | 55 | yoojia.com | ❌ | ❌ | 🔴 D — 与 target 无关,误命中 |
| 同花顺 | 49 | 10jqka.com.cn | ✅ | ❌ | 🟡 B |
| UC 头条 | 75 | uczzd.cn / a.mp.uc.cn | ❌ | ❌ | 🔴 D |
| 东方财富网/财富号 | 75 | eastmoney.com / caifuhao.eastmoney.com | ✅ | ✅(news/ann/research/industry) | 🟡 部分 A,caifuhao 子域可能漏 |
| 乙方宝招标 | 40 | yfbzb.com | ❌ | ❌ | 🔴 D — 招标站,与品牌舆情关系弱 |
| 格隆汇 | 37 | gelonghui.com | ✅ | ✅ | ✅ A |
| 搜狐 | 35 | sohu.com | ✅ | ❌ | 🟡 B |
| 网易 | 28 | 163.com | ✅(money.163) | ❌ | 🟡 B |
| 金投网 | 24 | (待补 cngold.org) | ❌ | ❌ | 🔴 D |
| 小红书 | 17 | xiaohongshu.com | (第一轮已跑) | | |
| 腾讯自选股 | 17 | gu.qq.com | ❌(只有 finance.qq.com) | ❌ | 🔴/🟡 — 看 finance.qq.com 是否能命中 |
| Wind APP | 17 | (闭源 APP) | ❌ | ❌ | ⚫ E |
| 一点资讯 | 17 | yidianzixun.com | ❌ | ❌ | 🔴 D |
| 哔哩哔哩 APP | 16 | (第一轮已跑) | | | |
| 抖音 APP | 14 | (第一轮已跑) | | | |
| 中金财富 APP | 13 | (闭源 APP) | ❌ | ❌ | ⚫ E |
| 韭研公社 | 11 | jiuyangongshe.com | ❌ | ❌ | 🔴/⚫ |
| 大智慧 APP | 10 | (闭源 APP) | ❌ | ❌ | ⚫ E |
| 和讯 | 9 | hexun.com | ✅ | ❌ | 🟡 B |

**第二轮交付**:同口径表;并把"搜得到但拿不到正文"的 B 档与"搜不到"的 C/D 档分开统计,得到一个总命中率(估计:链接 ~50%,正文 ~25%)。

---

## 五、执行步骤(只读 / 调用现有 endpoint,不改代码)

1. **启动 sentinel-service + backend**(假设已在跑;若未跑由用户手动起,我不动 service):
   ```
   curl http://localhost:8788/health    # sentinel-service
   curl http://localhost:8000/health    # backend
   ```
2. **逐家跑第一轮 8 个 probe**(脚本化,序列执行,避免抢搜索引擎额度):
   ```
   for d in mp.weixin.qq.com weibo.com douyin.com kuaishou.com \
            xiaohongshu.com twitter.com bilibili.com toutiao.com; do
     curl -s -X POST http://localhost:8788/run-monitor \
       -H 'Content-Type: application/json' \
       -d "{\"target\":\"世纪互联\",\"aliases\":[\"VNET\",\"21Vianet\"],\"ticker\":\"VNET\",\"media_allowlist\":[\"$d\"],\"timelimit\":\"m\",\"max_results_per_query\":20,\"engines\":[\"baidu\",\"cnbing\",\"ddg\"]}'
   done
   ```
3. **查 SQLite 看每家的命中数**:
   ```
   sqlite3 services/sentinel-service/data/account_2/sentinel.db \
     "SELECT json_extract(meta,'$.domain') AS domain, COUNT(*) FROM posts
      WHERE created_at > datetime('now','-1 hour') GROUP BY domain;"
   ```
4. **对未在种子表的媒体**(P3 直查):
   ```
   for d in baijiahao.baidu.com uczzd.cn cngold.org yidianzixun.com \
            jiuyangongshe.com gu.qq.com; do
     curl -s "https://www.baidu.com/s?wd=site:${d}+%E4%B8%96%E7%BA%AA%E4%BA%92%E8%81%94" \
       | grep -oE 'mu="[^"]+' | head -3
   done
   ```
5. **对 B 档做 1 个 URL 的 fetch 试探**:取 P1 命中里前 3 条 URL,直接 `curl -A 'Mozilla/5.0…' <url>`,看返回大小 + 是否含正文 selector(`<article>` / `class="content"`)。判断"正文拿不到"是 WAF 还是 JS 渲染。
6. **整合输出**:把 8(主流)+ 22(其他)= 30 家媒体写成最终对照 Markdown,每家一行 `[档位] 媒体名 | Excel=N | P1=x | P2=y | P3=z | 解释`。

---

## 六、关键文件(只读参考,不改)

| 用途 | 路径 |
|---|---|
| monitor endpoint 实现 | `services/sentinel-service/service.py:293` |
| 平台种子表 | `backend/migrations/010_sentiment_platforms.py:37–113`(68 条) |
| LLM plan + media 过滤 | `services/sentinel-service/search/plan.py:159` (`只能为这些域名生成 site:`) |
| 三引擎执行 | `services/sentinel-service/search/pipeline.py` |
| 13 crawler 列表 | `services/sentinel-service/crawler/__init__.py` |
| backend pipeline | `backend/geo/services/sentiment_pipeline.py:65–200` |
| posts SQLite 表 | `services/sentinel-service/data/account_2/sentinel.db` |

---

## 七、最终交付物

**一份 Markdown 报告**,放在 `tmp/sentiment_coverage_vnet_0508.md`,含三段:

1. **总览**:30 家媒体的档位分布柱状(A/B/C/D/E 各几家、各占 Excel 多少篇)
2. **第一轮主流 8 家详表**:每家 P1/P2/P3 实测数 vs Excel 篇数,卡点根因
3. **第二轮其他 22 家详表**:同口径
4. **建议**:哪些"该被加进 `sentiment_platforms` 种子表"(站在册了搜索引擎才会去查),哪些"该写正文 crawler"(已经能搜到但拿不到正文的),哪些"放弃"(闭源/登录墙/与 target 无关)

不输出代码改动建议的优先级排序(那是下一轮的事)。
