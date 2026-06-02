"""crawl_snapshot — 每天跑一次 AI 爬虫日志分析,落一份 JSON 快照.

prod 是 4-worker uvicorn 且**未设** GEO_SCHEDULER_LEADER,所以不走 APScheduler;
改由系统 crontab 每天调本模块的 __main__ 生成快照.admin 端点只读快照文件,
不在每次请求里实时解析十几万行日志.

cron 示例(prod):
  5 3 * * * cd /home/ubuntu/Dev/geo/backend && ./.venv/bin/python -m geo.services.crawl_snapshot >> data/crawl_snapshot.log 2>&1
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from geo_checker.modes.crawl_check import analyze_crawl_logs, resolve_log_paths

_LOG_GLOB = os.getenv("GEO_ACCESS_LOG_GLOB", "/var/log/nginx/access.log*")

_EMPTY = {
    "files": [], "total_size_mb": 0, "total_lines": 0, "parsed_lines": 0,
    "period": {"first": None, "last": None}, "total_bot_requests": 0,
    "bots": [], "missing_critical": [], "missing_optional": [],
}


def _data_dir() -> Path:
    # 绝对路径,不依赖 CWD —— cron 与 uvicorn 服务必须解析到同一个目录.
    return Path(os.environ.get("GEO_DATA_DIR") or (Path(__file__).resolve().parents[2] / "data"))


def snapshot_path() -> Path:
    return _data_dir() / "crawl_analysis_latest.json"


def build_snapshot() -> dict:
    files = resolve_log_paths(_LOG_GLOB)
    data = analyze_crawl_logs(files) if files else dict(_EMPTY)
    data["log_glob"] = _LOG_GLOB
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    return data


def build_and_store() -> dict:
    """跑分析 + 原子写盘,返回结果."""
    data = build_snapshot()
    p = snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # 原子替换,避免读到半截文件
    return data


def load_snapshot() -> dict | None:
    p = snapshot_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    d = build_and_store()
    print(
        f"[crawl_snapshot] {d.get('generated_at')} "
        f"bots={len(d.get('bots', []))} requests={d.get('total_bot_requests')} "
        f"-> {snapshot_path()}"
    )
