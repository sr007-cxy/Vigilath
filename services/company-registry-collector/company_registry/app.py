"""FastAPI service: lets the Sales Agent / upstream trigger collection on demand (multi-provider).

    uvicorn company_registry.app:app --host 0.0.0.0 --port 8021

env:
    <PROVIDER>_COOKIE  each provider's login session (e.g. QICHAMAO_COOKIE)
    ENTDATA_TOKEN      optional; if set, requests must carry the X-ENTDATA-Token header, preventing public abuse
"""
from __future__ import annotations

import os
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .collector import Collector
from .config import Settings
from .registry import available_providers, get_provider

app = FastAPI(title="company-registry-collector", description="企业工商数据采集(多服务商)")

_TOKEN = os.environ.get("ENTDATA_TOKEN", "").strip()


def _auth(tok: Optional[str]):
    if _TOKEN and tok != _TOKEN:
        raise HTTPException(status_code=401, detail="bad token")


class CollectRequest(BaseModel):
    provider: str = Field("qichamao", description="数据服务商")
    word: str = Field(..., description="核心词,如 代理记账")
    cities: List[str] = Field(default_factory=list, description="城市切片;空则不切")
    bands: str = Field("none", description="all=按服务商资本档 / none=不切 / 逗号分隔自定义")
    company_type: Optional[str] = Field(None, description="企业类型过滤;null=服务商默认")
    fetch_detail: bool = Field(True, description="是否补电话/邮箱")


@app.get("/health")
def health():
    return {"ok": True, "providers": available_providers()}


@app.get("/providers")
def providers():
    out = {}
    for name in available_providers():
        p = get_provider(name)
        out[name] = {"ready": p.ready(), "capital_bands": p.capital_bands,
                     "default_company_type": p.default_company_type}
    return out


@app.post("/collect")
def collect(req: CollectRequest, x_entdata_token: Optional[str] = Header(default=None)):
    _auth(x_entdata_token)
    try:
        provider = get_provider(req.provider, Settings())
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not provider.ready():
        raise HTTPException(status_code=400, detail=f"provider '{req.provider}' 凭证未配置")

    result = Collector(provider, Settings()).run(
        word=req.word,
        cities=req.cities or [""],
        bands=req.bands,
        company_type=req.company_type,
        fetch_detail=req.fetch_detail,
    )
    return {
        "stats": result.stats(),
        "aborted": result.aborted,
        "abort_reason": result.abort_reason,
        "companies": [c.to_row() for c in result.companies],
    }
