"""Mail provider presets -- if the user picks 163, they should get 163's own SMTP config.

Errors in this layer don't raise; they quietly store an unsendable combo like `smtp.163.com:587`, and the user only
gets a vague failure when clicking "test send". So the preset catalog itself (port ranges, valid encryption modes) and the autofill
boundaries (manual values must not be overwritten, guesses must not pose as user selections) must all be pinned in pure-function tests.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_email_provider_presets.py -q
"""
from __future__ import annotations

import json

import pytest

from agents.sales_agent.models import SalesAgentEmailAccountORM
from agents.sales_agent.service import (
    SALES_AGENT_EMAIL_PROVIDER_PRESETS,
    apply_email_provider_preset,
    detect_email_provider_preset,
    find_email_provider_preset,
    preset_for_smtp_host,
    serialize_sales_agent_email_account,
)
from agents.sales_agent.service.email_config import (
    _apply_email_account_payload,
    _normalize_smtp_security,
)


def _row(**kwargs) -> SalesAgentEmailAccountORM:
    """Build a bare sender row -- ORM column defaults only apply on flush; we don't touch the DB here, so fill them by hand."""
    base = dict(
        scope="user", user_id=1, display_name="", provider_type="smtp", api_provider="",
        enabled=True, is_default=False, from_email="", from_name="", reply_to_email="",
        smtp_host="", smtp_port=465, smtp_security="ssl", smtp_username="",
        smtp_secret_encrypted="", api_key_encrypted="", api_base_url="",
        provider_config_json="{}", last_test_status="", last_test_error="",
    )
    base.update(kwargs)
    return SalesAgentEmailAccountORM(**base)


# --- The catalog itself: every preset must be a config you can connect with directly ---

def test_preset_keys_unique():
    keys = [p["key"] for p in SALES_AGENT_EMAIL_PROVIDER_PRESETS]
    assert len(keys) == len(set(keys))


def test_preset_domains_unique_across_providers():
    """A domain may belong to only one provider -- on collision, detection depends on catalog order, a hidden bug."""
    seen: dict[str, str] = {}
    for preset in SALES_AGENT_EMAIL_PROVIDER_PRESETS:
        for domain in preset.get("domains") or []:
            assert domain not in seen, f"{domain} 同时归给了 {seen.get(domain)} 和 {preset['key']}"
            seen[domain] = preset["key"]


@pytest.mark.parametrize("preset", SALES_AGENT_EMAIL_PROVIDER_PRESETS, ids=lambda p: p["key"])
def test_preset_shape_is_usable(preset):
    assert preset["provider_type"] in {"smtp", "api"}
    assert preset["label"] and preset["secret_label"]
    if preset["provider_type"] != "smtp":
        return
    if not preset["smtp_host"]:
        # Only "custom SMTP" may leave host empty; it's a placeholder for the user to fill in
        assert preset["key"] == "custom_smtp"
        return
    assert 1 <= int(preset["smtp_port"]) <= 65535
    # Written verbatim into row.smtp_security; if it fails normalization, it's stored but can't send
    assert _normalize_smtp_security(preset["smtp_security"]) == preset["smtp_security"]
    for alt in preset.get("alt_ports") or []:
        assert 1 <= int(alt["smtp_port"]) <= 65535
        assert _normalize_smtp_security(alt["smtp_security"]) == alt["smtp_security"]


# --- Detect provider by domain ---

@pytest.mark.parametrize(("email", "key", "host", "port", "security"), [
    ("a@163.com", "netease_163", "smtp.163.com", 465, "ssl"),
    ("a@126.com", "netease_126", "smtp.126.com", 465, "ssl"),
    ("a@qq.com", "qq_mail", "smtp.qq.com", 465, "ssl"),
    ("a@foxmail.com", "qq_mail", "smtp.qq.com", 465, "ssl"),
    ("a@exmail.qq.com", "tencent_exmail", "smtp.exmail.qq.com", 465, "ssl"),
    ("a@gmail.com", "gmail", "smtp.gmail.com", 465, "ssl"),
    ("a@outlook.com", "outlook", "smtp.office365.com", 587, "starttls"),
    ("a@icloud.com", "icloud", "smtp.mail.me.com", 587, "starttls"),
    ("a@139.com", "chinamobile_139", "smtp.139.com", 465, "ssl"),
])
def test_detect_known_domains(email, key, host, port, security):
    preset, source = detect_email_provider_preset(email)
    assert source == "preset"
    assert preset is not None
    assert (preset["key"], preset["smtp_host"], preset["smtp_port"], preset["smtp_security"]) == (key, host, port, security)


def test_detect_is_case_insensitive_and_trims():
    preset, source = detect_email_provider_preset("  A.User@163.COM  ")
    assert source == "preset"
    assert preset is not None and preset["key"] == "netease_163"


def test_detect_accepts_bare_domain():
    preset, _ = detect_email_provider_preset("qq.com")
    assert preset is not None and preset["key"] == "qq_mail"


def test_detect_unknown_domain_guesses():
    """Unknown domains are guessed as smtp.<domain>:465 SSL and marked guess -- a wrong guess just means the user edits one line."""
    preset, source = detect_email_provider_preset("a@acme-corp.cn")
    assert source == "guess"
    assert preset is not None
    assert preset["smtp_host"] == "smtp.acme-corp.cn"
    assert (preset["smtp_port"], preset["smtp_security"]) == (465, "ssl")
    # A guess must not take a real key, or it would be stored as a preset the user selected
    assert preset["key"] == "custom_smtp"
    assert preset["domains"] == []


@pytest.mark.parametrize("bad", ["", "   ", "notanemail", "a@localhost", "@"])
def test_detect_rejects_domainless_input(bad):
    preset, source = detect_email_provider_preset(bad)
    assert (preset, source) == (None, "")


def test_custom_domain_presets_are_manual_only():
    """Corporate mail mostly uses the customer's own domain and can't be detected -- these can only be picked manually; attaching domains would misfire for others."""
    for key in ("netease_qiye", "custom_smtp", "resend_api"):
        preset = find_email_provider_preset(key)
        assert preset is not None and preset["domains"] == []


# --- Look up by key / host ---

def test_find_by_key():
    assert find_email_provider_preset("netease_163")["smtp_host"] == "smtp.163.com"
    assert find_email_provider_preset("  NETEASE_163 ")["key"] == "netease_163"
    assert find_email_provider_preset("nope") is None
    assert find_email_provider_preset("") is None


def test_preset_for_smtp_host_backfills_old_rows():
    """Old sender rows were stored before preset_key existed; the label can only be reverse-looked-up by host."""
    assert preset_for_smtp_host("smtp.163.com")["key"] == "netease_163"
    assert preset_for_smtp_host("SMTP.QQ.COM")["key"] == "qq_mail"
    assert preset_for_smtp_host("smtp.unknown.example") is None
    assert preset_for_smtp_host("") is None


# --- Autofill: filling blanks is fine, overwriting is not ---

def test_apply_fills_blank_smtp_from_key():
    row = _row(from_email="sales@example.com")
    preset, source = apply_email_provider_preset(row, "netease_163")
    assert (preset["key"], source) == ("netease_163", "preset")
    assert (row.smtp_host, row.smtp_port, row.smtp_security) == ("smtp.163.com", 465, "ssl")
    assert row.smtp_username == "sales@example.com"


def test_apply_falls_back_to_domain_detection():
    row = _row(from_email="sales@163.com")
    preset, source = apply_email_provider_preset(row, "")
    assert (preset["key"], source) == ("netease_163", "preset")
    assert row.smtp_host == "smtp.163.com"


def test_apply_does_not_overwrite_manual_host():
    """If the server was filled in manually, leave the whole set alone -- port and host go together; changing one alone gives an unsendable combo."""
    row = _row(from_email="sales@163.com", smtp_host="mail.internal.corp", smtp_port=2525, smtp_security="starttls")
    apply_email_provider_preset(row, "netease_163")
    assert (row.smtp_host, row.smtp_port, row.smtp_security) == ("mail.internal.corp", 2525, "starttls")


def test_apply_keeps_manual_username():
    row = _row(from_email="sales@163.com", smtp_username="postmaster@163.com")
    apply_email_provider_preset(row, "netease_163")
    assert row.smtp_username == "postmaster@163.com"


def test_apply_api_preset_sets_provider():
    row = _row(provider_type="api", from_email="sales@example.com")
    preset, _ = apply_email_provider_preset(row, "resend_api")
    assert preset["key"] == "resend_api"
    assert row.api_provider == "resend"
    assert row.smtp_host == ""  # API senders must not be given SMTP config


def test_apply_does_not_touch_smtp_when_row_is_api():
    row = _row(provider_type="api", api_provider="resend", from_email="sales@163.com")
    apply_email_provider_preset(row, "")
    assert row.smtp_host == ""


# --- Write path: filled in even if the frontend leaves it blank ---

def test_payload_autofills_from_email_domain_only():
    """Direct API callers give only sender email + auth code; a usable SMTP config should still be stored."""
    row = _row()
    _apply_email_account_payload(row, {"from_email": "sales@163.com"}, partial=False)
    assert (row.smtp_host, row.smtp_port, row.smtp_security) == ("smtp.163.com", 465, "ssl")
    assert row.smtp_username == "sales@163.com"
    assert row.display_name == "sales@163.com"
    assert json.loads(row.provider_config_json)["preset_key"] == "netease_163"


def test_payload_preset_key_beats_domain():
    """Domain is 163 but the user chose custom SMTP -- follow the user."""
    row = _row()
    _apply_email_account_payload(
        row,
        {"from_email": "sales@163.com", "preset_key": "custom_smtp", "smtp_host": "mail.acme.cn", "smtp_port": 25, "smtp_security": "none"},
        partial=False,
    )
    assert (row.smtp_host, row.smtp_port, row.smtp_security) == ("mail.acme.cn", 25, "none")
    assert json.loads(row.provider_config_json)["preset_key"] == "custom_smtp"


def test_payload_explicit_smtp_survives_preset():
    row = _row()
    _apply_email_account_payload(
        row,
        {"from_email": "sales@163.com", "preset_key": "netease_163", "smtp_host": "smtp-proxy.acme.cn", "smtp_port": 2525, "smtp_security": "starttls"},
        partial=False,
    )
    assert (row.smtp_host, row.smtp_port, row.smtp_security) == ("smtp-proxy.acme.cn", 2525, "starttls")


def test_payload_guess_is_not_recorded_as_choice():
    """Inferred providers are not written into provider_config, or the list would show a "guess" as "user selected"."""
    row = _row()
    _apply_email_account_payload(row, {"from_email": "sales@acme-corp.cn"}, partial=False)
    assert row.smtp_host == "smtp.acme-corp.cn"
    assert "preset_key" not in json.loads(row.provider_config_json)


def test_patch_does_not_relabel_manual_choice():
    """A PATCH that only changes enabled must not let domain detection change the user's custom_smtp back to netease_163."""
    row = _row(
        from_email="sales@163.com", smtp_host="mail.acme.cn", smtp_port=25, smtp_security="none",
        smtp_username="sales@163.com", provider_config_json=json.dumps({"preset_key": "custom_smtp"}),
    )
    _apply_email_account_payload(row, {"enabled": False}, partial=True)
    assert json.loads(row.provider_config_json)["preset_key"] == "custom_smtp"
    assert (row.smtp_host, row.smtp_port) == ("mail.acme.cn", 25)


def test_payload_preserves_other_provider_config_keys():
    row = _row(provider_config_json=json.dumps({"note": "备用发件箱"}))
    _apply_email_account_payload(row, {"from_email": "sales@163.com"}, partial=False)
    config = json.loads(row.provider_config_json)
    assert config["note"] == "备用发件箱"
    assert config["preset_key"] == "netease_163"


# --- Serialization: the list must show whether it's 163 or QQ ---

def test_serialize_exposes_preset_label():
    row = _row(
        id=1, from_email="sales@163.com", smtp_host="smtp.163.com", display_name="销售邮箱",
        provider_config_json=json.dumps({"preset_key": "netease_163"}),
    )
    data = serialize_sales_agent_email_account(row)
    assert data["preset_key"] == "netease_163"
    assert data["preset_label"] == "网易 163 邮箱"


def test_serialize_backfills_label_for_legacy_rows():
    """Stored before presets existed; must still be recognized by reverse lookup on smtp_host."""
    row = _row(id=2, from_email="sales@163.com", smtp_host="smtp.qq.com")
    data = serialize_sales_agent_email_account(row)
    assert data["preset_key"] == "qq_mail"


def test_serialize_never_leaks_secrets():
    row = _row(id=3, from_email="sales@163.com", smtp_host="smtp.163.com", smtp_secret_encrypted="ciphertext")
    data = serialize_sales_agent_email_account(row)
    assert data["has_smtp_secret"] is True
    assert "ciphertext" not in json.dumps(data, ensure_ascii=False, default=str)
