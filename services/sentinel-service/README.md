# Sentinel Service

LLM-native 舆情监测微服务 — 把 [yuqin](https://github.com/.../yuqin) 包成 FastAPI HTTP 接口,
供 GEO backend 的定时任务和 API 路由调用。

## 核心接口

| 方法 | 路径 | 用途 | 是否需 OPENAI_KEY |
|---|---|---|---|
| POST | `/run-monitor`  | LLM 生成 plan + 多引擎抓取 + 写 posts | ✓ |
| POST | `/run-analyze`  | 对未分析帖子做结构化分析 | ✓ |
| POST | `/run-brief`    | 生成日度简报 Markdown | ✓ |
| POST | `/run-respond`  | 生成三档响应草稿 | ✓ |
| POST | `/run-crawl-eastmoney` | 直爬东财股吧(免 LLM)| × |
| GET  | `/accounts/{id}/posts?ticker=` | 取帖子+分析 | × |
| GET  | `/accounts/{id}/briefs?ticker=` | 简报列表 | × |
| GET  | `/accounts/{id}/briefs/{brief_id}` | 简报详情 | × |
| GET  | `/accounts/{id}/drafts?ticker=` | 草稿列表 | × |
| GET  | `/health` | 健康检查 | × |

OpenAI API Key 通过 `X-OpenAI-Key` header 传递,优先级高于容器 env。

## 多租户隔离

每个 `account_id` 一个独立 SQLite:
```
data/account_42/yuqing.db
data/account_42/briefs/brief_VNET_2026-05-06.md
data/account_42/knowledge/brand_voice.md
```

不修改 yuqin 源 schema,通过 monkey-patch `storage.connect()` 实现。

## 开发

```bash
cd services/sentinel-service
pip install -e .
export OPENAI_API_KEY=sk-...
uvicorn service:app --reload --port 8090
```

测试一发:
```bash
curl -X POST http://localhost:8090/run-monitor \
  -H "Content-Type: application/json" \
  -H "X-OpenAI-Key: $OPENAI_API_KEY" \
  -d '{"account_id":1,"target":"世纪互联","ticker":"VNET","intent":"做空报告"}'
```

## 容器化

```bash
docker compose up sentinel-service
```

## 调用方

GEO backend 通过 `backend/geo/services/sentinel_client.py` 调本服务。
APScheduler 在 backend 内每日 07:00 (Asia/Shanghai) 遍历所有 active 账号触发 monitor → analyze → brief 全流程。

## 待办

- 关键词 `excludes` yuqin 当前没原生支持,后续在 plan 生成时加 NOT 修饰
- 把 plan 生成与 run_plan 拆成两步,允许 GEO backend 在中间持久化 plan 并支持人工编辑
- chat 端 — P2
