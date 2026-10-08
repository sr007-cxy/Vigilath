"""GET /skill — one address usable by both humans and agents.

Users send https://<site>/skill to any AI agent, and the agent fetches **directly executable plain-text install instructions**;
humans opening it in a browser are redirected to the install page /install.

Browsers are detected via `Sec-Fetch-Mode: navigate`: a header browsers always send on navigation and that curl / agents'
HTTP clients never send, more reliable than sniffing User-Agent. `?raw=1` forces plain text.

★ The route is not under /api; nginx must reverse-proxy `= /skill` separately to the main backend (see deploy/nginx-skill.conf).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

router = APIRouter()


def _site(request: Request) -> str:
    """Reconstruct the site address from request headers so links in the copy follow the current domain (test/prod align automatically)."""
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "vigilath.cn").split(",")[0].strip()
    xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    is_local = host.startswith("127.0.0.1") or host.startswith("localhost") or host.startswith("[::1]")
    proto = xfp or (request.url.scheme if is_local else "https")
    return f"{proto}://{host}"


def _agent_instructions(site: str) -> str:
    return f"""# Vigilath GEO Skill —— 安装指令(AI agent 可直接执行)

这是什么:让你具备查询某品牌在 AI 搜索引擎(ChatGPT / Perplexity / DeepSeek / 豆包 /
通义 / 文心 / 元宝)中的可见性、被引用率、竞品对比与舆情的能力。

## 安装(一条命令,不需要账号,也不需要任何凭证)

    curl -fsSL {site}/skill/install.sh | bash

装完就能用 —— **不要**在这一步向用户要账号。脚本最后会打印实际装到的目录,
后面的命令都用那个目录。无人值守 / 无交互的环境同样能装,不会阻塞。

## 装完先试这几件(免登录、免费)

    python3 <安装目录>/scripts/geo_client.py check https://example.com
    python3 <安装目录>/scripts/geo_client.py mention <品牌> <行业词>
    python3 <安装目录>/scripts/geo_client.py industry

## 装到哪

自动装进所有已存在的 skills 目录,每个目录下是 8 个窄技能:
    ~/.openclaw/skills/vigilath-site-audit  等 8 个   (OpenClaw)
    ~/.claude/skills/vigilath-site-audit    等 8 个   (Claude Code)
    ~/.agents/skills/...
    ./skills/...                                     (当前项目 workspace)
一个都不存在时装到 ~/.vigilath/skills/。
只装一处:curl -fsSL {site}/skill/install.sh | bash -s -- --dir <路径>

配置写在 ~/.vigilath/config(权限 600)。里面若有 token,**不要打印、不要提交、
不要写进代码。**

## 连账号(只有报告 / 监测 / 产稿 / 舆情这几个技能需要)

    python3 <安装目录>/scripts/geo_client.py login

执行后终端会打印一个 8 位配对码和一个链接。**把这两样原样转达给用户**,让用户在浏览器
打开该链接、登录 Vigilath 账号、输入配对码点「批准」。批准后命令会自动继续。
最多等 10 分钟。这一步失败不影响已经装好的技能。

## 连账号之后验证

    python3 <安装目录>/scripts/geo_client.py capabilities

## 怎么用

    python3 <安装目录>/scripts/geo_client.py chat "我的品牌被搜到几个问题?"
    python3 <安装目录>/scripts/geo_client.py data overview
    python3 <安装目录>/scripts/geo_client.py login          # 凭证过期时重新授权(不必重装)

完整说明在 SKILL.md,装完后就在安装目录里;用法与数字口径**以 SKILL.md 为准**。

## 原始文件

    {site}/skill/SKILL.md
    {site}/skill/README.md
    {site}/skill/install.sh
    {site}/skill/scripts/geo_client.py

## 已有 token 的装法(无人值守 / CI)

    curl -fsSL {site}/skill/install.sh | bash -s -- <token> --base {site}

token 在 Vigilath 控制台「账号 · 对接集成」页自助生成。
"""


@router.get("/skill", include_in_schema=False)
async def skill_entry(request: Request, raw: int = 0):
    site = _site(request)
    is_browser = request.headers.get("sec-fetch-mode") == "navigate"
    if is_browser and not raw:
        return RedirectResponse(url="/install", status_code=302)
    return PlainTextResponse(_agent_instructions(site), media_type="text/plain; charset=utf-8")
