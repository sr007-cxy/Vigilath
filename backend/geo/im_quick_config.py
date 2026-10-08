"""Defaults for IM quick menus/buttons + parsing of connector.config_json.

**No pydantic-ai / agent dependency**; importable by both the main backend (geo.api.account) and the agent service (agents.geo.embed.*).
config_json structure: {"agentid": "...", "quick_actions": [["label","query"],...], "menu_map": {"event_key":"query"}}
"""
from __future__ import annotations

import json

# Default quick buttons at the bottom of replies (label, query sent on click)
DEFAULT_QUICK_ACTIONS: list[list[str]] = [
    ["📈 最新数据", "报告中心最近的数据怎么样?"],
    ["📰 今日舆情", "今天舆情怎么样?"],
    ["🔍 舆情分析", "最近有哪些负面舆情?帮我分析一下趋势"],
    ["🎯 GEO 检测", "我最新的 GEO 诊断报告结果如何?"],
]

# Default Feishu bot bottom menu event_key → query ("__help__" = show the full feature list)
# ★ Existing keys (today/coverage/unhit/...) **must not be deleted**: the live Feishu menu is configured by event_key,
#   and deleting them breaks the corresponding buttons. Redesigns only change the mapped queries; new daily/weekly need menu items added in the Feishu console to take effect.
DEFAULT_MENU_MAP: dict[str, str] = {
    "today": "报告中心最近的数据怎么样?",
    "daily": "最近一期日报怎么样?",
    "weekly": "本周周报怎么样?有没有达标?",
    "coverage": "我累计被搜到几个问题?",
    "unhit": "哪些问题还没被搜到?",
    "sentiment": "今天舆情怎么样?",
    "articles": "文章发布进度如何?",
    "help": "__help__",
    "menu": "__help__",
}

MAX_QUICK_ACTIONS = 6


def parse_config(config_json) -> dict:
    """Parse config_json (str, dict or None) into a dict; bad data returns {}."""
    if isinstance(config_json, dict):
        return config_json
    try:
        cfg = json.loads(config_json) if config_json else {}
    except (ValueError, TypeError):
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def quick_actions(config_json) -> list[tuple[str, str]]:
    """Get the connector's quick buttons [(label, query), ...]; returns defaults when unconfigured."""
    cfg = parse_config(config_json)
    raw = cfg.get("quick_actions")
    out: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                label, q = str(item[0]).strip(), str(item[1]).strip()
                if label and q:
                    out.append((label, q))
    out = out[:MAX_QUICK_ACTIONS]
    return out or [(l, q) for l, q in DEFAULT_QUICK_ACTIONS]


def menu_map(config_json) -> dict[str, str]:
    """Get the connector's Feishu menu mapping {event_key: query}; returns defaults when unconfigured."""
    cfg = parse_config(config_json)
    raw = cfg.get("menu_map")
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if str(k).strip() and str(v).strip():
                out[str(k).strip()] = str(v).strip()
    return out or dict(DEFAULT_MENU_MAP)
