# Vigilath GEO 优化 Skill

把你的 AI agent(小龙虾)链接到 **Vigilath GEO 优化 agent**,让它具备"检测·审计·优化·监控品牌在 AI 搜索引擎中的可见性"的能力。

底层 = 一个 1 年期账号 token + Vigilath 的 `/api/agent/v1/*` 接口;本 skill 是 drop-in 封装,纯标准库、零依赖。

## 安装

把 `vigilath-geo/` 整个目录放进你 agent 的 skills 目录,例如:

```
你的-agent/
  skills/
    vigilath-geo/
      SKILL.md
      scripts/geo_client.py
      README.md
```

## 配置(领号拿 token)

向 Vigilath 领一个账号 token(1 年期),设进环境变量:

```bash
export VIGILATH_AGENT_TOKEN="emb_..."        # 必填,机密,等同 API key,别提交进代码库
# export VIGILATH_BASE="https://geo.vigilath.com/api/agent/v1"   # 选填,默认即此
```

> token 是机密:放环境变量 / 密钥管理,**不要硬编码、不要打印、不要提交**。一个 token = 一个账号,只能访问该账号自己的数据。

## 自测

```bash
python skills/vigilath-geo/scripts/geo_client.py capabilities
python skills/vigilath-geo/scripts/geo_client.py data today
python skills/vigilath-geo/scripts/geo_client.py chat "我的品牌累计被搜到几个问题?"
```

- `chat` 输出 = 自然语言回答 + 结构化卡片 JSON(命中数字、竞品等)。
- `data <name>` 直接取数(today / coverage / report …),不走大模型,更快更省。

## 能力边界

- 引擎、调度、频率由 Vigilath 平台固定,只看结果。
- **真实对外发布不在本 skill 范围**(受平台内部护栏控制);本 skill 只做查询 / 诊断 / 归因 / 优化建议。
- `401` = token 无效/过期,联系 Vigilath 重发。

## 需要 Python 之外的语言?

`/api/agent/v1/*` 是普通 HTTP(`chat` 为 SSE,`data/*`、`meta/*` 为 JSON),带 `Authorization: Bearer <token>` 即可,用任意语言重写客户端都行。协议见 Vigilath《对外开放设计-Agent小龙虾》§11 / §13。
