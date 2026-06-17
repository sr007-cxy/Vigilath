一、文章生成优化
  现状:后端 content_generator.py 已成熟(DeepSeek、N×M 多变体 combo、文风学习 _load_style_refs),但前端 Compose.tsx 仍是 mock 未接 LLM;生成是后台 daemon thread,前端全黑盒无进度;AI 稿用户不能编辑(content.py:220 仅 source=user 可改)。
[] Compose.tsx 接真实生成接口(POST /topics/{id}/run-now)
[] 加生成进度反馈:轮询 doc 状态(pending_review/失败),展示队列进度条 + generation_error
[] 多变体并排对比预览抽屉(同 query 的 N×M combo 一屏对标,而非逐条审)
[] 放开用户编辑 AI 稿(权限调整 + 审计留痕)
[] 失败可见 + 单条「重生」按钮
[] 增加浏览器自动化发文， openclaw 等方式

二、博客生成优化
  现状:Blog.tsx/BlogPost.tsx 纯静态,数据硬编码在 data/blogPosts.ts(9 篇),与文章生成零打通——优秀生成稿无法沉淀为博客。
[] 博客数据从 blogPosts.ts 迁到后端(新表 + CMS 接口),保留静态 fallback
[] 打通「文章生成 → 博客」:审核通过时勾选「作为博客发布」,复用 combo 的 long_form 稿
[] 复用 content_generator 的文风学习,让博客也学自有文风


三、功能界面模块清晰化
@李双旭 对齐
  
四、 用户资料管理
现状: (话题 profile_json 28字段)两套表,新建话题要重填品牌信息;前端只校验 5 个必填,但 LLM 需 15+
字段才出好稿,频繁拒审返修;无完整度提示。
[] 资料完整度指示:BrandProfileForm 实时算完整度分 + 列缺失字段(ProfileImporter 提取后即提示)
[] 话题创建预填:从账户品牌资料自动拉基础字段,免重复录入
[] 统一品牌主体模型(中期):品牌中心表,topic/sentiment_account 外键指向,改名级联
[] 清理废弃的创作方向字段(schema 已留但 UI 说明矛盾,BrandProfileForm.tsx:237)
[] 明确知识库(sentiment_knowledge)→生稿的复用路径
  
五、Agent 优化侧边栏展示 + ⑥ Agent 形式(右侧嵌入/大屏)
现状:AgentChatWidget 是 384×540 浮动气泡,全局挂载;对当前页面零感知(没用 useLocation,topic_id 写死传
null);移动端被压缩。后端 SSE 流式 + 多轮记忆 + 业务线路由都健壮,80% 逻辑可复用。
[] 上下文感知(地基,优先):接 useLocation/当前 topicId,传给 streamAgentChat,Agent 知道用户在看哪个主题/页面
[] 左嵌入形态:在 /brand-growth/* 把气泡改为可收起左侧栏(删 useDraggable、复用消息/卡片/SSE 渲染),按页面挂载
[] 大屏形态:isExpanded 切换浮窗↔并排大屏(lg 屏 page 60% + Agent 40%),md 以下回落浮标
[] 侧栏版去掉底部 12 个硬编码导航按钮(与 NAV_LINKS 重复)
[] 移动端适配:键盘遮挡、拖动越界、未读轮询 60s→更实时

六、收集平台信息
自动收录