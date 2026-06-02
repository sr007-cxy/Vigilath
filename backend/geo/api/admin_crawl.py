"""admin_crawl — AI/LLM 爬虫日志分析(仅 admin,只读本机 nginx 访问日志).

只分析 vigilath 正式环境**自己的** nginx access log,不接受用户上传日志.
日志路径默认 /var/log/nginx/access.log*(含 .gz 轮转),可用环境变量
GEO_ACCESS_LOG_GLOB 覆盖.geo.service 以 ubuntu 用户运行,且在 adm 组,
可直接读 nginx 日志,无需 sudo.

端点定义为同步 def —— FastAPI 会把它丢到 threadpool 跑,避免解析十几万行
日志(~1-2s)阻塞事件循环.
"""

from fastapi import APIRouter, Depends

from geo.api.auth import require_admin
from geo.services.crawl_snapshot import build_and_store, load_snapshot

router = APIRouter(prefix="/admin/crawl-analysis")


@router.get("")
def get_crawl_analysis(refresh: bool = False, _admin=Depends(require_admin)):
    """返回 AI 爬虫活动分析(结构化 JSON).

    默认读每天 cron 生成的快照(快).refresh=1 强制现在重新解析日志并落盘.
    还没有快照(cron 没跑过)时,实时算一次并落盘.
    """
    if refresh:
        return build_and_store()
    snap = load_snapshot()
    return snap if snap is not None else build_and_store()
