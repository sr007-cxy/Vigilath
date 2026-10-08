"""Regression tests for ops alerts on the unified model egress."""
from geo.services import llm_gateway


def test_account_overdue_detector_is_specific():
    assert llm_gateway._is_account_overdue_error(
        '{"error":{"code":"AccountOverdueError","message":"overdue"}}'
    )
    assert llm_gateway._is_account_overdue_error(
        "The request failed because your account has an overdue balance."
    )
    assert not llm_gateway._is_account_overdue_error(
        '{"error":{"code":"PermissionDenied","message":"forbidden"}}'
    )


def test_account_overdue_alert_reuses_webhook_and_deduplicates(monkeypatch):
    sent = []
    llm_gateway._account_overdue_alert_last.clear()
    monkeypatch.setattr(llm_gateway, "_send_llm_alert", sent.append)
    monkeypatch.setattr(llm_gateway.time, "monotonic", lambda: 1000.0)

    args = {
        "provider": "deepseek",
        "feature": "content.generate",
        "url": "https://gateway.example.com/v1/chat/completions",
        "http_status": 403,
        "body": ('{"error":{"code":"AccountOverdueError",'
                 '"message":"overdue. Request id: req_123"}}'),
    }
    llm_gateway._maybe_alert_account_overdue(**args)
    llm_gateway._maybe_alert_account_overdue(**args)

    assert len(sent) == 1
    assert "模型账户欠费" in sent[0]
    assert "gateway.example.com" in sent[0]
    assert "content.generate" in sent[0]
    assert "req_123" in sent[0]


def test_plain_403_does_not_alert(monkeypatch):
    sent = []
    llm_gateway._account_overdue_alert_last.clear()
    monkeypatch.setattr(llm_gateway, "_send_llm_alert", sent.append)

    llm_gateway._maybe_alert_account_overdue(
        provider="deepseek",
        feature="test",
        url="https://gateway.example.com/v1/chat/completions",
        http_status=403,
        body='{"error":{"code":"PermissionDenied"}}',
    )

    assert sent == []
