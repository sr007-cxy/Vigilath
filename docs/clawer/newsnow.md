# NewsNow 项目 媒体数据源处理文档
本文档详细说明 NewsNow 项目中**各媒体平台数据的采集、解析、处理规则**，统一技术实现标准与处理逻辑，为开发、维护、扩展数据源提供参考。
URL：https://github.com/ourongxing/newsnow

## 一、项目核心处理流程
1. **统一请求入口**：所有媒体数据均通过 `myFetch` 工具发起网络请求，获取原始数据
2. **多模式适配处理**：根据媒体数据格式，采用 API、网页抓取、正则提取、GraphQL 等方式解析
3. **标准化输出**：所有原始数据最终转换为符合 `NewsItem` 接口规范的对象数组
4. **自动加载机制**：`server/sources/` 目录下的文件会被系统自动识别为数据源

---

## 二、媒体处理方式汇总表
| 媒体名称 | 处理模式 | 核心技术点 | 核心实现文件 |
| :------- | :------- | :--------- | :----------- |
| 知乎 | API | 直接解析 REST API JSON 数据 | `zhihu.ts` |
| 微博 | 网页抓取(Scraping) | 携带 Cookie 访问页面，Cheerio 解析 DOM | `weibo.ts` |
| 哔哩哔哩 | API | 播放量/点赞数格式化为「1w+」标准格式 | `bilibili.ts` |
| 快手 | 正则提取(Regex) | 正则提取 HTML 中 `window.__APOLLO_STATE__` JSON 数据 | `kuaishou.ts` |
| ProductHunt | GraphQL | 发送 POST 请求执行查询，携带 Token 鉴权验证 | `producthunt.ts` |
| IT之家 | 网页抓取(Scraping) | 过滤「神券」「京东」等广告关键词数据 | `ithome.ts` |
| 联合早报 | 编码处理 | 使用 `iconv-lite` 完成 gb2312 编码转换 | `zaobao.ts` |
| 腾讯视频 | API(POST) | 构造复杂请求体，获取热搜、电视剧榜单 | `qqvideo.ts` |
| 雪球 | API(Cookie) | 先请求首页获取 Cookie，再携带 Cookie 请求 API | `xueqiu.ts` |
| 金十数据 | 正则提取(Regex) | 移除 `var newest =` 声明，JS 变量字符串转 JSON | `jin10.ts` |

---

## 三、分类型处理细节说明
### 1. 结构化 API 映射（API-Based）
**特点**：数据格式稳定、解析成本低，直接字段映射为标准 `NewsItem`
- 今日头条：直接请求 API，映射 `ClusterIdStr`、`Title` 核心字段
- 豆瓣电影：设置 `Referer` 请求头，伪装移动端 API 请求
- 华尔街见闻：通过 `resource_type` 字段过滤广告、主题帖，保留有效资讯

### 2. 网页抓取与解析（Web Scraping）
**核心工具**：`cheerio`（类 jQuery 语法操作 HTML DOM）
- 36氪：解析 `.newsflash-item` 节点获取快讯，`parseRelativeDate` 处理相对时间
- Freebuf：单条数据解析增加 `try-catch` 容错，避免局部格式异常导致整体失败
- Steam 统计：抓取页面游戏在线人数排名表格数据

### 3. 特殊脚本/动态数据提取（Regex/Special）
**适用场景**：数据嵌入页面脚本、需动态签名/鉴权、无公开标准 API
- 凤凰网：正则匹配页面 `var allData = {...};` 脚本块提取数据
- 酷安：`genHeaders` 生成动态请求头（签名、Token），突破 API 限制
- 抖音：模拟登录页面，获取 `Set-Cookie` 必要请求凭证

---

## 四、项目通用规范与注意事项
### 1. 自动加载规则
所有数据源文件存放于 `server/sources/` 目录，系统会**自动扫描并加载**，无需手动注册。

### 2. 数据标准化要求
无论原始数据格式如何，最终必须返回**符合 `NewsItem` 接口定义的对象数组**，保证前端展示统一。

### 3. 容错与重试机制
- 关键数据源（如酷安）需校验数据长度，数据为空时主动抛出错误
- 抛出错误会触发系统**自动重试机制**，提升数据采集稳定性

### 4. 通用技术点
- 编码处理：`iconv-lite` 处理非 UTF-8 编码（如联合早报 gb2312）
- 请求伪装：支持 Cookie、Referer、动态请求头、Token 鉴权等反爬绕过方案
- 数据格式化：数字、时间、文本统一标准化处理

---

## 五、文档说明
本文档覆盖 NewsNow 项目所有媒体数据源的处理逻辑，新增/修改数据源时，需严格遵循**统一请求、分类解析、标准化输出**的原则，保持代码一致性与可维护性。

### 总结
1. 核心流程：`myFetch` 请求 → 分类解析 → 标准化为 `NewsItem` → 自动加载
2. 四大处理模式：API、网页抓取、正则提取、GraphQL，适配不同媒体数据格式
3. 通用规范：自动加载、数据标准化、容错重试、编码/请求伪装统一处理