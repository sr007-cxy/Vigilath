# Vigilath GEO 优化 Skill

把你的 AI agent(小龙虾)链接到 **Vigilath GEO 优化 agent**,让它具备"检测·审计·优化·监控品牌在 AI 搜索引擎中的可见性"的能力。

底层 = 一个 1 年期账号 token + Vigilath 的 `/api/agent/v1/*` 接口;本 skill 是 drop-in 封装,纯标准库、零依赖。

## 安装(一行,推荐)

在 Vigilath 控制台「集成」页一键拿到 token,然后:

```bash
curl -fsSL https://geo.vigilath.com/skill/install.sh | bash -s -- <你的token>
```

脚本自动:① 把 skill 拷进探测到的 skills 目录(Claude `~/.claude/skills`、当前项目 `./skills`,或兜底 `~/.vigilath/skills`;也可 `--dir <路径>` 指定)② token 写进 `~/.vigilath/config`(权限 600,**无需设环境变量**)③ 自检链路。

装完即用,无需手动拷文件、无需配环境变量。

## 手动安装(可选)

把 `vigilath-geo/` 目录放进你 agent 的 skills 目录,token 二选一:
- 写进 `~/.vigilath/config`:`VIGILATH_AGENT_TOKEN=...`(一行 KEY=VALUE)
- 或设环境变量:`export VIGILATH_AGENT_TOKEN="..."`

> token 是机密(等同 API key):**不要硬编码、不要打印、不要提交**。一个 token = 一个账号,只能访问该账号自己的数据。

## 自测

```bash
python3 ~/.claude/skills/vigilath-geo/scripts/geo_client.py capabilities
python3 ~/.claude/skills/vigilath-geo/scripts/geo_client.py data today
python3 ~/.claude/skills/vigilath-geo/scripts/geo_client.py chat "我的品牌累计被搜到几个问题?"
```

- `chat` 输出 = 自然语言回答 + 结构化卡片 JSON(命中数字、竞品等)。
- `data <name>` 直接取数(today / coverage / report …),不走大模型,更快更省。

## 能力边界

- 引擎、调度、频率由 Vigilath 平台固定,只看结果。
- **真实对外发布不在本 skill 范围**(受平台内部护栏控制);本 skill 只做查询 / 诊断 / 归因 / 优化建议。
- `401` = token 无效/过期,联系 Vigilath 重发。

## 需要 Python 之外的语言?

`/api/agent/v1/*` 是普通 HTTP(`chat` 为 SSE,`data/*`、`meta/*` 为 JSON),带 `Authorization: Bearer <token>` 即可,用任意语言重写客户端都行。协议见 Vigilath《对外开放设计-Agent小龙虾》§11 / §13。
