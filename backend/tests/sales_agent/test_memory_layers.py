"""Three memory layers: short-term (in-session) / mid-term (profile) / long-term (accumulated preferences).

2026-08-23 retrospective (05-evaluation §8): mid-term was only "stored", readable only if the model chose to call a tool,
so every new session asked "who are you" again via the profile card; long-term didn't exist at all,
and after the user said "no education industry", the next round recommended it anyway.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_memory_layers.py -q
"""
from __future__ import annotations

import json
import pathlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentAccountICPORM, SalesAgentCompanyORM
from agents.sales_agent.service import append_account_icp_preferences
from geo.database import Base

USER = 1
_PKG = pathlib.Path(__file__).resolve().parents[2] / "agents" / "sales_agent"
API = (_PKG / "api.py").read_text(encoding="utf-8")


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SalesAgentAccountICPORM.__table__])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


# --- Mid-term: the profile unconditionally enters context every turn ---

def test_state_digest_is_unconditional():
    """Previously the summary was only built when trimming over budget; normally the profile never entered context.

    What's asserted here is "all three preamble parts are concatenated before body.message", not a whole source line --
    pinning the whole line meant inserting a `_gather_preamble` in the middle later broke this gate,
    even though what it meant to guard hadn't changed at all.
    """
    assert API.count("_page_preamble(body.page_context) + _state_preamble(deps)") >= 2
    assert API.count("_state_preamble(deps)") >= 2, "两个对话入口都要拼,否则 SSE 侧仍会重复追问"
    # Likewise don't pin the parameter list: prefetch later filters by "mailbox/company named in the question" and gained a message parameter.
    assert API.count("+ _gather_preamble(deps, intent") >= 2, \
        "两个入口都要按意图预取,否则 SSE 侧仍会靠模型自己想起来调只读工具"
    assert API.count("_gather_preamble(deps, intent, body.message)") >= 2, \
        "预取要看得到原问题,否则点名问某个邮箱/企业时又会预取成全账号聚合"


def test_state_preamble_has_kill_switch():
    assert 'os.environ.get("SALES_AGENT_STATE_PREAMBLE", "").strip() == "0"' in API


# --- Long-term: accumulated preferences ---

def test_preferences_persist_and_dedupe(db):
    first = append_account_icp_preferences(db, USER, exclude=["教育培训", "没有官网的"])
    assert first["added"]["exclude"] == ["教育培训", "没有官网的"]

    again = append_account_icp_preferences(db, USER, exclude=["教育培训", "预算低的"])
    assert again["added"]["exclude"] == ["预算低的"], "已有的不该重复追加"
    assert again["negative_icp"] == ["教育培训", "没有官网的", "预算低的"]


def test_signals_are_separate_from_exclusions(db):
    out = append_account_icp_preferences(db, USER, signals=["有博客的", "在招 SEO 的"])
    assert out["observable_signals"] == ["有博客的", "在招 SEO 的"]
    assert out["negative_icp"] == []


def test_creates_icp_row_when_missing(db):
    assert db.query(SalesAgentAccountICPORM).count() == 0
    append_account_icp_preferences(db, USER, exclude=["教育培训"])
    assert db.query(SalesAgentAccountICPORM).count() == 1


def test_blank_items_ignored(db):
    out = append_account_icp_preferences(db, USER, exclude=["", "  ", "教育培训"])
    assert out["negative_icp"] == ["教育培训"]


@pytest.fixture()
def company_db():
    """This one really runs the query, so the company table must be created too."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SalesAgentAccountICPORM.__table__, SalesAgentCompanyORM.__table__])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _company(db, name: str, industry: str, region: str) -> None:
    db.add(SalesAgentCompanyORM(name=name, industry=industry, region=region))


def test_negative_icp_actually_filters_companies_out(company_db):
    """Written but never read is the easiest trap here -- that's how it was before 2026-08-23.

    This used to assert whether the source had `def _apply_negative_icp(` and its call line. Named
    "is actually read", it only confirmed the function existed and was called once -- renaming it turned it red for no reason,
    while real breakage (call still there, `rules` always parsed empty) stayed green. Now it actually runs the query.

    **This runs on SQLite and verifies "exclusions really reach the query and cover all three columns", not ILIKE
    case semantics.** SQLite has no native ILIKE; SQLAlchemy compiles to `lower(x) NOT LIKE lower(?)`,
    and SQLite's `lower()` only folds ASCII (`lower('ÄÖ')` returns unchanged); production PG uses native
    `NOT ILIKE`. With Chinese exclusions both sides are equivalent (Chinese has no case); with accented ones like `Müller`
    only PG is correct -- to really verify case, point DATABASE_URL at PG and run.
    """
    from agents.sales_agent.service.recommend import _apply_negative_icp

    _company(company_db, "好学教育科技", "教育培训", "上海")      # industry hit
    _company(company_db, "某集团", "工业制造", "教育路科技园")     # region hit
    _company(company_db, "教育家具制造", "工业制造", "上海")       # company name hit
    _company(company_db, "正常制造公司", "工业制造", "上海")
    company_db.commit()

    icp = SalesAgentAccountICPORM(user_id=USER, negative_icp_json=json.dumps(["教育"], ensure_ascii=False))
    kept = _apply_negative_icp(company_db.query(SalesAgentCompanyORM), icp).all()

    assert [c.name for c in kept] == ["正常制造公司"], "排除项没真的落到查询上"


def test_unrecognizable_exclusions_do_not_silently_drop_everyone(company_db):
    """Things like 「预算低的」 ("low budget") can't be recognized at this layer and by design aren't blocked -- left to scoring and manual filtering.

    Pin this boundary: if this gets "conveniently strengthened" into fuzzy matching, one exclusion can empty the results,
    and the symptom is just "no recommendations", very hard to trace back here.
    """
    from agents.sales_agent.service.recommend import _apply_negative_icp

    _company(company_db, "正常制造公司", "工业制造", "上海")
    company_db.commit()

    icp = SalesAgentAccountICPORM(user_id=USER, negative_icp_json=json.dumps(["预算低的"], ensure_ascii=False))
    assert _apply_negative_icp(company_db.query(SalesAgentCompanyORM), icp).count() == 1


def test_no_icp_row_leaves_the_query_untouched(company_db):
    from agents.sales_agent.service.recommend import _apply_negative_icp

    _company(company_db, "正常制造公司", "工业制造", "上海")
    company_db.commit()
    assert _apply_negative_icp(company_db.query(SalesAgentCompanyORM), None).count() == 1


# --- Gather: prefetch by intent; don't rely on the model "remembering" the tool exists (05-evaluation §8.4) ---

@pytest.fixture()
def gather_deps():
    from datetime import datetime

    from agents.sales_agent.deps import SalesAgentDeps
    from agents.sales_agent.models import SalesAgentCompanyORM, SalesAgentEmailDeliveryORM
    from geo.models.user import UserORM

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(UserORM(id=USER, email="gather@test.local", password_hash="x", is_active=True))
    company = SalesAgentCompanyORM(name="开信科技", industry="工业制造", region="上海")
    s.add(company)
    s.flush()
    s.add(SalesAgentEmailDeliveryORM(
        user_id=USER, company_id=company.id, recipient_email="a@kaixin.test",
        subject="AI 搜索可见度诊断", status="sent",
        sent_at=datetime(2026, 8, 20, 2, 0, 0), first_opened_at=datetime(2026, 8, 20, 6, 0, 0),
        open_count=3, report_visit_count=1, tracking_token="t1",
    ))
    s.commit()
    try:
        yield SalesAgentDeps(account_id=USER, user_id=USER, db=s)
    finally:
        s.close()


def test_gather_prefetches_delivery_records_for_lookup(gather_deps):
    """Once lookup is detected, fetch the send records and put them in context, instead of prompting the model to call a read-only tool.

    §8.4 verbatim: "don't rely on the model 'remembering' the tool exists -- 2026-08-23 showed more specific old prompt
    rules override new tools". When the user asks "which companies opened the email", the model must already have these records.
    """
    from agents.sales_agent.api import _gather_preamble

    text = _gather_preamble(gather_deps, "lookup")
    assert "开信科技" in text, "公司名要进上下文,否则模型只能说'还没有记录'"
    assert "已开信 3 次" in text
    assert "不要再调 get_outreach_delivery_status" in text, "取好了就别让模型再调一遍"


@pytest.mark.parametrize("intent", ["lead", "chat", "faq", "outreach"])
def test_gather_only_fires_for_lookup(gather_deps, intent):
    """The lead half (profile + filters) is unconditionally added by _state_preamble every turn; not repeated here."""
    from agents.sales_agent.api import _gather_preamble

    assert _gather_preamble(gather_deps, intent) == ""


def test_gather_has_kill_switch(gather_deps, monkeypatch):
    from agents.sales_agent.api import _gather_preamble

    monkeypatch.setenv("SALES_AGENT_GATHER", "0")
    assert _gather_preamble(gather_deps, "lookup") == ""


def test_gather_failure_never_blocks_the_turn(gather_deps, monkeypatch):
    """Prefetch is a bonus -- if fetching blows up it just omits a section; it must not take down the whole turn."""
    from agents.sales_agent import service
    from agents.sales_agent.api import _gather_preamble

    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "agent_delivery_status", boom)
    assert _gather_preamble(gather_deps, "lookup") == ""
