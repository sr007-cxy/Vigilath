import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.models.ai_telemetry import TopicDocPublishTaskORM
from geo.services import aitoearn_poller
from geo.services.aitoearn_client import AitoearnApiError


def _task(status="pending_url", *, started_at=None, flow_id="flow-1"):
    meta = {"channel": "aitoearn"}
    if flow_id:
        meta["flow_id"] = flow_id
    if started_at:
        meta["publish_started_at"] = started_at.isoformat()
    return SimpleNamespace(
        id=1, topic_id=2, status=status, last_error=None,
        channel_meta_json=json.dumps(meta),
    )


def _run(monkeypatch, task, flow):
    class Client:
        async def get_flow(self, _flow_id):
            if isinstance(flow, Exception):
                raise flow
            return flow

    monkeypatch.setattr(aitoearn_poller, "get_client", lambda: Client())
    monkeypatch.setattr(aitoearn_poller, "_audit", lambda *_a, **_k: None)
    return asyncio.run(aitoearn_poller.reconcile_task(object(), task))


def test_reconcile_marks_work_link_as_published(monkeypatch):
    task = _task()
    submitted = {}

    def submit(_db, current_task, url, **kwargs):
        submitted.update(url=url, proof=kwargs["proof"])
        current_task.status = "published"

    monkeypatch.setattr(
        aitoearn_poller.publish_task_service, "mark_published", submit)
    result = _run(monkeypatch, task, {"tasks": [{
        "status": 1,
        "workLink": "https://www.tiktok.com/@user/photo/123",
        "platformWorkId": "work-123",
        "linkStatus": "ready",
    }]})

    assert result == {
        "action": "published",
        "detail": "https://www.tiktok.com/@user/photo/123",
    }
    assert task.status == "published"
    assert submitted == {
        "url": "https://www.tiktok.com/@user/photo/123",
        "proof": {
            "source": "aitoearn",
            "flow_id": "flow-1",
            "platform_work_id": "work-123",
        },
    }


def test_reconcile_keeps_waiting_when_link_is_not_ready(monkeypatch):
    task = _task(status="publishing")
    result = _run(monkeypatch, task, {"tasks": [{
        "status": 2, "platformWorkId": "work-1", "linkStatus": "pending",
    }]})

    assert result["action"] == "none"
    assert task.status == "pending_url"
    assert task.last_error is None
    assert json.loads(task.channel_meta_json)["platform_work_id"] == "work-1"


def test_reconcile_marks_platform_failure(monkeypatch):
    task = _task()
    result = _run(monkeypatch, task, {"tasks": [{
        "status": -1, "errorMsg": "account expired",
    }]})

    assert result == {"action": "failed", "detail": "account expired"}
    assert task.status == "failed"
    assert task.last_error == "account expired"


def test_reconcile_leaves_link_failure_for_manual_fill(monkeypatch):
    task = _task()
    result = _run(monkeypatch, task, {"tasks": [{
        "status": 1, "linkStatus": "failed", "platformWorkId": "work-9",
    }]})

    assert result["action"] == "link_failed"
    assert task.status == "pending_url"
    assert "work-9" in task.last_error
    assert "手工回填" in task.last_error


@pytest.mark.parametrize(("status", "age_minutes", "action", "final_status"), [
    ("publishing", 30, "revert_planned", "planned"),
    ("publishing", 1, "none", "publishing"),
    ("pending_url", 30, "none", "pending_url"),
])
def test_reconcile_api_error_obeys_cooldown(
        monkeypatch, status, age_minutes, action, final_status):
    task = _task(
        status=status,
        started_at=datetime.utcnow() - timedelta(minutes=age_minutes),
    )
    result = _run(monkeypatch, task, AitoearnApiError("flow unavailable"))

    assert result["action"] == action
    assert task.status == final_status


def test_reconcile_rejects_invalid_work_link_without_publishing(monkeypatch):
    task = _task()

    def reject(*_args, **_kwargs):
        raise ValueError("URL 无法解析")

    monkeypatch.setattr(
        aitoearn_poller.publish_task_service, "mark_published", reject)
    result = _run(monkeypatch, task, {"tasks": [{
        "status": 1, "workLink": "https://invalid.test/post",
    }]})

    assert result == {"action": "url_rejected", "detail": "URL 无法解析"}
    assert task.status == "pending_url"
    assert task.last_error == "作品链接落库失败:URL 无法解析"


def test_tick_only_scans_recent_inflight_aitoearn_tasks(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    TopicDocPublishTaskORM.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.utcnow()
    rows = [
        TopicDocPublishTaskORM(
            topic_id=1, doc_id=1, platform="TikTok", status="pending_url",
            channel_meta_json='{"channel":"aitoearn","flow_id":"current"}',
            created_at=now,
        ),
        TopicDocPublishTaskORM(
            topic_id=1, doc_id=2, platform="TikTok", status="pending_url",
            channel_meta_json='{"channel":"aitoearn","flow_id":"old"}',
            created_at=now - timedelta(days=15),
        ),
        TopicDocPublishTaskORM(
            topic_id=1, doc_id=3, platform="TikTok", status="published",
            channel_meta_json='{"channel":"aitoearn","flow_id":"done"}',
            created_at=now,
        ),
        TopicDocPublishTaskORM(
            topic_id=1, doc_id=4, platform="TikTok", status="pending_url",
            channel_meta_json='{"channel":"rwj","flow_id":"other"}',
            created_at=now,
        ),
    ]
    db.add_all(rows)
    db.commit()
    expected_id = rows[0].id
    db.close()
    seen = []

    async def reconcile(_db, task):
        seen.append(task.id)
        return {"action": "none", "detail": "waiting"}

    monkeypatch.setattr(aitoearn_poller, "configured", lambda: True)
    monkeypatch.setattr(aitoearn_poller, "SessionLocal", Session)
    monkeypatch.setattr(aitoearn_poller, "reconcile_task", reconcile)

    aitoearn_poller.tick()

    assert seen == [expected_id]
