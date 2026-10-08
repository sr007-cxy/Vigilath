"""Delivery status is queryable.

Before this, none of the 11 tools could read delivery records, so the Agent could only answer "no actual send records to view,
we do not send email or SMS on your behalf" -- both wrong: the data lives in `sales_agent_email_deliveries`,
and the platform really does send after the user confirms each email.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_delivery_status.py -q
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.agent import route_intent
from agents.sales_agent.api import _run_deterministic_flow
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.methods import append_chat_message
from agents.sales_agent.models import SalesAgentChatMessageORM, SalesAgentChatSessionORM, SalesAgentCompanyORM, SalesAgentEmailDeliveryORM, SalesAgentLeadRecommendationORM
from agents.sales_agent.service import agent_delivery_status, list_sales_agent_email_deliveries, track_sales_agent_email_open
from geo.database import Base
from geo.models.user import UserORM

USER = 1
OTHER = 2


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SalesAgentCompanyORM.__table__, SalesAgentEmailDeliveryORM.__table__])
    s = sessionmaker(bind=engine)()
    s.add_all([
        SalesAgentCompanyORM(id=1, name="示例清洁科技"),
        SalesAgentCompanyORM(id=2, name="Acme Cloud"),
    ])
    s.add_all([
        SalesAgentEmailDeliveryORM(id=1, user_id=USER, lead_id=11, company_id=1, recipient_email="a@cleantech.example",
                                   subject="AI 搜索可见度", message_type="test", status="sent", sent_at=datetime.utcnow(),
                                   first_opened_at=datetime.utcnow(), open_count=3, report_visit_count=1,
                                   created_at=datetime.utcnow()),
        SalesAgentEmailDeliveryORM(id=2, user_id=USER, lead_id=12, company_id=2, recipient_email="b@acmecloud.example",
                                   subject="竞品对比", status="failed", failure_reason="SMTP 550",
                                   created_at=datetime.utcnow()),
        SalesAgentEmailDeliveryORM(id=3, user_id=USER, company_id=1, recipient_email="c@cleantech.example",
                                   subject="旧的", status="sent", sent_at=datetime.utcnow() - timedelta(days=40),
                                   created_at=datetime.utcnow() - timedelta(days=40)),
        SalesAgentEmailDeliveryORM(id=4, user_id=OTHER, company_id=1, recipient_email="x@other.com",
                                   subject="别人的", status="sent", created_at=datetime.utcnow()),
    ])
    s.commit()
    try:
        yield s
    finally:
        s.close()


def test_returns_real_records_not_empty(db):
    """Core: if records exist, answer with them; never say "no send records" again."""
    out = agent_delivery_status(db, USER)
    assert out["total_matched"] == 3
    assert out["summary"]["sent"] == 2 and out["summary"]["failed"] == 1


def test_only_current_user(db):
    assert all(d["recipient_email"] != "x@other.com" for d in agent_delivery_status(db, USER)["deliveries"])


def test_filter_by_company_name(db):
    out = agent_delivery_status(db, USER, company_name="示例清洁")
    assert out["total_matched"] == 2
    assert {d["company_name"] for d in out["deliveries"]} == {"示例清洁科技"}


def test_filter_by_partial_recipient(db):
    assert agent_delivery_status(db, USER, company_name="acmecloud")["total_matched"] == 1


def test_filter_by_lead(db):
    out = agent_delivery_status(db, USER, lead_id=11)
    assert out["total_matched"] == 1 and out["deliveries"][0]["delivery_id"] == 1


def test_filter_by_status(db):
    out = agent_delivery_status(db, USER, status="failed")
    assert out["total_matched"] == 1 and out["deliveries"][0]["failure_reason"] == "SMTP 550"


def test_filter_by_days(db):
    """The one from 40 days ago must not appear in "last 7 days"."""
    assert agent_delivery_status(db, USER, days=7)["total_matched"] == 2


def test_open_and_report_visit_surface(db):
    row = next(d for d in agent_delivery_status(db, USER)["deliveries"] if d["delivery_id"] == 1)
    assert row["opened"] and row["open_count"] == 3 and row["report_visit_count"] == 1


def test_no_body_in_payload(db):
    """Trimmed fields for the model: no body, no company object, so it doesn't flood the context."""
    row = agent_delivery_status(db, USER)["deliveries"][0]
    assert "content" not in row and "company" not in row


def test_limit_is_capped(db):
    out = agent_delivery_status(db, USER, limit=999)
    assert out["returned"] <= 50


def test_empty_is_explicit_not_error(db):
    out = agent_delivery_status(db, USER, company_name="查无此公司")
    assert out["total_matched"] == 0 and out["deliveries"] == []


def test_note_states_platform_sends(db):
    """The metric definition is in the return value; the model repeats it and won't say "we don't send email" again."""
    assert "真实发出" in agent_delivery_status(db, USER)["note"]


def test_today_email_count_query_is_handled_deterministically():
    """"How many emails did I send today" is a read-only DB query; it must not detour to the model and surface a generic failure."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000002")
        s.add(session)
        company = SalesAgentCompanyORM(name="今天科技")
        s.add(company)
        s.flush()
        s.add(SalesAgentEmailDeliveryORM(
            user_id=USER, company_id=company.id, recipient_email="today@example.com",
            subject="AI 搜索可见度", status="sent", sent_at=datetime.utcnow(), created_at=datetime.utcnow(),
        ))
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)
        message = "今天发了几封邮件"
        intent = route_intent(message)
        assert intent == "lookup"
        append_chat_message(deps, "user", message, intent=intent)

        handled = asyncio.run(_run_deterministic_flow(deps, message, intent))

        assert handled is True
        reply = (
            s.query(SalesAgentChatMessageORM)
            .filter(SalesAgentChatMessageORM.session_id == session.id, SalesAgentChatMessageORM.role == "assistant")
            .order_by(SalesAgentChatMessageORM.id.desc())
            .first()
        )
        assert reply is not None
        assert "今天总共尝试发送 1 封邮件" in reply.content
        assert "成功 1 封,失败 0 封" in reply.content
    finally:
        s.close()


def test_delivery_status_query_failure_returns_readable_fallback(monkeypatch):
    """harness-style fallback: if read-only stats blow up, still persist a readable reply; never let the frontend show "processing incomplete"."""
    from agents.sales_agent import api

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000003")
        s.add(session)
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)

        def boom(*args, **kwargs):
            raise RuntimeError("delivery table missing")

        monkeypatch.setattr(api, "agent_delivery_status", boom)
        message = "今天发了几封邮件"
        intent = route_intent(message)
        append_chat_message(deps, "user", message, intent=intent)

        handled = asyncio.run(_run_deterministic_flow(deps, message, intent))

        assert handled is True
        reply = (
            s.query(SalesAgentChatMessageORM)
            .filter(SalesAgentChatMessageORM.session_id == session.id, SalesAgentChatMessageORM.role == "assistant")
            .order_by(SalesAgentChatMessageORM.id.desc())
            .first()
        )
        assert reply is not None
        assert "暂时没法读取邮件/短信发送记录" in reply.content
    finally:
        s.close()


def test_today_email_total_count_includes_failed_deliveries():
    """When asked for the total sent, 50 succeeded + 35 failed must clearly answer 85 total attempts."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000030")
        s.add(session)
        companies = [SalesAgentCompanyORM(name=f"邮件企业{i}") for i in range(1, 86)]
        s.add_all(companies)
        s.flush()
        now = datetime.utcnow()
        rows = []
        for idx, company in enumerate(companies[:50], start=1):
            rows.append(SalesAgentEmailDeliveryORM(
                user_id=USER, company_id=company.id, recipient_email=f"sent{idx}@example.com",
                subject="今天", status="sent", sent_at=now, created_at=now,
            ))
        for idx, company in enumerate(companies[50:], start=1):
            rows.append(SalesAgentEmailDeliveryORM(
                user_id=USER, company_id=company.id, recipient_email=f"failed{idx}@example.com",
                subject="今天", status="failed", failed_at=now, created_at=now,
            ))
        s.add_all(rows)
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)
        message = "今天总共发了多少"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "今天总共尝试发送 85 封邮件" in reply
        assert "成功 50 封,失败 35 封" in reply
        assert "成功发送涉及 50 家企业" in reply
        assert "尝试发送涉及 85 家企业" in reply
    finally:
        s.close()



def _latest_assistant_content(s, session_id: int) -> str:
    row = (
        s.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == session_id, SalesAgentChatMessageORM.role == "assistant")
        .order_by(SalesAgentChatMessageORM.id.desc())
        .first()
    )
    assert row is not None
    return row.content


def _delivery_flow_deps_with_rows(today_count: int = 1, yesterday_count: int = 1, before_yesterday_count: int = 0):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000010")
    s.add(session)
    company = SalesAgentCompanyORM(name="统计科技")
    s.add(company)
    s.flush()
    bj_now = datetime.utcnow() + timedelta(hours=8)
    bj_today = bj_now.replace(hour=10, minute=0, second=0, microsecond=0)
    rows = []
    for idx in range(today_count):
        rows.append(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email=f"today{idx}@example.com", subject="今天", status="sent", sent_at=bj_today - timedelta(hours=8), created_at=bj_today - timedelta(hours=8)))
    for idx in range(yesterday_count):
        bj_yesterday = (bj_now - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        rows.append(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email=f"yesterday{idx}@example.com", subject="昨天", status="sent", sent_at=bj_yesterday - timedelta(hours=8), created_at=bj_yesterday - timedelta(hours=8)))
    for idx in range(before_yesterday_count):
        bj_before = (bj_now - timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
        rows.append(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email=f"before{idx}@example.com", subject="前天", status="sent", sent_at=bj_before - timedelta(hours=8), created_at=bj_before - timedelta(hours=8)))
    s.add_all(rows)
    s.commit()
    deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)
    return s, session, deps


def test_yesterday_email_count_query_uses_exact_day_window():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=2, yesterday_count=1)
    try:
        message = "昨天发了几封邮件"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "昨天总共尝试发送 1 封邮件" in reply
        assert "总共尝试发送 3 封邮件" not in reply
    finally:
        s.close()


def test_delivery_count_followup_yesterday_inherits_previous_query():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=2, yesterday_count=1)
    try:
        first = "今天发了几封邮件"
        append_chat_message(deps, "user", first, intent=route_intent(first))
        assert asyncio.run(_run_deterministic_flow(deps, first, route_intent(first))) is True

        followup = "昨天呢？"
        append_chat_message(deps, "user", followup, intent=route_intent(followup))
        assert asyncio.run(_run_deterministic_flow(deps, followup, route_intent(followup))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "昨天总共尝试发送 1 封邮件" in reply
    finally:
        s.close()


def test_delivery_count_yesterday_and_today_query_sums_range():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=2, yesterday_count=1, before_yesterday_count=5)
    try:
        message = "昨天跟今天一共发了多少封邮件"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "昨天和今天总共尝试发送 3 封邮件" in reply
        assert "总共尝试发送 8 封邮件" not in reply
    finally:
        s.close()


def test_delivery_count_unspecified_day_asks_for_date_instead_of_failing():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=1, yesterday_count=1)
    try:
        message = "某一天发了几封邮件"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "需要告诉我要查哪一天" in reply
    finally:
        s.close()


def test_recommendation_count_query_is_handled_deterministically():
    """"How many companies were recommended" is a recommendation-history stat; it must not fall to the model and report incomplete."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000011")
        s.add(session)
        c1 = SalesAgentCompanyORM(name="推荐一号", website="https://one.example")
        c2 = SalesAgentCompanyORM(name="推荐二号")
        s.add_all([c1, c2])
        s.flush()
        s.add_all([
            SalesAgentLeadRecommendationORM(user_id=USER, company_id=c1.id, score=80, status="New", created_at=datetime.utcnow()),
            SalesAgentLeadRecommendationORM(user_id=USER, company_id=c2.id, score=70, status="New", created_at=datetime.utcnow()),
        ])
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)
        message = "推荐了几家企业"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "已推荐 2 家企业" in reply
    finally:
        s.close()


def test_recommendation_stats_followup_uses_last_stats_domain_not_old_delivery_state():
    """Harness state records the latest stats domain: after email then recommendation queries, "what about yesterday" inherits recommendation stats."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000012")
        s.add(session)
        company = SalesAgentCompanyORM(name="推荐昨天")
        s.add(company)
        s.flush()
        bj_now = datetime.utcnow() + timedelta(hours=8)
        bj_yesterday = (bj_now - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        s.add(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email="old-delivery@example.com", status="sent", created_at=bj_yesterday - timedelta(hours=8)))
        s.add(SalesAgentLeadRecommendationORM(user_id=USER, company_id=company.id, score=80, status="New", created_at=bj_yesterday - timedelta(hours=8)))
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)

        first = "今天发了几封邮件"
        append_chat_message(deps, "user", first, intent=route_intent(first))
        assert asyncio.run(_run_deterministic_flow(deps, first, route_intent(first))) is True

        second = "推荐了几家企业"
        append_chat_message(deps, "user", second, intent=route_intent(second))
        assert asyncio.run(_run_deterministic_flow(deps, second, route_intent(second))) is True

        followup = "昨天呢？"
        append_chat_message(deps, "user", followup, intent=route_intent(followup))
        assert asyncio.run(_run_deterministic_flow(deps, followup, route_intent(followup))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "昨天已推荐 1 家企业" in reply
        assert "成功发送" not in reply
    finally:
        s.close()


def test_explicit_delivery_query_overrides_previous_recommendation_stats():
    """Production regression: after recommendation stats, an explicit mention of sending email must not keep answering about recommended companies."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000013")
        s.add(session)
        company = SalesAgentCompanyORM(name="昨天邮件企业")
        s.add(company)
        s.flush()
        bj_now = datetime.utcnow() + timedelta(hours=8)
        bj_yesterday = (bj_now - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        s.add(SalesAgentLeadRecommendationORM(user_id=USER, company_id=company.id, score=80, status="New", created_at=bj_yesterday - timedelta(hours=8)))
        s.add(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email="sent-yesterday@example.com", status="sent", sent_at=bj_yesterday - timedelta(hours=8), created_at=datetime.utcnow()))
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)

        recommendation = "今天推荐了几家企业"
        append_chat_message(deps, "user", recommendation, intent=route_intent(recommendation))
        assert asyncio.run(_run_deterministic_flow(deps, recommendation, route_intent(recommendation))) is True

        delivery = "昨天发了几封邮件"
        append_chat_message(deps, "user", delivery, intent=route_intent(delivery))
        assert asyncio.run(_run_deterministic_flow(deps, delivery, route_intent(delivery))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "昨天总共尝试发送 1 封邮件" in reply
        assert "已推荐" not in reply
    finally:
        s.close()


def test_delivery_presence_and_company_count_queries_are_not_ambiguous_after_recommendation_stats():
    """"Did I send any email / how many companies did I send to" is a send stat, not a recommendation stat, and not an ambiguous clarification."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000014")
        s.add(session)
        company = SalesAgentCompanyORM(name="前天邮件企业")
        s.add(company)
        s.flush()
        bj_now = datetime.utcnow() + timedelta(hours=8)
        bj_yesterday = (bj_now - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        bj_before = (bj_now - timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
        s.add(SalesAgentLeadRecommendationORM(user_id=USER, company_id=company.id, score=80, status="New", created_at=bj_yesterday - timedelta(hours=8)))
        s.add(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email="sent-before@example.com", status="sent", sent_at=bj_before - timedelta(hours=8), created_at=datetime.utcnow()))
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)

        recommendation = "今天推荐了几家企业"
        append_chat_message(deps, "user", recommendation, intent=route_intent(recommendation))
        assert asyncio.run(_run_deterministic_flow(deps, recommendation, route_intent(recommendation))) is True

        presence = "前天有没有发送邮件"
        append_chat_message(deps, "user", presence, intent=route_intent(presence))
        assert asyncio.run(_run_deterministic_flow(deps, presence, route_intent(presence))) is True
        reply = _latest_assistant_content(s, session.id)
        assert "前天总共尝试发送 1 封邮件" in reply
        assert "你是想查" not in reply
        assert "已推荐" not in reply

        company_count = "查看前天发送了多少家？"
        append_chat_message(deps, "user", company_count, intent=route_intent(company_count))
        assert asyncio.run(_run_deterministic_flow(deps, company_count, route_intent(company_count))) is True
        reply = _latest_assistant_content(s, session.id)
        assert "前天共尝试给 1 家企业发送邮件" in reply
        assert "成功企业 1 家" in reply
        assert "已推荐" not in reply
    finally:
        s.close()


def test_delivery_company_detail_followup_inherits_today_sent_scope():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=2, yesterday_count=1)
    try:
        first = "今天发了几封邮件呢？"
        append_chat_message(deps, "user", first, intent=route_intent(first))
        assert asyncio.run(_run_deterministic_flow(deps, first, route_intent(first))) is True

        followup = "哪2家企业呢"
        append_chat_message(deps, "user", followup, intent=route_intent(followup))
        assert asyncio.run(_run_deterministic_flow(deps, followup, route_intent(followup))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "今天共尝试给 1 家企业发送邮件" in reply
        assert "成功企业 1 家" in reply
        assert "下面仅展示最近 2 条明细" in reply
        assert "统计科技" in reply
        assert "yesterday0@example.com" not in reply
        assert "处理未完成" not in reply
    finally:
        s.close()


def test_delivery_company_detail_followup_failure_returns_readable_fallback(monkeypatch):
    from agents.sales_agent import api

    s, session, deps = _delivery_flow_deps_with_rows(today_count=1, yesterday_count=0)
    try:
        first = "今天发了几封邮件呢？"
        append_chat_message(deps, "user", first, intent=route_intent(first))
        assert asyncio.run(_run_deterministic_flow(deps, first, route_intent(first))) is True

        def boom(*args, **kwargs):
            raise RuntimeError("delivery detail failed")

        monkeypatch.setattr(api, "agent_delivery_status", boom)
        followup = "哪1家企业呢"
        append_chat_message(deps, "user", followup, intent=route_intent(followup))
        assert asyncio.run(_run_deterministic_flow(deps, followup, route_intent(followup))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "暂时没法读取邮件/短信发送记录" in reply
        assert "处理未完成" not in reply
    finally:
        s.close()


def test_recent_three_days_delivery_count_uses_explicit_scope_label():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=2, yesterday_count=1, before_yesterday_count=3)
    try:
        message = "这三天总共发了多少封呢"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "最近 3 天总共尝试发送 6 封邮件" in reply
        assert "成功 6 封,失败 0 封" in reply
        assert "管理范围" not in reply
    finally:
        s.close()


def test_stats_scope_confirmation_after_recent_three_days_does_not_fail():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=2, yesterday_count=1, before_yesterday_count=3)
    try:
        first = "这三天总共发了多少封呢"
        append_chat_message(deps, "user", first, intent=route_intent(first))
        assert asyncio.run(_run_deterministic_flow(deps, first, route_intent(first))) is True

        followup = "是这三天的吗？"
        append_chat_message(deps, "user", followup, intent=route_intent(followup))
        assert asyncio.run(_run_deterministic_flow(deps, followup, route_intent(followup))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "上一条查的是最近 3 天的邮件发送统计" in reply
        assert "实际发送时间" in reply
    finally:
        s.close()


def test_stats_scope_confirmation_without_context_asks_for_concrete_query():
    s, session, deps = _delivery_flow_deps_with_rows(today_count=1, yesterday_count=0)
    try:
        message = "是这三天的吗？"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        reply = _latest_assistant_content(s, session.id)
        assert "还没有上一条可以确认的统计口径" in reply
        assert "处理未完成" not in reply
    finally:
        s.close()


def test_recommendation_detail_query_returns_readonly_history_card():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000015")
        s.add(session)
        company = SalesAgentCompanyORM(name="推荐明细企业", website="https://detail.example", industry="企业服务", region="上海")
        s.add(company)
        s.flush()
        s.add(SalesAgentLeadRecommendationORM(user_id=USER, company_id=company.id, score=88, status="New", created_at=datetime.utcnow()))
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)
        message = "今天推荐了哪些企业"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        row = (
            s.query(SalesAgentChatMessageORM)
            .filter(SalesAgentChatMessageORM.session_id == session.id, SalesAgentChatMessageORM.role == "assistant")
            .order_by(SalesAgentChatMessageORM.id.desc())
            .first()
        )
        assert row is not None
        assert "今天已推荐 1 家企业" in row.content
        metadata = json.loads(row.metadata_json)
        card = metadata["tool_cards"][0]
        assert card["tool"] == "get_recommended_companies"
        data = card["data"]
        assert data["readonly"] is True
        assert data["disable_email_actions"] is True
        assert data["disable_outreach_actions"] is True
        assert data["leads"]
    finally:
        s.close()


def test_recommendation_count_and_detail_query_returns_card_not_plain_stats():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
        session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000016")
        s.add(session)
        company = SalesAgentCompanyORM(name="推荐具体企业", website="https://specific.example")
        s.add(company)
        s.flush()
        s.add(SalesAgentLeadRecommendationORM(user_id=USER, company_id=company.id, score=81, status="New", created_at=datetime.utcnow()))
        s.commit()
        deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)
        message = "推荐企业有多少家 具体哪些"
        append_chat_message(deps, "user", message, intent=route_intent(message))

        assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True

        row = (
            s.query(SalesAgentChatMessageORM)
            .filter(SalesAgentChatMessageORM.session_id == session.id, SalesAgentChatMessageORM.role == "assistant")
            .order_by(SalesAgentChatMessageORM.id.desc())
            .first()
        )
        assert row is not None
        assert "已推荐 1 家企业" in row.content
        metadata = json.loads(row.metadata_json)
        assert metadata["tool_cards"][0]["tool"] == "get_recommended_companies"
    finally:
        s.close()


def test_list_filter_by_company_id(db):
    out = list_sales_agent_email_deliveries(db, USER, company_id=1)
    assert out["total"] == 2
    assert {d["recipient_email"] for d in out["deliveries"]} == {"a@cleantech.example", "c@cleantech.example"}


def test_list_filter_by_lead_id(db):
    out = list_sales_agent_email_deliveries(db, USER, lead_id=12)
    assert out["total"] == 1
    assert out["deliveries"][0]["recipient_email"] == "b@acmecloud.example"


def test_list_filter_keeps_user_scope(db):
    out = list_sales_agent_email_deliveries(db, USER, company_id=1)
    assert all(d["user_id"] == USER for d in out["deliveries"])
    assert "x@other.com" not in {d["recipient_email"] for d in out["deliveries"]}


def test_admin_list_filter_combines_target_user_and_company(db):
    out = list_sales_agent_email_deliveries(db, USER, admin=True, target_user_id=OTHER, company_id=1)
    assert out["total"] == 1
    assert out["deliveries"][0]["recipient_email"] == "x@other.com"


def test_list_pagination_metadata_after_filters(db):
    out = list_sales_agent_email_deliveries(db, USER, company_id=1, limit=1, offset=1)
    assert out["total"] == 2
    assert out["limit"] == 1
    assert out["offset"] == 1
    assert len(out["deliveries"]) == 1


def test_test_email_open_pixel_marks_read(db):
    delivery = db.get(SalesAgentEmailDeliveryORM, 1)
    assert delivery is not None
    assert delivery.read_at is None
    assert delivery.open_count == 3

    assert track_sales_agent_email_open(db, delivery.tracking_token, user_agent="pytest", ip_hash="local") is True

    out = list_sales_agent_email_deliveries(db, USER, read="read")
    assert out["total"] == 1
    row = out["deliveries"][0]
    assert row["id"] == delivery.id
    assert row["read_at"]
    assert row["open_count"] == 4


def test_test_email_unread_filter_includes_unopened_records(db):
    delivery = db.get(SalesAgentEmailDeliveryORM, 1)
    assert delivery is not None
    assert track_sales_agent_email_open(db, delivery.tracking_token, user_agent="pytest", ip_hash="local") is True

    out = list_sales_agent_email_deliveries(db, USER, read="unread")
    assert out["total"] == 2
    assert delivery.id not in {row["id"] for row in out["deliveries"]}


# --- Asking by name: "has this mailbox/company been emailed?" ---
#
# Production case: asked `has zhangsan@company201.example.com been emailed`, answered "the managed scope attempted 950 emails in total" --
# every number right, the answer entirely wrong. This group watches three things: the deterministic aggregate template must yield to such questions, prefetch must filter by subject,
# and if the model still answers with the account-wide metric, the harness overrides it using the same DB data.


def _named_target_session(public_id: str):
    """Build a minimal DB with companies/contacts/delivery records; return (session, deps)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(UserORM(id=USER, email="delivery@test.local", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id=public_id)
    s.add(session)
    s.flush()
    deps = SalesAgentDeps(account_id=USER, user_id=USER, db=s, session_id=session.id, session_public_id=session.public_id)
    return s, session, deps


def _add_noise_deliveries(s, count: int = 30):
    """A pile of records unrelated to the subject asked about -- an account-wide answer would cite their total."""
    now = datetime.utcnow()
    others = [SalesAgentCompanyORM(name=f"无关企业{i}") for i in range(count)]
    s.add_all(others)
    s.flush()
    s.add_all([
        SalesAgentEmailDeliveryORM(user_id=USER, company_id=c.id, recipient_email=f"noise{i}@example.com",
                                   subject="无关", status="sent", sent_at=now, created_at=now)
        for i, c in enumerate(others)
    ])


def test_named_email_query_is_not_answered_by_account_wide_template():
    """When a mailbox is named, the deterministic aggregate template must yield and hand the turn to the model to understand + query via tools."""
    s, _session, deps = _named_target_session("00000000-0000-4000-8000-000000000101")
    try:
        _add_noise_deliveries(s)
        s.commit()
        message = "zhangsan@company201.example.com 这个邮箱发过邮件吗?"
        intent = route_intent(message)
        append_chat_message(deps, "user", message, intent=intent)

        assert asyncio.run(_run_deterministic_flow(deps, message, intent)) is False
    finally:
        s.close()


def test_named_company_gather_preamble_is_filtered_to_that_company():
    """Prefetch filters by subject: the model gets this company's records, not account-wide totals."""
    from agents.sales_agent import api

    s, _session, deps = _named_target_session("00000000-0000-4000-8000-000000000102")
    try:
        _add_noise_deliveries(s)
        now = datetime.utcnow()
        company = SalesAgentCompanyORM(name="信阳市嘉欣电子科技有限公司")
        s.add(company)
        s.flush()
        s.add(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email="zhangsan@company201.example.com",
                                         subject="AI 搜索可见度", status="sent", sent_at=now, created_at=now))
        s.commit()
        message = "信阳市嘉欣电子科技有限公司 发过邮件吗?"
        intent = route_intent(message)

        preamble = api._gather_preamble(deps, intent, message)

        assert "信阳市嘉欣电子科技有限公司" in preamble
        assert "命中 1 条" in preamble
        assert "不要拿全账号/管理范围的总数充数" in preamble
        assert "31" not in preamble  # the account-wide total must not appear
    finally:
        s.close()


def test_account_wide_answer_is_overwritten_by_harness():
    """When the model answers with the account-wide metric, the harness overrides with a deterministic conclusion from the same DB data."""
    from agents.sales_agent import api

    s, _session, deps = _named_target_session("00000000-0000-4000-8000-000000000103")
    try:
        _add_noise_deliveries(s)
        now = datetime.utcnow()
        company = SalesAgentCompanyORM(name="信阳市嘉欣电子科技有限公司")
        s.add(company)
        s.flush()
        s.add(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email="zhangsan@company201.example.com",
                                         subject="AI 搜索可见度", status="sent", sent_at=now, created_at=now))
        s.commit()
        message = "zhangsan@company201.example.com 这个邮箱发过邮件吗?"
        intent = route_intent(message)

        corrected = api._delivery_target_answer_correction(
            deps, message, intent, "管理范围总共尝试发送 31 封邮件,成功 31 封。",
        )

        assert corrected
        assert "zhangsan@company201.example.com" in corrected
        assert "1 封" in corrected
        assert "31" not in corrected
    finally:
        s.close()


def test_correct_named_answer_is_left_alone():
    """If the model got it right, leave it alone -- the harness only fixes errors, it doesn't talk over the model."""
    from agents.sales_agent import api

    s, _session, deps = _named_target_session("00000000-0000-4000-8000-000000000104")
    try:
        now = datetime.utcnow()
        company = SalesAgentCompanyORM(name="信阳市嘉欣电子科技有限公司")
        s.add(company)
        s.flush()
        s.add(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email="zhangsan@company201.example.com",
                                         subject="AI 搜索可见度", status="sent", sent_at=now, created_at=now))
        s.commit()
        message = "zhangsan@company201.example.com 这个邮箱发过邮件吗?"
        intent = route_intent(message)

        assert api._delivery_target_answer_correction(
            deps, message, intent, "zhangsan@company201.example.com 一共有 1 封发送记录,已成功发出。",
        ) == ""
    finally:
        s.close()


def test_never_sent_distinguishes_known_company_and_gives_next_step():
    """Never sent != dead end: if the company is in the DB, say clearly "exists but never emailed" and give the next step to send."""
    from agents.sales_agent import api

    s, _session, deps = _named_target_session("00000000-0000-4000-8000-000000000105")
    try:
        _add_noise_deliveries(s)
        s.add(SalesAgentCompanyORM(name="信阳市嘉欣电子科技有限公司"))
        s.commit()
        message = "信阳市嘉欣电子科技有限公司 发过邮件吗?"
        intent = route_intent(message)

        corrected = api._delivery_target_answer_correction(
            deps, message, intent, "管理范围总共尝试发送 30 封邮件。",
        )

        assert "没有查到" in corrected
        assert "企业库里有这家企业" in corrected
        assert "生成一封跟进邮件" in corrected  # the next step must be actionable
    finally:
        s.close()


def test_never_sent_and_not_in_library_tells_user_how_to_import():
    """Company not in the DB at all is a different conclusion; the fallback should point to adding contact info/rephrasing, not just say never sent."""
    from agents.sales_agent import api

    s, _session, deps = _named_target_session("00000000-0000-4000-8000-000000000106")
    try:
        _add_noise_deliveries(s)
        s.commit()
        message = "信阳市嘉欣电子科技有限公司 发过邮件吗?"
        intent = route_intent(message)

        corrected = api._delivery_target_answer_correction(
            deps, message, intent, "管理范围总共尝试发送 30 封邮件。",
        )

        assert "企业库里也没有匹配到这个名称的企业" in corrected
        assert "帮我找一下" in corrected
    finally:
        s.close()


def test_full_name_miss_falls_back_to_core_name_and_says_so():
    """When the full name doesn't match the registered name, fall back once on the core term, and state explicitly that it is a fuzzy match."""
    from agents.sales_agent import api

    s, _session, deps = _named_target_session("00000000-0000-4000-8000-000000000107")
    try:
        now = datetime.utcnow()
        company = SalesAgentCompanyORM(name="嘉欣电子科技(河南)有限公司")  # the DB name lacks 「信阳市」 ("Xinyang City")
        s.add(company)
        s.flush()
        s.add(SalesAgentEmailDeliveryORM(user_id=USER, company_id=company.id, recipient_email="zhangsan@company201.example.com",
                                         subject="AI 搜索可见度", status="sent", sent_at=now, created_at=now))
        s.commit()
        message = "信阳市嘉欣电子科技有限公司 发过邮件吗?"
        intent = route_intent(message)

        corrected = api._delivery_target_answer_correction(
            deps, message, intent, "管理范围总共尝试发送 1 封邮件。",
        )

        assert "按全名「信阳市嘉欣电子科技有限公司」没有查到记录" in corrected
        assert "嘉欣电子科技" in corrected
        assert "模糊匹配" in corrected
    finally:
        s.close()


def test_aggregate_query_still_uses_deterministic_template():
    """Regression: aggregate questions without a named subject still use the deterministic template; the named branch must not disable it."""
    s, session, deps = _named_target_session("00000000-0000-4000-8000-000000000108")
    try:
        _add_noise_deliveries(s, count=3)
        s.commit()
        message = "今天发了几封邮件"
        intent = route_intent(message)
        append_chat_message(deps, "user", message, intent=intent)

        assert asyncio.run(_run_deterministic_flow(deps, message, intent)) is True
        reply = (
            s.query(SalesAgentChatMessageORM)
            .filter(SalesAgentChatMessageORM.session_id == session.id, SalesAgentChatMessageORM.role == "assistant")
            .order_by(SalesAgentChatMessageORM.id.desc())
            .first()
        )
        assert reply is not None and "今天总共尝试发送 3 封邮件" in reply.content
    finally:
        s.close()
