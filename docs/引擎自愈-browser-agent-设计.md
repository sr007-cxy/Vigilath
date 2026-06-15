# 引擎自愈(browser-agent)设计 + qwen 进程池评估

> 背景:2026-06 qwen 整站 SPA 改版,把基于硬编码选择器的 adapter 打挂(提交方式变、
> 答案容器读不出、引用挪进 SSE 流)。靠人手远程猜选择器修了快 25 轮才通。这类"引擎
> 前端改版"会反复发生,需要**自愈能力**而非每次手修。

## #5 qwen 进程池 —— 评估:低 ROI,暂缓

现状:每条 qwen 查询 `subprocess` 起一个干净浏览器进程(app/qwen_query.py)。
- 浏览器启动 ~5-10s;一条查询本身 60-90s(答案流式)→ 启动开销仅约 10%。
- GEO 是低频测量,不是高 QPS。进程池要管理 N 个常驻浏览器 worker + 队列 + IPC +
  健康回收,复杂度高;省下的 10% 启动开销价值有限。

**结论**:除非 qwen 跑批量级显著上升,否则不做。真要做,方案是「常驻 qwen-runner
服务(独立进程,非 browser-service 子进程)+ 本地 HTTP 队列」,而非 browser-service
内进程池(会撞常驻 hot 浏览器 context)。

## #7 引擎自愈 browser-agent —— 分 3 阶段

核心思路(混合,非"每条查询都 LLM 驱动浏览器",那样太慢太贵):
**平时走快选择器;选择器失配时,LLM 看页面结构 → 重新定位 → 验证 → 热更新配置。**

### Phase 1:选择器外置成配置(基础,本设计先做这步)
- 把各 adapter 的硬编码选择器(input/send/answer 候选链、citation API URL、来源面板)
  抽到一个**引擎配置**(`engine_selectors.json`,可被运行时覆盖),adapter 从配置读。
- 价值:① 改版时改配置即可、**不用改代码重新部署 5 台**;② 是 Phase 2 自愈的写入目标。
- 落点:`services/browser-service/app/engine_selectors.py`(默认值)+ 可选
  `ENGINE_SELECTORS_FILE` 覆盖文件;qwen_query.py / 各 adapter 读它。

### Phase 2:LLM 选择器发现(自愈核心)
- 触发:健康哨兵(已上线)测出某引擎 FAIL,或人工点"自愈"。
- 流程:跑结构探针(枚举可交互元素 + 抓 completion API 网络响应,已有 probe 脚本雏形)
  → 把结构喂给 LLM(平台已有 DEEPSEEK_API_KEY/OPENROUTER 模式)→ LLM 产出新的
  {input_sel, send_sel, answer 读法, citation API/字段} → **灰度验证**(用新配置跑一条
  canary,断言有答案+引用)→ 通过则写回 Phase 1 的配置(热更新),否则告警人工。
- 关键:LLM 只在"挂了"时调用(非每条),既省成本又自愈。

### Phase 3:全自动 + 多引擎复用
- 验证通过自动热更、无需人工确认;同一套 probe+heal 复用到 deepseek/wenxin/yuanbao。
- 加"自愈历史"看板(哪个引擎、何时、改了哪些选择器)。

## 落地顺序
Phase 1(配置外置,低风险、即时价值)→ Phase 2(LLM 发现+灰度,核心)→ Phase 3(全自动)。
每阶段独立可验证、可上线;不一次性大爆炸。
