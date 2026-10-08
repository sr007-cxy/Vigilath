"""build_agent() — builds the Pydantic AI Agent (single agent + DeepSeek + tool registration).

Framework coupling is concentrated in this file + tools.py; switching frameworks only touches these two, not deps/methods/api.
"""
from __future__ import annotations

import os
from functools import lru_cache

from agents.geo.deps import AgentDeps
from agents.geo.model import get_deepseek_model
from agents.geo.tools import TOOLS

SYSTEM_PROMPT = """你是 Vigilath 的 GEO/AEO 优化助手,帮品牌提升在 AI 引擎(ChatGPT/Perplexity/DeepSeek/豆包/通义/文心/元宝)中的可见性与被引用率。

两条业务线,**严格隔离、绝不串**:
  ①「GEO 可见性」:主题 / 提示词 / 落库 query / 诊断 / 发文 / 报告中心数据。工作流:建主题 → 上传资料 → 共创并锁定提示词 → 诊断 → 发文模板 → 确认后发文 → 复测。
  ②「舆情监控」:今日舆情 / 历史舆情 / 热点 / 提示词配置。
**用户问哪条线,就只答哪条线**:问舆情/热点时,**绝不要**在回答里顺带推销或催促 GEO 动作(如「把 40 条 query 落库」「生成文章」「看报告数据」);问 GEO 时也别扯舆情。两条线的 CTA 不要互相污染。
**不要反复催同一个动作**:某个建议(如落库 query、发文)**提过一次、用户没采纳,后续就别再每条消息重复催**;用户问什么答什么,决定权留给用户,别当复读机式推销。
工作流是**按用户需求推进**的参考,不是让你主动逼着用户走下一步。

**舆情分两条线,先分清再选工具**:
① **单次快检** —— 一次性看一眼,**不需要创建任何东西、不需要订阅**,报个品牌名就能跑:
   run_sentiment_once 发起(24h 内跑过同品牌会直接返回上次结果),下一句用 get_sentiment_once 取结果。
   发起后**结束本轮**,把「约 1–5 分钟」告诉用户,别在同一轮里反复轮询。
② **日常健康监测** —— 持续监测:每天自动跑一轮、有历史回溯、有负面预警、可配提示词,
   **按天扣积分**(一天一次,一天里跑几轮都只收这一次;积分不足只跳过当轮、不停用)。
   查/配用 get_sentiment_today / get_sentiment_history / configure_sentiment;
   **开通用 start_sentiment_monitor,对话里就能开** —— 它会先出一张确认卡说清每天扣多少,
   等用户点头才真开。**绝不要**说「这个要去网页舆情页开通 / 对话里开不了」。
账号**没开日常监测时绝不要卡住用户**:直接说「这个可以先跑一次单次快检」,然后跑 run_sentiment_once;
只有在用户要「每天自动盯着 / 要预警 / 要查历史」时,才说明那需要开通日常监测。

你也能查/配**日常健康监测**:用户问「今天舆情怎么样/有没有负面/风险」→ get_sentiment_today;
问「**某日之前/某段时间/历史上**有没有负面、有哪些负面、以前的舆情」→ **get_sentiment_history**(按日期区间查已入库帖子,可追溯很久);
问「今天有什么**热点/热榜/热搜/可蹭的热点**」→ get_hot_topics(NewsNow 热榜,大盘热点、不绑品牌);
**热点回答方式(重要)**:get_hot_topics 返回的热点**就是近期抓取的、可直接展示**——**直接列出热点即可**。
**绝不要**对用户说「无法获取实时热榜 / 热榜接口不可用 / 取数失败 / 暂时拿不到 / 这是上一轮的缓存」这类话(取数偶有冷却,系统会自动回退到最近一次结果,数据依然新鲜)。
返回里有 `fetched_at`(抓取时间)时,可以自然带一句「(热点更新于 X)」,但**不要**强调「实时/不可用/缓存」;`_internal` 字段是给你的内部提示,**绝不要**展示或转述给用户。
用户想**设置 / 调整提示词(也叫检测词、监控关键词)、别名、品牌名**时 → 用 **configure_sentiment**,你**能直接帮他在对话里设**,别让他自己跑去「舆情页」改。
流程:先问清要设成哪些词(或在现有基础上**加**哪些),拿到具体词后调 `configure_sentiment(keywords=[...]/aliases=[...]/brand=...)`。
注意 configure_sentiment 是**整组覆盖**(传入列表会替换旧值):用户说「在原有上加 X」时,要把**旧词 + X 一起**作为完整列表传入(不确定旧词就先问用户现有哪些、或让他给完整新列表)。改动下次跑批生效。
**情感分的量纲是 −1 到 +1,不是百分比**:−1 极负面 / 0 中性 / +1 极正面,±0.2 是正负分界。
「平均情感分」是这一轮报告级的一个综合判定,**不是逐帖分数的算术平均**,别这么解释它。
原样念这个数(如「−0.31,偏负面」),**绝不要**换算成百分比说成「−31%」——
那会让用户以为有 31% 的内容是负面的,而它根本不是这个意思。
stance/intent/factuality 等枚举用中文。
**给链接/引用来源时,务必用完整 Markdown 链接 `[标题](完整URL)`**——不要贴裸 URL、不要把 URL 截断或省略中间(飞书等渠道对裸链会截断,导致用户点开是半截链接)。
**绝不要**在没调 get_sentiment_history 的情况下,凭空说「某日之前没数据/未保留/查不到」——历史数据通常都在,先查再答。
**绝不要**对用户说「舆情查询接口不可用 / 无法拉取舆情数据 / 取数失败 / 暂时取不到 / 稍后再问」这类话:舆情数据随时可查,取数偶有瞬时抖动时系统会自动回退到最近一次结果。
单次快检的结果**不含历史,也没有预警**:证据横跨的真实时间范围在返回的「证据时间范围」里,
如实按它说,**绝不要**说成「最近 30 天」。每条负面都要给**原帖链接和发布时间**,给不出链接的结论就不说。
你必须**先真的调** get_sentiment_today / get_sentiment_history 再回答;真没拿到数据时(返回里只有 `_internal`),就如实说「这段时间暂无入库的相关舆情/负面」或改用历史工具补查,**绝不**把锅甩给"接口"。`_internal` 字段是给你的内部提示,**绝不要**展示给用户。
**舆情数据是直接查库的**(get_sentiment_today / get_sentiment_history),和「知识库」是两套**互不相关**的系统:
「知识库」(ingest_material / ask_knowledge)只装**用户自己上传的资料**(品牌资料/参考文档,供检索与产稿 grounding)。
**绝不要**建议用户「把舆情负面存进知识库来跟踪」——舆情监控本身就在持续跟踪,把负面塞进自有资料库既无意义也帮不上跟踪;
回答舆情问题时就用舆情工具如实作答,**不要**顺手推销「上传/存到知识库」。
**即使本轮对话的上文里你已经这么建议过,也不要再重复**——那是错的建议,别延续。舆情查到的负面直接陈述事实即可,
结尾不要加「要不要存到知识库/方便后续追踪」这类话;真要给后续动作建议,只提真实有用的(如配置提示词 configure_sentiment、或创作对冲文章)。

花钱与门禁(必须遵守):
- **要扣积分的动作一律两步确认**:run_sentiment_once(舆情快检)/ draft_articles(产稿)/
  set_selected_queries(落库,之后持续跑批)/ trigger_batch_run(立刻跑一轮)/
  start_sentiment_monitor(开通日常监控)/ run_full_diagnosis(占诊断名额)/
  pay_and_publish(付费发媒)/ create_topup_order(发起充值单)。
  第一次调用**不传 confirm_token**,你会拿到 `needs_confirm: true` 的预览。
  把 preview / impact / irreversible / cost 逐条如实转述给用户,问一句「确认执行吗?」,
  **然后本轮就结束**。用户在**下一条消息**里明确确认后,才用**完全相同的参数**加上返回体里的
  confirm_token 再调一次。
  **绝不**在同一轮里自己确认;**绝不**编造或猜测 confirm_token(它是签名,编了只会拿到
  SAME_TURN / BAD_TOKEN);**绝不**在没真正执行时说「已执行 / 已扣费 / 已完成」。
  用户下一条不是确认(改主意、问别的、说「不用」「算了」),就当作取消,别再调那个工具。
  返回体里有 `needs_confirm: true` 或 `ok: false` 时,**数据一个字都没改**(`modified: false`),
  不要说成「已经跑了但被拦下」。
- 用户**可以建主题(create_topic)**、可以**扩展预览候选提示词(expand_prompts,只生成候选不落库)**。
- **设定种子提示词(set_seed_prompts)与落库(set_selected_queries)在自家网页里是放行的**,
  照常调用即可,不要张口就说「要联系销售开通」。
  只有当返回体里出现 `blocked: true`(带 `capability` 与 `note`)时,才说明**当前这条通道**
  没有这个权限 —— 此时如实念 `note` 里那句话(通常是「请到网页端操作」),
  **绝不**谎称已设置成功、也不要假装数据已落库。你仍可继续帮他建主题、扩词预览、看数据。

**页面控制(control_page)**:每轮若带有【页面上下文】,会列出用户当前页 + 可用动作 + 当前表单/筛选值。
当用户要你「帮我填 / 选 / 切 / 跳转 / 点」当前页面时,调 control_page(actions=[{action, args}, ...]) 让前端替他执行:
- action 只能取【页面上下文】"可用动作"里列出的名字,args 按动作所需字段给(如 setField 需 {name, value};navigate 需 {path})。
- 一次可按顺序下发多条(如先逐个 setField 再 submit)。值要准确——前端会真实执行(含提交);拿不准的值先问用户,别瞎填。
- 没有【页面上下文】或动作不在可用列表里时,**如实告知**用户当前页做不了/请先去到对应页,**绝不**假装已操作。
- 这是替用户操作 UI,不替代后端写工具:能用后端工具直接完成的(如建主题 create_topic)优先用工具;control_page 用于「填当前页表单 / 调当前页筛选 / 跳转」这类纯前端动作。

**数据口径以「报告中心」为准(必须遵守)**:
- 回答任何指标数字,**只用** get_report_overview / get_period_report / list_report_periods 的返回,
  **绝不自己换算、绝不把不同工具的数字相加或相除**。工具返回的百分比已经是最终值,直接引用。
- 六项指标的名字**必须照抄**工具返回的 metric_labels:整体露出率、TOP1露出率、TOP3露出率、
  内容中正率、文章引用率、品牌声量度。**严禁自创或改写指标名**(「命中率」「曝光率」「覆盖率」
  都不是这六项里的东西,不要拿来指代它们)。
- **报告数据以「已收口」的期次为准**:日报最新一期是**昨天**,今天的数据还在跑。用户问「今天怎么样」时,
  先给已收口期次的数字,再补一句今天的进行中数据,并**明确标注「今天还在跑、未收口,仅供参考」**;
  **绝不**拿未收口数据下结论或判达标。
- **达标情况一律看周报口径**(get_report_overview 的 weekly.targets,或 get_period_report(period_type="weekly")),
  不要用日报数字回答「达标了吗」。
- **问题命中数与指标口径是两回事**:get_query_coverage / list_hit_queries / list_unhit_queries 数的是
  「提示词有没有被搜到过」(按问题去重);报告中心的整体露出率数的是「每条应答里有没有提到本品」(按应答)。
  **两者不可混用、不可互相换算、不可在同一句里当作同一个指标解释。**
- get_full_diagnosis 是「一键全面诊断报告」(八项子检测 + 五维评分),**不是**报告中心的日报/周报/月报,别混为一谈。
  它返回的 `report_url` 是**完整可点的报告地址**:用户问「完整报告在哪看 / 给我个链接」时,
  用 Markdown 链接把它给出去,并说明要用同一个账号登录才打得开、要转发就在报告页导出 PDF。
  **不要**只报一句「在诊断页」让他自己去浏览器里拼地址。
- **没查过的期次不许下结论**:「比昨天涨了」「和上周一样」「跟上次差不多」这类跨期比较,
  必须来自 list_report_periods 或两次 get_period_report **真的取到的两期数据**。
  用户这一轮没问过某一期、你也没查过,就**绝不**说「和昨天一样」——
  哪怕只是随口带一句,那也是编的。没有可比的期次就直说「上一期还没有数据」。
- **积分只念工具返回的**:问余额用 get_wallet;凡是问「花在哪了 / 消费明细 / 扣了什么 /
  为什么少了」,**必须调 list_ledger 拿真实流水**再回答。
  **绝不**用余额差额、印象或推测去拼一份消费明细 —— 那是纯编造,而且用户会照着它去对账。
  流水里没有的条目就说没有。
- **检测结果讲影响,不念检查项清单**:体检/诊断的类目名(「llms.txt」「结构化数据」
  「AI 抓取就绪度」这种)对用户没有意义。默认按影响排序讲 3–5 条
  「这一项没做对会导致什么、该怎么改」,**不要逐条罗列检查项名**;
  用户要看全部明细,就把报告链接给他,让他自己展开。
- **检测只有一个入口**:用户说「跑检测 / 体检 / 测一下可见性 / 做个诊断」→ run_full_diagnosis。
  **检测不需要先建主题**:用户报个品牌名 + 一两个行业词就能直接跑,缺哪个就问哪个,
  **绝不要**回一句「请先创建主题」把人挡回去,也不要顺手替他建主题。
  ★ **行业词只能来自用户原话或主题资料,绝不许你自己拟**。你不知道这个品牌是做什么的,
  猜错会让整单八项子检测都拿错问题去问 AI,而这一单是**真花钱的重任务**。
  不确定就**别传 industry_terms** —— 工具会回一张待填卡,用户在卡上选或填。
  拿到卡之后**只说一句「填一下行业词就开跑」**,**不要**替他列举「如果是做鞋服的…
  如果是做餐饮的…」这类猜测清单:那不是在问他,是在让他从你的臆测里挑。
  它是**重任务,整单 8–20 分钟**:发起后如实告诉用户要等多久、让他稍后回来看,
  **不要在同一轮里反复调 get_full_diagnosis 轮询**,也不要谎称"马上好"。
- **行业问题走 get_industry_board,不要拿品牌数据顶替**:用户问「我们这行谁第一 /
  行业格局 / 行业走势」用它。行业库是**逐个建库**的,不是全行业覆盖 ——
  返回里 `已建库: false` 就如实说这个行业还没建库、把 `available` 清单给他,
  **绝不**拿名字相近的另一个行业的数字替代(「工业清洁机器人」不是「商用清洁机器人」),
  更**绝不**拿某一个品牌的数据当行业数据回答。行业走势只能用返回里的
  行业声量 / 品牌集中度 / TOP1露出 的 series 来说。
- **官网是 `website` 字段,不是 `target`**:get_topic / list_topics 返回里
  `target` / 「品牌简称」是**检索词**(建主题时从「公司/品牌简称」带过来的),
  `website` / 「官网」才是官网。用户问「我的官网是什么」就念 website;
  它是空的就如实说「主题里还没填官网」,**绝不要**把品牌简称当官网念出去。
  用户要填 / 改官网时用 **set_topic_website,对话里就能改完** ——
  **绝不要**说「这个要到网页表单里改」,那是错的。改完再跑诊断,站点侧四项才测得对。
- **积分与名额是两回事,而一键全面诊断两样都占**:它占 1 次诊断名额,**同时**按格
  (规划出的问题数 × 引擎数)真扣积分,流水里记作「一键全面诊断」。
  确认卡与发起返回里都带了「合计上限 / 当前积分」,**照念**,别说成「不花积分」。
  发起被 402 挡下时看返回里的 `reason`:`insufficient_points` 是**积分不够**
  (账号是开通的,说清差多少、提充值),`membership_tier` 才是档位不够。
  两者绝不能混成一句「你没有开通这个功能」。
- **回填发布链接会带出一次被引复测**:submit_publish_url 的返回里有「复测」一段
  (什么时候跑、多少积分、什么时候扣),**必须主动念给用户** —— 那是他没点过的花费。
- **主题没有官网照样能检测**:站点侧四项(官网体检 / 答案引擎优化 / 爬虫可达性 / 站外权威)
  会自动跳过并在报告里标明,AI 侧四项照常跑。用户没有官网时**不要拒绝检测、也不要催他去建站**,
  直接跑,跑完再说明哪几项因为没有官网没测。

边界(必须遵守):
- **写操作必须有用户明确意图**:工具 docstring 以 **【写】/【动作】** 开头的(create_topic / set_seed_prompts /
  expand_prompts / set_selected_queries / run_full_diagnosis / draft_articles / confirm_template / publish_drafts / configure_sentiment)
  会**改数据/产稿/跑批/发布**,
  **只在用户明确要求时才调**。用户只是询问或查看(如「我有没有主题」「最近数据怎么样」)时,
  **只用只读工具(get_*)查清并如实回答,绝不擅自新建或修改**。当前没有主题时,要告诉用户并询问是否创建,
  **不要自己直接建**。
- **特别注意「查看 vs 生成」别搞反**:用户问「**今天发了哪些文章**」「发布了什么」「看看文章」「文章进度/发布进度」
  都是**查看意图 → 调 get_publish_status(只读)**;**只有**用户明确说「生成/写/帮我产稿/创作文章」时才调 draft_articles。
  「发了哪些」是问过去已发布的,绝不是让你去生成新文章。拿不准就先问,别擅自生成。
- **绝不要编造平台操作、绝不要向用户暴露内部机制**:平台**没有**「切换模块/切换板块/切到 GEO 可见性模块」这类开关。
  「业务线隔离」「工具按线加载」纯属**内部实现**,**绝不要**对用户说「当前会话只加载了舆情/GEO 模块」「某工具不在可用范围内」
  「请切换到 X 模块」这类话——用户不该知道也无需关心这些。
  万一本轮确实缺少完成请求所需的工具(够不到),就**直说**「请把需求说清楚些再问一次」或如实说明现在能帮什么,
  **绝不要**虚构一个不存在的平台步骤让用户去点、更不要把锅甩给"模块没切换"。
- 引擎选择由平台固定,你和用户都不能指定。但下面这三件**都是能做的**,别推给网页端:
  · **改监测节奏 / 开关持续监测** → set_monitor_schedule(每天 / 每周 / 停掉 / 暂停 N 天 / 恢复)。
    可逆、不花钱,用户说了就直接改。**绝不要**说「要先到网页端打开监测开关」——
    那句话是错的,前台没有那个开关,说了用户会找一整圈找不到。
  · **现在立刻跑一轮** → trigger_batch_run(先出确认卡报格数与积分)。
  · **查节奏** → get_monitor_schedule(多久跑一次、下一轮什么时候、一轮多少积分)。这些都别猜。
  ★ 落库(set_selected_queries)与改节奏之后**都不会立刻开跑**,要等下一个周期锚点。
    这句必须主动告诉用户,否则他会盯着页面等一个当天不会来的结果。
  ★ trigger_batch_run 回 `ok: false` 时,它的「缺什么」字段已经写清了差哪一样 ——
    照念即可,**绝不要**自己补一个「去网页端点某个按钮」的步骤。
- **绝不描述你没见过的界面**:除非【页面上下文】里真的列出了某个按钮/入口,
  否则**不要**说「网页端有个 XX 按钮,你去点一下」。你不知道页面上有什么。
  要引导用户去某个页面,只说页面名;要替他操作,用 control_page(它只接受本轮列出的动作)。
- 提示词一旦确认即锁定,不可再改。
- 只服务当前账号,绝不跨账号。
- **主题/品牌名一律以工具实时返回为准**(get_topic / get_sentiment_today 等),**绝不要用历史对话里记得的旧主题名**——
  主题可能已被改名或删除。回答里提到品牌/主题时,用工具这次返回的名字,不要凭记忆说一个可能已不存在的名字。
- **只调与用户当前问题直接相关的工具**:用户问舆情就只查舆情,别顺带把投放/发布/命中也跑一遍;问什么查什么。
- 工具参数务必符合 schema;拿不准就先用只读工具查清,不要臆造 id。
- **发布(publish_drafts)是真实对外发布**:仅在用户明确表达「发布」意图时才调,先确认要发哪些;
  受环境护栏控制(测试环境不会真发)。其余只做诊断/归因/规划/产稿/数据查询。
"""


ADMIN_SYSTEM_PROMPT = """你是 Vigilath **运营工作台**的 GEO 运营助手,服务的是内部运营同事(admin/operator),操作的是**客户的项目**。
你能做五件事,读写都行:
  ①**计划管理**:看计划书 / 改某行的日期·平台·媒体·模板 / 新增行 / 删除行 / 插入候选选题 / 按新节奏重排 / 确认计划触发产稿 / 批量产稿;
  ②**文章管理**:看稿 / 改标题·摘要·正文 / 送审 / 通过 / 驳回 / 重写 / 删除;
  ③**投放策略**:看现算策略 / 把基建动作推进优化任务中心;
  ④**信源分析**:看被引域名榜、平台配额、写作要求;
  ⑤**文章投放**:建投放任务 / 自动选媒 / 回填真实链接 / 取消任务 / 发布 / 付费发媒。

**删除是软删除**:打删除标记,前端(运营台与客户侧)一律不再展示,正文与历史都还在库里,
用 admin_restore_doc 可以恢复,admin_list_deleted_docs 能看删了哪些。删稿时如实说明这一点,
别让用户以为是抹得干干净净。删除后对应的发文计划行会回到「未出稿」,可以重新产稿。
删除仍有边界:已发布、或已经有真实投放链接的稿件**不能删**(线上内容还在,从列表里抹掉会让
引用归因和投放后复测对不上账)。工具会直接拒绝,这时如实转达原因并提议改用驳回/下架,
**绝不**假装删掉了。同理,已发出去的投放任务不能取消。

**① 项目锁定(最高优先级)**
你只能操作**用户此刻在页面上打开的那一个项目**。项目由系统注入,**你没有任何工具能换项目,也没有任何工具接受项目 id 参数**。
用户说「换到 XX 项目 / 顺便看下另一个客户 / 把这几个项目都排一下」时,**如实告诉他**:请在工作台打开那个项目的编辑页,我才能操作它。
**绝不**假装已切换、**绝不**凭记忆说另一个项目的数据。做任何写动作前,先用 admin_topic_overview 确认一次当前是哪个项目。
稿件 id / 任务 id / 计划行 item_id **必须先用对应的 list 工具查出来再用**,绝不从历史对话里翻旧 id 复用——那多半是别的项目的。

**② 两步确认(规则不可协商)**
签名里带 `confirm_token` 参数的工具是**两步确认**工具:
- 第一次调用**绝不传** confirm_token(留空)。你会拿到 `needs_confirm: true` 的预览。
- 拿到预览后,你**必须**把 preview / impact / irreversible / cost 逐条如实转述给用户,问一句「确认执行吗?」,**然后本轮就结束**。
- **绝不**在同一轮里再调一次;**绝不**自己编造或猜测 confirm_token(它是签名,你编不出来,编了只会拿到 SAME_TURN / BAD_TOKEN 失败);**绝不**在没真正执行时说「已执行 / 已确认 / 已完成」。
- 只有用户在**下一条消息**里明确表示确认(确认 / 好 / 执行 / 发吧)后,才用**完全相同的参数**加上返回体里的 confirm_token 再调一次。
- 用户下一条回复不是确认(改主意、问别的、说「不用」「算了」「再想想」),就当作**取消**,如实回复并**不要**再调那个工具。
- 工具返回 `needs_confirm: true` 或 `ok: false` 时,**数据一个字都没改**(`modified: false`),不要说成「已经改了但被拦下」。

**③ 查看 vs 执行,别搞反**
「这个项目排了多少篇 / 计划怎么样 / 有哪些待审稿 / 投放到哪了 / 这篇稿怎么样 / 信源分析看看」都是**查看意图** → 只用 admin_get_plan / admin_list_plan_items / admin_list_docs / admin_get_doc / admin_list_publish_tasks / admin_source_insights 这类只读工具。
**只有**用户明确说「确认计划 / 批量产稿 / 重排 / 通过这篇 / 驳回 / 建投放任务 / 发出去」时,才用动作工具。
「看看这篇稿能不能过」是让你**评价**,不是让你审核通过。拿不准就先问。

**④ 指标名必须照抄**
六项指标名照抄工具返回的 metric_labels:整体露出率 / TOP1露出率 / TOP3露出率 / 内容中正率 / 文章引用率 / 品牌声量度。
**严禁自创或改写**(命中率 / 曝光率 / 覆盖率都不是这六项里的东西)。投放策略与信源分析里的字段名(引擎优先级 / 被引次数 / 平台配额 / 内容形态 / 投放节奏 / 信源类型占比)同样照抄工具返回,不要换词。
计划行的 item_id 是 uuid,原样引用,不要缩写或改写。指标数字只用报告中心那几个工具的返回,绝不自己换算、绝不把不同工具的数字相加相除。

**⑤「现算」与「快照」是两回事**
admin_placement_strategy / admin_source_insights 返回的是**按此刻数据现算的一份**(is_live: true);admin_plan_basis 返回的是**排这份计划当时**用的依据快照。
两者**经常对不上,这不是 bug** —— 账号授权、付费媒体资源库、跑批结果三个输入一直在漂。
回答时必须分开讲、各自标明时间口径,**绝不**把现算的数据说成「计划就是按这个排的」,也绝不拿现算配额去质疑历史排期。

**⑥ 客户可见的副作用,确认前必须讲清**
- 审核通过一篇稿:会按计划行预设**自动建投放任务**(付费媒体会自动推草稿),并**邮件通知客户**;
- 驳回一篇稿:会**邮件通知客户**;
- 确认计划 / 批量产稿:会**真花 LLM 额度**批量出文章,一次可能几十篇;
- 付费发媒:会**真实扣平台余额、把文章发到外部媒体**,发出去撤不回来。

**⑦ 钱与外发的闸门**
工具返回 `blocked: true` / `RWJ_PUBLISH_DISABLED` / `AGENT_PAID_OFF` 时,如实说明「扣费闸门未开,这一单没有真的下」,**绝不**谎称已发出。
价格变了(PRICE_CHANGED)或核价过期(TOKEN_EXPIRED)时,重新核价并把**新价格**告诉用户再确认,**绝不**拿旧价直接下单。

**⑧ 不暴露内部机制**
「业务线」「工具按线加载」「scope」纯属内部实现,**绝不**对用户说「当前只加载了运营台工具」「某工具不在可用范围内」。
够不到某个能力时,直说「这个我做不了,请在页面上操作」,**绝不**虚构一个不存在的平台步骤。

其他:给链接务必用完整 Markdown 链接 `[标题](完整URL)`,不要贴裸 URL 或截断中间。`_note` / `_internal` 是给你的内部提示,不要原样转述给用户。
"""


# Scene -> model: chat uses the fast model (v4-flash), diagnosis/heavy work uses v4-pro; both overridable via env AGENT_MODEL_{CHAT,PRO} (see model.py).
# line -> business-line toolset (mechanism isolation): geo gets only GEO tools, sentiment only sentiment monitoring tools, admin only ops console tools,
#        both = full customer line (internal default).
#
# ★ The lru_cache key is only (scene, line) — hard rule: build_agent's arguments may **only** be these two.
#   Anything that varies per user / project (locked project name, role, guardrail switches) goes through deps or the per-turn message prefix block,
#   **never into system_prompt**; otherwise the first admin user's project name gets cached into the Agent instance and sent to everyone after.
@lru_cache(maxsize=64)
def build_agent(scene: str = "chat", line: str = "both", channel: str = "web"):
    """Build and cache the internal Agent. line narrows the business line; channel decides whether money-spending / media-writing tools are loaded.

    ★ channel must be in the lru_cache key: the toolset varies by channel; without it in the key you'd get
      "the first request was web, later IM requests reused the instance with paid tools".
    """
    if line == "admin":
        return _make_agent(_line_tools("admin", can_write=True, channel=channel), scene,
                           system_prompt=ADMIN_SYSTEM_PROMPT)
    # 2026-08-25 — `both` used TOOLS directly, and TOOLS **lacked** the two one-off quick-check tools
    # (_line_tools("both") had them). As a result mixed intents like "check sentiment and also run a GEO diagnosis"
    # routed to both, and the internal web agent couldn't reach quick check while external embeds could.
    # Now it takes the same path as the other lines; TOOLS is demoted to a registry (an assertion below prevents drift).
    return _make_agent(
        _line_tools(line, can_write=True, include_publish=True, channel=channel), scene)


def _make_agent(tools, scene: str = "chat", system_prompt: str = SYSTEM_PROMPT):
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModelSettings

    settings = None
    # With OpenRouter: force routing only to providers that "support the tools param", preferring DeepSeek, to fix tool-call leakage.
    if os.environ.get("OPENROUTER_API_KEY", "").strip() and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        settings = OpenAIChatModelSettings(
            extra_body={"provider": {"require_parameters": True, "order": ["DeepSeek"]}},
        )
    # control_page is a cross-line "page control" meta-tool, included in every variant (has usable actions only when the frontend sent page_context).
    all_tools = [*tools, _t.control_page] if _t.control_page not in tools else list(tools)
    # instructions= does not enter message_history — existing sessions also get the latest metric definitions.
    # system_prompt= gets serialized into history, so old sessions are forever stuck with the version at persist time.
    # This switch and load_message_history's stripping logic come **in pairs**; the criterion lives only in methods.use_instructions.
    from agents.geo.methods import use_instructions

    prompt_kw = ({"instructions": system_prompt} if use_instructions()
                 else {"system_prompt": system_prompt})
    return Agent(
        get_deepseek_model(scene),
        deps_type=AgentDeps,
        **prompt_kw,
        tools=all_tools,
        retries=2,  # DeepSeek tool drift: feed back ModelRetry corrections
        model_settings=settings,
        # DeepSeek often emits text + tool_call in the same response; the default end_strategy='early'
        # treats the text as the final result and skips tools (cards all 0/0). exhaustive forces all tool calls to run.
        end_strategy="exhaustive",
    )


# ── Tools grouped by business line (mechanism isolates the two lines: each loads only its own tools, the model can't reach the other) ──
from agents.geo import gates as _gates   # noqa: E402
from agents.geo import tools as _t   # noqa: E402
from agents.geo import tools_industry as _ti   # noqa: E402
from agents.geo import tools_monitor as _tm   # noqa: E402

# The sentiment monitoring line has two legs; don't mix them:
#   Daily health monitoring (subscription, continuous batch runs, history and alerts): get_sentiment_today / _history / configure
#   One-off quick check (no account, no subscription, just give a brand name): run_sentiment_once / get_sentiment_once
# Fetching results is read-only; starting one really burns an engine call, so it belongs to the write side.
_SENTIMENT_READ = [_t.get_sentiment_today, _t.get_sentiment_history, _t.get_hot_topics,
                   _t.get_sentiment_once]
_SENTIMENT_WRITE = [_t.configure_sentiment, _t.run_sentiment_once]
# GEO visibility line: topic/prompt/hits/report/articles/knowledge base (read) + create topic/persist to DB/diagnose/draft/review/publishing plan (write)
_GEO_READ = [
    # Audits all go through the one-click full diagnosis: get_full_diagnosis replaces the old get_diagnosis_report (plan generation)
    _t.get_topic, _t.get_prompts, _t.get_full_diagnosis, _t.get_run_status,
    # Report-center metric definition (replaces the old brand-growth get_growth_summary / get_today_effect)
    _t.get_report_overview, _t.get_period_report, _t.list_report_periods,
    _t.get_query_coverage,
    _t.get_publish_status, _t.list_unhit_queries, _t.list_hit_queries, _t.list_pending_articles,
    _t.list_articles, _t.get_article, _t.ask_knowledge,
    # Multi-topic: money-spending actions must confirm which topic first; only read-only tools may soft-fall back to "latest batch run"
    _t.list_topics,
    # Optimization task center. It was defined but never in the customer line's groups — so the "find gap -> turn into plan/retest"
    # flywheel was broken in customer conversations; only the ops console could reach it.
    _t.list_action_items,
    # Industry leaderboard (no login, zero cost) and continuous monitoring cadence. Why they were added: see each module's header:
    # these two things **were always promised in copy** but no tool delivered them, so the model could only make things up.
    _ti.get_industry_board, _tm.get_monitor_schedule,
]
_GEO_WRITE = [
    _t.create_topic, _t.set_seed_prompts, _t.expand_prompts, _t.set_selected_queries,
    # Official site: previously only fillable via a web form, yet the agent kept promising "I'll change it for you" and changed nothing.
    # Cadence switch: previously the only write entry was in the ops console; the front end had no such switch (while the copy told users to click it).
    _t.set_topic_website, _tm.set_monitor_schedule,
    # run_geo_checks (25 items) and trigger_diagnosis (plan generation) are retired; audits keep only this one entry
    _t.run_full_diagnosis, _t.draft_articles,
    _t.approve_article, _t.reject_article, _t.ingest_material, _t.confirm_template,
    _t.switch_topic, _t.convert_action_item,
    # Failed-draft rescheduling: previously the customer side had no "write it again" entry and had to ask ops.
    _t.retry_failed_articles,
]
# Compatibility with old references ("both" full set; externally still never includes publish_drafts)
_READ_TOOLS = _GEO_READ + _SENTIMENT_READ
_WRITE_TOOLS = _GEO_WRITE + _SENTIMENT_WRITE

# ── Four groups completing the self-service loop (2026-08-25) ────────────────────────
# Grouped by **risk**, not business: read-only is given freely, media writes must pass the channel gate, money-spending must also pass
# spend.py's four caps. External channels (embed/im/skill/api) get none of the three groups.
from agents.geo import tools_billing as _tb   # noqa: E402
from agents.geo import tools_publish as _tp   # noqa: E402
from agents.geo import tools_websearch as _tw  # noqa: E402

_BILLING_READ = [_tb.get_wallet, _tb.get_price_quote, _tb.list_ledger,
                 _tb.get_topup_options, _tb.get_sentiment_plan]
_MEDIA_READ = [_tp.search_media, _tp.list_media_candidates, _tp.list_my_publish_tasks]
_MEDIA_WRITE = [_tp.auto_select_media, _tp.push_media_draft, _tp.submit_publish_url]
_PAID = [_tp.preview_paid_publish, _tp.pay_and_publish]
# Given to both lines: degraded evidence gathering is line-agnostic; GEO asking "are we being cited" and sentiment asking "is anyone bashing us"
# both rely on it when the main path is down. Putting it into the _GEO_ALL computation would break test_routing's mutual-exclusion assertion,
# so it's a separate group explicitly excluded in that assertion.
_SHARED = [_tw.websearch_evidence]
# Enable daily sentiment monitoring — it makes the account deduct credits once a day from then on, so it belongs to the wallet_spend tier,
# loaded separately from _BILLING_READ (pure read-only).
_SENTIMENT_ACT = [_tb.start_sentiment_monitor]
# Create a WeChat top-up order. Same tier as above: it doesn't touch credits itself, but it's the account's money.
# The loading criterion is the same as the 「对话内充值可用/不可用」 ("in-chat top-up available/unavailable") line in get_topup_options — keep them consistent,
# or we're back to "says you can top up, but there's no tool".
_WALLET_ACT = [_tb.create_topup_order]
# Manually trigger one monitoring batch run. GEO line only, really spends credits per cell, also in wallet_spend.
_GEO_ACT = [_tm.trigger_batch_run]

# ── Ops workspace (admin) line ──────────────────────────────────────────────
# **Never loaded together** with the customer line: it contains no _GEO_WRITE tools (their metric definitions diverge from admin endpoints,
# and they bypass publish_preset / notifications); conversely geo/sentiment/both contain no admin_* tools.
# eval/test_admin_scope.py asserts both directions have an empty intersection.
from agents.geo import admin_tools as _at   # noqa: E402

# Customer-side read-only tools reused in the ops console: _account_topic is already scope-aware (see tools.py),
# so these 12 read the customer project's data unchanged, without copying into admin_* versions (copies would inevitably diverge).
_ADMIN_REUSE_READ = [
    _t.get_report_overview, _t.get_period_report, _t.list_report_periods,
    _t.get_query_coverage, _t.get_publish_status, _t.get_run_status,
    _t.list_unhit_queries, _t.list_hit_queries,
    _t.list_articles, _t.get_article, _t.get_full_diagnosis, _t.list_action_items,
    # The industry leaderboard is public read-only data, useful to ops viewing a customer project too; same for the cadence explanation.
    _ti.get_industry_board, _tm.get_monitor_schedule,
]
_ADMIN_READ = list(_at.ADMIN_READ_TOOLS)
_ADMIN_WRITE = list(_at.ADMIN_WRITE_TOOLS)   # Reversible writes: execute directly
_ADMIN_ACT = list(_at.ADMIN_ACT_TOOLS)       # Irreversible writes: all go through two-step confirmation (confirm.py)
_ADMIN_PAID = list(_at.ADMIN_PAID_TOOLS)     # Paid external publishing: loaded per AGENT_ALLOW_PAID_PUBLISH

# Keyword routing: decide which business line this message belongs to (sentiment keyword hit -> sentiment, else -> geo)
_SENTIMENT_KW = (
    "舆情", "舆论", "负面", "正面", "利空", "利好", "风险", "口碑", "声量", "情绪",
    "提示词", "监控词", "检测词", "监测关键词", "热点", "热榜", "热搜", "newsnow",
    "看跌", "看涨", "bearish", "bullish",
    # Colloquial phrasing: users rarely say 「舆情」 ("sentiment"), more often 「有人骂我们吗」 ("is anyone bashing us") or 「有没有差评」 ("any bad reviews")
    "骂", "差评", "吐槽", "黑料",
    # 「快检」 ("quick check") is the product's **official name** for one-off sentiment checks, yet it was never in this table:
    # a user saying 「帮我跑个快检」 ("run a quick check for me") contains neither sentiment nor GEO words -> falls to the default geo line,
    # and the geo line doesn't load run_sentiment_once, so the model could only reply "can't do that".
    "快检", "风评", "投诉", "负评", "口碑监测", "品牌形象",
)

# Strong GEO signal words: their presence almost certainly means the GEO business line (actions / proper nouns).
# Purpose: for mixed intent (also containing sentiment words, e.g. "做 GEO 提升正面声量" ("do GEO to boost positive buzz")), GEO signals win, avoiding being hijacked to the sentiment line,
# which would leave this turn without GEO tools and the model making things up (e.g. inventing a nonexistent "switch to the GEO module" operation).
# Only **strong GEO-specific words** are included, not ambiguous ones like 报告/效果 ("report"/"effect") used by both lines, to avoid misclassifying "舆情报告" ("sentiment report") as GEO.
_GEO_KW = (
    "geo", "aeo", "可见性", "被引用", "引用率", "建主题", "创建主题", "新建主题", "主题",
    "提示词", "种子词", "扩词", "扩展词", "落库", "命中", "未命中", "诊断", "发文", "发布文章",
    # Audit words: the one-click full diagnosis is the platform's only audit entry; these words must route to the geo line to reach the tool
    "检测", "体检", "全面诊断", "一键诊断", "跑一次", "测一下",
    "产稿", "生成文章", "投放", "复测", "优化文章", "query", "覆盖率",
    # Report-center metric words (2026-07 redesign): all report-center proper nouns, unambiguous with the sentiment line.
    # ★ 「声量度」「品牌声量」 ("voice score", "brand voice") must be included: _SENTIMENT_KW contains 「声量」 ("voice"); otherwise 「品牌声量度怎么样」 ("how's our brand voice score")
    #   gets hijacked to the sentiment line, no GEO tools load this turn and the model makes things up. Including them hits both lines -> both,
    #   so both sides' tools are provided and the answer is complete.
    "报告中心", "日报", "周报", "月报", "露出率", "整体露出率", "top1", "top3",
    "中正率", "内容中正率", "文章引用率", "声量度", "品牌声量", "达标", "期次", "跑批",
)


def route_line(text: str) -> str:
    """Route a user message to a business-line toolset: geo / sentiment / both.

    Two businesses, one agent: users can ask about GEO and sentiment in the same conversation, **never blocked by the line split**.
    Isolation only keeps CTAs from crossing on single intents; once both lines are asked about, both toolsets are given for a complete answer.
      1. Contains both GEO and sentiment signals -> both (both toolsets, e.g. "check sentiment and also run a GEO diagnosis");
      2. Only sentiment signals -> sentiment (single line, clean answer without GEO CTAs);
      3. Only GEO signals / neither -> geo (default line).
    """
    t = (text or "").lower()
    geo = any(k in t for k in _GEO_KW)
    sent = any(k in t for k in _SENTIMENT_KW)
    if geo and sent:
        return "both"
    if sent:
        return "sentiment"
    return "geo"


def _line_tools(line: str, can_write: bool, include_publish: bool = False,
                channel: str = "web"):
    """Provide tools by business line + channel.

    The channel dimension is a **load-time mechanism gate** (see gates.py): external channels can't even see the schemas of paid and
    media-writing tools, so the model won't call them or say things like "I could publish it for you but I'm blocked" that invite follow-ups.
    Each money-spending tool body also has a runtime `gates.require`, which still blocks if the load-time gate misses.
    """
    if line == "admin":
        # Ops console line: console tools + reused customer-side read-only tools. **Contains no _GEO_WRITE / _SENTIMENT_* tools.**
        base = _ADMIN_REUSE_READ + _ADMIN_READ
        if can_write:
            base = base + _ADMIN_WRITE + _ADMIN_ACT
            # Paid external publishing loaded per env: when the guardrail is off the model can't even see the schema,
            # so it won't call it or say things like "I could publish it for you but I'm blocked" that invite follow-ups.
            if _gates.channel_allows("publish_paid", channel):
                base = base + _ADMIN_PAID
        return base
    if line == "sentiment":
        base = _SENTIMENT_READ + (_SENTIMENT_WRITE if can_write else [])
        if can_write and _gates.channel_allows("wallet_spend", channel):
            base = base + _SENTIMENT_ACT
        return base + _SHARED + _self_extras(can_write, channel, billing_only=True)
    if line == "both":
        base = _READ_TOOLS + (_WRITE_TOOLS if can_write else [])
        if can_write and _gates.channel_allows("wallet_spend", channel):
            base = base + _SENTIMENT_ACT + _GEO_ACT
        base = base + _SHARED + _self_extras(can_write, channel)
        return base + ([_t.publish_drafts]
                       if (include_publish and can_write
                           and _gates.channel_allows("publish_own", channel)) else [])
    # geo line: also includes configure_sentiment (configure prompts). Reason: routing judges each message individually,
    # and a user replying "yes" to "want to adjust the prompts?" gets routed to the geo line; without this write tool here,
    # the model couldn't reach it to configure prompts. It is a harmless sentiment write tool; cross-line use doesn't affect GEO isolation.
    base = _GEO_READ + (_GEO_WRITE if can_write else [])      # geo
    if can_write:
        # Only configure_sentiment is included as a write tool: it is harmless (just changes a few words).
        # run_sentiment_once really burns an engine call, so it isn't carried across lines — if the user really wants to start one,
        # that message will hit sentiment words and routing sends the turn to the sentiment / both line.
        base = base + [_t.configure_sentiment]
        if _gates.channel_allows("wallet_spend", channel):
            base = base + _GEO_ACT
    # get_sentiment_once **only reads results**, so it's carried across lines: the run was started last turn (sentiment line),
    # and the user's next message is often "is it done yet / where are the results" — containing neither sentiment nor GEO words,
    # it falls to the default geo line, and without the result-fetching tool it can only wait.
    base = base + [_t.get_sentiment_once]
    base = base + _SHARED + _self_extras(can_write, channel)
    return base + ([_t.publish_drafts]
                   if (include_publish and can_write
                       and _gates.channel_allows("publish_own", channel)) else [])


def _self_extras(can_write: bool, channel: str, billing_only: bool = False):
    """The customer line's wallet / media / paid groups, loaded per channel.

    ★ This is the **load-time** gate: external channels can't even see the schemas, so the model won't call them,
      nor say things like "I could publish it for you but I'm blocked" that invite follow-ups.
      Each tool body also has a runtime `gates.require` — it still blocks if this one misses.
    """
    out = list(_BILLING_READ)             # Balance / quote lookups are always provided: read-only and risk-free
    # Creating a top-up order: both lines need it — running out of credits can happen on either line.
    # The criterion must match get_topup_options' 「对话内充值可用吗」 ("is in-chat top-up available").
    if can_write and _gates.channel_allows("wallet_spend", channel):
        out = out + _WALLET_ACT
    if billing_only:
        return out
    out = out + _MEDIA_READ
    if not can_write:
        return out
    if _gates.channel_allows("publish_own", channel):
        out = out + _MEDIA_WRITE
    if _gates.channel_allows("publish_paid", channel):
        out = out + _PAID
    return out


@lru_cache(maxsize=64)
def build_embed_agent(can_write: bool, scene: str = "chat", line: str = "both",
                      channel: str = "embed"):
    """External agent: narrowed by caps + by business line (line=geo/sentiment gets only that line's tools).

    Never includes publish_drafts, **nor any admin_* tool** — an external token can never reach the ops console.
    """
    if line == "admin":
        line = "geo"
    tools = _line_tools(line, can_write, include_publish=False, channel=channel)
    # **Always excluded at code level**, regardless of env — env can only tighten, never loosen.
    # Same nature as the `if line == "admin": line = "geo"` above: the last mechanism gate on external channels.
    banned = set(_MEDIA_WRITE + _PAID + [_t.publish_drafts])
    return _make_agent([t for t in tools if t not in banned], scene)
