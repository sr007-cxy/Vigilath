"""Target card semantics of Sales Agent checkpoint undo.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_undo_checkpoint.py -q
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentUndoCheckpointORM,
)
from agents.sales_agent.service import record_lead_flow_checkpoint, restore_lead_flow_checkpoint, session_bundle
from geo.database import Base
from geo.models.user import UserORM

USER = 1000002


@pytest.fixture()
def deps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="undo@test.local", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000001", title="Sales Agent 会话")
    db.add(session)
    db.commit()
    d = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id, session_public_id=session.public_id)
    try:
        yield d
    finally:
        db.close()


def _assistant_card(deps: SalesAgentDeps, step: str, title: str) -> SalesAgentChatMessageORM:
    card = {
        "tool": "recommend_companies",
        "data": {
            "status": "need_more_info",
            "needs_user_input": True,
            "question_card": {"id": step, "field": step, "title": title},
        },
    }
    row = SalesAgentChatMessageORM(
        session_id=deps.session_id,
        role="assistant",
        content=title,
        intent="lead",
        metadata_json=json.dumps({"tool_cards": [card]}, ensure_ascii=False),
    )
    deps.db.add(row)
    deps.db.commit()
    deps.db.refresh(row)
    return row


def _user_answer(deps: SalesAgentDeps, content: str) -> SalesAgentChatMessageORM:
    row = SalesAgentChatMessageORM(session_id=deps.session_id, role="user", content=content, intent="lead")
    deps.db.add(row)
    deps.db.commit()
    deps.db.refresh(row)
    return row


def _record_checkpoint_after_answer(deps: SalesAgentDeps, step: str, label: str, values: list[str] | None = None) -> SalesAgentUndoCheckpointORM:
    flow = {"step": step, step: values or [label]}
    deps.tool_state["lead_flow"] = flow
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    assert row is not None
    row.context_json = json.dumps({"tool_state": {"lead_flow": flow}}, ensure_ascii=False)
    deps.db.commit()
    record_lead_flow_checkpoint(deps, flow, step, label)
    checkpoint = (
        deps.db.query(SalesAgentUndoCheckpointORM)
        .filter(SalesAgentUndoCheckpointORM.session_id == deps.session_id, SalesAgentUndoCheckpointORM.step == step)
        .one()
    )
    return checkpoint


def test_session_bundle_exposes_undo_checkpoint_on_its_source_card(deps):
    source = _assistant_card(deps, "target_industries", "目标行业")
    _user_answer(deps, "企业服务")
    checkpoint = _record_checkpoint_after_answer(deps, "target_industries", "目标行业")
    _assistant_card(deps, "target_regions", "目标地区")

    bundle = session_bundle(deps.db, deps.user_id, session_id=deps.session_public_id)

    source_msg = next(m for m in bundle["messages"] if m["id"] == source.id)
    card = source_msg["metadata"]["tool_cards"][0]
    assert card["data"]["undo_checkpoint"] == {
        "checkpoint_id": checkpoint.id,
        "step": "target_industries",
        "label": "目标行业",
    }


def test_entry_card_gets_undo_checkpoint_as_soon_as_it_is_visible(deps):
    source = _assistant_card(deps, "sales_agent_entry_action", "选择操作")
    record_lead_flow_checkpoint(deps, {}, "sales_agent_entry_action", "选择操作")
    checkpoint = deps.db.query(SalesAgentUndoCheckpointORM).one()

    bundle = session_bundle(deps.db, deps.user_id, session_id=deps.session_public_id)

    source_msg = next(m for m in bundle["messages"] if m["id"] == source.id)
    card = source_msg["metadata"]["tool_cards"][0]
    assert card["data"]["undo_checkpoint"] == {
        "checkpoint_id": checkpoint.id,
        "step": "sales_agent_entry_action",
        "label": "选择操作",
    }


def test_target_company_size_checkpoint_keeps_multi_selected_values(deps):
    source = _assistant_card(deps, "target_company_size", "目标企业规模")
    _user_answer(deps, "1-10 名员工、11-100 名员工")
    checkpoint = _record_checkpoint_after_answer(
        deps,
        "target_company_size",
        "目标企业规模",
        ["1-10 名员工", "11-100 名员工"],
    )
    _assistant_card(deps, "target_industries", "目标行业")

    bundle = session_bundle(deps.db, deps.user_id, session_id=deps.session_public_id)

    source_msg = next(m for m in bundle["messages"] if m["id"] == source.id)
    card = source_msg["metadata"]["tool_cards"][0]
    assert card["data"]["undo_checkpoint"]["checkpoint_id"] == checkpoint.id
    assert card["data"]["question_card"].get("selected_values") == ["1-10 名员工", "11-100 名员工"]


def test_target_regions_checkpoint_keeps_multi_selected_values(deps):
    source = _assistant_card(deps, "target_regions", "目标地区")
    _user_answer(deps, "北京、上海")
    checkpoint = _record_checkpoint_after_answer(deps, "target_regions", "目标地区", ["北京", "上海"])
    _assistant_card(deps, "recommendations", "企业推荐")

    bundle = session_bundle(deps.db, deps.user_id, session_id=deps.session_public_id)

    source_msg = next(m for m in bundle["messages"] if m["id"] == source.id)
    card = source_msg["metadata"]["tool_cards"][0]
    assert card["data"]["undo_checkpoint"]["checkpoint_id"] == checkpoint.id
    assert card["data"]["question_card"].get("selected_values") == ["北京", "上海"]


def test_restore_target_checkpoint_deletes_messages_below_that_card(deps):
    source = _assistant_card(deps, "target_industries", "目标行业")
    _user_answer(deps, "企业服务")
    target_checkpoint = _record_checkpoint_after_answer(deps, "target_industries", "目标行业")
    _assistant_card(deps, "target_regions", "目标地区")
    _user_answer(deps, "全国")
    later_checkpoint = _record_checkpoint_after_answer(deps, "target_regions", "目标地区")

    restored = restore_lead_flow_checkpoint(deps, target_checkpoint.id)

    assert restored["restored"] is True
    assert restored["checkpoint_id"] == target_checkpoint.id
    assert restored["message_watermark_id"] == source.id
    messages = deps.db.query(SalesAgentChatMessageORM).filter(SalesAgentChatMessageORM.session_id == deps.session_id).all()
    assert [m.id for m in messages] == [source.id]
    assert deps.db.get(SalesAgentUndoCheckpointORM, target_checkpoint.id).consumed_at is not None
    assert deps.db.get(SalesAgentUndoCheckpointORM, later_checkpoint.id).consumed_at is not None
    assert (deps.tool_state.get("lead_flow") or {}).get("step") == "target_industries"
